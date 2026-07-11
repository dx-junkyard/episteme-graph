"""core/llm_usage/observe.py — フック関数群のテスト。

観点: OpenAI 形 usage 抽出（reported + cached/reasoning）/ usage 無し時の推計フォールバック /
Gemini 形 usage_metadata 抽出 / error 時の success=False + error_type / usage_context 帰属の
伝搬 / observe_* が内部例外時も raise しないこと。

recorder.record() は monkeypatch で差し替え、実際の DB / バッファには触れない。
"""

from __future__ import annotations

import types

import pytest

import core.llm_usage.observe as observe
from core.llm_usage.context import (
    current_usage_context,
    set_current_feature,
    usage_context,
)


@pytest.fixture()
def captured_events(monkeypatch):
    events: list = []
    monkeypatch.setattr(observe, "record", lambda event: events.append(event))
    return events


def _obj(**kwargs):
    """属性アクセス用の軽量オブジェクト（SimpleNamespace のラッパー）。"""
    return types.SimpleNamespace(**kwargs)


class TestOpenAIChatReported:
    def test_usage_reported_with_cached_and_reasoning(self, captured_events):
        response = _obj(
            usage=_obj(
                prompt_tokens=120,
                completion_tokens=40,
                total_tokens=160,
                prompt_tokens_details=_obj(cached_tokens=30),
                completion_tokens_details=_obj(reasoning_tokens=10),
            )
        )
        observe.observe_chat(
            provider="openai",
            model="gpt-4o",
            messages=[{"role": "user", "content": "hello"}],
            response=response,
            response_text="hi there",
        )
        assert len(captured_events) == 1
        event = captured_events[0]
        assert event.usage_source == "reported"
        assert event.prompt_tokens == 120
        assert event.completion_tokens == 40
        assert event.total_tokens == 160
        assert event.cached_tokens == 30
        assert event.reasoning_tokens == 10
        assert event.success is True
        assert event.provider == "openai"
        assert event.model == "gpt-4o"

    def test_usage_missing_falls_back_to_estimate(self, captured_events):
        response = _obj()  # usage 属性なし
        observe.observe_chat(
            provider="openai",
            model="gpt-4o",
            messages=[{"role": "user", "content": "hello world"}],
            response=response,
            response_text="a response",
        )
        assert len(captured_events) == 1
        event = captured_events[0]
        assert event.usage_source in ("estimated_tokenizer", "estimated_heuristic")
        assert event.prompt_tokens is not None
        assert event.prompt_tokens > 0
        assert event.metadata.get("reasoning_excluded") is True

    def test_no_response_no_response_text_falls_back_to_estimate(self, captured_events):
        observe.observe_chat(
            provider="openai",
            model="gpt-4o",
            messages=[{"role": "user", "content": "hello"}],
        )
        assert len(captured_events) == 1
        event = captured_events[0]
        assert event.usage_source != "reported"
        assert event.success is True


class TestGeminiUsageMetadata:
    def test_usage_metadata_reported(self, captured_events):
        response = _obj(
            usage_metadata=_obj(
                prompt_token_count=200,
                candidates_token_count=55,
                total_token_count=255,
            )
        )
        observe.observe_chat(
            provider="gemini",
            model="gemini-1.5-pro",
            messages=[{"role": "user", "content": "hi"}],
            response=response,
        )
        assert len(captured_events) == 1
        event = captured_events[0]
        assert event.usage_source == "reported"
        assert event.prompt_tokens == 200
        assert event.completion_tokens == 55
        assert event.total_tokens == 255
        assert event.cached_tokens is None
        assert event.reasoning_tokens is None


class TestErrorHandling:
    def test_chat_error_records_failure_with_input_estimate(self, captured_events):
        observe.observe_chat(
            provider="openai",
            model="gpt-4o",
            messages=[{"role": "user", "content": "hello there, this failed"}],
            error=ValueError("boom"),
        )
        assert len(captured_events) == 1
        event = captured_events[0]
        assert event.success is False
        assert event.error_type == "ValueError"
        assert event.completion_tokens is None
        assert event.prompt_tokens is not None and event.prompt_tokens > 0

    def test_embeddings_error_records_failure(self, captured_events):
        observe.observe_embeddings(
            provider="openai",
            model="text-embedding-3-large",
            texts=["some text to embed"],
            error=RuntimeError("db down"),
        )
        assert len(captured_events) == 1
        event = captured_events[0]
        assert event.success is False
        assert event.error_type == "RuntimeError"
        assert event.operation == "embedding"

    def test_vision_error_records_failure(self, captured_events):
        observe.observe_vision(
            provider="openai",
            model="gpt-4o",
            prompt="describe this image",
            image_count=1,
            image_dims=[(1024, 1024)],
            error=TimeoutError("slow"),
        )
        assert len(captured_events) == 1
        event = captured_events[0]
        assert event.success is False
        assert event.error_type == "TimeoutError"
        assert event.operation == "vision"

    def test_transcribe_error(self, captured_events):
        observe.observe_transcribe(
            provider="openai", model="whisper-1", audio_bytes_len=4096, error=IOError("nope")
        )
        event = captured_events[0]
        assert event.success is False
        assert event.error_type == "OSError"  # IOError is an alias of OSError
        assert event.audio_bytes == 4096

    def test_tts_error(self, captured_events):
        observe.observe_tts(
            provider="openai", model="tts-1", characters=100, error=ValueError("x")
        )
        event = captured_events[0]
        assert event.success is False
        assert event.tts_characters == 100


