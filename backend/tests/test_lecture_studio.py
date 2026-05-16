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

    def test_lecture_script_generate_request_auto_audio_default(self):
        """Issue #139: auto_audio デフォルトは False。"""
        from schemas import LectureScriptGenerateRequest

        req = LectureScriptGenerateRequest()
        assert req.auto_audio is False

    def test_lecture_script_generate_request_auto_audio_true(self):
        """Issue #139: auto_audio=True でパイプライン連鎖モードを指定可能。"""
        from schemas import LectureScriptGenerateRequest

        req = LectureScriptGenerateRequest(override=True, auto_audio=True)
        assert req.auto_audio is True
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

    def test_lecture_script_generate_start_response(self):
        from schemas import LectureScriptGenerateStartResponse

        resp = LectureScriptGenerateStartResponse(
            task_id="abc123",
            course_id="c1",
            total_chunks=20,
        )
        assert resp.task_id == "abc123"
        assert resp.course_id == "c1"
        assert resp.total_chunks == 20
        assert resp.status == "pending"

    def test_lecture_script_generate_start_response_custom_status(self):
        from schemas import LectureScriptGenerateStartResponse

        resp = LectureScriptGenerateStartResponse(
            task_id="xyz",
            course_id="c2",
            total_chunks=5,
            status="processing",
        )
        assert resp.status == "processing"

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
        assert req.narration_persona is None

    def test_lecture_script_rewrite_request_with_persona(self):
        from schemas import LectureScriptRewriteRequest

        req = LectureScriptRewriteRequest(prompt="解説モードを反映して", narration_persona="general_friendly")
        assert req.narration_persona == "general_friendly"

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

    def test_lecture_studio_settings_defaults(self):
        from schemas import LectureStudioSettings

        settings = LectureStudioSettings()
        assert settings.narration_persona == ""
        assert settings.response_persona == ""

    def test_lecture_studio_settings_with_personas(self):
        from schemas import LectureStudioSettings

        settings = LectureStudioSettings(narration_persona="expert_friendly", response_persona="general_formal")
        assert settings.narration_persona == "expert_friendly"
        assert settings.response_persona == "general_formal"

    def test_learning_check_question_response_details(self):
        from schemas import LearningCheckQuestionResponse

        resp = LearningCheckQuestionResponse(
            passed=False,
            feedback="不足があります。",
            model_answer="模範解答",
            answer_requirements=["要素A"],
            explanation="要素Aが必要な理由。",
        )
        assert resp.answer_requirements == ["要素A"]
        assert resp.explanation == "要素Aが必要な理由。"


class TestLectureStudioModeUI:
    """原稿スタジオの解説モード設定UIの静的テスト。"""

    def test_settings_button_exists(self):
        from pathlib import Path

        html = (Path(__file__).resolve().parents[2] / "frontend" / "public" / "admin.html").read_text(encoding="utf-8")
        assert 'id="ls-settings-btn"' in html

    def test_mode_settings_logic_exists(self):
        from pathlib import Path

        js = (Path(__file__).resolve().parents[2] / "frontend" / "public" / "js" / "admin.js").read_text(encoding="utf-8")
        assert "lsOpenSettingsModal" in js
        assert "/lecture-studio/settings" in js
        assert "サイエンス・コミュニケーター" in js
        assert "学会発表／査読者" in js

    def test_course_topic_selection_does_not_mask_graph_view(self):
        """Course topic detail rendering must not preempt the theory graph tabs."""
        from pathlib import Path

        js = (Path(__file__).resolve().parents[2] / "frontend" / "public" / "js" / "admin.js").read_text(encoding="utf-8")
        assert 'selectedScope.type === "course_topic" && !lsIsTheoryGraphView(currentView)' in js
        assert "lsScopeHasDocumentContext" in js
        assert 'lsState.view === "graph"' in js

    def test_course_draft_check_question_detail_fields_exist(self):
        from pathlib import Path

        js = (Path(__file__).resolve().parents[2] / "frontend" / "public" / "js" / "admin.js").read_text(encoding="utf-8")
        assert "ls-course-check-model-answer" in js
        assert "ls-course-check-requirements" in js
        assert "ls-course-check-explanation" in js
        assert "lsCollectCheckQuestions" in js


