"""M層（LLM モデル選択）レビュー指摘 C1 / m4 / m6 / m7 の回帰テスト。

正本: docs/features/llm_model_selection_design.md（§3 解決順序 / §10 実装記録）。

既存 ``test_pipeline_model_override.py`` は fake step が **orchestrator スレッド内で**
``resolve_scene_model`` を呼ぶため、agent の実クライアント
（``ProviderJSONLLMClient``）がウォールタイムアウト用の
``threading.Thread`` を起こす経路を通っていなかった。contextvars はスレッドを
またがないため、実運用では

  * M層の ``model_override``（run override / user・system ポリシー）が
    paper_skeleton 以下の主要ステージ全部に届かず ``LLM_ANALYSIS_MODEL`` 固定になり、
  * U層の帰属が ``feature='unattributed'`` になり、
  * それでも ``_stage_models`` には「選ばれたはずのモデル」が記録されて**嘘になる**

という3点が同時に起きていた（C1）。ここでは**実クライアント経由**で
(a) override が効く (b) 帰属がスレッド内へ伝搬する (c) ``_stage_models`` の記録値が
実際に generate に渡ったモデルと一致する、を固定する。

あわせて m4（pipeline の ``usage_context`` に user_id をバインドし
``scope='user'`` のポリシー行を有効にする）、m6（``_ctxexpl_model`` の env 直読み
撤去 = M1）、m7（equation_semantics vision の settings 直読み撤去）を検証する。

DB は一切使わない（``upsert_analysis_run`` / ``get_latest_analysis_run`` を
monkeypatch する ``test_pipeline_model_override.py`` と同型）。
"""
from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from core.config import get_settings as _real_get_settings  # noqa: E402
import core.llm_policy as llm_policy  # noqa: E402
from core.llm_usage.context import current_usage_context  # noqa: E402
from core.document_pipeline import orchestrator  # noqa: E402

from episteme_graph.agents import llm_json_client as agent_client_mod  # noqa: E402
from episteme_graph.agents.llm_json_client import ProviderJSONLLMClient  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_policy_and_settings():
    yield
    llm_policy.reset_policy_backend_for_tests()
    _real_get_settings.cache_clear()


class _FakeBackend:
    """test_llm_policy.py の _FakeBackend と同型（user_id 有無で行を出し分ける）。"""

    def __init__(self, *, system=None, user=None):
        self._system = system
        self._user = user

    def lookup(self, scene_key, feature, user_id):
        if user_id is not None:
            return self._user
        return self._system


# ===========================================================================
# 0. 構造ガードレール — スレッド起動が contextvars を持ち込むこと
# ===========================================================================


class TestWallTimeoutThreadCopiesContext:
    def test_call_with_wall_timeout_uses_copy_context(self):
        """``threading.Thread`` を素で起こすと contextvars を落とす（C1）。

        実装の形（``contextvars.copy_context()`` + ``ctx.run``）を構造で固定し、
        「target=target」への回帰を防ぐ。
        """
        source = inspect.getsource(agent_client_mod._call_with_wall_timeout)
        assert "contextvars.copy_context()" in source
        assert "ctx.run(" in source
        assert "target=target" not in source

    def test_context_value_is_visible_inside_worker_thread(self):
        """contextvar の値が実際にスレッド内へ届く（純ユニット・core 非依存）。"""
        import contextvars
        import threading

        probe: contextvars.ContextVar[str] = contextvars.ContextVar(
            "wall_timeout_probe", default="unset"
        )
        seen: dict = {}

        def inner():
            seen["value"] = probe.get()
            seen["thread"] = threading.current_thread().name
            return "done"

        token = probe.set("carried")
        try:
            outer_thread = threading.current_thread().name
            result = agent_client_mod._call_with_wall_timeout(inner, 10.0)
        finally:
            probe.reset(token)

        assert result == "done"
        assert seen["value"] == "carried"
        # 本当に別スレッドで動いている（copy_context が効いていることの意味がある）。
        assert seen["thread"] != outer_thread

    def test_worker_thread_cannot_leak_context_writes_back(self):
        """copy_context のコピーなので、スレッド内の set は呼び出し元へ漏れない。"""
        import contextvars

        probe: contextvars.ContextVar[str] = contextvars.ContextVar(
            "wall_timeout_probe_leak", default="unset"
        )

        def inner():
            probe.set("written-in-thread")
            return probe.get()

        token = probe.set("caller")
        try:
            assert agent_client_mod._call_with_wall_timeout(inner, 10.0) == "written-in-thread"
            assert probe.get() == "caller"
        finally:
            probe.reset(token)


