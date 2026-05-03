"""Tests for RhetoricalRoleValidator."""
from episteme_graph.agents.rhetorical_role.schema import (
    BlockRoleAnnotation,
    CartridgeContext,
    RhetoricalRoleResult,
    SpanAnnotation,
)
from episteme_graph.agents.rhetorical_role.validator import RhetoricalRoleValidator

VALIDATOR = RhetoricalRoleValidator()


def _span(**kwargs):
    defaults = dict(
        span_id="span_001",
        text="We assume a narrow-width limit.",
        char_start=0,
        char_end=len("We assume a narrow-width limit."),
        role_labels=["assumption"],
        is_claim_candidate=True,
        is_reject_candidate=False,
        confidence=0.9,
        reason="assumption cue",
    )
    defaults.update(kwargs)
    return SpanAnnotation(**defaults)


def _result(span=None):
    annotation = BlockRoleAnnotation("b1", "sec_1", "assumptions", [span or _span()])
    return RhetoricalRoleResult(
        document_id="doc",
        cartridge_id=None,
        role_annotations=[annotation],
        summary_stats={"claim_candidate_spans": 1, "reject_candidate_spans": 0},
    )


def test_valid_result_has_no_errors():
    issues = VALIDATOR.validate(
        _result(),
        {"b1": "We assume a narrow-width limit."},
    )
    assert not [i for i in issues if i.severity == "error"]


def test_empty_annotations_is_error():
    result = RhetoricalRoleResult("doc", None, [], {})
    issues = VALIDATOR.validate(result, {})
    assert any(i.rule_id == "no_role_annotations" for i in issues)


def test_invalid_char_range_is_error():
    issues = VALIDATOR.validate(_result(_span(char_start=5, char_end=4)), {"b1": "abcdef"})
    assert any(i.rule_id == "invalid_char_range" for i in issues)


def test_span_text_mismatch_is_error():
    span = _span(text="different", char_start=0, char_end=9)
    issues = VALIDATOR.validate(_result(span), {"b1": "We assume a narrow-width limit."})
    assert any(i.rule_id == "span_text_mismatch" for i in issues)


def test_invalid_role_label_is_error():
    issues = VALIDATOR.validate(_result(_span(role_labels=["bad_role"])), {"b1": "We assume a narrow-width limit."})
    assert any(i.rule_id == "invalid_role_label" for i in issues)


def test_conflicting_candidate_flags_is_error():
    issues = VALIDATOR.validate(
        _result(_span(is_claim_candidate=True, is_reject_candidate=True)),
        {"b1": "We assume a narrow-width limit."},
    )
    assert any(i.rule_id == "conflicting_candidate_flags" for i in issues)


def test_confidence_out_of_range_is_error():
    issues = VALIDATOR.validate(_result(_span(confidence=1.5)), {"b1": "We assume a narrow-width limit."})
    assert any(i.rule_id == "confidence_out_of_range" for i in issues)


def test_figure_narration_claim_only_is_warning():
    span = _span(
        text="Figure 1 shows the distribution.",
        char_start=0,
        char_end=len("Figure 1 shows the distribution."),
        role_labels=["figure_narration"],
        is_claim_candidate=True,
        is_reject_candidate=False,
    )
    issues = VALIDATOR.validate(_result(span), {"b1": "Figure 1 shows the distribution."})
    assert any(i.rule_id == "exclusion_role_marked_claim_only" for i in issues)


def test_cartridge_alias_without_content_role_is_warning():
    cartridge = CartridgeContext(
        cartridge_id="test",
        ontology={},
        validation_rules={},
        aliases={"R Lambda c": ["RΛc"]},
    )
    span = _span(
        text="RΛc is shown in figure 1.",
        char_start=0,
        char_end=len("RΛc is shown in figure 1."),
        role_labels=["figure_narration"],
        is_claim_candidate=False,
        is_reject_candidate=True,
    )
    issues = VALIDATOR.validate(_result(span), {"b1": "RΛc is shown in figure 1."}, cartridge)
    assert any(i.rule_id == "cartridge_alias_without_content_role" for i in issues)
