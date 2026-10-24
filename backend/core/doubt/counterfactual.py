"""反実仮想モード — 伝播計算（D3-3, 非LLM・決定論的）。

前提を仮に「偽」に倒し、崩れる領域（collapsed）と生き残る領域（surviving）を
依存グラフ上で描き分ける。

計算の限界（意図的な設計）:
  **「外した後に何が再構築できるか」は計算しない。** 崩壊領域の再構築・代替理論の
  探索はこのモジュールの守備範囲外であり、そこが人間の創造の場所である。
  出力は collapsed / surviving / indeterminate の 3 区分のみ。

区分の定義（決定論的・同入力同出力）:
  - seeds: トグルされた前提が直接支えるノード集合 → collapsed
  - affected: seeds の下流到達集合
  - affected のうち、上流（in-edge）が **すべて** collapsed/affected 側にあるノード
    → collapsed
  - affected のうち、生存側の上流も持つ（両者に依存する）ノード
    → indeterminate（崩壊側に倒さず判定不能で保持する）
  - それ以外 → surviving

閉路対応: 到達可能性は dependency.py（visited set BFS）を再利用。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text as sa_text

from core.doubt.dependency import (
    DependencyGraph,
    build_dependency_graph,
    seed_nodes_for_target,
)
from core.doubt.load_calculator import _assumption_seed_nodes
from core.doubt.schema import CounterfactualResult

logger = logging.getLogger(__name__)

#: 試算できなかった対象の事実文の頭（IK-0854。画面はこの文をそのまま描く）。
UNRESOLVED_WARNING_PREFIX = "グラフ上で見つからなかったため、依存する範囲を試算できなかった対象: "

#: 倒した観測が開いているグラフの論文の観測ではないとき（IK-0903。伝播には渡さない）。
OBSERVATION_OUTSIDE_SCOPE_WARNING_PREFIX = (
    "この観測はこのグラフの主張から参照されていないため、依存範囲は変わりません: "
)

#: 名前を引けない対象の置き換え語（内部 ID を画面に出さない — PL7）。正本は target_labels（IK-0901）。
from core.doubt.target_labels import UNNAMED_TARGET_LABEL  # noqa: E402


def _toggle_label(session, graph: DependencyGraph, target_id: str) -> str:
    """トグル対象の表示名（前提の文面 → 主張・部品の本文 → グラフのノード名）。"""
    tid = str(target_id or "").strip()
    if not tid:
        return UNNAMED_TARGET_LABEL
    if tid in graph.node_labels and graph.node_labels[tid] != tid:
        return graph.node_labels[tid]
    from core.doubt.open_assumptions import target_label

    for target_type in ("assumption", "claim", "component"):
        try:
            label = target_label(session, target_type, tid, document_id=None)
        except Exception:  # noqa: BLE001
            label = tid
        if label and label != tid:
            return label
    return UNNAMED_TARGET_LABEL


def _seeds_for_assumptions(
    session, graph: DependencyGraph, assumption_ids: list[str]
) -> tuple[set[str], list[str]]:
    """トグル対象の前提 → グラフ上の seed ノード集合。

    Returns:
        (seeds, unresolved_ids) — グラフに接続できなかった前提 id も返す
        （情報を落とさない: 呼び出し側が warnings として返す）。
    """
    if not assumption_ids:
        return set(), []
    rows = session.execute(
        sa_text("""
            SELECT id::text, created_from
            FROM assumption_nodes
            WHERE id::text = ANY(:ids)
        """),
        {"ids": list(assumption_ids)},
    ).fetchall()
    found = {str(r[0]): r[1] for r in rows}

    seeds: set[str] = set()
    unresolved: list[str] = []
    for aid in assumption_ids:
        aid = str(aid).strip()
        if not aid:
            continue
        if aid in found:
            node_seeds = _assumption_seed_nodes(graph, found[aid])
        else:
            # assumption_nodes に無い id は台帳対象（claim / component）として解決を試みる
            node_seeds = (
                seed_nodes_for_target(graph, "component", aid)
                | seed_nodes_for_target(graph, "claim", aid)
                | seed_nodes_for_target(graph, "equation", aid)
            )
        if node_seeds:
            seeds |= node_seeds
        else:
            unresolved.append(aid)
    return seeds, unresolved


def compute_propagation(graph: DependencyGraph, seeds: set[str]) -> CounterfactualResult:
    """seed ノード集合から 3 区分を決定論的に計算する（純関数・DB 非依存）。"""
    seeds = {s for s in seeds if s in graph.node_ids}
    downstream = graph.downstream_of_set(seeds)
    affected = seeds | downstream

    collapsed: set[str] = set(seeds)
    indeterminate: set[str] = set()
    for node in sorted(downstream):
        preds = graph.predecessors.get(node, set())
        if preds and any(p not in affected for p in preds):
            # 崩壊側と生存側の両方に依存 → 判定不能（崩壊側に倒さない）
            indeterminate.add(node)
        else:
            collapsed.add(node)

    surviving = graph.node_ids - collapsed - indeterminate
    return CounterfactualResult(
        collapsed_node_ids=sorted(collapsed),
        surviving_node_ids=sorted(surviving),
        indeterminate_node_ids=sorted(indeterminate),
    )


def compute_counterfactual(
    session,
    toggled_assumption_ids: list[str],
    *,
    document_ids,
) -> dict:
    """反実仮想の試算（保存なし）。

    範囲は ``document_ids``（必須・IK-0852）。route が document_id 指定ならその 1 件、
    コース指定ならコースの教材から求めた集合を渡す（course_id 列では絞らない）。

    Returns:
        {"toggled_assumption_ids", "collapsed_node_ids", "surviving_node_ids",
         "indeterminate_node_ids", "node_labels", "warnings"}
    """
    graph = build_dependency_graph(session, document_ids=document_ids)
    seeds, unresolved = _seeds_for_assumptions(session, graph, toggled_assumption_ids)
    result = compute_propagation(graph, seeds)
    result.toggled_assumption_ids = [str(a) for a in toggled_assumption_ids]

    # IK-0854: トグルした対象は内部 ID ではなく名前で返す（画面が件数でなく名前を列挙する）。
    toggled_labels = [
        _toggle_label(session, graph, str(a)) for a in toggled_assumption_ids if str(a or "").strip()
    ]
    unresolved_labels = [_toggle_label(session, graph, aid) for aid in unresolved]
    warnings = []
    if unresolved:
        warnings.append(UNRESOLVED_WARNING_PREFIX + "、".join(unresolved_labels))

    payload = result.model_dump()
    payload["node_labels"] = {
        nid: graph.node_labels.get(nid, nid)
        for nid in (result.collapsed_node_ids + result.indeterminate_node_ids)
    }
    payload["warnings"] = warnings
    payload["toggled_labels"] = toggled_labels
    payload["unresolved_labels"] = unresolved_labels
    # 範囲に解析済みのグラフが無い（コースの教材が未解析・範囲が空）ことを試算失敗と区別する。
    payload["graph_available"] = bool(graph.node_ids)
    return payload


def snapshot_subgraphs(payload: dict) -> tuple[str, str, str]:
    """保存用スナップショット（collapsed / surviving / indeterminate）を JSON 文字列で返す。"""
    labels = payload.get("node_labels") or {}

    def _subgraph(ids: list) -> str:
        return json.dumps(
            {
                "node_ids": list(ids or []),
                "labels": {nid: labels.get(nid, "") for nid in (ids or [])},
            },
            ensure_ascii=False,
        )

    return (
        _subgraph(payload.get("collapsed_node_ids") or []),
        _subgraph(payload.get("surviving_node_ids") or []),
        _subgraph(payload.get("indeterminate_node_ids") or []),
    )
