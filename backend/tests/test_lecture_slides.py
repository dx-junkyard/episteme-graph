"""migration 040: レクチャースライド同期 Phase 1 — スライド基盤のテスト。

- core.lecture.split_slides / get_course_lecture_language
- GET .../sequence の slides / total_slides / language
- POST .../tts の slide_index キャッシュキー
- GET .../audio-status の ready_slides / total_slides / stale_language
- lecture_studio._batch_audio_worker のスライド単位生成
"""

from __future__ import annotations

import os
import sys
import uuid
from unittest.mock import MagicMock, patch

import pytest

# api/ ディレクトリを sys.path に追加して schemas 等をインポート可能にする
_API_DIR = os.path.join(os.path.dirname(__file__), "..", "api")
if _API_DIR not in sys.path:
    sys.path.insert(0, _API_DIR)


# ---------------------------------------------------------------------------
# 1. core.lecture.split_slides
# ---------------------------------------------------------------------------


class TestSplitSlidesNoMarker:
    def test_no_marker_yields_single_slide(self):
        from core.lecture import split_slides

        slides, mismatch = split_slides("Hello world", "Spoken hello", [])
        assert mismatch is False
        assert slides == [{
            "slide_index": 0,
            "display_text": "Hello world",
            "spoken_text": "Spoken hello",
            "formulas": [],
            "figures": [],
        }]

    def test_none_display_and_spoken_yields_single_empty_slide(self):
        from core.lecture import split_slides

        slides, mismatch = split_slides(None, None, None)
        assert mismatch is False
        assert len(slides) == 1
        assert slides[0]["slide_index"] == 0
        assert slides[0]["display_text"] == ""
        assert slides[0]["spoken_text"] is None
        assert slides[0]["formulas"] == []


class TestSplitSlidesMatchingCounts:
    def test_matching_marker_counts_pair_by_index(self):
        from core.lecture import split_slides

        display = "Slide A\n===\nSlide B"
        spoken = "Speak A\n===\nSpeak B"
        slides, mismatch = split_slides(display, spoken, [])

        assert mismatch is False
        assert len(slides) == 2
        assert slides[0]["slide_index"] == 0
        assert slides[0]["display_text"] == "Slide A"
        assert slides[0]["spoken_text"] == "Speak A"
        assert slides[1]["slide_index"] == 1
        assert slides[1]["display_text"] == "Slide B"
        assert slides[1]["spoken_text"] == "Speak B"

    def test_marker_with_surrounding_whitespace_is_recognized(self):
        from core.lecture import split_slides

        display = "A\n  ===  \nB"
        slides, _mismatch = split_slides(display, None, [])
        assert len(slides) == 2

    def test_four_or_more_equals_is_also_a_marker(self):
        from core.lecture import split_slides

        slides, _mismatch = split_slides("A\n====\nB", None, [])
        assert len(slides) == 2
        assert slides[0]["display_text"] == "A"
        assert slides[1]["display_text"] == "B"

    def test_two_equals_is_not_a_marker(self):
        from core.lecture import split_slides

        slides, _mismatch = split_slides("A\n==\nB", None, [])
        assert len(slides) == 1
        assert "==" in slides[0]["display_text"]


class TestSplitSlidesMismatch:
    def test_mismatched_marker_counts_degrade_to_single_slide(self):
        from core.lecture import split_slides

        display = "A\n===\nB\n===\nC"  # 3 segments
        spoken = "Speak A\n===\nSpeak B"  # 2 segments
        slides, mismatch = split_slides(display, spoken, [])

        assert mismatch is True
        assert len(slides) == 1
        assert slides[0]["slide_index"] == 0
        assert slides[0]["display_text"] == "A\n\nB\n\nC"
        assert slides[0]["spoken_text"] == "Speak A\n\nSpeak B"


class TestSplitSlidesSpokenTextMissing:
    def test_spoken_text_none_creates_slides_without_spoken(self):
        from core.lecture import split_slides

        display = "A\n===\nB"
        slides, mismatch = split_slides(display, None, [])

        assert mismatch is False
        assert len(slides) == 2
        assert slides[0]["spoken_text"] is None
        assert slides[1]["spoken_text"] is None

    def test_whitespace_only_spoken_text_treated_as_missing(self):
        from core.lecture import split_slides

        slides, mismatch = split_slides("A\n===\nB", "   \n  ", [])
        assert mismatch is False
        assert all(s["spoken_text"] is None for s in slides)


