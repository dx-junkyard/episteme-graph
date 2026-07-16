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


def _functional_profile_is_consistent(profile: dict[str, Any]) -> bool:
    functions = [item for item in profile.get("functions") or [] if isinstance(item, dict)]
    ids = [str(item.get("id") or "") for item in functions]
    if not ids or any(not item for item in ids) or len(ids) != len(set(ids)):
        return False
    by_id = {str(item.get("id")): item for item in functions}
    for connection in profile.get("connections") or []:
        if not isinstance(connection, dict):
            return False
        source_id = str(connection.get("from_function_id") or "")
        target_id = str(connection.get("to_function_id") or "")
        output_id = str(connection.get("from_output_id") or "")
        input_id = str(connection.get("to_input_id") or "")
        if source_id not in by_id or target_id not in by_id or not output_id or not input_id:
            return False
        source_outputs = {
            str(port.get("id") or "")
            for port in by_id[source_id].get("outputs") or []
            if isinstance(port, dict)
        }
        target_inputs = {
            str(port.get("id") or "")
            for port in by_id[target_id].get("inputs") or []
            if isinstance(port, dict)
        }
        if output_id not in source_outputs or input_id not in target_inputs:
            return False
    return True


def _profile_has_reviewable_content(mode: str, profile: dict[str, Any]) -> bool:
    if mode == "functional_diagram":
        return _functional_profile_is_consistent(profile)
    if mode == "data_plot":
        return any(
            profile.get(key)
            for key in ("axes", "series", "observations", "interpretations", "highlights")
        )
    if mode == "descriptive_image":
        return bool(
            profile.get("summary")
            or profile.get("subjects")
            or profile.get("regions")
            or profile.get("teaching_points")
        )
    if mode == "mixed":
        return bool(profile.get("panels"))
    return False


def normalize_figure_analysis_candidate(value: Any) -> dict[str, Any] | None:
    """Validate and normalize a reviewable figure-analysis annotation body.

    Free-form ``{"text": ...}`` annotations from older dialogue turns are
    deliberately rejected: confirming them cannot safely create function/port
    relationships.  Only the structured output of the figure re-analysis path
    is eligible for teacher confirmation.
    """
    source = _json_object(value)
    mode_raw = str(source.get("presentation_mode") or source.get("suggested_mode") or "")
    mode = normalize_mode(mode_raw)
    if mode_raw.strip().lower() not in FIGURE_MODES or mode == "unknown":
        return None
    raw_profile = _json_object(source.get("analysis_profile"))
    if not raw_profile:
        return None
    normalized = analysis_profile_for_record({
        "suggested_mode": mode,
        "analysis_profile": raw_profile,
    })
    profile = normalized["analysis_profile"]
    if not _profile_has_reviewable_content(mode, profile):
        return None
    text = str(source.get("text") or source.get("summary") or "").strip()
    return {
        "candidate_type": "figure_analysis",
        "text": text or "図の構造化解析候補",
        "presentation_mode": mode,
        "analysis_profile": profile,
    }


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
                       analysis_profile, mode_reviewed_by::text, mode_reviewed_at,
                       reviewed_analysis_mode, reviewed_analysis_profile,
                       analysis_review_status, analysis_reviewed_by::text,
                       analysis_reviewed_at, analysis_review_source_annotation_id::text
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
                    mode_reviewed_at = CASE WHEN :reviewed_mode IS NULL THEN NULL ELSE now() END,
                    reviewed_analysis_mode = CASE
                        WHEN reviewed_analysis_mode = :reviewed_mode THEN reviewed_analysis_mode
                        ELSE NULL
                    END,
                    reviewed_analysis_profile = CASE
                        WHEN reviewed_analysis_mode = :reviewed_mode
                        THEN reviewed_analysis_profile ELSE '{}'::jsonb
                    END,
                    analysis_review_status = CASE
                        WHEN reviewed_analysis_mode = :reviewed_mode
                        THEN analysis_review_status ELSE 'pending'
                    END,
                    analysis_reviewed_by = CASE
                        WHEN reviewed_analysis_mode = :reviewed_mode
                        THEN analysis_reviewed_by ELSE NULL
                    END,
                    analysis_reviewed_at = CASE
                        WHEN reviewed_analysis_mode = :reviewed_mode
                        THEN analysis_reviewed_at ELSE NULL
                    END,
                    analysis_review_source_annotation_id = CASE
                        WHEN reviewed_analysis_mode = :reviewed_mode
                        THEN analysis_review_source_annotation_id ELSE NULL
                    END
                WHERE id = CAST(:figure_id AS uuid) AND document_id = :document_id
                RETURNING suggested_mode, reviewed_mode, mode_reason, mode_review_status,
                          analysis_profile, mode_reviewed_by::text, mode_reviewed_at,
                          reviewed_analysis_mode, reviewed_analysis_profile,
                          analysis_review_status, analysis_reviewed_by::text,
                          analysis_reviewed_at, analysis_review_source_annotation_id::text
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


