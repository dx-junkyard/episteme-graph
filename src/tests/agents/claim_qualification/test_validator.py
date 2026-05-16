"""Tests for ClaimQualificationValidator."""
from episteme_graph.agents.claim_qualification.schema import (
    CartridgeContext,
    ClaimQualificationResult,
    QualifiedSpanRecord,
)
from episteme_graph.agents.claim_qualification.validator import ClaimQualificationValidator

VALIDATOR = ClaimQualificationValidator()


def _record(**kwargs):
    defaults = dict(
        span_id="s1",
        block_id="b1",
        section_id="sec_1",
        text="We assume X.",
        role_labels=["assumption"],
        qualification={
            "status": "accepted",
            "claim_tier": "paper_core",
            "claim_type_candidate": "assumption",
            "granularity": "good",
            "evidence_adequacy": "sufficient",
            "reviewability": "good",
        },
        edit_suggestions={
            "should_split": False,
            "should_merge_with_prev": False,
            "should_merge_with_next": False,
            "normalized_text_hint": "",
        },
        reason="good claim",
        confidence=0.9,
    )
    defaults.update(kwargs)
    return QualifiedSpanRecord(**defaults)


def _result(record=None):
    return ClaimQualificationResult(
        document_id="doc",
        cartridge_id=None,
        qualified_spans=[record or _record()],
        rejected_spans=[],
        deferred_spans=[],
        summary_stats={},
    )


def test_valid_record_has_no_errors():
    issues = VALIDATOR.validate(_result())
    assert not [i for i in issues if i.severity == "error"]


def test_invalid_status_is_error():
    r = _record(qualification={**_record().qualification, "status": "bad"})
    issues = VALIDATOR.validate(_result(r))
    assert any(i.rule_id == "invalid_status" for i in issues)


def test_invalid_claim_tier_is_error():
    r = _record(qualification={**_record().qualification, "claim_tier": "bad"})
    issues = VALIDATOR.validate(_result(r))
    assert any(i.rule_id == "invalid_claim_tier" for i in issues)


def test_confidence_out_of_range_is_error():
    issues = VALIDATOR.validate(_result(_record(confidence=2.0)))
    assert any(i.rule_id == "confidence_out_of_range" for i in issues)


def test_prior_work_as_paper_core_is_error():
    r = _record(role_labels=["prior_work"])
    issues = VALIDATOR.validate(_result(r))
    assert any(i.rule_id == "prior_work_as_paper_core" for i in issues)


def test_too_broad_without_split_is_warning():
    q = {**_record().qualification, "granularity": "too_broad"}
    issues = VALIDATOR.validate(_result(_record(qualification=q)))
    assert any(i.rule_id == "too_broad_without_split" for i in issues)


def test_accepted_broken_evidence_is_warning():
    q = {**_record().qualification, "evidence_adequacy": "broken"}
    issues = VALIDATOR.validate(_result(_record(qualification=q)))
    assert any(i.rule_id == "accepted_broken_evidence" for i in issues)


def test_invalid_claim_type_candidate_is_error():
    q = {**_record().qualification, "claim_type_candidate": "invented_type"}
    issues = VALIDATOR.validate(_result(_record(qualification=q)))
    assert any(i.rule_id == "invalid_claim_type_candidate" for i in issues)


def test_cartridge_claim_type_is_allowed():
    cartridge = CartridgeContext(
        cartridge_id="test",
        ontology={"claim_types": [{"id": "domain_relation"}]},
        validation_rules={},
    )
    q = {**_record().qualification, "claim_type_candidate": "domain_relation"}
    issues = VALIDATOR.validate(_result(_record(qualification=q)), cartridge)
    assert not any(i.rule_id == "invalid_claim_type_candidate" for i in issues)