class TestSplitSlidesEmptySlideRemoval:
    def test_consecutive_markers_produce_no_empty_slides(self):
        from core.lecture import split_slides

        slides, _mismatch = split_slides("A\n===\n===\nB", None, [])
        assert len(slides) == 2
        assert slides[0]["display_text"] == "A"
        assert slides[1]["display_text"] == "B"

    def test_leading_and_trailing_markers_are_stripped(self):
        from core.lecture import split_slides

        slides, _mismatch = split_slides("===\nOnly content\n===", None, [])
        assert len(slides) == 1
        assert slides[0]["display_text"] == "Only content"

    def test_all_marker_text_yields_single_empty_slide(self):
        from core.lecture import split_slides

        slides, mismatch = split_slides("===\n===\n===", None, [])
        assert mismatch is False
        assert len(slides) == 1
        assert slides[0]["display_text"] == ""


class TestSplitSlidesFormulaAssignment:
    def test_formulas_assigned_to_referencing_slide(self):
        from core.lecture import split_slides

        display = "Slide A [[FORMULA_0]]\n===\nSlide B [[FORMULA_1]]"
        spoken = "Speak A\n===\nSpeak B"
        formulas = [
            {"id": "[[FORMULA_0]]", "latex": "x", "spoken": "x", "is_display": False},
            {"id": "[[FORMULA_1]]", "latex": "y", "spoken": "y", "is_display": False},
        ]
        slides, _mismatch = split_slides(display, spoken, formulas)

        assert slides[0]["formulas"] == [formulas[0]]
        assert slides[1]["formulas"] == [formulas[1]]

    def test_unreferenced_formulas_go_to_last_slide(self):
        from core.lecture import split_slides

        display = "Slide A\n===\nSlide B"
        spoken = "Speak A\n===\nSpeak B"
        formulas = [{"id": "[[FORMULA_9]]", "latex": "z", "spoken": "z", "is_display": False}]
        slides, _mismatch = split_slides(display, spoken, formulas)

        assert slides[0]["formulas"] == []
        assert slides[1]["formulas"] == formulas

    def test_formulas_union_preserves_all_input_formulas(self):
        """全スライドの formulas の和 == 入力 formulas（情報を落とさない）。"""
        from core.lecture import split_slides

        display = "Slide A [[FORMULA_0]]\n===\nSlide B"
        spoken = "Speak A\n===\nSpeak B"
        formulas = [
            {"id": "[[FORMULA_0]]", "latex": "x", "spoken": "x", "is_display": False},
            {"id": "[[FORMULA_5]]", "latex": "unused", "spoken": "u", "is_display": False},
        ]
        slides, _mismatch = split_slides(display, spoken, formulas)

        all_formulas = [f for s in slides for f in s["formulas"]]
        assert len(all_formulas) == len(formulas)
        assert {f["id"] for f in all_formulas} == {f["id"] for f in formulas}

    def test_formula_without_id_goes_to_last_slide(self):
        from core.lecture import split_slides

        display = "Slide A\n===\nSlide B"
        formulas = [{"latex": "z", "spoken": "z", "is_display": False}]
        slides, _mismatch = split_slides(display, None, formulas)
        assert slides[-1]["formulas"] == formulas


class TestSplitSlidesDeterminism:
    def test_split_slides_is_deterministic(self):
        from core.lecture import split_slides

        display = "A [[FORMULA_0]]\n===\nB"
        spoken = "sa\n===\nsb"
        formulas = [{"id": "[[FORMULA_0]]", "latex": "x", "spoken": "x", "is_display": False}]

        r1 = split_slides(display, spoken, formulas)
        r2 = split_slides(display, spoken, formulas)
        assert r1 == r2


class TestGetCourseLectureLanguage:
    def test_defaults_to_ja_when_missing(self):
        from core.lecture import get_course_lecture_language

        assert get_course_lecture_language({}) == "ja"
        assert get_course_lecture_language(None) == "ja"
        assert get_course_lecture_language({"lecture_studio_settings": {}}) == "ja"

    def test_reads_language_from_settings(self):
        from core.lecture import get_course_lecture_language

        course_data = {"lecture_studio_settings": {"lecture_language": "en"}}
        assert get_course_lecture_language(course_data) == "en"


