"""Tests for ComponentRefiner (issue #300)."""
from episteme_graph.agents.component_assembly.component_refiner import ComponentRefiner
from episteme_graph.agents.component_assembly.schema import (
    COMPONENTS_VERSION,
    ComponentAssemblyResult,
    ComponentRecord,
)
from episteme_graph.agents.derivation_chain.schema import (
    DerivationChainRecord,
    DerivationChainResult,
    DerivationStep,
)

REFINER = ComponentRefiner()


def _coarse_component(**kwargs):
    defaults = dict(
        component_id="comp_bias",
        component_type="RelationComponent",
        label="Linear elimination of nonlinear bias parameters",
        summary="A coarse summary bundling several operations.",
        inputs=[{"name": "bias model"}],
        outputs=[{"name": "consistency relations"}],
        preconditions=[],
        cautions=[],
        dependencies=[],
        evidence_refs={"claim_ids": ["claim_1"], "equation_ids": []},
        reason="coarse",
        confidence=0.8,
        review_notes=[],
        internal_flow=[],
        linked_claim_ids=["claim_1"],
        linked_derivation_ids=["deriv_bias"],
        review_status="auto_accepted",
        source_scope={"section_ids": ["s3"], "block_ids": ["b10"], "page_ranges": [[4, 6]]},
    )
    defaults.update(kwargs)
    return ComponentRecord(**defaults)


def _result(components):
    return ComponentAssemblyResult(
        document_id="doc",
        components_version=COMPONENTS_VERSION,
        cartridge_id=None,
        components=components,
        assembly_hints=[],
        review_notes=[],
        confidence=0.8,
    )


def _bias_derivation():
    operations = [
        ("linearize_skewness_bias_dependence", ["eq_skew"], ["eq_skew_lin"]),
        ("linearize_kurtosis_bias_dependence", ["eq_kurt"], ["eq_kurt_lin"]),
        ("solve_second_order_bias", ["eq_skew_lin"], ["eq_b2"]),
        ("solve_third_order_bias", ["eq_kurt_lin"], ["eq_b3"]),
        ("derive_skewness_consistency_relation", ["eq_b2"], ["eq_cons_skew"]),
        ("derive_first_kurtosis_consistency_relation", ["eq_b3"], ["eq_cons_kurt1"]),
        ("derive_second_kurtosis_consistency_relation", ["eq_b3"], ["eq_cons_kurt2"]),
    ]
    steps = [
        DerivationStep(
            step_id=f"step_{i}",
            input_equation_ids=inp,
            operation=op,
            output_equation_ids=out,
        )
        for i, (op, inp, out) in enumerate(operations)
    ]
    chain = DerivationChainRecord(
        derivation_id="deriv_bias",
        document_id="doc",
        source_section_ids=["s3"],
        steps=steps,
        linked_component_ids=["comp_bias"],
    )
    return DerivationChainResult(document_id="doc", cartridge_id=None, chains=[chain])


def test_coarse_component_split_into_reusable_theory_units():
    result = _result([_coarse_component()])
    refined = REFINER.refine(result, llm_input=None, derivations=_bias_derivation())

    # Acceptance criterion #1: the coarse component is gone, replaced by children.
    ids = [c.component_id for c in refined.components]
    assert "comp_bias" not in ids
    # Issue #308: split by reusable theory-unit family, not per equation step.
    # 7 operations across {linearize, solve, derive} -> 3 reusable units.
    assert len(refined.components) == 3
    assert {c.operation for c in refined.components} == {"linearize", "solve", "derive"}

    for child in refined.components:
        # criterion #2/#3: a reusable unit with non-empty inputs/operation/outputs.
        assert child.operation
        assert child.inputs
        assert child.outputs
        # Labels/summaries are theory-object centric, never a bare generic op.
        assert child.label.lower() not in ("transform", "relate", "")
        assert "refined theory operation" not in child.summary.lower()
        # criterion #4: derivation children have internal_flow.
        assert child.internal_flow
        # criterion #5: equation roles populated.
        assert child.input_equation_ids or child.output_equation_ids
        # Component public I/O is the theory operation; equations are supporting detail.
        assert len(child.inputs) <= 1
        assert len(child.outputs) <= 1
        assert child.linked_equation_ids
        # source_scope inherited from parent (refiner responsibility #5).
        assert child.source_scope == {
            "section_ids": ["s3"], "block_ids": ["b10"], "page_ranges": [[4, 6]],
        }


