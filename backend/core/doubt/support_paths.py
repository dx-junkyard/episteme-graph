"""独立支持経路の計算（SL-3, 非LLM・純 Python・決定論的）。

正本: docs/features/stakes_ledger_design.md §5.1。観測系 claim
（core/doubt/observation_targets.py）から対象ノードまでの**エッジ非交差経路数**を
単位容量 max-flow（Edmonds–Karp・BFS）で計算する。networkx は使わない
（requirements に無い・追加もしない, SL9）。

「独立な支持線」= 資格付きエッジ（source_backing_status が source_backed /
partially_source_backed のもの。review_required / inferred / 空文字＝フォールバック
簡易 edge は数えない）だけを辿る、観測系 claim（root）から対象ノードまでのパス。
dependency.DependencyGraph は edge の source_backing_status を保持しない
（build_dependency_graph は非改変）ため、このモジュールは独自に graph_json を
読んで資格フィルタを適用する。

数値（経路数・容量・カットのサイズ）は関数外に一切出さない。戻り値は3段の事実文
（"none" / "single" / "several"）と少数の列挙（node_id 昇順・最大5件）のみ（SL4）。
「このコーパスの中では」の固定文言は分野レベルの不在言明を作らないための固定表現
（SL1）。
"""

from __future__ import annotations

import json
import logging
from collections import deque
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text as sa_text

from core.doubt.dependency import DependencyGraph, build_dependency_graph, seed_nodes_for_target
from core.doubt.observation_targets import observation_claim_targets

logger = logging.getLogger(__name__)

# 支持線として数えてよい edge.source_backing_status（§2-7 のすり抜けをここで塞ぐ）
_QUALIFIED_BACKING_STATUSES = ("source_backed", "partially_source_backed")

# 実在ノード id と衝突しない仮想 super-source / super-sink のセンチネル
_SOURCE_NODE = "\x00__support_source__\x00"
_SINK_NODE = "\x00__support_sink__\x00"

# sink → 仮想 T の容量（ボトルネックにしない。実際の制約は各 root の容量1と
# 資格付きエッジの容量1だけで表現する）
_SINK_EDGE_CAPACITY = 10 ** 9

_MAX_CUT_MEMBERS = 5
_MAX_OBSERVATION_ROOTS = 5

# 段階の事実文（SL1: 分野レベルの不在言明を作らず、コーパス内の記録の有無だけを言う）
FACT_LINE_NONE = "この対象への、観測記録からの支持線はこのコーパスの中では見つかりません。"
FACT_LINE_SEVERAL = "この対象は複数の独立した支持線に支えられています。"


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


def _qualified_adjacency(
    session, course_id: str, document_id: str, node_ids: set[str],
) -> dict[str, set[str]]:
    """資格付きエッジ（source_backing_status が source_backed /
    partially_source_backed）の隣接リストを graph_json から直接読む。
    """
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
    try:
        rows = session.execute(
            sa_text(f"""
                SELECT graph_json
                FROM theory_component_graphs
                WHERE {' AND '.join(filters)}
            """),
            params,
        ).fetchall()
    except Exception:
        logger.warning("support path adjacency lookup failed", exc_info=True)
        return {}

    adjacency: dict[str, set[str]] = {}
    for row in rows:
        payload = _graph_payload(row[0])
        for edge in (e for e in _as_list(payload.get("edges")) if isinstance(e, dict)):
            source = str(edge.get("source_component_id") or edge.get("source") or "").strip()
            target = str(edge.get("target_component_id") or edge.get("target") or "").strip()
            if not source or not target or source not in node_ids or target not in node_ids:
                continue
            backing = str(edge.get("source_backing_status") or "").strip()
            if backing not in _QUALIFIED_BACKING_STATUSES:
                continue
            adjacency.setdefault(source, set()).add(target)
    return adjacency


def _bfs_augmenting_path(
    capacity: dict[str, dict[str, int]], source: str, sink: str,
) -> list[tuple[str, str]] | None:
    """BFS で S→T の増加可能パスを1本探す（Edmonds–Karp の1反復）。"""
    parent: dict[str, str] = {}
    visited = {source}
    queue = deque([source])
    while queue:
        u = queue.popleft()
        if u == sink:
            break
        for v, cap in capacity.get(u, {}).items():
            if cap > 0 and v not in visited:
                visited.add(v)
                parent[v] = u
                queue.append(v)
    if sink not in visited:
        return None
    path: list[tuple[str, str]] = []
    node = sink
    while node != source:
        prev = parent[node]
        path.append((prev, node))
        node = prev
    path.reverse()
    return path