# ===========================================================================
# 1. 実クライアント経由の伝搬（model_override + usage_context）
# ===========================================================================


def _stub_core_generate_text(calls: list[dict]):
    """``core.llm.generate_text`` の model 解決規約を模した stub を返す。

    実物（core/llm.py: ``model is None`` なら
    ``resolve_scene_model(current_usage_context().feature)``）と同じ分岐だけを持つ。
    """

    def fake_generate_text(messages, *, model=None, **kwargs):
        ctx = current_usage_context()
        if model is not None:
            used = model
            source = llm_policy.SOURCE_CALL_ARGUMENT
        else:
            resolved = llm_policy.resolve_scene_model(ctx.feature)
            used = resolved.model
            source = resolved.source
        calls.append(
            {
                "model": used,
                "source": source,
                "feature": ctx.feature,
                "user_id": ctx.user_id,
                "document_id": ctx.document_id,
                "run_id": ctx.run_id,
            }
        )
        return json.dumps({"ok": True})

    return fake_generate_text


class TestRealClientContextPropagation:
    def test_model_override_reaches_generate_text_through_worker_thread(self, monkeypatch):
        monkeypatch.setenv("LLM_ANALYSIS_MODEL", "env-analysis-model")
        _real_get_settings.cache_clear()
        calls: list[dict] = []
        monkeypatch.setattr(
            "core.llm.generate_text", _stub_core_generate_text(calls), raising=True
        )

        from core.llm_usage.context import usage_context

        with usage_context("pipeline:paper_skeleton", document_id="doc-1"):
            with llm_policy.model_override(
                "gpt-5.4-mini", source=llm_policy.SOURCE_RUN_OVERRIDE
            ):
                result = ProviderJSONLLMClient().generate(
                    [{"role": "user", "content": "return json"}]
                )

        assert result == {"ok": True}
        assert calls[0]["model"] == "gpt-5.4-mini"
        assert calls[0]["source"] == llm_policy.SOURCE_RUN_OVERRIDE

    def test_usage_context_feature_and_ids_reach_worker_thread(self, monkeypatch):
        calls: list[dict] = []
        monkeypatch.setattr(
            "core.llm.generate_text", _stub_core_generate_text(calls), raising=True
        )

        from core.llm_usage.context import usage_context

        with usage_context(
            "pipeline:claim_qualification",
            user_id="teacher-9",
            document_id="doc-7",
            run_id="run-7",
        ):
            ProviderJSONLLMClient().generate([{"role": "user", "content": "x"}])

        assert calls[0]["feature"] == "pipeline:claim_qualification"
        assert calls[0]["user_id"] == "teacher-9"
        assert calls[0]["document_id"] == "doc-7"
        assert calls[0]["run_id"] == "run-7"

    def test_user_policy_row_resolves_inside_worker_thread(self, monkeypatch):
        """解決層③（scope='user'）も contextvars 経由なのでスレッド越えが必要。"""
        monkeypatch.setenv("LLM_ANALYSIS_MODEL", "env-analysis-model")
        _real_get_settings.cache_clear()
        llm_policy.set_policy_backend(
            _FakeBackend(
                system=llm_policy.PolicyRow(model="system-model", scope="system"),
                user=llm_policy.PolicyRow(model="user-model", scope="user"),
            )
        )
        calls: list[dict] = []
        monkeypatch.setattr(
            "core.llm.generate_text", _stub_core_generate_text(calls), raising=True
        )

        from core.llm_usage.context import usage_context

        with usage_context("pipeline:rhetorical_role", user_id="teacher-1"):
            ProviderJSONLLMClient().generate([{"role": "user", "content": "x"}])

        assert calls[0]["model"] == "user-model"
        assert calls[0]["source"] == llm_policy.SOURCE_USER_POLICY

    def test_explicit_client_model_still_wins(self, monkeypatch):
        """明示引数（解決順①）の意味は変えない。"""
        calls: list[dict] = []
        monkeypatch.setattr(
            "core.llm.generate_text", _stub_core_generate_text(calls), raising=True
        )
        with llm_policy.model_override("override-model"):
            ProviderJSONLLMClient(model="explicit-model").generate(
                [{"role": "user", "content": "x"}]
            )
        assert calls[0]["model"] == "explicit-model"


# ===========================================================================
# 2. orchestrator 統合 — 実クライアントで走らせ _stage_models の正直さを固定
# ===========================================================================


