"""Tests for NarrativeAnnotator (issue #360)."""
from __future__ import annotations

from unittest.mock import patch

from episteme_graph.agents.component_graph.schema import (
    ComponentGraphEdge,
    ComponentGraphNode,
    ComponentGraphResult,
    GRAPH_SCHEMA_VERSION,
)
from episteme_graph.agents.narrative_annotator.agent import NarrativeAnnotator
from episteme_graph.agents.narrative_annotator.input_builder import (
    NarrativeInputBuilder,
)
from episteme_graph.agents.narrative_annotator.repair import _parse_raw
from episteme_graph.agents.narrative_annotator.schema import (
    NarrativeAnnotationResult,
)
from episteme_graph.agents.narrative_annotator.validator import (
    NarrativeValidator,
    verify_graph_unchanged,
)


def _graph():
    main_a = ComponentGraphNode(
        component_id="main_a", label="Theory basis",
        component_type="TheoryOperationNode", graph_layer="main",
        description="Defines the operators.",
        linked_derivation_ids=["derivation_1"],
        display_order=0,
    )
    main_b = ComponentGraphNode(
        component_id="main_b", label="Consistency relation",
        component_type="TheoryOperationNode", graph_layer="main",
        description="Derives the final relation.",
        display_order=1,
    )
    detail = ComponentGraphNode(
        component_id="detail_1", label="derive eq_2",
        component_type="EquationOperationNode", graph_layer="equation_detail",
        parent_component_id="main_a",
    )
    edge = ComponentGraphEdge(
        edge_id="edge_ab", source="main_a", target="main_b",
        edge_type="derives", support_status="source_inferred",
        evidence_claims=[], reasoning="basis feeds the relation",
    )
    detail_edge = ComponentGraphEdge(
        edge_id="edge_detail", source="detail_1", target="main_b",
        edge_type="derives", support_status="source_inferred",
        evidence_claims=[], reasoning="detail",
    )
    return ComponentGraphResult(
        document_id="doc", graph_schema_version=GRAPH_SCHEMA_VERSION,
        cartridge_id=None, nodes=[main_a, main_b, detail],
        edges=[edge, detail_edge], review_notes=[], confidence=0.8,
    )


class _Thesis:
    central_thesis = {"text": "A single relation links the ratios."}


class _Chain:
    derivation_id = "derivation_1"
    teaching_takeaway = "The operators define every later stage."


class _Derivations:
    chains = [_Chain()]


def _llm_output(node_ids=("main_a", "main_b"), edge_ids=("edge_ab",)):
    return {
        "graph_summary": "The paper links the ratios through one relation.",
        "node_narratives": [
            {"component_id": nid, "narrative_role": f"Role of {nid}.",
             "reason": "desc", "confidence": 0.8}
            for nid in node_ids
        ],
        "edge_narratives": [
            {"edge_id": eid, "transition_text": f"Transition {eid}.",
             "reason": "edge", "confidence": 0.7}
            for eid in edge_ids
        ],
        "review_notes": [],
        "confidence": 0.8,
    }


# ---------------------------------------------------------------------------
# input builder
# ---------------------------------------------------------------------------

def test_input_builder_packages_main_layer_only():
    llm_input = NarrativeInputBuilder().build(
        _graph(), thesis=_Thesis(), derivations=_Derivations(),
    )
    node_ids = [n["component_id"] for n in llm_input.main_nodes]
    assert node_ids == ["main_a", "main_b"]  # detail node excluded
    edge_ids = [e["edge_id"] for e in llm_input.main_edges]
    assert edge_ids == ["edge_ab"]  # detail edge excluded
    assert llm_input.central_thesis_text.startswith("A single relation")


def test_input_builder_rolls_up_teaching_takeaways():
    llm_input = NarrativeInputBuilder().build(
        _graph(), derivations=_Derivations(),
    )
    node_a = next(n for n in llm_input.main_nodes if n["component_id"] == "main_a")
    assert node_a["teaching_takeaways"] == [
        "The operators define every later stage."
    ]