def test_multiple_equation_steps_collapse_into_one_unit():
    # Issue #308: the derive family bundles 3 consistency-relation steps into a
    # single reusable unit whose internal_flow keeps every step.
    result = _result([_coarse_component()])
    refined = REFINER.refine(result, llm_input=None, derivations=_bias_derivation())
    derive_unit = next(c for c in refined.components if c.operation == "derive")
    assert len(derive_unit.internal_flow) >= 3
    # It carries the consistency-relation outputs from all three steps.
    assert "eq_cons_skew" in derive_unit.output_equation_ids
    assert "eq_cons_kurt1" in derive_unit.output_equation_ids
    assert "eq_cons_kurt2" in derive_unit.output_equation_ids


def test_derive_unit_exposes_output_equations():
    result = _result([_coarse_component()])
    refined = REFINER.refine(result, llm_input=None, derivations=_bias_derivation())
    derive_unit = next(c for c in refined.components if c.operation == "derive")
    # criterion #7: consistency-relation (derive) unit exposes output equation IDs.
    assert derive_unit.output_equation_ids


def test_refinement_report_records_split():
    result = _result([_coarse_component()])
    refined = REFINER.refine(result, llm_input=None, derivations=_bias_derivation())
    report = refined.refinement_report
    assert report["split_count"] == 1
    action = report["split_actions"][0]
    assert action["parent_component_id"] == "comp_bias"
    assert len(action["child_component_ids"]) == 3


def test_review_required_equation_propagates_to_child_status():
    llm_input = type("LLMInput", (), {
        "equations": [{"equation_id": "eq_b2", "needs_math_review": True}],
        "available_equations": [],
    })()
    result = _result([_coarse_component()])
    refined = REFINER.refine(result, llm_input=llm_input, derivations=_bias_derivation())
    # criterion #8: review-required equations propagate to component review_status.
    solving = [c for c in refined.components if "eq_b2" in c.linked_equation_ids]
    assert solving
    for child in solving:
        assert child.review_status == "teacher_review_required"
        assert "eq_b2" in child.review_required_equation_ids


def test_generic_operation_does_not_form_its_own_unit():
    # Issue #308: a generic "transform"/"relate" step must not become a standalone
    # reusable component; it is folded into a real unit's internal_flow.
    operations = [
        ("linearize_skewness_bias_dependence", ["eq_skew"], ["eq_skew_lin"]),
        ("transform", ["eq_skew_lin"], ["eq_mid"]),
        ("solve_second_order_bias", ["eq_mid"], ["eq_b2"]),
        ("derive_skewness_consistency_relation", ["eq_b2"], ["eq_cons"]),
    ]
    steps = [
        DerivationStep(step_id=f"step_{i}", input_equation_ids=inp, operation=op, output_equation_ids=out)
        for i, (op, inp, out) in enumerate(operations)
    ]
    chain = DerivationChainRecord(
        derivation_id="deriv_bias", document_id="doc", source_section_ids=["s3"],
        steps=steps, linked_component_ids=["comp_bias"],
    )
    derivations = DerivationChainResult(document_id="doc", cartridge_id=None, chains=[chain])
    refined = REFINER.refine(_result([_coarse_component()]), llm_input=None, derivations=derivations)

    # 3 non-generic families -> 3 units; no unit is named for the generic op.
    assert {c.operation for c in refined.components} == {"linearize", "solve", "derive"}
    assert "transform" not in {c.operation for c in refined.components}
    # The generic step's equations survive inside some unit's internal_flow.
    flows = [f for c in refined.components for f in c.internal_flow]
    assert any(f.get("relation") == "transform" for f in flows)


