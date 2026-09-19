"""式プレビューのチャンク割り当てと latex の出所（2026-09-19）。

固定する契約:

①``chunks.formulas`` への割り当ては **block_id 一致が第一条件**。ページ一致は
  block_id が引けないときだけの保険で、しかもページ番号が信用できない文書
  （全式が同じページ番号 = GROBID の誤付与）では使わない。以前はページ一致だけで
  判定していたため、``page=1`` が大量に付く文書で1つのチャンクに文書中のほぼ全式が
  流れ込んでいた（実測: 当該チャンクの ``block_ids`` 外が 87〜94%）。
②プレビューは latex の出所（``reconstructed`` / ``latex_source``）を持つ。PDF 由来の
  式はほぼ全件が「AI が文脈から復元した式」なので、抽出できた式と同じ顔で出さない。
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.document_pipeline import persistence  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _preview(eq_id: str, block_id: str, page: int) -> dict:
    return {
        "id": eq_id,
        "latex": "x = y",
        "spoken": "x equals y",
        "is_display": True,
        "label": None,
        "block_id": block_id,
        "source_location": {"page": page, "block_id": block_id},
        "raw_text": "x = y",
    }


def _equations(records: list) -> types.SimpleNamespace:
    return types.SimpleNamespace(equations=records)


def _record(*, reconstruction_status: str, latex: str | None, src_latex: str | None = None):
    return types.SimpleNamespace(
        equation_id="eq_1",
        label="(1)",
        source_extraction=types.SimpleNamespace(
            raw_text="delta g = b delta m",
            latex=src_latex,
            plain_text=None,
            source_location={"page": 2, "block_id": "blk_1"},
            source_image=None,
            needs_math_review=True,
            review_reason=["pdf_text_layer_untrusted"],
        ),
        reconstruction=types.SimpleNamespace(
            status=reconstruction_status,
            latex=latex,
            plain_text="delta g equals b delta m" if latex else None,
        ),
    )


# ---------------------------------------------------------------------------
# ① block_id 優先の割り当て
# ---------------------------------------------------------------------------


def test_block_id_decides_which_chunk_gets_the_formula():
    previews = [_preview("eq_1", "blk_1", 1), _preview("eq_2", "blk_9", 1)]
    merged = persistence._merge_equation_previews_for_chunk(
        [], previews, 1, 1, chunk_block_ids=["blk_1", "blk_2"]
    )
    assert [f["id"] for f in merged] == ["eq_1"]


def test_page_match_does_not_pull_in_formulas_from_other_blocks():
    """ページが一致していても block_id が違えば入らない（F-16 の再発防止）。"""
    previews = [_preview(f"eq_{i}", f"blk_{i}", 1) for i in range(1, 20)]
    merged = persistence._merge_equation_previews_for_chunk(
        [], previews, 1, 1, chunk_block_ids=["blk_3"]
    )
    assert [f["id"] for f in merged] == ["eq_3"]


def test_page_fallback_applies_only_when_the_chunk_has_no_blocks():
    previews = [_preview("eq_1", "blk_1", 2)]
    merged = persistence._merge_equation_previews_for_chunk(
        [], previews, 2, 2, chunk_block_ids=[]
    )
    assert [f["id"] for f in merged] == ["eq_1"]


def test_page_fallback_is_refused_when_pages_are_not_trustworthy():
    previews = [_preview("eq_1", "blk_1", 1), _preview("eq_2", "blk_2", 1)]
    assert persistence._page_fallback_allowed(previews) is False
    merged = persistence._merge_equation_previews_for_chunk(
        [], previews, 1, 1, chunk_block_ids=[], allow_page_fallback=False
    )
    assert merged == []


def test_page_fallback_is_allowed_when_pages_actually_differ():
    previews = [_preview("eq_1", "blk_1", 1), _preview("eq_2", "blk_2", 7)]
    assert persistence._page_fallback_allowed(previews) is True


def test_existing_formula_entries_are_updated_not_duplicated():
    previews = [_preview("eq_1", "blk_1", 2)]
    merged = persistence._merge_equation_previews_for_chunk(
        [{"id": "eq_1", "block_id": "blk_1", "latex": "old"}],
        previews,
        2,
        2,
        chunk_block_ids=["blk_1"],
    )
    assert len(merged) == 1
    assert merged[0]["latex"] == "x = y"


# ---------------------------------------------------------------------------
# ② latex の出所
# ---------------------------------------------------------------------------


def test_reconstructed_latex_is_marked_as_such():
    previews = persistence._equation_previews(
        _equations([_record(reconstruction_status="inferred_from_context", latex="\\delta_g = b \\delta_m")])
    )
    assert previews[0]["reconstructed"] is True
    assert previews[0]["latex_source"] == persistence.EQUATION_LATEX_SOURCE_RECONSTRUCTION
    assert previews[0]["latex"] == "\\delta_g = b \\delta_m"


def test_extracted_latex_is_marked_as_extraction():
    previews = persistence._equation_previews(
        _equations([_record(reconstruction_status="none", latex=None, src_latex="\\delta_g = b \\delta_m")])
    )
    assert previews[0]["reconstructed"] is False
    assert previews[0]["latex_source"] == persistence.EQUATION_LATEX_SOURCE_EXTRACTION


def test_raw_text_is_still_kept_for_audit():
    previews = persistence._equation_previews(
        _equations([_record(reconstruction_status="inferred_from_context", latex="\\delta_g = b \\delta_m")])
    )
    assert previews[0]["raw_text"] == "delta g = b delta m"
    assert previews[0]["needs_math_review"] is True
