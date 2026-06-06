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
        internal_flow=[
            {"from": "eq_1", "relation": "combine_with", "to": "eq_2"},
        ],
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


def test_qualified_dependency_type_is_normalized_to_qualifies():
    component = _component(dependencies=[{"dependency_type": "qualified", "component_refs": [], "reason": "qualifies relation"}])
    issues = VALIDATOR.validate(_result(components=[component]))
    assert not any(i.rule_id == "invalid_dependency_type" for i in issues)
    assert component.dependencies[0]["dependency_type"] == "qualifies"


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


def test_derivation_component_without_operation_is_warning():
    """issue #300: derivation-path components with equations should declare an operation."""
    component = _component(operation="")
    assert any(
        i.rule_id == "derivation_component_missing_operation"
        for i in VALIDATOR.validate(_result(components=[component]))
    )


def test_derivation_component_with_operation_has_no_operation_warning():
    component = _component(operation="derive")
    assert not any(
        i.rule_id == "derivation_component_missing_operation"
        for i in VALIDATOR.validate(_result(components=[component]))
    )


def test_invalid_assembly_hint_is_error():
    result = _result(assembly_hints=[{"hint_type": "bad", "component_ids": ["comp_1"], "reason": "bad"}])
    assert any(i.rule_id == "invalid_assembly_hint_type" for i in VALIDATOR.validate(result))


def test_relation_component_missing_internal_flow_is_warning():
    component = _component(internal_flow=[])
    issues = VALIDATOR.validate(_result(components=[component]))
    assert any(i.rule_id == "component_missing_internal_flow" for i in issues)


def test_claim_bundle_does_not_require_internal_flow():
    component = _component(
        component_type="ClaimBundleComponent",
        internal_flow=[],
        outputs=[{"text": "summary"}],
    )
    issues = VALIDATOR.validate(_result(components=[component]))
    assert not any(i.rule_id == "component_missing_internal_flow" for i in issues)


def test_internal_flow_incomplete_step_is_error():
    component = _component(
        internal_flow=[{"from": "eq_1", "to": "eq_2"}],  # missing relation
    )
    issues = VALIDATOR.validate(_result(components=[component]))
    assert any(i.rule_id == "internal_flow_incomplete_step" for i in issues)


def test_multi_input_without_internal_flow_emits_warning():
    component = _component(
        component_type="TheoryComponent",
        inputs=[
            {"text": "premise A"},
            {"text": "premise B"},
            {"text": "premise C"},
        ],
        internal_flow=[],
    )
    issues = VALIDATOR.validate(_result(components=[component]))
    assert any(i.rule_id == "component_multi_io_without_flow" for i in issues)


def test_invalid_responsibility_type_is_error():
    component = _component(responsibility_type="bad_type")
    issues = VALIDATOR.validate(_result(components=[component]))
    assert any(i.rule_id == "invalid_responsibility_type" for i in issues)


def test_responsibility_alias_is_normalized_to_canonical_type():
    component = _component(responsibility_type="observation_design")
    issues = VALIDATOR.validate(_result(components=[component]))
    assert not any(i.rule_id == "invalid_responsibility_type" for i in issues)
    assert component.responsibility_type == "observation_model"


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


# ---------------------------------------------------------------------------
# Tests for cross-reference ID validation (issue #249)
# ---------------------------------------------------------------------------

from episteme_graph.agents.component_assembly.schema import ComponentAssemblyLLMInput
from episteme_graph.agents.component_assembly.validator import _build_available_id_index


def _make_llm_input(
    available_claims=None,
    available_evidence=None,
    available_equations=None,
    available_dsl_nodes=None,
    available_dsl_edges=None,
    available_derivation_ids=None,
):
    return ComponentAssemblyLLMInput(
        document_id="doc",
        cartridge_id=None,
        accepted_claims=[],
        equations=[],
        thesis_nodes=[],
        dsl_nodes=[],
        dsl_edges=[],
        allowed_component_types=["RelationComponent"],
        allowed_dependency_types=["requires"],
        available_claims=available_claims or [],
        available_evidence=available_evidence or [],
        available_equations=available_equations or [],
        available_dsl_nodes=available_dsl_nodes or [],
        available_dsl_edges=available_dsl_edges or [],
        available_derivation_ids=available_derivation_ids or [],
    )


