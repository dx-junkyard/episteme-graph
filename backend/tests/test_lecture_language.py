"""migration 040 Phase 4: 言語切替バックエンド + 試聴エンドポイントのテスト。

- core.tts.generate_tts_audio: language 引数 → Google locale 反映 / OpenAI voice が
  settings.lecture_tts_voice 由来
- core.lecture: 言語指示のプロンプト注入 (_language_instruction /
  generate_spoken_text_and_formulas)
- schemas: LectureStudioSettings.lecture_language / LectureScriptGenerateRequest.language /
  LectureAudioGenerateRequest.language の Literal["ja","en"] バリデーション
- lecture_studio.py: 原稿生成が spoken_language を書く / 音声生成の同一言語スキップ維持 /
  別言語チェーン (phase: script -> audio) / GET .../lecture-studio/settings の
  roundtrip / LectureScriptChunkOut の新フィールド / 試聴エンドポイント
- audio-status: spoken_language 不一致が ready に数えられないこと（Phase 1 の回帰確認）
"""

from __future__ import annotations

import base64
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

# api/ ディレクトリを sys.path に追加して schemas 等をインポート可能にする
_API_DIR = os.path.join(os.path.dirname(__file__), "..", "api")
if _API_DIR not in sys.path:
    sys.path.insert(0, _API_DIR)


_CHUNK_ID = "550e8400-e29b-41d4-a716-446655440000"

_LECTURE_STUDIO_SOURCE = os.path.join(
    os.path.dirname(__file__), "..", "api", "routes", "lecture_studio.py",
)


def _read_lecture_studio_source() -> str:
    return open(_LECTURE_STUDIO_SOURCE, encoding="utf-8").read()


# ---------------------------------------------------------------------------
# 1. core.tts.generate_tts_audio — language 引数
# ---------------------------------------------------------------------------


class TestGenerateTtsAudioLanguage:
    def _patch_settings(self, monkeypatch, *, provider: str, api_key: str = "", voice: str = "alloy"):
        import types
        import core.tts as tts_module
        fake_settings = types.SimpleNamespace(
            llm_api_key=api_key, llm_provider=provider, lecture_tts_voice=voice,
        )
        monkeypatch.setattr(tts_module, "get_settings", lambda: fake_settings)

    def _register_fake_google_tts(self, monkeypatch, fake_tts_class):
        import types
        if "google" not in sys.modules:
            monkeypatch.setitem(sys.modules, "google", types.ModuleType("google"))
        if "google.cloud" not in sys.modules:
            monkeypatch.setitem(sys.modules, "google.cloud", types.ModuleType("google.cloud"))
        monkeypatch.setitem(sys.modules, "google.cloud.texttospeech", fake_tts_class)

    def test_google_locale_is_en_us_for_english(self, monkeypatch):
        import types
        import core.tts as tts_module

        captured = {}

        class FakeTTSClient:
            def synthesize_speech(self, input, voice, audio_config):
                captured["language_code"] = voice.language_code
                return types.SimpleNamespace(audio_content=b"ok")

        class FakeTextToSpeech:
            TextToSpeechClient = FakeTTSClient

            class SynthesisInput:
                def __init__(self, text):
                    self.text = text

            class VoiceSelectionParams:
                def __init__(self, **kwargs):
                    self.language_code = kwargs.get("language_code")

            class AudioConfig:
                def __init__(self, **kwargs):
                    pass

            class SsmlVoiceGender:
                NEUTRAL = "NEUTRAL"

            class AudioEncoding:
                MP3 = "MP3"

        self._register_fake_google_tts(monkeypatch, FakeTextToSpeech)
        self._patch_settings(monkeypatch, provider="google")

        result = tts_module.generate_tts_audio("hello", language="en")
        assert result == b"ok"
        assert captured["language_code"] == "en-US"

    def test_google_locale_is_ja_jp_by_default(self, monkeypatch):
        import types
        import core.tts as tts_module

        captured = {}

        class FakeTTSClient:
            def synthesize_speech(self, input, voice, audio_config):
                captured["language_code"] = voice.language_code
                return types.SimpleNamespace(audio_content=b"ok")

        class FakeTextToSpeech:
            TextToSpeechClient = FakeTTSClient

            class SynthesisInput:
                def __init__(self, text):
                    self.text = text

            class VoiceSelectionParams:
                def __init__(self, **kwargs):
                    self.language_code = kwargs.get("language_code")

            class AudioConfig:
                def __init__(self, **kwargs):
                    pass

            class SsmlVoiceGender:
                NEUTRAL = "NEUTRAL"

            class AudioEncoding:
                MP3 = "MP3"

        self._register_fake_google_tts(monkeypatch, FakeTextToSpeech)
        self._patch_settings(monkeypatch, provider="google")

        # language 未指定 = 既定 "ja" (後方互換で ja-JP のまま)
        result = tts_module.generate_tts_audio("こんにちは")
        assert result == b"ok"
        assert captured["language_code"] == "ja-JP"

    def test_google_locale_ja_for_unknown_language_value(self, monkeypatch):
        """未知の language 値は ja-JP にフォールバックする（fail-safe）。"""
        import types
        import core.tts as tts_module

        captured = {}

        class FakeTTSClient:
            def synthesize_speech(self, input, voice, audio_config):
                captured["language_code"] = voice.language_code
                return types.SimpleNamespace(audio_content=b"ok")

        class FakeTextToSpeech:
            TextToSpeechClient = FakeTTSClient

            class SynthesisInput:
                def __init__(self, text):
                    self.text = text

            class VoiceSelectionParams:
                def __init__(self, **kwargs):
                    self.language_code = kwargs.get("language_code")

            class AudioConfig:
                def __init__(self, **kwargs):
                    pass

            class SsmlVoiceGender:
                NEUTRAL = "NEUTRAL"

            class AudioEncoding:
                MP3 = "MP3"

        self._register_fake_google_tts(monkeypatch, FakeTextToSpeech)
        self._patch_settings(monkeypatch, provider="google")

        tts_module.generate_tts_audio("text", language="fr")
        assert captured["language_code"] == "ja-JP"

    def test_openai_voice_comes_from_settings(self, monkeypatch):
        import types
        import core.tts as tts_module

        captured = {}

        class FakeSpeech:
            def create(self, **kwargs):
                captured["voice"] = kwargs.get("voice")
                class FakeResponse:
                    content = b"fake-mp3"
                return FakeResponse()

        class FakeOpenAIClient:
            audio = type("FakeAudio", (), {"speech": FakeSpeech()})()

        fake_openai_mod = types.ModuleType("openai")
        fake_openai_mod.OpenAI = lambda api_key: FakeOpenAIClient()
        monkeypatch.setitem(sys.modules, "openai", fake_openai_mod)

        self._patch_settings(monkeypatch, provider="openai", api_key="sk-test", voice="nova")
        result = tts_module.generate_tts_audio("text", language="en")

        assert result == b"fake-mp3"
        assert captured["voice"] == "nova"

    def test_openai_voice_defaults_to_alloy_when_settings_lack_attribute(self, monkeypatch):
        """settings に lecture_tts_voice 属性が無くても防御的に "alloy" にフォールバックする。"""
        import types
        import core.tts as tts_module

        captured = {}

        class FakeSpeech:
            def create(self, **kwargs):
                captured["voice"] = kwargs.get("voice")
                class FakeResponse:
                    content = b"ok"
                return FakeResponse()

        class FakeOpenAIClient:
            audio = type("FakeAudio", (), {"speech": FakeSpeech()})()

        fake_openai_mod = types.ModuleType("openai")
        fake_openai_mod.OpenAI = lambda api_key: FakeOpenAIClient()
        monkeypatch.setitem(sys.modules, "openai", fake_openai_mod)

        # lecture_tts_voice を持たない SimpleNamespace（既存テストのモックパターン）
        fake_settings = types.SimpleNamespace(llm_api_key="sk-test", llm_provider="openai")
        monkeypatch.setattr(tts_module, "get_settings", lambda: fake_settings)

        tts_module.generate_tts_audio("text")
        assert captured["voice"] == "alloy"


