"""解析 run の生成言語（``document_analysis_runs.options.language``）。

正本: ``docs/features/discuss_opening_authoring_design.md`` §14（言語の run オプション）。

守る性質:

- 入口（multipart アップロード / URL 取得 / arXiv 取り込み / 再解析）が ``language``
  （``ja`` / ``en``）を run options に通す。語彙外は 422。未指定はキーを入れない。
- 再解析は ``None`` で前回 run の ``language`` を引き継ぐ（``models`` と同じ流儀）。
- orchestrator は run options を env より優先して discuss_opening に渡し、
  contextual_explanation には指定があるときだけ言語を渡す（未指定の prompt は従来と逐語で同じ）。
- **LLM 呼び出しを増やさない**（同じ 1 コールの指示が変わるだけ）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.document_pipeline import orchestrator as orch  # noqa: E402

ORCH_SRC = (BACKEND / "core" / "document_pipeline" / "orchestrator.py").read_text(encoding="utf-8")


class TestRunLanguageResolution:
    @pytest.mark.parametrize("options,expected", [
        ({"language": "en"}, "en"),
        ({"language": "JA"}, "ja"),
        ({"language": "fr"}, None),
        ({"language": ""}, None),
        ({}, None),
        (None, None),
    ])
    def test_run_generation_language(self, options, expected):
        assert orch._run_generation_language(options) == expected  # noqa: SLF001

    def test_run_option_wins_over_env(self, monkeypatch):
        monkeypatch.setenv("DISCUSS_OPENING_LANGUAGE", "ja")
        assert orch._discuss_opening_language("en") == "en"  # noqa: SLF001

    def test_none_keeps_env(self, monkeypatch):
        monkeypatch.setenv("DISCUSS_OPENING_LANGUAGE", "en")
        assert orch._discuss_opening_language(None) == "en"  # noqa: SLF001
        monkeypatch.delenv("DISCUSS_OPENING_LANGUAGE", raising=False)
        assert orch._discuss_opening_language() == "ja"  # noqa: SLF001

    def test_stages_thread_the_run_option(self):
        from tests.guardrail_helpers import extract_function_source

        discuss = extract_function_source(ORCH_SRC, "_stage_discuss_opening")
        assert "language=_run_generation_language(ctx.effective_options)" in discuss
        ctxexpl = extract_function_source(ORCH_SRC, "_build_contextual_explanation")
        assert "_run_generation_language(effective_options)" in ctxexpl


class TestContextualExplanationPrompt:
    def _system(self, language):
        from episteme_graph.agents.contextual_explanation.prompt import (
            ContextualExplanationPromptFactory,
        )

        return ContextualExplanationPromptFactory(language=language)._system_content()  # noqa: SLF001

    def test_default_keeps_the_match_source_instruction_verbatim(self):
        text = self._system(None)
        assert "match whatever language the source material uses." in text
        assert "Write contextual_explanation / generic_explanation / reason in the SAME" in text

    def test_fixed_language_replaces_the_instruction(self):
        text = self._system("en")
        assert "in English," in text
        assert "match whatever language the source material uses." not in text
        # 逐語引用は翻訳させない（verbatim 検査を壊さない）。
        assert "never translate it" in text

    def test_unknown_language_falls_back_to_the_default(self):
        assert self._system("fr") == self._system(None)

    def test_agent_accepts_language_without_extra_calls(self):
        from episteme_graph.agents.contextual_explanation.agent import ContextualExplanationAgent

        agent = ContextualExplanationAgent(language="ja")
        assert "Japanese" in agent._prompt_factory._system_content()  # noqa: SLF001


class TestDiscussOpeningStageLanguage:
    def test_build_discuss_opening_accepts_language(self):
        import inspect

        sig = inspect.signature(orch._build_discuss_opening)  # noqa: SLF001
        assert "language" in sig.parameters
        assert sig.parameters["language"].default is None




class TestUploadEntryPoints:
    @pytest.fixture()
    def env(self, monkeypatch):
        pytest.importorskip("fastapi")
        from fastapi.testclient import TestClient

        from api.main import app
        from dependencies import ROLE_TEACHER, _create_token
        import routes.admin as admin_routes

        accepted: list[dict] = []

        def _accept(**kwargs):
            accepted.append(kwargs)
            return {
                "task_id": "t1", "material_id": "m1", "filename": kwargs["filename"],
                "title": "x", "source_kind": kwargs["source_kind"], "status": "pending",
                "uploaded_at": "2026-09-30T00:00:00",
                "analyze_images": bool(kwargs["analyze_images"]),
            }

        monkeypatch.setattr(admin_routes, "_accept_material_source", _accept)
        token = _create_token(
            "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "kyoin", "kyoin@x", ROLE_TEACHER
        )
        return {
            "client": TestClient(app),
            "headers": {"Authorization": f"Bearer {token}"},
            "accepted": accepted,
        }

    def _post(self, env, data):
        return env["client"].post(
            "/api/admin/materials/upload",
            files={"file": ("p.pdf", b"%PDF-1.7\nhello", "application/pdf")},
            data=data,
            headers=env["headers"],
        )

    def test_multipart_language_reaches_the_acceptance(self, env):
        resp = self._post(env, {"language": "en"})
        assert resp.status_code == 202, resp.text
        assert env["accepted"][0]["language"] == "en"

    def test_multipart_omitted_language_is_none(self, env):
        resp = self._post(env, {})
        assert resp.status_code == 202, resp.text
        assert env["accepted"][0]["language"] is None

    def test_multipart_unknown_language_is_422(self, env):
        resp = self._post(env, {"language": "de"})
        assert resp.status_code == 422
        assert env["accepted"] == []


class TestAcceptStoresLanguageInOptions:
    def _capture(self, monkeypatch, language):
        pytest.importorskip("fastapi")
        from unittest.mock import MagicMock

        import routes.admin as admin_mod

        captured: dict = {}

        class _FakeThread:
            def __init__(self, *args, **kwargs):
                captured.update(kwargs)

            def start(self):
                pass

        monkeypatch.setattr(admin_mod, "get_storage_client", lambda: MagicMock())
        monkeypatch.setattr(admin_mod, "_pg_session", lambda: MagicMock())
        monkeypatch.setattr(admin_mod, "create_background_task", lambda *a, **k: None)
        monkeypatch.setattr(admin_mod.threading, "Thread", _FakeThread)
        admin_mod._accept_material_source(  # noqa: SLF001
            source_bytes=b"%PDF-1.7", filename="p.pdf", source_kind="pdf",
            analyze_images=False, models_option=None,
            current_user={"id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"},
            language=language,
        )
        return captured["kwargs"]["options"]

    def test_language_is_stored(self, monkeypatch):
        assert self._capture(monkeypatch, "en") == {"analyze_images": False, "language": "en"}

    def test_unset_language_adds_no_key(self, monkeypatch):
        assert self._capture(monkeypatch, None) == {"analyze_images": False}


class TestReanalyzeLanguage:
    def test_explicit_language_overrides_and_keeps_the_rest(self, monkeypatch):
        pytest.importorskip("fastapi")
        import routes.admin as admin_mod
        from tests.test_llm_model_policy_api import _reanalyze_capture

        options = _reanalyze_capture(
            monkeypatch,
            body=admin_mod.ReanalyzeRequest(language="en"),
            previous_options={"analyze_images": True, "models": {"pipeline": "m"}, "language": "ja"},
        )
        assert options == {"analyze_images": True, "models": {"pipeline": "m"}, "language": "en"}

    def test_unspecified_language_inherits_the_previous_run(self, monkeypatch):
        pytest.importorskip("fastapi")
        import routes.admin as admin_mod
        from tests.test_llm_model_policy_api import _reanalyze_capture

        options = _reanalyze_capture(
            monkeypatch,
            body=admin_mod.ReanalyzeRequest(analyze_images=False),
            previous_options={"analyze_images": True, "language": "en"},
        )
        assert options == {"analyze_images": False, "language": "en"}

    def test_nothing_specified_defers_to_orchestrator_inheritance(self, monkeypatch):
        pytest.importorskip("fastapi")
        import routes.admin as admin_mod
        from tests.test_llm_model_policy_api import _reanalyze_capture

        options = _reanalyze_capture(
            monkeypatch,
            body=admin_mod.ReanalyzeRequest(),
            previous_options={"language": "en"},
        )
        assert options is None

    def test_empty_string_resets_to_unspecified(self, monkeypatch):
        """``""`` は「指定しない」への解除（cartridge_id と同じ約束 — レビュー m6）。"""
        pytest.importorskip("fastapi")
        import routes.admin as admin_mod
        from tests.test_llm_model_policy_api import _reanalyze_capture

        options = _reanalyze_capture(
            monkeypatch,
            body=admin_mod.ReanalyzeRequest(language=""),
            previous_options={"analyze_images": True, "models": {"pipeline": "m"}, "language": "en"},
        )
        assert options == {"analyze_images": True, "models": {"pipeline": "m"}}

    def test_unknown_language_is_rejected_by_the_model(self):
        pytest.importorskip("fastapi")
        import pydantic

        import routes.admin as admin_mod

        with pytest.raises(pydantic.ValidationError):
            admin_mod.ReanalyzeRequest(language="fr")


class TestArxivIngestLanguage:
    def test_ingest_request_has_language(self):
        pytest.importorskip("fastapi")
        import routes.paper_discovery as pd_routes

        assert pd_routes.IngestRequest(language="en").language == "en"
        assert pd_routes.IngestRequest().language is None

    def test_ingest_passes_language_to_the_acceptance(self):
        from tests.guardrail_helpers import extract_function_source

        src = (BACKEND / "api" / "routes" / "paper_discovery.py").read_text(encoding="utf-8")
        body = extract_function_source(src, "ingest_candidates")
        assert "language=language_option" in body