def test_valid_linkage_no_errors():
    """Component referencing real IDs → no cross-reference errors."""
    llm_input = _make_llm_input(
        available_claims=[{"claim_id": "claim_span_001"}],
        available_evidence=[{"evidence_id": "ev_0001"}],
        available_equations=[{"equation_id": "eq_001"}],
    )
    component = _component(
        evidence_refs={
            "claim_ids": ["claim_span_001"],
            "evidence_ids": ["ev_0001"],
            "equation_ids": ["eq_001"],
            "dsl_refs": {"node_ids": [], "edge_ids": []},
        }
    )
    issues = VALIDATOR.validate(_result(components=[component]), llm_input=llm_input)
    cross_errors = [i for i in issues if i.rule_id in (
        "unresolved_claim_id", "unresolved_evidence_id", "unresolved_equation_id"
    )]
    assert cross_errors == []


def test_derivation_component_with_unclassified_equations_warns():
    llm_input = _make_llm_input(
        available_claims=[{"claim_id": "claim_span_001"}],
        available_equations=[{
            "equation_id": "eq_001",
            "confidence_policy": {"can_support_claim": True},
        }],
    )
    component = _component(
        evidence_refs={
            "claim_ids": ["claim_span_001"],
            "evidence_ids": [],
            "equation_ids": ["eq_001"],
            "dsl_refs": {"node_ids": [], "edge_ids": []},
        },
        linked_equation_ids=["eq_001"],
    )
    issues = VALIDATOR.validate(_result(components=[component]), llm_input=llm_input)
    assert any(i.rule_id == "component_equations_not_role_classified" for i in issues)


def test_non_supporting_equation_cannot_support_claim():
    llm_input = _make_llm_input(
        available_claims=[{"claim_id": "claim_span_001"}],
        available_equations=[{
            "equation_id": "eq_review",
            "confidence_policy": {
                "can_support_claim": False,
                "must_not_treat_as_source_extracted": True,
            },
        }],
    )
    component = _component(
        evidence_refs={
            "claim_ids": ["claim_span_001"],
            "evidence_ids": [],
            "equation_ids": ["eq_review"],
            "dsl_refs": {"node_ids": [], "edge_ids": []},
        },
        linked_equation_ids=["eq_review"],
        input_equation_ids=["eq_review"],
        output_equation_ids=[],
    )
    issues = VALIDATOR.validate(_result(components=[component]), llm_input=llm_input)
    assert any(i.rule_id == "claim_support_uses_non_supporting_equation" for i in issues)


def test_review_required_output_cannot_be_auto_accepted():
    llm_input = _make_llm_input(
        available_equations=[{
            "equation_id": "eq_review",
            "needs_math_review": True,
            "confidence_policy": {
                "can_support_claim": False,
                "must_not_treat_as_source_extracted": True,
            },
        }],
    )
    component = _component(
        evidence_refs={
            "claim_ids": [],
            "evidence_ids": [],
            "equation_ids": ["eq_review"],
            "dsl_refs": {"node_ids": [], "edge_ids": []},
        },
        linked_equation_ids=["eq_review"],
        input_equation_ids=[],
        output_equation_ids=["eq_review"],
        review_status="auto_accepted",
    )
    issues = VALIDATOR.validate(_result(components=[component]), llm_input=llm_input)
    assert any(i.rule_id == "component_output_uses_non_supporting_equation" for i in issues)
    assert any(i.rule_id == "review_required_output_component_auto_accepted" for i in issues)