# ---------------------------------------------------------------------------
# 2. core.lecture — 言語指示のプロンプト注入
# ---------------------------------------------------------------------------


class TestLanguageInstruction:
    def test_ja_instruction_mentions_japanese(self):
        from core.lecture import _language_instruction

        assert "日本語" in _language_instruction("ja")

    def test_en_instruction_mentions_english(self):
        from core.lecture import _language_instruction

        assert "English" in _language_instruction("en")

    def test_unknown_language_falls_back_to_japanese_instruction(self):
        from core.lecture import _language_instruction

        assert "日本語" in _language_instruction("fr")

    def test_instruction_preserves_display_text_language(self):
        from core.lecture import _language_instruction

        for lang in ("ja", "en"):
            assert "display_text" in _language_instruction(lang)


class TestGenerateSpokenTextLanguagePrompt:
    @patch("core.lecture.generate_text")
    def test_prompt_contains_english_instruction(self, mock_gen):
        from core.lecture import generate_spoken_text_and_formulas

        mock_gen.return_value = json.dumps(
            {"display_text": "x", "spoken_text": "y", "formulas": []},
        )
        generate_spoken_text_and_formulas("some source text", language="en")

        sent_prompt = mock_gen.call_args.kwargs["messages"][0]["content"]
        assert "English" in sent_prompt

    @patch("core.lecture.generate_text")
    def test_prompt_defaults_to_japanese_instruction(self, mock_gen):
        from core.lecture import generate_spoken_text_and_formulas

        mock_gen.return_value = json.dumps(
            {"display_text": "x", "spoken_text": "y", "formulas": []},
        )
        generate_spoken_text_and_formulas("some source text")

        sent_prompt = mock_gen.call_args.kwargs["messages"][0]["content"]
        assert "日本語で書くこと" in sent_prompt


# ---------------------------------------------------------------------------
# 3. schemas — Literal["ja","en"] バリデーション
# ---------------------------------------------------------------------------


