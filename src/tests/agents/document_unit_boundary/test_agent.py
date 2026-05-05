"""DocumentUnitBoundaryAgent の統合テスト。"""
from __future__ import annotations

import pytest

from episteme_graph.agents.document_structure.schema import (
    DocumentMetadata,
    DocumentStructureResult,
    TypedBlock,
)
from episteme_graph.agents.document_unit_boundary.agent import DocumentUnitBoundaryAgent
from episteme_graph.agents.document_unit_boundary.schema import (
    DocumentUnitBoundaryResult,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_structure(blocks: list[TypedBlock], document_id: str = "doc_test") -> DocumentStructureResult:
    return DocumentStructureResult(
        document_id=document_id,
        source_file="/fake/path.pdf",
        cartridge_id=None,
        metadata=DocumentMetadata(pages=max((b.page for b in blocks), default=1)),
        sections=[],
        blocks=blocks,
    )


SINGLE_ARTICLE_BLOCKS = [
    TypedBlock(block_id="blk_001_0000", page=1, order=0, text="A New Method for Quantum Error Correction", block_type="section_heading", confidence=0.9),
    TypedBlock(block_id="blk_001_0001", page=1, order=1, text="Alice Smith, Bob Johnson", block_type="body_paragraph", confidence=0.8),
    TypedBlock(block_id="blk_001_0002", page=1, order=2, text="Abstract", block_type="section_heading", confidence=0.95),
    TypedBlock(block_id="blk_001_0003", page=1, order=3, text="We present a novel approach...", block_type="body_paragraph", confidence=0.8),
    TypedBlock(block_id="blk_002_0004", page=2, order=4, text="1 Introduction", block_type="section_heading", confidence=0.88),
    TypedBlock(block_id="blk_002_0005", page=2, order=5, text="Recent work has shown...", block_type="body_paragraph", confidence=0.8),
    TypedBlock(block_id="blk_003_0006", page=3, order=6, text="2 Method", block_type="section_heading", confidence=0.88),
    TypedBlock(block_id="blk_003_0007", page=3, order=7, text="Our method consists of...", block_type="body_paragraph", confidence=0.8),
    TypedBlock(block_id="blk_004_0008", page=4, order=8, text="References", block_type="section_heading", confidence=0.95),
    TypedBlock(block_id="blk_004_0009", page=4, order=9, text="[1] Smith et al. 2020.", block_type="reference_entry", confidence=0.92),
]

MULTI_ARTICLE_BLOCKS = [
    # Article 1
    TypedBlock(block_id="blk_001_0000", page=1, order=0, text="First Paper Title on Quantum Computing", block_type="section_heading", confidence=0.9),
    TypedBlock(block_id="blk_001_0001", page=1, order=1, text="Abstract", block_type="section_heading", confidence=0.95),
    TypedBlock(block_id="blk_001_0002", page=1, order=2, text="This paper presents...", block_type="body_paragraph", confidence=0.8),
    TypedBlock(block_id="blk_001_0003", page=1, order=3, text="1 Introduction", block_type="section_heading", confidence=0.88),
    TypedBlock(block_id="blk_002_0004", page=2, order=4, text="Background and motivation...", block_type="body_paragraph", confidence=0.8),
    # Article 2
    TypedBlock(block_id="blk_003_0005", page=3, order=5, text="Second Paper Title on Error Correction", block_type="section_heading", confidence=0.9),
    TypedBlock(block_id="blk_003_0006", page=3, order=6, text="Abstract", block_type="section_heading", confidence=0.95),
    TypedBlock(block_id="blk_003_0007", page=3, order=7, text="We study error correction in...", block_type="body_paragraph", confidence=0.8),
    TypedBlock(block_id="blk_003_0008", page=3, order=8, text="1 Introduction", block_type="section_heading", confidence=0.88),
    TypedBlock(block_id="blk_004_0009", page=4, order=9, text="Error correction is essential...", block_type="body_paragraph", confidence=0.8),
]

CHAPTER_BLOCKS = [
    TypedBlock(block_id="blk_001_0000", page=1, order=0, text="Chapter 1 Introduction", block_type="section_heading", confidence=0.9),
    TypedBlock(block_id="blk_001_0001", page=1, order=1, text="This chapter introduces the topic.", block_type="body_paragraph", confidence=0.8),
    TypedBlock(block_id="blk_002_0002", page=2, order=2, text="Chapter 2 Background", block_type="section_heading", confidence=0.9),
    TypedBlock(block_id="blk_002_0003", page=2, order=3, text="Background material is described here.", block_type="body_paragraph", confidence=0.8),
    TypedBlock(block_id="blk_003_0004", page=3, order=4, text="Chapter 3 Methods", block_type="section_heading", confidence=0.9),
    TypedBlock(block_id="blk_003_0005", page=3, order=5, text="The methods used are as follows.", block_type="body_paragraph", confidence=0.8),
]


@pytest.fixture
def agent() -> DocumentUnitBoundaryAgent:
    return DocumentUnitBoundaryAgent()


# ---------------------------------------------------------------------------
# Single article tests
# ---------------------------------------------------------------------------

def test_single_article_returns_result(agent):
    structure = _make_structure(SINGLE_ARTICLE_BLOCKS)
    result = agent.run(structure)
    assert isinstance(result, DocumentUnitBoundaryResult)


def test_single_article_has_one_unit(agent):
    structure = _make_structure(SINGLE_ARTICLE_BLOCKS)
    result = agent.run(structure)
    assert len(result.units) == 1


def test_single_article_unit_type_is_article(agent):
    structure = _make_structure(SINGLE_ARTICLE_BLOCKS)
    result = agent.run(structure)
    assert result.units[0].unit_type == "article"


def test_single_article_target_unit_set(agent):
    structure = _make_structure(SINGLE_ARTICLE_BLOCKS)
    result = agent.run(structure)
    assert result.target_unit_id is not None
    assert result.target_unit_id == result.units[0].unit_id


def test_single_article_has_included_blocks(agent):
    structure = _make_structure(SINGLE_ARTICLE_BLOCKS)
    result = agent.run(structure)
    target = result.get_target_unit()
    assert target is not None
    assert len(target.included_block_ids) > 0


def test_single_article_references_excluded(agent):
    structure = _make_structure(SINGLE_ARTICLE_BLOCKS)
    result = agent.run(structure)
    excluded_ids = {e.block_id for e in result.excluded_blocks}
    # references blocks should be excluded
    ref_block = next(b for b in SINGLE_ARTICLE_BLOCKS if b.block_type == "reference_entry")
    assert ref_block.block_id in excluded_ids


def test_single_article_no_hard_errors(agent):
    structure = _make_structure(SINGLE_ARTICLE_BLOCKS)
    result = agent.run(structure)
    assert result.is_completed()


def test_single_article_get_target_unit_block_ids(agent):
    structure = _make_structure(SINGLE_ARTICLE_BLOCKS)
    result = agent.run(structure)
    block_ids = result.get_target_unit_block_ids()
    assert len(block_ids) > 0


# ---------------------------------------------------------------------------
# Multi-article tests
# ---------------------------------------------------------------------------

def test_multi_article_detects_multiple_units(agent):
    structure = _make_structure(MULTI_ARTICLE_BLOCKS)
    result = agent.run(structure)
    assert len(result.units) >= 2


def test_multi_article_needs_review(agent):
    structure = _make_structure(MULTI_ARTICLE_BLOCKS)
    result = agent.run(structure)
    assert result.needs_review is True


def test_multi_article_has_review_reason(agent):
    structure = _make_structure(MULTI_ARTICLE_BLOCKS)
    result = agent.run(structure)
    assert "multiple_candidate_units_detected" in result.review_reasons


def test_multi_article_target_auto_selected(agent):
    structure = _make_structure(MULTI_ARTICLE_BLOCKS)
    result = agent.run(structure)
    assert result.target_unit_id is not None


def test_multi_article_explicit_target(agent):
    structure = _make_structure(MULTI_ARTICLE_BLOCKS)
    result = agent.run(structure)
    # Get available unit IDs
    unit_ids = [u.unit_id for u in result.units]
    # Select the last unit explicitly
    second_target = unit_ids[-1]
    result2 = agent.run(structure, target_unit_id=second_target)
    assert result2.target_unit_id == second_target


# ---------------------------------------------------------------------------
# Chapter detection tests
# ---------------------------------------------------------------------------

def test_chapter_detection(agent):
    structure = _make_structure(CHAPTER_BLOCKS)
    result = agent.run(structure)
    unit_types = {u.unit_type for u in result.units}
    assert "chapter" in unit_types


def test_chapter_count(agent):
    structure = _make_structure(CHAPTER_BLOCKS)
    result = agent.run(structure)
    chapter_units = [u for u in result.units if u.unit_type == "chapter"]
    assert len(chapter_units) == 3


# ---------------------------------------------------------------------------
# Empty / edge case tests
# ---------------------------------------------------------------------------

def test_empty_blocks(agent):
    structure = _make_structure([])
    result = agent.run(structure)
    assert result.target_unit_id is None
    assert not result.is_completed()


def test_block_annotation(agent):
    structure = _make_structure(SINGLE_ARTICLE_BLOCKS)
    agent_with_annotation = DocumentUnitBoundaryAgent(annotate_blocks=True)
    result = agent_with_annotation.run(structure)
    # Verify annotation was added to blocks
    for block in structure.blocks:
        assert "is_target_unit_block" in block.raw
        assert "unit_id" in block.raw
        assert "boundary_role" in block.raw


def test_block_annotation_references_excluded(agent):
    structure = _make_structure(SINGLE_ARTICLE_BLOCKS)
    agent_with_annotation = DocumentUnitBoundaryAgent(annotate_blocks=True)
    agent_with_annotation.run(structure)
    # Reference blocks should have boundary_role = "excluded" or "references"
    ref_blocks = [b for b in structure.blocks if b.block_type == "reference_entry"]
    for b in ref_blocks:
        # Either excluded from target or annotated as references role
        assert b.raw.get("boundary_role") in ("excluded", "references") or not b.raw.get("is_target_unit_block")


# ---------------------------------------------------------------------------
# Serialization tests
# ---------------------------------------------------------------------------

def test_result_serialization(agent):
    structure = _make_structure(SINGLE_ARTICLE_BLOCKS)
    result = agent.run(structure)
    d = result.to_dict()
    assert "document_id" in d
    assert "units" in d
    assert "excluded_blocks" in d
    assert "target_unit_id" in d


def test_result_roundtrip(agent):
    structure = _make_structure(SINGLE_ARTICLE_BLOCKS)
    result = agent.run(structure)
    json_str = result.to_json()
    import json
    d = json.loads(json_str)
    restored = DocumentUnitBoundaryResult.from_dict(d)
    assert restored.document_id == result.document_id
    assert restored.target_unit_id == result.target_unit_id
    assert len(restored.units) == len(result.units)
