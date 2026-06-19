"""#412: revision run status transitions, structured logging, report sections."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.document_pipeline.revision import coordinator  # noqa: E402
from core.document_pipeline.revision.diff import (  # noqa: E402
    build_stage_summary,
    summarize_rejections,
)


def _run():
    return {"id": "rev-1", "run_type": "revision", "document_id": "doc-1",
            "stage_outputs": {"_artifacts": {
                "audit_checkpoints": [{"checkpoint_id": "c1"}, {"checkpoint_id": "c2"}]}}}


def _patch_stages(monkeypatch, *, audit=None, proposals=None, candidate=None, report=None):
    monkeypatch.setattr(coordinator.persistence, "get_analysis_run", lambda *, run_id: _run())
    updates: list[dict] = []
    monkeypatch.setattr(coordinator.persistence, "update_revision_status",
                        lambda **k: updates.append(k))
    monkeypatch.setattr(coordinator, "audit_revision_run",
                        lambda **k: audit or {"revision_targets": [{}], "review_items": [{}, {}],
                                              "verdict_counts": {}})
    monkeypatch.setattr(coordinator, "generate_proposals",
                        lambda **k: proposals or {"operations": [{"operation": "update_entity"}],
                                                  "rejected": [], "manual_review": []})
    monkeypatch.setattr(coordinator, "assemble_candidate",
                        lambda **k: candidate or {"invalid": False, "applied_operations": [{}],
                                                  "operation_errors": []})
    monkeypatch.setattr(coordinator, "revalidate_and_report",
                        lambda **k: report or {"report": {
                            "recommendation": "accept",
                            "summary": {"outcome": "changes_proposed",
                                        "entity_change_count": 1, "candidate_invalid": False},
                            "stage_summary": {}}})
    return updates


# --- #412 P1-3: status transitions -----------------------------------------

def test_pipeline_status_transitions_through_each_stage(monkeypatch, caplog):
    updates = _patch_stages(monkeypatch)
    caplog.set_level(logging.INFO)
    out = coordinator.run_revision_pipeline(run_id="rev-1")
    stages = [(u.get("status"), u.get("current_stage")) for u in updates]
    assert ("running", "audit") in stages
    assert (None, "proposal") in stages
    assert (None, "candidate_assembly") in stages
    assert (None, "report") in stages
    assert ("completed", "completed") in stages
    assert out["revision_status"] == "proposed"
    # #412 P1-5: stage boundary logs are present and run-scoped
    assert "revision_started" in caplog.text
    assert "revision_audit_completed" in caplog.text
    assert "revision_completed" in caplog.text
    assert "run_id=rev-1" in caplog.text


def test_pipeline_failure_marks_failed_with_stage_and_error(monkeypatch):
    updates = _patch_stages(monkeypatch)

    def _boom(**k):
        raise RuntimeError("downstream blew up")
    monkeypatch.setattr(coordinator, "audit_revision_run", _boom)

    with pytest.raises(RuntimeError):
        coordinator.run_revision_pipeline(run_id="rev-1")
    failed = [u for u in updates if u.get("status") == "failed"]
    assert failed
    assert failed[0]["current_stage"] == "audit"
    assert "downstream blew up" in failed[0]["error_message"]


def test_pipeline_proposal_validation_failure_marks_candidate_assembly(monkeypatch):
    updates = _patch_stages(monkeypatch)

    def _invalid(**k):
        raise coordinator.ProposalValidationError([{"raw": {}, "reason": "missing_checkpoint"}])
    monkeypatch.setattr(coordinator, "assemble_candidate", _invalid)

    with pytest.raises(coordinator.ProposalValidationError):
        coordinator.run_revision_pipeline(run_id="rev-1", operations=[{"operation": "update_entity"}])
    failed = [u for u in updates if u.get("status") == "failed"]
    assert failed and failed[0]["current_stage"] == "candidate_assembly"


def test_safe_error_is_bounded_and_single_line():
    msg = coordinator._safe_error("X", RuntimeError("a" * 500 + "\nsecret line"))
    assert msg.startswith("X: ")
    assert "\n" not in msg
    assert len(msg) <= 210


# --- #412 P1-8: report stage sections --------------------------------------

def test_stage_summary_distinguishes_no_targets_from_failed_proposals():
    no_targets = build_stage_summary(
        audit_summary={"revision_target_count": 0, "checkpoints_total": 199,
                       "manual_review_count": 0},
        proposal_summary={}, candidate_invalid=False,
        new_unknown_reference_count=0, applied_change_count=0,
    )
    assert no_targets["outcome"] == "no_audit_targets"

    # 59 targets, 58 rejected, 0 adopted -> "proposals failed", not "nothing to do"
    failed = build_stage_summary(
        audit_summary={"revision_target_count": 59, "checkpoints_total": 199,
                       "manual_review_count": 138},
        proposal_summary={"accepted_count": 0, "rejected_count": 58},
        candidate_invalid=False, new_unknown_reference_count=0, applied_change_count=0,
    )
    assert failed["outcome"] == "proposals_failed"
    assert failed["audit"]["manual_review_count"] == 138
    assert failed["candidate_generation"]["rejected_count"] == 58

    invalid = build_stage_summary(
        audit_summary={"revision_target_count": 59},
        proposal_summary={"accepted_count": 1, "rejected_count": 58},
        candidate_invalid=True, new_unknown_reference_count=2, applied_change_count=1,
    )
    assert invalid["outcome"] == "candidate_invalid"
    assert invalid["candidate_evaluation"]["new_unknown_reference_count"] == 2


def test_summarize_rejections_groups_by_leading_code():
    out = summarize_rejections([
        {"reason": "unknown_evidence:a"},
        {"reason": "unknown_evidence:b"},
        {"reason": "missing_checkpoint"},
    ])
    assert out[0] == {"reason": "unknown_evidence", "count": 2}
    assert {"reason": "missing_checkpoint", "count": 1} in out