class TestLanguageSchemaValidation:
    def test_lecture_studio_settings_default_language_is_ja(self):
        from schemas import LectureStudioSettings

        assert LectureStudioSettings().lecture_language == "ja"

    def test_lecture_studio_settings_accepts_en(self):
        from schemas import LectureStudioSettings

        assert LectureStudioSettings(lecture_language="en").lecture_language == "en"

    def test_lecture_studio_settings_rejects_invalid_language(self):
        from schemas import LectureStudioSettings

        with pytest.raises(ValidationError):
            LectureStudioSettings(lecture_language="fr")

    def test_script_generate_request_language_optional_and_validated(self):
        from schemas import LectureScriptGenerateRequest

        assert LectureScriptGenerateRequest().language is None
        assert LectureScriptGenerateRequest(language="en").language == "en"
        with pytest.raises(ValidationError):
            LectureScriptGenerateRequest(language="fr")

    def test_audio_generate_request_language_optional_and_validated(self):
        from schemas import LectureAudioGenerateRequest

        assert LectureAudioGenerateRequest().language is None
        assert LectureAudioGenerateRequest(language="ja").language == "ja"
        with pytest.raises(ValidationError):
            LectureAudioGenerateRequest(language="de")


# ---------------------------------------------------------------------------
# 4. lecture_studio.py — settings GET/PUT roundtrip
# ---------------------------------------------------------------------------


class TestLectureStudioSettingsLanguageEndpoint:
    @patch("api.routes.lecture_studio._pg_session")
    @patch("api.routes.lecture_studio.get_viewable_course_data")
    def test_get_settings_returns_stored_language(self, mock_course, mock_pg):
        from api.routes.lecture_studio import get_lecture_studio_settings

        mock_course.return_value = {"lecture_studio_settings": {"lecture_language": "en"}}
        result = get_lecture_studio_settings("course-1", {"id": "u1", "role": "TEACHER"})
        assert result.lecture_language == "en"

    @patch("api.routes.lecture_studio._pg_session")
    @patch("api.routes.lecture_studio.get_viewable_course_data")
    def test_get_settings_clamps_invalid_stored_value_to_ja(self, mock_course, mock_pg):
        """レガシーデータで不正値が入っていても 500 にせず ja にフォールバックする。"""
        from api.routes.lecture_studio import get_lecture_studio_settings

        mock_course.return_value = {"lecture_studio_settings": {"lecture_language": "fr"}}
        result = get_lecture_studio_settings("course-1", {"id": "u1", "role": "TEACHER"})
        assert result.lecture_language == "ja"

    @patch("api.routes.lecture_studio._pg_session")
    @patch("api.routes.lecture_studio.get_editable_course_data")
    def test_put_settings_persists_language_and_marks_regeneration(self, mock_course, mock_pg):
        from api.routes.lecture_studio import update_lecture_studio_settings
        from schemas import LectureStudioSettings

        mock_course.return_value = {"lecture_studio_settings": {"lecture_language": "ja"}}
        mock_session = MagicMock()
        mock_pg.return_value = mock_session

        body = LectureStudioSettings(lecture_language="en")
        result = update_lecture_studio_settings("course-1", body, {"id": "u1", "role": "TEACHER"})

        assert result.lecture_language == "en"
        saved_json = mock_session.execute.call_args.args[1]["data"]
        saved = json.loads(saved_json)
        assert saved["lecture_studio_settings"]["lecture_language"] == "en"
        assert saved["lecture_studio_settings"]["scripts_need_regeneration"] is True

    @patch("api.routes.lecture_studio._pg_session")
    @patch("api.routes.lecture_studio.get_editable_course_data")
    def test_put_settings_same_language_does_not_force_regeneration(self, mock_course, mock_pg):
        from api.routes.lecture_studio import update_lecture_studio_settings
        from schemas import LectureStudioSettings

        mock_course.return_value = {"lecture_studio_settings": {"lecture_language": "ja"}}
        mock_session = MagicMock()
        mock_pg.return_value = mock_session

        body = LectureStudioSettings(lecture_language="ja")
        update_lecture_studio_settings("course-1", body, {"id": "u1", "role": "TEACHER"})

        saved_json = mock_session.execute.call_args.args[1]["data"]
        saved = json.loads(saved_json)
        assert saved["lecture_studio_settings"]["scripts_need_regeneration"] is False


# ---------------------------------------------------------------------------
# 5. 原稿生成: spoken_language の永続化
# ---------------------------------------------------------------------------


_WORKER_CHUNK_ID = _CHUNK_ID


