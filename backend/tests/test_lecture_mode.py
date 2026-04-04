"""Issue #66: インタラクティブ・レクチャーモードのテスト。

- spoken_text / formulas 生成ロジック
- レクチャーシーケンス構築ロジック
- ワードタイムスタンプ推定ロジック
- Pydantic スキーマの検証
"""

from __future__ import annotations

import json
import os
import sys
from unittest.mock import patch, MagicMock

import pytest

# api/ ディレクトリを sys.path に追加して schemas 等をインポート可能にする
_API_DIR = os.path.join(os.path.dirname(__file__), "..", "api")
if _API_DIR not in sys.path:
    sys.path.insert(0, _API_DIR)


# ---------------------------------------------------------------------------
# 1. spoken_text / formulas 生成テスト
# ---------------------------------------------------------------------------

class TestFallbackSpokenText:
    """LLM不使用のフォールバック spoken_text 生成ロジック。"""

    def test_fallback_with_display_math(self):
        from core.lecture import _fallback_spoken_text

        text = "エネルギーは $$E = mc^2$$ で表される。"
        result = _fallback_spoken_text(text)

        assert "spoken_text" in result
        assert "formulas" in result
        assert len(result["formulas"]) == 1
        assert result["formulas"][0]["latex"] == "E = mc^2"
        assert result["formulas"][0]["id"] == "formula_0"
        assert "（数式1）" in result["spoken_text"]

    def test_fallback_with_inline_math(self):
        from core.lecture import _fallback_spoken_text

        text = "変数 $x$ と $y$ の関係"
        result = _fallback_spoken_text(text)

        assert len(result["formulas"]) == 2
        assert result["formulas"][0]["latex"] == "x"
        assert result["formulas"][1]["latex"] == "y"

    def test_fallback_no_math(self):
        from core.lecture import _fallback_spoken_text

        text = "これは普通のテキストです。"
        result = _fallback_spoken_text(text)

        assert result["spoken_text"] == text
        assert len(result["formulas"]) == 0

    def test_fallback_empty_text(self):
        from core.lecture import _fallback_spoken_text

        result = _fallback_spoken_text("")
        assert result["spoken_text"] == ""
        assert result["formulas"] == []

    def test_fallback_mixed_math(self):
        from core.lecture import _fallback_spoken_text

        text = "インライン $a+b$ とディスプレイ $$F = ma$$ が混在"
        result = _fallback_spoken_text(text)

        assert len(result["formulas"]) == 2
        latex_values = [f["latex"] for f in result["formulas"]]
        assert "F = ma" in latex_values
        assert "a+b" in latex_values


class TestGenerateSpokenTextAndFormulas:
    """LLM 経由の spoken_text 生成（モック使用）。"""

    @patch("core.lecture.generate_text")
    def test_llm_success(self, mock_gen):
        from core.lecture import generate_spoken_text_and_formulas

        mock_gen.return_value = json.dumps({
            "spoken_text": "Eイコールmcの二乗",
            "formulas": [{"id": "formula_0", "latex": "E=mc^2", "spoken": "Eイコールmcの二乗"}],
        })

        result = generate_spoken_text_and_formulas("Energy is $E=mc^2$")
        assert result["spoken_text"] == "Eイコールmcの二乗"
        assert len(result["formulas"]) == 1

    @patch("core.lecture.generate_text")
    def test_llm_with_code_fences(self, mock_gen):
        from core.lecture import generate_spoken_text_and_formulas

        mock_gen.return_value = '```json\n{"spoken_text": "テスト", "formulas": []}\n```'

        result = generate_spoken_text_and_formulas("テスト")
        assert result["spoken_text"] == "テスト"
        assert result["formulas"] == []

    @patch("core.lecture.generate_text")
    def test_llm_failure_uses_fallback(self, mock_gen):
        from core.lecture import generate_spoken_text_and_formulas

        mock_gen.side_effect = Exception("API Error")

        result = generate_spoken_text_and_formulas("$E=mc^2$")
        assert "spoken_text" in result
        assert len(result["formulas"]) == 1

    def test_empty_input(self):
        from core.lecture import generate_spoken_text_and_formulas

        result = generate_spoken_text_and_formulas("")
        assert result["spoken_text"] == ""
        assert result["formulas"] == []

    def test_whitespace_only_input(self):
        from core.lecture import generate_spoken_text_and_formulas

        result = generate_spoken_text_and_formulas("   ")
        assert result["spoken_text"] == ""
        assert result["formulas"] == []


