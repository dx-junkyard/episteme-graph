"""D層共有 — TheoryOperationGraph からの依存グラフ構築と到達可能性（非LLM）。

load_calculator（D2-1）と counterfactual（D3-3）が共用する決定論的な基盤。

原則:
  - graph_layer='debug' のノード、source_backing_status='inferred' のノードは
    依存計算の根拠にしない（弱い backing を負荷・伝播に混ぜない）。
  - 閉路があってもハングしない（visited set による BFS）。
  - エッジの向きは source → target（target は source の出力に依存する）。
    「X に依存する下流」= X から辺をたどって到達できるノード集合。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text as sa_text

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


def build_dependency_graph(session, course_id: str = "", document_id: str = "") -> DependencyGraph:
    """theory_component_graphs から依存グラフを構築する（course / document 範囲）。"""
    graph = DependencyGraph()
    filters = []
    params: dict[str, Any] = {}
    if course_id:
        filters.append("course_id = :course")
        params["course"] = course_id
    if document_id:
        filters.append("document_id = :doc")
        params["doc"] = document_id
    if not filters:
        filters.append("TRUE")

    rows = session.execute(
        sa_text(f"""
            SELECT document_id, graph_json
            FROM theory_component_graphs
            WHERE {' AND '.join(filters)}
        """),
        params,
    ).fetchall()

    for row in rows:
        doc_id = str(row[0] or "")
        if doc_id and doc_id not in graph.document_ids:
            graph.document_ids.append(doc_id)
        payload = _graph_payload(row[1])
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
            for key in _NODE_CLAIM_KEYS:
                for cid in _as_list(node.get(key)):
                    claim_id = str(cid or "").strip()
                    if claim_id:
                        graph.claim_refs.setdefault(claim_id, set()).add(nid)
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
            graph.successors.setdefault(source, set()).add(target)
            graph.predecessors.setdefault(target, set()).add(source)
            evidence = edge.get("evidence") if isinstance(edge.get("evidence"), dict) else edge
            for cid in _as_list(evidence.get("evidence_claim_ids")) + _as_list(evidence.get("evidence_claims")):
                claim_id = str(cid or "").strip()
                if claim_id:
                    # claim が偽なら辺の target 以降が崩れる
                    graph.claim_refs.setdefault(claim_id, set()).add(target)
            for eid in _as_list(evidence.get("evidence_equation_ids")):
                eq_id = str(eid or "").strip()
                if eq_id:
                    graph.equation_refs.setdefault(eq_id, set()).add(target)

    return graph


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
