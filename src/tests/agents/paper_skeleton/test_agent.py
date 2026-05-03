"""Integration tests for PaperSkeletonAgent (LLM mocked)."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from episteme_graph.agents.document_structure.schema import (
    DocumentMetadata,
    DocumentStructureResult,
    Section,
    TypedBlock,
)
from episteme_graph.agents.paper_skeleton.agent import PaperSkeletonAgent
from episteme_graph.agents.paper_skeleton.schema import PaperSkeletonResult

# ---------------------------------------------------------------------------
# Minimal mock LLM output — valid skeleton JSON
# ---------------------------------------------------------------------------
MOCK_LLM_RESPONSE = {
    "document_id": "doc_test",
    "skeleton_version": "v1",
    "cartridge_id": None,
    "paper_goal": {
        "text": "Derive quantum corrections to classical scattering.",
        "evidence_block_ids": ["blk_001_0001"],
        "reason": "Stated in abstract.",
        "confidence": 0.88,
    },
    "central_question": {
        "text": "How does the Born approximation modify the Rutherford cross-section?",
        "evidence_block_ids": ["blk_001_0003"],
        "reason": "Introduction formulates this question.",
        "confidence": 0.85,
    },
    "headline_claim": {
        "text": "The Born approximation reproduces the Rutherford cross-section at leading order.",
        "evidence_block_ids": ["blk_003_0020"],
        "reason": "Main result stated in derivation section.",
        "confidence": 0.82,
    },
    "supporting_subclaims": [
        {
            "subclaim_id": "subclaim_1",
            "text": "Higher-order corrections are suppressed by the Sommerfeld parameter.",
            "role": "supporting_claim",
            "evidence_block_ids": ["blk_004_0030"],
            "reason": "Correction section quantifies the suppression.",
            "confidence": 0.75,
        }
    ],
    "logical_blocks": [
        {
            "block_id": "logic_1",
            "block_type": "problem_setting",
            "label": "Classical vs. quantum scattering",
            "section_ids": ["doc_test:sec_1"],
            "evidence_block_ids": ["blk_001_0001"],
            "summary": "Motivates a quantum treatment.",
            "reason": "Introduction framing.",
            "confidence": 0.90,
        },
        {
            "block_id": "logic_2",
            "block_type": "derivation",
            "label": "Born amplitude computation",
            "section_ids": ["doc_test:sec_2"],
            "evidence_block_ids": ["blk_003_0020"],
            "summary": "Derives the first-order Born amplitude.",
            "reason": "Core derivation section.",
            "confidence": 0.92,
        },
        {
            "block_id": "logic_3",
            "block_type": "conclusion",
            "label": "Physical interpretation",
            "section_ids": ["doc_test:sec_3"],
            "evidence_block_ids": ["blk_005_0040"],
            "summary": "Summarises equivalence with classical result.",
            "reason": "Conclusion section.",
            "confidence": 0.88,
        },
    ],
    "excluded_regions": [
        {
            "region_type": "prior_work",
            "section_ids": ["doc_test:sec_1"],
            "block_ids": ["blk_001_0005"],
            "reason": "Historical references to Rutherford 1911.",
        }
    ],
    "review_notes": ["provisional — teacher review recommended"],
    "confidence": 0.86,
}

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_structure() -> DocumentStructureResult:
    blocks = [
        TypedBlock("blk_001_0001", 1, 0, "Abstract text.", "body_paragraph"),
        TypedBlock("blk_001_0003", 1, 1, "Introduction text.", "body_paragraph"),
        TypedBlock("blk_002_0010", 2, 2, "1 Introduction", "section_heading"),
        TypedBlock("blk_003_0020", 3, 3, "E = mc² (1)", "equation_block"),
        TypedBlock("blk_004_0030", 4, 4, "[1] Prior work.", "reference_entry"),
        TypedBlock("blk_005_0040", 5, 5, "In conclusion...", "body_paragraph"),
    ]
    for i, b in enumerate(blocks):
        b.section_id = f"doc_test:sec_{(i // 2) + 1}"

    sections = [
        Section("doc_test:sec_1", "Introduction", 1, 1, 1, 3),
        Section("doc_test:sec_2", "Methods", 1, 2, 3, 5),
        Section("doc_test:sec_3", "Conclusion", 1, 3, 5, 6),
    ]
    return DocumentStructureResult(
        document_id="doc_test",
        source_file="/tmp/paper.pdf",
        cartridge_id=None,
        metadata=DocumentMetadata(title="Test Paper", pages=6),
        blocks=blocks,
        sections=sections,
    )


@pytest.fixture
def agent():
    return PaperSkeletonAgent()


@pytest.fixture
def structure():
    return _make_structure()


def run_with_mock(agent: PaperSkeletonAgent, structure: DocumentStructureResult) -> PaperSkeletonResult:
    with patch.object(agent._llm_client, "generate", return_value=MOCK_LLM_RESPONSE):
        return agent.run(structure)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_run_returns_result(agent, structure):
    result = run_with_mock(agent, structure)
    assert isinstance(result, PaperSkeletonResult)


def test_paper_goal_extracted(agent, structure):
    result = run_with_mock(agent, structure)
    assert result.paper_goal.get("text", "") != ""


def test_headline_claim_extracted(agent, structure):
    result = run_with_mock(agent, structure)
    assert result.headline_claim.get("text", "") != ""


def test_logical_blocks_present(agent, structure):
    result = run_with_mock(agent, structure)
    assert len(result.logical_blocks) >= 2


def test_logical_blocks_have_valid_types(agent, structure):
    from episteme_graph.agents.paper_skeleton.schema import LOGICAL_BLOCK_TYPES
    result = run_with_mock(agent, structure)
    for lb in result.logical_blocks:
        assert lb.block_type in LOGICAL_BLOCK_TYPES


def test_prior_work_in_excluded_regions(agent, structure):
    result = run_with_mock(agent, structure)
    region_types = [r.get("region_type") for r in result.excluded_regions]
    assert "prior_work" in region_types


def test_headline_is_not_figure_narration(agent, structure):
    result = run_with_mock(agent, structure)
    text = result.headline_claim.get("text", "")
    assert not text.lower().startswith("figure")


def test_confidence_in_range(agent, structure):
    result = run_with_mock(agent, structure)
    assert 0.0 <= result.confidence <= 1.0


def test_validation_issues_list(agent, structure):
    result = run_with_mock(agent, structure)
    assert isinstance(result.validation_issues, list)


def test_cartridge_id_none_without_cartridge(agent, structure):
    result = run_with_mock(agent, structure)
    assert result.cartridge_id is None


def test_to_json_round_trip(agent, structure):
    import json
    result = run_with_mock(agent, structure)
    raw = result.to_json()
    d = json.loads(raw)
    assert d["document_id"] == result.document_id
    assert len(d["logical_blocks"]) == len(result.logical_blocks)


def test_run_without_cartridge_does_not_raise(agent, structure):
    result = run_with_mock(agent, structure)
    assert result is not None


def test_llm_failure_returns_fallback(agent, structure):
    with patch.object(agent._llm_client, "generate", side_effect=RuntimeError("LLM error")):
        result = agent.run(structure)
    assert isinstance(result, PaperSkeletonResult)
    assert result.confidence == 0.0
    assert any("failed" in note.lower() or "error" in note.lower() for note in result.review_notes)


def test_repair_called_on_empty_headline(agent, structure):
    """When LLM returns empty headline_claim, repair is triggered."""
    bad_response = {**MOCK_LLM_RESPONSE, "headline_claim": {"text": "", "evidence_block_ids": [], "reason": "", "confidence": 0.0}}
    good_response = MOCK_LLM_RESPONSE

    call_count = {"n": 0}

    def side_effect(_messages, *args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return bad_response
        return good_response

    with patch.object(agent._llm_client, "generate", side_effect=side_effect):
        result = agent.run(structure)

    # Either repaired successfully or returned fallback — both are valid
    assert isinstance(result, PaperSkeletonResult)
    assert call_count["n"] >= 2  # confirm repair was attempted
