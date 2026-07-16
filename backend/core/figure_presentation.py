"""Persistence and API projection for figure presentation modes (#496)."""
from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from typing import Any, Iterable

from sqlalchemy import text as sa_text

from core.postgres import get_session
from episteme_graph.agents.figure_modes import (
    FIGURE_MODES,
    MODE_REVIEW_PENDING,
    MODE_REVIEW_REVIEWED,
    analysis_profile_for_record,
    effective_mode,
    normalize_mode,
)


def _plain(value: Any) -> dict[str, Any]:
    if is_dataclass(value):
        return asdict(value)
    return dict(value) if isinstance(value, dict) else {}


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return dict(parsed) if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def persist_suggestions(document_id: str, records: Iterable[Any]) -> int:
    """Persist candidate vision output without changing a teacher override."""
    session = get_session()
    updated = 0
    try:
        for raw in records:
            record = _plain(raw)
            figure_id = str(record.get("figure_id") or "")
            if not figure_id:
                continue
            normalized = analysis_profile_for_record(record)
            result = session.execute(
                sa_text(
                    """
                    UPDATE document_figures
                    SET suggested_mode = :suggested_mode,
                        mode_reason = :mode_reason,
                        analysis_profile = CAST(:analysis_profile AS jsonb)
                    WHERE id = CAST(:figure_id AS uuid)
                      AND document_id = :document_id
                    """
                ),
                {
                    "figure_id": figure_id,
                    "document_id": document_id,
                    "suggested_mode": normalized["suggested_mode"],
                    "mode_reason": normalized["mode_reason"],
                    "analysis_profile": json.dumps(
                        normalized["analysis_profile"], ensure_ascii=False
                    ),
                },
            )
            updated += max(0, int(getattr(result, "rowcount", 0) or 0))
        session.commit()
        return updated
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def set_reviewed_mode(
    document_id: str,
    figure_id: str,
    reviewed_mode: str | None,
    reviewed_by: str | None,
) -> dict[str, Any] | None:
    """Set or clear a teacher override and return old/new projection data."""
    if reviewed_mode is not None and str(reviewed_mode).strip().lower() not in FIGURE_MODES:
        raise ValueError(f"invalid figure presentation mode: {reviewed_mode!r}")
    normalized_reviewed = (
        normalize_mode(reviewed_mode) if reviewed_mode is not None else None
    )
    new_status = MODE_REVIEW_REVIEWED if normalized_reviewed is not None else MODE_REVIEW_PENDING

    session = get_session()
    try:
        old = session.execute(
            sa_text(
                """
                SELECT suggested_mode, reviewed_mode, mode_reason, mode_review_status,
                       analysis_profile, mode_reviewed_by::text, mode_reviewed_at
                FROM document_figures
                WHERE id = CAST(:figure_id AS uuid) AND document_id = :document_id
                FOR UPDATE
                """
            ),
            {"figure_id": figure_id, "document_id": document_id},
        ).mappings().first()
        if not old:
            session.rollback()
            return None
        updated = session.execute(
            sa_text(
                """
                UPDATE document_figures
                SET reviewed_mode = :reviewed_mode,
                    mode_review_status = :mode_review_status,
                    mode_reviewed_by = CAST(:reviewed_by AS uuid),
                    mode_reviewed_at = CASE WHEN :reviewed_mode IS NULL THEN NULL ELSE now() END
                WHERE id = CAST(:figure_id AS uuid) AND document_id = :document_id
                RETURNING suggested_mode, reviewed_mode, mode_reason, mode_review_status,
                          analysis_profile, mode_reviewed_by::text, mode_reviewed_at
                """
            ),
            {
                "figure_id": figure_id,
                "document_id": document_id,
                "reviewed_mode": normalized_reviewed,
                "mode_review_status": new_status,
                "reviewed_by": reviewed_by,
            },
        ).mappings().first()
        session.commit()
        if not updated:
            return None
        return {
            "old": dict(old),
            "new": dict(updated),
            **presentation_payload(dict(updated)),
        }
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def presentation_payload(
    row: Any,
    artifact_record: Any = None,
    *,
    caption_text: str = "",
) -> dict[str, Any]:
    """Build a stable response contract from DB data plus an optional old artifact."""
    item = _plain(row)
    artifact = _plain(artifact_record)
    persisted_profile = _json_object(item.get("analysis_profile"))
    persisted_suggested = normalize_mode(item.get("suggested_mode"))
    artifact_normalized = analysis_profile_for_record(artifact, caption_text=caption_text)

    # Migration defaults old rows to unknown/empty. Prefer a usable old run
    # artifact in that one compatibility case.
    if (
        persisted_suggested == "unknown"
        and not persisted_profile
        and artifact
        and artifact_normalized["suggested_mode"] != "unknown"
    ):
        suggested = artifact_normalized["suggested_mode"]
        reason = artifact_normalized["mode_reason"]
        profile = artifact_normalized["analysis_profile"]
    else:
        suggested = persisted_suggested
        reason = str(item.get("mode_reason") or "")
        profile = persisted_profile

    reviewed = str(item.get("reviewed_mode") or "").strip().lower() or None
    if reviewed not in FIGURE_MODES:
        reviewed = None
    effective = effective_mode(suggested, reviewed)
    if effective != suggested:
        # A teacher may correct the category before a matching specialist
        # analysis exists. Return a correctly shaped empty/fail-soft profile
        # instead of presenting the old category's analysis under the new one.
        profile = analysis_profile_for_record({
            "suggested_mode": effective,
            "mode_reason": reason,
            "analysis_profile": profile,
        })["analysis_profile"]

    return {
        "suggested_mode": suggested,
        "reviewed_mode": reviewed,
        "effective_mode": effective,
        "mode_reason": reason,
        "mode_review_status": (
            MODE_REVIEW_REVIEWED if reviewed is not None else MODE_REVIEW_PENDING
        ),
        "analysis_profile": profile,
    }