# ---------------------------------------------------------------------------
# 2. レクチャーシーケンス構築テスト
# ---------------------------------------------------------------------------

class TestBuildLectureSequence:
    """レクチャーシーケンスの構築ロジック。"""

    def test_empty_chunks(self):
        from core.lecture import build_lecture_sequence

        result = build_lecture_sequence("topic-1", {"topics": []}, [])
        assert result == []

    def test_sorts_by_chunk_index(self):
        from core.lecture import build_lecture_sequence

        chunks = [
            {"id": "c3", "chunk_index": 2, "text": "third", "spoken_text": "third"},
            {"id": "c1", "chunk_index": 0, "text": "first", "spoken_text": "first"},
            {"id": "c2", "chunk_index": 1, "text": "second", "spoken_text": "second"},
        ]
        result = build_lecture_sequence("topic-1", {"topics": []}, chunks)

        assert len(result) == 3
        assert result[0]["chunk_id"] == "c1"
        assert result[1]["chunk_id"] == "c2"
        assert result[2]["chunk_id"] == "c3"

    def test_uses_text_as_spoken_fallback(self):
        from core.lecture import build_lecture_sequence

        chunks = [
            {"id": "c1", "chunk_index": 0, "text": "plain text", "spoken_text": None},
        ]
        result = build_lecture_sequence("topic-1", {"topics": []}, chunks)

        assert result[0]["spoken_text"] == "plain text"

    def test_preserves_formulas(self):
        from core.lecture import build_lecture_sequence

        formulas = [{"id": "formula_0", "latex": "x^2", "spoken": "x squared"}]
        chunks = [
            {"id": "c1", "chunk_index": 0, "text": "test", "spoken_text": "test", "formulas": formulas},
        ]
        result = build_lecture_sequence("topic-1", {"topics": []}, chunks)

        assert result[0]["formulas"] == formulas

    def test_segment_structure(self):
        from core.lecture import build_lecture_sequence

        chunks = [
            {
                "id": "chunk-abc",
                "chunk_index": 0,
                "text": "Hello world",
                "spoken_text": "Hello world spoken",
                "formulas": [],
                "has_audio": True,
                "duration_ms": 5000,
            },
        ]
        result = build_lecture_sequence("topic-1", {"topics": []}, chunks)

        seg = result[0]
        assert seg["chunk_id"] == "chunk-abc"
        assert seg["chunk_index"] == 0
        assert seg["text"] == "Hello world"
        assert seg["spoken_text"] == "Hello world spoken"
        assert seg["has_audio"] is True
        assert seg["duration_ms"] == 5000


# ---------------------------------------------------------------------------
# 3. Pydantic スキーマ検証テスト (旧4)
# ---------------------------------------------------------------------------

class TestLectureSchemas:
    """レクチャーモードの Pydantic スキーマ。"""

    def test_lecture_formula_item(self):
        from schemas import LectureFormulaItem

        f = LectureFormulaItem(id="formula_0", latex="E=mc^2", spoken="E equals mc squared")
        assert f.id == "formula_0"
        assert f.latex == "E=mc^2"

    def test_lecture_segment(self):
        from schemas import LectureSegment

        seg = LectureSegment(
            chunk_id="abc",
            chunk_index=0,
            text="test",
            spoken_text="test spoken",
            formulas=[],
            has_audio=False,
            duration_ms=0,
        )
        assert seg.chunk_id == "abc"
        assert seg.spoken_text == "test spoken"

    def test_lecture_sequence_response(self):
        from schemas import LectureSequenceResponse

        resp = LectureSequenceResponse(
            course_id="c1",
            topic_id="t1",
            segments=[],
            total_segments=0,
            total_duration_ms=0,
        )
        assert resp.course_id == "c1"
        assert resp.total_segments == 0

    def test_lecture_tts_request(self):
        from schemas import LectureTTSRequest

        req = LectureTTSRequest(chunk_id="abc")
        assert req.voice == "alloy"

    def test_lecture_interrupt_request(self):
        from schemas import LectureInterruptRequest

        req = LectureInterruptRequest(
            message="質問です",
            current_chunk_id="abc",
            pause_position_ms=5000,
        )
        assert req.message == "質問です"
        assert req.pause_position_ms == 5000

    def test_lecture_interrupt_response(self):
        from schemas import LectureInterruptResponse

        resp = LectureInterruptResponse(
            answer="回答です",
            resume_chunk_id="abc",
            resume_position_ms=5000,
        )
        assert resp.answer == "回答です"
        assert resp.resume_chunk_id == "abc"


