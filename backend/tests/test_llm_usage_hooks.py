"""core/llm.py（+ core/tts.py）に追加した LLM トークン使用量計測フックのテスト（U層 Task B）。

DB・実 API は一切使わない。``core.llm_usage.observe.record`` を monkeypatch して
イベントをキャプチャし、各公開関数の全プロバイダ経路で「1 呼び出し = 1 イベント」
「reported 優先」「失敗時も記録した上で re-raise」「戻り値・例外挙動が変わらないこと」
を検証する。

観点別クラス:
- ``TestGenerateTextOpenAI``            — generate_text openai 経路（reported / 例外）
- ``TestGenerateStructuredOpenAI``      — generate_text_with_structured_output openai 経路
- ``TestGenerateEmbeddingsOpenAI``      — generate_embeddings openai 経路
- ``TestGenerateStructuredWithImages``  — generate_structured_with_images (vision)
- ``TestTranscribeAudio``               — transcribe_audio
- ``TestGenerateTtsAudioOpenAI``        — core.tts.generate_tts_audio openai 経路
- ``TestGeminiHelperReportedUsage``     — _gemini_generate_text（gemini 形 usage_metadata）
- ``TestObserveFailureDoesNotBreakCallers`` — observe 内部例外が呼び出し元に伝播しないこと
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

import core.llm_usage.observe as observe
from core.config import Settings


def _make_settings(provider: str = "openai", **overrides) -> Settings:
    """core.llm / core.tts のフック検証用にプロバイダ・モデルを固定した Settings を作る。"""
    defaults: dict = dict(
        _env_file=None,
        llm_provider=provider,
        llm_api_key="sk-test-key",
        llm_analysis_model="gpt-4o",
        llm_embedding_model="text-embedding-3-large",
        llm_transcribe_model="whisper-1",
    )
    defaults.update(overrides)
    return Settings(**defaults)


@pytest.fixture()
def captured_events(monkeypatch):
    """``core.llm_usage.observe.record`` を差し替えてイベントをキャプチャする。

    ``observe_chat`` 等は同一モジュール（observe.py）の global ``record`` を呼ぶため、
    ここを monkeypatch すれば core.llm / core.tts からの呼び出しも横断的に捕捉できる
    （tests/test_llm_usage_observe.py と同じ手法）。
    """
    events: list = []
    monkeypatch.setattr(observe, "record", lambda event: events.append(event))
    return events


class _DummyStruct(BaseModel):
    answer: str


def _fake_openai_chat_response(
    *,
    content: str = "hi there",
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
    total_tokens: int = 15,
    cached_tokens: int | None = None,
    reasoning_tokens: int | None = None,
    parsed=None,
):
    message_kwargs = {"content": content}
    if parsed is not None:
        message_kwargs["parsed"] = parsed
    usage = types.SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        prompt_tokens_details=types.SimpleNamespace(cached_tokens=cached_tokens),
        completion_tokens_details=types.SimpleNamespace(reasoning_tokens=reasoning_tokens),
    )
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=types.SimpleNamespace(**message_kwargs))],
        usage=usage,
    )


# ---------------------------------------------------------------------------
# generate_text — openai 経路
# ---------------------------------------------------------------------------


class TestGenerateTextOpenAI:
    def test_reported_usage_and_return_value_unchanged(self, monkeypatch, captured_events):
        import core.llm as llm

        fake_response = _fake_openai_chat_response(
            content="hello world",
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            cached_tokens=2,
            reasoning_tokens=1,
        )
        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = fake_response

        monkeypatch.setattr(llm, "get_settings", lambda: _make_settings("openai"))
        monkeypatch.setattr(llm, "_get_openai_client", lambda: fake_client)

        result = llm.generate_text([{"role": "user", "content": "hi"}])

        # 戻り値はフック追加前と同一（response.choices[0].message.content）。
        assert result == "hello world"

        assert len(captured_events) == 1
        event = captured_events[0]
        assert event.operation == "chat"
        assert event.provider == "openai"
        assert event.usage_source == "reported"
        assert event.prompt_tokens == 10
        assert event.completion_tokens == 5
        assert event.total_tokens == 15
        assert event.cached_tokens == 2
        assert event.reasoning_tokens == 1
        assert event.success is True

    def test_sdk_exception_records_failure_and_propagates(self, monkeypatch, captured_events):
        import core.llm as llm

        fake_client = MagicMock()
        fake_client.chat.completions.create.side_effect = RuntimeError("network down")

        monkeypatch.setattr(llm, "get_settings", lambda: _make_settings("openai"))
        monkeypatch.setattr(llm, "_get_openai_client", lambda: fake_client)

        with pytest.raises(RuntimeError, match="network down"):
            llm.generate_text([{"role": "user", "content": "hi"}])

        assert len(captured_events) == 1
        event = captured_events[0]
        assert event.operation == "chat"
        assert event.success is False
        assert event.error_type == "RuntimeError"
        # 入力側は推計でも必ず埋まる。
        assert event.prompt_tokens is not None and event.prompt_tokens > 0


# ---------------------------------------------------------------------------
# generate_text_with_structured_output — openai 経路
# ---------------------------------------------------------------------------


class TestGenerateStructuredOpenAI:
    def test_structured_reported(self, monkeypatch, captured_events):
        import core.llm as llm

        parsed = _DummyStruct(answer="42")
        fake_response = _fake_openai_chat_response(
            content='{"answer": "42"}',
            parsed=parsed,
            prompt_tokens=8,
            completion_tokens=3,
            total_tokens=11,
        )
        fake_client = MagicMock()
        fake_client.beta.chat.completions.parse.return_value = fake_response

        monkeypatch.setattr(llm, "get_settings", lambda: _make_settings("openai"))
        monkeypatch.setattr(llm, "_get_openai_client", lambda: fake_client)

        result = llm.generate_text_with_structured_output(
            [{"role": "user", "content": "q"}], _DummyStruct
        )

        assert result == parsed

        assert len(captured_events) == 1
        event = captured_events[0]
        assert event.operation == "structured"
        assert event.usage_source == "reported"
        assert event.prompt_tokens == 8
        assert event.completion_tokens == 3
        assert event.success is True

    def test_structured_sdk_exception_records_failure_and_propagates(self, monkeypatch, captured_events):
        import core.llm as llm

        fake_client = MagicMock()
        fake_client.beta.chat.completions.parse.side_effect = ValueError("bad schema")

        monkeypatch.setattr(llm, "get_settings", lambda: _make_settings("openai"))
        monkeypatch.setattr(llm, "_get_openai_client", lambda: fake_client)

        with pytest.raises(ValueError, match="bad schema"):
            llm.generate_text_with_structured_output(
                [{"role": "user", "content": "q"}], _DummyStruct
            )

        assert len(captured_events) == 1
        event = captured_events[0]
        assert event.operation == "structured"
        assert event.success is False
        assert event.error_type == "ValueError"


# ---------------------------------------------------------------------------
# generate_embeddings — openai 経路
# ---------------------------------------------------------------------------


class TestGenerateEmbeddingsOpenAI:
    def test_embeddings_reported(self, monkeypatch, captured_events):
        import core.llm as llm

        fake_response = types.SimpleNamespace(
            data=[
                types.SimpleNamespace(embedding=[0.1, 0.2]),
                types.SimpleNamespace(embedding=[0.3, 0.4]),
            ],
            usage=types.SimpleNamespace(prompt_tokens=7),
        )
        fake_client = MagicMock()
        fake_client.embeddings.create.return_value = fake_response

        monkeypatch.setattr(llm, "get_settings", lambda: _make_settings("openai"))
        monkeypatch.setattr(llm, "_get_openai_client", lambda: fake_client)

        result = llm.generate_embeddings(["a", "b"])

        assert result == [[0.1, 0.2], [0.3, 0.4]]

        assert len(captured_events) == 1
        event = captured_events[0]
        assert event.operation == "embedding"
        assert event.usage_source == "reported"
        assert event.prompt_tokens == 7
        assert event.total_tokens == 7

    def test_embeddings_sdk_exception_records_failure_and_propagates(self, monkeypatch, captured_events):
        import core.llm as llm

        fake_client = MagicMock()
        fake_client.embeddings.create.side_effect = RuntimeError("db down")

        monkeypatch.setattr(llm, "get_settings", lambda: _make_settings("openai"))
        monkeypatch.setattr(llm, "_get_openai_client", lambda: fake_client)

        with pytest.raises(RuntimeError, match="db down"):
            llm.generate_embeddings(["a"])

        assert len(captured_events) == 1
        event = captured_events[0]
        assert event.operation == "embedding"
        assert event.success is False


# ---------------------------------------------------------------------------
# generate_structured_with_images — vision
# ---------------------------------------------------------------------------


class TestGenerateStructuredWithImages:
    def test_vision_image_count(self, monkeypatch, captured_events):
        import core.llm as llm

        fake_response = _fake_openai_chat_response(
            content='{"result": "ok"}',
            prompt_tokens=100,
            completion_tokens=20,
            total_tokens=120,
        )
        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = fake_response

        monkeypatch.setattr(llm, "get_settings", lambda: _make_settings("openai"))
        monkeypatch.setattr(llm, "_get_openai_client", lambda: fake_client)

        images = [b"\x89PNG\r\n\x1a\n" + b"0" * 10, b"\xff\xd8\xff" + b"0" * 10]
        result = llm.generate_structured_with_images("describe", images, {"type": "object"})

        assert result == {"result": "ok"}

        assert len(captured_events) == 1
        event = captured_events[0]
        assert event.operation == "vision"
        assert event.image_count == 2
        assert event.usage_source == "reported"

    def test_vision_sdk_exception_records_failure_and_propagates(self, monkeypatch, captured_events):
        import core.llm as llm

        fake_client = MagicMock()
        fake_client.chat.completions.create.side_effect = TimeoutError("slow")

        monkeypatch.setattr(llm, "get_settings", lambda: _make_settings("openai"))
        monkeypatch.setattr(llm, "_get_openai_client", lambda: fake_client)

        with pytest.raises(TimeoutError):
            llm.generate_structured_with_images("describe", [b"\x89PNG\r\n\x1a\n"], {"type": "object"})

        assert len(captured_events) == 1
        event = captured_events[0]
        assert event.operation == "vision"
        assert event.success is False
        assert event.image_count == 1


# ---------------------------------------------------------------------------
# transcribe_audio
# ---------------------------------------------------------------------------


class TestTranscribeAudio:
    def test_transcribe_records_audio_bytes(self, monkeypatch, captured_events):
        import core.llm as llm

        fake_resp = types.SimpleNamespace(text="hello transcript")
        fake_client = MagicMock()
        fake_client.audio.transcriptions.create.return_value = fake_resp

        monkeypatch.setattr(llm, "get_settings", lambda: _make_settings("openai"))
        monkeypatch.setattr(llm, "_get_openai_client", lambda: fake_client)

        audio_bytes = b"x" * 1234
        result = llm.transcribe_audio(audio_bytes)

        assert result == "hello transcript"

        assert len(captured_events) == 1
        event = captured_events[0]
        assert event.operation == "transcribe"
        assert event.audio_bytes == 1234
        assert event.success is True

    def test_transcribe_sdk_exception_records_failure_and_propagates(self, monkeypatch, captured_events):
        import core.llm as llm

        fake_client = MagicMock()
        fake_client.audio.transcriptions.create.side_effect = OSError("nope")

        monkeypatch.setattr(llm, "get_settings", lambda: _make_settings("openai"))
        monkeypatch.setattr(llm, "_get_openai_client", lambda: fake_client)

        with pytest.raises(OSError):
            llm.transcribe_audio(b"abcd")

        assert len(captured_events) == 1
        event = captured_events[0]
        assert event.operation == "transcribe"
        assert event.success is False
        assert event.audio_bytes == 4


# ---------------------------------------------------------------------------
# core.tts.generate_tts_audio — openai 経路
# ---------------------------------------------------------------------------


class TestGenerateTtsAudioOpenAI:
    def test_tts_characters(self, monkeypatch, captured_events):
        import core.tts as tts_module

        class FakeSpeech:
            def create(self, **kwargs):
                class FakeResponse:
                    content = b"fake-mp3-bytes"

                return FakeResponse()

        class FakeOpenAIClient:
            audio = type("FakeAudio", (), {"speech": FakeSpeech()})()

        fake_openai_mod = types.ModuleType("openai")
        fake_openai_mod.OpenAI = lambda api_key: FakeOpenAIClient()
        monkeypatch.setitem(sys.modules, "openai", fake_openai_mod)

        fake_settings = types.SimpleNamespace(
            llm_api_key="sk-test", llm_provider="openai", lecture_tts_voice="alloy"
        )
        monkeypatch.setattr(tts_module, "get_settings", lambda: fake_settings)

        text = "こんにちは、これはテストです。" * 5
        result = tts_module.generate_tts_audio(text)

        assert result == b"fake-mp3-bytes"

        assert len(captured_events) == 1
        event = captured_events[0]
        assert event.operation == "tts"
        assert event.tts_characters == len(text[:4096])
        assert event.success is True

    def test_tts_fatal_error_records_failure_and_raises(self, monkeypatch, captured_events):
        import core.tts as tts_module

        class FakeSpeech:
            def create(self, **kwargs):
                raise RuntimeError("401 Unauthorized: AuthenticationError")

        class FakeOpenAIClient:
            audio = type("FakeAudio", (), {"speech": FakeSpeech()})()

        fake_openai_mod = types.ModuleType("openai")
        fake_openai_mod.OpenAI = lambda api_key: FakeOpenAIClient()
        monkeypatch.setitem(sys.modules, "openai", fake_openai_mod)

        fake_settings = types.SimpleNamespace(
            llm_api_key="sk-broken", llm_provider="openai", lecture_tts_voice="alloy"
        )
        monkeypatch.setattr(tts_module, "get_settings", lambda: fake_settings)

        with pytest.raises(tts_module.TtsFatalError):
            tts_module.generate_tts_audio("text")

        assert len(captured_events) == 1
        event = captured_events[0]
        assert event.operation == "tts"
        assert event.success is False

    def test_tts_generic_error_records_failure_and_returns_none(self, monkeypatch, captured_events):
        """既存の非致命経路（None を返す）の挙動を維持しつつ、失敗も記録する。"""
        import core.tts as tts_module

        class FakeSpeech:
            def create(self, **kwargs):
                raise RuntimeError("temporary glitch")

        class FakeOpenAIClient:
            audio = type("FakeAudio", (), {"speech": FakeSpeech()})()

        fake_openai_mod = types.ModuleType("openai")
        fake_openai_mod.OpenAI = lambda api_key: FakeOpenAIClient()
        monkeypatch.setitem(sys.modules, "openai", fake_openai_mod)

        fake_settings = types.SimpleNamespace(
            llm_api_key="sk-test", llm_provider="openai", lecture_tts_voice="alloy"
        )
        monkeypatch.setattr(tts_module, "get_settings", lambda: fake_settings)

        result = tts_module.generate_tts_audio("text")

        assert result is None
        assert len(captured_events) == 1
        event = captured_events[0]
        assert event.operation == "tts"
        assert event.success is False

    def test_tts_client_setup_failure_before_synthesis_not_recorded(self, monkeypatch, captured_events):
        """合成を試みる前（クライアント生成時点）の失敗は記録しない（実際に合成を試行した場合のみ記録）。"""
        import core.tts as tts_module

        fake_openai_mod = types.ModuleType("openai")
        fake_openai_mod.OpenAI = lambda api_key: (_ for _ in ()).throw(RuntimeError("connection failed"))
        monkeypatch.setitem(sys.modules, "openai", fake_openai_mod)

        fake_settings = types.SimpleNamespace(
            llm_api_key="sk-broken", llm_provider="openai", lecture_tts_voice="alloy"
        )
        monkeypatch.setattr(tts_module, "get_settings", lambda: fake_settings)

        result = tts_module.generate_tts_audio("text")

        assert result is None
        assert len(captured_events) == 0


# ---------------------------------------------------------------------------
# _gemini_generate_text — gemini 形 usage_metadata
# ---------------------------------------------------------------------------


class TestGeminiHelperReportedUsage:
    def test_gemini_generate_text_reports_usage(self, monkeypatch, captured_events):
        import core.llm as llm

        fake_usage_metadata = types.SimpleNamespace(
            prompt_token_count=50, candidates_token_count=20, total_token_count=70
        )
        fake_response = types.SimpleNamespace(text="gemini output", usage_metadata=fake_usage_metadata)
        fake_model = MagicMock()
        fake_model.generate_content.return_value = fake_response
        fake_genai = MagicMock()
        fake_genai.GenerativeModel.return_value = fake_model

        monkeypatch.setattr(llm, "get_settings", lambda: _make_settings("gemini"))

        result = llm._gemini_generate_text(
            [{"role": "user", "content": "hi"}],
            "gemini-1.5-pro",
            genai_module=fake_genai,
        )

        assert result == "gemini output"

        assert len(captured_events) == 1
        event = captured_events[0]
        assert event.operation == "chat"
        assert event.provider == "gemini"
        assert event.usage_source == "reported"
        assert event.prompt_tokens == 50
        assert event.completion_tokens == 20
        assert event.total_tokens == 70

    def test_gemini_generate_text_sdk_exception_propagates(self, monkeypatch, captured_events):
        import core.llm as llm

        fake_model = MagicMock()
        fake_model.generate_content.side_effect = RuntimeError("gemini down")
        fake_genai = MagicMock()
        fake_genai.GenerativeModel.return_value = fake_model

        monkeypatch.setattr(llm, "get_settings", lambda: _make_settings("gemini"))

        with pytest.raises(RuntimeError, match="gemini down"):
            llm._gemini_generate_text(
                [{"role": "user", "content": "hi"}],
                "gemini-1.5-pro",
                genai_module=fake_genai,
            )

        assert len(captured_events) == 1
        event = captured_events[0]
        assert event.operation == "chat"
        assert event.provider == "gemini"
        assert event.success is False


# ---------------------------------------------------------------------------
# observe が内部で失敗しても呼び出し元の成功フローが壊れないこと
# ---------------------------------------------------------------------------


class TestObserveFailureDoesNotBreakCallers:
    def test_generate_text_succeeds_even_if_record_raises(self, monkeypatch):
        import core.llm as llm

        monkeypatch.setattr(
            observe, "record", lambda event: (_ for _ in ()).throw(RuntimeError("boom"))
        )

        fake_response = _fake_openai_chat_response(content="still works")
        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = fake_response

        monkeypatch.setattr(llm, "get_settings", lambda: _make_settings("openai"))
        monkeypatch.setattr(llm, "_get_openai_client", lambda: fake_client)

        result = llm.generate_text([{"role": "user", "content": "hi"}])

        assert result == "still works"

    def test_generate_tts_audio_succeeds_even_if_record_raises(self, monkeypatch):
        import core.tts as tts_module

        monkeypatch.setattr(
            observe, "record", lambda event: (_ for _ in ()).throw(RuntimeError("boom"))
        )

        class FakeSpeech:
            def create(self, **kwargs):
                class FakeResponse:
                    content = b"still-works-mp3"

                return FakeResponse()

        class FakeOpenAIClient:
            audio = type("FakeAudio", (), {"speech": FakeSpeech()})()

        fake_openai_mod = types.ModuleType("openai")
        fake_openai_mod.OpenAI = lambda api_key: FakeOpenAIClient()
        monkeypatch.setitem(sys.modules, "openai", fake_openai_mod)

        fake_settings = types.SimpleNamespace(
            llm_api_key="sk-test", llm_provider="openai", lecture_tts_voice="alloy"
        )
        monkeypatch.setattr(tts_module, "get_settings", lambda: fake_settings)

        result = tts_module.generate_tts_audio("text")

        assert result == b"still-works-mp3"
