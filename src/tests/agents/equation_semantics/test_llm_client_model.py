"""EquationSemanticsLLMClient のモデル解決（M層レビュー指摘 m7）。

vision 分岐は provider SDK を直接叩くため具体的なモデル名が必要だが、以前は
``settings.llm_analysis_model`` を直読みしていて M層の解決層
（run override / user・system ポリシー）を素通りしていた。現在は
``core.llm_policy.resolve_scene_model`` へ委譲し、解決できないときだけ
従来と同じ analysis tier へフェイルオープンする。

ここ（src/tests）は backend の ``core.*`` を import しない環境でも動く必要があるため、
「llm_policy が使えないときのフォールバック」だけを検証する。policy 層が実際に
効くことは ``backend/tests/test_pipeline_model_thread_propagation.py`` が固定する。
"""
from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

from episteme_graph.agents.equation_semantics.llm_client import _resolve_vision_model


def test_falls_back_to_analysis_tier_when_llm_policy_unavailable(monkeypatch):
    # resolve_scene_model を持たない偽 core.llm_policy → from ... import が ImportError。
    monkeypatch.setitem(sys.modules, "core", ModuleType("core"))
    monkeypatch.setitem(sys.modules, "core.llm_policy", ModuleType("core.llm_policy"))
    settings = SimpleNamespace(llm_analysis_model="analysis-tier-model")

    assert _resolve_vision_model(settings) == "analysis-tier-model"


def test_falls_back_when_policy_resolution_raises(monkeypatch):
    policy_mod = ModuleType("core.llm_policy")
    policy_mod.SCENE_PIPELINE = "pipeline"

    def boom(feature):
        raise RuntimeError("policy backend down")

    policy_mod.resolve_scene_model = boom
    monkeypatch.setitem(sys.modules, "core", ModuleType("core"))
    monkeypatch.setitem(sys.modules, "core.llm_policy", policy_mod)
    settings = SimpleNamespace(llm_analysis_model="analysis-tier-model")

    assert _resolve_vision_model(settings) == "analysis-tier-model"


def test_falls_back_when_policy_returns_empty_model(monkeypatch):
    policy_mod = ModuleType("core.llm_policy")
    policy_mod.SCENE_PIPELINE = "pipeline"
    policy_mod.resolve_scene_model = lambda feature: SimpleNamespace(model="")
    monkeypatch.setitem(sys.modules, "core", ModuleType("core"))
    monkeypatch.setitem(sys.modules, "core.llm_policy", policy_mod)
    settings = SimpleNamespace(llm_analysis_model="analysis-tier-model")

    assert _resolve_vision_model(settings) == "analysis-tier-model"


def test_uses_policy_resolution_with_pipeline_equation_semantics_feature(monkeypatch):
    policy_mod = ModuleType("core.llm_policy")
    policy_mod.SCENE_PIPELINE = "pipeline"
    seen: dict = {}

    def fake_resolve(feature):
        seen["feature"] = feature
        return SimpleNamespace(model="policy-model")

    policy_mod.resolve_scene_model = fake_resolve
    monkeypatch.setitem(sys.modules, "core", ModuleType("core"))
    monkeypatch.setitem(sys.modules, "core.llm_policy", policy_mod)
    settings = SimpleNamespace(llm_analysis_model="analysis-tier-model")

    assert _resolve_vision_model(settings) == "policy-model"
    assert seen["feature"] == "pipeline:equation_semantics"