def _run_pipeline(*, options, steps, latest_run=None, user_id=None):
    upserts: list[dict] = []

    def fake_upsert(*, run_id=None, document_id, material_id, cartridge_id=None,
                    status="running", current_stage=None, stage_outputs=None,
                    error_message="", options=None):
        upserts.append({
            "current_stage": current_stage,
            "stage_outputs": stage_outputs,
            "status": status,
        })
        return run_id or "run-new"

    with patch.object(orchestrator, "get_latest_analysis_run", return_value=latest_run), \
         patch.object(orchestrator, "upsert_analysis_run", side_effect=fake_upsert), \
         patch.object(orchestrator, "_PIPELINE_STEPS", steps):
        orchestrator.run_document_pipeline(
            pdf_bytes=b"%PDF-1.4",
            document_id="doc-1",
            material_id="mat-1",
            user_id=user_id,
            resume=True,
            options=options,
        )
    return upserts


def _last_stage_models(upserts: list[dict]) -> dict | None:
    for entry in reversed(upserts):
        stage_outputs = entry.get("stage_outputs") or {}
        if orchestrator.STAGE_MODELS_KEY in stage_outputs:
            return stage_outputs[orchestrator.STAGE_MODELS_KEY]
    return None


def _real_client_step(stage: str, calls: list[dict]):
    """実 ``ProviderJSONLLMClient`` を1回叩いて artifact を残す fake ステージ。

    実ステージと同じく先頭で ``report_start`` を呼ぶ（U層の feature が
    ``pipeline:<stage>`` へ差し替わるのはこの呼び出しの副作用）。
    """

    def step(ctx):
        ctx.report_start(stage)
        ProviderJSONLLMClient().generate([{"role": "user", "content": "x"}])
        ctx.save_artifact(stage, {"status": "completed", "llm_calls": len(calls)})
        return True

    return orchestrator.PipelineStageDef(stage, step)


class TestOrchestratorIntegrationWithRealClient:
    def test_run_override_reaches_agent_client_and_stage_models_is_truthful(self, monkeypatch):
        monkeypatch.setenv("LLM_ANALYSIS_MODEL", "env-analysis-model")
        _real_get_settings.cache_clear()
        calls: list[dict] = []
        monkeypatch.setattr(
            "core.llm.generate_text", _stub_core_generate_text(calls), raising=True
        )

        upserts = _run_pipeline(
            options={"models": {"pipeline:paper_skeleton": "gpt-5.2"}},
            steps=[_real_client_step("paper_skeleton", calls)],
        )

        assert len(calls) == 1
        assert calls[0]["model"] == "gpt-5.2"
        assert calls[0]["feature"] == "pipeline:paper_skeleton"
        # M7: 記録値は実際に使われたモデルと一致しなければならない（嘘を書かない）。
        assert _last_stage_models(upserts) == {"paper_skeleton": calls[0]["model"]}

    def test_without_override_stage_models_matches_actual_default(self, monkeypatch):
        monkeypatch.setenv("LLM_ANALYSIS_MODEL", "env-analysis-model")
        _real_get_settings.cache_clear()
        calls: list[dict] = []
        monkeypatch.setattr(
            "core.llm.generate_text", _stub_core_generate_text(calls), raising=True
        )

        upserts = _run_pipeline(
            options=None,
            steps=[_real_client_step("paper_skeleton", calls)],
            latest_run=None,
        )

        assert calls[0]["model"] == "env-analysis-model"
        assert _last_stage_models(upserts) == {"paper_skeleton": "env-analysis-model"}

    def test_pipeline_user_id_enables_user_scope_policy(self, monkeypatch):
        """m4: user_id を渡すと解決層③（scope='user'）に到達する。"""
        monkeypatch.setenv("LLM_ANALYSIS_MODEL", "env-analysis-model")
        _real_get_settings.cache_clear()
        llm_policy.set_policy_backend(
            _FakeBackend(
                system=llm_policy.PolicyRow(model="system-model", scope="system"),
                user=llm_policy.PolicyRow(model="user-model", scope="user"),
            )
        )
        calls: list[dict] = []
        monkeypatch.setattr(
            "core.llm.generate_text", _stub_core_generate_text(calls), raising=True
        )

        upserts = _run_pipeline(
            options=None,
            steps=[_real_client_step("paper_skeleton", calls)],
            user_id="teacher-42",
        )

        assert calls[0]["user_id"] == "teacher-42"
        assert calls[0]["model"] == "user-model"
        assert calls[0]["source"] == llm_policy.SOURCE_USER_POLICY
        assert _last_stage_models(upserts) == {"paper_skeleton": "user-model"}

    def test_pipeline_without_user_id_falls_back_to_system_policy(self, monkeypatch):
        """後方互換: user_id 未指定なら従来どおり system 行 → env → tier。"""
        monkeypatch.setenv("LLM_ANALYSIS_MODEL", "env-analysis-model")
        _real_get_settings.cache_clear()
        llm_policy.set_policy_backend(
            _FakeBackend(
                system=llm_policy.PolicyRow(model="system-model", scope="system"),
                user=llm_policy.PolicyRow(model="user-model", scope="user"),
            )
        )
        calls: list[dict] = []
        monkeypatch.setattr(
            "core.llm.generate_text", _stub_core_generate_text(calls), raising=True
        )

        _run_pipeline(options=None, steps=[_real_client_step("paper_skeleton", calls)])

        assert calls[0]["user_id"] is None
        assert calls[0]["model"] == "system-model"

    def test_run_document_pipeline_accepts_user_id_keyword(self):
        sig = inspect.signature(orchestrator.run_document_pipeline)
        assert "user_id" in sig.parameters
        assert sig.parameters["user_id"].default is None

    def test_bind_usage_context_call_passes_user_id(self):
        """帰属 bind から user_id を落とす回帰を構造で防ぐ。"""
        source = inspect.getsource(orchestrator.run_document_pipeline)
        bind_call = source[source.index("bind_usage_context("):]
        bind_call = bind_call[: bind_call.index(")")]
        assert "user_id=" in bind_call