class TestBatchGenerateWorkerLanguage:
    @patch("api.routes.lecture_studio.time.sleep", return_value=None)
    @patch("api.routes.lecture_studio.generate_spoken_text_and_formulas")
    @patch("api.routes.lecture_studio._pg_session")
    @patch("api.routes.lecture_studio.update_background_task")
    @patch("api.routes.lecture_studio.create_background_task")
    def test_worker_uses_course_language_and_persists_spoken_language(
        self, mock_create, mock_update, mock_pg, mock_gen, mock_sleep,
    ):
        from api.routes.lecture_studio import _batch_generate_worker

        mock_gen.return_value = {"display_text": "d", "spoken_text": "s", "formulas": []}
        mock_session = MagicMock()
        mock_pg.return_value = mock_session

        chunks = [{"id": _WORKER_CHUNK_ID, "chunk_index": 0, "text": "t", "spoken_text": ""}]
        course_data = {"lecture_studio_settings": {"lecture_language": "en"}}

        _batch_generate_worker("task-1", "course-1", chunks, True, course_data)

        assert mock_gen.call_args.kwargs["language"] == "en"
        update_calls = [
            c for c in mock_session.execute.call_args_list
            if "UPDATE chunks" in str(c.args[0])
        ]
        assert len(update_calls) == 1
        assert update_calls[0].args[1]["spoken_language"] == "en"

    @patch("api.routes.lecture_studio.time.sleep", return_value=None)
    @patch("api.routes.lecture_studio.generate_spoken_text_and_formulas")
    @patch("api.routes.lecture_studio._pg_session")
    @patch("api.routes.lecture_studio.update_background_task")
    @patch("api.routes.lecture_studio.create_background_task")
    def test_explicit_language_overrides_course_setting(
        self, mock_create, mock_update, mock_pg, mock_gen, mock_sleep,
    ):
        from api.routes.lecture_studio import _batch_generate_worker

        mock_gen.return_value = {"display_text": "d", "spoken_text": "s", "formulas": []}
        mock_pg.return_value = MagicMock()

        chunks = [{"id": _WORKER_CHUNK_ID, "chunk_index": 0, "text": "t", "spoken_text": ""}]
        course_data = {"lecture_studio_settings": {"lecture_language": "ja"}}

        _batch_generate_worker(
            "task-1", "course-1", chunks, True, course_data, language="en",
        )

        assert mock_gen.call_args.kwargs["language"] == "en"

    @patch("api.routes.lecture_studio.time.sleep", return_value=None)
    @patch("api.routes.lecture_studio._get_course_chunks", return_value=[])
    @patch("api.routes.lecture_studio.threading.Thread")
    @patch("api.routes.lecture_studio.generate_spoken_text_and_formulas")
    @patch("api.routes.lecture_studio._pg_session")
    @patch("api.routes.lecture_studio.update_background_task")
    @patch("api.routes.lecture_studio.create_background_task")
    def test_auto_audio_chain_is_active_and_passes_language(
        self, mock_create, mock_update, mock_pg, mock_gen, mock_thread, mock_get_chunks, mock_sleep,
    ):
        """Issue #139 の auto_audio 連鎖: if False で無効化されていたが Phase 4 で再実装した。"""
        from api.routes.lecture_studio import _batch_generate_worker

        mock_gen.return_value = {"display_text": "d", "spoken_text": "s", "formulas": []}
        mock_pg.return_value = MagicMock()
        mock_thread_instance = MagicMock()
        mock_thread.return_value = mock_thread_instance

        chunks = [{"id": _WORKER_CHUNK_ID, "chunk_index": 0, "text": "t", "spoken_text": ""}]
        course_data = {"lecture_studio_settings": {"lecture_language": "en"}}

        _batch_generate_worker(
            "task-1", "course-1", chunks, True, course_data,
            auto_audio=True, user_id="teacher-1",
        )

        mock_thread_instance.start.assert_called_once()
        assert mock_thread.call_args.kwargs["target"].__name__ == "_batch_audio_worker"
        chain_args = mock_thread.call_args.kwargs["args"]
        assert chain_args[3] == "en"  # course_language が引き継がれる

        completed_calls = [c for c in mock_update.call_args_list if c.args[1] == "completed"]
        assert "next_task_id" in completed_calls[-1].kwargs["result_data"]

    def test_source_no_longer_disables_auto_audio(self):
        source = _read_lecture_studio_source()
        assert "#if auto_audio:" not in source
        assert "\n    if False:\n" not in source


# ---------------------------------------------------------------------------
# 6. 音声生成: 同一言語スキップ維持 / 別言語チェーン
# ---------------------------------------------------------------------------


