"""Tests for ContextualExplanationValidator, including the E3 generic-explanation
hallucination guard (design §5.1 hard error rules)."""
from __future__ import annotations

from episteme_graph.agents.contextual_explanation.schema import (
    ElementExplanationInput,
    ElementExplanationResult,
)
from episteme_graph.agents.contextual_explanation.validator import (
    ContextualExplanationValidator,
)

VALIDATOR = ContextualExplanationValidator()


def _input(**kwargs) -> ElementExplanationInput:
    defaults = dict(
        element_type="theory_component",
        element_id="e1",
        local_text="Defines the effective operators.",
        upper_context=[{"relation": "supports", "kind": "thesis", "text": "Central thesis text."}],
        lower_context=[{"relation": "constructed_from", "kind": "equation", "text": "eq_1 details."}],
        library_excerpt=None,
    )
    defaults.update(kwargs)
    return ElementExplanationInput(**defaults)


def _result(**kwargs) -> ElementExplanationResult:
    defaults = dict(
        element_id="e1",
        element_type="theory_component",
        contextual_explanation="This component defines the operators that back the thesis.",
        generic_explanation=None,
        evidence_quote="Central thesis text.",
        reason="grounded in upper_context",
        confidence=0.8,
        skipped_reason=None,
    )
    defaults.update(kwargs)
    return ElementExplanationResult(**defaults)


def test_valid_result_has_no_errors():
    issues = VALIDATOR.validate([_result()], [_input()])
    assert not [i for i in issues if i.severity == "error"]


def test_generic_explanation_without_library_excerpt_is_hard_error():
    """E3 hallucination guard: no confirmed L-layer link -> no generic_explanation, ever."""
    result = _result(generic_explanation="A generic textbook explanation the model just knows.")
    issues = VALIDATOR.validate([result], [_input(library_excerpt=None)])
    rule_ids = {i.rule_id for i in issues if i.severity == "error"}
    assert "generic_without_library_excerpt" in rule_ids


def test_generic_explanation_with_library_excerpt_is_allowed():
    excerpt = {
        "entry_id": "lib_1", "version_no": 1, "name": "Effective Hamiltonian",
        "summary": "summary", "body_excerpt": "body",
    }
    result = _result(generic_explanation="Rephrased from the library entry.")
    issues = VALIDATOR.validate([result], [_input(library_excerpt=excerpt)])
    rule_ids = {i.rule_id for i in issues if i.severity == "error"}
    assert "generic_without_library_excerpt" not in rule_ids


def test_output_element_id_not_in_input_is_hard_error():
    issues = VALIDATOR.validate([_result(element_id="ghost")], [_input()])
    rule_ids = {i.rule_id for i in issues if i.severity == "error"}
    assert "unknown_element_id" in rule_ids


def test_explanation_without_evidence_quote_is_repair_target():
    result = _result(evidence_quote="")
    issues = VALIDATOR.validate([result], [_input()])
    rule_ids = {i.rule_id for i in issues if i.severity == "error"}
    assert "explanation_missing_evidence_quote" in rule_ids


def test_no_explanation_with_evidence_quote_absent_and_no_skip_reason_is_ok_when_skipped():
    """An element with no explanation is fine as long as skipped_reason is set."""
    result = _result(
        contextual_explanation=None,
        generic_explanation=None,
        evidence_quote="",
        skipped_reason="no_context_available",
    )
    issues = VALIDATOR.validate([result], [_input()])
    assert not [i for i in issues if i.severity == "error"]


def test_empty_result_without_skipped_reason_is_error():
    result = _result(
        contextual_explanation=None,
        generic_explanation=None,
        evidence_quote="",
        skipped_reason=None,
    )
    issues = VALIDATOR.validate([result], [_input()])
    rule_ids = {i.rule_id for i in issues if i.severity == "error"}
    assert "empty_result_without_skipped_reason" in rule_ids


def test_confidence_out_of_range_is_error():
    issues = VALIDATOR.validate([_result(confidence=1.5)], [_input()])
    rule_ids = {i.rule_id for i in issues if i.severity == "error"}
    assert "confidence_out_of_range" in rule_ids


def test_duplicate_element_output_is_error():
    issues = VALIDATOR.validate([_result(), _result()], [_input()])
    rule_ids = {i.rule_id for i in issues if i.severity == "error"}
    assert "duplicate_element_output" in rule_ids


def test_missing_element_output_is_error():
    other = _input(element_id="e2")
    issues = VALIDATOR.validate([_result(element_id="e1")], [_input(), other])
    rule_ids = {i.rule_id for i in issues if i.severity == "error"}
    assert "missing_element_output" in rule_ids
    assert any(i.field == "elements[e2]" for i in issues if i.rule_id == "missing_element_output")


def test_element_type_mismatch_is_warning_not_error():
    result = _result(element_type="figure")  # input element_type is theory_component
    issues = VALIDATOR.validate([result], [_input()])
    rule_ids_error = {i.rule_id for i in issues if i.severity == "error"}
    rule_ids_warning = {i.rule_id for i in issues if i.severity == "warning"}
    assert "element_type_mismatch" not in rule_ids_error
    assert "element_type_mismatch" in rule_ids_warning


def test_does_not_flag_overreach_for_empty_upper_lower_context():
    """Design explicitly does not attempt to detect over-specific claims when
    upper/lower context are empty -- that check is out of scope (too fuzzy)."""
    sparse_input = _input(upper_context=[], lower_context=[])
    result = _result(
        contextual_explanation="This is definitely the central pillar of the whole paper.",
        evidence_quote="Defines the effective operators.",
    )
    issues = VALIDATOR.validate([result], [sparse_input])
    # No rule exists for "overreach" -- only the generic hard errors above apply.
    assert not [i for i in issues if i.severity == "error"]