# ---------------------------------------------------------------------------
# 5. ORM モデル検証テスト
# ---------------------------------------------------------------------------

class TestLectureModels:
    """レクチャーモード関連の ORM モデル。"""

    def test_chunk_has_spoken_text_column(self):
        from core.models import Chunk

        assert hasattr(Chunk, "spoken_text")
        assert hasattr(Chunk, "formulas")

    def test_lecture_audio_cache_model(self):
        from core.models import LectureAudioCache

        assert hasattr(LectureAudioCache, "chunk_id")
        assert hasattr(LectureAudioCache, "voice")
        assert hasattr(LectureAudioCache, "audio_data")
        assert hasattr(LectureAudioCache, "duration_ms")
        assert hasattr(LectureAudioCache, "word_timestamps")
        assert LectureAudioCache.__tablename__ == "lecture_audio_cache"


# ---------------------------------------------------------------------------
# 6. 適応的セグメント分類テスト
# ---------------------------------------------------------------------------

class TestClassifySegment:
    """_classify_segment による full / summary / skip 分類ロジック。"""

    def test_no_mastered_returns_full(self):
        from core.lecture import _classify_segment

        result = _classify_segment("Some text about linear algebra", set(), set())
        assert result == "full"

    def test_short_chunk_with_mastered_prereq_returns_skip(self):
        from core.lecture import _classify_segment

        text = "線形代数の基本的な定義です。"  # short (<= 200 chars)
        mastered = {"線形代数"}
        prereqs = {"線形代数"}
        result = _classify_segment(text, mastered, prereqs)
        assert result == "skip"

    def test_medium_chunk_with_multiple_prereqs_returns_summary(self):
        from core.lecture import _classify_segment

        # 201-500 chars: triggers "summary" instead of "skip"
        text = "線形代数と微分積分はどちらも重要な基礎科目です。これらの概念は高度な数学の基盤となります。" * 5
        assert 200 < len(text) <= 500, f"text len={len(text)}"
        mastered = {"線形代数", "微分積分"}
        prereqs = {"線形代数", "微分積分"}
        result = _classify_segment(text, mastered, prereqs)
        assert result == "summary"

    def test_long_chunk_returns_full(self):
        from core.lecture import _classify_segment

        text = "線形代数の応用について。" * 100  # > 500 chars
        mastered = {"線形代数"}
        prereqs = {"線形代数"}
        result = _classify_segment(text, mastered, prereqs)
        assert result == "full"

    def test_mastered_but_not_prereq_short(self):
        from core.lecture import _classify_segment

        text = "量子力学の基本です。"
        mastered = {"量子力学"}
        prereqs = set()  # not a prerequisite
        result = _classify_segment(text, mastered, prereqs)
        # matched_prereq == 0, so won't skip even though short
        assert result == "full"

    def test_no_concept_match_returns_full(self):
        from core.lecture import _classify_segment

        text = "This text has no matching concepts at all."
        mastered = {"量子力学", "線形代数"}
        prereqs = {"量子力学", "線形代数"}
        result = _classify_segment(text, mastered, prereqs)
        assert result == "full"


# ---------------------------------------------------------------------------
# 7. 習得済み概念を考慮したレクチャーシーケンス構築テスト
# ---------------------------------------------------------------------------