# ---------------------------------------------------------------------------
# agent
# ---------------------------------------------------------------------------

def test_agent_annotates_without_modifying_graph():
    graph = _graph()
    snapshot = graph.to_dict()
    agent = NarrativeAnnotator()
    with patch.object(agent._llm_client, "generate", return_value=_llm_output()):
        result = agent.run(graph, thesis=_Thesis(), derivations=_Derivations())
    assert verify_graph_unchanged(snapshot, graph.to_dict())
    assert result.graph_summary
    assert result.maturity_source == "llm_proposed"
    assert not [i for i in result.validation_issues if i.severity == "error"]
    assert {n.component_id for n in result.node_narratives} == {"main_a", "main_b"}


def test_agent_llm_failure_returns_fallback():
    agent = NarrativeAnnotator()
    with patch.object(agent._llm_client, "generate", side_effect=RuntimeError("down")):
        result = agent.run(_graph())
    assert result.graph_summary == ""
    assert any(
        i.rule_id == "narrative_annotation_failed" for i in result.validation_issues
    )


def test_agent_repairs_unknown_component_id():
    agent = NarrativeAnnotator()
    bad = _llm_output(node_ids=("ghost",))
    good = _llm_output()
    with patch.object(agent._llm_client, "generate", side_effect=[bad, good]):
        result = agent.run(_graph(), thesis=_Thesis())
    assert {n.component_id for n in result.node_narratives} == {"main_a", "main_b"}
    assert not [i for i in result.validation_issues if i.severity == "error"]


def test_agent_without_main_nodes_degrades():
    graph = _graph()
    for node in graph.nodes:
        node.graph_layer = "equation_detail"
    agent = NarrativeAnnotator()
    result = agent.run(graph)
    assert any(
        i.rule_id == "narrative_annotation_failed" for i in result.validation_issues
    )


# ---------------------------------------------------------------------------
# validator
# ---------------------------------------------------------------------------

def _validate(raw, graph=None):
    builder = NarrativeInputBuilder()
    llm_input = builder.build(graph or _graph())
    result = _parse_raw(raw, llm_input)
    return NarrativeValidator().validate(result, llm_input), result


def test_validator_flags_unknown_ids_and_lengths():
    raw = _llm_output(node_ids=("ghost",), edge_ids=("ghost_edge",))
    raw["graph_summary"] = "x" * 2000
    raw["node_narratives"][0]["narrative_role"] = "y" * 500
    issues, _ = _validate(raw)
    rule_ids = {i.rule_id for i in issues}
    assert "unknown_component_id" in rule_ids
    assert "unknown_edge_id" in rule_ids
    assert "graph_summary_too_long" in rule_ids
    assert "narrative_role_too_long" in rule_ids


def test_validator_warns_on_missing_coverage():
    raw = _llm_output(node_ids=("main_a",), edge_ids=())
    issues, _ = _validate(raw)
    warnings = {i.rule_id for i in issues if i.severity == "warning"}
    assert "node_without_narrative" in warnings
    assert "edge_without_narrative" in warnings
    assert not [i for i in issues if i.severity == "error"]


def test_validator_enforces_provisional_maturity():
    raw = _llm_output()
    issues, result = _validate(raw)
    assert result.maturity_source == "llm_proposed"
    result.maturity_source = "auto_confirmed"
    issues = NarrativeValidator().validate(result)
    assert any(i.rule_id == "invalid_maturity_source" for i in issues)


# ---------------------------------------------------------------------------
# schema round trip
# ---------------------------------------------------------------------------

def test_result_round_trips_via_dict():
    builder = NarrativeInputBuilder()
    llm_input = builder.build(_graph())
    result = _parse_raw(_llm_output(), llm_input)
    reloaded = NarrativeAnnotationResult.from_dict(result.to_dict())
    assert reloaded.graph_summary == result.graph_summary
    assert len(reloaded.node_narratives) == len(result.node_narratives)
    assert reloaded.maturity_source == "llm_proposed"