# ---------------------------------------------------------------------------
# 1b. core.lecture.compute_material_audio_readiness (Tier2-11: 音声 readiness の一本化)
#
# core/status/projector.py と api/routes/lecture.py::get_topic_audio_status の両方が
# 呼ぶ唯一の正本判定。スライド単位 + 言語一致で readiness を決める。
# ---------------------------------------------------------------------------


class TestComputeMaterialAudioReadiness:
    def test_no_material_ids_returns_empty_without_querying(self):
        from core.lecture import compute_material_audio_readiness

        session = MagicMock()
        result = compute_material_audio_readiness(session, [], "ja")

        session.execute.assert_not_called()
        assert result == {
            "total_chunks": 0,
            "generated_chunks": 0,
            "ready_chunks": 0,
            "total_slides": 0,
            "ready_slides": 0,
            "stale_language": False,
        }

    def test_no_chunk_rows_returns_empty(self):
        from core.lecture import compute_material_audio_readiness

        chunk_result = MagicMock()
        chunk_result.fetchall.return_value = []
        session = MagicMock()
        session.execute.side_effect = [chunk_result]

        result = compute_material_audio_readiness(session, ["m1"], "ja")

        assert result["total_chunks"] == 0
        assert result["total_slides"] == 0

    def test_single_slide_chunks_ready_and_not_ready(self):
        from core.lecture import compute_material_audio_readiness

        # 10 チャンク（マーカーなし = 1スライド/チャンク）、うち4チャンクに音声キャッシュあり
        chunk_rows = [
            (f"c{i}", f"text {i}", f"text {i}", f"spoken {i}", [], "ja")
            for i in range(10)
        ]
        audio_rows = [(f"c{i}", 0) for i in range(4)]

        chunk_result = MagicMock()
        chunk_result.fetchall.return_value = chunk_rows
        audio_result = MagicMock()
        audio_result.fetchall.return_value = audio_rows

        session = MagicMock()
        session.execute.side_effect = [chunk_result, audio_result]

        result = compute_material_audio_readiness(session, ["m1"], "ja")

        assert result["total_chunks"] == 10
        assert result["generated_chunks"] == 10
        assert result["ready_chunks"] == 4
        assert result["total_slides"] == 10
        assert result["ready_slides"] == 4
        assert result["stale_language"] is False

    def test_multi_slide_chunk_counts_slides_not_chunks(self):
        """1チャンクが2スライドに分割される場合、readiness はスライド単位で数える
        （chunk 単位の粗い判定なら1行のキャッシュだけで「対応チャンク済み」と誤認しうる）。"""
        from core.lecture import compute_material_audio_readiness

        display = "Slide A\n===\nSlide B"
        spoken = "Speak A\n===\nSpeak B"
        chunk_rows = [("c0", display, display, spoken, [], "ja")]
        audio_rows = [("c0", 0)]  # スライド0のみ音声あり（スライド1は未生成）

        chunk_result = MagicMock()
        chunk_result.fetchall.return_value = chunk_rows
        audio_result = MagicMock()
        audio_result.fetchall.return_value = audio_rows

        session = MagicMock()
        session.execute.side_effect = [chunk_result, audio_result]

        result = compute_material_audio_readiness(session, ["m1"], "ja")

        assert result["total_slides"] == 2
        assert result["ready_slides"] == 1
        assert result["ready_chunks"] == 1
        assert result["total_chunks"] == 1

    def test_stale_language_not_counted_as_ready(self):
        from core.lecture import compute_material_audio_readiness

        chunk_rows = [("c0", "text", "text", "spoken", [], "en")]
        audio_rows = [("c0", 0)]

        chunk_result = MagicMock()
        chunk_result.fetchall.return_value = chunk_rows
        audio_result = MagicMock()
        audio_result.fetchall.return_value = audio_rows

        session = MagicMock()
        session.execute.side_effect = [chunk_result, audio_result]

        result = compute_material_audio_readiness(session, ["m1"], "ja")

        assert result["ready_slides"] == 0
        assert result["ready_chunks"] == 1  # 「何かキャッシュがある」ことは分かる
        assert result["stale_language"] is True


