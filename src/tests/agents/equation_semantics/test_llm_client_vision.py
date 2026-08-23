"""EquationSemanticsLLMClient の vision 分岐が core.llm 経由であること。

vision 分岐はかつて provider SDK（``_get_openai_client().chat.completions.create``）を
このモジュールから直接叩いており、U層（llm_usage）の観測フックを素通りしていた
（設計書 llm_usage_metering_design.md U3「計測点は core/llm.py に一元化」の穴）。
現在は ``core.llm.generate_json_with_image`` に委譲する。

ここ（src/tests）は backend の ``core.*`` を import できない環境でも動く必要があるため、
``core`` 系モジュールはすべて偽物を ``sys.modules`` に差し込んで検証する。実 provider
呼び出しが observe されることは ``backend/tests/test_llm_usage_hooks.py``
（``TestGenerateJsonWithImage``）が固定する。
"""
from __future__ import annotations

import base64
import sys
from types import ModuleType, SimpleNamespace

from episteme_graph.agents.equation_semantics.llm_client import (
    EquationSemanticsLLMClient,
)

_IMAGE_BYTES = b"\x89PNG\r\n\x1a\n" + b"eq-crop"
_IMAGE = {
    "mime_type": "image/png",
    "data_base64": base64.b64encode(_IMAGE_BYTES).decode("ascii"),
}
_MESSAGES = [
    {"role": "system", "content": "OCR the equation"},
    {"role": "user", "content": "read this"},
]


def _install_fake_core(
    monkeypatch,
    *,
    vision=None,
    text=None,
    provider: str = "openai",
) -> dict:
    """偽 ``core`` パッケージ（config / llm / llm_policy）を差し込む。"""
    calls: dict = {"vision": [], "text": []}

    def default_vision(messages, image_bytes, *, model, mime_type=None):
        calls["vision"].append(
            {
                "messages": messages,
                "image_bytes": image_bytes,
                "model": model,
                "mime_type": mime_type,
            }
        )
        return '{"latex": "x = 1"}'

    def default_text(**kwargs):
        calls["text"].append(kwargs)
        return '{"latex": null}'

    core = ModuleType("core")
    config = ModuleType("core.config")
    config.get_settings = lambda: SimpleNamespace(
        llm_provider=provider, llm_analysis_model="analysis-tier-model"
    )
    llm = ModuleType("core.llm")
    llm.generate_json_with_image = vision or default_vision
    llm.generate_text = text or default_text
    llm.max_tokens_for_model = lambda model: 12000
    policy = ModuleType("core.llm_policy")
    policy.SCENE_PIPELINE = "pipeline"
    policy.resolve_scene_model = lambda feature: SimpleNamespace(model="policy-model")

    core.config = config
    core.llm = llm
    core.llm_policy = policy
    for name, module in (
        ("core", core),
        ("core.config", config),
        ("core.llm", llm),
        ("core.llm_policy", policy),
    ):
        monkeypatch.setitem(sys.modules, name, module)
    return calls


class TestVisionGoesThroughCoreLlm:
    def test_vision_call_is_delegated_with_decoded_image_and_resolved_model(self, monkeypatch):
        calls = _install_fake_core(monkeypatch)

        out = EquationSemanticsLLMClient().generate(list(_MESSAGES), image=dict(_IMAGE))

        assert len(calls["vision"]) == 1
        assert calls["text"] == []  # フォールバックは走らない
        call = calls["vision"][0]
        assert call["image_bytes"] == _IMAGE_BYTES  # base64 はここでデコードして渡す
        assert call["mime_type"] == "image/png"
        assert call["model"] == "policy-model"  # M層の解決結果（従来と同じ）
        assert [m["role"] for m in call["messages"]] == ["system", "user"]

        # パースは agent 側（truncate 復旧付きパーサ）が持つ。
        assert out["latex"] == "x = 1"
        assert out["_vision_ocr"] == {
            "attempted": True,
            "used": True,
            "provider": "openai",
            "model": "policy-model",
            "reason": None,
        }

    def test_explicit_model_wins_over_policy_resolution(self, monkeypatch):
        calls = _install_fake_core(monkeypatch)

        EquationSemanticsLLMClient(model="explicit-vision-model").generate(
            list(_MESSAGES), image=dict(_IMAGE)
        )

        assert calls["vision"][0]["model"] == "explicit-vision-model"


class TestVisionFailureFallsBackToText:
    def test_generation_failure_falls_back_to_text_context(self, monkeypatch):
        def boom(messages, image_bytes, *, model, mime_type=None):
            raise RuntimeError("provider down")

        calls = _install_fake_core(monkeypatch, vision=boom)

        out = EquationSemanticsLLMClient().generate(list(_MESSAGES), image=dict(_IMAGE))

        assert len(calls["text"]) == 1  # テキスト経路へ fail-soft
        assert out["_vision_ocr"]["used"] is False
        assert out["_vision_ocr"]["reason"] == "vision_generation_failed"
        assert out["_vision_ocr"]["provider"] == "openai"

    def test_text_fallback_never_receives_image_parts(self, monkeypatch):
        """vision 失敗時のテキスト再試行に画像パーツ入り content を流さない。"""

        def boom(messages, image_bytes, *, model, mime_type=None):
            raise RuntimeError("provider down")

        calls = _install_fake_core(monkeypatch, vision=boom)

        EquationSemanticsLLMClient().generate(list(_MESSAGES), image=dict(_IMAGE))

        fallback_messages = calls["text"][0]["messages"]
        assert all(isinstance(m["content"], str) for m in fallback_messages)

    def test_unsupported_provider_reason_is_preserved(self, monkeypatch):
        def not_implemented(messages, image_bytes, *, model, mime_type=None):
            raise NotImplementedError("no vision for this provider")

        calls = _install_fake_core(monkeypatch, vision=not_implemented, provider="gemini")

        out = EquationSemanticsLLMClient().generate(list(_MESSAGES), image=dict(_IMAGE))

        assert len(calls["text"]) == 1
        assert out["_vision_ocr"]["reason"] == "provider_has_no_equation_vision_path"

    def test_settings_unavailable_reason_is_preserved(self, monkeypatch):
        calls = _install_fake_core(monkeypatch)
        sys.modules["core.config"].get_settings = lambda: (_ for _ in ()).throw(
            RuntimeError("settings backend down")
        )

        out = EquationSemanticsLLMClient().generate(list(_MESSAGES), image=dict(_IMAGE))

        assert calls["vision"] == []
        assert len(calls["text"]) == 1
        assert out["_vision_ocr"] == {
            "attempted": True,
            "used": False,
            "provider": None,
            "model": None,
            "reason": "settings_unavailable",
        }


class TestTextPathUnchanged:
    def test_no_image_keeps_model_unset_for_core_entry_point(self, monkeypatch):
        calls = _install_fake_core(monkeypatch)

        EquationSemanticsLLMClient().generate(list(_MESSAGES))

        assert calls["vision"] == []
        assert len(calls["text"]) == 1
        assert calls["text"][0]["model"] is None


class TestNoDirectProviderSdkInClientSource:
    def test_source_has_no_provider_sdk_entry_points(self):
        import inspect

        from episteme_graph.agents.equation_semantics import llm_client as eq_mod

        src = inspect.getsource(eq_mod)
        for forbidden in (
            "chat.completions",
            "_get_openai_client",
            "_get_gemini_module",
            "_get_vertex_ai_client",
            "GenerativeModel",
            "vertexai",
        ):
            assert forbidden not in src, f"{forbidden} must not be called from the agent"
        assert "from core.llm import generate_json_with_image" in src
