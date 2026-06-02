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


def test_equation_role_fields_round_trip():
    component = ComponentRecord(
        "comp_001", "RelationComponent", "bias elimination", "summary", [], [], [], [], [],
        {"claim_ids": [], "equation_ids": ["eq_in", "eq_out"], "thesis_refs": [], "dsl_refs": {"node_ids": [], "edge_ids": []}},
        "reason", 0.8, [],
        input_equation_ids=["eq_in"],
        output_equation_ids=["eq_out"],
        eliminated_symbols=["b_2"],
        retained_symbols=["S_3"],
        equation_confidence_summary={"all_source_backed": True},
    )
    result = ComponentAssemblyResult("doc", COMPONENTS_VERSION, None, [component], [], [], 0.8)
    restored = ComponentAssemblyResult.from_dict(json.loads(result.to_json()))
    restored_component = restored.components[0]
    assert restored_component.input_equation_ids == ["eq_in"]
    assert restored_component.output_equation_ids == ["eq_out"]
    assert restored_component.eliminated_symbols == ["b_2"]


def test_operation_and_refinement_report_round_trip():
    """issue #300: operation field and refinement_report survive serialization."""
    component = ComponentRecord(
        "comp_001", "RelationComponent", "skewness consistency", "summary",
        [], [], [], [], [],
        {"claim_ids": [], "equation_ids": [], "thesis_refs": [], "dsl_refs": {"node_ids": [], "edge_ids": []}},
        "reason", 0.8, [],
        operation="derive",
    )
    result = ComponentAssemblyResult(
        "doc", COMPONENTS_VERSION, None, [component], [], [], 0.8,
        refinement_report={"split_count": 1, "split_actions": [], "warnings": []},
    )
    restored = ComponentAssemblyResult.from_dict(json.loads(result.to_json()))
    assert restored.components[0].operation == "derive"
    assert restored.refinement_report["split_count"] == 1
