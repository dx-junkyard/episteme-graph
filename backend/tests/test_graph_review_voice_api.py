"""グラフレビュー画面の音声対話 API（管理側 STT / TTS）のテスト。

対象は ``POST /api/admin/deliberation/voice/{transcribe,speak}``
（`backend/api/routes/deliberation.py`）。学習側の
``/api/learning/voice/*``（`routes/learning.py`）は非改変で、本テストも触らない。

DB / LLM への実接続は行わず、route 関数を直接呼んで monkeypatch で分離する
（`test_graph_review_api.py` と同じ流儀）。検査するのは:

- ``_require_teacher`` の fail-closed（STUDENT は 403）
- 入力ガード（空音声 400 / 上限超 413 / 空テキスト 400）
- プロバイダ未対応（transcribe の RuntimeError → 503）・TTS 不能（None → 503）
- CostGate(day-only) 超過で 429、かつ detail に数値を出さない
- U層 feature の帰属（``deliberation:voice_stt`` / ``deliberation:voice_tts``）
- 読み上げ前に ``strip_text_for_speech`` が適用されること
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _path in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import routes.deliberation as delib_routes  # noqa: E402
from core.deliberation import dialogue  # noqa: E402
from core.llm_usage.context import current_usage_context  # noqa: E402
from core.llm_worker.cost_gate import CostGate  # noqa: E402
from dependencies import _require_teacher  # noqa: E402

_TEACHER = {"id": "22222222-2222-2222-2222-222222222222", "role": "TEACHER"}
_ADMIN = {"id": "33333333-3333-3333-3333-333333333333", "role": "SYSTEM_ADMIN"}
_STUDENT = {"id": "44444444-4444-4444-4444-444444444444", "role": "STUDENT"}


class _FakeUpload:
    """UploadFile の最小スタブ（route が使うのは ``read()`` と ``filename`` のみ）。"""

    def __init__(self, data: bytes, filename: str = "audio.webm") -> None:
        self._data = data
        self.filename = filename

    async def read(self) -> bytes:
        return self._data


def _transcribe(audio: _FakeUpload, user: dict = _TEACHER, language: str = "ja"):
    return asyncio.run(
        delib_routes.deliberation_voice_transcribe_route(
            audio=audio, language=language, current_user=user
        )
    )


def _speak(text: str, user: dict = _TEACHER):
    body = delib_routes.DeliberationVoiceSpeakRequest(text=text)
    return delib_routes.deliberation_voice_speak_route(body=body, current_user=user)


@pytest.fixture(autouse=True)
def _fresh_voice_gate(monkeypatch):
    """テスト間で in-memory の日次カウンタを持ち越さない。"""
    monkeypatch.setattr(dialogue, "_voice_cost_gate", CostGate())
    yield


def _set_voice_limit(monkeypatch, limit: int) -> None:
    real = dialogue.get_settings()

    class _Settings:
        def __getattr__(self, name):  # 既定は本物の Settings に委譲
            return getattr(real, name)

        deliberation_voice_max_calls_per_day = limit

    monkeypatch.setattr(dialogue, "get_settings", lambda: _Settings())


# ---------------------------------------------------------------------------
# 権限（_require_teacher の fail-closed）
# ---------------------------------------------------------------------------


class TestPermission:
    def test_student_is_rejected(self):
        with pytest.raises(HTTPException) as exc:
            _require_teacher(current_user=_STUDENT)
        assert exc.value.status_code == 403

    def test_teacher_and_admin_pass(self):
        assert _require_teacher(current_user=_TEACHER) is _TEACHER
        assert _require_teacher(current_user=_ADMIN) is _ADMIN

    def test_voice_routes_depend_on_require_teacher(self):
        src = (BACKEND / "api" / "routes" / "deliberation.py").read_text(encoding="utf-8")
        for fn in ("deliberation_voice_transcribe_route", "deliberation_voice_speak_route"):
            body = src.split(f"def {fn}")[1].split("\n@router")[0]
            assert "Depends(_require_teacher)" in body, fn


# ---------------------------------------------------------------------------
# 文字起こし（STT）
# ---------------------------------------------------------------------------


class TestTranscribe:
    def test_empty_audio_is_400(self):
        with pytest.raises(HTTPException) as exc:
            _transcribe(_FakeUpload(b""))
        assert exc.value.status_code == 400

    def test_oversized_audio_is_413(self):
        oversized = b"x" * (delib_routes._VOICE_MAX_AUDIO_BYTES + 1)
        with pytest.raises(HTTPException) as exc:
            _transcribe(_FakeUpload(oversized))
        assert exc.value.status_code == 413

    def test_unsupported_provider_is_503(self, monkeypatch):
        def _boom(*_a, **_k):
            raise RuntimeError("transcribe_audio is not supported for provider 'google'")

        monkeypatch.setattr(delib_routes, "transcribe_audio", _boom)
        with pytest.raises(HTTPException) as exc:
            _transcribe(_FakeUpload(b"audio-bytes"))
        assert exc.value.status_code == 503

    def test_unexpected_error_is_500(self, monkeypatch):
        def _boom(*_a, **_k):
            raise ValueError("boom")

        monkeypatch.setattr(delib_routes, "transcribe_audio", _boom)
        with pytest.raises(HTTPException) as exc:
            _transcribe(_FakeUpload(b"audio-bytes"))
        assert exc.value.status_code == 500

    def test_success_returns_text_and_passes_language(self, monkeypatch):
        captured = {}

        def _fake(data, filename, *, language="ja", model=None):
            captured.update(data=data, filename=filename, language=language)
            return "こんにちは"

        monkeypatch.setattr(delib_routes, "transcribe_audio", _fake)
        result = _transcribe(_FakeUpload(b"audio-bytes", "chunk.webm"), language="en")
        assert result == {"text": "こんにちは"}
        assert captured["data"] == b"audio-bytes"
        assert captured["filename"] == "chunk.webm"
        assert captured["language"] == "en"

    def test_usage_context_feature_is_deliberation_voice_stt(self, monkeypatch):
        seen = {}

        def _fake(*_a, **_k):
            ctx = current_usage_context()
            seen["feature"] = ctx.feature
            seen["user_id"] = ctx.user_id
            return "text"

        monkeypatch.setattr(delib_routes, "transcribe_audio", _fake)
        _transcribe(_FakeUpload(b"audio-bytes"))
        assert seen["feature"] == "deliberation:voice_stt"
        assert seen["user_id"] == _TEACHER["id"]


# ---------------------------------------------------------------------------
# 読み上げ（TTS）
# ---------------------------------------------------------------------------


class TestSpeak:
    def test_empty_text_is_400(self):
        with pytest.raises(HTTPException) as exc:
            _speak("   ")
        assert exc.value.status_code == 400

    def test_text_that_strips_to_nothing_is_400(self):
        # markdown 記号・出典マーカーだけなら読み上げる文が残らない
        with pytest.raises(HTTPException) as exc:
            _speak("**[出典1]**")
        assert exc.value.status_code == 400

    def test_provider_unavailable_is_503(self, monkeypatch):
        monkeypatch.setattr(delib_routes, "generate_tts_audio", lambda *_a, **_k: None)
        with pytest.raises(HTTPException) as exc:
            _speak("読み上げます")
        assert exc.value.status_code == 503

    def test_unexpected_error_is_500(self, monkeypatch):
        def _boom(*_a, **_k):
            raise ValueError("boom")

        monkeypatch.setattr(delib_routes, "generate_tts_audio", _boom)
        with pytest.raises(HTTPException) as exc:
            _speak("読み上げます")
        assert exc.value.status_code == 500

    def test_success_returns_base64_mp3(self, monkeypatch):
        monkeypatch.setattr(delib_routes, "generate_tts_audio", lambda *_a, **_k: b"\x00\x01mp3")
        result = _speak("読み上げます")
        assert result["format"] == "mp3"
        import base64

        assert base64.b64decode(result["audio_base64"]) == b"\x00\x01mp3"

    def test_strip_text_for_speech_is_applied(self, monkeypatch):
        captured = {}

        def _fake(spoken, *_a, **_k):
            captured["spoken"] = spoken
            return b"mp3"

        monkeypatch.setattr(delib_routes, "generate_tts_audio", _fake)
        _speak(
            "**重要**: エネルギーは $E=mc^2$ です。[出典1] を参照。"
        )
        spoken = captured["spoken"]
        assert "$" not in spoken
        assert "*" not in spoken
        assert "出典1" not in spoken
        assert "エネルギーは" in spoken

    def test_usage_context_feature_is_deliberation_voice_tts(self, monkeypatch):
        seen = {}

        def _fake(*_a, **_k):
            ctx = current_usage_context()
            seen["feature"] = ctx.feature
            seen["user_id"] = ctx.user_id
            return b"mp3"

        monkeypatch.setattr(delib_routes, "generate_tts_audio", _fake)
        _speak("読み上げます")
        assert seen["feature"] == "deliberation:voice_tts"
        assert seen["user_id"] == _TEACHER["id"]


# ---------------------------------------------------------------------------
# CostGate（day-only・STT/TTS 共通カウンタ）
# ---------------------------------------------------------------------------


class TestCostGate:
    def test_daily_limit_is_shared_between_stt_and_tts(self, monkeypatch):
        _set_voice_limit(monkeypatch, 1)
        assert dialogue.check_and_count_voice_call("u1") is True
        assert dialogue.check_and_count_voice_call("u1") is False
        # ユーザーごとに独立
        assert dialogue.check_and_count_voice_call("u2") is True

    def test_transcribe_over_limit_is_429_without_numbers(self, monkeypatch):
        _set_voice_limit(monkeypatch, 0)
        monkeypatch.setattr(delib_routes, "transcribe_audio", lambda *_a, **_k: "x")
        with pytest.raises(HTTPException) as exc:
            _transcribe(_FakeUpload(b"audio-bytes"))
        assert exc.value.status_code == 429
        detail = str(exc.value.detail)
        assert not re.search(r"\d", detail), detail
        assert "上限" in detail

    def test_speak_over_limit_is_429_without_numbers(self, monkeypatch):
        _set_voice_limit(monkeypatch, 0)
        monkeypatch.setattr(delib_routes, "generate_tts_audio", lambda *_a, **_k: b"mp3")
        with pytest.raises(HTTPException) as exc:
            _speak("読み上げます")
        assert exc.value.status_code == 429
        assert not re.search(r"\d", str(exc.value.detail))

    def test_gate_is_not_consumed_by_input_validation_failures(self, monkeypatch):
        """空入力・過大入力で上限を焼かない（422/400 経路の後に消費する）。"""
        _set_voice_limit(monkeypatch, 1)
        with pytest.raises(HTTPException):
            _transcribe(_FakeUpload(b""))
        with pytest.raises(HTTPException):
            _speak("   ")
        # まだ1回分残っている
        assert dialogue.check_and_count_voice_call(_TEACHER["id"]) is True


# ---------------------------------------------------------------------------
# 層の境界・語彙登録
# ---------------------------------------------------------------------------


class TestWiring:
    def test_features_are_registered_in_known_features(self):
        from core.llm_usage.schema import KNOWN_FEATURES

        assert "deliberation:voice_stt" in KNOWN_FEATURES
        assert "deliberation:voice_tts" in KNOWN_FEATURES

    def test_features_resolve_to_read_only_voice_scene(self):
        from core import llm_policy

        for feature in ("deliberation:voice_stt", "deliberation:voice_tts"):
            scene = llm_policy.scene_for_feature(feature)
            assert scene == llm_policy.SCENE_LEARNING_VOICE
            assert llm_policy.is_read_only_scene(scene) is True

    def test_voice_gate_lives_in_core_without_fastapi(self):
        src = (BACKEND / "core" / "deliberation" / "dialogue.py").read_text(encoding="utf-8")
        assert "check_and_count_voice_call" in src
        assert "fastapi" not in src

    def test_route_does_not_call_approval_apis(self):
        """GR1: 音声は入出力手段であって、承認・却下を代行しない。"""
        src = (BACKEND / "api" / "routes" / "deliberation.py").read_text(encoding="utf-8")
        voice_section = src.split("# 音声対話（グラフレビュー画面のハンズフリー入出力）")[1]
        for forbidden in ("approve", "review_status", "record_review_event"):
            assert forbidden not in voice_section, forbidden
