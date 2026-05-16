"""Tests for provider-aware agent LLM client."""
from __future__ import annotations

import sys
from types import ModuleType

from episteme_graph.agents.llm_json_client import ProviderJSONLLMClient


def test_generate_uses_core_llm_provider_router(monkeypatch):
    calls = []
    core_mod = ModuleType("core")
    llm_mod = ModuleType("core.llm")

    def fake_generate_text(**kwargs):
        calls.append(kwargs)
        return '{"ok": true}'

    llm_mod.generate_text = fake_generate_text
    monkeypatch.setitem(sys.modules, "core", core_mod)
    monkeypatch.setitem(sys.modules, "core.llm", llm_mod)
    client = ProviderJSONLLMClient(model="gemini-2.5-pro")

    result = client.generate([{"role": "user", "content": "return json"}])

    assert result == {"ok": True}
    assert calls[0]["model"] == "gemini-2.5-pro"
    assert calls[0]["temperature"] == 0.0


def test_default_model_is_delegated_to_core_settings(monkeypatch):
    monkeypatch.delenv("LLM_ANALYSIS_MODEL", raising=False)
    client = ProviderJSONLLMClient()
    assert client.model is None
