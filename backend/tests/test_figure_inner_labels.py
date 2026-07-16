"""図中ラベル抽出（``core/document_pipeline/figure_images.py::_extract_inner_labels``）の単体テスト。

装置図理解機能の拡張（Phase 1 サブタスク A）。設計は
docs/features/image_pipeline_knowledge_library_design.md（装置図理解拡張）を参照。
DB / MinIO への実接続は行わない（純関数 ``_extract_inner_labels`` と、
``_save_figure`` / ``load_document_figures`` の配線・migration ファイルの構造ガードのみ）。

FakePage は PyMuPDF の ``Page.get_text("words", clip=...)`` を模した最小スタブで、
``(x0, y0, x1, y1, word, block_no, line_no, word_no)`` タプルの列を返す。
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path
from typing import Any

_BACKEND = Path(__file__).resolve().parents[1]
_SRC = _BACKEND.parent / "src"
for _p in (str(_BACKEND), str(_SRC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.document_pipeline import figure_images as fi  # noqa: E402

_DB_DIR = _BACKEND / "db"


class FakePage:
    """``page.get_text("words", clip=...)`` を模した最小スタブ。"""

    def __init__(self, words: list[tuple], raise_exc: Exception | None = None):
        self._words = words
        self._raise_exc = raise_exc
        self.last_clip: Any = None

    def get_text(self, kind: str, clip: Any = None) -> list[tuple]:
        self.last_clip = clip
        if self._raise_exc is not None:
            raise self._raise_exc
        assert kind == "words"
        return self._words


class TestExtractInnerLabels:
    def test_close_words_on_same_line_merge_with_union_bbox(self):
        words = [
            (10.0, 10.0, 30.0, 20.0, "CCD", 0, 0, 0),
            (36.0, 12.0, 60.0, 22.0, "camera", 0, 0, 1),  # gap = 36-30 = 6 <= threshold
        ]
        page = FakePage(words)
        labels = fi._extract_inner_labels(page, [0, 0, 200, 200])
        assert len(labels) == 1
        assert labels[0]["text"] == "CCD camera"
        assert labels[0]["bbox"] == [10.0, 10.0, 60.0, 22.0]

    def test_gap_exceeding_threshold_splits_into_two_labels(self):
        words = [
            (10.0, 10.0, 30.0, 20.0, "PBS", 0, 0, 0),
            (40.0, 10.0, 60.0, 20.0, "mirror", 0, 0, 1),  # gap = 40-30 = 10 > threshold
        ]
        page = FakePage(words)
        labels = fi._extract_inner_labels(page, [0, 0, 200, 200])
        assert len(labels) == 2
        texts = {lbl["text"] for lbl in labels}
        assert texts == {"PBS", "mirror"}

    def test_different_line_no_stays_separate_even_when_close(self):
        words = [
            (10.0, 10.0, 30.0, 20.0, "ECDL", 0, 0, 0),
            (32.0, 10.0, 50.0, 20.0, "EOM", 0, 1, 0),  # same block, different line
        ]
        page = FakePage(words)
        labels = fi._extract_inner_labels(page, [0, 0, 200, 200])
        assert len(labels) == 2
        texts = {lbl["text"] for lbl in labels}
        assert texts == {"ECDL", "EOM"}

    def test_word_intersecting_caption_bbox_is_excluded(self):
        words = [
            (10.0, 10.0, 30.0, 20.0, "ECDL", 0, 0, 0),  # inside figure, kept
            (10.0, 100.0, 60.0, 115.0, "Figure", 0, 1, 0),  # overlaps caption bbox
        ]
        page = FakePage(words)
        caption_bbox = [0.0, 95.0, 200.0, 120.0]
        labels = fi._extract_inner_labels(page, [0, 0, 200, 200], caption_bbox=caption_bbox)
        assert len(labels) == 1
        assert labels[0]["text"] == "ECDL"

    def test_output_sorted_by_y_then_x(self):
        words = [
            (50.0, 100.0, 70.0, 110.0, "PBS", 1, 0, 0),
            (10.0, 10.0, 30.0, 20.0, "ECDL", 0, 0, 0),
            (5.0, 100.0, 20.0, 110.0, "EOM", 2, 0, 0),
        ]
        page = FakePage(words)
        labels = fi._extract_inner_labels(page, [0, 0, 200, 200])
        texts_in_order = [lbl["text"] for lbl in labels]
        assert texts_in_order == ["ECDL", "EOM", "PBS"]

    def test_whitespace_only_word_is_skipped(self):
        words = [
            (10.0, 10.0, 30.0, 20.0, "ECDL", 0, 0, 0),
            (32.0, 10.0, 40.0, 20.0, "   ", 0, 0, 1),
        ]
        page = FakePage(words)
        labels = fi._extract_inner_labels(page, [0, 0, 200, 200])
        assert len(labels) == 1
        assert labels[0]["text"] == "ECDL"

    def test_parameter_style_tokens_are_kept_verbatim(self):
        # "f = 75 mm" 形式のパラメータ表記も落とさず1ラベルにまとめる（P4）。
        words = [
            (10.0, 10.0, 16.0, 20.0, "f", 0, 0, 0),
            (18.0, 10.0, 26.0, 20.0, "=", 0, 0, 1),
            (28.0, 10.0, 38.0, 20.0, "75", 0, 0, 2),
            (40.0, 10.0, 54.0, 20.0, "mm", 0, 0, 3),
        ]
        page = FakePage(words)
        labels = fi._extract_inner_labels(page, [0, 0, 200, 200])
        assert len(labels) == 1
        assert labels[0]["text"] == "f = 75 mm"

    def test_get_text_exception_returns_empty_list_non_fatal(self):
        page = FakePage([], raise_exc=RuntimeError("boom"))
        labels = fi._extract_inner_labels(page, [0, 0, 200, 200])
        assert labels == []

    def test_figure_bbox_none_returns_empty_list(self):
        page = FakePage([(10.0, 10.0, 30.0, 20.0, "ECDL", 0, 0, 0)])
        assert fi._extract_inner_labels(page, None) == []

    def test_figure_bbox_empty_list_returns_empty_list(self):
        page = FakePage([(10.0, 10.0, 30.0, 20.0, "ECDL", 0, 0, 0)])
        assert fi._extract_inner_labels(page, []) == []


class TestPersistenceWiring:
    """``_save_figure`` / ``load_document_figures`` の inner_labels 配線の構造ガード。"""

    def test_save_figure_upserts_inner_labels_on_conflict(self):
        src = inspect.getsource(fi._save_figure)
        assert "inner_labels" in src
        assert "EXCLUDED.inner_labels" in src

    def test_load_document_figures_selects_and_normalizes_inner_labels(self):
        src = inspect.getsource(fi.load_document_figures)
        assert "inner_labels" in src

    def test_extract_document_figures_wires_inner_labels_extraction(self):
        src = inspect.getsource(fi.extract_document_figures)
        assert "_extract_inner_labels" in src


class TestMigrationFile:
    def test_migration_051_exists_and_is_idempotent(self):
        path = _DB_DIR / "051_figure_inner_labels.sql"
        assert path.is_file(), f"expected migration file at {path}"
        content = path.read_text(encoding="utf-8")
        assert "ADD COLUMN IF NOT EXISTS inner_labels" in content
