"""API tests for revision accept/reject/revise endpoints (#407)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]   # .../backend
REPO = BACKEND.parent
SRC = REPO / "src"
for p in (str(SRC), str(BACKEND), str(BACKEND / "api")):
    if p not in sys.path:
        sys.path.insert(0, p)

_HAS_FASTAPI = True
try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
except Exception:  # pragma: no cover
    _HAS_FASTAPI = False

pytestmark = pytest.mark.skipif(not _HAS_FASTAPI, reason="FastAPI not installed")


@pytest.fixture
def app_client(monkeypatch):
    from dependencies import _require_teacher
    from routes import revisions

    app = FastAPI()
    app.include_router(revisions.router, prefix="/api/admin")
    app.dependency_overrides[_require_teacher] = lambda: {"id": "user-1", "role": "TEACHER"}

    # default: document authorization passes (overridden per-test for denial)
    monkeypatch.setattr(revisions, "_authorize_view", lambda doc, user: None)
    monkeypatch.setattr(revisions, "_authorize_edit", lambda doc, user: None)

    # default: the run exists and belongs to the document
    monkeypatch.setattr(revisions.persistence, "get_analysis_run",
                        lambda *, run_id: {"id": run_id, "run_type": "revision",
                                           "document_id": "doc-1", "status": "running",
                                           "revision_status": "proposed",
                                           "base_run_id": "base-1",
                                           "stage_outputs": {"_artifacts": {}}})
    return TestClient(app), revisions, _require_teacher, app


def test_create_revision(app_client, monkeypatch):
    client, revisions, _dep, _app = app_client
    monkeypatch.setattr(revisions.coordinator, "start_revision_run",
                        lambda **k: {"revision_run_id": "rev-1", "base_run_id": "base-1",
                                     "audit_checkpoints": [{"checkpoint_id": "c1"}]})
    resp = client.post("/api/admin/documents/doc-1/revisions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["revision_run_id"] == "rev-1"
    assert body["checkpoint_count"] == 1


def test_create_revision_no_base_returns_409(app_client, monkeypatch):
    client, revisions, _dep, _app = app_client

    def _boom(**k):
        raise ValueError("no adoptable base run")
    monkeypatch.setattr(revisions.coordinator, "start_revision_run", _boom)
    resp = client.post("/api/admin/documents/doc-1/revisions")
    assert resp.status_code == 409


def _claimable(monkeypatch, revisions, ok=True):
    """Stub the atomic DB run-claim (#414-1) as winner (ok) or loser (409)."""
    monkeypatch.setattr(revisions.persistence, "claim_revision_run",
                        lambda *, run_id, **k: ok)
    monkeypatch.setattr(revisions.persistence, "release_revision_run",
                        lambda *, run_id, **k: None)


def test_run_returns_202_and_launches_worker(app_client, monkeypatch):
    client, revisions, _dep, _app = app_client
    _claimable(monkeypatch, revisions, ok=True)
    launched = {}
    monkeypatch.setattr(revisions, "create_background_task", lambda *a, **k: None)
    monkeypatch.setattr(revisions, "update_background_task", lambda *a, **k: None)
    monkeypatch.setattr(revisions, "_launch_revision_worker",
                        lambda **kwargs: launched.update(kwargs))
    resp = client.post("/api/admin/documents/doc-1/revisions/rev-1/run", json={})
    assert resp.status_code == 202
    body = resp.json()
    assert body["revision_run_id"] == "rev-1"
    assert body["revision_status"] == "running"
    assert body["task_id"]
    # the long-running work was handed to the worker, not done in the request
    assert launched["revision_id"] == "rev-1"


def test_run_rejects_duplicate_via_failed_atomic_claim_with_409(app_client, monkeypatch):
    client, revisions, _dep, _app = app_client
    # The atomic claim lost the race -> 409, and no worker is started.
    _claimable(monkeypatch, revisions, ok=False)
    monkeypatch.setattr(revisions, "create_background_task",
                        lambda *a, **k: pytest.fail("must not start a duplicate run"))
    monkeypatch.setattr(revisions, "_launch_revision_worker",
                        lambda **kwargs: pytest.fail("must not launch worker on 409"))
    resp = client.post("/api/admin/documents/doc-1/revisions/rev-1/run", json={})
    assert resp.status_code == 409


def test_run_releases_claim_when_worker_launch_fails(app_client, monkeypatch):
    client, revisions, _dep, _app = app_client
    _claimable(monkeypatch, revisions, ok=True)
    released = {}
    monkeypatch.setattr(revisions.persistence, "release_revision_run",
                        lambda *, run_id, **k: released.update({"run_id": run_id}))
    monkeypatch.setattr(revisions, "create_background_task", lambda *a, **k: None)
    monkeypatch.setattr(revisions, "update_background_task", lambda *a, **k: None)

    def _boom(**kwargs):
        raise RuntimeError("thread pool exhausted")
    monkeypatch.setattr(revisions, "_launch_revision_worker", _boom)
    resp = client.post("/api/admin/documents/doc-1/revisions/rev-1/run", json={})
    assert resp.status_code == 500
    assert released.get("run_id") == "rev-1"  # claim released -> retryable


def test_run_worker_records_invalid_raw_operations(app_client, monkeypatch):
    client, revisions, _dep, _app = app_client
    _claimable(monkeypatch, revisions, ok=True)
    tasks: dict = {}

    def _update(task_id, status, result_data=None, error_message=None):
        tasks[task_id] = {"status": status, "result_data": result_data,
                          "error_message": error_message}
    monkeypatch.setattr(revisions, "create_background_task", lambda *a, **k: None)
    monkeypatch.setattr(revisions, "update_background_task", _update)

    def _invalid(**kwargs):
        raise revisions.coordinator.ProposalValidationError([
            {"raw": (kwargs.get("operations") or [{}])[0], "reason": "missing_source_or_evidence"},
        ])
    monkeypatch.setattr(revisions.coordinator, "run_revision_pipeline", _invalid)
    # run the worker synchronously so we can inspect the recorded outcome
    monkeypatch.setattr(revisions, "_launch_revision_worker",
                        lambda **kwargs: revisions._revision_run_worker(**kwargs))

    resp = client.post(
        "/api/admin/documents/doc-1/revisions/rev-1/run",
        json={"operations": [{"operation": "update_entity", "target_type": "claim",
                              "target_id": "clm_1"}]},
    )
    assert resp.status_code == 202
    failed = [t for t in tasks.values() if t["status"] == "failed"]
    assert failed and failed[0]["result_data"]["rejected_operations"][0]["reason"] == \
        "missing_source_or_evidence"


def test_run_status_attaches_only_owning_task(app_client, monkeypatch):
    client, revisions, _dep, _app = app_client
    # A task id from a *different* revision must not be attached; fall back to the
    # latest task that belongs to this revision (#414-2 / #414-4).
    monkeypatch.setattr(revisions, "get_background_task",
                        lambda task_id: {"task_id": task_id,
                                         "result_data": {"revision_run_id": "OTHER"}})
    monkeypatch.setattr(revisions, "get_latest_revision_task",
                        lambda rev: {"task_id": "own-task", "status": "processing",
                                     "stage": "audit",
                                     "result_data": {"revision_run_id": rev}})
    resp = client.get("/api/admin/documents/doc-1/revisions/rev-1/run-status?task_id=foreign")
    assert resp.status_code == 200
    body = resp.json()
    assert body["task"]["task_id"] == "own-task"
    assert body["task"]["result_data"]["revision_run_id"] == "rev-1"


def test_detail_returns_restore_fields(app_client, monkeypatch):
    client, revisions, _dep, _app = app_client
    monkeypatch.setattr(revisions.persistence, "get_analysis_run",
                        lambda *, run_id: {"id": run_id, "run_type": "revision",
                                           "document_id": "doc-1", "status": "failed",
                                           "revision_status": "proposed",
                                           "current_stage": "candidate_assembly",
                                           "error_message": "ProposalValidationError: 3",
                                           "base_run_id": "base-1",
                                           "stage_outputs": {"_artifacts": {}}})
    monkeypatch.setattr(revisions.persistence, "get_revision_decisions", lambda *, run_id: [])
    monkeypatch.setattr(revisions, "get_latest_revision_task",
                        lambda rev: {"task_id": "t1", "status": "failed", "stage": "candidate_assembly"})
    resp = client.get("/api/admin/documents/doc-1/revisions/rev-1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["current_stage"] == "candidate_assembly"
    assert body["error_message"].startswith("ProposalValidationError")
    assert body["latest_task"]["status"] == "failed"


def test_accept_success(app_client, monkeypatch):
    client, revisions, _dep, _app = app_client
    monkeypatch.setattr(revisions.coordinator, "accept_revision",
                        lambda **k: {"active_run_id": "rev-1", "superseded_run_id": "base-1"})
    resp = client.post("/api/admin/documents/doc-1/revisions/rev-1/accept",
                       json={"comment": "lgtm"})
    assert resp.status_code == 200
    assert resp.json()["accepted"] is True


def test_accept_hard_error_returns_422(app_client, monkeypatch):
    client, revisions, _dep, _app = app_client
    from core.document_pipeline.revision.coordinator import AcceptBlockedError

    def _blocked(**k):
        raise AcceptBlockedError("candidate has 3 hard error(s)")
    monkeypatch.setattr(revisions.coordinator, "accept_revision", _blocked)
    resp = client.post("/api/admin/documents/doc-1/revisions/rev-1/accept", json={})
    assert resp.status_code == 422


def test_accept_conflict_returns_409(app_client, monkeypatch):
    client, revisions, _dep, _app = app_client
    from core.document_pipeline.persistence import RevisionConflictError

    def _conflict(**k):
        raise RevisionConflictError("active run changed")
    monkeypatch.setattr(revisions.coordinator, "accept_revision", _conflict)
    resp = client.post("/api/admin/documents/doc-1/revisions/rev-1/accept", json={})
    assert resp.status_code == 409


def test_reject_success(app_client, monkeypatch):
    client, revisions, _dep, _app = app_client
    monkeypatch.setattr(revisions.coordinator, "reject_revision",
                        lambda **k: {"run_id": "rev-1"})
    resp = client.post("/api/admin/documents/doc-1/revisions/rev-1/reject",
                       json={"comment": "no"})
    assert resp.status_code == 200
    assert resp.json()["rejected"] is True


def test_revise_creates_child(app_client, monkeypatch):
    client, revisions, _dep, _app = app_client
    monkeypatch.setattr(revisions.coordinator, "revise_revision_run",
                        lambda **k: {"revision_run_id": "child-1", "base_run_id": "active-1",
                                     "parent_revision_id": "rev-1",
                                     "audit_checkpoints": []})
    resp = client.post("/api/admin/documents/doc-1/revisions/rev-1/revise",
                       json={"comment": "split eq"})
    assert resp.status_code == 200
    assert resp.json()["parent_revision_id"] == "rev-1"


def test_report_404_when_missing(app_client):
    client, _revisions, _dep, _app = app_client
    resp = client.get("/api/admin/documents/doc-1/revisions/rev-1/report")
    assert resp.status_code == 404


def test_wrong_document_returns_404(app_client, monkeypatch):
    client, revisions, _dep, _app = app_client
    monkeypatch.setattr(revisions.persistence, "get_analysis_run",
                        lambda *, run_id: {"id": run_id, "run_type": "revision",
                                           "document_id": "OTHER"})
    resp = client.post("/api/admin/documents/doc-1/revisions/rev-1/accept", json={})
    assert resp.status_code == 404


def test_list_revisions_distinguishes_active_and_latest(app_client, monkeypatch):
    client, revisions, _dep, _app = app_client
    monkeypatch.setattr(revisions.persistence, "get_run_lineage",
                        lambda *, document_id: {
                            "document_id": document_id,
                            "active_run_id": "r1", "latest_run_id": "r2",
                            "runs": [{"id": "r1", "is_active": True, "is_latest": False},
                                     {"id": "r2", "is_active": False, "is_latest": True}]})
    resp = client.get("/api/admin/documents/doc-1/revisions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["active_run_id"] == "r1"
    assert body["latest_run_id"] == "r2"
    assert body["active_run_id"] != body["latest_run_id"]


def test_authorization_required(app_client):
    client, _revisions, dep, app = app_client
    from fastapi import HTTPException

    def _forbidden():
        raise HTTPException(status_code=403, detail="teacher only")
    app.dependency_overrides[dep] = _forbidden
    resp = client.post("/api/admin/documents/doc-1/revisions")
    assert resp.status_code == 403


def test_write_endpoints_require_document_edit_permission(app_client, monkeypatch):
    client, revisions, _dep, _app = app_client
    from fastapi import HTTPException

    def _deny(doc, user):
        raise HTTPException(status_code=404, detail="Document not found")
    monkeypatch.setattr(revisions, "_authorize_edit", _deny)
    # accept/reject/revise/run/create must all be blocked
    for path, method in [
        ("/api/admin/documents/doc-1/revisions", "post"),
        ("/api/admin/documents/doc-1/revisions/rev-1/run", "post"),
        ("/api/admin/documents/doc-1/revisions/rev-1/accept", "post"),
        ("/api/admin/documents/doc-1/revisions/rev-1/reject", "post"),
        ("/api/admin/documents/doc-1/revisions/rev-1/revise", "post"),
    ]:
        resp = getattr(client, method)(path, json={})
        assert resp.status_code == 404, path


def test_read_endpoints_require_document_view_permission(app_client, monkeypatch):
    client, revisions, _dep, _app = app_client
    from fastapi import HTTPException

    def _deny(doc, user):
        raise HTTPException(status_code=404, detail="Document not found")
    monkeypatch.setattr(revisions, "_authorize_view", _deny)
    for path in [
        "/api/admin/documents/doc-1/revisions",
        "/api/admin/documents/doc-1/revisions/rev-1",
        "/api/admin/documents/doc-1/revisions/rev-1/report",
    ]:
        resp = client.get(path)
        assert resp.status_code == 404, path


def test_write_endpoint_does_not_run_coordinator_when_unauthorized(app_client, monkeypatch):
    client, revisions, _dep, _app = app_client
    from fastapi import HTTPException

    monkeypatch.setattr(revisions, "_authorize_edit",
                        lambda doc, user: (_ for _ in ()).throw(HTTPException(status_code=404)))
    monkeypatch.setattr(revisions.coordinator, "accept_revision",
                        lambda **k: pytest.fail("coordinator must not run when unauthorized"))
    resp = client.post("/api/admin/documents/doc-1/revisions/rev-1/accept", json={})
    assert resp.status_code == 404
