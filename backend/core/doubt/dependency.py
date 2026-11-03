"""D層共有 — TheoryOperationGraph からの依存グラフ構築と到達可能性（非LLM）。

load_calculator（D2-1）と counterfactual（D3-3）が共用する決定論的な基盤。

原則:
  - graph_layer='debug' のノード、source_backing_status='inferred' のノードは
    依存計算の根拠にしない（弱い backing を負荷・伝播に混ぜない）。
  - 閉路があってもハングしない（visited set による BFS）。
  - エッジの向きは source → target（target は source の出力に依存する）。
    「X に依存する下流」= X から辺をたどって到達できるノード集合。
  - ``exclude_claim_sequence_edges=True``（負荷・反実仮想が渡す・IK-1030 / 2026-10-27 オーナー判断。
    2026-10-28 第 7 波で支持線・反証条件の入力・ゼミ前ブリーフにも広げた — IK-1048）:
    論文の主張を出現順に並べた claim チェーン由来ノードに接する辺を依存に数えない。出現順は
    読む順であって「偽なら崩れる」関係ではないため。判定の正本は一覧の母集合（IK-0933）と同じ
    ``core/theory_modules/builder.py::claim_sequence_node_ids``（``population.claim_sequence_ids_for_payload``
    経由）。ノードそのもの・主張 / 式の参照は残す（台帳行の種は消えない — 下流が出現順の辺を通らないだけ）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text as sa_text

from core.doubt.scope import document_scope_clause

logger = logging.getLogger(__name__)

_EXCLUDED_LAYERS = ("debug",)
_EXCLUDED_BACKINGS = ("inferred",)

# ノード → claim / equation 参照キー（component_graph/schema.py の payload 語彙）
_NODE_CLAIM_KEYS = ("linked_claim_ids", "input_claim_ids", "output_claim_ids", "required_claim_ids")
_NODE_EQUATION_KEYS = (
    "linked_equation_ids", "input_equation_ids", "output_equation_ids",
    "definition_equation_ids", "constraint_equation_ids",
)


@dataclass
class DependencyGraph:
    """course / document 範囲の依存グラフのスナップショット。"""

    node_ids: set[str] = field(default_factory=set)
    node_labels: dict[str, str] = field(default_factory=dict)
    node_layers: dict[str, str] = field(default_factory=dict)
    successors: dict[str, set[str]] = field(default_factory=dict)
    predecessors: dict[str, set[str]] = field(default_factory=dict)
    # claim_id / equation_id → その対象を根拠として参照するノード集合
    claim_refs: dict[str, set[str]] = field(default_factory=dict)
    equation_refs: dict[str, set[str]] = field(default_factory=dict)
    document_ids: list[str] = field(default_factory=list)
    # claim チェーンの辺を外して作ったか（IK-1030）と、外した辺の有無（点検用・画面に出さない）。
    claim_sequence_edges_excluded: bool = False
    excluded_edge_count: int = 0
    # 外した claim チェーン由来ノード（IK-1048: 支持線の資格付きエッジも同じ集合で外す — 判定の正本は 1 つ）。
    claim_sequence_node_ids: set[str] = field(default_factory=set)
    # 表示用の補助（読める名前を組むため。依存計算には使わない — 第 17 周 te-05）:
    # ノード → 根拠の主張 ID（graph 上の綴りのまま）/ 操作語彙（``infer_intermediate_claim`` 等）。
    node_claim_ids: dict[str, list[str]] = field(default_factory=dict)
    node_operations: dict[str, str] = field(default_factory=dict)

    def downstream(self, node_id: str) -> set[str]:
        """node_id に依存する下流ノード集合（自身は含まない）。閉路対応。"""
        if node_id not in self.node_ids:
            return set()
        visited: set[str] = set()
        stack = list(self.successors.get(node_id, ()))
        while stack:
            current = stack.pop()
            if current in visited or current == node_id:
                continue
            visited.add(current)
            stack.extend(self.successors.get(current, ()))
        return visited

    def downstream_of_set(self, seed_ids: set[str]) -> set[str]:
        """複数シードの下流の和集合（シード自身は含まない）。"""
        visited: set[str] = set()
        stack: list[str] = []
        for seed in seed_ids:
            stack.extend(self.successors.get(seed, ()))
        while stack:
            current = stack.pop()
            if current in visited or current in seed_ids:
                continue
            visited.add(current)
            stack.extend(self.successors.get(current, ()))
        return visited


def is_claim_sequence_edge(source: str, target: str, sequence_ids) -> bool:
    """辺が claim チェーン由来ノードに接するか（IK-1030 / IK-1048 — 依存グラフと支持線の共通の判定）。"""
    if not sequence_ids:
        return False
    return source in sequence_ids or target in sequence_ids


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _graph_payload(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _node_included(node: dict) -> bool:
    layer = str(node.get("graph_layer") or "main")
    backing = str(node.get("source_backing_status") or "")
    if layer in _EXCLUDED_LAYERS:
        return False
    if backing in _EXCLUDED_BACKINGS:
        return False
    return True


def build_dependency_graph(
    session,
    *,
    document_ids,
    exclude_claim_sequence_edges: bool = False,
    claim_sequence_resolver=None,
) -> DependencyGraph:
    """theory_component_graphs から依存グラフを構築する（document 集合の範囲, IK-0852）。

    ``document_ids`` は必須（範囲の正本は ``core/doubt/scope.py``）。空集合なら SQL を
    出さずに空のグラフを返す（fail-closed）。成果の行の course_id 列では絞らない。

    ``exclude_claim_sequence_edges=True`` のとき、論文ごとに claim チェーン由来ノードを
    ``claim_sequence_resolver(session, document_id, payload) -> set[str]``（既定は
    ``population.claim_sequence_ids_for_payload``）で求め、その端点を持つ辺を依存に数えない（IK-1030）。
    """
    graph = DependencyGraph()
    graph.claim_sequence_edges_excluded = bool(exclude_claim_sequence_edges)
    if exclude_claim_sequence_edges and claim_sequence_resolver is None:
        from core.doubt.population import claim_sequence_ids_for_payload as claim_sequence_resolver
    clause = document_scope_clause(document_ids)
    if clause is None:
        return graph
    where, params = clause

    rows = session.execute(
        sa_text(f"""
            SELECT document_id::text AS document_id, graph_json
            FROM theory_component_graphs
            WHERE {where}
        """),
        params,
    ).fetchall()

    claim_refs_by_document: dict[str, set[str]] = {}
    for row in rows:
        doc_id = str(row[0] or "")
        if doc_id and doc_id not in graph.document_ids:
            graph.document_ids.append(doc_id)
        doc_claim_refs = claim_refs_by_document.setdefault(doc_id, set())
        payload = _graph_payload(row[1])
        sequence_ids: set[str] = set()
        if exclude_claim_sequence_edges:
            try:
                sequence_ids = set(claim_sequence_resolver(session, doc_id, payload) or ())
            except Exception:  # noqa: BLE001 — 判定できない = 外さない（従来どおり）
                logger.debug("claim sequence resolution failed for %s", doc_id, exc_info=True)
                sequence_ids = set()
            graph.claim_sequence_node_ids |= sequence_ids
        nodes = [n for n in _as_list(payload.get("nodes")) if isinstance(n, dict)]
        edges = [e for e in _as_list(payload.get("edges")) if isinstance(e, dict)]

        for node in nodes:
            nid = str(node.get("component_id") or node.get("id") or "").strip()
            if not nid or not _node_included(node):
                continue
            graph.node_ids.add(nid)
            graph.node_labels[nid] = str(
                node.get("visual_label") or node.get("display_label") or node.get("label") or nid
            )
            graph.node_layers[nid] = str(node.get("graph_layer") or "main")
            graph.node_operations[nid] = str(node.get("operation") or "")
            for key in _NODE_CLAIM_KEYS:
                for cid in _as_list(node.get(key)):
                    claim_id = str(cid or "").strip()
                    if claim_id:
                        graph.claim_refs.setdefault(claim_id, set()).add(nid)
                        doc_claim_refs.add(claim_id)
                        claims = graph.node_claim_ids.setdefault(nid, [])
                        if claim_id not in claims:
                            claims.append(claim_id)
            for key in _NODE_EQUATION_KEYS:
                for eid in _as_list(node.get(key)):
                    eq_id = str(eid or "").strip()
                    if eq_id:
                        graph.equation_refs.setdefault(eq_id, set()).add(nid)

        for edge in edges:
            source = str(edge.get("source_component_id") or edge.get("source") or "").strip()
            target = str(edge.get("target_component_id") or edge.get("target") or "").strip()
            if not source or not target:
                continue
            # 除外ノードに接続する辺は根拠にしない
            if source not in graph.node_ids or target not in graph.node_ids:
                continue
            # 主張の出現順の辺は依存に数えない（IK-1030）
            if is_claim_sequence_edge(source, target, sequence_ids):
                graph.excluded_edge_count += 1
                continue
            graph.successors.setdefault(source, set()).add(target)
            graph.predecessors.setdefault(target, set()).add(source)
            evidence = edge.get("evidence") if isinstance(edge.get("evidence"), dict) else edge
            for cid in _as_list(evidence.get("evidence_claim_ids")) + _as_list(evidence.get("evidence_claims")):
                claim_id = str(cid or "").strip()
                if claim_id:
                    # claim が偽なら辺の target 以降が崩れる
                    graph.claim_refs.setdefault(claim_id, set()).add(target)
                    doc_claim_refs.add(claim_id)
            for eid in _as_list(evidence.get("evidence_equation_ids")):
                eq_id = str(eid or "").strip()
                if eq_id:
                    graph.equation_refs.setdefault(eq_id, set()).add(target)

    _alias_claim_refs(session, graph, claim_refs_by_document)
    return graph


def _alias_claim_refs(session, graph: DependencyGraph, refs_by_document: dict[str, set[str]]) -> None:
    """主張参照の別名（局所 ID ⇄ UUID）を ``claim_refs`` に足す（IK-0930）。

    グラフの主張参照は生成時の局所 ID（``synth_claim_*`` / ``claim_span_*``）か UUID。
    観測の選択肢（主張の型で拾った UUID）や台帳の主張 UUID で種を引けるよう、解決の正本
    （``core/knowledge_objects/claim_refs.py``）で同じ主張の別の綴りを同じノード集合に向ける。
    既存のキーは書き換えない（別名を足すだけ）。失敗は fail-soft（別名なし = 従来どおり）。
    """
    try:
        from core.knowledge_objects.claim_refs import ClaimRefResolver
    except Exception:  # noqa: BLE001
        return
    for doc_id, refs in refs_by_document.items():
        if not doc_id or not refs:
            continue
        try:
            resolver = ClaimRefResolver(session, doc_id)
            for ref in sorted(refs):
                nodes = graph.claim_refs.get(ref)
                if not nodes:
                    continue
                for alias in resolver.aliases(ref):
                    if alias != ref:
                        graph.claim_refs.setdefault(alias, set()).update(nodes)
        except Exception:  # noqa: BLE001
            logger.debug("claim ref aliasing failed for %s", doc_id, exc_info=True)


def seed_nodes_for_target(graph: DependencyGraph, target_type: str, target_id: str) -> set[str]:
    """台帳対象がグラフ上で直接支えているノード集合。"""
    target_type = str(target_type or "").strip()
    target_id = str(target_id or "").strip()
    if target_type == "component":
        return {target_id} if target_id in graph.node_ids else set()
    if target_type == "claim":
        return set(graph.claim_refs.get(target_id, set()))
    if target_type == "equation":
        return set(graph.equation_refs.get(target_id, set()))
    return set()
