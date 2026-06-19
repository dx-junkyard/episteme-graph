"""Iterative-improvement (revision) API — accept / reject / revise + history (#407).

Mounted under the admin router (so paths live at ``/api/admin/documents/...``).
Latest-run (processing status) and active-run (adopted artifacts) are kept
distinct throughout; only ``accept`` switches the active run, and it does so
under optimistic concurrency (409 on base conflict).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel

from dependencies import _require_teacher

from core.document_pipeline import persistence
from core.document_pipeline.persistence import RevisionConflictError
from core.document_pipeline.revision import coordinator
from core.document_pipeline.revision.coordinator import AcceptBlockedError

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


@router.post("/documents/{document_id}/revisions/{revision_id}/run")
def run_revision(
    document_id: str,
    revision_id: str,
    body: RevisionRunRequest = Body(default=RevisionRunRequest()),
    current_user: dict = Depends(_require_teacher),
):
    """Run source re-audit, build revision-operation proposals, assemble + validate.

    Operations come from the audit-driven proposal generator by default (#410
    P1-6); supplying ``operations`` is a debug/admin override (raw JSON), still
    validated server-side during candidate assembly.
    """
    _authorize_edit(document_id, current_user)
    _require_run_for_document(document_id, revision_id)
    audit = coordinator.audit_revision_run(run_id=revision_id)
    response = {"revision_run_id": revision_id, "audit": {
        "verdict_counts": audit.get("verdict_counts"),
        "revision_targets": audit.get("revision_targets"),
    }}

    if body.operations is not None:
        operations = body.operations
        response["operation_source"] = "raw"
    else:
        proposals = coordinator.generate_proposals(run_id=revision_id)
        operations = proposals.get("operations") or []
        response["operation_source"] = "generated"
        response["proposed_count"] = len(operations)
        response["manual_review"] = proposals.get("manual_review") or []

    candidate = coordinator.assemble_candidate(run_id=revision_id, operations=operations)
    report = coordinator.revalidate_and_report(run_id=revision_id)
    response["candidate_invalid"] = candidate["invalid"]
    response["report_summary"] = report["report"]["summary"]
    response["revision_status"] = "proposed"
    return response


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
        "checkpoint_count": len(artifacts.get("audit_checkpoints") or []),
        "has_candidate": "candidate" in artifacts,
        "has_report": "diff_report" in artifacts,
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
