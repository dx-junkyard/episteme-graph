"""Tests for PaperSkeletonValidator."""
import pytest

from episteme_graph.agents.paper_skeleton.schema import (
    CartridgeContext,
    LogicalBlock,
    PaperSkeletonResult,
    SKELETON_VERSION,
)
from episteme_graph.agents.paper_skeleton.validator import PaperSkeletonValidator

VALIDATOR = PaperSkeletonValidator()


def _lb(block_id="logic_1", block_type="derivation", confidence=0.8):
    return LogicalBlock(
        block_id=block_id, block_type=block_type,
        label="Test block", section_ids=[], evidence_block_ids=["blk_001"],
        summary="summary", reason="reason", confidence=confidence,
    )


def _entry(text="Valid claim.", evidence=None):
    return {
        "text": text,
        "evidence_block_ids": evidence if evidence is not None else ["blk_001"],
        "reason": "reason",
        "confidence": 0.8,
    }


def _result(**kwargs):
    defaults = dict(
        document_id="doc_test",
        skeleton_version=SKELETON_VERSION,
        cartridge_id=None,
        paper_goal=_entry(),
        central_question=_entry(),
        headline_claim=_entry("The Born approximation reproduces Rutherford scattering."),
        supporting_subclaims=[],
        logical_blocks=[_lb("l1"), _lb("l2")],
        excluded_regions=[{"region_type": "prior_work", "section_ids": [], "block_ids": [], "reason": "lit"}],
        review_notes=[],
        confidence=0.85,
    )
    defaults.update(kwargs)
    return PaperSkeletonResult(**defaults)


# ── headline_claim not empty ──────────────────────────────────────────────────

def test_empty_headline_claim_is_error():
    r = _result(headline_claim=_entry(""))
    issues = VALIDATOR.validate(r)
    assert any(i.rule_id == "empty_headline_claim" and i.severity == "error" for i in issues)


def test_valid_headline_claim_no_error():
    issues = VALIDATOR.validate(_result())
    assert not any(i.rule_id == "empty_headline_claim" for i in issues)


# ── logical_blocks count ──────────────────────────────────────────────────────

def test_too_few_logical_blocks_is_error():
    r = _result(logical_blocks=[_lb()])
    issues = VALIDATOR.validate(r)
    assert any(i.rule_id == "insufficient_logical_blocks" for i in issues)


def test_two_logical_blocks_ok():
    issues = VALIDATOR.validate(_result())
    assert not any(i.rule_id == "insufficient_logical_blocks" for i in issues)


# ── logical_block_type vocabulary ─────────────────────────────────────────────

def test_invalid_logical_block_type():
    r = _result(logical_blocks=[_lb("l1", "invalid_type"), _lb("l2")])
    issues = VALIDATOR.validate(r)
    assert any(i.rule_id == "invalid_logical_block_type" for i in issues)


def test_valid_logical_block_types_pass():
    from episteme_graph.agents.paper_skeleton.schema import LOGICAL_BLOCK_TYPES
    for bt in LOGICAL_BLOCK_TYPES:
        r = _result(logical_blocks=[_lb("l1", bt), _lb("l2", "conclusion")])
        issues = VALIDATOR.validate(r)
        assert not any(i.rule_id == "invalid_logical_block_type" for i in issues)


# ── evidence_block_ids ────────────────────────────────────────────────────────

def test_missing_evidence_is_warning():
    r = _result(paper_goal=_entry("goal", evidence=[]))
    issues = VALIDATOR.validate(r)
    assert any(i.rule_id == "missing_evidence_block_ids" and i.severity == "warning" for i in issues)


# ── excluded_regions ──────────────────────────────────────────────────────────

def test_no_prior_work_excluded_is_warning():
    r = _result(excluded_regions=[])
    issues = VALIDATOR.validate(r)
    assert any(i.rule_id == "no_excluded_prior_work_or_meta" for i in issues)


def test_meta_excluded_satisfies_rule():
    r = _result(excluded_regions=[{"region_type": "meta", "section_ids": [], "block_ids": [], "reason": "x"}])
    issues = VALIDATOR.validate(r)
    assert not any(i.rule_id == "no_excluded_prior_work_or_meta" for i in issues)


# ── headline not figure narration ─────────────────────────────────────────────

def test_figure_narration_headline_is_error():
    r = _result(headline_claim=_entry("Figure 1 shows the apparatus."))
    issues = VALIDATOR.validate(r)
    assert any(i.rule_id == "headline_claim_is_figure_narration" for i in issues)


def test_normal_claim_not_flagged_as_figure():
    issues = VALIDATOR.validate(_result())
    assert not any(i.rule_id == "headline_claim_is_figure_narration" for i in issues)


# ── confidence range ──────────────────────────────────────────────────────────

def test_confidence_out_of_range():
    r = _result(confidence=1.5)
    issues = VALIDATOR.validate(r)
    assert any(i.rule_id == "confidence_out_of_range" for i in issues)


def test_logical_block_confidence_out_of_range():
    r = _result(logical_blocks=[_lb("l1", confidence=-0.1), _lb("l2")])
    issues = VALIDATOR.validate(r)
    assert any(i.rule_id == "confidence_out_of_range" for i in issues)


def test_valid_confidence_no_error():
    issues = VALIDATOR.validate(_result())
    assert not any(i.rule_id == "confidence_out_of_range" for i in issues)