class TestUsageContextAttribution:
    def test_context_fields_propagate_to_event(self, captured_events):
        with usage_context(
            feature="pipeline:apparatus_semantics",
            document_id="doc-123",
            run_id="run-456",
        ):
            observe.observe_chat(
                provider="openai",
                model="gpt-4o",
                messages=[{"role": "user", "content": "hi"}],
                response=_obj(usage=_obj(prompt_tokens=1, completion_tokens=1, total_tokens=2)),
            )
        assert len(captured_events) == 1
        event = captured_events[0]
        assert event.feature == "pipeline:apparatus_semantics"
        assert event.document_id == "doc-123"
        assert event.run_id == "run-456"

    def test_unattributed_when_no_context_set(self, captured_events):
        observe.observe_chat(
            provider="openai",
            model="gpt-4o",
            messages=[{"role": "user", "content": "hi"}],
        )
        event = captured_events[0]
        assert event.feature == "unattributed"
        assert event.user_id is None
        assert event.document_id is None

    def test_set_current_feature_replaces_feature_and_keeps_ids(self, captured_events):
        with usage_context("pipeline", document_id="doc-123", run_id="run-456"):
            set_current_feature("pipeline:claim_qualification")
            observe.observe_chat(
                provider="openai",
                model="gpt-4o",
                messages=[{"role": "user", "content": "hi"}],
                response=_obj(usage=_obj(prompt_tokens=1, completion_tokens=1, total_tokens=2)),
            )
            # 次の set_current_feature まで有効（帰属 ID は維持）
            set_current_feature("pipeline:equation_semantics")
            assert current_usage_context().feature == "pipeline:equation_semantics"
            assert current_usage_context().document_id == "doc-123"
        event = captured_events[0]
        assert event.feature == "pipeline:claim_qualification"
        assert event.document_id == "doc-123"
        assert event.run_id == "run-456"
        # with を抜けたら set_current_feature の効果も外側の値へ戻る
        assert current_usage_context().feature == "unattributed"


class TestObserveNeverRaises:
    def test_observe_chat_never_raises_even_if_record_fails(self, monkeypatch):
        monkeypatch.setattr(observe, "record", lambda event: (_ for _ in ()).throw(RuntimeError("boom")))
        # 例外が外に漏れなければ OK
        observe.observe_chat(
            provider="openai",
            model="gpt-4o",
            messages=[{"role": "user", "content": "hi"}],
        )

    def test_observe_chat_never_raises_with_broken_messages(self, monkeypatch, captured_events):
        # messages が None のような想定外入力でも例外を漏らさない
        observe.observe_chat(provider="openai", model="gpt-4o", messages=None)  # type: ignore[arg-type]

    def test_observe_embeddings_never_raises(self, monkeypatch):
        monkeypatch.setattr(observe, "record", lambda event: (_ for _ in ()).throw(RuntimeError("boom")))
        observe.observe_embeddings(provider="openai", model="text-embedding-3-large", texts=["a"])

    def test_observe_vision_never_raises(self, monkeypatch):
        monkeypatch.setattr(observe, "record", lambda event: (_ for _ in ()).throw(RuntimeError("boom")))
        observe.observe_vision(provider="openai", model="gpt-4o", prompt="p", image_count=2)

    def test_observe_transcribe_never_raises(self, monkeypatch):
        monkeypatch.setattr(observe, "record", lambda event: (_ for _ in ()).throw(RuntimeError("boom")))
        observe.observe_transcribe(provider="openai", model="whisper-1", audio_bytes_len=10)

    def test_observe_tts_never_raises(self, monkeypatch):
        monkeypatch.setattr(observe, "record", lambda event: (_ for _ in ()).throw(RuntimeError("boom")))
        observe.observe_tts(provider="openai", model="tts-1", characters=10)


class TestEmbeddingsReportedUsage:
    def test_reported_prompt_tokens(self, captured_events):
        response = _obj(usage=_obj(prompt_tokens=42))
        observe.observe_embeddings(
            provider="openai", model="text-embedding-3-large", texts=["hello", "world"], response=response
        )
        event = captured_events[0]
        assert event.usage_source == "reported"
        assert event.prompt_tokens == 42
        assert event.total_tokens == 42


class TestVisionEstimateFallback:
    def test_missing_dims_marks_flat_metadata(self, captured_events):
        observe.observe_vision(
            provider="openai", model="gpt-4o", prompt="describe", image_count=2, image_dims=None
        )
        event = captured_events[0]
        assert event.image_count == 2
        assert event.metadata.get("image_estimate") == "flat"
        assert event.prompt_tokens is not None and event.prompt_tokens > 0
