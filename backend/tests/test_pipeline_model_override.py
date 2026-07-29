"""M層（LLM モデル選択）Phase 2 バックエンドのテスト。

正本: docs/features/llm_model_selection_design.md（§3 解決順序 / §7 API / §10 Phase 2）。
Phase 0 (core/llm_policy.py) は実装済み・変更しない。ここでは:

  1. ``api/routes/admin.py::_validate_models_option`` の fail-closed 検証
     （M4: カタログに無いモデルを拒否 / M5: capability・provider 不一致を拒否）。
  2. ``core/document_pipeline/orchestrator.py`` のステージ単位 run override
     （``_resolve_stage_override_model`` の解決順序）と、それが実際に
     ``_PIPELINE_STEPS`` ランナーループで ``core.llm_policy.model_override``
     contextvar として張られ、``resolve_scene_model`` から見えることを検証する。
  3. 使用モデルの記録（M7, ``_stage_models``）が実行済みステージのみに限られ、
     resume で artifact 再利用したステージ・非LLMステージ・skip placeholder
     を残したステージでは記録されないこと。
  4. models 未指定時の完全な後方互換（override contextvar が終始 None のまま）。

DB は一切使わない（``upsert_analysis_run`` / ``get_latest_analysis_run`` を
monkeypatch する既存 ``test_document_pipeline.py`` の流儀に倣う）。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

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
from core.document_pipeline import orchestrator  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_policy_and_settings():
    """PolicyBackend / settings キャッシュをテスト間で汚さない（test_llm_policy.py と同型）。"""
    yield
    llm_policy.reset_policy_backend_for_tests()
    _real_get_settings.cache_clear()


def _import_admin_routes():
    """routes/admin.py を import する（api 系テストと同じパス設定）。"""
    pytest.importorskip("fastapi")
    for _p in (str(BACKEND_DIR), str(BACKEND_DIR / "api")):
        if _p not in sys.path:
            sys.path.insert(0, _p)
    import routes.admin as admin_mod
    return admin_mod


def _configure_shipped_catalog(monkeypatch):
    shipped = BACKEND_DIR / "config" / "llm_models.json"
    assert shipped.is_file()
    monkeypatch.setenv("LLM_MODEL_CATALOG_PATH", str(shipped))
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    _real_get_settings.cache_clear()


# ===========================================================================
# 1. _validate_models_option — fail-closed 検証
# ===========================================================================


class TestValidateModelsOption:
    def test_normal_pipeline_and_vision_keys_pass_through(self, monkeypatch):
        _configure_shipped_catalog(monkeypatch)
        admin_mod = _import_admin_routes()

        result = admin_mod._validate_models_option(
            {"pipeline": "gpt-5.4-mini", "pipeline.vision": "gpt-4o"}
        )
        assert result == {"pipeline": "gpt-5.4-mini", "pipeline.vision": "gpt-4o"}

    def test_stage_specific_key_passes_when_stage_and_model_known(self, monkeypatch):
        _configure_shipped_catalog(monkeypatch)
        admin_mod = _import_admin_routes()

        result = admin_mod._validate_models_option(
            {"pipeline:paper_skeleton": "gpt-5.2"}
        )
        assert result == {"pipeline:paper_skeleton": "gpt-5.2"}

    def test_unknown_stage_name_rejected(self, monkeypatch):
        _configure_shipped_catalog(monkeypatch)
        admin_mod = _import_admin_routes()
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            admin_mod._validate_models_option({"pipeline:not_a_real_stage": "gpt-5.2"})
        assert exc_info.value.status_code == 422

    def test_model_not_in_catalog_rejected(self, monkeypatch):
        _configure_shipped_catalog(monkeypatch)
        admin_mod = _import_admin_routes()
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            admin_mod._validate_models_option({"pipeline": "made-up-model-9000"})
        assert exc_info.value.status_code == 422

    def test_non_vision_model_rejected_for_pipeline_vision_key(self, monkeypatch):
        _configure_shipped_catalog(monkeypatch)
        admin_mod = _import_admin_routes()
        from fastapi import HTTPException

        # gpt-5.4-mini has no "vision" capability in the shipped catalog.
        with pytest.raises(HTTPException) as exc_info:
            admin_mod._validate_models_option({"pipeline.vision": "gpt-5.4-mini"})
        assert exc_info.value.status_code == 422

    def test_non_vision_model_rejected_for_apparatus_semantics_stage_key(self, monkeypatch):
        _configure_shipped_catalog(monkeypatch)
        admin_mod = _import_admin_routes()
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            admin_mod._validate_models_option(
                {"pipeline:apparatus_semantics": "gpt-5.4-mini"}
            )
        assert exc_info.value.status_code == 422

    def test_vision_model_accepted_for_apparatus_semantics_stage_key(self, monkeypatch):
        _configure_shipped_catalog(monkeypatch)
        admin_mod = _import_admin_routes()

        result = admin_mod._validate_models_option(
            {"pipeline:apparatus_semantics": "gpt-4o"}
        )
        assert result == {"pipeline:apparatus_semantics": "gpt-4o"}

    def test_invalid_key_rejected(self, monkeypatch):
        _configure_shipped_catalog(monkeypatch)
        admin_mod = _import_admin_routes()
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            admin_mod._validate_models_option({"not_a_scene": "gpt-5.2"})
        assert exc_info.value.status_code == 422

    def test_no_catalog_configured_rejects_any_models_selection(self, monkeypatch, tmp_path):
        # 「カタログ無し」= 存在しないパスの明示指定。env 未設定は同梱カタログに
        # フォールバックする（2026-07-28 以降の既定。test_llm_policy.py 参照）ため、
        # ここで delenv を使うと「カタログ無し」を再現できない。
        monkeypatch.setenv("LLM_MODEL_CATALOG_PATH", str(tmp_path / "no_such_catalog.json"))
        _real_get_settings.cache_clear()
        admin_mod = _import_admin_routes()
        from fastapi import HTTPException

        assert llm_policy.load_catalog() is None
        with pytest.raises(HTTPException) as exc_info:
            admin_mod._validate_models_option({"pipeline": "gpt-5.2"})
        assert exc_info.value.status_code == 422

    def test_empty_dict_rejected(self, monkeypatch):
        _configure_shipped_catalog(monkeypatch)
        admin_mod = _import_admin_routes()
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            admin_mod._validate_models_option({})


# ===========================================================================
# 2. _resolve_stage_override_model — 解決順序（pure function）
# ===========================================================================


class TestResolveStageOverrideModel:
    def test_no_models_key_returns_none(self):
        assert orchestrator._resolve_stage_override_model("paper_skeleton", {}) is None

    def test_stage_specific_key_wins_over_generic_pipeline(self):
        options = {
            "models": {
                "pipeline:paper_skeleton": "gpt-5.2",
                "pipeline": "gpt-5.4-mini",
            }
        }
        assert (
            orchestrator._resolve_stage_override_model("paper_skeleton", options)
            == "gpt-5.2"
        )

    def test_falls_back_to_generic_pipeline_when_no_stage_specific_key(self):
        options = {"models": {"pipeline": "gpt-5.4-mini"}}
        assert (
            orchestrator._resolve_stage_override_model("paper_skeleton", options)
            == "gpt-5.4-mini"
        )

    def test_apparatus_semantics_falls_back_to_pipeline_vision_not_pipeline(self):
        options = {"models": {"pipeline": "gpt-5.4-mini", "pipeline.vision": "gpt-4o"}}
        assert (
            orchestrator._resolve_stage_override_model("apparatus_semantics", options)
            == "gpt-4o"
        )

    def test_apparatus_semantics_does_not_fall_back_to_generic_pipeline(self):
        """apparatus_semantics（vision）は "pipeline.vision" だけを見る。generic
        "pipeline" の値は vision capability を持つ保証が無いため、fail-closed に
        override なしのまま env/tier 既定へ委ねる（M5）。"""
        options = {"models": {"pipeline": "gpt-5.4-mini"}}
        assert (
            orchestrator._resolve_stage_override_model("apparatus_semantics", options)
            is None
        )

    def test_stage_name_none_returns_none(self):
        options = {"models": {"pipeline": "gpt-5.4-mini"}}
        assert orchestrator._resolve_stage_override_model(None, options) is None

    def test_non_dict_models_value_returns_none(self):
        assert (
            orchestrator._resolve_stage_override_model("paper_skeleton", {"models": "oops"})
            is None
        )

    def test_blank_value_is_ignored(self):
        options = {"models": {"pipeline:paper_skeleton": "   ", "pipeline": "gpt-5.4-mini"}}
        assert (
            orchestrator._resolve_stage_override_model("paper_skeleton", options)
            == "gpt-5.4-mini"
        )


# ===========================================================================
# 3. _stage_artifact_indicates_llm_skip
# ===========================================================================


class TestStageArtifactIndicatesLlmSkip:
    def test_skipped_by_option_true(self):
        assert orchestrator._stage_artifact_indicates_llm_skip({"skipped_by_option": True})

    def test_skipped_by_limit_true(self):
        assert orchestrator._stage_artifact_indicates_llm_skip({"skipped_by_limit": True})

    def test_llm_calls_zero(self):
        assert orchestrator._stage_artifact_indicates_llm_skip({"llm_calls": 0, "status": "completed"})

    def test_normal_result_is_not_a_skip(self):
        assert not orchestrator._stage_artifact_indicates_llm_skip({"status": "completed", "llm_calls": 1})

    def test_non_dict_is_not_a_skip(self):
        assert not orchestrator._stage_artifact_indicates_llm_skip(None)
        assert not orchestrator._stage_artifact_indicates_llm_skip("some-agent-dataclass-dump")


# ===========================================================================
# 4. ランナーループの統合テスト — override 適用 + M7 記録
#
# _PIPELINE_STEPS を最小限のフェイクステージに差し替え、DB は
# upsert_analysis_run/get_latest_analysis_run を monkeypatch して一切叩かない
# （tests/test_document_pipeline.py の _run_pipeline_capturing_options と同型）。
# ===========================================================================


def _run_pipeline_with_fake_steps(*, options, steps, latest_run=None):
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


class TestRunnerLoopOverrideAndRecording:
    def test_generic_pipeline_override_seen_by_resolve_scene_model(self, monkeypatch):
        """options.models={"pipeline": ...} のとき、ステージ実行中に
        resolve_scene_model("pipeline:paper_skeleton") がその値を返す
        （model_override contextvar 経由。call-argument より下、
        env/tier より上の優先順位で効いていることの確認）。"""
        monkeypatch.setenv("LLM_ANALYSIS_MODEL", "env-analysis-model")
        _real_get_settings.cache_clear()

        seen: dict = {}

        def paper_skeleton_step(ctx):
            seen["resolved"] = llm_policy.resolve_scene_model("pipeline:paper_skeleton").model
            seen["override_active"] = llm_policy.current_model_override() is not None
            ctx.save_artifact("paper_skeleton", {"status": "completed"})
            return True  # stop here; nothing downstream needed for this test

        steps = [orchestrator.PipelineStageDef("paper_skeleton", paper_skeleton_step)]
        options = {"models": {"pipeline": "gpt-5.4-mini"}}

        upserts = _run_pipeline_with_fake_steps(options=options, steps=steps)

        assert seen["resolved"] == "gpt-5.4-mini"
        assert seen["override_active"] is True
        stage_models = _last_stage_models(upserts)
        assert stage_models == {"paper_skeleton": "gpt-5.4-mini"}

    def test_stage_specific_override_wins_over_generic_pipeline_key(self, monkeypatch):
        seen: dict = {}

        def paper_skeleton_step(ctx):
            seen["resolved"] = llm_policy.resolve_scene_model("pipeline:paper_skeleton").model
            ctx.save_artifact("paper_skeleton", {"status": "completed"})
            return True

        steps = [orchestrator.PipelineStageDef("paper_skeleton", paper_skeleton_step)]
        options = {
            "models": {
                "pipeline:paper_skeleton": "gpt-5.2",
                "pipeline": "gpt-5.4-mini",
            }
        }
        _run_pipeline_with_fake_steps(options=options, steps=steps)
        assert seen["resolved"] == "gpt-5.2"

    def test_apparatus_semantics_uses_pipeline_vision_fallback(self, monkeypatch):
        seen: dict = {}

        def apparatus_step(ctx):
            seen["resolved"] = llm_policy.resolve_scene_model("pipeline:apparatus_semantics").model
            ctx.save_artifact("apparatus_semantics", {"status": "completed", "apparatus_records": 1})
            return True

        steps = [orchestrator.PipelineStageDef("apparatus_semantics", apparatus_step)]
        options = {"models": {"pipeline": "gpt-5.4-mini", "pipeline.vision": "gpt-4o"}}
        upserts = _run_pipeline_with_fake_steps(options=options, steps=steps)

        assert seen["resolved"] == "gpt-4o"
        assert _last_stage_models(upserts) == {"apparatus_semantics": "gpt-4o"}

    def test_no_models_option_leaves_override_unset_and_behavior_unchanged(self, monkeypatch):
        """models 未指定時: model_override contextvar は終始 None のまま
        （挙動不変・env/tier 既定のみが効く既存の解決経路をそのまま通る）。"""
        monkeypatch.setenv("LLM_ANALYSIS_MODEL", "env-analysis-model")
        _real_get_settings.cache_clear()

        seen: dict = {}

        def paper_skeleton_step(ctx):
            seen["override_active"] = llm_policy.current_model_override()
            seen["resolved"] = llm_policy.resolve_scene_model("pipeline:paper_skeleton").model
            ctx.save_artifact("paper_skeleton", {"status": "completed"})
            return True

        steps = [orchestrator.PipelineStageDef("paper_skeleton", paper_skeleton_step)]
        # options=None triggers the "inherit from previous run" branch; no previous run
        # here, so effective_options ends up {} (no "models" key at all).
        _run_pipeline_with_fake_steps(options=None, steps=steps, latest_run=None)

        assert seen["override_active"] is None
        assert seen["resolved"] == "env-analysis-model"

    def test_non_llm_stage_is_not_recorded_even_if_artifact_saved(self):
        """grobid_parse は _LLM_STAGE_NAMES に無いので、artifact を保存しても
        stage_models に記録されない（不正確な網羅より正直な部分記録を優先）。"""
        def grobid_step(ctx):
            ctx.save_artifact("grobid_parse", {"status": "completed"})
            return True

        steps = [orchestrator.PipelineStageDef("grobid_parse", grobid_step)]
        upserts = _run_pipeline_with_fake_steps(options=None, steps=steps)

        assert _last_stage_models(upserts) is None

    def test_skipped_by_option_artifact_is_not_recorded(self):
        """apparatus_semantics が analyze_images=off で skipped_by_option を保存した
        場合、LLM は呼ばれていないので stage_models に記録しない。"""
        def apparatus_step(ctx):
            ctx.save_artifact("apparatus_semantics", {"skipped_by_option": True})
            return True

        steps = [orchestrator.PipelineStageDef("apparatus_semantics", apparatus_step)]
        upserts = _run_pipeline_with_fake_steps(options=None, steps=steps)

        assert _last_stage_models(upserts) is None

    def test_resumed_stage_with_existing_artifact_is_not_re_recorded(self):
        """artifact 再利用（resume）したステージは resolve_scene_model を呼ばず、
        新しい stage_models 書き込みも起こさない（前回の記録をそのまま保持）。"""
        previous_run = {
            "id": "run-prev",
            "status": "running",
            "current_stage": "paper_skeleton",
            "stage_outputs": {
                orchestrator.ARTIFACTS_KEY: {"paper_skeleton": {"status": "completed"}},
                orchestrator.STAGE_MODELS_KEY: {"paper_skeleton": "gpt-5.2-previous"},
            },
            "options": {},
        }

        called = {"resolved_again": False}

        def paper_skeleton_step(ctx):
            if ctx.should_use_artifact("paper_skeleton"):
                return True
            called["resolved_again"] = True
            ctx.save_artifact("paper_skeleton", {"status": "completed"})
            return True

        steps = [orchestrator.PipelineStageDef("paper_skeleton", paper_skeleton_step)]
        upserts = _run_pipeline_with_fake_steps(
            options=None, steps=steps, latest_run=previous_run,
        )

        assert called["resolved_again"] is False
        # No new write of STAGE_MODELS_KEY should have happened for this run.
        assert _last_stage_models(upserts) is None

    def test_llm_calls_zero_artifact_is_not_recorded(self):
        """contextual_explanation が対象要素0件で llm_calls=0 のまま artifact を
        保存した場合も、LLM は呼ばれていないので記録しない。"""
        def ctxexpl_step(ctx):
            ctx.save_artifact("contextual_explanation", {"status": "completed", "llm_calls": 0})
            return True

        steps = [orchestrator.PipelineStageDef("contextual_explanation", ctxexpl_step)]
        upserts = _run_pipeline_with_fake_steps(options=None, steps=steps)

        assert _last_stage_models(upserts) is None

    def test_actual_llm_call_stage_is_recorded_with_default_model_when_no_override(self):
        """override が無くても、実行された LLM ステージは env/tier 既定の実効モデルで
        記録される（M7 は override が無い実行でも常に記録する）。"""
        def paper_skeleton_step(ctx):
            ctx.save_artifact("paper_skeleton", {"status": "completed"})
            return True

        steps = [orchestrator.PipelineStageDef("paper_skeleton", paper_skeleton_step)]
        upserts = _run_pipeline_with_fake_steps(options=None, steps=steps)

        stage_models = _last_stage_models(upserts)
        assert stage_models is not None
        assert stage_models["paper_skeleton"] == _real_get_settings().llm_analysis_model
