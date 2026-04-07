"""Issue #95: LLM プロバイダ抽象化 (OpenAI / Gemini) のテスト。"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel


class _DummyStruct(BaseModel):
    answer: str
    score: int


def _make_settings(provider: str, dim: int = 3072):
    from core.config import Settings

    return Settings(
        _env_file=None,
        llm_provider=provider,  # type: ignore[arg-type]
        llm_api_key="key-test",
        llm_analysis_model="gemini-2.0-flash" if provider == "gemini" else "gpt-4o",
        llm_embedding_model="text-embedding-004" if provider == "gemini" else "text-embedding-3-large",
        llm_embedding_dim=dim,
    )


class TestProviderConfig:
    def test_default_provider_is_openai(self):
        from core.config import Settings

        s = Settings(_env_file=None, llm_api_key="sk-test")
        assert s.llm_provider == "openai"
        assert s.llm_embedding_dim == 3072

    def test_provider_env_switch(self):
        from core.config import Settings

        s = Settings(_env_file=None, llm_provider="gemini", llm_api_key="g-test")
        assert s.llm_provider == "gemini"

    def test_gemini_api_key_alias(self, monkeypatch):
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("GEMINI_API_KEY", "g-alias")
        from core.config import Settings

        s = Settings(_env_file=None)
        assert s.llm_api_key == "g-alias"

    def test_embedding_dim_override(self):
        from core.config import Settings

        s = Settings(_env_file=None, llm_api_key="k", llm_embedding_dim=768)
        assert s.llm_embedding_dim == 768


class TestEmbeddingDimHelper:
    def test_get_embedding_dim_reads_settings(self):
        from core import llm

        with patch.object(llm, "get_settings", return_value=_make_settings("gemini", dim=768)):
            assert llm.get_embedding_dim() == 768


class TestMessageRoleMapping:
    def test_system_user_assistant_mapped_to_gemini(self):
        from core.llm import _messages_to_gemini

        system, contents = _messages_to_gemini([
            {"role": "system", "content": "sys1"},
            {"role": "system", "content": "sys2"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
            {"role": "user", "content": "again"},
        ])
        assert system == "sys1\n\nsys2"
        assert [m["role"] for m in contents] == ["user", "model", "user"]
        assert contents[0]["parts"] == ["hi"]
        assert contents[1]["parts"] == ["hello"]


class TestProviderBranching:
    def test_generate_text_uses_gemini_when_provider_gemini(self):
        from core import llm

        fake_resp = types.SimpleNamespace(text="gemini-response")
        fake_model = MagicMock()
        fake_model.generate_content.return_value = fake_resp
        fake_genai = MagicMock()
        fake_genai.GenerativeModel.return_value = fake_model

        with patch.object(llm, "get_settings", return_value=_make_settings("gemini")), \
             patch.object(llm, "_get_gemini_module", return_value=fake_genai), \
             patch.object(llm, "_get_openai_client") as openai_client:
            out = llm.generate_text(
                [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
                temperature=0.5,
                max_tokens=128,
            )
            assert out == "gemini-response"
            openai_client.assert_not_called()
            fake_genai.GenerativeModel.assert_called_once()
            kwargs = fake_genai.GenerativeModel.call_args.kwargs
            assert kwargs["model_name"] == "gemini-2.0-flash"
            assert kwargs["system_instruction"] == "s"
            gen_kwargs = fake_model.generate_content.call_args.kwargs
            assert gen_kwargs["generation_config"]["temperature"] == 0.5
            assert gen_kwargs["generation_config"]["max_output_tokens"] == 128

    def test_generate_text_uses_openai_when_provider_openai(self):
        from core import llm

        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="openai-response"))]
        )
        with patch.object(llm, "get_settings", return_value=_make_settings("openai")), \
             patch.object(llm, "_get_openai_client", return_value=fake_client), \
             patch.object(llm, "_get_gemini_module") as gemini_mod:
            out = llm.generate_text([{"role": "user", "content": "hi"}])
            assert out == "openai-response"
            gemini_mod.assert_not_called()

    def test_generate_embeddings_uses_gemini(self):
        from core import llm

        fake_genai = MagicMock()
        fake_genai.embed_content.side_effect = [
            {"embedding": [0.1, 0.2, 0.3]},
            {"embedding": {"values": [0.4, 0.5, 0.6]}},
        ]
        with patch.object(llm, "get_settings", return_value=_make_settings("gemini", dim=3)), \
             patch.object(llm, "_get_gemini_module", return_value=fake_genai):
            out = llm.generate_embeddings(["a", "b"])
            assert out == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
            assert fake_genai.embed_content.call_count == 2

    def test_generate_structured_uses_gemini(self):
        from core import llm

        fake_resp = types.SimpleNamespace(text='{"answer": "hi", "score": 7}')
        fake_model = MagicMock()
        fake_model.generate_content.return_value = fake_resp
        fake_genai = MagicMock()
        fake_genai.GenerativeModel.return_value = fake_model

        with patch.object(llm, "get_settings", return_value=_make_settings("gemini")), \
             patch.object(llm, "_get_gemini_module", return_value=fake_genai):
            parsed = llm.generate_text_with_structured_output(
                [{"role": "user", "content": "q"}],
                _DummyStruct,
            )
            assert isinstance(parsed, _DummyStruct)
            assert parsed.answer == "hi"
            assert parsed.score == 7