# ---------------------------------------------------------------------------
# 1c. core.lecture.count_slide_marker_segments (Tier2-11: プレビュー整合インジケータ用)
# ---------------------------------------------------------------------------


class TestCountSlideMarkerSegments:
    def test_no_marker_is_one_segment_each(self):
        from core.lecture import count_slide_marker_segments

        assert count_slide_marker_segments("Hello", "Speak") == (1, 1)

    def test_marker_splits_into_multiple_segments(self):
        from core.lecture import count_slide_marker_segments

        display_count, spoken_count = count_slide_marker_segments(
            "A\n===\nB\n===\nC", "X\n===\nY",
        )
        assert display_count == 3
        assert spoken_count == 2

    def test_empty_spoken_text_is_zero_segments(self):
        from core.lecture import count_slide_marker_segments

        assert count_slide_marker_segments("A", None) == (1, 0)
        assert count_slide_marker_segments("A", "") == (1, 0)

    def test_none_display_text_is_zero_segments(self):
        from core.lecture import count_slide_marker_segments

        assert count_slide_marker_segments(None, None) == (0, 0)


# ---------------------------------------------------------------------------
# 2. GET .../sequence — slides / total_slides / language
# ---------------------------------------------------------------------------


_CHUNK_ID = "550e8400-e29b-41d4-a716-446655440099"


def _sequence_row(display_text, spoken_text, formulas=None, spoken_language=None):
    """chunks テーブルの行 (get_lecture_sequence の SELECT 列順) をシミュレート。"""
    return (
        uuid.UUID(_CHUNK_ID),
        0,
        display_text,       # c.text
        display_text,       # c.display_text
        spoken_text,        # c.spoken_text
        formulas or [],     # c.formulas
        "第1章",
        "1.1",
        spoken_language,    # c.spoken_language (migration 040)
    )


_COURSE_DATA = {
    "title": "Test Course",
    "topics": [{"id": "topic-1", "title": "T1"}],
    "sources": [{"material_id": "mat-1"}],
}


class TestSequenceApiSlides:
    @patch("api.routes.lecture.get_user_mastered_concepts", return_value=set())
    @patch("api.routes.lecture._check_audio_cache", return_value=False)
    @patch("api.routes.lecture._pg_session")
    @patch("api.routes.lecture.get_course_data")
    def test_no_marker_chunk_returns_single_slide(
        self, mock_course, mock_pg, mock_audio, mock_mastery,
    ):
        from api.routes.lecture import get_lecture_sequence

        mock_course.return_value = _COURSE_DATA
        row = _sequence_row("Hello world", "Spoken hello")

        main_result = MagicMock()
        main_result.fetchall.return_value = [row]
        slide_audio_result = MagicMock()
        slide_audio_result.fetchall.return_value = []

        session = MagicMock()
        session.execute.side_effect = [main_result, slide_audio_result]
        mock_pg.return_value = session

        resp = get_lecture_sequence("course-1", "topic-1", {"id": "u1"})

        assert resp.total_slides == 1
        seg = resp.segments[0]
        # 既存フィールドの後方互換
        assert seg.text == "Hello world"
        assert seg.spoken_text == "Spoken hello"
        assert seg.segment_mode == "full"
        # 新フィールド
        assert len(seg.slides) == 1
        assert seg.slides[0].slide_index == 0
        assert seg.slides[0].display_text == "Hello world"
        assert seg.slides[0].spoken_text == "Spoken hello"
        assert seg.slides[0].has_audio is False
        assert seg.language == "ja"

    @patch("api.routes.lecture.get_user_mastered_concepts", return_value=set())
    @patch("api.routes.lecture._check_audio_cache", return_value=False)
    @patch("api.routes.lecture._pg_session")
    @patch("api.routes.lecture.get_course_data")
    def test_marker_chunk_splits_into_slides_with_audio_map(
        self, mock_course, mock_pg, mock_audio, mock_mastery,
    ):
        from api.routes.lecture import get_lecture_sequence

        mock_course.return_value = _COURSE_DATA
        display = "Slide A\n===\nSlide B"
        spoken = "Speak A\n===\nSpeak B"
        row = _sequence_row(display, spoken, spoken_language="en")

        main_result = MagicMock()
        main_result.fetchall.return_value = [row]
        slide_audio_result = MagicMock()
        # slide_index=1 にのみ音声キャッシュがある
        slide_audio_result.fetchall.return_value = [(_CHUNK_ID, 1, 4000)]

        session = MagicMock()
        session.execute.side_effect = [main_result, slide_audio_result]
        mock_pg.return_value = session

        resp = get_lecture_sequence("course-1", "topic-1", {"id": "u1"})

        seg = resp.segments[0]
        assert resp.total_slides == 2
        assert len(seg.slides) == 2
        assert seg.slides[0].has_audio is False
        assert seg.slides[0].duration_ms == 0
        assert seg.slides[1].has_audio is True
        assert seg.slides[1].duration_ms == 4000
        assert seg.language == "en"

    @patch("api.routes.lecture._pg_session")
    @patch("api.routes.lecture.get_course_data")
    def test_draft_topic_sequence_returns_slides(self, mock_course, mock_pg):
        from api.routes.lecture import get_lecture_sequence

        mock_course.return_value = {
            "topics": [{
                "id": "topic-1",
                "student_material": {"source_text": "Draft A\n===\nDraft B"},
                "spoken_script": "Speak A\n===\nSpeak B",
            }],
            "sources": [],
        }
        # トピック音声キャッシュは空（音声未生成）→ has_audio False + タイマー送り
        audio_result = MagicMock()
        audio_result.fetchall.return_value = []
        session = MagicMock()
        session.execute.return_value = audio_result
        mock_pg.return_value = session

        resp = get_lecture_sequence("course-1", "topic-1", {"id": "u1"})

        assert resp.total_slides == 2
        seg = resp.segments[0]
        assert seg.chunk_id == "topic:topic-1"
        assert len(seg.slides) == 2
        assert all(sl.has_audio is False for sl in seg.slides)
        assert seg.slides[0].display_text == "Draft A"
        assert seg.slides[1].display_text == "Draft B"