def _max_flow_edge_disjoint_paths(
    adjacency: dict[str, set[str]], roots: set[str], sinks: set[str],
) -> tuple[int, dict[str, dict[str, int]], set[str]]:
    """単位容量 Edmonds–Karp（BFS、純 Python・networkx 不使用）。

    S→各root（容量1）→資格エッジ（容量1）→各sink→T（容量大, ボトルネック回避）の
    max-flow を計算する。flow の値 = エッジ非交差経路数 = 独立な支持線の数。

    戻り値: (flow, residual_capacity, s_reachable)。s_reachable は最終残余グラフで
    S から到達可能な集合（最小カットの導出に使う）。決定論を保証するため、辺の
    追加順序は常にソート済みキーで行う（同一入力なら常に同一の増加パス列になる）。
    """
    capacity: dict[str, dict[str, int]] = {}

    def _add_edge(u: str, v: str, cap: int) -> None:
        capacity.setdefault(u, {})
        capacity.setdefault(v, {})
        capacity[u][v] = capacity[u].get(v, 0) + cap
        capacity[v].setdefault(u, capacity[v].get(u, 0))

    for root in sorted(roots):
        _add_edge(_SOURCE_NODE, root, 1)
    for source in sorted(adjacency.keys()):
        for target in sorted(adjacency[source]):
            _add_edge(source, target, 1)
    for sink in sorted(sinks):
        _add_edge(sink, _SINK_NODE, _SINK_EDGE_CAPACITY)

    flow = 0
    while True:
        path = _bfs_augmenting_path(capacity, _SOURCE_NODE, _SINK_NODE)
        if path is None:
            break
        for u, v in path:
            capacity[u][v] -= 1
            capacity[v][u] += 1
        flow += 1

    # 最終残余グラフで S から到達可能な集合（単位容量なので、この境界が最小カット）
    s_reachable = {_SOURCE_NODE}
    queue = deque([_SOURCE_NODE])
    while queue:
        u = queue.popleft()
        for v, cap in capacity.get(u, {}).items():
            if cap > 0 and v not in s_reachable:
                s_reachable.add(v)
                queue.append(v)

    return flow, capacity, s_reachable


def _real_node(node_id: str) -> bool:
    return node_id not in (_SOURCE_NODE, _SINK_NODE)


def _cut_members(
    original_edges: list[tuple[str, str]],
    s_reachable: set[str],
    node_labels: dict[str, str],
) -> list[dict[str, str]]:
    """最小カットを跨ぐエッジの実ノード側端点を cut_members として列挙する。

    エッジ (u, v) が u∈s_reachable, v∉s_reachable のとき「カットエッジ」。
    上流側 u が実ノードならそれを使う。u が仮想 super-source（対象が単一の
    観測 root にしか繋がっていない場合に起こる）なら、代わりに実ノードである
    下流側 v（その root 自身）を使う（"上流側端点" の意図を仮想ノードの
    ケースでも実ノードに落とし込むための一般化）。
    """
    members: dict[str, str] = {}
    for u, v in original_edges:
        if u in s_reachable and v not in s_reachable:
            real = u if _real_node(u) else (v if _real_node(v) else None)
            if real is not None:
                members[real] = node_labels.get(real, real)
    return [
        {"node_id": nid, "label": members[nid]}
        for nid in sorted(members.keys())
    ][:_MAX_CUT_MEMBERS]


@dataclass
class SupportPathContext:
    """複数対象を評価するときに再利用する、コース/文書スコープの共有文脈（性能改善）。

    ``build_dependency_graph`` / ``observation_claim_targets`` / ``_qualified_adjacency`` は
    いずれもコース・文書スコープ単位で1回計算すれば十分な情報しか使わないため、
    対象（target）ごとに繰り返し呼ぶと N 回グラフを再構築してしまう
    （``core/doubt/open_assumptions.py::compile_open_assumptions`` のような一括評価で顕在化）。
    :func:`build_support_context` で1回だけ作り、対象ごとに
    :func:`compute_support_lines_from_context` を呼ぶこと。
    """

    graph: DependencyGraph
    obs_targets: list[dict]
    adjacency: dict[str, set[str]]