class TestBatchGenerateAudioLanguageChain:
    @patch("api.routes.lecture_studio.threading.Thread")
    @patch("api.routes.lecture_studio.create_background_task")
    @patch("api.routes.lecture_studio._get_course_chunks")
    @patch("api.routes.lecture_studio.get_editable_course_data")
    def test_same_language_uses_simple_audio_worker(
        self, mock_course, mock_chunks, mock_create, mock_thread,
    ):
        from api.routes.lecture_studio import batch_generate_audio
        from schemas import LectureAudioGenerateRequest

        mock_course.return_value = {"lecture_studio_settings": {"lecture_language": "ja"}}
        mock_chunks.return_value = [
            {"id": "c1", "spoken_language": "ja"},
            {"id": "c2", "spoken_language": "ja"},
        ]
        mock_thread_instance = MagicMock()
        mock_thread.return_value = mock_thread_instance

        resp = batch_generate_audio(
            "course-1", LectureAudioGenerateRequest(language="ja"), {"id": "u1", "role": "TEACHER"},
        )

        assert mock_thread.call_args.kwargs["target"].__name__ == "_batch_audio_worker"
        mock_thread_instance.start.assert_called_once()
        assert resp.total_chunks == 2

    @patch("api.routes.lecture_studio.threading.Thread")
    @patch("api.routes.lecture_studio.create_background_task")
    @patch("api.routes.lecture_studio._get_course_chunks")
    @patch("api.routes.lecture_studio.get_editable_course_data")
    def test_no_body_falls_back_to_course_language(
        self, mock_course, mock_chunks, mock_create, mock_thread,
    ):
        """body=None（従来の呼び出し）でも壊れず、コース設定言語で従来どおり動く。"""
        from api.routes.lecture_studio import batch_generate_audio

        mock_course.return_value = {"lecture_studio_settings": {"lecture_language": "ja"}}
        mock_chunks.return_value = [{"id": "c1", "spoken_language": "ja"}]
        mock_thread.return_value = MagicMock()

        resp = batch_generate_audio("course-1", None, {"id": "u1", "role": "TEACHER"})

        assert mock_thread.call_args.kwargs["target"].__name__ == "_batch_audio_worker"
        assert resp.total_chunks == 1

    @patch("api.routes.lecture_studio.threading.Thread")
    @patch("api.routes.lecture_studio.create_background_task")
    @patch("api.routes.lecture_studio._save_lecture_language")
    @patch("api.routes.lecture_studio._get_course_chunks")
    @patch("api.routes.lecture_studio.get_editable_course_data")
    def test_language_switch_updates_setting_and_uses_chain_worker(
        self, mock_course, mock_chunks, mock_save_lang, mock_create, mock_thread,
    ):
        from api.routes.lecture_studio import batch_generate_audio
        from schemas import LectureAudioGenerateRequest

        course_data = {"lecture_studio_settings": {"lecture_language": "ja"}}
        mock_course.return_value = course_data
        mock_chunks.return_value = [{"id": "c1", "spoken_language": "ja"}]
        mock_save_lang.return_value = {"lecture_studio_settings": {"lecture_language": "en"}}
        mock_thread.return_value = MagicMock()

        batch_generate_audio(
            "course-1", LectureAudioGenerateRequest(language="en"), {"id": "u1", "role": "TEACHER"},
        )

        mock_save_lang.assert_called_once_with("course-1", course_data, "en")
        assert mock_thread.call_args.kwargs["target"].__name__ == "_batch_generate_and_audio_worker"

    @patch("api.routes.lecture_studio.threading.Thread")
    @patch("api.routes.lecture_studio.create_background_task")
    @patch("api.routes.lecture_studio._get_course_chunks")
    @patch("api.routes.lecture_studio.get_editable_course_data")
    def test_mismatched_chunk_language_triggers_chain_even_if_setting_matches(
        self, mock_course, mock_chunks, mock_create, mock_thread,
    ):
        """設定言語と要求言語が同じでも、一部チャンクの spoken_language が食い違えば
        チェーン実行する（§3-3: "または spoken_language 不一致チャンクあり"）。"""
        from api.routes.lecture_studio import batch_generate_audio

        mock_course.return_value = {"lecture_studio_settings": {"lecture_language": "ja"}}
        mock_chunks.return_value = [
            {"id": "c1", "spoken_language": "ja"},
            {"id": "c2", "spoken_language": "en"},
        ]
        mock_thread.return_value = MagicMock()

        batch_generate_audio("course-1", None, {"id": "u1", "role": "TEACHER"})

        assert mock_thread.call_args.kwargs["target"].__name__ == "_batch_generate_and_audio_worker"


class TestBatchGenerateAndAudioChainWorker:
    @patch("api.routes.lecture_studio._batch_audio_worker")
    @patch("api.routes.lecture_studio.time.sleep", return_value=None)
    @patch("api.routes.lecture_studio.generate_spoken_text_and_formulas")
    @patch("api.routes.lecture_studio._pg_session")
    @patch("api.routes.lecture_studio.update_background_task")
    @patch("api.routes.lecture_studio._get_course_chunks")
    def test_script_phase_then_audio_phase(
        self, mock_get_chunks, mock_update, mock_pg, mock_gen, mock_sleep, mock_audio_worker,
    ):
        from api.routes.lecture_studio import _batch_generate_and_audio_worker

        chunks = [{"id": _WORKER_CHUNK_ID, "chunk_index": 0, "text": "t"}]
        mock_get_chunks.return_value = chunks
        mock_gen.return_value = {"display_text": "d", "spoken_text": "s", "formulas": []}
        mock_session = MagicMock()
        mock_pg.return_value = mock_session

        course_data = {"lecture_studio_settings": {"lecture_language": "ja"}}
        _batch_generate_and_audio_worker("task-1", "course-1", course_data, "en")

        # スクリプトフェーズ: 指定言語で原稿を再生成する
        assert mock_gen.call_args.kwargs["language"] == "en"

        update_calls = [
            c for c in mock_session.execute.call_args_list if "UPDATE chunks" in str(c.args[0])
        ]
        assert len(update_calls) == 1
        assert update_calls[0].args[1]["spoken_language"] == "en"

        # 言語切替のため旧音声キャッシュ（全スライド）を無効化する
        delete_calls = [
            c for c in mock_session.execute.call_args_list
            if "DELETE FROM lecture_audio_cache" in str(c.args[0])
        ]
        assert len(delete_calls) == 1

        # result_data.phase が "script" で報告されること
        script_phase_calls = [
            c for c in mock_update.call_args_list
            if (c.kwargs.get("result_data") or {}).get("phase") == "script"
        ]
        assert script_phase_calls

        # 完了後に音声フェーズへ連鎖する
        mock_audio_worker.assert_called_once()
        args = mock_audio_worker.call_args.args
        assert args[0] == "task-1"
        assert args[1] == "course-1"
        assert args[3] == "en"

    @patch("api.routes.lecture_studio._batch_audio_worker")
    @patch("api.routes.lecture_studio.time.sleep", return_value=None)
    @patch("api.routes.lecture_studio.generate_spoken_text_and_formulas")
    @patch("api.routes.lecture_studio._pg_session")
    @patch("api.routes.lecture_studio.update_background_task")
    @patch("api.routes.lecture_studio._get_course_chunks")
    def test_script_phase_failure_marks_task_failed_and_skips_audio(
        self, mock_get_chunks, mock_update, mock_pg, mock_gen, mock_sleep, mock_audio_worker,
    ):
        from api.routes.lecture_studio import _batch_generate_and_audio_worker

        mock_get_chunks.return_value = [{"id": _WORKER_CHUNK_ID, "chunk_index": 0, "text": "t"}]
        mock_gen.side_effect = RuntimeError("boom")
        mock_pg.return_value = MagicMock()

        _batch_generate_and_audio_worker(
            "task-1", "course-1", {"lecture_studio_settings": {}}, "en",
        )

        failed_calls = [c for c in mock_update.call_args_list if c.args[1] == "failed"]
        assert failed_calls
        mock_audio_worker.assert_not_called()