class TestLectureStudioPipelineWorkersPassUserId:
    """道具立て（MinIO / threading）が重いので呼び出し形を構造で固定する。

    ``test_document_pipeline.py`` が ``services.py`` の呼び出し形を本文検査で
    固定しているのと同じ流儀。
    """

    def test_both_workers_forward_user_id_to_run_document_pipeline(self):
        path = BACKEND_DIR / "api" / "routes" / "lecture_studio" / "pipeline.py"
        source = path.read_text(encoding="utf-8")
        call_sites = [
            source[idx:idx + 900]
            for idx in _find_all(source, "run_document_pipeline(\n")
        ]
        assert len(call_sites) == 2, "material 用 / course 用の2箇所を想定"
        for call in call_sites:
            body = call[: call.index("\n        )") if "\n        )" in call else len(call)]
            assert "user_id=user_id" in body


def _find_all(haystack: str, needle: str) -> list[int]:
    out: list[int] = []
    start = 0
    while True:
        idx = haystack.find(needle, start)
        if idx < 0:
            return out
        out.append(idx)
        start = idx + 1


# ===========================================================================
# 3. m6 — _ctxexpl_model が llm_policy へ委譲する（M1: env 直読み禁止）
# ===========================================================================


class TestContextualExplanationModelResolution:
    def test_does_not_read_env_directly(self):
        source = inspect.getsource(orchestrator._ctxexpl_model)
        assert "os.getenv" not in source
        assert "resolve_scene_model" in source

    def test_env_only_environment_resolves_identically(self, monkeypatch):
        """挙動不変: CTXEXPL_LLM_MODEL のみ設定 → その値。"""
        monkeypatch.setenv("CTXEXPL_LLM_MODEL", "ctxexpl-env-model")
        _real_get_settings.cache_clear()
        assert orchestrator._ctxexpl_model() == "ctxexpl-env-model"
        assert orchestrator._ctxexpl_model({}) == "ctxexpl-env-model"

    def test_unset_env_falls_back_to_fast_tier(self, monkeypatch):
        monkeypatch.delenv("CTXEXPL_LLM_MODEL", raising=False)
        monkeypatch.setenv("LLM_FAST_MODEL", "fast-tier-model")
        _real_get_settings.cache_clear()
        assert orchestrator._ctxexpl_model() == "fast-tier-model"

    def test_run_option_override_still_wins(self, monkeypatch):
        monkeypatch.setenv("CTXEXPL_LLM_MODEL", "ctxexpl-env-model")
        _real_get_settings.cache_clear()
        options = {"models": {"pipeline:contextual_explanation": "run-model"}}
        assert orchestrator._ctxexpl_model(options) == "run-model"
        assert orchestrator._ctxexpl_model({"models": {"pipeline": "generic"}}) == "generic"

    def test_user_policy_now_wins_over_env(self, monkeypatch):
        """m6 の主目的: DB ポリシー層を素通ししない。"""
        monkeypatch.setenv("CTXEXPL_LLM_MODEL", "ctxexpl-env-model")
        _real_get_settings.cache_clear()
        llm_policy.set_policy_backend(
            _FakeBackend(user=llm_policy.PolicyRow(model="user-model", scope="user"))
        )
        from core.llm_usage.context import usage_context

        with usage_context("pipeline:contextual_explanation", user_id="teacher-1"):
            assert orchestrator._ctxexpl_model() == "user-model"

    def test_model_override_context_is_honored(self, monkeypatch):
        monkeypatch.setenv("CTXEXPL_LLM_MODEL", "ctxexpl-env-model")
        _real_get_settings.cache_clear()
        with llm_policy.model_override("stage-override", source=llm_policy.SOURCE_RUN_OVERRIDE):
            assert orchestrator._ctxexpl_model() == "stage-override"