class TestBuildLectureSequenceWithMastery:
    """mastered_concepts を渡した場合の build_lecture_sequence 動作。"""

    def test_no_mastery_all_full(self):
        from core.lecture import build_lecture_sequence

        chunks = [
            {"id": "c1", "chunk_index": 0, "text": "新しい概念の説明", "spoken_text": "新しい概念の説明"},
            {"id": "c2", "chunk_index": 1, "text": "別の概念", "spoken_text": "別の概念"},
        ]
        result = build_lecture_sequence("topic-1", {"topics": []}, chunks, mastered_concepts=None)
        assert len(result) == 2
        assert all(s["segment_mode"] == "full" for s in result)

    def test_skip_short_mastered_prereq_chunk(self):
        from core.lecture import build_lecture_sequence

        course_data = {
            "topics": [{
                "id": "topic-1",
                "title": "応用数学",
                "prerequisites": [{"name": "線形代数", "status": "mastered"}],
            }],
        }
        chunks = [
            {"id": "c1", "chunk_index": 0, "text": "線形代数の基本定義", "spoken_text": "線形代数の基本定義"},
            {"id": "c2", "chunk_index": 1, "text": "応用数学の新しいトピックについて詳細に解説する長いテキスト" * 10,
             "spoken_text": "応用数学の新しいトピック"},
        ]
        result = build_lecture_sequence(
            "topic-1", course_data, chunks, mastered_concepts={"線形代数"},
        )
        # c1 is short and about a mastered prereq -> skipped
        assert len(result) == 1
        assert result[0]["chunk_id"] == "c2"

    def test_summary_medium_mastered_prereq_chunk(self):
        from core.lecture import build_lecture_sequence

        course_data = {
            "topics": [{
                "id": "topic-1",
                "title": "応用",
                "prerequisites": [
                    {"name": "線形代数", "status": "mastered"},
                    {"name": "微分積分", "status": "mastered"},
                ],
            }],
        }
        # 201-500 chars to trigger "summary" (not "skip")
        text = "線形代数と微分積分の基礎を復習します。これらの概念は高度な数学の基盤となり、応用分野で活用されます。" * 5
        assert 200 < len(text) <= 500, f"text len={len(text)}"
        chunks = [
            {"id": "c1", "chunk_index": 0, "text": text, "spoken_text": text},
        ]
        result = build_lecture_sequence(
            "topic-1", course_data, chunks, mastered_concepts={"線形代数", "微分積分"},
        )
        assert len(result) == 1
        assert result[0]["segment_mode"] == "summary"

    def test_segment_mode_in_output(self):
        from core.lecture import build_lecture_sequence

        chunks = [
            {"id": "c1", "chunk_index": 0, "text": "test", "spoken_text": "test"},
        ]
        result = build_lecture_sequence("topic-1", {"topics": []}, chunks)
        assert "segment_mode" in result[0]
        assert result[0]["segment_mode"] == "full"


# ---------------------------------------------------------------------------
# 8. スキーマの segment_mode / skipped_segments / summary_segments テスト
# ---------------------------------------------------------------------------

class TestLectureSchemasAdaptive:
    """適応的シーケンス関連の Pydantic フィールド。"""

    def test_segment_mode_default(self):
        from schemas import LectureSegment

        seg = LectureSegment(
            chunk_id="abc", chunk_index=0, text="t", spoken_text="t",
        )
        assert seg.segment_mode == "full"

    def test_segment_mode_summary(self):
        from schemas import LectureSegment

        seg = LectureSegment(
            chunk_id="abc", chunk_index=0, text="t", spoken_text="t",
            segment_mode="summary",
        )
        assert seg.segment_mode == "summary"

    def test_sequence_response_skipped_and_summary(self):
        from schemas import LectureSequenceResponse

        resp = LectureSequenceResponse(
            course_id="c1", topic_id="t1",
            total_segments=5, total_duration_ms=0,
            skipped_segments=2, summary_segments=1,
        )
        assert resp.skipped_segments == 2
        assert resp.summary_segments == 1


# ---------------------------------------------------------------------------
# 9. UUID バリデーション / フォールバック安全性テスト (Issue #68)
# ---------------------------------------------------------------------------