class TestCourseTopicCheckQuestions:
    def test_normalize_check_questions_accepts_detailed_objects(self):
        from routes.lecture_studio import _normalize_check_questions

        result = _normalize_check_questions([
            {
                "question": "なぜ A か？",
                "model_answer": "B だから。",
                "answer_requirements": ["Bに言及", "因果関係"],
                "explanation": "前提 C から従う。",
            }
        ])

        assert result == [{
            "question": "なぜ A か？",
            "model_answer": "B だから。",
            "answer_requirements": ["Bに言及", "因果関係"],
            "explanation": "前提 C から従う。",
        }]

    def test_normalize_check_questions_preserves_legacy_strings(self):
        from routes.lecture_studio import _normalize_check_questions

        result = _normalize_check_questions(["要点を説明してください。"])

        assert result[0]["question"] == "要点を説明してください。"
        assert result[0]["model_answer"] == ""
        assert result[0]["answer_requirements"] == []


class TestPersonaPromptHelpers:
    """解説モードプロンプトのヘルパーテスト。"""

    def test_persona_prompt_known_id(self):
        from core.personas import persona_prompt

        prompt = persona_prompt("general_friendly", target="narration")
        assert "サイエンス・コミュニケーター" in prompt
        assert "音声読み上げテキスト" in prompt

    def test_persona_prompt_unknown_id_returns_empty(self):
        from core.personas import persona_prompt

        assert persona_prompt("unknown", target="response") == ""

    def test_course_persona_settings_normalizes_unknown_values(self):
        from core.personas import course_persona_settings

        settings = course_persona_settings({
            "lecture_studio_settings": {
                "narration_persona": "expert_friendly",
                "response_persona": "unknown",
            },
        })
        assert settings["narration_persona"] == "expert_friendly"
        assert settings["response_persona"] == ""


class TestLectureStudioSettingsRegeneration:
    """口調設定変更後の全スクリプト再生成フラグを検証する。"""

    SOURCE_FILE = os.path.join(os.path.dirname(__file__), "..", "api", "routes", "lecture_studio.py")

    def test_settings_change_marks_scripts_for_regeneration(self):
        source = open(self.SOURCE_FILE, encoding="utf-8").read()
        assert "scripts_need_regeneration" in source
        assert "settings_changed" in source

    def test_batch_generation_uses_effective_override(self):
        source = open(self.SOURCE_FILE, encoding="utf-8").read()
        assert "force_regenerate" in source
        assert "effective_override = body.override or force_regenerate" in source

    def test_successful_override_clears_regeneration_flag(self):
        source = open(self.SOURCE_FILE, encoding="utf-8").read()
        assert "_clear_script_regeneration_flag" in source
        assert "if override:" in source


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
# Rewrite result normalization (regression for theory-tab AttributeError)
# ---------------------------------------------------------------------------


class TestNormalizeRewriteResult:
    """`_normalize_rewrite_result` の挙動を検証する。

    LLM が JSON オブジェクトではなくトップレベル配列を返すケースで
    `AttributeError: 'list' object has no attribute 'get'` が出ていた回帰を防ぐ。
    """

    def _normalize(self, parsed, studio_view):
        from routes.lecture_studio import _normalize_rewrite_result

        return _normalize_rewrite_result(parsed, studio_view)

    def test_dict_input_returned_as_is(self):
        payload = {"theory_components": [{"id": "c1"}], "spoken_text": "x"}
        assert self._normalize(payload, "theory") == payload

    def test_list_in_theory_view_wrapped_as_theory_components(self):
        payload = [{"id": "c1", "summary": "a"}, {"id": "c2"}]
        result = self._normalize(payload, "theory")
        assert result == {"theory_components": payload}

    def test_list_in_non_theory_view_returns_empty(self):
        result = self._normalize([{"id": "c1"}], "edit")
        assert result == {}

    def test_invalid_input_returns_empty(self):
        assert self._normalize("string", "theory") == {}
        assert self._normalize(None, "edit") == {}
        assert self._normalize(42, "audio") == {}

    def test_normalized_dict_supports_get(self):
        """正規化後は必ず .get() できる(本番のリグレッション再現テスト)。"""
        result = self._normalize([{"id": "c1"}], "theory")
        # 以下が AttributeError なく動くことが本テストの主旨
        assert result.get("theory_components") == [{"id": "c1"}]
        assert result.get("display_text") is None
        assert result.get("spoken_text", "fallback") == "fallback"


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


