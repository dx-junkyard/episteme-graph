"""Tests for revision accept/reject/revise + audit history (#407)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.document_pipeline import persistence  # noqa: E402
from core.document_pipeline.persistence import RevisionConflictError  # noqa: E402
from core.document_pipeline.revision import coordinator  # noqa: E402
from core.document_pipeline.revision.coordinator import AcceptBlockedError  # noqa: E402


# --- fake session ----------------------------------------------------------

class _FakeResult:
    def __init__(self, *, one=None, rows=None, rowcount=0):
        self._one = one
        self._rows = rows or []
        self.rowcount = rowcount

    def mappings(self):
        return self

    def fetchone(self):
        return self._one if self._one is not None else (self._rows[0] if self._rows else None)

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, results):
        self._results = list(results)
        self.sql: list[str] = []
        self.committed = False
        self.rolled_back = False

    def execute(self, statement, params=None):
        self.sql.append(str(statement))
        return self._results.pop(0) if self._results else _FakeResult()

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        pass


def _install(monkeypatch, results):
    session = _FakeSession(results)
    monkeypatch.setattr(persistence, "_pg_session", lambda: session)
    return session


# --- persistence.accept_revision ------------------------------------------

def test_accept_switches_active_and_records_decision(monkeypatch):
    session = _install(monkeypatch, [
        _FakeResult(one=("running", "proposed", "base-1", "revision")),  # SELECT FOR UPDATE
        _FakeResult(),  # materialize candidate artifacts
        _FakeResult(rowcount=1),  # UPDATE documents (active switch)
    ])
    out = persistence.accept_revision(
        document_id="doc-1", run_id="rev-1", expected_base_run_id="base-1",
        changed_by="11111111-1111-1111-1111-111111111111", comment="ok",
        candidate_artifacts={"component_graph": {"nodes": [], "edges": []},
                             "claim_object_builder": {"claims": []},
                             "component_assembly": {"components": []}},
    )
    assert out["accepted"] is True
    assert session.committed is True
    joined = "\n".join(session.sql)
    assert "UPDATE documents" in joined and "IS NOT DISTINCT FROM" in joined
    # all projections rebuilt in the same transaction
    assert "theory_component_graphs" in joined
    assert "DELETE FROM theory_claims" in joined
    assert "DELETE FROM theory_components" in joined
    assert "theory_review_events" in joined      # decision recorded
    assert "revision_status = 'accepted'" in joined


def test_accept_conflict_when_active_moved(monkeypatch):
    session = _install(monkeypatch, [
        _FakeResult(one=("running", "proposed", "base-1", "revision")),
        _FakeResult(),  # materialize candidate artifacts
        _FakeResult(rowcount=0),  # optimistic switch matched nothing
    ])
    with pytest.raises(RevisionConflictError):
        persistence.accept_revision(
            document_id="doc-1", run_id="rev-1", expected_base_run_id="base-1",
        )
    assert session.rolled_back is True


def test_accept_rejected_when_not_proposed(monkeypatch):
    _install(monkeypatch, [
        _FakeResult(one=("completed", "accepted", "base-1", "revision")),
    ])
    with pytest.raises(RevisionConflictError):
        persistence.accept_revision(
            document_id="doc-1", run_id="rev-1", expected_base_run_id="base-1",
        )


def test_accept_base_mismatch_conflict(monkeypatch):
    _install(monkeypatch, [
        _FakeResult(one=("running", "proposed", "real-base", "revision")),
    ])
    with pytest.raises(RevisionConflictError):
        persistence.accept_revision(
            document_id="doc-1", run_id="rev-1", expected_base_run_id="stale-base",
        )


def test_reject_does_not_touch_active_or_projection(monkeypatch):
    session = _install(monkeypatch, [
        _FakeResult(one=("proposed", "revision")),  # SELECT FOR UPDATE
    ])
    out = persistence.reject_revision(run_id="rev-1", changed_by=None, comment="no good")
    assert out["rejected"] is True
    joined = "\n".join(session.sql)
    assert "UPDATE documents" not in joined            # active untouched
    assert "theory_component_graphs" not in joined     # projection untouched
    assert "revision_status = 'rejected'" in joined
    assert "theory_review_events" in joined


# --- coordinator.accept_revision gating ------------------------------------

def _run_with_report(report_summary, candidate=None):
    return {
        "id": "rev-1", "run_type": "revision", "document_id": "doc-1",
        "base_run_id": "base-1",
        "stage_outputs": {"_artifacts": {
            "diff_report": {"summary": report_summary},
            "candidate": candidate or {"candidate_artifacts": {"component_graph": {"nodes": []}}},
        }},
    }


def test_coordinator_blocks_accept_on_hard_errors(monkeypatch):
    monkeypatch.setattr(coordinator.persistence, "get_analysis_run",
                        lambda *, run_id: _run_with_report(
                            {"acceptable": False, "hard_error_count": 2,
                             "protected_change_count": 0}))
    with pytest.raises(AcceptBlockedError):
        coordinator.accept_revision(document_id="doc-1", run_id="rev-1")


def test_coordinator_blocks_accept_on_unconfirmed_protected(monkeypatch):
    monkeypatch.setattr(coordinator.persistence, "get_analysis_run",
                        lambda *, run_id: _run_with_report(
                            {"acceptable": True, "hard_error_count": 0,
                             "protected_change_count": 1}))
    with pytest.raises(AcceptBlockedError):
        coordinator.accept_revision(document_id="doc-1", run_id="rev-1", confirm_protected=False)


def test_coordinator_accept_passes_base_and_graph(monkeypatch):
    monkeypatch.setattr(coordinator.persistence, "get_analysis_run",
                        lambda *, run_id: _run_with_report(
                            {"acceptable": True, "hard_error_count": 0,
                             "protected_change_count": 0}))
    captured = {}
    monkeypatch.setattr(coordinator.persistence, "accept_revision",
                        lambda **k: captured.update(k) or {"accepted": True})
    out = coordinator.accept_revision(document_id="doc-1", run_id="rev-1", comment="go")
    assert out["accepted"] is True
    assert captured["expected_base_run_id"] == "base-1"
    assert captured["candidate_artifacts"]["component_graph"] == {"nodes": []}
    assert captured["comment"] == "go"


def test_coordinator_accept_confirmed_protected_proceeds(monkeypatch):
    monkeypatch.setattr(coordinator.persistence, "get_analysis_run",
                        lambda *, run_id: _run_with_report(
                            {"acceptable": True, "hard_error_count": 0,
                             "protected_change_count": 1}))
    monkeypatch.setattr(coordinator.persistence, "accept_revision",
                        lambda **k: {"accepted": True})
    out = coordinator.accept_revision(document_id="doc-1", run_id="rev-1", confirm_protected=True)
    assert out["accepted"] is True


# --- #415: partial adoption gating -----------------------------------------

def _partial_run(candidate_status, candidate=None):
    return _run_with_report(
        {"candidate_status": candidate_status, "hard_error_count": 0,
         "protected_change_count": 0,
         "operation_summary": {"applied_count": 48, "excluded_invalid_count": 1,
                               "excluded_dependency_count": 0}},
        candidate=candidate or {
            "candidate_artifacts": {"component_graph": {"nodes": []}},
            "candidate_status": candidate_status,
            "operation_summary": {"applied_count": 48, "excluded_invalid_count": 1,
                                  "excluded_dependency_count": 0},
            "excluded_operations": [{"operation_id": "op-49", "status": "excluded_invalid",
                                     "reason": "target not found", "target_type": "equation",
                                     "target_id": "eq_missing"}],
        })


def test_coordinator_blocks_accept_when_candidate_blocked(monkeypatch):
    monkeypatch.setattr(coordinator.persistence, "get_analysis_run",
                        lambda *, run_id: _partial_run("blocked"))
    with pytest.raises(AcceptBlockedError):
        coordinator.accept_revision(document_id="doc-1", run_id="rev-1")


def test_coordinator_blocks_partial_accept_without_explicit_flag(monkeypatch):
    monkeypatch.setattr(coordinator.persistence, "get_analysis_run",
                        lambda *, run_id: _partial_run("partially_adoptable"))
    with pytest.raises(AcceptBlockedError):
        coordinator.accept_revision(document_id="doc-1", run_id="rev-1", accept_partial=False)


def test_coordinator_partial_accept_records_excluded_in_decision(monkeypatch):
    monkeypatch.setattr(coordinator.persistence, "get_analysis_run",
                        lambda *, run_id: _partial_run("partially_adoptable"))
    captured = {}
    monkeypatch.setattr(coordinator.persistence, "accept_revision",
                        lambda **k: captured.update(k) or {"accepted": True})
    out = coordinator.accept_revision(document_id="doc-1", run_id="rev-1", accept_partial=True)
    assert out["accepted"] is True
    meta = captured["decision_metadata"]
    assert meta["partial"] is True
    assert meta["candidate_status"] == "partially_adoptable"
    assert meta["excluded_operations"][0]["operation_id"] == "op-49"


def test_revise_carries_parent_exclusions_into_checkpoints(monkeypatch):
    parent = {"id": "rev-1", "run_type": "revision", "document_id": "doc-1",
              "stage_outputs": {"_artifacts": {"candidate": {"excluded_operations": [
                  {"operation_id": "op-49", "status": "excluded_invalid",
                   "reason": "target not found", "target_type": "equation",
                   "target_id": "eq_missing"}]}}}}
    monkeypatch.setattr(coordinator.persistence, "get_analysis_run", lambda *, run_id: parent)
    monkeypatch.setattr(coordinator.persistence, "resolve_artifact_run",
                        lambda *, document_id, material_id=None: {
                            "id": "active-1", "document_id": document_id,
                            "stage_outputs": {"_artifacts": {}}})
    monkeypatch.setattr(coordinator.persistence, "load_revision_projection_overlay",
                        lambda *, document_id: {"claims": [], "components": [], "review_events": []})
    monkeypatch.setattr(coordinator.persistence, "create_revision_run", lambda **k: "child-1")
    monkeypatch.setattr(coordinator.persistence, "update_revision_status", lambda **k: None)
    out = coordinator.revise_revision_run(document_id="doc-1", parent_run_id="rev-1", comment="")
    carried = [c for c in out["audit_checkpoints"] if c.get("trigger_reason") == "prior_exclusion"]
    assert carried and carried[0]["prior_exclusion"]["operation_id"] == "op-49"
    assert carried[0]["target_id"] == "eq_missing"


# --- coordinator.revise ----------------------------------------------------

def test_revise_creates_child_with_parent_and_active_base(monkeypatch):
    monkeypatch.setattr(coordinator.persistence, "get_analysis_run",
                        lambda *, run_id: {"id": run_id, "run_type": "revision",
                                           "document_id": "doc-1"})
    monkeypatch.setattr(coordinator.persistence, "resolve_artifact_run",
                        lambda *, document_id, material_id=None: {
                            "id": "active-1", "document_id": document_id,
                            "stage_outputs": {"_artifacts": {}}})
    captured = {}
    monkeypatch.setattr(coordinator.persistence, "create_revision_run",
                        lambda **k: captured.update(k) or "child-1")
    monkeypatch.setattr(coordinator.persistence, "update_revision_status",
                        lambda **k: None)
    out = coordinator.revise_revision_run(
        document_id="doc-1", parent_run_id="rev-1", comment="please split eq 2")
    assert out["revision_run_id"] == "child-1"
    assert captured["base_run_id"] == "active-1"
    assert captured["parent_revision_id"] == "rev-1"
    # user comment carried as an extra checkpoint
    assert any(c["trigger_reason"] == "user_comment" for c in out["audit_checkpoints"])