def test_unresolved_claim_id_is_error():
    """Component referencing a non-existent claim ID → unresolved_claim_id error."""
    llm_input = _make_llm_input(
        available_claims=[{"claim_id": "claim_real"}],
    )
    component = _component(
        evidence_refs={
            "claim_ids": ["claim_MISSING"],
            "evidence_ids": [],
            "equation_ids": [],
            "dsl_refs": {"node_ids": [], "edge_ids": []},
        }
    )
    issues = VALIDATOR.validate(_result(components=[component]), llm_input=llm_input)
    error_ids = [i.rule_id for i in issues if i.severity == "error"]
    assert "unresolved_claim_id" in error_ids


def test_unresolved_evidence_id_is_error():
    """Component referencing a non-existent evidence ID → unresolved_evidence_id error."""
    llm_input = _make_llm_input(
        available_evidence=[{"evidence_id": "ev_real"}],
    )
    component = _component(
        evidence_refs={
            "claim_ids": [],
            "evidence_ids": ["ev_MISSING"],
            "equation_ids": [],
            "dsl_refs": {"node_ids": [], "edge_ids": []},
        }
    )
    issues = VALIDATOR.validate(_result(components=[component]), llm_input=llm_input)
    error_ids = [i.rule_id for i in issues if i.severity == "error"]
    assert "unresolved_evidence_id" in error_ids


def test_unresolved_equation_id_is_error():
    """Component referencing a non-existent equation ID → unresolved_equation_id error."""
    llm_input = _make_llm_input(
        available_equations=[{"equation_id": "eq_real"}],
    )
    component = _component(
        evidence_refs={
            "claim_ids": [],
            "evidence_ids": [],
            "equation_ids": ["eq_MISSING"],
            "dsl_refs": {"node_ids": [], "edge_ids": []},
        }
    )
    issues = VALIDATOR.validate(_result(components=[component]), llm_input=llm_input)
    error_ids = [i.rule_id for i in issues if i.severity == "error"]
    assert "unresolved_equation_id" in error_ids


def test_summary_only_component_is_warning():
    """Component with no claim_ids and no evidence_ids → summary_only_component warning."""
    llm_input = _make_llm_input(
        available_claims=[{"claim_id": "claim_real"}],
        available_evidence=[{"evidence_id": "ev_real"}],
    )
    component = _component(
        evidence_refs={
            "claim_ids": [],
            "evidence_ids": [],
            "equation_ids": [],
            "dsl_refs": {"node_ids": [], "edge_ids": []},
        }
    )
    issues = VALIDATOR.validate(_result(components=[component]), llm_input=llm_input)
    warning_ids = [i.rule_id for i in issues if i.severity == "warning"]
    assert "summary_only_component" in warning_ids


def test_no_cross_ref_validation_without_llm_input():
    """Without llm_input, no cross-reference errors are raised."""
    component = _component(
        evidence_refs={
            "claim_ids": ["any_claim"],
            "evidence_ids": ["any_ev"],
            "equation_ids": [],
            "dsl_refs": {"node_ids": [], "edge_ids": []},
        }
    )
    issues = VALIDATOR.validate(_result(components=[component]))
    cross_errors = [i for i in issues if i.rule_id in (
        "unresolved_claim_id", "unresolved_evidence_id"
    )]
    assert cross_errors == []


def test_build_available_id_index():
    """_build_available_id_index extracts the correct sets."""
    llm_input = _make_llm_input(
        available_claims=[{"claim_id": "c1"}, {"claim_id": "c2"}],
        available_evidence=[{"evidence_id": "ev1"}],
        available_equations=[{"equation_id": "eq1"}],
        available_dsl_nodes=[{"node_id": "n1"}],
        available_dsl_edges=[{"edge_id": "e1"}],
    )
    idx = _build_available_id_index(llm_input)
    assert idx["claim_ids"] == {"c1", "c2"}
    assert idx["evidence_ids"] == {"ev1"}
    assert idx["equation_ids"] == {"eq1"}
    assert idx["dsl_node_ids"] == {"n1"}
    assert idx["dsl_edge_ids"] == {"e1"}
