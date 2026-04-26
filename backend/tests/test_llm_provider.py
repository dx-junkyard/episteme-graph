"""Issue #95 / #99: LLM プロバイダ抽象化 (OpenAI / Gemini / Google Vertex AI) のテスト。"""

from __future__ import annotations

import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel


class _DummyStruct(BaseModel):
    answer: str
    score: int


def _make_settings(provider: str, dim: int = 3072, **kwargs):
    from core.config import Settings

    gemini_like = provider in ("gemini", "google")
    return Settings(
        _env_file=None,
        llm_provider=provider,  # type: ignore[arg-type]
        llm_api_key="key-test" if provider not in ("google",) else "",
        llm_analysis_model="gemini-2.0-flash" if gemini_like else "gpt-4o",
        llm_embedding_model="text-embedding-004" if gemini_like else "text-embedding-3-large",
        llm_embedding_dim=dim,
        **kwargs,
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
        assert contents[0]["parts"] == [{"text": "hi"}]
        assert contents[1]["parts"] == [{"text": "hello"}]


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


# ---------------------------------------------------------------------------
# Issue #99: LLM_PROVIDER=google (Vertex AI ADC) テスト
# ---------------------------------------------------------------------------

class TestGoogleVertexAIProvider:
    """LLM_PROVIDER=google のルーティング・初期化テスト。"""

    def _make_fake_vertex_model(self, text: str):
        fake_resp = types.SimpleNamespace(text=text)
        fake_model = MagicMock()
        fake_model.generate_content.return_value = fake_resp
        return fake_model

    def _make_vertex_response_from_parts(self, *texts: str):
        return types.SimpleNamespace(
            candidates=[
                types.SimpleNamespace(
                    content=types.SimpleNamespace(
                        parts=[types.SimpleNamespace(text=text) for text in texts]
                    )
                )
            ]
        )

    def test_config_accepts_google_provider(self):
        from core.config import Settings

        s = Settings(
            _env_file=None,
            llm_provider="google",
            gcp_project_id="my-project",
            gcp_location="asia-northeast1",
        )
        assert s.llm_provider == "google"
        assert s.gcp_project_id == "my-project"
        assert s.gcp_location == "asia-northeast1"
        assert s.gcp_use_vertex_ai is True  # デフォルト値

    def test_config_gcp_fields_default(self):
        from core.config import Settings

        s = Settings(_env_file=None, llm_provider="google")
        assert s.gcp_project_id == ""
        assert s.gcp_location == "us-central1"
        assert s.gcp_use_vertex_ai is True

    def test_config_gcp_project_id_env_alias(self, monkeypatch):
        monkeypatch.setenv("GCP_PROJECT_ID", "proj-from-env")
        monkeypatch.setenv("GCP_LOCATION", "us-east1")
        from core.config import Settings

        s = Settings(_env_file=None)
        assert s.gcp_project_id == "proj-from-env"
        assert s.gcp_location == "us-east1"

    def test_config_gcp_backward_compat_alias(self, monkeypatch):
        """GOOGLE_CLOUD_PROJECT も gcp_project_id にマップされる。"""
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "legacy-proj")
        monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
        from core.config import Settings

        s = Settings(_env_file=None)
        assert s.gcp_project_id == "legacy-proj"

    def test_generate_text_uses_vertex_ai_when_provider_google(self):
        from core import llm

        fake_model = self._make_fake_vertex_model("vertex-response")

        fake_generative_model_cls = MagicMock(return_value=fake_model)
        fake_generation_config_cls = MagicMock()

        vertex_module = MagicMock()
        vertex_module.GenerativeModel = fake_generative_model_cls
        vertex_module.GenerationConfig = fake_generation_config_cls

        with patch.object(llm, "get_settings", return_value=_make_settings("google")), \
             patch.object(llm, "_get_vertex_ai_client", return_value=MagicMock()), \
             patch.object(llm, "_get_openai_client") as openai_client, \
             patch.object(llm, "_get_gemini_module") as gemini_mod, \
             patch("core.llm._vertex_ai_generate_text", return_value="vertex-response") as vtx_gen:
            out = llm.generate_text(
                [{"role": "user", "content": "hello"}],
                temperature=0.7,
            )
            assert out == "vertex-response"
            vtx_gen.assert_called_once()
            openai_client.assert_not_called()
            gemini_mod.assert_not_called()

    def test_generate_embeddings_uses_vertex_ai_when_provider_google(self):
        from core import llm

        with patch.object(llm, "get_settings", return_value=_make_settings("google")), \
             patch.object(llm, "_get_vertex_ai_client", return_value=MagicMock()), \
             patch("core.llm._vertex_ai_generate_embeddings", return_value=[[0.1, 0.2]]) as vtx_emb:
            out = llm.generate_embeddings(["text"])
            assert out == [[0.1, 0.2]]
            vtx_emb.assert_called_once()

    def test_generate_structured_uses_vertex_ai_when_provider_google(self):
        from core import llm

        with patch.object(llm, "get_settings", return_value=_make_settings("google")), \
             patch.object(llm, "_get_vertex_ai_client", return_value=MagicMock()), \
             patch("core.llm._vertex_ai_generate_structured",
                   return_value=_DummyStruct(answer="v", score=1)) as vtx_struct:
            out = llm.generate_text_with_structured_output(
                [{"role": "user", "content": "q"}],
                _DummyStruct,
            )
            assert isinstance(out, _DummyStruct)
            vtx_struct.assert_called_once()

    def test_extract_vertex_ai_text_joins_multiple_parts(self):
        from core.llm import _extract_vertex_ai_text

        response = self._make_vertex_response_from_parts("hello ", "world")

        assert _extract_vertex_ai_text(response) == "hello world"

    def test_vertex_ai_generate_structured_accepts_multiple_text_parts(self):
        from core import llm

        fake_model = MagicMock()
        fake_model.generate_content.return_value = self._make_vertex_response_from_parts(
            '{"answer": "hi", ',
            '"score": 7}',
        )
        fake_generative_model_cls = MagicMock(return_value=fake_model)
        fake_generation_config_cls = MagicMock()
        fake_vertex_models = types.ModuleType("vertexai.generative_models")
        fake_vertex_models.GenerativeModel = fake_generative_model_cls
        fake_vertex_models.GenerationConfig = fake_generation_config_cls

        with patch.dict("sys.modules", {"vertexai.generative_models": fake_vertex_models}):
            parsed = llm._vertex_ai_generate_structured(
                [{"role": "user", "content": "q"}],
                _DummyStruct,
                "gemini-2.5-pro",
            )

        assert parsed == _DummyStruct(answer="hi", score=7)

    def test_get_vertex_ai_client_raises_on_missing_adc(self):
        """ADC が見つからない場合は EnvironmentError を投げる。"""
        from core import llm

        # google.auth.exceptions.DefaultCredentialsError を模倣するダミー例外クラス
        class FakeDefaultCredentialsError(Exception):
            pass

        fake_vertexai = MagicMock()
        fake_vertexai.init.side_effect = FakeDefaultCredentialsError("no credentials")

        # google / google.auth / google.auth.exceptions のモジュール階層を構築
        fake_google_auth_exceptions = types.ModuleType("google.auth.exceptions")
        fake_google_auth_exceptions.DefaultCredentialsError = FakeDefaultCredentialsError

        fake_google_auth = types.ModuleType("google.auth")
        fake_google_auth.exceptions = fake_google_auth_exceptions

        fake_google = types.ModuleType("google")
        fake_google.auth = fake_google_auth

        with patch.object(llm, "get_settings", return_value=_make_settings("google")), \
             patch.dict("sys.modules", {
                 "vertexai": fake_vertexai,
                 "google": fake_google,
                 "google.auth": fake_google_auth,
                 "google.auth.exceptions": fake_google_auth_exceptions,
             }):
            llm._get_vertex_ai_client.cache_clear()
            with pytest.raises(EnvironmentError, match="Vertex AI ADC"):
                llm._get_vertex_ai_client()
            llm._get_vertex_ai_client.cache_clear()

    def test_no_api_key_required_for_google_provider(self):
        """LLM_PROVIDER=google は llm_api_key が空でも設定を受け付ける。"""
        from core.config import Settings

        s = Settings(
            _env_file=None,
            llm_provider="google",
            llm_api_key="",
            gcp_project_id="my-project",
        )
        assert s.llm_provider == "google"
        assert s.llm_api_key == ""

    def test_config_google_application_credentials_field(self, monkeypatch):
        """GOOGLE_APPLICATION_CREDENTIALS が Settings に読み込まれる。"""
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/app/.gcp/credentials.json")
        from core.config import Settings

        s = Settings(_env_file=None)
        assert s.google_application_credentials == "/app/.gcp/credentials.json"

    def test_get_vertex_ai_client_raises_when_cred_file_missing(self, tmp_path):
        """GOOGLE_APPLICATION_CREDENTIALS に存在しないパスを指定すると EnvironmentError。"""
        from core import llm
        from core.config import Settings

        missing_path = str(tmp_path / "nonexistent.json")
        settings = Settings(
            _env_file=None,
            llm_provider="google",
            google_application_credentials=missing_path,
        )

        with patch.object(llm, "get_settings", return_value=settings):
            llm._get_vertex_ai_client.cache_clear()
            with pytest.raises(EnvironmentError, match="GOOGLE_APPLICATION_CREDENTIALS"):
                llm._get_vertex_ai_client()
            llm._get_vertex_ai_client.cache_clear()

    def test_get_vertex_ai_client_sets_env_var_from_settings(self, tmp_path):
        """設定ファイルが存在する場合、os.environ に GOOGLE_APPLICATION_CREDENTIALS がセットされる。"""
        import json as _json
        from core import llm
        from core.config import Settings

        # ダミーサービスアカウント JSON を作成
        cred_file = tmp_path / "credentials.json"
        cred_file.write_text(_json.dumps({"type": "service_account"}))

        settings = Settings(
            _env_file=None,
            llm_provider="google",
            gcp_project_id="test-proj",
            google_application_credentials=str(cred_file),
        )

        fake_vertexai = MagicMock()

        # google.auth.exceptions のモック階層
        class FakeDCE(Exception):
            pass

        fake_exc_mod = types.ModuleType("google.auth.exceptions")
        fake_exc_mod.DefaultCredentialsError = FakeDCE
        fake_auth_mod = types.ModuleType("google.auth")
        fake_auth_mod.exceptions = fake_exc_mod
        fake_google_mod = types.ModuleType("google")
        fake_google_mod.auth = fake_auth_mod

        with patch.object(llm, "get_settings", return_value=settings), \
             patch.dict("sys.modules", {
                 "vertexai": fake_vertexai,
                 "google": fake_google_mod,
                 "google.auth": fake_auth_mod,
                 "google.auth.exceptions": fake_exc_mod,
             }), \
             patch.dict("os.environ", {}, clear=False):
            llm._get_vertex_ai_client.cache_clear()
            llm._get_vertex_ai_client()
            assert os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") == str(cred_file)
            fake_vertexai.init.assert_called_once_with(
                project="test-proj", location="us-central1"
            )
            llm._get_vertex_ai_client.cache_clear()
