"""Tests for ComponentOverlapCleanup."""
from episteme_graph.agents.component_assembly.overlap_cleanup import ComponentOverlapCleanup
from episteme_graph.agents.component_assembly.schema import ComponentAssemblyResult, ComponentRecord, COMPONENTS_VERSION


def _component(component_id, label):
    return ComponentRecord(
        component_id, "RelationComponent", label, "summary", [], [], [], [], [],
        {"claim_ids": [component_id], "equation_ids": [], "thesis_refs": [], "dsl_refs": {"node_ids": [], "edge_ids": []}},
        "reason", 0.8, []
    )


def test_cleanup_dedupes_components_and_dependency_refs():
    result = ComponentAssemblyResult(
        "doc", COMPONENTS_VERSION, None,
        [_component("comp_001", "sum rule"), _component("comp_002", "sum rule"), _component("comp_003", "uncertainty")],
        [], [], 0.8
    )
    result.components[2].dependencies = [{"dependency_type": "requires", "component_refs": ["comp_002"], "reason": "uses"}]
    cleaned = ComponentOverlapCleanup().cleanup(result)
    assert [c.component_id for c in cleaned.components] == ["comp_001", "comp_003"]
    assert cleaned.components[1].dependencies[0]["component_refs"] == ["comp_001"]