def test_single_operation_component_not_split():
    component = _coarse_component(
        component_id="comp_single",
        label="Skewness linearization",
        linked_derivation_ids=["deriv_single"],
    )
    step = DerivationStep(
        step_id="step_0",
        input_equation_ids=["eq_a"],
        operation="linearize_skewness_bias_dependence",
        output_equation_ids=["eq_b"],
    )
    chain = DerivationChainRecord(
        derivation_id="deriv_single",
        document_id="doc",
        source_section_ids=[],
        steps=[step],
        linked_component_ids=["comp_single"],
    )
    derivations = DerivationChainResult(document_id="doc", cartridge_id=None, chains=[chain])
    result = _result([component])
    refined = REFINER.refine(result, llm_input=None, derivations=derivations)
    assert len(refined.components) == 1
    assert refined.components[0].component_id == "comp_single"
    # Single-operation components still get their operation recorded.
    assert refined.components[0].operation == "linearize"


def test_single_family_equation_bundle_rebuilt_as_operation_component():
    component = _coarse_component(
        component_id="comp_derive_many",
        label="Consistency relation derivation bundle",
        outputs=[
            {"name": "eq_cons_1", "equation_ids": ["eq_cons_1"]},
            {"name": "eq_cons_2", "equation_ids": ["eq_cons_2"]},
            {"name": "eq_cons_3", "equation_ids": ["eq_cons_3"]},
            {"name": "eq_cons_4", "equation_ids": ["eq_cons_4"]},
        ],
        linked_derivation_ids=["deriv_many"],
    )
    steps = [
        DerivationStep(
            step_id=f"step_{idx}",
            input_equation_ids=[f"eq_in_{idx}"],
            operation="derive_result",
            output_equation_ids=[f"eq_cons_{idx}"],
        )
        for idx in range(1, 5)
    ]
    chain = DerivationChainRecord(
        derivation_id="deriv_many",
        document_id="doc",
        source_section_ids=["s3"],
        steps=steps,
        linked_component_ids=["comp_derive_many"],
    )
    derivations = DerivationChainResult(document_id="doc", cartridge_id=None, chains=[chain])

    refined = REFINER.refine(_result([component]), llm_input=None, derivations=derivations)

    assert len(refined.components) == 1
    rebuilt = refined.components[0]
    assert rebuilt.component_id == "comp_derive_many"
    assert rebuilt.operation == "derive"
    assert len(rebuilt.outputs) == 1
    assert rebuilt.outputs[0]["role"] == "operation_output"
    assert set(rebuilt.output_equation_ids) == {
        "eq_cons_1",
        "eq_cons_2",
        "eq_cons_3",
        "eq_cons_4",
    }
    assert "equation dependency bundle" in refined.refinement_report["warnings"][0]


def test_dependencies_remapped_to_first_child_after_split():
    dependent = _coarse_component(
        component_id="comp_dep",
        component_type="ClaimBundleComponent",
        linked_derivation_ids=[],
        dependencies=[{
            "dependency_type": "depends_on",
            "component_refs": ["comp_bias"],
            "reason": "uses the bias result",
        }],
    )
    result = _result([_coarse_component(), dependent])
    result.assembly_hints = [{
        "hint_type": "candidate_core_component",
        "component_ids": ["comp_bias"],
        "reason": "core",
    }]
    refined = REFINER.refine(result, llm_input=None, derivations=_bias_derivation())
    dep = next(c for c in refined.components if c.component_id == "comp_dep")
    refs = dep.dependencies[0]["component_refs"]
    assert refs == ["comp_bias__op1"]
    assert refined.assembly_hints[0]["component_ids"] == ["comp_bias__op1"]


def test_non_derivation_component_left_untouched():
    component = _coarse_component(
        component_id="comp_claim",
        component_type="ClaimBundleComponent",
    )
    result = _result([component])
    refined = REFINER.refine(result, llm_input=None, derivations=_bias_derivation())
    assert len(refined.components) == 1
    assert refined.components[0].component_id == "comp_claim"