class TestBuildSlidesForSegmentSummaryMode:
    """segment_mode == 'summary' は分割せず1スライドにする。"""

    def test_summary_mode_is_not_split_even_with_markers(self):
        from api.routes.lecture import _build_slides_for_segment

        display = "A\n===\nB"  # マーカーを含むが summary モードでは分割しない
        spoken = "要約されたテキスト"
        slides = _build_slides_for_segment(display, spoken, [], "summary", "chunk-1", {})

        assert len(slides) == 1
        assert slides[0].slide_index == 0
        assert slides[0].display_text == display
        assert slides[0].spoken_text == spoken

    def test_full_mode_splits_normally(self):
        from api.routes.lecture import _build_slides_for_segment

        slides = _build_slides_for_segment(
            "A\n===\nB", "sa\n===\nsb", [], "full", "chunk-1", {},
        )
        assert len(slides) == 2

    def test_audio_map_populates_has_audio_and_duration(self):
        from api.routes.lecture import _build_slides_for_segment

        audio_map = {("chunk-1", 0): 1234}
        slides = _build_slides_for_segment("A", "sa", [], "full", "chunk-1", audio_map)
        assert slides[0].has_audio is True
        assert slides[0].duration_ms == 1234


# ---------------------------------------------------------------------------
# 3. POST .../tts — slide_index キャッシュキー
# ---------------------------------------------------------------------------


_UUID = "550e8400-e29b-41d4-a716-446655440000"


