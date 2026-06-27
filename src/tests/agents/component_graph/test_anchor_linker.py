"""Issue #449: thesis-anchor フラグの component graph への決定論的伝搬テスト。

ドメイン非依存（claim/equation ID の重なりのみで判定）であることを、
物理・生物の 2 ドメイン fixture で検証する。
"""
from __future__ import annotations

from types import SimpleNamespace

from episteme_graph.agents.component_graph.anchor_linker import (
    link_component_thesis_anchors,
)
from episteme_graph.agents.component_graph.schema import (
    ComponentGraphNode,
    ComponentGraphResult,
)


def _thesis(claim_ids=None, equation_ids=None, supporting=None):
    return SimpleNamespace(
        central_thesis={"claim_ids": claim_ids or [], "equation_ids": equation_ids or []},
        supporting_subclaim_ids=supporting or [],
    )


def _result(nodes):
    return ComponentGraphResult(
        document_id="doc",
        graph_schema_version="1",
        cartridge_id=None,
        nodes=nodes,
        edges=[],
        review_notes=[],
        confidence=1.0,
    )


def test_physics_domain_flags_claim_overlap_node():
    nodes = [
        ComponentGraphNode(component_id="n1", label="Assumption", component_type="TheoryOperationNode",
                           linked_claim_ids=["c_locality"]),
        ComponentGraphNode(component_id="n_goal", label="Conclusion", component_type="TheoryOperationNode",
                           output_claim_ids=["c_incompleteness"]),
    ]
    result = _result(nodes)
    anchors = link_component_thesis_anchors(_thesis(claim_ids=["c_incompleteness"]), result)
    assert anchors == ["n_goal"]
    assert nodes[1].is_thesis_anchor is True
    assert nodes[0].is_thesis_anchor is False


def test_biology_domain_flags_equation_overlap_and_propagates_to_parent():
    nodes = [
        ComponentGraphNode(component_id="main_stage", label="Consistency relation",
                           component_type="TheoryOperationNode", member_component_ids=["detail_eq"]),
        ComponentGraphNode(component_id="detail_eq", label="Derive", component_type="EquationOperationNode",
                           parent_component_id="main_stage", linked_equation_ids=["eq_growth_rate"]),
    ]
    result = _result(nodes)
    anchors = link_component_thesis_anchors(_thesis(equation_ids=["eq_growth_rate"]), result)
    # detail node matches and its aggregating main node is propagated.
    assert set(anchors) == {"detail_eq", "main_stage"}
    assert all(n.is_thesis_anchor for n in nodes)


def test_supporting_subclaims_also_anchor():
    nodes = [ComponentGraphNode(component_id="n1", label="X", component_type="TheoryOperationNode",
                                linked_claim_ids=["c_sub"])]
    anchors = link_component_thesis_anchors(_thesis(claim_ids=["c_head"], supporting=["c_sub"]), _result(nodes))
    assert anchors == ["n1"]


def test_no_thesis_ids_flags_nothing():
    nodes = [ComponentGraphNode(component_id="n1", label="X", component_type="TheoryOperationNode",
                                output_claim_ids=["c1"])]
    assert link_component_thesis_anchors(_thesis(), _result(nodes)) == []
    assert nodes[0].is_thesis_anchor is False


def test_tolerates_none_inputs():
    assert link_component_thesis_anchors(None, None) == []
    assert link_component_thesis_anchors(_thesis(claim_ids=["c"]), None) == []


def test_flag_serializes_in_payload():
    nodes = [ComponentGraphNode(component_id="n_goal", label="Conclusion",
                                component_type="TheoryOperationNode", output_claim_ids=["c_main"])]
    result = _result(nodes)
    link_component_thesis_anchors(_thesis(claim_ids=["c_main"]), result)
    payload = result.to_graph_payload()
    assert payload["nodes"][0]["is_thesis_anchor"] is True
