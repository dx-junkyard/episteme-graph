"""LLM Adapter（core.llm）の単体テスト。

Reasoning モデル向けのメッセージ変換・API 引数変換をテストする。
外部 API は一切呼び出さない。
"""

from __future__ import annotations

import pytest

from core.config import Settings
from core.llm import (
    _adapt_messages_for_model,
    _build_api_kwargs,
    _is_reasoning_model,
    get_llm_params,
)


# ---------------------------------------------------------------------------
# _is_reasoning_model
# ---------------------------------------------------------------------------


class TestIsReasoningModel:
    @pytest.mark.parametrize("model", ["o1", "o1-preview", "o3-mini", "o4-mini", "gpt-5.0"])
    def test_reasoning_models(self, model):
        assert _is_reasoning_model(model) is True

    @pytest.mark.parametrize("model", ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "text-embedding-3-large"])
    def test_non_reasoning_models(self, model):
        assert _is_reasoning_model(model) is False


# ---------------------------------------------------------------------------
# _adapt_messages_for_model
# ---------------------------------------------------------------------------


class TestAdaptMessagesForModel:
    def test_reasoning_model_converts_system_to_developer(self):
        messages = [
            {"role": "system", "content": "You are an AI."},
            {"role": "user", "content": "Hello"},
        ]
        adapted = _adapt_messages_for_model(messages, "o3-mini")
        assert adapted[0]["role"] == "developer"
        assert adapted[0]["content"] == "You are an AI."
        assert adapted[1]["role"] == "user"

    def test_non_reasoning_model_keeps_system(self):
        messages = [
            {"role": "system", "content": "You are an AI."},
            {"role": "user", "content": "Hello"},
        ]
        adapted = _adapt_messages_for_model(messages, "gpt-4o")
        assert adapted[0]["role"] == "system"

    def test_no_system_message(self):
        messages = [{"role": "user", "content": "Hello"}]
        adapted = _adapt_messages_for_model(messages, "o3-mini")
        assert len(adapted) == 1
        assert adapted[0]["role"] == "user"

    def test_multiple_system_messages(self):
        messages = [
            {"role": "system", "content": "First system"},
            {"role": "system", "content": "Second system"},
            {"role": "user", "content": "Hello"},
        ]
        adapted = _adapt_messages_for_model(messages, "o1")
        assert adapted[0]["role"] == "developer"
        assert adapted[1]["role"] == "developer"
        assert adapted[2]["role"] == "user"

    def test_original_messages_not_mutated(self):
        messages = [{"role": "system", "content": "X"}]
        _adapt_messages_for_model(messages, "o3-mini")
        assert messages[0]["role"] == "system"


# ---------------------------------------------------------------------------
# _build_api_kwargs
# ---------------------------------------------------------------------------


class TestBuildApiKwargs:
    def test_normal_model_preserves_temperature(self):
        kwargs = _build_api_kwargs("gpt-4o", temperature=0.3)
        assert kwargs["temperature"] == 0.3

    def test_normal_model_preserves_max_tokens(self):
        kwargs = _build_api_kwargs("gpt-4o", max_tokens=1000)
        assert kwargs["max_tokens"] == 1000

    def test_reasoning_model_removes_temperature(self):
        kwargs = _build_api_kwargs("o3-mini", temperature=0.3)
        assert "temperature" not in kwargs

    def test_reasoning_model_converts_max_tokens(self):
        kwargs = _build_api_kwargs("o3-mini", max_tokens=1000)
        assert "max_tokens" not in kwargs
        assert kwargs["max_completion_tokens"] == 1000

    def test_reasoning_model_no_args(self):
        kwargs = _build_api_kwargs("o3-mini")
        assert kwargs == {}

    def test_normal_model_no_args(self):
        kwargs = _build_api_kwargs("gpt-4o")
        assert kwargs == {}

    def test_extra_kwargs_passed_through(self):
        kwargs = _build_api_kwargs("gpt-4o", extra_kwargs={"top_p": 0.9})
        assert kwargs["top_p"] == 0.9

    def test_extra_kwargs_on_reasoning_model(self):
        kwargs = _build_api_kwargs("o3-mini", temperature=0.5, extra_kwargs={"stream": True})
        assert "temperature" not in kwargs
        assert kwargs["stream"] is True


# ---------------------------------------------------------------------------
# get_llm_params
# ---------------------------------------------------------------------------


class TestGetLlmParams:
    def test_non_openai_provider_falls_back_to_analysis_model_for_openai_defaults(self, monkeypatch):
        settings = Settings(
            llm_provider="google",
            llm_analysis_model="gemini-2.5-pro",
            llm_standard_model="gpt-5.2",
        )
        monkeypatch.setattr("core.llm.get_settings", lambda: settings)

        params = get_llm_params("standard")

        assert params["model"] == "gemini-2.5-pro"
        assert params["reasoning_effort"] is None

    def test_explicit_non_openai_mode_model_is_preserved(self, monkeypatch):
        settings = Settings(
            llm_provider="google",
            llm_analysis_model="gemini-2.5-flash",
            llm_standard_model="gemini-2.5-pro",
        )
        monkeypatch.setattr("core.llm.get_settings", lambda: settings)

        params = get_llm_params("standard")

        assert params["model"] == "gemini-2.5-pro"