class TestTtsSlideIndex:
    @patch("api.routes.lecture._get_audio_cache")
    def test_tts_passes_slide_index_to_cache_lookup(self, mock_cache):
        from api.routes.lecture import generate_tts
        from schemas import LectureTTSRequest

        mock_cache.return_value = {
            "audio_base64": "Zm9v", "duration_ms": 500, "word_timestamps": [],
        }
        resp = generate_tts(
            "c1", "t1", LectureTTSRequest(chunk_id=_UUID, slide_index=2), {"id": "u1"},
        )
        mock_cache.assert_called_once_with(_UUID, "alloy", 2)
        assert resp.duration_ms == 500

    @patch("api.routes.lecture._get_audio_cache", return_value=None)
    def test_tts_uncached_slide_returns_404(self, mock_cache):
        from fastapi import HTTPException
        from api.routes.lecture import generate_tts
        from schemas import LectureTTSRequest

        with pytest.raises(HTTPException) as exc:
            generate_tts(
                "c1", "t1", LectureTTSRequest(chunk_id=_UUID, slide_index=1), {"id": "u1"},
            )
        assert exc.value.status_code == 404

    def test_tts_non_uuid_chunk_returns_404_regardless_of_slide_index(self):
        from fastapi import HTTPException
        from api.routes.lecture import generate_tts
        from schemas import LectureTTSRequest

        with pytest.raises(HTTPException) as exc:
            generate_tts(
                "c1", "t1", LectureTTSRequest(chunk_id="topic:t1", slide_index=3), {"id": "u1"},
            )
        assert exc.value.status_code == 404

    def test_slide_index_default_is_zero(self):
        from schemas import LectureTTSRequest

        req = LectureTTSRequest(chunk_id=_UUID)
        assert req.slide_index == 0


# ---------------------------------------------------------------------------
# 4. GET .../audio-status — ready_slides / total_slides / stale_language
# ---------------------------------------------------------------------------


class TestAudioStatusSlideCalculation:
    @patch("api.routes.lecture._pg_session")
    @patch("api.routes.lecture.get_course_data")
    def test_marker_split_chunk_counts_multiple_slides(self, mock_course, mock_session):
        from api.routes.lecture import get_topic_audio_status

        mock_course.return_value = {
            "topics": [{"id": "t1"}],
            "sources": [{"material_id": "m1"}],
        }
        display = "Slide A\n===\nSlide B"
        spoken = "Speak A\n===\nSpeak B"
        chunk_rows = [("c0", display, display, spoken, [], "ja")]
        audio_rows = [("c0", 0), ("c0", 1)]  # 両スライドとも音声あり

        chunk_result = MagicMock()
        chunk_result.fetchall.return_value = chunk_rows
        audio_result = MagicMock()
        audio_result.fetchall.return_value = audio_rows

        session = MagicMock()
        session.execute.side_effect = [chunk_result, audio_result]
        mock_session.return_value = session

        result = get_topic_audio_status("c1", "t1", {"id": "u1"})

        assert result["total_slides"] == 2
        assert result["ready_slides"] == 2
        assert result["ready_chunks"] == 1
        assert result["total_chunks"] == 1
        assert result["has_audio"] is True
        assert result["stale_language"] is False
        assert result["language"] == "ja"

    @patch("api.routes.lecture._pg_session")
    @patch("api.routes.lecture.get_course_data")
    def test_stale_language_audio_not_counted_as_ready(self, mock_course, mock_session):
        from api.routes.lecture import get_topic_audio_status

        mock_course.return_value = {
            "topics": [{"id": "t1"}],
            "sources": [{"material_id": "m1"}],
            # lecture_language 未設定 -> 既定 "ja"
        }
        chunk_rows = [("c0", "text", "text", "spoken", [], "en")]  # 原稿は英語
        audio_rows = [("c0", 0)]  # 音声キャッシュはあるが言語不一致

        chunk_result = MagicMock()
        chunk_result.fetchall.return_value = chunk_rows
        audio_result = MagicMock()
        audio_result.fetchall.return_value = audio_rows

        session = MagicMock()
        session.execute.side_effect = [chunk_result, audio_result]
        mock_session.return_value = session

        result = get_topic_audio_status("c1", "t1", {"id": "u1"})

        assert result["has_audio"] is False
        assert result["ready_slides"] == 0
        assert result["ready_chunks"] == 1  # 「何かキャッシュがある」ことは分かる
        assert result["stale_language"] is True
        assert result["language"] == "ja"

    @patch("api.routes.lecture._pg_session")
    @patch("api.routes.lecture.get_course_data")
    def test_draft_topic_without_generated_audio_is_not_ready(self, mock_course, mock_session):
        """トピック教材ベース（spoken_script あり）のトピックは、音声未生成なら
        has_audio=False。ただしスライド自体は導出される（total_slides>=1）ので、音声を
        生成すれば再生可能になる（旧仕様の「ドラフトは常に total_slides=0」から変更）。"""
        from api.routes.lecture import get_topic_audio_status

        mock_course.return_value = {
            "topics": [{"id": "t1", "spoken_script": "draft only"}],
        }
        # topic_lecture_audio_cache は空（音声未生成）
        audio_result = MagicMock()
        audio_result.fetchall.return_value = []
        session = MagicMock()
        session.execute.return_value = audio_result
        mock_session.return_value = session

        result = get_topic_audio_status("c1", "t1", {"id": "u1"})

        assert result["has_audio"] is False
        assert result["total_slides"] == 1
        assert result["ready_slides"] == 0

    @patch("api.routes.lecture._pg_session")
    @patch("api.routes.lecture.get_course_data")
    def test_draft_topic_with_generated_audio_is_ready(self, mock_course, mock_session):
        """トピック音声が生成済みなら has_audio=True（実声で読み上げ可能）。"""
        from api.routes.lecture import get_topic_audio_status

        mock_course.return_value = {
            "topics": [{"id": "t1", "spoken_script": "Speak A\n===\nSpeak B",
                        "student_material": {"source_text": "Show A\n===\nShow B"}}],
        }
        # 両スライドとも ja でキャッシュ済み
        audio_result = MagicMock()
        audio_result.fetchall.return_value = [(0, "ja"), (1, "ja")]
        session = MagicMock()
        session.execute.return_value = audio_result
        mock_session.return_value = session

        result = get_topic_audio_status("c1", "t1", {"id": "u1"})

        assert result["has_audio"] is True
        assert result["total_slides"] == 2
        assert result["ready_slides"] == 2
        assert result["stale_language"] is False

    @patch("api.routes.lecture.get_course_data")
    def test_no_material_ids_audio_status_is_empty(self, mock_course):
        from api.routes.lecture import get_topic_audio_status

        mock_course.return_value = {"topics": [{"id": "t1"}], "sources": []}
        result = get_topic_audio_status("c1", "t1", {"id": "u1"})

        assert result["has_audio"] is False
        assert result["total_slides"] == 0
        assert result["ready_slides"] == 0
        assert result["stale_language"] is False


