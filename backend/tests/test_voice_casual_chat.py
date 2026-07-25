"""ハンズフリー音声会話（カジュアル対話モード）のテスト。

- casual モード: 雑談拒否・前提ゲート・誤解検出のバイパスと、根拠の一線
  （RAG・tier・guard 注入）の維持をソース静的検証で確認
- strip_text_for_speech / transcribe_audio: 純関数・プロバイダガードのユニットテスト
- 音声 API・フロント（🤖ボタン・無音検知ループ・教材表示）の存在検証
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

LEARNING = ROOT / "backend" / "api" / "routes" / "learning.py"
CONFIG = ROOT / "backend" / "core" / "config.py"
APP_JS = ROOT / "frontend" / "public" / "js" / "app.js"
INDEX_HTML = ROOT / "frontend" / "public" / "index.html"
STYLES = ROOT / "frontend" / "public" / "css" / "styles.css"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestCasualModeRouting:
    def test_casual_branch_exists(self):
        source = _read(LEARNING)
        assert '_is_casual = (body.intent_mode or "").strip() == "casual"' in source
        assert "_get_casual_teacher_system_prompt" in source

    def test_casual_bypasses_intent_classification(self):
        """CHIT_CHAT 拒否ルートを通らない（雑談を弾かない）。

        discuss モード追加（discuss モード設計書 §6.2）で `_is_discuss` が同じ条件式に
        併記されたが、casual のバイパス自体は不変。
        """
        source = _read(LEARNING)
        assert "None if (_is_casual or _is_discuss or _atlas_ctx) else (" in source

    def test_casual_bypasses_prerequisite_gate(self):
        source = _read(LEARNING)
        assert "None if (_is_casual or _is_discuss or _atlas_ctx) else check_prerequisites(" in source

    def test_casual_skips_visible_notice_but_keeps_guard(self):
        """OutOfSourceGuard の system 注入は維持し、可視プレフィックスのみ省略する。"""
        source = _read(LEARNING)
        assert "if overall_tier == TIER_OUT_OF_SOURCE and not _is_casual:" in source
        # guard 注入は casual 分岐の外（無条件）にある
        guard_idx = source.find('_system_prompt += "\\n\\n" + out_of_source_guard_instruction()')
        assert guard_idx > 0

    def test_casual_skips_misconception_pressure(self):
        source = _read(LEARNING)
        assert "if not _is_casual and topic_info and any(" in source

    def test_casual_trace_is_recorded_with_flag(self):
        """雑談も痕跡として資産化する（tension プレフィルタの入力になる）。"""
        source = _read(LEARNING)
        assert '"casual": _is_casual' in source

    def test_casual_does_not_enter_detour(self):
        """discuss モード追加（設計 §6.2）でタプルに "discuss" が併記されたが、casual 自体の
        detour 非化は不変。"""
        source = _read(LEARNING)
        assert 'in ("on_path", "casual", "discuss")' in source

    def test_casual_prompt_is_voice_friendly(self):
        source = _read(LEARNING)
        body = source.split("def _get_casual_teacher_system_prompt")[1].split("\ndef ")[0]
        assert "気軽に話せる先生" in body
        assert "2〜4文" in body
        assert "箇条書き" in body      # 禁止の明示
        assert "ACTION_BUTTON" in body  # システム記法の禁止


class TestVoiceEndpoints:
    def test_routes_exist(self):
        source = _read(LEARNING)
        assert '"/voice/transcribe"' in source
        assert '"/voice/speak"' in source

    def test_transcribe_guards(self):
        source = _read(LEARNING)
        body = source.split("async def voice_transcribe_route")[1].split("\ndef ")[0]
        assert "Empty audio" in body
        assert "_VOICE_MAX_AUDIO_BYTES" in body
        assert "503" in body  # 非対応プロバイダ

    def test_speak_strips_before_tts(self):
        source = _read(LEARNING)
        body = source.split("def voice_speak_route")[1].split("\ndef ")[0]
        assert "strip_text_for_speech" in body
        assert "audio_base64" in body


class TestStripTextForSpeech:
    def test_removes_latex_and_markers(self):
        from core.tts import strip_text_for_speech

        raw = (
            "**重要**: エネルギーは $E=mc^2$ です。$$\\int f dx$$\n\n"
            "[出典1] を参照。[ACTION_BUTTON: 質量について聞く] [慣性について詳しく聞く]"
        )
        cleaned = strip_text_for_speech(raw)
        assert "$" not in cleaned
        assert "ACTION_BUTTON" not in cleaned
        assert "出典1" not in cleaned
        assert "詳しく聞く" not in cleaned
        assert "*" not in cleaned
        assert "エネルギーは" in cleaned

    def test_empty_and_length_cap(self):
        from core.tts import strip_text_for_speech

        assert strip_text_for_speech("") == ""
        assert strip_text_for_speech(None) == ""
        assert len(strip_text_for_speech("あ" * 9000)) == 4000


class TestTranscribeAudio:
    def test_rejects_non_openai_provider(self, monkeypatch):
        import core.llm as llm

        settings = MagicMock()
        settings.llm_provider = "gemini"
        monkeypatch.setattr(llm, "get_settings", lambda: settings)
        with pytest.raises(RuntimeError):
            llm.transcribe_audio(b"xx", "a.webm")

    def test_openai_path_returns_stripped_text(self, monkeypatch):
        import core.llm as llm

        settings = MagicMock()
        settings.llm_provider = "openai"
        settings.llm_transcribe_model = "whisper-1"
        monkeypatch.setattr(llm, "get_settings", lambda: settings)
        client = MagicMock()
        client.audio.transcriptions.create.return_value = MagicMock(text="  こんにちは  ")
        monkeypatch.setattr(llm, "_get_openai_client", lambda: client)

        assert llm.transcribe_audio(b"audio-bytes", "speech.webm", language="ja") == "こんにちは"
        kwargs = client.audio.transcriptions.create.call_args.kwargs
        assert kwargs["model"] == "whisper-1"
        assert kwargs["language"] == "ja"  # 言語はハードコードせず引数で渡る

    def test_config_has_transcribe_model(self):
        assert "LLM_TRANSCRIBE_MODEL" in _read(CONFIG)


class TestFrontendVoiceMode:
    def test_robot_button_and_panel_markup(self):
        html = _read(INDEX_HTML)
        assert 'id="voice-mode-btn"' in html
        assert 'id="voice-panel"' in html
        assert 'id="voice-material"' in html
        assert "気軽に話せる先生" in html

    def test_handsfree_loop_wiring(self):
        js = _read(APP_JS)
        # 無音検知による自動送信ループ
        assert "VOICE_SILENCE_MS" in js
        assert "MediaRecorder" in js
        assert "getFloatTimeDomainData" in js
        # 文字起こし → casual 送信 → TTS 再生 → 聞き取り再開
        assert "/learning/voice/transcribe" in js
        assert '{ intent_mode: "casual" }' in js
        assert "/learning/voice/speak" in js
        assert "resumeVoiceListening" in js

    def test_material_shown_from_top_source(self):
        js = _read(APP_JS)
        body = js.split("async function showVoiceSourceMaterial")[1].split("\n  async function ")[0]
        assert "chunk_id" in body
        assert "source-chunk" in body
        assert "renderMaterialChunk" in body

    def test_playback_pauses_listening(self):
        """応答再生中はマイク（区切り検知）を止める — busy フラグで排他。"""
        js = _read(APP_JS)
        assert "voiceState.busy" in js
        vad = js.split("function voiceVadTick()")[1].split("\n  function ")[0]
        assert "voiceState.busy" in js.split("function voiceVadTick()")[1][:200] or "busy" in vad

    def test_styles_exist(self):
        css = _read(STYLES)
        assert ".voice-mode-btn" in css
        assert ".voice-panel" in css
        assert ".voice-material" in css


class TestVoiceErrorFeedback:
    """N33 (vision_ux_gap_survey_2026-07-17 §5-7): サーバーエラー時に無言で
    聞き取りへ戻らない。短い定型文を TTS で返し、TTS 失敗時はパネル表示のみに縮退。
    エラー定型文 TTS はクールダウンで抑え、無限リトライループにしない。
    """

    def _fn(self, js: str, name: str) -> str:
        """app.js の2-spaceインデント関数本体を切り出す（test_learner_ux_static.py と同様式）。"""
        import re

        match = re.search(
            r"\n  (?:async )?function " + re.escape(name) + r"\(.*?\n  \}\n", js, re.S
        )
        assert match, f"{name} が見つかりません"
        return match.group(0)

    def test_transcribe_error_is_distinguished_from_silence(self):
        """transcribe の HTTP エラーは throw（空文字＝無音と区別する）。"""
        js = _read(APP_JS)
        fn = self._fn(js, "transcribeVoiceBlob")
        assert "throw new Error" in fn

    def test_segment_handler_gives_feedback_on_errors(self):
        """transcribe / chat 失敗時に voiceErrorFeedback を呼んでから聞き取り再開。"""
        js = _read(APP_JS)
        fn = self._fn(js, "handleVoiceSegment")
        assert fn.count("voiceErrorFeedback") >= 2  # transcribe 失敗 + chat 失敗
        assert "resumeVoiceListening" in fn

    def test_tts_failure_degrades_to_panel_only(self):
        """回答 TTS 失敗時は再帰 TTS せずパネル表示のみに縮退する。"""
        js = _read(APP_JS)
        fn = self._fn(js, "handleVoiceSegment")
        assert "音声を再生できませんでした" in fn
        speak = self._fn(js, "speakVoiceAnswer")
        assert "return false" in speak
        assert "return true" in speak

    def test_error_tts_has_cooldown_no_retry_loop(self):
        js = _read(APP_JS)
        assert "VOICE_ERROR_TTS_COOLDOWN_MS" in js
        fn = self._fn(js, "voiceErrorFeedback")
        assert "lastErrorTtsAt" in fn
        # フィードバック自身の失敗からさらにフィードバックを呼ばない（再帰なし）
        assert "voiceErrorFeedback" not in fn.replace("function voiceErrorFeedback", "")

    def test_feedback_message_is_short_and_neutral(self):
        js = _read(APP_JS)
        assert "エラーが発生しました。もう一度どうぞ。" in js
