"""figure_image_extraction (`core/document_pipeline/figure_images.py`) の純関数部分の単体テスト。

設計: docs/features/image_pipeline_knowledge_library_design.md §4。DB / MinIO への
実接続は行わない（``extract_document_figures`` / ``load_document_figures`` はテスト対象外）。

- ``_normalize_figure_key``: caption text からの figure_key 正規化
- ``_find_best_image_match``: caption bbox と embedded image bbox の近接対応付け
- ``_prev_block_bottom``: reading order 上の直前ブロック下端の探索
- ``_estimate_region_bbox`` / ``_render_region``: 領域レンダリングの bbox 推定
  （PyMuPDF の Page オブジェクトが要るため ``pytest.importorskip("fitz")`` でガードする）
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[2]
_SRC = _BACKEND.parent / "src"
for _p in (str(_BACKEND), str(_SRC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.document_pipeline import figure_images as fi  # noqa: E402


class TestNormalizeFigureKey:
    def test_figure_with_number_label(self):
        key, label = fi._normalize_figure_key("Figure 3: A schematic of the apparatus.", page=2, index=0)
        assert key == "fig_3"
        assert label == "Figure 3"

    def test_fig_abbreviation_with_letter_suffix(self):
        key, label = fi._normalize_figure_key("Fig. 2a - detail view", page=1, index=0)
        assert key == "fig_2a"
        assert label == "Figure 2a"

    def test_no_caption_falls_back_to_page_index(self):
        key, label = fi._normalize_figure_key("", page=5, index=2)
        assert key == "p5_i2"
        assert label is None

    def test_unrecognized_caption_format_falls_back(self):
        key, label = fi._normalize_figure_key("A diagram without a figure label", page=1, index=0)
        assert key == "p1_i0"
        assert label is None

    def test_missing_page_defaults_to_zero_in_fallback(self):
        key, _ = fi._normalize_figure_key("", page=None, index=1)
        assert key == "p0_i1"

    def test_dotted_numeric_label_is_sanitized(self):
        key, label = fi._normalize_figure_key("Figure 3.2: sub-part", page=1, index=0)
        assert key == "fig_3_2"
        assert label == "Figure 3.2"


class TestFindBestImageMatch:
    def test_prefers_image_directly_above_caption(self):
        # caption top edge (y0) = 200; candidate 0 sits far above, candidate 1 sits
        # immediately above (small gap) and should win.
        candidates = [
            {"bbox": [0, 0, 100, 50]},
            {"bbox": [0, 150, 100, 195]},
        ]
        idx = fi._find_best_image_match(candidates, used_indices=set(), cap_bbox=[0, 200, 100, 220])
        assert idx == 1

    def test_returns_none_when_no_bbox_available(self):
        candidates = [{"bbox": None}]
        assert fi._find_best_image_match(candidates, set(), [0, 200, 100, 220]) is None

    def test_returns_none_when_caption_bbox_missing(self):
        candidates = [{"bbox": [0, 150, 100, 195]}]
        assert fi._find_best_image_match(candidates, set(), None) is None

    def test_skips_already_used_indices(self):
        candidates = [{"bbox": [0, 150, 100, 195]}]
        idx = fi._find_best_image_match(candidates, used_indices={0}, cap_bbox=[0, 200, 100, 220])
        assert idx is None

    def test_returns_none_when_beyond_max_match_distance(self):
        candidates = [{"bbox": [0, 0, 100, 10]}]  # gap to caption (y0=1000) far exceeds threshold
        idx = fi._find_best_image_match(candidates, set(), [0, 1000, 100, 1020])
        assert idx is None


class TestPrevBlockBottom:
    def test_returns_bottom_of_closest_prior_block(self):
        blocks = [
            {"order": 1, "bbox": [0, 0, 100, 50]},
            {"order": 2, "bbox": [0, 60, 100, 120]},
            {"order": 5, "bbox": [0, 500, 100, 600]},  # after caption, ignored
        ]
        assert fi._prev_block_bottom(blocks, cap_order=3) == 120

    def test_returns_none_when_no_prior_blocks(self):
        blocks = [{"order": 5, "bbox": [0, 500, 100, 600]}]
        assert fi._prev_block_bottom(blocks, cap_order=1) is None

    def test_ignores_blocks_without_bbox(self):
        blocks = [{"order": 1, "bbox": None}, {"order": 2, "bbox": [0, 10, 100, 40]}]
        assert fi._prev_block_bottom(blocks, cap_order=3) == 40


class TestEstimateRegionBbox:
    """領域レンダリングの bbox 推定（PyMuPDF Page が要るため fitz を importorskip する）。"""

    @pytest.fixture(autouse=True)
    def _require_fitz(self):
        pytest.importorskip("fitz")

    def _page(self):
        import fitz

        doc = fitz.open()
        page = doc.new_page(width=612, height=792)
        return doc, page

    def test_bbox_none_when_caption_has_no_bbox(self):
        _, page = self._page()
        bbox, confidence = fi._estimate_region_bbox(page, {"bbox": None}, [])
        assert bbox is None
        assert confidence == 0.0

    def test_uses_previous_block_bottom_when_available(self):
        _, page = self._page()
        cap = {"bbox": [50, 300, 500, 320], "order": 3}
        blocks_same_page = [{"order": 1, "bbox": [50, 100, 500, 150]}]
        bbox, confidence = fi._estimate_region_bbox(page, cap, blocks_same_page)
        assert bbox is not None
        # y0 は前ブロック下端(150) + 2.0 のマージン
        assert bbox[1] == pytest.approx(152.0)
        assert confidence == 0.75

    def test_falls_back_to_page_top_margin_without_prior_block(self):
        _, page = self._page()
        cap = {"bbox": [50, 300, 500, 320], "order": 1}
        bbox, confidence = fi._estimate_region_bbox(page, cap, [])
        assert bbox is not None
        assert bbox[1] == pytest.approx(page.rect.y0 + fi._PAGE_TOP_MARGIN)
        assert confidence == 0.4

    def test_confidence_penalized_for_oversized_region(self):
        _, page = self._page()
        # caption near the bottom of a tall page with no prior block → falls back to
        # page-top margin, producing a region taller than 60% of the page height.
        cap = {"bbox": [50, 700, 500, 720], "order": 1}
        bbox, confidence = fi._estimate_region_bbox(page, cap, [])
        assert bbox is not None
        region_height_ratio = (bbox[3] - bbox[1]) / page.rect.height
        assert region_height_ratio > 0.6
        assert confidence < 0.4

    def test_returns_none_when_region_collapses_to_empty(self):
        _, page = self._page()
        # caption sits at/above the page top margin with no prior block → y0 >= y1.
        cap = {"bbox": [50, 5, 500, 10], "order": 1}
        bbox, confidence = fi._estimate_region_bbox(page, cap, [])
        assert bbox is None
        assert confidence == 0.0