class TestBatchAudioWorkerPhaseLabel:
    """_batch_audio_worker が result_data.phase="audio" を出すこと（既存 generated/errors
    などのキー・挙動は変更しない）。"""

    @patch("api.routes.lecture_studio.time.sleep", return_value=None)
    @patch("api.routes.lecture_studio.generate_tts_audio")
    @patch("api.routes.lecture_studio._pg_session")
    @patch("api.routes.lecture_studio.update_background_task")
    @patch("api.routes.lecture_studio.create_background_task")
    def test_phase_audio_present_and_language_passed_to_tts(
        self, mock_create, mock_update, mock_pg, mock_tts, mock_sleep,
    ):
        from api.routes.lecture_studio import _batch_audio_worker

        mock_session = MagicMock()
        mock_session.execute.return_value.fetchone.return_value = None
        mock_pg.return_value = mock_session
        mock_tts.return_value = b"AUDIO"

        chunks = [{
            "id": _WORKER_CHUNK_ID,
            "display_text": "Slide A",
            "text": "Slide A",
            "spoken_text": "Speak A",
            "formulas": [],
            # spoken_language 省略 -> course_language フォールバックを使う
        }]

        _batch_audio_worker("task-1", "course-1", chunks, "en")

        mock_tts.assert_any_call("Speak A", language="en")

        phase_calls = [
            c for c in mock_update.call_args_list
            if (c.kwargs.get("result_data") or {}).get("phase") == "audio"
        ]
        assert phase_calls


# ---------------------------------------------------------------------------
# 7. audio-status: spoken_language 不一致の回帰確認 (Phase 1 実装)
# ---------------------------------------------------------------------------


class TestAudioStatusLanguageRegression:
    @patch("api.routes.lecture.get_user_mastered_concepts", return_value=set())
    @patch("api.routes.lecture._pg_session")
    @patch("api.routes.lecture.get_course_data")
    def test_stale_language_still_excluded_after_phase4_changes(self, mock_course, mock_session, mock_mastery):
        from api.routes.lecture import get_topic_audio_status

        mock_course.return_value = {
            "topics": [{"id": "t1"}],
            "sources": [{"material_id": "m1"}],
            "lecture_studio_settings": {"lecture_language": "en"},
        }
        chunk_rows = [("c0", "text", "text", "spoken", [], "ja")]  # 原稿は日本語のまま
        audio_rows = [("c0", 0)]  # 音声キャッシュはあるが言語不一致 (ja != en)

        chunk_result = MagicMock()
        chunk_result.fetchall.return_value = chunk_rows
        audio_result = MagicMock()
        audio_result.fetchall.return_value = audio_rows

        session = MagicMock()
        session.execute.side_effect = [chunk_result, audio_result]
        mock_session.return_value = session

        result = get_topic_audio_status("c1", "t1", {"id": "u1"})

        assert result["language"] == "en"
        assert result["stale_language"] is True
        assert result["has_audio"] is False
        assert result["ready_slides"] == 0


# ---------------------------------------------------------------------------
# 8. LectureScriptChunkOut の新フィールド + get_course_scripts の1クエリ集計
# ---------------------------------------------------------------------------


class TestLectureScriptChunkOutLanguageFields:
    def test_defaults(self):
        from schemas import LectureScriptChunkOut

        chunk = LectureScriptChunkOut(chunk_id="c1", chunk_index=0, text="t")
        assert chunk.spoken_language is None
        assert chunk.slide_count == 1
        assert chunk.slide_mismatch is False
        assert chunk.audio_ready_slides == 0

    def test_with_values(self):
        from schemas import LectureScriptChunkOut

        chunk = LectureScriptChunkOut(
            chunk_id="c1", chunk_index=0, text="t",
            spoken_language="en", slide_count=3, slide_mismatch=True, audio_ready_slides=2,
        )
        assert chunk.spoken_language == "en"
        assert chunk.slide_count == 3
        assert chunk.slide_mismatch is True
        assert chunk.audio_ready_slides == 2


class TestLoadChunkSlideAudioMap:
    @patch("api.routes.lecture_studio._pg_session")
    def test_groups_slide_indices_by_chunk(self, mock_pg):
        from api.routes.lecture_studio import _load_chunk_slide_audio_map

        mock_session = MagicMock()
        mock_session.execute.return_value.fetchall.return_value = [
            ("c1", 0), ("c1", 1), ("c2", 0),
        ]
        mock_pg.return_value = mock_session

        result = _load_chunk_slide_audio_map(["c1", "c2"])
        assert result == {"c1": {0, 1}, "c2": {0}}

    def test_empty_input_returns_empty_without_query(self):
        from api.routes.lecture_studio import _load_chunk_slide_audio_map

        assert _load_chunk_slide_audio_map([]) == {}