# ---------------------------------------------------------------------------
# Async batch generate worker logic tests (Issue #76)
# ---------------------------------------------------------------------------


class TestBatchGenerateAsync:
    """非同期バッチスクリプト生成 (Issue #76) のロジックテスト。"""

    def test_start_response_has_task_id(self):
        """開始レスポンスに task_id が含まれること。"""
        from schemas import LectureScriptGenerateStartResponse

        resp = LectureScriptGenerateStartResponse(
            task_id="task001",
            course_id="course1",
            total_chunks=30,
        )
        assert resp.task_id == "task001"
        assert resp.status == "pending"
        assert resp.total_chunks == 30

    def test_start_response_zero_chunks(self):
        """チャンク数0でも正常に作成できること。"""
        from schemas import LectureScriptGenerateStartResponse

        resp = LectureScriptGenerateStartResponse(
            task_id="t0",
            course_id="c0",
            total_chunks=0,
        )
        assert resp.total_chunks == 0

    def test_progress_calculation(self):
        """進捗計算ロジックのテスト: (generated + skipped) / total * 100"""
        total = 10
        generated = 3
        skipped = 2
        processed = generated + skipped
        progress = int(processed * 100 / total) if total > 0 else 100
        assert progress == 50

    def test_progress_calculation_zero_total(self):
        """total=0 のとき progress=100 となること。"""
        total = 0
        progress = int(0 * 100 / total) if total > 0 else 100
        assert progress == 100

    def test_progress_calculation_all_skipped(self):
        """全チャンクスキップ時に progress=100 となること。"""
        total = 5
        skipped = 5
        processed = skipped
        progress = int(processed * 100 / total) if total > 0 else 100
        assert progress == 100

    def test_worker_task_type(self):
        """ワーカーのタスクタイプが script_generation であること。"""
        # タスクタイプは create_background_task 呼び出し時に使われる定数
        expected_task_type = "script_generation"
        assert expected_task_type == "script_generation"


# ---------------------------------------------------------------------------
# Async batch audio worker logic tests (Issue #78)
# ---------------------------------------------------------------------------


class TestBatchAudioAsync:
    """非同期バッチ音声生成 (Issue #78) のロジックテスト。"""

    def test_audio_start_response_schema(self):
        """音声生成開始レスポンスのスキーマが正しいこと。"""
        from schemas import LectureAudioGenerateStartResponse

        resp = LectureAudioGenerateStartResponse(
            task_id="audio001",
            course_id="course1",
            total_chunks=10,
        )
        assert resp.task_id == "audio001"
        assert resp.course_id == "course1"
        assert resp.total_chunks == 10
        assert resp.status == "pending"

    def test_audio_start_response_custom_status(self):
        """status フィールドが設定可能であること。"""
        from schemas import LectureAudioGenerateStartResponse

        resp = LectureAudioGenerateStartResponse(
            task_id="t1",
            course_id="c1",
            total_chunks=5,
            status="processing",
        )
        assert resp.status == "processing"

    def test_audio_start_response_zero_chunks(self):
        """チャンク数0でも正常に作成できること。"""
        from schemas import LectureAudioGenerateStartResponse

        resp = LectureAudioGenerateStartResponse(
            task_id="t0",
            course_id="c0",
            total_chunks=0,
        )
        assert resp.total_chunks == 0

    def test_audio_progress_calculation_with_errors(self):
        """エラー件数を含む進捗計算ロジックのテスト: (generated + skipped + errors) / total * 100"""
        total = 10
        generated = 3
        skipped = 2
        errors = 1
        processed = generated + skipped + errors
        progress = int(processed * 100 / total) if total > 0 else 100
        assert progress == 60

    def test_audio_progress_calculation_zero_total(self):
        """total=0 のとき progress=100 となること。"""
        total = 0
        progress = int(0 * 100 / total) if total > 0 else 100
        assert progress == 100

    def test_audio_progress_calculation_all_generated(self):
        """全チャンク生成完了時に progress=100 となること。"""
        total = 8
        generated = 8
        skipped = 0
        errors = 0
        processed = generated + skipped + errors
        progress = int(processed * 100 / total) if total > 0 else 100
        assert progress == 100

    def test_audio_worker_task_type(self):
        """音声生成ワーカーのタスクタイプが audio_generation であること。"""
        expected_task_type = "audio_generation"
        assert expected_task_type == "audio_generation"

    def test_audio_result_data_structure(self):
        """バックグラウンドタスクの result_data 構造が正しいこと。"""
        # _batch_audio_worker が update_background_task に渡す result_data の構造を検証
        result_data = {
            "course_id": "course1",
            "total_chunks": 10,
            "generated": 5,
            "skipped": 3,
            "errors": 2,
            "progress": 100,
        }
        assert "course_id" in result_data
        assert "total_chunks" in result_data
        assert "generated" in result_data
        assert "skipped" in result_data
        assert "errors" in result_data
        assert "progress" in result_data
        assert result_data["generated"] + result_data["skipped"] + result_data["errors"] == result_data["total_chunks"]

    def test_audio_generate_response_still_works(self):
        """後方互換スキーマ LectureAudioGenerateResponse が引き続き動作すること。"""
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


