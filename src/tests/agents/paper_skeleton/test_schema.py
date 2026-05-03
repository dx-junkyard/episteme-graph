"""Tests for PaperSkeletonResult schema serialization."""
import json

import pytest

from episteme_graph.agents.paper_skeleton.schema import (
    LogicalBlock,
    PaperSkeletonResult,
    ValidationIssue,
    SKELETON_VERSION,
)


def _make_logical_block(block_id: str = "logic_1") -> LogicalBlock:
    return LogicalBlock(
        block_id=block_id,
        block_type="derivation",
        label="Core derivation",
        section_ids=["doc:sec_1"],
        evidence_block_ids=["blk_001_0000"],
        summary="Derives the main result.",
        reason="Central computation section.",
        confidence=0.88,
    )


def _make_result() -> PaperSkeletonResult:
    entry = {
        "text": "Derive the scattering cross-section.",
        "evidence_block_ids": ["blk_001_0000"],
        "reason": "Stated in abstract.",
        "confidence": 0.9,
    }
    return PaperSkeletonResult(
        document_id="doc_test",
        skeleton_version=SKELETON_VERSION,
        cartridge_id="particle_physics",
        paper_goal=entry,
        central_question=entry,
        headline_claim={**entry, "text": "The Born approximation reproduces Rutherford."},
        supporting_subclaims=[],
        logical_blocks=[_make_logical_block("logic_1"), _make_logical_block("logic_2")],
        excluded_regions=[{"region_type": "prior_work", "section_ids": [], "block_ids": [], "reason": "Literature review"}],
        review_notes=["provisional"],
        confidence=0.85,
    )


def test_to_dict_has_top_level_keys():
    result = _make_result()
    d = result.to_dict()
    for key in ("document_id", "skeleton_version", "cartridge_id", "paper_goal",
                "central_question", "headline_claim", "logical_blocks",
                "excluded_regions", "confidence"):
        assert key in d


def test_to_json_is_valid_json():
    result = _make_result()
    raw = result.to_json()
    parsed = json.loads(raw)
    assert parsed["document_id"] == "doc_test"


def test_round_trip():
    original = _make_result()
    d = original.to_dict()
    restored = PaperSkeletonResult.from_dict(d)

    assert restored.document_id == original.document_id
    assert restored.cartridge_id == original.cartridge_id
    assert len(restored.logical_blocks) == 2
    assert restored.logical_blocks[0].block_type == "derivation"
    assert restored.confidence == pytest.approx(0.85)


def test_from_dict_missing_fields_use_defaults():
    d = {
        "document_id": "doc_min",
        "cartridge_id": None,
        # minimal — other fields missing
    }
    result = PaperSkeletonResult.from_dict(d)
    assert result.document_id == "doc_min"
    assert result.cartridge_id is None
    assert result.logical_blocks == []
    assert result.supporting_subclaims == []
    assert result.confidence == 0.0


def test_make_fallback():
    fallback = PaperSkeletonResult.make_fallback("doc_x", None, "LLM error")
    assert fallback.document_id == "doc_x"
    assert fallback.confidence == 0.0
    assert any("failed" in note.lower() for note in fallback.review_notes)
    assert fallback.logical_blocks == []


def test_logical_block_dataclass():
    lb = _make_logical_block()
    assert lb.block_type == "derivation"
    assert lb.confidence == pytest.approx(0.88)