# ---------------------------------------------------------------------------
# 5. _batch_audio_worker — スライド単位生成 (lecture_studio.py)
# ---------------------------------------------------------------------------


_WORKER_CHUNK_ID = "550e8400-e29b-41d4-a716-446655440000"


class TestBatchAudioWorkerSlides:
    @patch("api.routes.lecture_studio.scripts.time.sleep", return_value=None)
    @patch("api.routes.lecture_studio.scripts.generate_tts_audio")
    @patch("api.routes.lecture_studio.scripts._pg_session")
    @patch("api.routes.lecture_studio.scripts.update_background_task")
    @patch("api.routes.lecture_studio.scripts.create_background_task")
    def test_worker_generates_audio_per_slide(
        self, mock_create, mock_update, mock_pg, mock_tts, mock_sleep,
    ):
        from api.routes.lecture_studio import _batch_audio_worker

        mock_session = MagicMock()
        mock_session.execute.return_value.fetchone.return_value = None  # 未キャッシュ
        mock_pg.return_value = mock_session
        mock_tts.return_value = b"FAKE_AUDIO_BYTES"

        chunks = [{
            "id": _WORKER_CHUNK_ID,
            "display_text": "Slide A\n===\nSlide B",
            "text": "Slide A\n===\nSlide B",
            "spoken_text": "Speak A\n===\nSpeak B",
            "formulas": [],
            "spoken_language": "ja",
        }]

        _batch_audio_worker("task-1", "course-1", chunks)

        # generate_tts_audio がスライドごとに (2回) 呼ばれること。migration 040 Phase 4:
        # 言語はチャンクの spoken_language を引き継いで渡される。
        assert mock_tts.call_count == 2
        mock_tts.assert_any_call("Speak A", language="ja")
        mock_tts.assert_any_call("Speak B", language="ja")

        # INSERT 文が slide_index / language を含むこと
        insert_calls = [
            c for c in mock_session.execute.call_args_list
            if "INSERT INTO lecture_audio_cache" in str(c.args[0])
        ]
        assert len(insert_calls) == 2
        for call in insert_calls:
            params = call.args[1]
            assert params["voice"] == "alloy"
            assert params["language"] == "ja"
            assert "slide_index" in params
        assert "ON CONFLICT (chunk_id, slide_index, voice)" in str(insert_calls[0].args[0])

        # 分割数変化時の残留行掃除 DELETE が実行されること
        delete_calls = [
            c for c in mock_session.execute.call_args_list
            if "DELETE FROM lecture_audio_cache" in str(c.args[0])
        ]
        assert len(delete_calls) == 1
        assert delete_calls[0].args[1]["slide_count"] == 2

        # 完了時: チャンク単位で generated=1 として集計されること
        completed_calls = [c for c in mock_update.call_args_list if c.args[1] == "completed"]
        assert completed_calls
        result_data = completed_calls[-1].kwargs["result_data"]
        assert result_data["generated"] == 1
        assert result_data["errors"] == 0

    @patch("api.routes.lecture_studio.scripts.time.sleep", return_value=None)
    @patch("api.routes.lecture_studio.scripts.generate_tts_audio")
    @patch("api.routes.lecture_studio.scripts._pg_session")
    @patch("api.routes.lecture_studio.scripts.update_background_task")
    @patch("api.routes.lecture_studio.scripts.create_background_task")
    def test_worker_skips_already_cached_slides(
        self, mock_create, mock_update, mock_pg, mock_tts, mock_sleep,
    ):
        from api.routes.lecture_studio import _batch_audio_worker

        mock_session = MagicMock()
        mock_session.execute.return_value.fetchone.return_value = (1,)  # 既にキャッシュ済み
        mock_pg.return_value = mock_session

        chunks = [{
            "id": _WORKER_CHUNK_ID,
            "display_text": "Slide A\n===\nSlide B",
            "text": "Slide A\n===\nSlide B",
            "spoken_text": "Speak A\n===\nSpeak B",
            "formulas": [],
        }]

        _batch_audio_worker("task-1", "course-1", chunks)

        mock_tts.assert_not_called()
        completed_calls = [c for c in mock_update.call_args_list if c.args[1] == "completed"]
        result_data = completed_calls[-1].kwargs["result_data"]
        assert result_data["skipped"] == 1
        assert result_data["generated"] == 0

    @patch("api.routes.lecture_studio.scripts.generate_tts_audio")
    @patch("api.routes.lecture_studio.scripts._pg_session")
    @patch("api.routes.lecture_studio.scripts.update_background_task")
    @patch("api.routes.lecture_studio.scripts.create_background_task")
    def test_worker_skips_chunk_without_spoken_text(
        self, mock_create, mock_update, mock_pg, mock_tts,
    ):
        from api.routes.lecture_studio import _batch_audio_worker

        chunks = [{
            "id": _WORKER_CHUNK_ID,
            "display_text": "text",
            "text": "text",
            "spoken_text": "",
            "formulas": [],
        }]

        _batch_audio_worker("task-1", "course-1", chunks)

        mock_tts.assert_not_called()
        mock_pg.assert_not_called()
        completed_calls = [c for c in mock_update.call_args_list if c.args[1] == "completed"]
        result_data = completed_calls[-1].kwargs["result_data"]
        assert result_data["skipped"] == 1

    @patch("api.routes.lecture_studio.scripts.generate_tts_audio")
    @patch("api.routes.lecture_studio.scripts._pg_session")
    @patch("api.routes.lecture_studio.scripts.update_background_task")
    @patch("api.routes.lecture_studio.scripts.create_background_task")
    def test_worker_aborts_on_fatal_tts_error(
        self, mock_create, mock_update, mock_pg, mock_tts,
    ):
        from api.routes.lecture_studio import _batch_audio_worker
        from core.tts import TtsFatalError

        mock_session = MagicMock()
        mock_session.execute.return_value.fetchone.return_value = None
        mock_pg.return_value = mock_session
        mock_tts.side_effect = TtsFatalError("API not enabled")

        chunks = [{
            "id": _WORKER_CHUNK_ID,
            "display_text": "Slide A",
            "text": "Slide A",
            "spoken_text": "Speak A",
            "formulas": [],
        }]

        _batch_audio_worker("task-1", "course-1", chunks)

        failed_calls = [c for c in mock_update.call_args_list if c.args[1] == "failed"]
        assert failed_calls
