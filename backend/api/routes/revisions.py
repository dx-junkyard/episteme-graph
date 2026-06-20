"""Iterative-improvement (revision) API — accept / reject / revise + history (#407).

Mounted under the admin router (so paths live at ``/api/admin/documents/...``).
Latest-run (processing status) and active-run (adopted artifacts) are kept
distinct throughout; only ``accept`` switches the active run, and it does so
under optimistic concurrency (409 on base conflict).
"""
from __future__ import annotations

import logging
import threading
import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from dependencies import _require_teacher

from core.document_pipeline import persistence
from core.document_pipeline.persistence import RevisionConflictError
from core.document_pipeline.revision import coordinator
from core.document_pipeline.revision.coordinator import AcceptBlockedError
import time

from services import (
    create_background_task,
    get_background_task,
    get_latest_revision_task,
    update_background_task,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Revisions"])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class RevisionRunRequest(BaseModel):
    operations: list[dict] | None = None


class DecisionRequest(BaseModel):
    comment: str = ""
    confirm_protected: bool = False


def _user_id(current_user: dict) -> str | None:
    return current_user.get("id") or current_user.get("user_id")


def _require_run_for_document(document_id: str, revision_id: str) -> dict:
    run = persistence.get_analysis_run(run_id=revision_id)
    if not run or str(run.get("document_id")) != str(document_id):
        raise HTTPException(status_code=404, detail="revision not found for document")
    if run.get("run_type") != "revision":
        raise HTTPException(status_code=400, detail="not a revision run")
    return run


def _authorize_view(document_id: str, current_user: dict) -> None:
    """Require the caller can VIEW the document (#410 P1-4).

    Reuses the existing document authorization (visibility / group / owner / admin).
    A run's document_id match alone is NOT authorization: any teacher knowing the
    UUID could otherwise operate on another user's document.
    """
    from routes.theory_components import _ensure_document_viewable
    _ensure_document_viewable(document_id, current_user)


def _authorize_edit(document_id: str, current_user: dict) -> None:
    """Require the caller can EDIT the document (write / decision endpoints)."""
    from routes.theory_components import _ensure_document_editable
    _ensure_document_editable(document_id, current_user)


# ---------------------------------------------------------------------------
# Create / run
# ---------------------------------------------------------------------------

@router.post("/documents/{document_id}/revisions")
def create_revision(
    document_id: str,
    current_user: dict = Depends(_require_teacher),
):
    """Start a revision: resolve base (active) run + build inventory/checkpoints."""
    _authorize_edit(document_id, current_user)
    try:
        result = coordinator.start_revision_run(
            document_id=document_id, created_by=_user_id(current_user),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {
        "revision_run_id": result["revision_run_id"],
        "base_run_id": result["base_run_id"],
        "checkpoint_count": len(result.get("audit_checkpoints") or []),
        "revision_status": "preparing",
    }


# Minimum seconds between throttled progress writes, so a 199-checkpoint audit
# does not hammer the DB once per checkpoint (#414-3).
_PROGRESS_MIN_INTERVAL = 1.0


def _revision_run_worker(
    *,
    task_id: str,
    document_id: str,
    revision_id: str,
    operations: list[dict] | None = None,
) -> None:
    """Background worker: run the whole revision pipeline off the HTTP request.

    Long-running LLM audit/proposal work no longer blocks the request (so Nginx
    never 504s mid-run); progress — including ``completed / total`` per stage —
    is observable via the task + revision status (#412 P0-2 / #414-3). Failures
    are recorded on the task so the UI can show a real server error rather than a
    generic timeout.
    """
    def _result(**extra) -> dict:
        return {"document_id": document_id, "revision_run_id": revision_id, **extra}

    state = {"last": 0.0}

    def on_progress(stage: str, completed: int, total: int) -> None:
        now = time.monotonic()
        boundary = (completed == 0) or (total and completed >= total)
        if not boundary and (now - state["last"]) < _PROGRESS_MIN_INTERVAL:
            return
        state["last"] = now
        pct = int(completed * 100 / total) if total else 0
        update_background_task(
            task_id, "processing",
            result_data=_result(stage=stage, label="反復改善",
                                 completed_count=completed, total_count=total,
                                 progress=pct),
        )

    try:
        update_background_task(
            task_id, "processing",
            result_data=_result(stage="audit", label="反復改善",
                                 completed_count=0, total_count=0, progress=0),
        )
        result = coordinator.run_revision_pipeline(
            run_id=revision_id, operations=operations,
            progress_callback=on_progress, task_id=task_id,
        )
        update_background_task(
            task_id, "completed",
            result_data=_result(
                stage="completed", revision_status="proposed", progress=100,
                candidate_invalid=result.get("candidate_invalid"),
                report_summary=result.get("report_summary"),
                stage_summary=result.get("stage_summary"),
            ),
        )
    except coordinator.ProposalValidationError as exc:
        update_background_task(
            task_id, "failed",
            result_data=_result(stage="candidate_assembly",
                                 rejected_operations=exc.rejected, progress=100),
            error_message="proposal validation failed",
        )
    except Exception as exc:  # noqa: BLE001 — worker must record any failure
        logger.exception("revision run worker failed: task=%s run=%s", task_id, revision_id)
        update_background_task(
            task_id, "failed",
            result_data=_result(stage="failed", progress=100),
            error_message=coordinator._safe_error(type(exc).__name__, exc),
        )


def _launch_revision_worker(**kwargs) -> None:
    """Spawn the revision worker thread (indirection kept for testability)."""
    thread = threading.Thread(target=_revision_run_worker, kwargs=kwargs, daemon=True)
    thread.start()


@router.post("/documents/{document_id}/revisions/{revision_id}/run")
def run_revision(
    document_id: str,
    revision_id: str,
    body: RevisionRunRequest = Body(default=RevisionRunRequest()),
    current_user: dict = Depends(_require_teacher),
):
    """Start the revision pipeline asynchronously and return 202 + a task id.

    Source re-audit, audit-driven proposal generation (#410 P1-6), candidate
    assembly and validation run in a background worker; the request returns
    immediately so it can never exceed the proxy read timeout (#412 P0-2).
    Supplying ``operations`` is a debug/admin override (raw JSON), still validated
    server-side in the worker. A duplicate ``/run`` while one is in progress is
    rejected with 409.
    """
    _authorize_edit(document_id, current_user)
    _require_run_for_document(document_id, revision_id)
    # Acquire the right to run atomically in the DB — not via a read-then-write
    # check — so two concurrent POSTs cannot both start a worker (#414-1). The
    # loser gets 409. Works across multiple API processes.
    if not persistence.claim_revision_run(run_id=revision_id):
        raise HTTPException(status_code=409, detail="revision run already in progress")

    task_id = str(uuid.uuid4())[:12]
    try:
        create_background_task(task_id, "revision_pipeline", _user_id(current_user))
        update_background_task(task_id, "pending", result_data={
            "document_id": document_id, "revision_run_id": revision_id,
            "stage": "queued", "label": "反復改善",
            "completed_count": 0, "total_count": 0, "progress": 0,
        })
        _launch_revision_worker(
            task_id=task_id, document_id=document_id,
            revision_id=revision_id, operations=body.operations,
        )
    except Exception as exc:  # noqa: BLE001
        # Task/worker could not start: release the claim so the run is retryable
        # rather than stuck in 'running' (#414-1).
        persistence.release_revision_run(
            run_id=revision_id,
            error_message=coordinator._safe_error("worker_launch_failed", exc),
        )
        logger.exception("failed to launch revision worker: run=%s", revision_id)
        raise HTTPException(status_code=500, detail="failed to start revision run")
    logger.info(
        "revision run accepted: task=%s run=%s document=%s source=%s",
        task_id, revision_id, document_id, "raw" if body.operations is not None else "generated",
    )
    return JSONResponse(status_code=202, content={
        "task_id": task_id,
        "revision_run_id": revision_id,
        "revision_status": "running",
        "status": "accepted",
    })


@router.get("/documents/{document_id}/revisions/{revision_id}/run-status")
def get_revision_run_status(
    document_id: str,
    revision_id: str,
    task_id: str | None = Query(default=None),
    current_user: dict = Depends(_require_teacher),
):
    """Poll a revision run's state (+ optional background-task progress).

    Lets the UI recover after a reload / dropped connection: the run's generic
    status/current_stage is the source of truth, and the task carries live stage
    progress while the worker is active (#412 P0-2 / P1-4).
    """
    _authorize_view(document_id, current_user)
    run = _require_run_for_document(document_id, revision_id)
    out = {
        "revision_run_id": revision_id,
        "document_id": document_id,
        "status": run.get("status"),
        "revision_status": run.get("revision_status"),
        "current_stage": run.get("current_stage"),
        "error_message": run.get("error_message") or "",
    }
    # Attach the explicitly-requested task only if it belongs to this revision;
    # otherwise fall back to the latest revision task. Never leak another
    # revision's task progress (#414-2 / #414-4).
    task = get_background_task(task_id) if task_id else None
    if not _task_belongs_to_revision(task, revision_id):
        task = get_latest_revision_task(revision_id)
    if task:
        out["task"] = task
    return out


def _task_belongs_to_revision(task: dict | None, revision_id: str) -> bool:
    if not task:
        return False
    result_data = task.get("result_data") or {}
    return str(result_data.get("revision_run_id") or "") == str(revision_id)


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

@router.get("/documents/{document_id}/revisions")
def list_revisions(
    document_id: str,
    current_user: dict = Depends(_require_teacher),
):
    """List run lineage, distinguishing the latest run from the active (adopted) run."""
    _authorize_view(document_id, current_user)
    lineage = persistence.get_run_lineage(document_id=document_id)
    return lineage


@router.get("/documents/{document_id}/revisions/{revision_id}")
def get_revision(
    document_id: str,
    revision_id: str,
    current_user: dict = Depends(_require_teacher),
):
    _authorize_view(document_id, current_user)
    run = _require_run_for_document(document_id, revision_id)
    from core.document_pipeline.revision import get_artifacts
    artifacts = get_artifacts(run.get("stage_outputs"))
    return {
        "revision_run_id": revision_id,
        "document_id": document_id,
        "run_type": run.get("run_type"),
        "status": run.get("status"),
        "revision_status": run.get("revision_status"),
        "base_run_id": run.get("base_run_id"),
        "parent_revision_id": run.get("parent_revision_id"),
        "current_stage": run.get("current_stage"),
        "error_message": run.get("error_message") or "",
        "checkpoint_count": len(artifacts.get("audit_checkpoints") or []),
        "has_candidate": "candidate" in artifacts,
        "has_report": "diff_report" in artifacts,
        # Latest revision task so the UI can restore running/failed/completed
        # state and resume polling after a reload (#414-4).
        "latest_task": get_latest_revision_task(revision_id),
        "decisions": persistence.get_revision_decisions(run_id=revision_id),
    }


@router.get("/documents/{document_id}/revisions/{revision_id}/report")
def get_revision_report(
    document_id: str,
    revision_id: str,
    current_user: dict = Depends(_require_teacher),
):
    _authorize_view(document_id, current_user)
    run = _require_run_for_document(document_id, revision_id)
    from core.document_pipeline.revision import get_artifacts
    artifacts = get_artifacts(run.get("stage_outputs"))
    report = artifacts.get("diff_report")
    if not report:
        raise HTTPException(status_code=404, detail="diff report not generated yet")
    return report


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------

@router.post("/documents/{document_id}/revisions/{revision_id}/accept")
def accept_revision(
    document_id: str,
    revision_id: str,
    body: DecisionRequest = Body(default=DecisionRequest()),
    current_user: dict = Depends(_require_teacher),
):
    _authorize_edit(document_id, current_user)
    _require_run_for_document(document_id, revision_id)
    try:
        result = coordinator.accept_revision(
            document_id=document_id, run_id=revision_id,
            changed_by=_user_id(current_user), comment=body.comment,
            confirm_protected=body.confirm_protected,
        )
    except AcceptBlockedError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except RevisionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"accepted": True, **result}


@router.post("/documents/{document_id}/revisions/{revision_id}/reject")
def reject_revision(
    document_id: str,
    revision_id: str,
    body: DecisionRequest = Body(default=DecisionRequest()),
    current_user: dict = Depends(_require_teacher),
):
    _authorize_edit(document_id, current_user)
    _require_run_for_document(document_id, revision_id)
    try:
        result = coordinator.reject_revision(
            run_id=revision_id, changed_by=_user_id(current_user), comment=body.comment,
        )
    except RevisionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"rejected": True, **result}


@router.post("/documents/{document_id}/revisions/{revision_id}/revise")
def revise_revision(
    document_id: str,
    revision_id: str,
    body: DecisionRequest = Body(default=DecisionRequest()),
    current_user: dict = Depends(_require_teacher),
):
    _authorize_edit(document_id, current_user)
    _require_run_for_document(document_id, revision_id)
    try:
        result = coordinator.revise_revision_run(
            document_id=document_id, parent_run_id=revision_id,
            created_by=_user_id(current_user), comment=body.comment,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {
        "revision_run_id": result["revision_run_id"],
        "base_run_id": result["base_run_id"],
        "parent_revision_id": result["parent_revision_id"],
        "checkpoint_count": len(result.get("audit_checkpoints") or []),
        "revision_status": "preparing",
    }
