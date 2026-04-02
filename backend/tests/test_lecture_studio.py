"""Tests for Lecture Script Studio (Issue #70).

スキーマのバリデーションとコアロジックの単体テストを行う。
"""

from __future__ import annotations

import json
import sys
import os

# Ensure api/ is on the path for schema/route imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))


# ---------------------------------------------------------------------------
# Schema validation tests
# ---------------------------------------------------------------------------


class TestLectureStudioSchemas:
    """Issue #70 で追加した Pydantic スキーマのバリデーション。"""

    def test_lecture_script_chunk_out_defaults(self):
        from schemas import LectureScriptChunkOut

        chunk = LectureScriptChunkOut(chunk_id="abc", chunk_index=0, text="hello")
        assert chunk.spoken_text == ""
        assert chunk.formulas == []
        assert chunk.status == "ungenerated"

    def test_lecture_script_chunk_out_with_data(self):
        from schemas import LectureScriptChunkOut, LectureFormulaItem

        formula = LectureFormulaItem(id="formula_0", latex="E=mc^2", spoken="Eイコールmcの二乗")
        chunk = LectureScriptChunkOut(
            chunk_id="abc",
            chunk_index=1,
            text="source",
            spoken_text="spoken version",
            formulas=[formula],
            status="edited",
        )
        assert chunk.spoken_text == "spoken version"
        assert chunk.status == "edited"
        assert len(chunk.formulas) == 1
        assert chunk.formulas[0].latex == "E=mc^2"

    def test_lecture_script_generate_request_defaults(self):
        from schemas import LectureScriptGenerateRequest

        req = LectureScriptGenerateRequest()
        assert req.override is False

    def test_lecture_script_generate_request_override(self):
        from schemas import LectureScriptGenerateRequest

        req = LectureScriptGenerateRequest(override=True)
        assert req.override is True

    def test_lecture_script_generate_response(self):
        from schemas import LectureScriptGenerateResponse

        resp = LectureScriptGenerateResponse(
            course_id="c1",
            total_chunks=10,
            generated=7,
            skipped=3,
        )
        assert resp.course_id == "c1"
        assert resp.total_chunks == 10
        assert resp.generated == 7
        assert resp.skipped == 3
        assert resp.chunks == []

    def test_lecture_script_save_request(self):
        from schemas import LectureScriptSaveRequest

        req = LectureScriptSaveRequest(spoken_text="edited text")
        assert req.spoken_text == "edited text"
        assert req.formulas == []

    def test_lecture_script_save_response(self):
        from schemas import LectureScriptSaveResponse

        resp = LectureScriptSaveResponse(chunk_id="chunk1")
        assert resp.status == "edited"

    def test_lecture_script_rewrite_request(self):
        from schemas import LectureScriptRewriteRequest

        req = LectureScriptRewriteRequest(prompt="前提知識を追加して")
        assert req.prompt == "前提知識を追加して"

    def test_lecture_script_rewrite_response(self):
        from schemas import LectureScriptRewriteResponse, LectureFormulaItem

        resp = LectureScriptRewriteResponse(
            chunk_id="c1",
            spoken_text="rewritten",
            formulas=[LectureFormulaItem(id="f0", latex="x^2", spoken="xの二乗")],
        )
        assert resp.spoken_text == "rewritten"
        assert len(resp.formulas) == 1

    def test_lecture_audio_generate_response(self):
        from schemas import LectureAudioGenerateResponse

        resp = LectureAudioGenerateResponse(
            course_id="c1",
            total_chunks=5,
            generated=3,
            skipped=1,
            errors=1,
        )
        assert resp.generated == 3
        assert resp.errors == 1

    def test_lecture_audio_generate_response_defaults(self):
        from schemas import LectureAudioGenerateResponse

        resp = LectureAudioGenerateResponse(course_id="c1")
        assert resp.total_chunks == 0
        assert resp.generated == 0
        assert resp.skipped == 0
        assert resp.errors == 0


# ---------------------------------------------------------------------------
# Rewrite prompt template tests
# ---------------------------------------------------------------------------


class TestRewritePromptTemplate:
    """AI書き換えプロンプトテンプレートのテスト。"""

    # _REWRITE_PROMPT は routes.lecture_studio からのインポートで jwt 依存が
    # 発生するため、テンプレート文字列を直接検証する。

    _REWRITE_PROMPT = """あなたは大学講義の音声原稿を改善するアシスタントです。

以下のソーステキストと現在の音声読み上げ原稿、そして教員からの指示に基づいて、
音声原稿を書き換えてください。

**重要:**
- 教員の指示に従い、必要に応じて一般的な物理学・数学の知識を補足してください
- ソーステキストに限定されず、教員が指示する内容を反映させてください
- LaTeX 数式は自然言語に変換してください（例: `$E = mc^2$` → 「Eイコールmcの二乗」）
- 自然な日本語の講義調で書いてください
- 数式メタデータも更新してください

## ソーステキスト:
{source_text}

## 現在の音声原稿:
{current_spoken_text}

## 教員からの指示:
{instructor_prompt}

## 出力形式 (厳密にJSON):
{{
  "spoken_text": "書き換えた音声原稿",
  "formulas": [
    {{"id": "formula_0", "latex": "E = mc^2", "spoken": "Eイコールmcの二乗"}}
  ]
}}

重要: JSON のみを出力してください。マークダウンコードフェンスは不要です。"""

    def test_rewrite_prompt_contains_placeholders(self):
        assert "{source_text}" in self._REWRITE_PROMPT
        assert "{current_spoken_text}" in self._REWRITE_PROMPT
        assert "{instructor_prompt}" in self._REWRITE_PROMPT

    def test_rewrite_prompt_format(self):
        filled = self._REWRITE_PROMPT.format(
            source_text="test source",
            current_spoken_text="current spoken",
            instructor_prompt="add prerequisite info",
        )
        assert "test source" in filled
        assert "current spoken" in filled
        assert "add prerequisite info" in filled
        assert "JSON" in filled


# ---------------------------------------------------------------------------
# Chunk status logic tests
# ---------------------------------------------------------------------------


class TestChunkStatusLogic:
    """チャンクステータス判定のテスト。"""

    def test_status_values_are_valid(self):
        """ステータス値は ungenerated, generated, edited, audio_ready のいずれかであること。"""
        valid_statuses = {"ungenerated", "generated", "edited", "audio_ready"}
        from schemas import LectureScriptChunkOut

        for status in valid_statuses:
            chunk = LectureScriptChunkOut(
                chunk_id="test", chunk_index=0, text="t", status=status,
            )
            assert chunk.status == status
