"""Tests for ComponentAssemblyValidator."""
from episteme_graph.agents.component_assembly.schema import (
    COMPONENTS_VERSION,
    CartridgeContext,
    ComponentAssemblyResult,
    ComponentRecord,
)
from episteme_graph.agents.component_assembly.validator import ComponentAssemblyValidator

VALIDATOR = ComponentAssemblyValidator()


def _component(**kwargs):
    defaults = dict(
        component_id="comp_1",
        component_type="RelationComponent",
        label="total-rate relation",
        summary="Reusable relation component.",
        inputs=[{"text": "heavy quark limit", "claim_ids": ["claim:b1:s1"]}],
        outputs=[{"text": "sum rule", "equation_ids": ["eq_1"]}],
        preconditions=[{"text": "valid in heavy quark limit"}],
        cautions=[{"text": "form-factor uncertainty applies"}],
        dependencies=[],
        evidence_refs={
            "claim_ids": ["claim:b1:s1"],
            "equation_ids": ["eq_1"],
            "thesis_refs": ["central_thesis"],
            "dsl_refs": {"node_ids": ["n1"], "edge_ids": ["e1"]},
        },
        reason="Relation is explicitly supported by claims and equations.",
        confidence=0.84,
        review_notes=[],
    )
    defaults.update(kwargs)
    return ComponentRecord(**defaults)


def _result(components=None, assembly_hints=None, confidence=0.8):
    return ComponentAssemblyResult(
        "doc",
        COMPONENTS_VERSION,
        None,
        [_component()] if components is None else components,
        [] if assembly_hints is None else assembly_hints,
        [],
        confidence,
    )


def test_valid_component_has_no_errors():
    assert not [i for i in VALIDATOR.validate(_result()) if i.severity == "error"]


def test_no_components_is_error():
    assert any(i.rule_id == "no_components" for i in VALIDATOR.validate(_result(components=[])))


def test_invalid_component_type_is_error():
    result = _result(components=[_component(component_type="BadComponent")])
    assert any(i.rule_id == "invalid_component_type" for i in VALIDATOR.validate(result))


def test_invalid_dependency_type_is_error():
    component = _component(dependencies=[{"dependency_type": "bad", "component_refs": [], "reason": "bad"}])
    assert any(i.rule_id == "invalid_dependency_type" for i in VALIDATOR.validate(_result(components=[component])))


def test_missing_dependency_component_is_warning():
    component = _component(dependencies=[{"dependency_type": "requires", "component_refs": ["missing"], "reason": "missing"}])
    assert any(i.rule_id == "dependency_missing_component" for i in VALIDATOR.validate(_result(components=[component])))


def test_confidence_out_of_range_is_error():
    result = _result(components=[_component(confidence=1.2)], confidence=-0.1)
    issues = VALIDATOR.validate(result)
    assert sum(1 for i in issues if i.rule_id == "confidence_out_of_range") == 2


def test_strong_component_without_evidence_is_warning():
    component = _component(evidence_refs={"claim_ids": [], "equation_ids": [], "thesis_refs": [], "dsl_refs": {"node_ids": [], "edge_ids": []}})
    assert any(i.rule_id == "strong_component_without_evidence" for i in VALIDATOR.validate(_result(components=[component])))


def test_relation_component_without_output_is_warning():
    assert any(i.rule_id == "relation_component_without_output" for i in VALIDATOR.validate(_result(components=[_component(outputs=[])])))


def test_invalid_assembly_hint_is_error():
    result = _result(assembly_hints=[{"hint_type": "bad", "component_ids": ["comp_1"], "reason": "bad"}])
    assert any(i.rule_id == "invalid_assembly_hint_type" for i in VALIDATOR.validate(result))


def test_cartridge_component_and_relation_types_are_allowed():
    cartridge = CartridgeContext(
        "test",
        {},
        {"component_types": [{"id": "PaperRelationComponent", "required_fields": ["outputs"]}]},
        {"relation_types": [{"id": "REQUIRES"}]},
        {},
    )
    component = _component(
        component_type="PaperRelationComponent",
        dependencies=[{"dependency_type": "requires", "component_refs": [], "reason": "cartridge relation"}],
    )
    assert not [i for i in VALIDATOR.validate(_result(components=[component]), cartridge) if i.severity == "error"]
