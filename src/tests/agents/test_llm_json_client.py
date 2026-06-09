"""Tests for provider-aware agent LLM client."""
from __future__ import annotations

import sys
from types import ModuleType

from episteme_graph.agents.llm_json_client import ProviderJSONLLMClient


def test_generate_uses_core_llm_provider_router(monkeypatch):
    calls = []
    wall_timeouts = []
    core_mod = ModuleType("core")
    llm_mod = ModuleType("core.llm")

    def fake_generate_text(**kwargs):
        calls.append(kwargs)
        return '{"ok": true}'

    def fake_wall_timeout(func, timeout_seconds, *args, **kwargs):
        wall_timeouts.append(timeout_seconds)
        return func(*args, **kwargs)

    llm_mod.generate_text = fake_generate_text
    monkeypatch.setitem(sys.modules, "core", core_mod)
    monkeypatch.setitem(sys.modules, "core.llm", llm_mod)
    monkeypatch.delenv("AGENT_LLM_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("AGENT_LLM_WALL_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setattr(
        "episteme_graph.agents.llm_json_client._call_with_wall_timeout",
        fake_wall_timeout,
    )
    client = ProviderJSONLLMClient(model="gemini-2.5-pro")

    result = client.generate([{"role": "user", "content": "return json"}])

    assert result == {"ok": True}
    assert calls[0]["model"] == "gemini-2.5-pro"
    assert calls[0]["temperature"] == 0.0
    assert calls[0]["timeout"] == 300.0
    assert wall_timeouts == [330.0]


def test_generate_allows_explicit_wall_timeout(monkeypatch):
    wall_timeouts = []
    core_mod = ModuleType("core")
    llm_mod = ModuleType("core.llm")

    def fake_generate_text(**kwargs):
        return '{"ok": true}'

    def fake_wall_timeout(func, timeout_seconds, *args, **kwargs):
        wall_timeouts.append(timeout_seconds)
        return func(*args, **kwargs)

    llm_mod.generate_text = fake_generate_text
    monkeypatch.setitem(sys.modules, "core", core_mod)
    monkeypatch.setitem(sys.modules, "core.llm", llm_mod)
    monkeypatch.setenv("AGENT_LLM_TIMEOUT_SECONDS", "12")
    monkeypatch.setenv("AGENT_LLM_WALL_TIMEOUT_SECONDS", "20")
    monkeypatch.setattr(
        "episteme_graph.agents.llm_json_client._call_with_wall_timeout",
        fake_wall_timeout,
    )

    result = ProviderJSONLLMClient().generate([{"role": "user", "content": "return json"}])

    assert result == {"ok": True}
    assert wall_timeouts == [20.0]


def test_default_model_is_delegated_to_core_settings(monkeypatch):
    monkeypatch.delenv("LLM_ANALYSIS_MODEL", raising=False)
    client = ProviderJSONLLMClient()
    assert client.model is None
