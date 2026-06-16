"""Tests for the generic theory-operation framework (issues #395 / #396 / #397)."""

from __future__ import annotations

from episteme_graph.agents.theory_operations import (
    CORE_OPERATION_FAMILIES,
    UNKNOWN_OPERATION,
    classify_operation,
    operation_family,
)


def test_core_families_are_broad_and_domain_neutral():
    # No paper-specific term may appear in any core family name.
    forbidden = ("skew", "kurt", "bias", "galaxy", "spectral")
    for fam in CORE_OPERATION_FAMILIES:
        for term in forbidden:
            assert term not in fam


def test_generic_verbs_map_to_families():
    assert operation_family("define the relation") == "define_relation"
    assert operation_family("assume the field is weak") == "impose_assumption"
    assert operation_family("construct a model") == "construct_model"
    assert operation_family("linearize the equation") == "transform_representation"
    assert operation_family("solve and eliminate the parameter") == "derive_consequence"
    assert operation_family("evaluate the constraint condition") == "evaluate_condition"
    assert operation_family("compare the two limits") == "compare_cases"
    assert operation_family("validate numerically") == "validate_or_test"
    assert operation_family("apply to forecasting") == "apply_to_context"
    assert operation_family("state the limitation") == "state_limitation"


def test_paper_specific_operation_maps_to_generic_family_not_specific_key():
    # A paper-flavoured operation string still yields a GENERIC family.
    c = classify_operation("linearize the skewness bias equation")
    assert c.operation_family == "transform_representation"
    assert c.operation_subtype is None  # no core subtype without a cartridge


def test_unknown_operation_is_review_required():
    c = classify_operation("frobnicate the wibble")
    assert c.operation_family == UNKNOWN_OPERATION
    assert c.review_required is True
    assert "operation_family_unrecognized" in c.review_reasons


def test_cartridge_adapter_supplies_subtype_without_changing_core_families():
    class _Cartridge:
        ontology = {
            "operation_subtype_hints": [
                {
                    "when_terms_present": ["skewness", "bias"],
                    "operation_family": "transform_representation",
                    "operation_subtype": "bias_linearization",
                }
            ]
        }

    c = classify_operation("linearize the skewness bias equation", cartridge=_Cartridge())
    # Family stays in the stable core set; subtype is cartridge-sourced.
    assert c.operation_family == "transform_representation"
    assert c.operation_subtype == "bias_linearization"
    assert c.subtype_source == "cartridge"
    # Without the cartridge the family is unchanged (core stable).
    assert operation_family("linearize the skewness bias equation") == "transform_representation"


def test_cartridge_with_unknown_family_is_review_required():
    class _Cartridge:
        ontology = {
            "operation_subtype_hints": [
                {"when_terms_present": ["foo"], "operation_family": "not_a_real_family"}
            ]
        }

    c = classify_operation("foo bar", cartridge=_Cartridge())
    assert c.operation_family == UNKNOWN_OPERATION
    assert c.review_required is True
