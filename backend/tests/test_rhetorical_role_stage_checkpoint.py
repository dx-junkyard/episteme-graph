"""IK-0710: rhetorical_role のステージ内チェックポイントの配線（orchestrator 側）。

agent の窓判定・チェックポイントの形は ``src/tests/agents/rhetorical_role/test_windowing.py``。
ここでは ``_stage_rhetorical_role`` が ①前回 run の状態を agent に渡し ②agent の状態を
``stage_outputs["_stage_checkpoints"]`` に書き ③完了で外し ④引き継がない run では最初に外す
ことだけを、DB なし（``upsert_analysis_run`` を差し替え）で確かめる。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.document_pipeline import orchestrator
from core.document_pipeline.orchestrator import (
    STAGE_CHECKPOINTS_KEY,
    DocumentPipelineResult,
    PipelineContext,
    _stage_rhetorical_role,
)


class _Agent:
    def __init__(self) -> None:
        self.received_checkpoint = "unset"

    def run(self, structure, skeleton, cartridge_id=None, config=None,
            progress_callback=None, checkpoint=None, checkpoint_callback=None):
        self.received_checkpoint = checkpoint
        checkpoint_callback({"version": 1, "blocks": {"b0": {"fp": "x"}}})
        checkpoint_callback({"version": 1, "blocks": {"b0": {"fp": "x"}, "b1": {"fp": "y"}}})
        return SimpleNamespace(summary_stats={"blocks_processed": 2})


class _LegacyAgent:
    def run(self, **kwargs):
        assert "checkpoint" not in kwargs and "checkpoint_callback" not in kwargs
        return SimpleNamespace(summary_stats={})


def _ctx(agent, checkpoints: dict | None = None) -> PipelineContext:
    ctx = PipelineContext(
        pdf_bytes=b"",
        document_id="doc",
        material_id="mat",
        filename=None,
        source_kind="pdf",
        cartridge_id=None,
        course_id=None,
        run_id="run-1",
        agent_classes={"RhetoricalRoleAgent": agent},
        effective_options={},
        result=DocumentPipelineResult(document_id="doc", material_id="mat", cartridge_id=None, run_id="run-1"),
    )
    saved: dict = {}
    ctx.report_start = lambda *a, **k: None
    ctx.report_item = lambda *a, **k: None
    ctx.report_done = lambda *a, **k: None
    ctx.save_artifact = lambda stage, value: saved.__setitem__(stage, value)
    ctx.artifact = lambda stage: None
    ctx.should_use_artifact = lambda stage: False
    ctx.finish_target_stage = lambda stage, payload=None: False
    ctx.resume_stage_checkpoints = dict(checkpoints or {})
    ctx._saved = saved  # type: ignore[attr-defined]
    return ctx


@pytest.fixture
def writes(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(orchestrator, "upsert_analysis_run", lambda **kw: calls.append(kw) or kw.get("run_id"))
    return calls


def _checkpoint_writes(calls):
    return [c["stage_outputs"][STAGE_CHECKPOINTS_KEY] for c in calls if STAGE_CHECKPOINTS_KEY in (c.get("stage_outputs") or {})]


def test_resume_passes_previous_checkpoint_and_drops_it_on_completion(writes):
    previous = {"version": 1, "blocks": {"b0": {"fp": "x"}}}
    agent = _Agent()
    ctx = _ctx(agent, {"rhetorical_role": previous})
    _stage_rhetorical_role(ctx)
    assert agent.received_checkpoint == previous
    states = _checkpoint_writes(writes)
    # progressive writes (2 windows) then the drop at completion
    assert states[0]["rhetorical_role"]["blocks"].keys() == {"b0"}
    assert states[1]["rhetorical_role"]["blocks"].keys() == {"b0", "b1"}
    assert states[-1] == {}
    assert "rhetorical_role" in ctx._saved  # artifact still written once by save_artifact
    # the checkpoint is not part of the progress payload (polled by the task API)
    assert all("rhetorical_role" not in (c.get("stage_outputs") or {}) for c in writes)


def test_fresh_or_restart_run_clears_stale_checkpoint_before_first_window(writes):
    agent = _Agent()
    _stage_rhetorical_role(_ctx(agent))
    assert agent.received_checkpoint is None
    states = _checkpoint_writes(writes)
    assert states[0] == {}  # cleared before the agent wrote anything
    assert states[-1] == {}


def test_injected_agent_without_checkpoint_parameters_is_called_as_before(writes):
    _stage_rhetorical_role(_ctx(_LegacyAgent()))
    # no progressive state; only the unconditional drop at completion
    assert _checkpoint_writes(writes) == [{}]


def test_checkpoint_write_failure_does_not_stop_the_stage(monkeypatch):
    def boom(**kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(orchestrator, "upsert_analysis_run", boom)
    agent = _Agent()
    _stage_rhetorical_role(_ctx(agent))
    assert agent.received_checkpoint is None
