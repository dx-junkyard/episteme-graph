"""Tests for CourseMappingAgent."""
from __future__ import annotations

from episteme_graph.agents.component_assembly.schema import ComponentRecord
from episteme_graph.agents.course_mapping import CourseMappingAgent


def _make_component(
    component_id: str,
    *,
    label: str = "Origin of Sum-Rule Coefficients",
    component_type: str = "RelationComponent",
    preconditions: list[dict] | None = None,
    equation_ids: list[str] | None = None,
    outputs: list[dict] | None = None,
) -> ComponentRecord:
    rec = ComponentRecord(
        component_id=component_id,
        component_type=component_type,
        label=label,
        summary="A reusable derivation component.",
        inputs=[],
        outputs=outputs or [{"name": "coefficient set"}],
        preconditions=preconditions or [
            {"condition": "heavy quark limit"},
            {"condition": "zero-recoil limit"},
        ],
        cautions=[],
        dependencies=[],
        evidence_refs={"claim_ids": []},
        reason="",
        confidence=0.7,
        review_notes=[],
    )
    rec.equation_ids = equation_ids or ["eq_3_5", "eq_3_14"]  # type: ignore[attr-defined]
    return rec


def test_each_component_becomes_topic_with_links():
    components = [
        _make_component("component_origin_of_coefficients"),
        _make_component(
            "component_uncertainty_budget",
            label="Uncertainty Budget",
            component_type="UncertaintyComponent",
        ),
    ]
    agent = CourseMappingAgent()
    result = agent.run("doc_test", components, cartridge_id="particle_physics")

    assert len(result.topics) == 2
    for topic in result.topics:
        assert topic.linked_component_ids, "linked_component_ids must not be empty"
        assert topic.learning_objectives
        assert topic.prerequisite_concepts
        assert topic.blackbox_policy


def test_topic_concepts_grounded_in_component_concepts():
    comp = _make_component("component_concepts")
    comp.concepts = ["Skewness", "Kurtosis"]  # type: ignore[attr-defined]
    comp.prerequisite_concepts = ["Galaxy bias"]  # type: ignore[attr-defined]
    comp.introduced_concepts = ["Skewness"]  # type: ignore[attr-defined]

    agent = CourseMappingAgent()
    result = agent.run("doc_test", [comp], cartridge_id="particle_physics")

    topic = result.topics[0]
    assert topic.prerequisite_concepts == ["Galaxy bias"]
    assert topic.introduced_concepts == ["Skewness"]


def test_no_components_returns_validation_error():
    agent = CourseMappingAgent()
    result = agent.run("doc_test", [], cartridge_id=None)
    rules = {i.rule_id for i in result.validation_issues}
    assert "course_mapping_no_components" in rules
    assert result.topics == []


def test_blackbox_policy_for_theory_component():
    comp = _make_component(
        "component_theory",
        component_type="TheoryComponent",
        equation_ids=["eq_a", "eq_b", "eq_c", "eq_d"],
    )
    agent = CourseMappingAgent()
    result = agent.run("doc_test", [comp])
    policy = result.topics[0].blackbox_policy
    assert policy["show_derivation_depth"] == "medium"
    assert policy["expand_equations"] == ["eq_c", "eq_d"]
    assert policy["blackbox_equations"] == ["eq_a", "eq_b"]


def test_method_component_gets_shallow_depth():
    comp = _make_component(
        "component_method",
        component_type="MethodComponent",
        equation_ids=["eq_x"],
    )
    agent = CourseMappingAgent()
    result = agent.run("doc_test", [comp])
    policy = result.topics[0].blackbox_policy
    assert policy["show_derivation_depth"] == "shallow"
    assert policy["blackbox_equations"] == ["eq_x"]


def test_prerequisite_concepts_extracted_from_preconditions():
    comp = _make_component(
        "comp_a",
        preconditions=[
            {"condition": "branching ratio definition"},
            {"condition": "Standard Model prediction"},
        ],
    )
    agent = CourseMappingAgent()
    topic = agent.run("doc_test", [comp]).topics[0]
    assert "branching ratio definition" in topic.prerequisite_concepts
    assert "Standard Model prediction" in topic.prerequisite_concepts


def test_introduced_concepts_fall_back_to_concepts_when_absent():
    # issue #8: when a component has concepts but no explicit introduced_concepts,
    # the topic's introduced_concepts fall back to the component concepts.
    comp = _make_component("comp_a")
    comp.concepts = ["Skewness", "Kurtosis"]  # type: ignore[attr-defined]
    # introduced_concepts intentionally left empty
    agent = CourseMappingAgent()
    topic = agent.run("doc_test", [comp]).topics[0]
    assert topic.introduced_concepts == ["Skewness", "Kurtosis"]


def test_prerequisite_concepts_grounded_in_component_not_preconditions():
    # When component.prerequisite_concepts exist, they are authoritative and the
    # precondition text is NOT mixed in (no phantom prerequisites).
    comp = _make_component(
        "comp_a",
        preconditions=[{"condition": "heavy quark limit"}],
    )
    comp.prerequisite_concepts = ["Galaxy bias"]  # type: ignore[attr-defined]
    agent = CourseMappingAgent()
    topic = agent.run("doc_test", [comp]).topics[0]
    assert topic.prerequisite_concepts == ["Galaxy bias"]
    assert "heavy quark limit" not in topic.prerequisite_concepts


def test_empty_component_concepts_safe_fallback():
    # No concepts at all on the component: introduced_concepts stays empty and the
    # topic still builds (prerequisites fall back to precondition text).
    comp = _make_component(
        "comp_a",
        preconditions=[{"condition": "zero-recoil limit"}],
    )
    agent = CourseMappingAgent()
    topic = agent.run("doc_test", [comp]).topics[0]
    assert topic.introduced_concepts == []
    assert "zero-recoil limit" in topic.prerequisite_concepts
