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


def test_responsibility_type_serializes_as_required_type_alias():
    component = ComponentRecord(
        "comp_001", "RelationComponent", "skewness basis", "summary",
        [], [], [], [], [],
        {"claim_ids": [], "equation_ids": [], "thesis_refs": [], "dsl_refs": {"node_ids": [], "edge_ids": []}},
        "reason", 0.8, [],
        responsibility_type="observable_basis",
        primary_operation="define",
        secondary_operations=["normalize"],
        split_recommendation={"required": False, "suggested_components": []},
    )
    result = ComponentAssemblyResult("doc", COMPONENTS_VERSION, None, [component], [], [], 0.8)
    payload = json.loads(result.to_json())
    assert payload["components"][0]["responsibility_type"] == "observable_basis"
    assert payload["components"][0]["type"] == "observable_basis"
    assert payload["components"][0]["primary_operation"] == "define"
    assert payload["components"][0]["secondary_operations"] == ["normalize"]
    assert payload["components"][0]["split_recommendation"]["required"] is False
    restored = ComponentAssemblyResult.from_dict(payload)
    assert restored.components[0].responsibility_type == "observable_basis"
    assert restored.components[0].primary_operation == "define"
    assert restored.components[0].secondary_operations == ["normalize"]


def test_component_quality_round_trip():
    component = ComponentRecord(
        "comp_001", "RelationComponent", "coarse component", "summary",
        [], [], [], [], [],
        {"claim_ids": [], "equation_ids": ["eq_1"], "thesis_refs": [], "dsl_refs": {"node_ids": [], "edge_ids": []}},
        "reason", 0.8, [],
        component_quality={
            "component_id": "comp_001",
            "granularity_status": "too_coarse",
            "equation_count": 1,
            "claim_count": 0,
            "derivation_step_count": 0,
            "responsibility_count": 2,
            "source_scope_width": 1,
            "split_required": True,
            "split_reasons": ["responsibility type has more than one primary value"],
            "suggested_split": [{"name": "Constraint", "responsibility_type": "constraint"}],
        },
    )
    result = ComponentAssemblyResult("doc", COMPONENTS_VERSION, None, [component], [], [], 0.8)
    restored = ComponentAssemblyResult.from_dict(json.loads(result.to_json()))
    assert restored.components[0].component_quality["granularity_status"] == "too_coarse"
    assert restored.components[0].component_quality["suggested_split"][0]["responsibility_type"] == "constraint"


def test_claim_support_fields_round_trip():
    component = ComponentRecord(
        "comp_001", "RelationComponent", "bias elimination", "summary",
        [], [], [], [], [],
        {"claim_ids": ["claim_1"], "equation_ids": ["eq_1"], "thesis_refs": [], "dsl_refs": {"node_ids": [], "edge_ids": []}},
        "reason", 0.8, [],
        support_role="derivation_core",
        supports_claim_ids=["claim_1"],
        support_distance_to_headline_claim=0,
        support_kind="derivational",
    )
    result = ComponentAssemblyResult("doc", COMPONENTS_VERSION, None, [component], [], [], 0.8)
    restored = ComponentAssemblyResult.from_dict(json.loads(result.to_json()))
    restored_component = restored.components[0]
    assert restored_component.support_role == "derivation_core"
    assert restored_component.supports_claim_ids == ["claim_1"]
    assert restored_component.support_distance_to_headline_claim == 0
    assert restored_component.support_kind == "derivational"


def test_concept_fields_round_trip():
    component = ComponentRecord(
        "comp_001", "RelationComponent", "bias elimination", "summary",
        [], [], [], [], [],
        {"claim_ids": ["claim_1"], "equation_ids": ["eq_1"], "thesis_refs": [], "dsl_refs": {"node_ids": [], "edge_ids": []}},
        "reason", 0.8, [],
        concepts=["Skewness", "Kurtosis"],
        prerequisite_concepts=["Galaxy bias"],
        introduced_concepts=["Skewness"],
        reused_concepts=["Kurtosis"],
    )
    result = ComponentAssemblyResult("doc", COMPONENTS_VERSION, None, [component], [], [], 0.8)
    restored = ComponentAssemblyResult.from_dict(json.loads(result.to_json()))
    restored_component = restored.components[0]
    assert restored_component.concepts == ["Skewness", "Kurtosis"]
    assert restored_component.prerequisite_concepts == ["Galaxy bias"]
    assert restored_component.introduced_concepts == ["Skewness"]
    assert restored_component.reused_concepts == ["Kurtosis"]