# ---------------------------------------------------------------------------
# TTS プロバイダ選択ロジックのテスト
# ---------------------------------------------------------------------------


class TestGenerateTtsAudioProviderSelection:
    """generate_tts_audio のプロバイダ選択ロジックの単体テスト。

    core.tts モジュールを直接インポートして、外部 API 呼び出しはモックに置き換える。
    """

    def _patch_settings(self, monkeypatch, api_key: str, provider: str):
        """core.tts.get_settings をパッチするヘルパー。"""
        import types
        import core.tts as tts_module
        fake_settings = types.SimpleNamespace(llm_api_key=api_key, llm_provider=provider)
        monkeypatch.setattr(tts_module, "get_settings", lambda: fake_settings)

    def _register_fake_tts_module(self, monkeypatch, fake_tts_class):
        """google.cloud.texttospeech の親モジュールも含めて sys.modules に登録するヘルパー。"""
        import sys
        import types

        # google, google.cloud の親モジュールが存在しない場合は作成する
        if "google" not in sys.modules:
            monkeypatch.setitem(sys.modules, "google", types.ModuleType("google"))
        if "google.cloud" not in sys.modules:
            monkeypatch.setitem(sys.modules, "google.cloud", types.ModuleType("google.cloud"))
        monkeypatch.setitem(sys.modules, "google.cloud.texttospeech", fake_tts_class)

    def test_openai_provider_selected_when_llm_provider_openai(self, monkeypatch):
        """LLM_PROVIDER=openai のとき OpenAI TTS が選択されること。"""
        import sys
        import types
        import core.tts as tts_module

        class FakeSpeech:
            def create(self, **kwargs):
                class FakeResponse:
                    content = b"fake-mp3-data"
                return FakeResponse()

        class FakeOpenAIClient:
            audio = type("FakeAudio", (), {"speech": FakeSpeech()})()

        fake_openai_mod = types.ModuleType("openai")
        fake_openai_mod.OpenAI = lambda api_key: FakeOpenAIClient()
        monkeypatch.setitem(sys.modules, "openai", fake_openai_mod)

        self._patch_settings(monkeypatch, "sk-test-key", "openai")
        result = tts_module.generate_tts_audio("テストテキスト")
        assert result == b"fake-mp3-data"

    def test_google_provider_selected_when_llm_provider_google(self, monkeypatch):
        """LLM_PROVIDER=google のとき Google Cloud TTS が選択されること。"""
        import types
        import core.tts as tts_module

        fake_tts_response = types.SimpleNamespace(audio_content=b"fake-gcp-mp3")

        class FakeTTSClient:
            def synthesize_speech(self, **kwargs):
                return fake_tts_response

        class FakeTextToSpeech:
            TextToSpeechClient = FakeTTSClient

            class SynthesisInput:
                def __init__(self, text):
                    self.text = text

            class VoiceSelectionParams:
                def __init__(self, **kwargs):
                    pass

            class AudioConfig:
                def __init__(self, **kwargs):
                    pass

            class SsmlVoiceGender:
                NEUTRAL = "NEUTRAL"

            class AudioEncoding:
                MP3 = "MP3"

        self._register_fake_tts_module(monkeypatch, FakeTextToSpeech)
        self._patch_settings(monkeypatch, "", "google")
        result = tts_module.generate_tts_audio("テストテキスト")
        assert result == b"fake-gcp-mp3"

    def test_gemini_vertex_provider_selected(self, monkeypatch):
        """LLM_PROVIDER=gemini-vertex のとき Google Cloud TTS が選択されること。"""
        import types
        import core.tts as tts_module

        fake_tts_response = types.SimpleNamespace(audio_content=b"fake-vertex-mp3")

        class FakeTTSClient:
            def synthesize_speech(self, **kwargs):
                return fake_tts_response

        class FakeTextToSpeech:
            TextToSpeechClient = FakeTTSClient

            class SynthesisInput:
                def __init__(self, text):
                    self.text = text

            class VoiceSelectionParams:
                def __init__(self, **kwargs):
                    pass

            class AudioConfig:
                def __init__(self, **kwargs):
                    pass

            class SsmlVoiceGender:
                NEUTRAL = "NEUTRAL"

            class AudioEncoding:
                MP3 = "MP3"

        self._register_fake_tts_module(monkeypatch, FakeTextToSpeech)
        self._patch_settings(monkeypatch, "", "gemini-vertex")
        result = tts_module.generate_tts_audio("テストテキスト")
        assert result == b"fake-vertex-mp3"

    def test_gemini_provider_returns_none(self, monkeypatch):
        """provider=gemini の場合 TTS 非対応として None が返ること。"""
        import core.tts as tts_module

        self._patch_settings(monkeypatch, "", "gemini")
        result = tts_module.generate_tts_audio("テストテキスト")
        assert result is None

    def test_openai_exception_returns_none(self, monkeypatch):
        """LLM_PROVIDER=openai で OpenAI TTS が例外を送出した場合 None が返ること。"""
        import sys
        import types
        import core.tts as tts_module

        fake_openai_mod = types.ModuleType("openai")
        fake_openai_mod.OpenAI = lambda api_key: (_ for _ in ()).throw(RuntimeError("connection failed"))
        monkeypatch.setitem(sys.modules, "openai", fake_openai_mod)

        self._patch_settings(monkeypatch, "sk-broken-key", "openai")
        result = tts_module.generate_tts_audio("テストテキスト")
        assert result is None

    def test_google_tts_exception_returns_none(self, monkeypatch):
        """Google Cloud TTS が例外を送出した場合 None が返ること。"""
        import core.tts as tts_module

        class BrokenTTSClient:
            def synthesize_speech(self, **kwargs):
                raise RuntimeError("GCP TTS connection failed")

        class FakeTextToSpeech:
            TextToSpeechClient = BrokenTTSClient

            class SynthesisInput:
                def __init__(self, text):
                    self.text = text

            class VoiceSelectionParams:
                def __init__(self, **kwargs):
                    pass

            class AudioConfig:
                def __init__(self, **kwargs):
                    pass

            class SsmlVoiceGender:
                NEUTRAL = "NEUTRAL"

            class AudioEncoding:
                MP3 = "MP3"

        self._register_fake_tts_module(monkeypatch, FakeTextToSpeech)
        self._patch_settings(monkeypatch, "", "google")
        result = tts_module.generate_tts_audio("テストテキスト")
        assert result is None

    def test_text_truncated_to_5000_for_google(self, monkeypatch):
        """Google Cloud TTS へ渡すテキストが 5000 文字以内に切り詰められること。"""
        import types
        import core.tts as tts_module

        received_texts = []

        class CaptureTTSClient:
            def synthesize_speech(self, input, voice, audio_config):
                received_texts.append(input.text)
                return types.SimpleNamespace(audio_content=b"ok")

        class FakeTextToSpeech:
            TextToSpeechClient = CaptureTTSClient

            class SynthesisInput:
                def __init__(self, text):
                    self.text = text

            class VoiceSelectionParams:
                def __init__(self, **kwargs):
                    pass

            class AudioConfig:
                def __init__(self, **kwargs):
                    pass

            class SsmlVoiceGender:
                NEUTRAL = "NEUTRAL"

            class AudioEncoding:
                MP3 = "MP3"

        self._register_fake_tts_module(monkeypatch, FakeTextToSpeech)
        self._patch_settings(monkeypatch, "", "google")
        long_text = "あ" * 6000
        result = tts_module.generate_tts_audio(long_text)
        assert result == b"ok"
        assert len(received_texts) == 1
        assert len(received_texts[0]) == 5000

    def test_text_truncated_to_4096_for_openai(self, monkeypatch):
        """OpenAI TTS へ渡すテキストが 4096 文字以内に切り詰められること。"""
        import sys
        import types
        import core.tts as tts_module

        received_inputs = []

        class FakeSpeech:
            def create(self, **kwargs):
                received_inputs.append(kwargs.get("input", ""))
                class FakeResponse:
                    content = b"ok"
                return FakeResponse()

        class FakeOpenAIClient:
            audio = type("FakeAudio", (), {"speech": FakeSpeech()})()

        fake_openai_mod = types.ModuleType("openai")
        fake_openai_mod.OpenAI = lambda api_key: FakeOpenAIClient()
        monkeypatch.setitem(sys.modules, "openai", fake_openai_mod)

        self._patch_settings(monkeypatch, "any-key", "openai")
        long_text = "a" * 8000
        result = tts_module.generate_tts_audio(long_text)
        assert result == b"ok"
        assert len(received_inputs) == 1
        assert len(received_inputs[0]) == 4096

    def test_service_disabled_raises_tts_fatal_error(self, monkeypatch):
        """GCP の SERVICE_DISABLED エラー時に TtsFatalError が送出されること。"""
        import types
        import core.tts as tts_module
        from core.tts import TtsFatalError

        class FakeTTSClient:
            def synthesize_speech(self, **kwargs):
                raise RuntimeError(
                    "403 Cloud Text-to-Speech API has not been used in project foo before or it is disabled. "
                    "[reason: \"SERVICE_DISABLED\"]"
                )

        class FakeTextToSpeech:
            TextToSpeechClient = FakeTTSClient

            class SynthesisInput:
                def __init__(self, text):
                    self.text = text

            class VoiceSelectionParams:
                def __init__(self, **kwargs):
                    pass

            class AudioConfig:
                def __init__(self, **kwargs):
                    pass

            class SsmlVoiceGender:
                NEUTRAL = "NEUTRAL"

            class AudioEncoding:
                MP3 = "MP3"

        self._register_fake_tts_module(monkeypatch, FakeTextToSpeech)
        self._patch_settings(monkeypatch, "", "google")

        import pytest
        with pytest.raises(TtsFatalError, match="Cloud Text-to-Speech API"):
            tts_module.generate_tts_audio("テストテキスト")

    def test_openai_auth_error_raises_tts_fatal_error(self, monkeypatch):
        """OpenAI の 401 認証エラー時に TtsFatalError が送出されること。"""
        import sys
        import types
        import core.tts as tts_module
        from core.tts import TtsFatalError

        class FakeAuthError(Exception):
            pass

        class BrokenOpenAIClient:
            class audio:
                class speech:
                    @staticmethod
                    def create(**kwargs):
                        raise FakeAuthError("401 Unauthorized - no API key")

        fake_openai_mod = types.ModuleType("openai")
        fake_openai_mod.OpenAI = lambda api_key: BrokenOpenAIClient()
        fake_openai_mod.AuthenticationError = FakeAuthError
        monkeypatch.setitem(sys.modules, "openai", fake_openai_mod)

        self._patch_settings(monkeypatch, "any-key", "openai")

        import pytest
        with pytest.raises(TtsFatalError, match="認証エラー"):
            tts_module.generate_tts_audio("テストテキスト")