class TestGetCourseScriptsSlideFields:
    @patch("api.routes.lecture_studio._chunk_status", return_value="generated")
    @patch("api.routes.lecture_studio._load_chunk_slide_audio_map")
    @patch("api.routes.lecture_studio._get_course_chunks")
    @patch("api.routes.lecture_studio.get_viewable_course_data")
    def test_slide_fields_computed_with_single_audio_query(
        self, mock_course, mock_chunks, mock_audio_map, mock_status,
    ):
        from api.routes.lecture_studio import get_course_scripts

        mock_course.return_value = {"sources": [{"material_id": "m1"}]}
        mock_chunks.return_value = [
            {
                "id": "c1", "chunk_index": 0, "text": "A", "display_text": "A\n===\nB",
                "spoken_text": "sa\n===\nsb", "formulas": [], "spoken_language": "en",
            },
        ]
        mock_audio_map.return_value = {"c1": {1}}

        result = get_course_scripts("course-1", {"id": "u1", "role": "TEACHER"})

        assert len(result) == 1
        chunk_out = result[0]
        assert chunk_out.spoken_language == "en"
        assert chunk_out.slide_count == 2
        assert chunk_out.slide_mismatch is False
        assert chunk_out.audio_ready_slides == 1
        mock_audio_map.assert_called_once_with(["c1"])

    @patch("api.routes.lecture_studio._chunk_status", return_value="ungenerated")
    @patch("api.routes.lecture_studio._load_chunk_slide_audio_map", return_value={})
    @patch("api.routes.lecture_studio._get_course_chunks")
    @patch("api.routes.lecture_studio.get_viewable_course_data")
    def test_slide_mismatch_flagged(self, mock_course, mock_chunks, mock_audio_map, mock_status):
        from api.routes.lecture_studio import get_course_scripts

        mock_course.return_value = {"sources": [{"material_id": "m1"}]}
        mock_chunks.return_value = [
            {
                "id": "c1", "chunk_index": 0, "text": "A",
                "display_text": "A\n===\nB\n===\nC",  # 3 セグメント
                "spoken_text": "sa\n===\nsb",  # 2 セグメント -> mismatch
                "formulas": [], "spoken_language": "ja",
            },
        ]

        result = get_course_scripts("course-1", {"id": "u1", "role": "TEACHER"})
        assert result[0].slide_count == 1
        assert result[0].slide_mismatch is True


# ---------------------------------------------------------------------------
# 9. 試聴エンドポイント (GET /api/admin/chunks/{chunk_id}/lecture-audio)
# ---------------------------------------------------------------------------


class TestPreviewLectureAudioWiring:
    def test_route_declared_with_expected_path_and_query_defaults(self):
        source = _read_lecture_studio_source()
        assert '"/chunks/{chunk_id}/lecture-audio"' in source
        body = source.split("def preview_lecture_audio")[1].split("\ndef ")[0]
        assert "slide_index: int = 0" in body
        assert 'voice: str = "alloy"' in body
        assert "Depends(_require_teacher)" in body

    def test_require_teacher_rejects_student(self):
        from fastapi import HTTPException
        from dependencies import _require_teacher

        with pytest.raises(HTTPException) as exc:
            _require_teacher({"id": "u1", "role": "STUDENT"})
        assert exc.value.status_code == 403

    def test_require_teacher_allows_teacher_and_admin(self):
        from dependencies import _require_teacher

        teacher = {"id": "u1", "role": "TEACHER"}
        admin = {"id": "u2", "role": "SYSTEM_ADMIN"}
        assert _require_teacher(teacher) == teacher
        assert _require_teacher(admin) == admin


