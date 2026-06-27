"""Tests for provider-aware agent LLM client."""
from __future__ import annotations

import sys
from types import ModuleType

from episteme_graph.agents.llm_json_client import (
    ProviderJSONLLMClient,
    recover_truncated_json,
)


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


# --- truncated-JSON recovery (max_tokens cutoff) ----------------------------


def test_recover_truncated_json_recovers_complete_array_elements():
    # Output cut off inside the 3rd object, after a key with no value — exactly
    # the shape produced when the model hits its max-token ceiling.
    truncated = (
        '{"document_id": "doc1", "components": ['
        '{"component_id": "comp_001", "label": "A"}, '
        '{"component_id": "comp_002", "label": "B"}, '
        '{"component_id": "comp_003", "label": "C", "evidence_ids":'
    )
    recovered = recover_truncated_json(truncated)
    assert recovered is not None
    ids = [c["component_id"] for c in recovered["components"]]
    assert ids == ["comp_001", "comp_002", "comp_003"]
    assert recovered["document_id"] == "doc1"
    # comp_003 keeps its complete fields and drops only the dangling key.
    assert recovered["components"][-1]["label"] == "C"
    assert "evidence_ids" not in recovered["components"][-1]


def test_recover_truncated_json_cut_inside_string():
    truncated = '{"components": [{"component_id": "comp_001"}], "summary": "half a sen'
    recovered = recover_truncated_json(truncated)
    assert recovered is not None
    assert [c["component_id"] for c in recovered["components"]] == ["comp_001"]
    assert "summary" not in recovered


def test_recover_truncated_json_cut_inside_number():
    truncated = '{"a": 1, "b": 234'
    recovered = recover_truncated_json(truncated)
    assert recovered == {"a": 1}


def test_recover_truncated_json_returns_none_for_unsalvageable():
    assert recover_truncated_json("not json at all") is None
    assert recover_truncated_json("{") is None


def test_parse_json_uses_recovery_on_truncation():
    client = ProviderJSONLLMClient()
    truncated = (
        '{"components": ['
        '{"component_id": "comp_001"}, '
        '{"component_id": "comp_002", "label":'
    )
    parsed = client._parse_json(truncated)
    assert [c["component_id"] for c in parsed["components"]] == [
        "comp_001",
        "comp_002",
    ]
    assert client.last_parse_error is not None
    assert "recovered_truncated_json" in client.last_parse_error


def test_parse_json_clean_output_sets_no_parse_error():
    client = ProviderJSONLLMClient()
    parsed = client._parse_json('{"components": [{"component_id": "comp_001"}]}')
    assert parsed["components"][0]["component_id"] == "comp_001"
    assert client.last_parse_error is None


# --- per-tier max_tokens resolution -----------------------------------------


def test_resolve_max_tokens_uses_core_resolver(monkeypatch):
    core_mod = ModuleType("core")
    llm_mod = ModuleType("core.llm")
    captured = []

    def fake_max_tokens_for_model(model):
        captured.append(model)
        return 777_000

    llm_mod.max_tokens_for_model = fake_max_tokens_for_model
    monkeypatch.setitem(sys.modules, "core", core_mod)
    monkeypatch.setitem(sys.modules, "core.llm", llm_mod)
    monkeypatch.delenv("AGENT_LLM_MAX_TOKENS", raising=False)

    client = ProviderJSONLLMClient(model="deep-m")
    assert client._resolve_max_tokens() == 777_000
    assert captured == ["deep-m"]


def test_resolve_max_tokens_env_override_takes_precedence(monkeypatch):
    monkeypatch.setenv("AGENT_LLM_MAX_TOKENS", "5000")
    client = ProviderJSONLLMClient(model="deep-m")
    assert client._resolve_max_tokens() == 5000


def test_resolve_max_tokens_falls_back_when_core_unavailable(monkeypatch):
    # No core.llm.max_tokens_for_model available (isolated unit context).
    core_mod = ModuleType("core")
    llm_mod = ModuleType("core.llm")
    monkeypatch.setitem(sys.modules, "core", core_mod)
    monkeypatch.setitem(sys.modules, "core.llm", llm_mod)
    monkeypatch.delenv("AGENT_LLM_MAX_TOKENS", raising=False)

    client = ProviderJSONLLMClient()
    assert client._resolve_max_tokens() == 12000