def build_support_context(
    session,
    *,
    course_id: str = "",
    document_id: str = "",
) -> SupportPathContext | None:
    """コース/文書スコープの共有文脈を1回だけ構築する（fail-soft: 失敗時は None）。"""
    course_id = str(course_id or "").strip()
    document_id = str(document_id or "").strip()

    try:
        graph = build_dependency_graph(session, course_id=course_id, document_id=document_id)
    except Exception:
        logger.warning("support path dependency graph build failed", exc_info=True)
        return None

    try:
        obs_targets = observation_claim_targets(session, course_id=course_id, document_id=document_id)
    except Exception:
        logger.warning("support path observation target lookup failed", exc_info=True)
        obs_targets = []

    adjacency = _qualified_adjacency(session, course_id, document_id, graph.node_ids)
    return SupportPathContext(graph=graph, obs_targets=obs_targets, adjacency=adjacency)


def compute_support_lines_from_context(
    ctx: SupportPathContext | None,
    target_type: str,
    target_id: str,
) -> dict | None:
    """1つの対象の独立支持経路を、既に構築済みの :class:`SupportPathContext` から計算する。

    Returns:
        ``{"level", "fact_line", "cut_members", "observation_roots"}``。
        ``ctx`` が ``None``、または対象ノードがグラフ上に解決できない場合は ``None``
        （呼び出し側は台帳本体を壊さずキーを省略すること）。
    """
    if ctx is None:
        return None
    target_type = str(target_type or "").strip()
    target_id = str(target_id or "").strip()

    sinks = seed_nodes_for_target(ctx.graph, target_type, target_id)
    if not sinks:
        return None

    graph = ctx.graph
    adjacency = ctx.adjacency

    roots: set[str] = set()
    contributing_claims: dict[str, dict] = {}
    for item in ctx.obs_targets:
        claim_id = str(item.get("claim_id") or "")
        referencing_nodes = {
            nid for nid in graph.claim_refs.get(claim_id, set())
            if nid in graph.node_ids and nid not in sinks
        }
        if referencing_nodes:
            roots |= referencing_nodes
            contributing_claims[claim_id] = item

    original_edges: list[tuple[str, str]] = [
        (u, v) for u, targets in adjacency.items() for v in targets
    ]
    original_edges += [(_SOURCE_NODE, r) for r in roots]
    original_edges += [(s, _SINK_NODE) for s in sinks]

    flow, _capacity, s_reachable = _max_flow_edge_disjoint_paths(adjacency, roots, sinks)

    observation_roots = [
        {
            "claim_id": claim_id,
            "label": item.get("label", claim_id),
            "identified_via": item.get("identified_via", ""),
        }
        for claim_id, item in sorted(contributing_claims.items())
    ][:_MAX_OBSERVATION_ROOTS]

    if flow <= 0:
        return {
            "level": "none",
            "fact_line": FACT_LINE_NONE,
            "cut_members": [],
            "observation_roots": observation_roots,
        }

    if flow == 1:
        cut_members = _cut_members(original_edges, s_reachable, graph.node_labels)
        labels = "、".join(m["label"] for m in cut_members) or "（不明）"
        fact_line = (
            f"この対象は単一の支持線に立っています。"
            f"『{labels}』が同時に崩れると、観測からの支持が途切れます。"
        )
        return {
            "level": "single",
            "fact_line": fact_line,
            "cut_members": cut_members,
            "observation_roots": observation_roots,
        }

    return {
        "level": "several",
        "fact_line": FACT_LINE_SEVERAL,
        "cut_members": [],
        "observation_roots": observation_roots,
    }


def compute_support_lines(
    session,
    target_type: str,
    target_id: str,
    *,
    course_id: str = "",
    document_id: str = "",
) -> dict | None:
    """対象の独立支持経路を計算する（数値非公開・SL4）。

    単発呼び出し用の薄いラッパ: :func:`build_support_context` で文脈を1回作り、
    :func:`compute_support_lines_from_context` に渡すだけ。複数対象をまとめて評価する
    場合（``core/doubt/open_assumptions.py::compile_open_assumptions`` 等）は文脈を
    1度だけ作って :func:`compute_support_lines_from_context` を繰り返し呼ぶこと。

    Returns:
        ``{"level", "fact_line", "cut_members", "observation_roots"}``。
        対象ノードがグラフ上に解決できない、またはグラフ自体が存在しない場合は
        ``None``（呼び出し側は台帳本体を壊さずキーを省略すること）。
    """
    ctx = build_support_context(session, course_id=course_id, document_id=document_id)
    return compute_support_lines_from_context(ctx, target_type, target_id)