class TestPreviewLectureAudioBehavior:
    def test_non_uuid_chunk_returns_404(self):
        from fastapi import HTTPException
        from api.routes.lecture_studio import preview_lecture_audio

        with pytest.raises(HTTPException) as exc:
            preview_lecture_audio("topic:abc", 0, "alloy", {"id": "u1", "role": "TEACHER"})
        assert exc.value.status_code == 404

    @patch("api.routes.lecture_studio._pg_session")
    def test_no_cache_returns_404(self, mock_pg):
        from fastapi import HTTPException
        from api.routes.lecture_studio import preview_lecture_audio

        mock_session = MagicMock()
        mock_session.execute.return_value.fetchone.return_value = None
        mock_pg.return_value = mock_session

        with pytest.raises(HTTPException) as exc:
            preview_lecture_audio(_CHUNK_ID, 0, "alloy", {"id": "u1", "role": "TEACHER"})
        assert exc.value.status_code == 404

    @patch("api.routes.lecture_studio._pg_session")
    def test_cache_hit_returns_expected_response_shape(self, mock_pg):
        from api.routes.lecture_studio import preview_lecture_audio

        mock_session = MagicMock()
        mock_session.execute.return_value.fetchone.return_value = (b"FAKE_AUDIO", 4321, [])
        mock_pg.return_value = mock_session

        resp = preview_lecture_audio(_CHUNK_ID, 1, "alloy", {"id": "u1", "role": "TEACHER"})

        assert resp.chunk_id == _CHUNK_ID
        assert resp.duration_ms == 4321
        assert resp.content_type == "audio/mp3"
        assert base64.b64decode(resp.audio_base64) == b"FAKE_AUDIO"

        # キャッシュキー (chunk_id, slide_index, voice) で引いていること・生成しないこと
        query_call = mock_session.execute.call_args
        assert query_call.args[1] == {"cid": _CHUNK_ID, "voice": "alloy", "slide_index": 1}
        assert "SELECT" in str(query_call.args[0])
        assert "generate_tts_audio" not in str(query_call.args[0])

    @patch("api.routes.lecture_studio._pg_session")
    def test_does_not_generate_audio(self, mock_pg):
        """生成しない方針: generate_tts_audio が一切呼ばれないこと。"""
        from api.routes.lecture_studio import preview_lecture_audio

        mock_session = MagicMock()
        mock_session.execute.return_value.fetchone.return_value = None
        mock_pg.return_value = mock_session

        with patch("api.routes.lecture_studio.generate_tts_audio") as mock_tts:
            from fastapi import HTTPException
            with pytest.raises(HTTPException):
                preview_lecture_audio(_CHUNK_ID, 0, "alloy", {"id": "u1", "role": "TEACHER"})
            mock_tts.assert_not_called()

    @patch("api.routes.lecture_studio._pg_session")
    def test_db_error_is_treated_as_not_generated_not_500(self, mock_pg):
        """DB エラーでも 500 で詳細を漏らさず 404 (未生成扱い) にする（fail-safe）。"""
        from fastapi import HTTPException
        from api.routes.lecture_studio import preview_lecture_audio

        mock_session = MagicMock()
        mock_session.execute.side_effect = RuntimeError("connection lost")
        mock_pg.return_value = mock_session

        with pytest.raises(HTTPException) as exc:
            preview_lecture_audio(_CHUNK_ID, 0, "alloy", {"id": "u1", "role": "TEACHER"})
        assert exc.value.status_code == 404
        mock_session.close.assert_called_once()


# ---------------------------------------------------------------------------
# 10. 異常系: DB エラー時のロールバック
# ---------------------------------------------------------------------------


class TestSaveLectureStudioSettingsErrorHandling:
    @patch("api.routes.lecture_studio._pg_session")
    def test_db_error_rolls_back_and_reraises(self, mock_pg):
        from api.routes.lecture_studio import _save_lecture_studio_settings

        mock_session = MagicMock()
        mock_session.execute.side_effect = RuntimeError("db down")
        mock_pg.return_value = mock_session

        with pytest.raises(RuntimeError):
            _save_lecture_studio_settings("course-1", {}, {"lecture_language": "en"})

        mock_session.rollback.assert_called_once()
        mock_session.close.assert_called_once()


class TestBatchGenerateAndAudioWorkerErrorHandling:
    @patch("api.routes.lecture_studio._batch_audio_worker")
    @patch("api.routes.lecture_studio.time.sleep", return_value=None)
    @patch("api.routes.lecture_studio.generate_spoken_text_and_formulas")
    @patch("api.routes.lecture_studio._pg_session")
    @patch("api.routes.lecture_studio.update_background_task")
    @patch("api.routes.lecture_studio._get_course_chunks")
    def test_script_phase_db_error_rolls_back_session(
        self, mock_get_chunks, mock_update, mock_pg, mock_gen, mock_sleep, mock_audio_worker,
    ):
        from api.routes.lecture_studio import _batch_generate_and_audio_worker

        mock_get_chunks.return_value = [{"id": _WORKER_CHUNK_ID, "chunk_index": 0, "text": "t"}]
        mock_gen.return_value = {"display_text": "d", "spoken_text": "s", "formulas": []}
        mock_session = MagicMock()
        mock_session.execute.side_effect = RuntimeError("db down")
        mock_pg.return_value = mock_session

        _batch_generate_and_audio_worker(
            "task-1", "course-1", {"lecture_studio_settings": {}}, "en",
        )

        mock_session.rollback.assert_called_once()
        failed_calls = [c for c in mock_update.call_args_list if c.args[1] == "failed"]
        assert failed_calls
        mock_audio_worker.assert_not_called()


class TestSchemaSerializationIncludesNewFields:
    def test_lecture_studio_settings_model_dump_includes_language(self):
        from schemas import LectureStudioSettings

        dumped = LectureStudioSettings(lecture_language="en").model_dump()
        assert dumped["lecture_language"] == "en"

    def test_lecture_script_chunk_out_model_dump_includes_slide_fields(self):
        from schemas import LectureScriptChunkOut

        dumped = LectureScriptChunkOut(
            chunk_id="c1", chunk_index=0, text="t",
            spoken_language="en", slide_count=2, slide_mismatch=True, audio_ready_slides=1,
        ).model_dump()
        assert dumped["spoken_language"] == "en"
        assert dumped["slide_count"] == 2
        assert dumped["slide_mismatch"] is True
        assert dumped["audio_ready_slides"] == 1

    def test_lecture_audio_generate_request_model_dump(self):
        from schemas import LectureAudioGenerateRequest

        assert LectureAudioGenerateRequest(language="en").model_dump() == {"language": "en"}
