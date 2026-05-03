"""Tests for component assembly schema helpers."""
import json

from episteme_graph.agents.component_assembly.schema import ComponentAssemblyResult, ComponentRecord, COMPONENTS_VERSION


def test_to_json_round_trip():
    component = ComponentRecord(
        "comp_001", "RelationComponent", "sum rule", "summary", [], [], [], [], [],
        {"claim_ids": ["c1"], "equation_ids": [], "thesis_refs": [], "dsl_refs": {"node_ids": [], "edge_ids": []}},
        "reason", 0.8, []
    )
    result = ComponentAssemblyResult("doc", COMPONENTS_VERSION, None, [component], [], [], 0.8)
    restored = ComponentAssemblyResult.from_dict(json.loads(result.to_json()))
    assert restored.components[0].component_type == "RelationComponent"
