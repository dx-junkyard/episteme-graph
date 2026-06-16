"""Tests for ComponentGranularityAnalyzer Step 1 behavior."""
from episteme_graph.agents.component_assembly.granularity_analyzer import ComponentGranularityAnalyzer
from episteme_graph.agents.component_assembly.schema import (
    COMPONENTS_VERSION,
    ComponentAssemblyResult,
    ComponentRecord,
)


ANALYZER = ComponentGranularityAnalyzer()


def _component(**kwargs):
    defaults = dict(
        component_id="comp_1",
        component_type="RelationComponent",
        label="linearized bias relation",
        summary="Reusable relation component.",
        inputs=[{"name": "input"}],
        outputs=[{"name": "output"}],
        preconditions=[],
        cautions=[],
        dependencies=[],
        evidence_refs={
            "claim_ids": ["claim_1"],
            "equation_ids": ["eq_1"],
            "thesis_refs": [],
            "dsl_refs": {"node_ids": [], "edge_ids": []},
        },
        reason="source backed",
        confidence=0.8,
        review_notes=[],
        responsibility_type="equation_system",
        primary_operation="linearize",
        internal_flow=[{"from": "eq_0", "relation": "linearize", "to": "eq_1"}],
    )
    defaults.update(kwargs)
    return ComponentRecord(**defaults)


def _result(components):
    return ComponentAssemblyResult(
        "doc",
        COMPONENTS_VERSION,
        None,
        components,
        [],
        [],
        0.8,
    )


def test_analyzer_annotates_without_changing_component_count():
    components = [
        _component(
            component_id="comp_big",
            evidence_refs={
                "claim_ids": ["claim_1"],
                "equation_ids": [f"eq_{i}" for i in range(10)],
                "thesis_refs": [],
                "dsl_refs": {"node_ids": [], "edge_ids": []},
            },
            linked_equation_ids=[f"eq_{i}" for i in range(10)],
        )
    ]
    result = _result(components)

    analyzed = ANALYZER.analyze(result)

    assert len(analyzed.components) == 1
    component = analyzed.components[0]
    assert component.component_id == "comp_big"
    assert component.component_quality["granularity_status"] == "too_coarse"
    assert component.split_recommendation["required"] is True
    assert component.split_recommendation["suggested_components"]
    assert analyzed.diagnostics["component_granularity"]["component_quality"][0]["component_id"] == "comp_big"


def test_analyzer_detects_mixed_responsibility_with_canonical_aliases():
    # Domain-neutral (#395/#396): mixed responsibilities are detected from the
    # generic operation families of the component's own operations, not from
    # paper-specific summary text. Here a "construct model" + "derive" component
    # mixes model construction and derivation.
    component = _component(
        label="Model construction and derived consequence",
        summary="Constructs a model and derives a consequence.",
        responsibility_type="final_constraint",
        primary_operation="construct model",
        secondary_operations=["derive consequence"],
        internal_flow=[{"from": "eq_0", "relation": "derive", "to": "eq_1"}],
    )

    analyzed = ANALYZER.analyze(_result([component]))

    quality = analyzed.components[0].component_quality
    assert analyzed.components[0].responsibility_type == "constraint"
    assert quality["granularity_status"] == "mixed_responsibility"
    assert quality["split_required"] is True
    detected = {item["responsibility_type"] for item in quality["suggested_split"]}
    # Generic families map to >= 2 distinct responsibilities (constraint from the
    # declared responsibility_type, plus model/derivation from the operations).
    assert "constraint" in detected
    assert len(detected) >= 2


def test_teaching_takeaway_is_not_a_step1_split_reason():
    component = _component(
        component_id="comp_teaching",
        review_status="source_backed",
        teaching_takeaway=(
            "This sentence is deliberately long, multi-clause, and pedagogical, "
            "but the Step 1 analyzer must not use teaching granularity."
        ),
    )

    analyzed = ANALYZER.analyze(_result([component]))

    quality = analyzed.components[0].component_quality
    assert quality["granularity_status"] == "good"
    assert quality["split_required"] is False
    assert not any("teaching" in reason for reason in quality["split_reasons"])