def commit_reviewed_analysis(
    document_id: str,
    figure_id: str,
    candidate_body: Any,
    reviewed_by: str | None,
    source_annotation_id: str,
) -> dict[str, Any] | None:
    """Promote one structured candidate without overwriting the AI suggestion."""
    normalized = normalize_figure_analysis_candidate(candidate_body)
    if normalized is None:
        raise ValueError(
            "この候補は図の構造化解析を含みません。図を再解析してから確定してください"
        )
    mode = normalized["presentation_mode"]
    session = get_session()
    try:
        row = session.execute(
            sa_text(
                """
                UPDATE document_figures
                SET reviewed_mode = :reviewed_mode,
                    mode_review_status = 'reviewed',
                    mode_reviewed_by = CAST(:reviewed_by AS uuid),
                    mode_reviewed_at = now(),
                    reviewed_analysis_mode = :reviewed_mode,
                    reviewed_analysis_profile = CAST(:profile AS jsonb),
                    analysis_review_status = 'reviewed',
                    analysis_reviewed_by = CAST(:reviewed_by AS uuid),
                    analysis_reviewed_at = now(),
                    analysis_review_source_annotation_id = CAST(:annotation_id AS uuid)
                WHERE id = CAST(:figure_id AS uuid) AND document_id = :document_id
                RETURNING id::text
                """
            ),
            {
                "document_id": document_id,
                "figure_id": figure_id,
                "reviewed_mode": mode,
                "profile": json.dumps(normalized["analysis_profile"], ensure_ascii=False),
                "reviewed_by": reviewed_by,
                "annotation_id": source_annotation_id,
            },
        ).fetchone()
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    if not row:
        return None
    return {
        "type": "figure_analysis",
        "id": str(row[0]),
        "mode": mode,
        "source_annotation_id": source_annotation_id,
    }


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
    reviewed_profile = _json_object(item.get("reviewed_analysis_profile"))
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
    reviewed_analysis_mode = str(item.get("reviewed_analysis_mode") or "").strip().lower()
    has_reviewed_analysis = (
        reviewed_analysis_mode == effective
        and bool(reviewed_profile)
        and _profile_has_reviewable_content(effective, reviewed_profile)
    )
    if has_reviewed_analysis:
        profile = analysis_profile_for_record({
            "suggested_mode": effective,
            "analysis_profile": reviewed_profile,
        })["analysis_profile"]
        analysis_source = "teacher_reviewed"
    elif effective != suggested:
        # A teacher may correct the category before a matching specialist
        # analysis exists. Return a correctly shaped empty/fail-soft profile
        # instead of presenting the old category's analysis under the new one.
        profile = analysis_profile_for_record({
            "suggested_mode": effective,
            "mode_reason": reason,
            "analysis_profile": profile,
        })["analysis_profile"]
        analysis_source = "none"
    else:
        analysis_source = "ai_suggestion" if profile else "none"

    return {
        "suggested_mode": suggested,
        "reviewed_mode": reviewed,
        "effective_mode": effective,
        "mode_reason": reason,
        "mode_review_status": (
            MODE_REVIEW_REVIEWED if reviewed is not None else MODE_REVIEW_PENDING
        ),
        "analysis_profile": profile,
        "reviewed_analysis_mode": reviewed_analysis_mode or None,
        "analysis_review_status": (
            MODE_REVIEW_REVIEWED if has_reviewed_analysis else MODE_REVIEW_PENDING
        ),
        "analysis_source": analysis_source,
    }
