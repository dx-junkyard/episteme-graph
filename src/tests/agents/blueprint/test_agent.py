"""Tests for BlueprintAgent."""
from __future__ import annotations

from episteme_graph.agents.blueprint import BlueprintAgent, NarrativeStep
from episteme_graph.agents.component_assembly.schema import (
    COMPONENTS_VERSION,
    ComponentAssemblyResult,
    ComponentRecord,
)
from episteme_graph.agents.course_mapping.schema import (
    CourseMappingResult,
    CourseTopicMapping,
)


def _comp(component_id: str, component_type: str, **kwargs) -> ComponentRecord:
    defaults = dict(
        component_id=component_id,
        component_type=component_type,
        label=component_id,
        summary=f"summary of {component_id}",
        inputs=[],
        outputs=[],
        preconditions=[],
        cautions=[],
        dependencies=[],
        evidence_refs={
            "claim_ids": [],
            "equation_ids": [],
            "thesis_refs": [],
            "dsl_refs": {"node_ids": [], "edge_ids": []},
        },
        reason="",
        confidence=0.8,
        review_notes=[],
        internal_flow=[],
    )
    defaults.update(kwargs)
    return ComponentRecord(**defaults)


def _components(*records: ComponentRecord) -> ComponentAssemblyResult:
    return ComponentAssemblyResult(
        document_id="doc_test",
        components_version=COMPONENTS_VERSION,
        cartridge_id=None,
        components=list(records),
        assembly_hints=[],
        review_notes=[],
        confidence=0.8,
    )


def _course(linked_ids: list[str], **topic_kwargs) -> CourseMappingResult:
    topic_defaults = dict(
        title="topic",
        description="desc",
        linked_component_ids=linked_ids,
        learning_objectives=["objective_1"],
        prerequisite_concepts=["pre_1"],
        blackbox_policy={
            "blackbox_equations": ["eq_skip"],
            "expand_equations": ["eq_show"],
        },
        assessment_prompts=[],
    )
    topic_defaults.update(topic_kwargs)
    return CourseMappingResult(
        document_id="doc_test",
        cartridge_id=None,
        topics=[CourseTopicMapping(**topic_defaults)],
    )


def test_blueprint_orders_components_by_role_priority():
    course = _course(["c_result", "c_relation", "c_setup"])
    components = _components(
        _comp("c_result", "ClaimBundleComponent"),
        _comp("c_relation", "RelationComponent"),
        _comp("c_setup", "TheoryComponent"),
    )
    result = BlueprintAgent().run(course, components)
    roles = [s.role for s in result.narrative_arc]
    assert roles == ["problem_setup", "core_relation", "result_summary"]
    assert result.narrative_arc[0].linked_component_ids == ["c_setup"]
    assert result.narrative_arc[1].linked_component_ids == ["c_relation"]


def test_blueprint_attaches_blackbox_policy_to_steps():
    course = _course(["c_relation"])
    components = _components(_comp("c_relation", "RelationComponent"))
    result = BlueprintAgent().run(course, components)
    step = result.narrative_arc[0]
    assert step.blackbox_equations == ["eq_skip"]
    assert step.expand_equations == ["eq_show"]


def test_blueprint_warns_on_unknown_component_ref():
    course = _course(["c_missing"])
    components = _components(_comp("c_relation", "RelationComponent"))
    result = BlueprintAgent().run(course, components)
    rules = {i.rule_id for i in result.validation_issues}
    assert "blueprint_unknown_component_ref" in rules


def test_blueprint_warns_when_no_core_relation():
    course = _course(["c_uncertainty"])
    components = _components(_comp("c_uncertainty", "UncertaintyComponent"))
    result = BlueprintAgent().run(course, components)
    rules = {i.rule_id for i in result.validation_issues}
    assert "blueprint_missing_core_relation" in rules


def test_blueprint_errors_on_empty_course():
    course = CourseMappingResult(document_id="doc_test", cartridge_id=None, topics=[])
    components = _components(_comp("c_relation", "RelationComponent"))
    result = BlueprintAgent().run(course, components)
    rules = {i.rule_id for i in result.validation_issues}
    assert "blueprint_missing_course_topics" in rules
    assert result.narrative_arc == []


def test_blueprint_invalid_audience_level_warning():
    course = _course(["c_relation"])
    components = _components(_comp("c_relation", "RelationComponent"))
    result = BlueprintAgent().run(course, components, audience_level="weird_level")
    rules = {i.rule_id for i in result.validation_issues}
    assert "blueprint_invalid_audience_level" in rules


def test_blueprint_topic_missing_objectives_warning():
    course = _course(["c_relation"], learning_objectives=[])
    components = _components(_comp("c_relation", "RelationComponent"))
    result = BlueprintAgent().run(course, components)
    rules = {i.rule_id for i in result.validation_issues}
    assert "blueprint_topic_missing_learning_objectives" in rules


def test_blueprint_visual_strategy_per_role():
    course = _course(["c_relation", "c_uncertainty", "c_diag"])
    components = _components(
        _comp("c_relation", "RelationComponent"),
        _comp("c_uncertainty", "UncertaintyComponent"),
        _comp("c_diag", "DiagnosticComponent"),
    )
    result = BlueprintAgent().run(course, components)
    by_role = {s.role: s.visual_strategy for s in result.narrative_arc}
    assert by_role["core_relation"] == "equation_map"
    assert by_role["uncertainty_evaluation"] == "uncertainty_breakdown"
    assert by_role["diagnostic"] == "comparison_plot"


def test_blueprint_rationale_enricher_hook():
    course = _course(["c_relation"])
    components = _components(_comp("c_relation", "RelationComponent"))

    def enricher(step: NarrativeStep, _all):
        step.rationale = f"enriched:{step.role}"
        return step

    result = BlueprintAgent(rationale_enricher=enricher).run(course, components)
    assert result.narrative_arc[0].rationale == "enriched:core_relation"
