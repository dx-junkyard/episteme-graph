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
# 3. ワードタイムスタンプ推定テスト
# ---------------------------------------------------------------------------

class TestEstimateWordTimestamps:
    """ワードタイムスタンプの近似推定。"""

    def test_basic_timestamps(self):
        from core.lecture import estimate_word_timestamps

        result = estimate_word_timestamps("hello world test", 3000)

        assert len(result) == 3
        assert result[0]["word"] == "hello"
        assert result[0]["start_ms"] == 0
        assert result[-1]["end_ms"] <= 3000

    def test_empty_text(self):
        from core.lecture import estimate_word_timestamps

        result = estimate_word_timestamps("", 1000)
        assert result == []

    def test_single_word(self):
        from core.lecture import estimate_word_timestamps

        result = estimate_word_timestamps("hello", 1000)
        assert len(result) == 1
        assert result[0]["word"] == "hello"
        assert result[0]["start_ms"] == 0

    def test_timestamps_are_contiguous(self):
        from core.lecture import estimate_word_timestamps

        result = estimate_word_timestamps("aaa bbb ccc", 3000)
        assert len(result) == 3
        # Each word starts where the previous ends
        for i in range(1, len(result)):
            assert result[i]["start_ms"] == result[i - 1]["end_ms"]


# ---------------------------------------------------------------------------
# 4. Pydantic スキーマ検証テスト
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
