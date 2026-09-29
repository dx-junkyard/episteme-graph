"""JSON を取り出す経路が JSON モードを要求していること（IK-0452）。

check-question の並置 / structure_anchor の帰属 / tension の抽出 / D層・R層・標準化の
worker は、JSON をプロンプト文面でしか求めておらず、provider へは
``response_format: null`` が渡っていた。共通入口（``BaseJSONLLMClient.complete_json`` /
``single_shot.json_call`` / ``structured_call`` のテキスト降格）が
``response_format={"type": "json_object"}`` を渡し、``core.llm.generate_text`` が openai
経路でそれを API に載せることを、provider 呼び出しを patch して確かめる。
"""

from __future__ import annotations

import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core import llm
from core.llm_worker import single_shot
from core.llm_worker.client import BaseJSONLLMClient

JSON_OBJECT = {"type": "json_object"}
BACKEND = Path(__file__).resolve().parents[1]


def _openai_settings():
    return types.SimpleNamespace(llm_provider="openai")


def _fake_openai(content: str = '{"ok": true}'):
    client = MagicMock()
    client.chat.completions.create.return_value = types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=content))],
        usage=None,
    )
    return client


def _create_kwargs(fake):
    return fake.chat.completions.create.call_args.kwargs


class TestGenerateTextResponseFormat:
    def test_json_object_reaches_openai_create(self):
        fake = _fake_openai()
        with patch.object(llm, "get_settings", return_value=_openai_settings()), \
             patch.object(llm, "_get_openai_client", return_value=fake):
            llm.generate_text(
                [{"role": "user", "content": "Return JSON only."}],
                model="gpt-4o-mini",
                response_format=JSON_OBJECT,
            )
        assert _create_kwargs(fake)["response_format"] == JSON_OBJECT

    def test_not_requested_means_not_sent(self):
        fake = _fake_openai()
        with patch.object(llm, "get_settings", return_value=_openai_settings()), \
             patch.object(llm, "_get_openai_client", return_value=fake):
            llm.generate_text([{"role": "user", "content": "JSON"}], model="gpt-4o-mini")
        assert "response_format" not in _create_kwargs(fake)

    def test_dropped_when_messages_do_not_mention_json(self):
        """OpenAI は "JSON" の語が無い json_object 要求を 400 にする。要求の方を落とす。"""
        fake = _fake_openai()
        with patch.object(llm, "get_settings", return_value=_openai_settings()), \
             patch.object(llm, "_get_openai_client", return_value=fake):
            llm.generate_text(
                [{"role": "user", "content": "hello"}],
                model="gpt-4o-mini",
                response_format=JSON_OBJECT,
            )
        assert "response_format" not in _create_kwargs(fake)

    def test_reasoning_model_keeps_no_temperature_and_no_system(self):
        fake = _fake_openai()
        with patch.object(llm, "get_settings", return_value=_openai_settings()), \
             patch.object(llm, "_get_openai_client", return_value=fake):
            llm.generate_text(
                [{"role": "system", "content": "s"}, {"role": "user", "content": "JSON please"}],
                model="o3-mini",
                temperature=0.1,
                response_format=JSON_OBJECT,
            )
        kwargs = _create_kwargs(fake)
        assert kwargs["response_format"] == JSON_OBJECT
        assert "temperature" not in kwargs
        assert all(m["role"] != "system" for m in kwargs["messages"])


class TestWorkerRoutesRequestJsonMode:
    """BaseJSONLLMClient を使う worker 系統（tension / structure_anchor ほか）。"""

    @pytest.mark.parametrize(
        "module_path, class_name",
        [
            ("core.tension.llm_client", "TensionLLMClient"),
            ("core.structure_anchor.llm_client", "AnchorLLMClient"),
            ("core.reconstruction.llm_client", None),
            ("core.doubt.scope_candidates.llm_client", None),
            ("core.doubt.assumption_mining.llm_client", None),
            ("core.doubt.falsification_conditions.llm_client", None),
        ],
    )
    def test_client_sends_json_object_to_provider(self, module_path, class_name):
        import importlib

        module = importlib.import_module(module_path)
        if class_name is None:
            class_name = next(
                name for name, obj in vars(module).items()
                if isinstance(obj, type) and issubclass(obj, BaseJSONLLMClient)
                and obj is not BaseJSONLLMClient
            )
        client = getattr(module, class_name)(model="gpt-4o-mini")
        fake = _fake_openai('{"items": []}')
        with patch.object(llm, "get_settings", return_value=_openai_settings()), \
             patch.object(llm, "_get_openai_client", return_value=fake):
            result = client.complete_json("Return a JSON object.")
        assert result == {"items": []}
        assert _create_kwargs(fake)["response_format"] == JSON_OBJECT

    def test_base_client_passes_json_mode(self):
        client = BaseJSONLLMClient(model_setting_key="k", model="m")
        with patch("core.llm_worker.client.generate_text", return_value="{}") as mocked:
            client.complete_json("JSON")
        assert mocked.call_args.kwargs["response_format"] == JSON_OBJECT


class TestSingleShotJsonMode:
    """check-question の並置（``api/routes/learning.py``）は json_call を経由する。"""

    def test_json_call_with_real_generate_text_sends_json_object(self):
        fake = _fake_openai('{"observations": []}')
        with patch.object(llm, "get_settings", return_value=_openai_settings()), \
             patch.object(llm, "_get_openai_client", return_value=fake):
            out = single_shot.json_call(
                "Return JSON only.", call=llm.generate_text, model="gpt-4o-mini",
                temperature=0.1, degraded=None,
            )
        assert out == {"observations": []}
        assert _create_kwargs(fake)["response_format"] == JSON_OBJECT

    def test_json_call_skips_narrow_fake(self):
        seen = {}

        def fake(messages, model=None):
            seen["called"] = True
            return "{}"

        assert single_shot.json_call("JSON", call=fake, model="m") == {}
        assert seen["called"]

    def test_json_call_can_opt_out(self):
        mocked = MagicMock(return_value="{}")
        single_shot.json_call("JSON", call=mocked, json_mode=False)
        assert "response_format" not in mocked.call_args.kwargs

    def test_structured_call_text_fallback_requests_json_mode(self):
        def boom(**kwargs):
            raise RuntimeError("schema unsupported")

        text = MagicMock(return_value='{"a": 1}')
        out = single_shot.structured_call(
            "JSON", object, structured_fn=boom, text_fn=text, text_fallback=True,
        )
        assert out == {"a": 1}
        assert text.call_args.kwargs["response_format"] == JSON_OBJECT


def test_json_routes_do_not_opt_out_of_json_mode():
    """JSON を取り出す主要経路が ``json_mode=False`` で JSON モードを外していない。"""
    for rel in ("api/routes/learning.py", "api/services.py", "core/simulator.py"):
        source = (BACKEND / rel).read_text(encoding="utf-8")
        assert "json_mode=False" not in source, rel
