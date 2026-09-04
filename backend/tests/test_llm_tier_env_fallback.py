"""tier モデル env の解決順序（core/config.py::Settings）。

2026-09-05 に docker-compose.yml の `${VAR:-o3-mini}` 連鎖を撤去し、旧名吸収と
tier 間フォールバックを Settings へ移設した。その契約を固定する:

- 明示 env（LLM_*_MODEL、fast は旧名 OPENAI_FAST_MODEL も）が最優先
- LLM_STANDARD_MODEL 未設定 + LLM_ANALYSIS_MODEL（旧名 OPENAI_ANALYSIS_MODEL）明示 → standard は analysis に従う
- LLM_DEEP_MODEL 未設定 + standard が明示（直接 or analysis 経由） → deep は standard に従う
- 何も明示しなければ既定値（既定どうしの関係は変えない）
"""

from __future__ import annotations

import importlib

import pytest

_TIER_ENV = (
    "LLM_FAST_MODEL", "OPENAI_FAST_MODEL", "LLM_STANDARD_MODEL", "LLM_DEEP_MODEL",
    "LLM_ANALYSIS_MODEL", "OPENAI_ANALYSIS_MODEL",
)


def _settings(monkeypatch, **env):
    for key in _TIER_ENV:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    import core.config as config_module
    importlib.reload(config_module)
    return config_module.Settings(_env_file=None)


def test_defaults_when_nothing_is_set(monkeypatch):
    s = _settings(monkeypatch)
    assert s.llm_fast_model == "gpt-5.4-nano"
    assert s.llm_standard_model == "gpt-5.2"
    assert s.llm_deep_model == "gpt-5.2"


def test_legacy_openai_fast_model_is_absorbed(monkeypatch):
    s = _settings(monkeypatch, OPENAI_FAST_MODEL="legacy-fast")
    assert s.llm_fast_model == "legacy-fast"


def test_explicit_fast_wins_over_legacy_name(monkeypatch):
    s = _settings(monkeypatch, LLM_FAST_MODEL="new-fast", OPENAI_FAST_MODEL="legacy-fast")
    assert s.llm_fast_model == "new-fast"


@pytest.mark.parametrize("analysis_key", ["LLM_ANALYSIS_MODEL", "OPENAI_ANALYSIS_MODEL"])
def test_standard_and_deep_follow_explicit_analysis(monkeypatch, analysis_key):
    s = _settings(monkeypatch, **{analysis_key: "analysis-x"})
    assert s.llm_analysis_model == "analysis-x"
    assert s.llm_standard_model == "analysis-x"
    assert s.llm_deep_model == "analysis-x"


def test_explicit_standard_wins_and_deep_follows_standard(monkeypatch):
    s = _settings(monkeypatch, LLM_ANALYSIS_MODEL="analysis-x", LLM_STANDARD_MODEL="std-y")
    assert s.llm_standard_model == "std-y"
    assert s.llm_deep_model == "std-y"


def test_explicit_deep_is_never_overridden(monkeypatch):
    s = _settings(monkeypatch, LLM_ANALYSIS_MODEL="analysis-x", LLM_DEEP_MODEL="deep-z")
    assert s.llm_standard_model == "analysis-x"
    assert s.llm_deep_model == "deep-z"


def test_compose_no_longer_hardcodes_tier_defaults():
    from pathlib import Path

    compose = (Path(__file__).resolve().parents[2] / "docker-compose.yml").read_text(encoding="utf-8")
    body = "\n".join(ln for ln in compose.splitlines() if not ln.lstrip().startswith("#"))
    for var in ("LLM_FAST_MODEL", "LLM_STANDARD_MODEL", "LLM_DEEP_MODEL"):
        assert f"{var}:" not in body, f"docker-compose.yml が {var} を environment で上書きしている"
