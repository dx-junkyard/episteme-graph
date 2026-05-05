"""UnitDetector のユニットテスト。"""
from __future__ import annotations

import pytest

from episteme_graph.agents.document_structure.schema import TypedBlock
from episteme_graph.agents.document_unit_boundary.detector import UnitDetector


def _block(
    page: int,
    order: int,
    text: str,
    block_type: str = "body_paragraph",
    block_id: str | None = None,
) -> TypedBlock:
    bid = block_id or f"blk_{page:03d}_{order:04d}"
    return TypedBlock(block_id=bid, page=page, order=order, text=text, block_type=block_type)


@pytest.fixture
def detector() -> UnitDetector:
    return UnitDetector()


class TestFrontMatterDetection:
    def test_abstract_marks_end_of_front_matter(self, detector):
        blocks = [
            _block(1, 0, "Contents", "section_heading"),
            _block(1, 1, "Abstract", "section_heading"),
            _block(1, 2, "We present a new method.", "body_paragraph"),
        ]
        idx = detector._find_front_matter_end(blocks)
        assert idx == 1  # Abstract block index

    def test_introduction_marks_end_of_front_matter(self, detector):
        blocks = [
            _block(1, 0, "Table of Contents", "body_paragraph"),
            _block(1, 1, "1 Introduction", "section_heading"),
            _block(2, 2, "Recent work...", "body_paragraph"),
        ]
        idx = detector._find_front_matter_end(blocks)
        assert idx == 1

    def test_no_front_matter_returns_zero(self, detector):
        blocks = [
            _block(1, 0, "A Novel Method", "section_heading"),
            _block(1, 1, "Abstract", "section_heading"),
        ]
        idx = detector._find_front_matter_end(blocks)
        assert idx == 1  # Abstract found


class TestBackMatterDetection:
    def test_references_starts_back_matter(self, detector):
        blocks = [
            _block(1, 0, "Body paragraph.", "body_paragraph"),
            _block(2, 1, "References", "section_heading"),
            _block(2, 2, "[1] Smith et al.", "reference_entry"),
        ]
        idx = detector._find_back_matter_start(blocks)
        assert idx == 1

    def test_acknowledgements_starts_back_matter(self, detector):
        blocks = [
            _block(1, 0, "Conclusion text.", "body_paragraph"),
            _block(2, 1, "Acknowledgements", "section_heading"),
            _block(2, 2, "We thank our funding...", "body_paragraph"),
        ]
        idx = detector._find_back_matter_start(blocks)
        assert idx == 1

    def test_no_back_matter_returns_len(self, detector):
        blocks = [
            _block(1, 0, "Introduction text.", "body_paragraph"),
            _block(2, 1, "Method description.", "body_paragraph"),
        ]
        idx = detector._find_back_matter_start(blocks)
        assert idx == len(blocks)


class TestChapterDetection:
    def test_chapter_markers_detected(self, detector):
        blocks = [
            _block(1, 0, "Chapter 1 Introduction", "section_heading"),
            _block(1, 1, "Introduction text.", "body_paragraph"),
            _block(2, 2, "Chapter 2 Background", "section_heading"),
            _block(2, 3, "Background text.", "body_paragraph"),
        ]
        starts = detector._find_chapter_starts(blocks)
        assert len(starts) == 2
        assert starts[0] == 0
        assert starts[1] == 2

    def test_japanese_chapter_markers_detected(self, detector):
        blocks = [
            _block(1, 0, "第1章 はじめに", "section_heading"),
            _block(1, 1, "はじめにのテキスト。", "body_paragraph"),
            _block(2, 2, "第2章 関連研究", "section_heading"),
        ]
        starts = detector._find_chapter_starts(blocks)
        assert len(starts) == 2


class TestSingleUnitDetection:
    def test_single_article_with_abstract(self, detector):
        blocks = [
            _block(1, 0, "Paper Title Here", "section_heading"),
            _block(1, 1, "Abstract", "section_heading"),
            _block(1, 2, "We present a method.", "body_paragraph"),
            _block(2, 3, "1 Introduction", "section_heading"),
            _block(2, 4, "Introduction text.", "body_paragraph"),
        ]
        units, excluded, confidence, reasons = detector.detect(blocks, "doc_test")
        assert len(units) == 1
        assert units[0].unit_type == "article"

    def test_single_unit_has_block_ids(self, detector):
        blocks = [
            _block(1, 0, "Abstract", "section_heading"),
            _block(1, 1, "We study...", "body_paragraph"),
            _block(2, 2, "1 Introduction", "section_heading"),
            _block(2, 3, "Introduction.", "body_paragraph"),
        ]
        units, _, _, _ = detector.detect(blocks, "doc_test")
        assert len(units) > 0
        unit = units[0]
        assert unit.start_block_id
        assert unit.end_block_id
        assert len(unit.included_block_ids) > 0

    def test_empty_blocks_returns_empty(self, detector):
        units, excluded, confidence, reasons = detector.detect([], "doc_test")
        assert units == []
        assert confidence == 0.0


class TestMultiUnitDetection:
    def test_multi_article_detected(self, detector):
        blocks = [
            # Article 1
            _block(1, 0, "First Paper Title on Machine Learning", "section_heading"),
            _block(1, 1, "Abstract", "section_heading"),
            _block(1, 2, "First paper abstract.", "body_paragraph"),
            _block(2, 3, "1 Introduction", "section_heading"),
            _block(2, 4, "First paper intro.", "body_paragraph"),
            # Article 2
            _block(3, 5, "Second Paper Title on Deep Learning", "section_heading"),
            _block(3, 6, "Abstract", "section_heading"),
            _block(3, 7, "Second paper abstract.", "body_paragraph"),
            _block(4, 8, "1 Introduction", "section_heading"),
            _block(4, 9, "Second paper intro.", "body_paragraph"),
        ]
        units, excluded, confidence, reasons = detector.detect(blocks, "doc_test")
        assert len(units) >= 2
        assert "multiple_candidate_units_detected" in reasons


class TestExcludedBlocks:
    def test_back_matter_blocks_excluded(self, detector):
        blocks = [
            _block(1, 0, "Abstract", "section_heading"),
            _block(1, 1, "Body content.", "body_paragraph"),
            _block(2, 2, "References", "section_heading"),
            _block(2, 3, "[1] Smith et al.", "reference_entry"),
        ]
        units, excluded, _, _ = detector.detect(blocks, "doc_test")
        excluded_ids = {e.block_id for e in excluded}
        # References blocks should be excluded
        assert "blk_002_0002" in excluded_ids or "blk_002_0003" in excluded_ids

    def test_excluded_blocks_have_reasons(self, detector):
        blocks = [
            _block(1, 0, "Abstract", "section_heading"),
            _block(1, 1, "Body content.", "body_paragraph"),
            _block(2, 2, "References", "section_heading"),
            _block(2, 3, "[1] Smith et al.", "reference_entry"),
        ]
        _, excluded, _, _ = detector.detect(blocks, "doc_test")
        for e in excluded:
            assert e.reason in ("front_matter", "back_matter", "outside_target_unit")