class TestIsValidUuid:
    """_is_valid_uuid ヘルパーの検証。"""

    def test_valid_uuid4(self):
        from api.routes.lecture import _is_valid_uuid

        assert _is_valid_uuid("550e8400-e29b-41d4-a716-446655440000") is True

    def test_search_prefix_is_invalid(self):
        from api.routes.lecture import _is_valid_uuid

        assert _is_valid_uuid("search_0") is False
        assert _is_valid_uuid("search_42") is False

    def test_empty_string_is_invalid(self):
        from api.routes.lecture import _is_valid_uuid

        assert _is_valid_uuid("") is False

    def test_arbitrary_string_is_invalid(self):
        from api.routes.lecture import _is_valid_uuid

        assert _is_valid_uuid("not-a-uuid") is False

    def test_none_is_invalid(self):
        from api.routes.lecture import _is_valid_uuid

        assert _is_valid_uuid(None) is False


class TestHelperUuidGuards:
    """DB ヘルパー関数が非 UUID chunk_id でクラッシュしないことを検証 (Issue #68)。"""

    def test_check_audio_cache_non_uuid(self):
        from api.routes.lecture import _check_audio_cache

        assert _check_audio_cache("search_0") is False

    def test_get_audio_cache_non_uuid(self):
        from api.routes.lecture import _get_audio_cache

        assert _get_audio_cache("search_0", "alloy") is None

    @patch("api.routes.lecture._pg_session")
    def test_save_audio_cache_non_uuid_skips_db(self, mock_session):
        from api.routes.lecture import _save_audio_cache

        _save_audio_cache("search_0", "alloy", b"audio", 1000, [])
        mock_session.assert_not_called()

    def test_get_chunk_spoken_text_non_uuid(self):
        from api.routes.lecture import _get_chunk_spoken_text

        assert _get_chunk_spoken_text("search_0") is None

    def test_get_chunk_text_non_uuid(self):
        from api.routes.lecture import _get_chunk_text

        assert _get_chunk_text("search_0") == ""

    @patch("api.routes.lecture._pg_session")
    def test_persist_spoken_text_filters_non_uuid(self, mock_session):
        from api.routes.lecture import _persist_spoken_text

        _persist_spoken_text([
            {"id": "search_0", "spoken_text": "test", "formulas": []},
            {"id": "search_1", "spoken_text": "test2", "formulas": []},
        ])
        # All entries are non-UUID, so DB should not be touched
        mock_session.assert_not_called()


class TestGenerateSequenceFromSearchUsesRealIds:
    """_generate_sequence_from_search が検索結果の実 UUID を使用することを検証。"""

    @patch("api.routes.lecture._persist_spoken_text")
    @patch("api.routes.lecture._check_audio_cache", return_value=False)
    @patch("api.routes.lecture.generate_spoken_text_and_formulas")
    @patch("api.routes.lecture.search_chunks_with_metadata")
    def test_uses_chunk_id_from_search_results(
        self, mock_search, mock_gen, mock_cache, mock_persist,
    ):
        from api.routes.lecture import _generate_sequence_from_search

        real_uuid = "550e8400-e29b-41d4-a716-446655440000"
        mock_search.return_value = [
            {"id": real_uuid, "text": "test content", "score": 0.9},
        ]
        mock_gen.return_value = {"spoken_text": "spoken", "formulas": []}

        result = _generate_sequence_from_search(
            "course-1", "topic-1", "Test Topic", {}, {"id": "user-1"},
        )

        assert len(result.segments) == 1
        assert result.segments[0].chunk_id == real_uuid

    @patch("api.routes.lecture._persist_spoken_text")
    @patch("api.routes.lecture.generate_spoken_text_and_formulas")
    @patch("api.routes.lecture.search_chunks_with_metadata")
    def test_fallback_id_when_no_id_in_result(
        self, mock_search, mock_gen, mock_persist,
    ):
        from api.routes.lecture import _generate_sequence_from_search

        mock_search.return_value = [
            {"text": "test content", "score": 0.9},  # no "id" key
        ]
        mock_gen.return_value = {"spoken_text": "spoken", "formulas": []}

        result = _generate_sequence_from_search(
            "course-1", "topic-1", "Test Topic", {}, {"id": "user-1"},
        )

        assert len(result.segments) == 1
        assert result.segments[0].chunk_id == "search_0"
        assert result.segments[0].has_audio is False
