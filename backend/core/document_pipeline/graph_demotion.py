"""理論操作グラフのノード単位の降格（export_validation の hard error を受けて）。

ExportValidationGate が component_graph に hard error を出したとき、従来はグラフ全体を
保存しなかった（``skip_graph_persist``）。1 ノードの欠陥で 100 を超えるノードと辺が
教材から消え、論文層・理論モジュール・レーダーの深い段の材料まで縮退していた
（2026-10-16 実測: 135 ノード中 1 ノードの ``elimination_missing_eliminated_symbols``）。

本モジュールは **ノード単位** の hard error に限り、そのノードを ``graph_layer="debug"`` /
``source_backing_status="review_required"`` に降格して残し（P4: 落とさない）、
ComponentGraphValidator で**再検証して error がゼロになったときだけ**降格版を保存対象に
する。validator の規則そのものは変えない（#306 / #308: 確定扱いしない・main に置かない）。

- 降格で解消できる規則だけを ``DEMOTABLE_RULE_IDS`` に列挙する（main 層でだけ error に
  なる規則 + 降格時に中和できる規則）。列挙外の error（循環・gate 自身の参照検査など
  グラフ全体に関わるもの）が 1 件でもあれば降格しない＝従来どおりグラフを保存しない。
- 純関数。入力を mutate しない。DB・LLM・FastAPI を import しない。
"""
from __future__ import annotations

import re
from dataclasses import replace

from episteme_graph.agents.component_graph.schema import (
    ELIMINATION_TARGET_UNRESOLVED_REASON,
    GRAPH_LAYER_DEBUG,
    UNRESOLVED_ELIMINATION_OPERATION,
    VALIDATION_DEMOTED_REASON,
    review_status_for_backing,
)
from episteme_graph.agents.component_graph.validator import ComponentGraphValidator

# 降格（debug 層・review_required）で解消できるノード単位の hard error。
_MAIN_ONLY_RULE_IDS = frozenset({
    "component_graph_node_label_generic",
    "main_graph_node_equation_id_label",
    "main_graph_contains_fallback_component",
    "main_graph_generic_operation",
    "theory_node_missing_source_link",
    "component_graph_node_missing_component_type",
})
# 降格に加えてノードの宣言を中和すると解消できる規則。
_ELIMINATION_RULE_ID = "elimination_missing_eliminated_symbols"
_NEUTRALIZED_RULE_IDS = frozenset({
    _ELIMINATION_RULE_ID,
    "fallback_node_marked_source_backed",
    "review_required_node_missing_reasons",
})
DEMOTABLE_RULE_IDS = _MAIN_ONLY_RULE_IDS | _NEUTRALIZED_RULE_IDS

_NODE_REF_RE = re.compile(r"nodes\[([^\]]+)\]")


def _node_id_of(error: dict) -> str:
    path = str(error.get("path") or error.get("field") or "")
    m = _NODE_REF_RE.search(path)
    if m and not m.group(1).isdigit():
        return m.group(1)
    if str(error.get("target_type") or "") == "graph_node":
        return str(error.get("target_id") or "")
    return ""


def plan_node_demotion(graph_errors: list[dict]) -> dict[str, set[str]] | None:
    """gate の component_graph error 群から {node_id: {rule_id}} を作る。

    1 件でも降格で解消できない error（規則が列挙外・ノードを特定できない）があれば
    None（= 降格しない）を返す。
    """
    plan: dict[str, set[str]] = {}
    for err in graph_errors or []:
        code = str(err.get("code") or err.get("rule_id") or "")
        node_id = _node_id_of(err)
        if code not in DEMOTABLE_RULE_IDS or not node_id:
            return None
        plan.setdefault(node_id, set()).add(code)
    return plan or None


def _ordered_unique(values) -> list[str]:
    out: list[str] = []
    for v in values or []:
        s = str(v or "")
        if s and s not in out:
            out.append(s)
    return out


def _demote_node(node, rules: set[str]):
    reasons = list(getattr(node, "review_reasons", []) or []) + [VALIDATION_DEMOTED_REASON]
    changes: dict = {
        "graph_layer": GRAPH_LAYER_DEBUG,
        "source_backing_status": "review_required",
        "review_status": review_status_for_backing("review_required"),
        "publish_ready": False,
    }
    if _ELIMINATION_RULE_ID in rules:
        declared = str(getattr(node, "operation", "") or "")
        changes["operation"] = UNRESOLVED_ELIMINATION_OPERATION
        changes["derivation_operations"] = [
            op for op in (getattr(node, "derivation_operations", []) or [])
            if "eliminat" not in str(op).lower()
        ]
        reasons.append(ELIMINATION_TARGET_UNRESOLVED_REASON)
        note = (
            f"declared operation {declared!r} names no eliminated symbol; "
            "kept as a generic step."
        )
        prior = str(getattr(node, "review_reason", "") or "")
        changes["review_reason"] = f"{note} {prior}".strip()
    changes["review_reasons"] = _ordered_unique(reasons)
    return replace(node, **changes)


def demote_nodes(result, plan: dict[str, set[str]]):
    """plan のノードを降格した新しい ComponentGraphResult を返す（入力は不変）。

    降格したノードに触れる辺も debug 層に移す（debug ノードが辺を公開層へ引き上げない）。
    """
    demoted_ids = set(plan)
    nodes = [
        _demote_node(n, plan[n.component_id]) if n.component_id in demoted_ids else n
        for n in (result.nodes or [])
    ]
    edges = [
        replace(e, graph_layer=GRAPH_LAYER_DEBUG)
        if (e.source in demoted_ids or e.target in demoted_ids) else e
        for e in (result.edges or [])
    ]
    return replace(result, nodes=nodes, edges=edges)


def try_demote_graph(result, graph_errors: list[dict]):
    """降格 → 再検証。error がゼロになれば (降格版, 降格したノード ID 列) を返す。

    降格できない・再検証で error が残る場合は (None, []) を返す（呼び出し側は従来どおり
    グラフを保存しない）。
    """
    if result is None:
        return None, []
    plan = plan_node_demotion(graph_errors)
    if not plan:
        return None, []
    known = {n.component_id for n in (result.nodes or [])}
    if not set(plan) <= known:
        return None, []
    demoted = demote_nodes(result, plan)
    issues = ComponentGraphValidator().validate(demoted)
    if any(getattr(i, "severity", "") == "error" for i in issues):
        return None, []
    demoted = replace(demoted, validation_issues=list(issues))
    return demoted, sorted(plan)
