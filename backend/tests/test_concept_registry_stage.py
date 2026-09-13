"""パイプラインステージ ``identity_candidates``（概念レジストリ P3-6・§6.2）。

固定するもの:

- ``_PIPELINE_STEPS`` の**末尾**（``persist_claims_components_graph`` の直後）に居ること
  — component 行・stable_key が確定した後でなければ同一性は言えない。
- 決定論の宣言（``llm_kind='none'`` / ``model_policy=False``）— M層の対象にしない。
- **非致命**（例外は warning に落として run を止めない）。
- resume では artifact を再利用し、``run_identity_candidates`` を呼ばない。

DB・LLM には触らない。
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _path in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from core.document_pipeline import orchestrator as orch  # noqa: E402

STAGE = "identity_candidates"


class _FakeCtx:
    """``_stage_identity_candidates`` が触る面だけを持つ最小 ctx。"""

    def __init__(self, *, artifact=None, use_artifact=False):
        self.document_id = "doc-1"
        self.material_id = "mat-1"
        self.run_id = "run-1"
        self._artifact = artifact
        self._use_artifact = use_artifact
        self.saved: dict = {}
        self.done: dict = {}
        self.started: list = []

    def artifact(self, name):
        return self._artifact

    def should_use_artifact(self, name):
        return self._use_artifact

    def report_start(self, name, total=None, unit=None):
        self.started.append((name, unit))

    def save_artifact(self, name, payload):
        self.saved[name] = payload

    def report_done(self, name, payload):
        self.done[name] = payload

    def finish_target_stage(self, name, payload):
        return False


class TestStageRegistration:
    def test_stage_is_the_last_named_step(self):
        named = [step.name for step in orch._PIPELINE_STEPS if step.name]
        assert named[-1] == STAGE
        assert named[-2] == "persist_claims_components_graph"

    def test_stage_is_in_pipeline_stages_before_completed(self):
        stages = orch.PIPELINE_STAGES
        assert stages[-1] == "completed"
        assert stages[-2] == STAGE

    def test_stage_declares_no_model_usage(self):
        step = next(s for s in orch._PIPELINE_STEPS if s.name == STAGE)
        assert step.llm_kind == orch.LLM_KIND_NONE
        assert step.model_policy is False
        assert step.progress_unit == "builder"

    def test_stage_is_not_a_model_selection_target(self):
        assert STAGE not in orch.LLM_STAGE_NAMES
        assert STAGE not in orch.LLM_CALLING_STAGE_NAMES
        assert STAGE not in orch.VISION_STAGE_NAMES

    def test_stage_has_a_display_label(self):
        from routes.lecture_studio import pipeline as ls_pipeline

        assert ls_pipeline.DOCUMENT_PIPELINE_STAGE_LABELS[STAGE]
        assert STAGE in ls_pipeline.DOCUMENT_PIPELINE_STAGES


class TestStageExecution:
    def test_failure_is_non_fatal(self, monkeypatch):
        from core.library import identity_candidates as ic

        def _boom(**kwargs):
            raise RuntimeError("db down")

        monkeypatch.setattr(ic, "run_identity_candidates", _boom)
        ctx = _FakeCtx()
        assert orch._stage_identity_candidates(ctx) is False
        payload = ctx.saved[STAGE]
        assert payload["status"] == "completed"
        assert "db down" in payload["error"]

    def test_success_saves_the_payload(self, monkeypatch):
        from core.library import identity_candidates as ic

        monkeypatch.setattr(
            ic, "run_identity_candidates",
            lambda **kwargs: {"entries_created": 1, "links_created": 2},
        )
        ctx = _FakeCtx()
        orch._stage_identity_candidates(ctx)
        assert ctx.saved[STAGE]["links_created"] == 2
        assert ctx.saved[STAGE]["status"] == "completed"
        assert ctx.started == [(STAGE, "builder")]

    def test_resume_reuses_the_artifact_without_recomputing(self, monkeypatch):
        from core.library import identity_candidates as ic

        def _boom(**kwargs):  # 呼ばれたら失敗させる
            raise AssertionError("resume must not recompute")

        monkeypatch.setattr(ic, "run_identity_candidates", _boom)
        ctx = _FakeCtx(artifact={"links_created": 3}, use_artifact=True)
        orch._stage_identity_candidates(ctx)
        assert ctx.done[STAGE]["links_created"] == 3
        assert ctx.saved == {}
        assert ctx.started == []


class TestUsageFeature:
    def test_pipeline_feature_is_registered(self):
        from core.llm_usage.schema import KNOWN_FEATURES

        assert f"pipeline:{STAGE}" in KNOWN_FEATURES