# ===========================================================================
# 4. m7 — equation_semantics の vision 分岐が settings を直読みしない
# ===========================================================================


class TestEquationSemanticsVisionModelResolution:
    def test_client_source_does_not_read_settings_analysis_model_inline(self):
        from episteme_graph.agents.equation_semantics import llm_client as eq_mod

        generate_source = inspect.getsource(eq_mod.EquationSemanticsLLMClient.generate)
        assert "settings.llm_analysis_model" not in generate_source
        assert "_resolve_vision_model" in generate_source

    def test_fallback_matches_previous_analysis_tier_behaviour(self, monkeypatch):
        from episteme_graph.agents.equation_semantics import llm_client as eq_mod

        monkeypatch.setenv("LLM_ANALYSIS_MODEL", "env-analysis-model")
        _real_get_settings.cache_clear()
        settings = _real_get_settings()
        assert eq_mod._resolve_vision_model(settings) == settings.llm_analysis_model

    def test_run_override_is_honored_for_vision_model(self, monkeypatch):
        from episteme_graph.agents.equation_semantics import llm_client as eq_mod

        monkeypatch.setenv("LLM_ANALYSIS_MODEL", "env-analysis-model")
        _real_get_settings.cache_clear()
        settings = _real_get_settings()
        with llm_policy.model_override("vision-run-model", source=llm_policy.SOURCE_RUN_OVERRIDE):
            assert eq_mod._resolve_vision_model(settings) == "vision-run-model"

    def test_user_policy_is_honored_for_vision_model(self, monkeypatch):
        from episteme_graph.agents.equation_semantics import llm_client as eq_mod

        monkeypatch.setenv("LLM_ANALYSIS_MODEL", "env-analysis-model")
        _real_get_settings.cache_clear()
        llm_policy.set_policy_backend(
            _FakeBackend(user=llm_policy.PolicyRow(model="user-vision-model", scope="user"))
        )
        from core.llm_usage.context import usage_context

        settings = _real_get_settings()
        with usage_context("pipeline:equation_semantics", user_id="teacher-1"):
            assert eq_mod._resolve_vision_model(settings) == "user-vision-model"

    def test_explicit_client_model_wins_for_vision(self, monkeypatch):
        """明示 llm_model 指定（vision_probe CLI 等）の意味は変えない。"""
        from episteme_graph.agents.equation_semantics.llm_client import (
            EquationSemanticsLLMClient,
        )

        client = EquationSemanticsLLMClient(model="explicit-vision-model")
        captured: dict = {}

        def fake_openai_vision(messages, image, model):
            captured["model"] = model
            return {"equations": []}

        monkeypatch.setattr(
            client, "_generate_openai_vision", fake_openai_vision, raising=True
        )
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        _real_get_settings.cache_clear()

        out = client.generate(
            [{"role": "user", "content": "x"}],
            image={"mime_type": "image/png", "data_base64": "AA=="},
        )
        assert captured["model"] == "explicit-vision-model"
        assert out["_vision_ocr"]["used"] is True

    def test_text_path_leaves_model_unset_for_entry_point_resolution(self, monkeypatch):
        """image なしの経路は model=None のまま core.llm 入口へ委ねる。"""
        from episteme_graph.agents.equation_semantics.llm_client import (
            EquationSemanticsLLMClient,
        )

        monkeypatch.setenv("LLM_ANALYSIS_MODEL", "env-analysis-model")
        _real_get_settings.cache_clear()
        calls: list[dict] = []
        monkeypatch.setattr(
            "core.llm.generate_text", _stub_core_generate_text(calls), raising=True
        )
        with llm_policy.model_override("policy-model", source=llm_policy.SOURCE_RUN_OVERRIDE):
            EquationSemanticsLLMClient().generate([{"role": "user", "content": "x"}])
        assert calls[0]["model"] == "policy-model"
        assert calls[0]["source"] == llm_policy.SOURCE_RUN_OVERRIDE
