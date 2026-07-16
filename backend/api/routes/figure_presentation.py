"""Figure presentation-mode API (#496).

This router is mounted before the legacy admin router so its enriched figures
GET is authoritative while retaining the existing URL and response fields.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from dependencies import _require_teacher
from core import figure_reanalysis
from core.deliberation.identity_links import confidence_label
from core.document_pipeline.figure_images import load_document_figures
from core.document_pipeline.persistence import get_latest_analysis_run
from core.figure_presentation import presentation_payload, set_reviewed_mode
from core.schema import AUDIT_ENTITY_FIGURE_PRESENTATION
from routes.theory_components import _ensure_document_editable, _ensure_document_viewable
from services import record_review_event


router = APIRouter(prefix="/api/admin", tags=["figure-presentation"])


def _canonical_document_id(chunks: list[dict], fallback: str) -> str:
    for chunk in chunks:
        if chunk.get("document_id"):
            return str(chunk["document_id"])
    return fallback


def _latest_records(document_id: str) -> dict[str, dict]:
    """Read current or pre-#496 artifacts; absence is a normal old-run case."""
    try:
        latest_run = get_latest_analysis_run(document_id=document_id)
        artifacts = (((latest_run or {}).get("stage_outputs") or {}).get("_artifacts") or {})
        records = (artifacts.get("apparatus_semantics") or {}).get("apparatus_records") or []
        return {
            str(record.get("figure_id")): record
            for record in records
            if isinstance(record, dict) and record.get("figure_id")
        }
    except Exception:
        return {}


@router.get("/documents/{document_id}/figures")
def list_document_figures_with_presentation(
    document_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """Return the existing figures contract plus classification/profile data."""
    chunks = _ensure_document_viewable(document_id, current_user)
    canonical_document_id = _canonical_document_id(chunks, document_id)
    rows = load_document_figures(canonical_document_id)
    artifacts = _latest_records(canonical_document_id)
    figures: list[dict] = []
    for row in rows:
        figure_id = str(row.get("id") or "")
        artifact = artifacts.get(figure_id) or {}
        presentation = presentation_payload(
            row,
            artifact,
            caption_text=str(row.get("caption_text") or ""),
        )
        parts = [
            {
                "name": part.get("name", ""),
                "role": part.get("role", ""),
                "label_ref": part.get("label_ref"),
                "expanded_name": part.get("expanded_name", ""),
                "bbox": part.get("bbox"),
                "evidence_quote": part.get("evidence_quote", ""),
                "reason": part.get("reason", ""),
                "confidence": part.get("confidence", 0.0),
            }
            for part in (artifact.get("parts") or [])
            if isinstance(part, dict)
        ]
        connections = [
            {
                "from_part": connection.get("from_part", ""),
                "to_part": connection.get("to_part", ""),
                "relation": connection.get("relation", ""),
                "reason": connection.get("reason", ""),
                "confidence": connection.get("confidence", 0.0),
            }
            for connection in (artifact.get("connections") or [])
            if isinstance(connection, dict)
        ]
        apparatus_candidates = []
        if artifact and presentation["effective_mode"] in ("functional_diagram", "mixed"):
            apparatus_candidates.append({
                "apparatus_name_candidate": artifact.get("apparatus_name_candidate", ""),
                "match_status": artifact.get("match_status", "unknown"),
                "parts": parts,
                "connections": connections,
                "confidence": artifact.get("confidence", 0.0),
                "review_status": artifact.get("review_status", "review_required"),
                "matched_library_entry_id": artifact.get("matched_library_entry_id"),
            })
        figures.append({
            "id": figure_id,
            "figure_key": row.get("figure_key"),
            "figure_label": row.get("figure_label"),
            "page": row.get("page"),
            "caption_text": row.get("caption_text"),
            "extraction_method": row.get("extraction_method"),
            "region_confidence": row.get("region_confidence"),
            "status": row.get("status"),
            "image_url": f"/api/admin/documents/{document_id}/figures/{figure_id}/image",
            "bbox": row.get("bbox"),
            "inner_labels": row.get("inner_labels") or [],
            "apparatus_candidates": apparatus_candidates,
            **presentation,
        })
    return {"figures": figures}


class FigurePresentationModePatch(BaseModel):
    presentation_mode: str | None


@router.patch("/documents/{document_id}/figures/{figure_id}/presentation-mode")
def update_figure_presentation_mode(
    document_id: str,
    figure_id: str,
    body: FigurePresentationModePatch,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """Set a teacher override; ``null`` clears it and resumes the suggestion."""
    chunks = _ensure_document_editable(document_id, current_user)
    canonical_document_id = _canonical_document_id(chunks, document_id)
    try:
        result = set_reviewed_mode(
            canonical_document_id,
            figure_id,
            body.presentation_mode,
            current_user.get("id"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Figure not found")

    old = result.pop("old", {})
    new = result.pop("new", {})
    record_review_event(
        AUDIT_ENTITY_FIGURE_PRESENTATION,
        figure_id,
        str(old.get("reviewed_mode") or ""),
        str(new.get("reviewed_mode") or ""),
        current_user.get("id"),
        {
            "action": "figure.presentation_mode.review",
            "document_id": canonical_document_id,
            "suggested_mode": result.get("suggested_mode"),
            "review_cleared": body.presentation_mode is None,
        },
    )
    return {"figure_id": figure_id, **result}


@router.post("/documents/{document_id}/figures/{figure_id}/reanalyze")
def reanalyze_document_figure(
    document_id: str,
    figure_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """Create a structured AI candidate for one figure; never auto-confirm it."""
    chunks = _ensure_document_editable(document_id, current_user)
    canonical_document_id = _canonical_document_id(chunks, document_id)
    try:
        result = figure_reanalysis.reanalyze_figure(
            canonical_document_id,
            figure_id,
            created_by=current_user.get("id"),
        )
    except figure_reanalysis.FigureReanalysisError as exc:
        status = {"not_found": 404, "limit": 429}.get(exc.kind, 422)
        raise HTTPException(status_code=status, detail=str(exc)) from exc

    annotation = result.pop("annotation")
    record_review_event(
        AUDIT_ENTITY_FIGURE_PRESENTATION,
        figure_id,
        "",
        "candidate",
        current_user.get("id"),
        {
            "action": "figure.analysis.reanalyze",
            "document_id": canonical_document_id,
            "annotation_id": annotation.get("id"),
            "suggested_mode": result.get("suggested_mode"),
        },
    )
    return {
        **result,
        "annotation": {
            "id": annotation.get("id"),
            "kind": annotation.get("kind"),
            "body": annotation.get("body") or {},
            "evidence": annotation.get("evidence") or [],
            "reason": annotation.get("reason") or "",
            "confidence_label": confidence_label(annotation.get("confidence")),
            "status": annotation.get("status"),
            "committed_target": {},
            "created_at": annotation.get("created_at") or "",
            "commit_supported": True,
            "commit_note": "",
        },
    }
