"""Domain-neutral ComponentRefiner tests (issues #395 / #396 / #397).

The previous version of this file exercised one specific paper family
(observable / parameter / elimination terminology). Per #395-#397 the refiner
must split mixed-responsibility components using GENERIC operation families with
no paper-specific terminology, so these tests use only domain-neutral operations.
"""
from __future__ import annotations

import inspect

from episteme_graph.agents.component_assembly import component_refiner as refiner_mod
from episteme_graph.agents.component_assembly.component_refiner import ComponentRefiner
from episteme_graph.agents.component_assembly.granularity_analyzer import (
    ComponentGranularityAnalyzer,
)
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
ANALYZER = ComponentGranularityAnalyzer()

# Paper-specific terms that must never appear in core refinement output.
_FORBIDDEN_TERMS = ("skew", "kurt", "galaxy", "bias", "spectral", "smoothed")


def _component(**kwargs) -> ComponentRecord:
    defaults = dict(
        component_id="comp_1",
        component_type="RelationComponent",
        label="Coarse theory component",
        summary="A coarse unit bundling several distinct operations.",
        inputs=[],
        outputs=[],
        preconditions=[],
        cautions=[],
        dependencies=[],
        evidence_refs={"claim_ids": ["claim_1"], "equation_ids": []},
        reason="coarse",
        confidence=0.8,
        review_notes=[],
        internal_flow=[],
        linked_claim_ids=["claim_1"],
        linked_derivation_ids=["deriv_1"],
        review_status="auto_accepted",
        source_scope={"section_ids": ["s1"], "block_ids": ["b1"]},
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


def _derivation(operations, *, derivation_id="deriv_1", component_id="comp_1"):
    steps = [
        DerivationStep(step_id=f"step_{i}", input_equation_ids=inp, operation=op, output_equation_ids=out)
        for i, (op, inp, out) in enumerate(operations)
    ]
    chain = DerivationChainRecord(
        derivation_id=derivation_id,
        document_id="doc",
        source_section_ids=["s1"],
        steps=steps,
        linked_component_ids=[component_id],
    )
    return DerivationChainResult(document_id="doc", cartridge_id=None, chains=[chain])


def _all_equation_ids(operations):
    ids = []
    for _op, inp, out in operations:
        ids.extend(inp + out)
    return sorted(set(ids))


def _no_forbidden_terms(components):
    for c in components:
        blob = " ".join([
            str(c.label or ""), str(c.summary or ""), str(c.operation or ""),
            str(c.primary_operation or ""), str(c.responsibility_type or ""),
        ] + [str(o) for o in (c.secondary_operations or [])]).lower()
        for term in _FORBIDDEN_TERMS:
            assert term not in blob, f"paper-specific term {term!r} leaked into refined component"


# ---------------------------------------------------------------------------
# #395: core produces no paper-specific operation keys.
# ---------------------------------------------------------------------------


def test_operation_group_key_is_domain_neutral():
    src = inspect.getsource(refiner_mod._operation_group_key)
    for term in _FORBIDDEN_TERMS:
        assert term not in src.lower()
    # A paper-flavoured operation string still maps to a GENERIC family.
    assert refiner_mod._operation_group_key("linearize skewness bias equation") == "transform_representation"
    assert refiner_mod._operation_group_key("eliminate second-order bias") == "derive_consequence"


def test_no_core_branch_returns_paper_specific_keys():
    source = inspect.getsource(refiner_mod)
    for forbidden in ("linearize_skewness", "linearize_kurtosis", "eliminate_second_order"):
        # Allowed only inside comments referencing the removal; assert not returned.
        assert f'return "{forbidden}' not in source
        assert f"return '{forbidden}" not in source


# ---------------------------------------------------------------------------
# #396: refinement splits mixed-responsibility components by generic families.
# ---------------------------------------------------------------------------


def test_mixed_operation_component_splits_into_generic_units():
    operations = [
        ("define the relation", ["eq_a"], ["eq_b"]),
        ("derive the consequence", ["eq_b"], ["eq_c"]),
        ("evaluate the constraint condition", ["eq_c"], ["eq_d"]),
    ]
    component = _component(
        evidence_refs={"claim_ids": ["claim_1"], "equation_ids": _all_equation_ids(operations)},
        internal_flow=[{"from": "eq_a", "relation": "define"}, {"from": "eq_c", "relation": "derive"}],
    )
    result = _result([component])
    REFINER.refine(result, None, _derivation(operations))
    assert len(result.components) >= 2, "mixed-responsibility component should split"
    families = {c.operation for c in result.components}
    # Generic families only.
    from episteme_graph.agents.theory_operations import CORE_OPERATION_FAMILIES
    assert families <= set(CORE_OPERATION_FAMILIES)
    _no_forbidden_terms(result.components)


def test_single_operation_component_not_split():
    operations = [
        ("derive the consequence", ["eq_a"], ["eq_b"]),
        ("derive the consequence again", ["eq_b"], ["eq_c"]),
    ]
    component = _component(
        evidence_refs={"claim_ids": ["claim_1"], "equation_ids": _all_equation_ids(operations)},
    )
    result = _result([component])
    REFINER.refine(result, None, _derivation(operations))
    # One operation family -> not split into multiple units.
    assert len(result.components) == 1
    _no_forbidden_terms(result.components)


def test_refinement_report_records_split():
    operations = [
        ("define the relation", ["eq_a"], ["eq_b"]),
        ("derive the consequence", ["eq_b"], ["eq_c"]),
    ]
    component = _component(
        evidence_refs={"claim_ids": ["claim_1"], "equation_ids": _all_equation_ids(operations)},
    )
    result = _result([component])
    REFINER.refine(result, None, _derivation(operations))
    report = result.refinement_report
    assert report.get("split_actions"), "refinement report should record the split"


def test_generic_operation_does_not_form_its_own_unit():
    operations = [
        ("define the relation", ["eq_a"], ["eq_b"]),
        ("transform", ["eq_b"], ["eq_c"]),  # generic op, folded into a neighbour
        ("derive the consequence", ["eq_c"], ["eq_d"]),
    ]
    component = _component(
        evidence_refs={"claim_ids": ["claim_1"], "equation_ids": _all_equation_ids(operations)},
    )
    result = _result([component])
    REFINER.refine(result, None, _derivation(operations))
    families = [c.operation for c in result.components]
    assert "transform_representation" not in families or len([f for f in families if f]) >= 2
    _no_forbidden_terms(result.components)


def test_source_links_preserved_after_split():
    operations = [
        ("define the relation", ["eq_a"], ["eq_b"]),
        ("derive the consequence", ["eq_b"], ["eq_c"]),
    ]
    component = _component(
        evidence_refs={"claim_ids": ["claim_1"], "equation_ids": _all_equation_ids(operations)},
    )
    result = _result([component])
    REFINER.refine(result, None, _derivation(operations))
    all_eq = {e for c in result.components for e in (c.linked_equation_ids or [])}
    # Equations are reassigned to children, never dropped.
    assert {"eq_a", "eq_b", "eq_c"} <= all_eq


# ---------------------------------------------------------------------------
# #396 required cases A-F: generic operation patterns, not only equation systems.
# ---------------------------------------------------------------------------


def _granularity_split_count(primary, secondary):
    component = _component(
        component_type="RelationComponent",
        responsibility_type="",
        primary_operation=primary,
        secondary_operations=secondary,
        internal_flow=[{"from": "eq_a", "relation": primary}, {"from": "eq_b", "relation": secondary[0]}],
    )
    analyzed = ANALYZER.analyze(_result([component]))
    quality = analyzed.components[0].component_quality
    return quality


def test_case_definition_derivation_result():  # Case A
    q = _granularity_split_count("define relation", ["derive consequence"])
    assert q["split_required"] is True


def test_case_assumption_approximation_condition():  # Case B
    q = _granularity_split_count("impose assumption", ["evaluate condition"])
    assert q["split_required"] is True


def test_case_model_comparison_limitation():  # Case C
    q = _granularity_split_count("construct model", ["state limitation"])
    assert q["split_required"] is True


def test_case_classification_case_split_consequence():  # Case F
    q = _granularity_split_count("compare cases", ["derive consequence"])
    assert q["split_required"] is True


def test_refined_components_expose_responsibility_type():
    # Issue #396 review: a refined component whose operation is a broad family
    # (e.g. transform_representation) must still expose a responsibility_type.
    operations = [
        ("define the relation", ["eq_a"], ["eq_b"]),
        ("transform the representation", ["eq_b"], ["eq_c"]),
        ("derive the consequence", ["eq_c"], ["eq_d"]),
    ]
    component = _component(
        evidence_refs={"claim_ids": ["claim_1"], "equation_ids": _all_equation_ids(operations)},
    )
    result = _result([component])
    REFINER.refine(result, None, _derivation(operations))
    assert len(result.components) >= 2
    for c in result.components:
        assert c.operation, "refined component must declare an operation family"
        assert c.responsibility_type, "refined component must expose a responsibility_type"


def test_single_family_does_not_split():  # negative control
    component = _component(
        component_type="RelationComponent",
        responsibility_type="derivation",
        primary_operation="derive consequence",
        secondary_operations=[],
        internal_flow=[{"from": "eq_a", "relation": "derive", "to": "eq_b"}],
    )
    analyzed = ANALYZER.analyze(_result([component]))
    assert analyzed.components[0].component_quality["split_required"] is False


# ---------------------------------------------------------------------------
# _inherit_parent_flow — fallback for split children with fewer than 2 eqs
# ---------------------------------------------------------------------------


def test_inherit_parent_flow_filters_by_child_equations():
    """子の eq_ids に合致するステップのみ返す。"""
    from episteme_graph.agents.component_assembly.component_refiner import (
        _inherit_parent_flow,
    )

    parent = _component(
        internal_flow=[
            {"from": "eq_A", "relation": "derives", "to": "eq_B"},
            {"from": "eq_B", "relation": "derives", "to": "eq_C"},
        ]
    )
    result = _inherit_parent_flow(parent, ["eq_A"])
    assert result == [{"from": "eq_A", "relation": "derives", "to": "eq_B"}]


def test_inherit_parent_flow_falls_back_to_full_slice_when_no_match():
    """子の eq_ids が親フローに現れない場合は親フロー全体のスライスを返す。"""
    from episteme_graph.agents.component_assembly.component_refiner import (
        _inherit_parent_flow,
    )

    parent = _component(
        internal_flow=[
            {"from": "eq_A", "relation": "derives", "to": "eq_B"},
        ]
    )
    result = _inherit_parent_flow(parent, [])
    assert result == [{"from": "eq_A", "relation": "derives", "to": "eq_B"}]


def test_inherit_parent_flow_returns_empty_when_parent_has_no_flow():
    """親フローが空なら [] を返す。"""
    from episteme_graph.agents.component_assembly.component_refiner import (
        _inherit_parent_flow,
    )

    parent = _component(internal_flow=[])
    assert _inherit_parent_flow(parent, ["eq_X"]) == []


def test_split_relation_component_with_one_eq_gets_non_empty_flow():
    """RelationComponent の split 後に各子コンポーネントが non-empty internal_flow を持つこと。

    comp_005__r1 / comp_005__r2 の再現ケース: 親は eq_A→eq_B のフローを持ち、
    split 後に子が 1 本ずつ eq を受け取る。export gate が要求する non-empty flow を
    _inherit_parent_flow のフォールバックで充足すること。
    """
    operations = [
        ("define relation", ["eq_A"], ["eq_B"]),
        ("derive consequence", ["eq_B"], ["eq_C"]),
    ]
    component = _component(
        component_type="RelationComponent",
        evidence_refs={"claim_ids": ["claim_1"], "equation_ids": ["eq_A", "eq_B", "eq_C"]},
        internal_flow=[
            {"from": "eq_A", "relation": "defines", "to": "eq_B"},
            {"from": "eq_B", "relation": "derives", "to": "eq_C"},
        ],
    )
    result = _result([component])
    REFINER.refine(result, None, _derivation(operations))
    for child in result.components:
        if child.component_type in {
            "RelationComponent",
            "MethodComponent",
            "DiagnosticComponent",
            "CorrectionComponent",
        }:
            assert child.internal_flow, (
                f"{child.component_id} ({child.component_type}) has empty internal_flow"
            )
