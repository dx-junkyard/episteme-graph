"""Lecture Studio theory component APIs."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text as sa_text

from dependencies import ROLE_SYSTEM_ADMIN, _require_teacher
from schemas import (
    ClaimExtractResponse,
    ClaimOut,
    ClaimUpsertRequest,
    ComponentAssembleRequest,
    ComponentAssembleResponse,
    ComponentGraphResponse,
    TheoryComponentExtractRequest,
    TheoryComponentExtractResponse,
    TheoryComponentOut,
    TheoryComponentUpsertRequest,
    TheoryConnectionValidateRequest,
)
from services import (
    create_background_task,
    get_editable_course_data,
    get_viewable_course_data,
    reanalyze_course_structure_background,
    update_background_task,
)
from core.postgres import get_session as _pg_session
from core.theory_components import (
    enrich_theory_components_with_llm,
    extract_theory_components_from_dsl,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Theory Components"])


_JSON_FIELDS = (
    "source_scope",
    "evidence_claims",
    "source_chunks",
    "inputs",
    "outputs",
    "preconditions",
    "cautions",
    "constraints",
    "invalid_conditions",
    "dependencies",
    "connectors",
    "blackbox_policy",
    "validation_warnings",
)

_CLAIM_TYPES = {
    "definition",
    "assumption",
    "approximation",
    "equation",
    "relation",
    "derivation_step",
    "observable_definition",
    "correction",
    "uncertainty",
    "limitation",
    "result",
    "diagnostic_claim",
}


def _dump_model(value: Any) -> dict:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value.dict()


def _json_value(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return json.loads(value)
        except Exception:
            return default
    return default


def _row_to_out(row: Any) -> TheoryComponentOut:
    data = {
        "id": str(row[0]),
        "course_id": row[1],
        "primary_chunk_id": str(row[2]) if row[2] else None,
        "name": row[3],
        "component_type": row[4],
        "summary": row[5] or "",
        "status": row[6],
        "source_chunks": _json_value(row[7], []),
        "inputs": _json_value(row[8], []),
        "outputs": _json_value(row[9], []),
        "preconditions": _json_value(row[10], []),
        "constraints": _json_value(row[11], []),
        "invalid_conditions": _json_value(row[12], []),
        "dependencies": _json_value(row[13], []),
        "blackbox_policy": _json_value(row[14], {}),
        "validation_warnings": _json_value(row[15], []),
        "teacher_notes": row[16] or "",
        "source_scope": _json_value(row[17], {}),
        "evidence_claims": _json_value(row[18], []),
        "maturity_level": row[19] or "paper_claim",
        "maturity_source": row[20] or "llm_proposed",
        "review_status": row[21] or "teacher_review_required",
        "cautions": _json_value(row[22], []),
        "connectors": _json_value(row[23], {}),
        "created_at": row[24].isoformat() if row[24] else "",
        "updated_at": row[25].isoformat() if row[25] else "",
    }
    return TheoryComponentOut(**data)


def _select_components_sql(where: str) -> str:
    return f"""
        SELECT id, course_id, primary_chunk_id, name, component_type, summary, status,
               source_chunks, inputs, outputs, preconditions, constraints,
               invalid_conditions, dependencies, blackbox_policy, validation_warnings,
               teacher_notes, source_scope, evidence_claims, maturity_level, maturity_source,
               review_status, cautions, connectors, created_at, updated_at
        FROM theory_components
        WHERE {where}
        ORDER BY updated_at DESC, created_at DESC
    """


def _get_component(component_id: str) -> TheoryComponentOut | None:
    session = _pg_session()
    try:
        row = session.execute(
            sa_text(_select_components_sql("id = CAST(:id AS uuid)")),
            {"id": component_id},
        ).fetchone()
        return _row_to_out(row) if row else None
    finally:
        session.close()


def _course_exists(course_id: str) -> bool:
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("SELECT 1 FROM learning_courses WHERE id = :course_id LIMIT 1"),
            {"course_id": course_id},
        ).fetchone()
        return row is not None
    finally:
        session.close()


def _system_admin_course_data(course_id: str) -> dict | None:
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("SELECT data FROM learning_courses WHERE id = :course_id LIMIT 1"),
            {"course_id": course_id},
        ).fetchone()
        if not row or not row[0]:
            return None
        return row[0] if isinstance(row[0], dict) else json.loads(row[0])
    finally:
        session.close()


def _editable_course_data(course_id: str, current_user: dict) -> dict:
    course_data = get_editable_course_data(current_user["id"], course_id)
    if not course_data and current_user.get("role") == ROLE_SYSTEM_ADMIN:
        course_data = _system_admin_course_data(course_id)
    if not course_data:
        raise HTTPException(status_code=404, detail="Course not found")
    return course_data


def _ensure_viewable(course_id: str, current_user: dict) -> None:
    if current_user.get("role") == ROLE_SYSTEM_ADMIN:
        if not _course_exists(course_id):
            raise HTTPException(status_code=404, detail="Course not found")
        return
    if not get_viewable_course_data(current_user["id"], course_id):
        raise HTTPException(status_code=404, detail="Course not found")


def _ensure_editable(course_id: str, current_user: dict) -> None:
    if current_user.get("role") == ROLE_SYSTEM_ADMIN:
        if not _course_exists(course_id):
            raise HTTPException(status_code=404, detail="Course not found")
        return
    if not get_editable_course_data(current_user["id"], course_id):
        raise HTTPException(status_code=404, detail="Course not found")


def _chunk_row(chunk_id: str) -> dict | None:
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("""
                SELECT id, chunk_index, text, display_text, material_id, document_id,
                       page_start, page_end, smiles_dsl, variables, ancestors
                FROM chunks
                WHERE id = CAST(:chunk_id AS uuid)
                LIMIT 1
            """),
            {"chunk_id": chunk_id},
        ).fetchone()
        if not row:
            return None
        return {
            "id": str(row[0]),
            "chunk_index": row[1],
            "raw_text": row[2] or "",
            "text": row[3] or row[2] or "",
            "display_text": row[3] or row[2] or "",
            "material_id": row[4] or "",
            "document_id": str(row[5]) if row[5] else "",
            "page_start": row[6],
            "page_end": row[7],
            "smiles_dsl": row[8] or "",
            "variables": _json_value(row[9], None),
            "ancestors": _json_value(row[10], []),
            "graph_elements": [],
        }
    finally:
        session.close()


def _find_course_for_chunk(chunk: dict, current_user: dict) -> str | None:
    material_id = chunk.get("material_id")
    if not material_id:
        return None
    session = _pg_session()
    try:
        rows = session.execute(
            sa_text("""
                SELECT id
                FROM learning_courses
                WHERE data IS NOT NULL
                  AND EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements(COALESCE(data->'sources', '[]'::jsonb)) AS src
                    WHERE src->>'material_id' = :material_id
                  )
            """),
            {"material_id": material_id},
        ).fetchall()
    finally:
        session.close()
    for row in rows:
        course_id = row[0]
        try:
            _ensure_editable(course_id, current_user)
            return course_id
        except HTTPException:
            continue
    return None


def _find_viewable_course_for_chunk(chunk: dict, current_user: dict) -> str | None:
    material_id = chunk.get("material_id")
    if not material_id:
        return None
    session = _pg_session()
    try:
        rows = session.execute(
            sa_text("""
                SELECT id
                FROM learning_courses
                WHERE data IS NOT NULL
                  AND EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements(COALESCE(data->'sources', '[]'::jsonb)) AS src
                    WHERE src->>'material_id' = :material_id
                  )
            """),
            {"material_id": material_id},
        ).fetchall()
    finally:
        session.close()
    for row in rows:
        course_id = row[0]
        try:
            _ensure_viewable(course_id, current_user)
            return course_id
        except HTTPException:
            continue
    return None


def _course_chunks(course_data: dict) -> list[dict]:
    sources = course_data.get("sources", []) if isinstance(course_data, dict) else []
    material_ids = [
        str(s.get("material_id")).strip()
        for s in sources
        if isinstance(s, dict) and s.get("material_id")
    ]
    material_ids = list(dict.fromkeys(material_ids))
    if not material_ids:
        return []
    session = _pg_session()
    try:
        placeholders = ", ".join(f":mid_{i}" for i in range(len(material_ids)))
        rows = session.execute(
            sa_text(f"""
                SELECT id, chunk_index, text, display_text, material_id, document_id,
                       page_start, page_end, smiles_dsl, variables, ancestors
                FROM chunks
                WHERE material_id IN ({placeholders})
                  AND text IS NOT NULL AND text != ''
                ORDER BY material_id, chunk_index
            """),
            {f"mid_{i}": mid for i, mid in enumerate(material_ids)},
        ).fetchall()
        return [{
            "id": str(row[0]),
            "chunk_index": row[1],
            "raw_text": row[2] or "",
            "text": row[3] or row[2] or "",
            "display_text": row[3] or row[2] or "",
            "material_id": row[4] or "",
            "document_id": str(row[5]) if row[5] else "",
            "page_start": row[6],
            "page_end": row[7],
            "smiles_dsl": row[8] or "",
            "variables": _json_value(row[9], None),
            "ancestors": _json_value(row[10], []),
            "graph_elements": [],
        } for row in rows]
    finally:
        session.close()


def _chunks_for_document(document_id: str) -> list[dict]:
    session = _pg_session()
    try:
        rows = session.execute(
            sa_text("""
                SELECT id, chunk_index, text, display_text, material_id, document_id,
                       page_start, page_end, smiles_dsl, variables, ancestors
                FROM chunks
                WHERE document_id::text = :document_id OR material_id = :document_id
                ORDER BY chunk_index ASC
            """),
            {"document_id": document_id},
        ).fetchall()
        return [{
            "id": str(row[0]),
            "chunk_index": row[1],
            "raw_text": row[2] or "",
            "text": row[3] or row[2] or "",
            "display_text": row[3] or row[2] or "",
            "material_id": row[4] or "",
            "document_id": str(row[5]) if row[5] else "",
            "page_start": row[6],
            "page_end": row[7],
            "smiles_dsl": row[8] or "",
            "variables": _json_value(row[9], None),
            "ancestors": _json_value(row[10], []),
            "graph_elements": [],
        } for row in rows]
    finally:
        session.close()


def _ensure_document_viewable(document_id: str, current_user: dict) -> list[dict]:
    chunks = _chunks_for_document(document_id)
    if not chunks:
        raise HTTPException(status_code=404, detail="Document not found")
    if current_user.get("role") == ROLE_SYSTEM_ADMIN:
        return chunks
    for chunk in chunks:
        course_id = _find_viewable_course_for_chunk(chunk, current_user)
        if course_id:
            return chunks
    raise HTTPException(status_code=404, detail="Document not found")


def _ensure_document_editable(document_id: str, current_user: dict) -> list[dict]:
    chunks = _chunks_for_document(document_id)
    if not chunks:
        raise HTTPException(status_code=404, detail="Document not found")
    if current_user.get("role") == ROLE_SYSTEM_ADMIN:
        return chunks
    for chunk in chunks:
        course_id = _find_course_for_chunk(chunk, current_user)
        if course_id:
            _ensure_editable(course_id, current_user)
            return chunks
    raise HTTPException(status_code=404, detail="Document not found")


def _source_ref_for_chunk(chunk: dict) -> dict:
    quote = (chunk.get("raw_text") or chunk.get("text") or "").strip()
    return {
        "chunk_id": chunk["id"],
        "page_start": chunk.get("page_start"),
        "page_end": chunk.get("page_end"),
        "quote": quote[:240],
    }


def _section_id_for_chunk(chunk: dict) -> str:
    document_id = chunk.get("document_id") or chunk.get("material_id") or "document"
    page = chunk.get("page_start")
    if page is not None:
        return f"{document_id}:page_{page}"
    index = int(chunk.get("chunk_index") or 0)
    return f"{document_id}:section_{max(1, (index // 4) + 1)}"


def _source_scope_for_chunk(chunk: dict, level: str = "chunk") -> dict:
    chunk_id = str(chunk.get("id") or chunk.get("chunk_id") or "")
    pages = []
    for key in ("page_start", "page_end"):
        if chunk.get(key) is not None and chunk[key] not in pages:
            pages.append(chunk[key])
    return {
        "level": level,
        "document_id": str(chunk.get("document_id") or chunk.get("material_id") or ""),
        "section_id": _section_id_for_chunk(chunk),
        "chunk_id": chunk_id if level == "chunk" else "",
        "chunks": [chunk_id] if chunk_id else [],
        "pages": pages,
        "equations": [],
        "claims": [],
    }


def _row_to_claim(row: Any) -> ClaimOut:
    return ClaimOut(**{
        "claim_id": str(row[0]),
        "document_id": row[1] or "",
        "source_scope": _json_value(row[3], {}),
        "claim_type": row[4] or "diagnostic_claim",
        "text": row[5] or "",
        "normalized_text": row[6] or "",
        "concepts": _json_value(row[7], []),
        "support_status": row[8] or "source_backed",
        "evidence_text": row[9] or "",
        "review_status": row[10] or "teacher_review_required",
        "created_by": str(row[11]) if row[11] else None,
        "created_at": row[12].isoformat() if row[12] else "",
        "updated_at": row[13].isoformat() if row[13] else "",
    })


def _claim_rows_for_chunk(chunk_id: str) -> list[ClaimOut]:
    session = _pg_session()
    try:
        rows = session.execute(
            sa_text("""
                SELECT id, document_id, chunk_id, source_scope, claim_type, text,
                       normalized_text, concepts, support_status, evidence_text,
                       review_status, created_by, created_at, updated_at
                FROM theory_claims
                WHERE chunk_id = CAST(:chunk_id AS uuid)
                ORDER BY created_at ASC
            """),
            {"chunk_id": chunk_id},
        ).fetchall()
        return [_row_to_claim(row) for row in rows]
    finally:
        session.close()


def _claim_rows_for_section(document_id: str, section_id: str) -> list[ClaimOut]:
    session = _pg_session()
    try:
        rows = session.execute(
            sa_text("""
                SELECT id, document_id, chunk_id, source_scope, claim_type, text,
                       normalized_text, concepts, support_status, evidence_text,
                       review_status, created_by, created_at, updated_at
                FROM theory_claims
                WHERE document_id = :document_id
                  AND source_scope->>'section_id' = :section_id
                ORDER BY created_at ASC
            """),
            {"document_id": document_id, "section_id": section_id},
        ).fetchall()
        return [_row_to_claim(row) for row in rows]
    finally:
        session.close()


def _extract_claim_candidates(chunk: dict) -> list[dict]:
    text = (chunk.get("raw_text") or chunk.get("text") or "").strip()
    if not text:
        return []
    pieces = [p.strip() for p in re.split(r"(?<=[。.!?])\s+|\n+", text) if p.strip()]
    candidates: list[dict] = []
    for idx, sentence in enumerate(pieces[:8]):
        lower = sentence.lower()
        claim_type = "diagnostic_claim"
        if any(token in lower for token in ("define", "definition", "denote", "is called")):
            claim_type = "definition"
        elif any(token in lower for token in ("assume", "assuming", "仮定")):
            claim_type = "assumption"
        elif any(token in lower for token in ("approx", "limit", "近似", "極限")):
            claim_type = "approximation"
        elif any(token in sentence for token in ("=", "∝", "\\", "$")):
            claim_type = "equation"
        elif any(token in lower for token in ("uncertainty", "error", "不確か")):
            claim_type = "uncertainty"
        elif any(token in lower for token in ("result", "therefore", "we find", "結果")):
            claim_type = "result"
        candidates.append({
            "claim_type": claim_type,
            "text": sentence[:1200],
            "normalized_text": sentence[:1200],
            "concepts": [],
            "support_status": "source_backed",
            "evidence_text": sentence[:360],
            "review_status": "teacher_review_required",
            "source_scope": _source_scope_for_chunk(chunk, "chunk"),
        })
    variables = _json_value(chunk.get("variables"), [])
    if isinstance(variables, dict):
        variables = list(variables.values())
    for item in variables[:6] if isinstance(variables, list) else []:
        label = item.get("name") or item.get("label") if isinstance(item, dict) else str(item)
        if label:
            candidates.append({
                "claim_type": "definition",
                "text": f"{label} is defined or used in this chunk.",
                "normalized_text": f"{label} is defined or used in this chunk.",
                "concepts": [{"name": str(label), "concept_type": "Concept"}],
                "support_status": "source_backed",
                "evidence_text": text[:240],
                "review_status": "teacher_review_required",
                "source_scope": _source_scope_for_chunk(chunk, "chunk"),
            })
    return candidates[:12]


def _insert_claim(document_id: str, chunk_id: str, payload: dict, user_id: str) -> ClaimOut:
    if payload.get("claim_type") not in _CLAIM_TYPES:
        raise HTTPException(status_code=422, detail="Invalid claim_type")
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("""
                INSERT INTO theory_claims (
                    document_id, chunk_id, source_scope, claim_type, text, normalized_text,
                    concepts, support_status, evidence_text, review_status, created_by
                )
                VALUES (
                    :document_id, CAST(:chunk_id AS uuid), CAST(:source_scope AS jsonb),
                    :claim_type, :text, :normalized_text, CAST(:concepts AS jsonb),
                    :support_status, :evidence_text, :review_status, CAST(:created_by AS uuid)
                )
                RETURNING id, document_id, chunk_id, source_scope, claim_type, text,
                          normalized_text, concepts, support_status, evidence_text,
                          review_status, created_by, created_at, updated_at
            """),
            {
                "document_id": document_id,
                "chunk_id": chunk_id,
                "source_scope": json.dumps(payload.get("source_scope") or {}, ensure_ascii=False),
                "claim_type": payload.get("claim_type") or "diagnostic_claim",
                "text": payload.get("text") or "",
                "normalized_text": payload.get("normalized_text") or payload.get("text") or "",
                "concepts": json.dumps(payload.get("concepts") or [], ensure_ascii=False),
                "support_status": payload.get("support_status") or "source_backed",
                "evidence_text": payload.get("evidence_text") or "",
                "review_status": payload.get("review_status") or "teacher_review_required",
                "created_by": user_id,
            },
        ).fetchone()
        session.commit()
        return _row_to_claim(row)
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        logger.exception("Failed to insert claim")
        raise HTTPException(status_code=500, detail="Failed to create claim")
    finally:
        session.close()


def _refs_present(item: dict) -> bool:
    refs = item.get("source_refs")
    return isinstance(refs, list) and any(isinstance(ref, dict) and ref.get("chunk_id") for ref in refs)


def _validation_warnings(payload: dict) -> list[dict]:
    warnings: list[dict] = []
    if not payload.get("source_chunks"):
        warnings.append({"field": "source_chunks", "message": "コンポーネント全体の出典チャンクがありません。"})
    if not payload.get("inputs"):
        warnings.append({"field": "inputs", "message": "入力が未設定です。"})
    if not payload.get("outputs"):
        warnings.append({"field": "outputs", "message": "出力が未設定です。"})
    for field in ("inputs", "outputs", "preconditions", "constraints", "invalid_conditions"):
        for idx, item in enumerate(payload.get(field) or []):
            if not isinstance(item, dict):
                continue
            if item.get("needs_source") or not _refs_present(item):
                warnings.append({
                    "field": f"{field}.{idx}",
                    "message": f"{item.get('label') or field} には出典がありません。",
                })
    return warnings


def _validate_for_review(payload: dict) -> None:
    warnings = _validation_warnings(payload)
    blocking = [w for w in warnings if w["field"] in ("source_chunks", "inputs", "outputs") or "." in w["field"]]
    if not str(payload.get("name") or "").strip():
        blocking.append({"field": "name", "message": "名前が空です。"})
    if blocking:
        raise HTTPException(status_code=422, detail=blocking)


def _normalize_payload(body: TheoryComponentUpsertRequest, chunk: dict | None = None) -> dict:
    payload = _dump_model(body)
    if payload.get("component_type") not in {"theory", "concept", "law", "mechanism", "operator", "observation"}:
        raise HTTPException(status_code=422, detail="Invalid component_type")
    if chunk and not payload.get("source_chunks"):
        payload["source_chunks"] = [_source_ref_for_chunk(chunk)]
    if chunk and not payload.get("source_scope"):
        payload["source_scope"] = _source_scope_for_chunk(chunk, "chunk")
    for field in ("source_chunks", "inputs", "outputs", "preconditions", "constraints", "invalid_conditions", "dependencies"):
        payload[field] = payload.get(field) or []
    payload["cautions"] = payload.get("cautions") or []
    payload["evidence_claims"] = payload.get("evidence_claims") or []
    payload["connectors"] = payload.get("connectors") or {
        "requires_before_use": [],
        "can_accept": [],
        "can_output_to": [],
        "may_conflict_with": [],
    }
    payload["maturity_level"] = payload.get("maturity_level") or "paper_claim"
    payload["maturity_source"] = payload.get("maturity_source") or "llm_proposed"
    if payload.get("status") == "teacher_reviewed":
        payload["review_status"] = "teacher_approved"
    else:
        payload["review_status"] = payload.get("review_status") or "teacher_review_required"
    payload["blackbox_policy"] = payload.get("blackbox_policy") or {
        "default_level": "summary",
        "expand_if_unlearned": True,
        "requires_source_display": True,
    }
    payload["validation_warnings"] = _validation_warnings(payload)
    if payload.get("status") == "teacher_reviewed":
        _validate_for_review(payload)
        payload["validation_warnings"] = []
    elif payload.get("status") not in ("candidate", "draft", "rejected"):
        raise HTTPException(status_code=422, detail="Invalid status")
    return payload


def _raw_items_with_chunk_refs(items: Any, chunk: dict) -> list[dict]:
    if not isinstance(items, list):
        return []
    normalized: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        copied = dict(item)
        refs = copied.get("source_refs") if isinstance(copied.get("source_refs"), list) else []
        fixed_refs = []
        for ref in refs:
            if not isinstance(ref, dict):
                continue
            fixed = dict(ref)
            fixed.setdefault("chunk_id", chunk["id"])
            fixed.setdefault("page_start", chunk.get("page_start"))
            fixed.setdefault("page_end", chunk.get("page_end"))
            fixed_refs.append(fixed)
        copied["source_refs"] = fixed_refs
        normalized.append(copied)
    return normalized


def _raw_component_to_request(raw: dict, chunk: dict) -> TheoryComponentUpsertRequest:
    allowed_types = {"theory", "concept", "law", "mechanism", "operator", "observation"}
    component_type = str(raw.get("component_type") or "theory").strip()
    if component_type not in allowed_types:
        component_type = "theory"
    source_chunks = raw.get("source_chunks") if isinstance(raw.get("source_chunks"), list) else []
    fixed_source_chunks = []
    for ref in source_chunks:
        if not isinstance(ref, dict):
            continue
        fixed = dict(ref)
        fixed.setdefault("chunk_id", chunk["id"])
        fixed.setdefault("page_start", chunk.get("page_start"))
        fixed.setdefault("page_end", chunk.get("page_end"))
        fixed_source_chunks.append(fixed)
    return TheoryComponentUpsertRequest(**{
        "name": str(raw.get("name") or "").strip(),
        "component_type": component_type,
        "summary": raw.get("summary") or "",
        "status": "candidate",
        "source_scope": _source_scope_for_chunk(chunk, "chunk"),
        "evidence_claims": raw.get("evidence_claims") if isinstance(raw.get("evidence_claims"), list) else [],
        "maturity_level": raw.get("maturity_level") or "paper_claim",
        "maturity_source": raw.get("maturity_source") or "llm_proposed",
        "review_status": raw.get("review_status") or "teacher_review_required",
        "source_chunks": fixed_source_chunks or [_source_ref_for_chunk(chunk)],
        "inputs": _raw_items_with_chunk_refs(raw.get("inputs"), chunk),
        "outputs": _raw_items_with_chunk_refs(raw.get("outputs"), chunk),
        "preconditions": _raw_items_with_chunk_refs(raw.get("preconditions"), chunk),
        "cautions": _raw_items_with_chunk_refs(raw.get("cautions"), chunk),
        "constraints": _raw_items_with_chunk_refs(raw.get("constraints"), chunk),
        "invalid_conditions": _raw_items_with_chunk_refs(raw.get("invalid_conditions"), chunk),
        "dependencies": _raw_items_with_chunk_refs(raw.get("dependencies"), chunk),
        "connectors": raw.get("connectors") if isinstance(raw.get("connectors"), dict) else {},
        "blackbox_policy": raw.get("blackbox_policy") or {"default_level": "summary", "expand_if_unlearned": True},
    })


def _preserve_structural_io(
    components: list[dict],
    structural_components: list[dict],
) -> list[dict]:
    """Force LLM-enriched candidates to keep DSL-derived inputs and outputs."""
    structural_by_name = {
        str(component.get("name") or ""): component
        for component in structural_components
        if isinstance(component, dict)
    }
    preserved = []
    for component in components:
        if not isinstance(component, dict):
            continue
        structural = structural_by_name.get(str(component.get("name") or ""))
        if structural:
            component = dict(component)
            component["inputs"] = structural.get("inputs") or []
            component["outputs"] = structural.get("outputs") or []
        preserved.append(component)
    return preserved


def _insert_component(course_id: str, primary_chunk_id: str | None, payload: dict, user_id: str) -> TheoryComponentOut:
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("""
                INSERT INTO theory_components (
                    course_id, primary_chunk_id, name, component_type, summary, status,
                    source_chunks, inputs, outputs, preconditions, constraints,
                    invalid_conditions, dependencies, blackbox_policy, validation_warnings,
                    teacher_notes, source_scope, evidence_claims, maturity_level, maturity_source,
                    review_status, cautions, connectors, created_by
                )
                VALUES (
                    :course_id, CAST(:primary_chunk_id AS uuid), :name, :component_type, :summary, :status,
                    CAST(:source_chunks AS jsonb), CAST(:inputs AS jsonb), CAST(:outputs AS jsonb),
                    CAST(:preconditions AS jsonb), CAST(:constraints AS jsonb),
                    CAST(:invalid_conditions AS jsonb), CAST(:dependencies AS jsonb),
                    CAST(:blackbox_policy AS jsonb), CAST(:validation_warnings AS jsonb),
                    :teacher_notes, CAST(:source_scope AS jsonb), CAST(:evidence_claims AS jsonb),
                    :maturity_level, :maturity_source, :review_status, CAST(:cautions AS jsonb),
                    CAST(:connectors AS jsonb), CAST(:created_by AS uuid)
                )
                RETURNING id
            """),
            {
                "course_id": course_id,
                "primary_chunk_id": primary_chunk_id,
                "name": payload["name"],
                "component_type": payload.get("component_type") or "theory",
                "summary": payload.get("summary") or "",
                "status": payload.get("status") or "candidate",
                "source_chunks": json.dumps(payload.get("source_chunks") or [], ensure_ascii=False),
                "inputs": json.dumps(payload.get("inputs") or [], ensure_ascii=False),
                "outputs": json.dumps(payload.get("outputs") or [], ensure_ascii=False),
                "preconditions": json.dumps(payload.get("preconditions") or [], ensure_ascii=False),
                "constraints": json.dumps(payload.get("constraints") or [], ensure_ascii=False),
                "invalid_conditions": json.dumps(payload.get("invalid_conditions") or [], ensure_ascii=False),
                "dependencies": json.dumps(payload.get("dependencies") or [], ensure_ascii=False),
                "blackbox_policy": json.dumps(payload.get("blackbox_policy") or {}, ensure_ascii=False),
                "validation_warnings": json.dumps(payload.get("validation_warnings") or [], ensure_ascii=False),
                "teacher_notes": payload.get("teacher_notes") or "",
                "source_scope": json.dumps(payload.get("source_scope") or {}, ensure_ascii=False),
                "evidence_claims": json.dumps(payload.get("evidence_claims") or [], ensure_ascii=False),
                "maturity_level": payload.get("maturity_level") or "paper_claim",
                "maturity_source": payload.get("maturity_source") or "llm_proposed",
                "review_status": payload.get("review_status") or "teacher_review_required",
                "cautions": json.dumps(payload.get("cautions") or [], ensure_ascii=False),
                "connectors": json.dumps(payload.get("connectors") or {}, ensure_ascii=False),
                "created_by": user_id,
            },
        ).fetchone()
        session.commit()
        return _get_component(str(row[0]))
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        logger.exception("Failed to insert theory component")
        raise HTTPException(status_code=500, detail="Failed to create theory component")
    finally:
        session.close()


def _update_component(component_id: str, payload: dict) -> TheoryComponentOut:
    session = _pg_session()
    try:
        session.execute(
            sa_text("""
                UPDATE theory_components
                SET name = :name,
                    component_type = :component_type,
                    summary = :summary,
                    status = :status,
                    source_chunks = CAST(:source_chunks AS jsonb),
                    inputs = CAST(:inputs AS jsonb),
                    outputs = CAST(:outputs AS jsonb),
                    preconditions = CAST(:preconditions AS jsonb),
                    constraints = CAST(:constraints AS jsonb),
                    invalid_conditions = CAST(:invalid_conditions AS jsonb),
                    dependencies = CAST(:dependencies AS jsonb),
                    blackbox_policy = CAST(:blackbox_policy AS jsonb),
                    validation_warnings = CAST(:validation_warnings AS jsonb),
                    teacher_notes = :teacher_notes,
                    source_scope = CAST(:source_scope AS jsonb),
                    evidence_claims = CAST(:evidence_claims AS jsonb),
                    maturity_level = :maturity_level,
                    maturity_source = :maturity_source,
                    review_status = :review_status,
                    cautions = CAST(:cautions AS jsonb),
                    connectors = CAST(:connectors AS jsonb),
                    updated_at = now()
                WHERE id = CAST(:id AS uuid)
            """),
            {
                "id": component_id,
                "name": payload["name"],
                "component_type": payload.get("component_type") or "theory",
                "summary": payload.get("summary") or "",
                "status": payload.get("status") or "candidate",
                "source_chunks": json.dumps(payload.get("source_chunks") or [], ensure_ascii=False),
                "inputs": json.dumps(payload.get("inputs") or [], ensure_ascii=False),
                "outputs": json.dumps(payload.get("outputs") or [], ensure_ascii=False),
                "preconditions": json.dumps(payload.get("preconditions") or [], ensure_ascii=False),
                "constraints": json.dumps(payload.get("constraints") or [], ensure_ascii=False),
                "invalid_conditions": json.dumps(payload.get("invalid_conditions") or [], ensure_ascii=False),
                "dependencies": json.dumps(payload.get("dependencies") or [], ensure_ascii=False),
                "blackbox_policy": json.dumps(payload.get("blackbox_policy") or {}, ensure_ascii=False),
                "validation_warnings": json.dumps(payload.get("validation_warnings") or [], ensure_ascii=False),
                "teacher_notes": payload.get("teacher_notes") or "",
                "source_scope": json.dumps(payload.get("source_scope") or {}, ensure_ascii=False),
                "evidence_claims": json.dumps(payload.get("evidence_claims") or [], ensure_ascii=False),
                "maturity_level": payload.get("maturity_level") or "paper_claim",
                "maturity_source": payload.get("maturity_source") or "llm_proposed",
                "review_status": payload.get("review_status") or "teacher_review_required",
                "cautions": json.dumps(payload.get("cautions") or [], ensure_ascii=False),
                "connectors": json.dumps(payload.get("connectors") or {}, ensure_ascii=False),
            },
        )
        session.commit()
        return _get_component(component_id)
    except Exception:
        session.rollback()
        logger.exception("Failed to update theory component %s", component_id)
        raise HTTPException(status_code=500, detail="Failed to update theory component")
    finally:
        session.close()


def _components_for_document(document_id: str) -> list[TheoryComponentOut]:
    session = _pg_session()
    try:
        rows = session.execute(
            sa_text(_select_components_sql("source_scope->>'document_id' = :document_id")),
            {"document_id": document_id},
        ).fetchall()
        return [_row_to_out(row) for row in rows]
    finally:
        session.close()


def _build_component_graph_payload(document_id: str, components: list[TheoryComponentOut]) -> dict:
    nodes = [
        {"component_id": component.id, "label": component.name, "review_status": component.review_status}
        for component in components
    ]
    edges = []
    for idx in range(len(components) - 1):
        edges.append({
            "source_component_id": components[idx].id,
            "target_component_id": components[idx + 1].id,
            "relation": "SUPPORTS",
            "support_status": "design_inferred",
            "review_status": "teacher_review_required",
        })
    validation_results = []
    for component in components:
        if component.review_status != "teacher_approved":
            validation_results.append({
                "severity": "warning",
                "message": "未レビューのコンポーネントがあります。",
                "component_id": component.id,
            })
        if not component.evidence_claims:
            validation_results.append({
                "severity": "warning",
                "message": "根拠Claimが未設定です。",
                "component_id": component.id,
            })
    return {
        "graph_id": f"graph_{document_id}",
        "document_id": document_id,
        "scope": {"level": "paper"},
        "nodes": nodes,
        "edges": edges,
        "validation_results": validation_results,
    }


def _save_component_graph(course_id: str, document_id: str, graph: dict, user_id: str | None) -> None:
    session = _pg_session()
    try:
        session.execute(
            sa_text("""
                INSERT INTO theory_component_graphs (
                    course_id, document_id, scope, graph_json, validation_results, updated_by
                )
                VALUES (
                    :course_id, :document_id, CAST(:scope AS jsonb), CAST(:graph_json AS jsonb),
                    CAST(:validation_results AS jsonb), CAST(:updated_by AS uuid)
                )
                ON CONFLICT (course_id, document_id)
                DO UPDATE SET
                    scope = EXCLUDED.scope,
                    graph_json = EXCLUDED.graph_json,
                    validation_results = EXCLUDED.validation_results,
                    updated_by = EXCLUDED.updated_by,
                    updated_at = now()
            """),
            {
                "course_id": course_id,
                "document_id": document_id,
                "scope": json.dumps(graph.get("scope") or {"level": "paper"}, ensure_ascii=False),
                "graph_json": json.dumps(graph, ensure_ascii=False),
                "validation_results": json.dumps(graph.get("validation_results") or [], ensure_ascii=False),
                "updated_by": user_id,
            },
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _group_sections(chunks: list[dict]) -> dict[tuple[str, str], list[dict]]:
    grouped: dict[tuple[str, str], list[dict]] = {}
    for chunk in chunks:
        document_id = chunk.get("document_id") or chunk.get("material_id") or ""
        if not document_id:
            continue
        key = (document_id, _section_id_for_chunk(chunk))
        grouped.setdefault(key, []).append(chunk)
    return grouped


def _run_claim_extraction(course_id: str, course_data: dict, user_id: str | None, task_id: str | None = None) -> dict:
    chunks = _course_chunks(course_data)
    total = len(chunks)
    created = 0
    skipped = 0
    if task_id:
        update_background_task(task_id, "processing", result_data={
            "course_id": course_id, "total_chunks": total, "generated": 0,
            "skipped": 0, "progress": 0, "stage": "claims",
        })
    for idx, chunk in enumerate(chunks):
        document_id = chunk.get("document_id") or chunk.get("material_id") or ""
        if not document_id:
            skipped += 1
        elif _claim_rows_for_chunk(chunk["id"]):
            skipped += 1
        else:
            for payload in _extract_claim_candidates(chunk):
                _insert_claim(document_id, chunk["id"], payload, user_id or "")
                created += 1
        if task_id:
            update_background_task(task_id, "processing", result_data={
                "course_id": course_id, "total_chunks": total, "generated": created,
                "skipped": skipped, "progress": int((idx + 1) * 100 / total) if total else 100,
                "stage": "claims",
            })
    return {"total_chunks": total, "generated": created, "skipped": skipped, "progress": 100}


def _assemble_section(
    course_id: str,
    document_id: str,
    section_id: str,
    section_chunks: list[dict],
    user_id: str | None,
    force: bool = False,
) -> TheoryComponentOut | None:
    existing = list_section_components(document_id, section_id, {"role": ROLE_SYSTEM_ADMIN, "id": user_id or ""})
    if existing and not force:
        return existing[0]
    claims = _claim_rows_for_section(document_id, section_id)
    if not claims:
        for chunk in section_chunks:
            for payload in _extract_claim_candidates(chunk):
                claims.append(_insert_claim(document_id, chunk["id"], payload, user_id or ""))
    if not claims:
        return None
    evidence_claims = [claim.claim_id for claim in claims]
    source_scope = {
        "level": "section",
        "document_id": document_id,
        "section_id": section_id,
        "chunks": [c["id"] for c in section_chunks],
        "pages": sorted({p for c in section_chunks for p in (c.get("page_start"), c.get("page_end")) if p is not None}),
        "equations": [],
        "claims": evidence_claims,
    }
    def item_for_claim(claim: ClaimOut) -> dict:
        return {
            "label": claim.normalized_text or claim.text,
            "name": claim.normalized_text or claim.text,
            "type": "Concept",
            "concept_type": "Relation" if claim.claim_type in ("relation", "equation") else "Concept",
            "description": claim.text,
            "support_status": claim.support_status,
            "evidence_claims": [claim.claim_id],
            "source_refs": [{"chunk_id": claim.source_scope.chunk_id, "quote": claim.evidence_text}],
            "needs_source": False,
        }
    inputs = [item_for_claim(c) for c in claims if c.claim_type in ("definition", "assumption", "approximation", "observable_definition")][:6]
    outputs = [item_for_claim(c) for c in claims if c.claim_type in ("equation", "relation", "result", "derivation_step")][:6]
    cautions = [{
        "label": c.normalized_text or c.text,
        "condition": c.normalized_text or c.text,
        "description": c.text,
        "support_status": c.support_status,
        "evidence_claims": [c.claim_id],
        "source_refs": [{"chunk_id": c.source_scope.chunk_id, "quote": c.evidence_text}],
        "needs_source": False,
    } for c in claims if c.claim_type in ("uncertainty", "limitation", "correction")][:4]
    payload = _normalize_payload(TheoryComponentUpsertRequest(**{
        "name": f"Component for {section_id.rsplit(':', 1)[-1].replace('_', ' ')}",
        "component_type": "theory",
        "summary": "複数Claimを束ねた節単位の理論コンポーネント候補です。",
        "status": "candidate",
        "source_scope": source_scope,
        "evidence_claims": evidence_claims,
        "maturity_level": "paper_claim",
        "maturity_source": "llm_proposed",
        "review_status": "teacher_review_required",
        "source_chunks": [_source_ref_for_chunk(c) for c in section_chunks],
        "inputs": inputs or outputs[:1],
        "outputs": outputs or inputs[:1],
        "preconditions": [],
        "cautions": cautions,
        "invalid_conditions": cautions,
        "connectors": {"requires_before_use": [], "can_accept": [], "can_output_to": [], "may_conflict_with": []},
    }))
    return _insert_component(course_id, section_chunks[0]["id"] if section_chunks else None, payload, user_id or "")


def _run_component_assembly(course_id: str, course_data: dict, user_id: str | None, task_id: str | None = None, force: bool = False) -> dict:
    grouped = _group_sections(_course_chunks(course_data))
    total = len(grouped)
    generated = 0
    skipped = 0
    if task_id:
        update_background_task(task_id, "processing", result_data={
            "course_id": course_id, "total_sections": total, "generated": 0,
            "skipped": 0, "progress": 0, "stage": "components",
        })
    for idx, ((document_id, section_id), chunks) in enumerate(grouped.items()):
        component = _assemble_section(course_id, document_id, section_id, chunks, user_id, force=force)
        if component:
            generated += 1
        else:
            skipped += 1
        if task_id:
            update_background_task(task_id, "processing", result_data={
                "course_id": course_id, "total_sections": total, "generated": generated,
                "skipped": skipped, "progress": int((idx + 1) * 100 / total) if total else 100,
                "stage": "components",
            })
    return {"total_sections": total, "generated": generated, "skipped": skipped, "progress": 100}


def _run_graph_update(course_id: str, course_data: dict, user_id: str | None, task_id: str | None = None) -> dict:
    document_ids = sorted({c.get("document_id") or c.get("material_id") for c in _course_chunks(course_data) if c.get("document_id") or c.get("material_id")})
    total = len(document_ids)
    generated = 0
    if task_id:
        update_background_task(task_id, "processing", result_data={
            "course_id": course_id, "total_documents": total, "generated": 0,
            "progress": 0, "stage": "graph",
        })
    for idx, document_id in enumerate(document_ids):
        graph = _build_component_graph_payload(document_id, _components_for_document(document_id))
        _save_component_graph(course_id, document_id, graph, user_id)
        generated += 1
        if task_id:
            update_background_task(task_id, "processing", result_data={
                "course_id": course_id, "total_documents": total, "generated": generated,
                "progress": int((idx + 1) * 100 / total) if total else 100,
                "stage": "graph",
            })
    return {"total_documents": total, "generated": generated, "progress": 100}


def _worker_wrapper(task_id: str, fn, *args) -> None:
    try:
        result = fn(*args, task_id)
        update_background_task(task_id, "completed", result_data={**result, "course_id": args[0], "progress": 100})
    except Exception as exc:
        logger.exception("Background theory task failed: %s", task_id)
        update_background_task(task_id, "failed", error_message=str(exc))


def _component_assembly_worker(task_id: str, course_id: str, course_data: dict, user_id: str, force: bool) -> None:
    try:
        result = _run_component_assembly(course_id, course_data, user_id, task_id, force=force)
        update_background_task(task_id, "completed", result_data={**result, "course_id": course_id, "progress": 100})
    except Exception as exc:
        logger.exception("Component assembly task failed: %s", task_id)
        update_background_task(task_id, "failed", error_message=str(exc))


@router.get("/documents/{document_id}/chunks/{chunk_id}/claims", response_model=list[ClaimOut])
def list_chunk_claims(
    document_id: str,
    chunk_id: str,
    current_user: dict = Depends(_require_teacher),
) -> list[ClaimOut]:
    _ensure_document_viewable(document_id, current_user)
    return _claim_rows_for_chunk(chunk_id)


@router.post("/documents/{document_id}/chunks/{chunk_id}/claims/extract", response_model=ClaimExtractResponse)
def extract_chunk_claims(
    document_id: str,
    chunk_id: str,
    current_user: dict = Depends(_require_teacher),
) -> ClaimExtractResponse:
    chunk = _chunk_row(chunk_id)
    if not chunk:
        raise HTTPException(status_code=404, detail="Chunk not found")
    chunks = _ensure_document_editable(document_id, current_user)
    if not any(c["id"] == chunk_id for c in chunks):
        raise HTTPException(status_code=404, detail="Chunk not found")
    existing = _claim_rows_for_chunk(chunk_id)
    if existing:
        return ClaimExtractResponse(chunk_id=chunk_id, claims=existing)
    saved = [
        _insert_claim(document_id, chunk_id, payload, current_user["id"])
        for payload in _extract_claim_candidates(chunk)
    ]
    return ClaimExtractResponse(chunk_id=chunk_id, claims=saved)


@router.patch("/claims/{claim_id}", response_model=ClaimOut)
def update_claim(
    claim_id: str,
    body: ClaimUpsertRequest,
    current_user: dict = Depends(_require_teacher),
) -> ClaimOut:
    payload = _dump_model(body)
    if payload.get("claim_type") not in _CLAIM_TYPES:
        raise HTTPException(status_code=422, detail="Invalid claim_type")
    session = _pg_session()
    try:
        existing = session.execute(
            sa_text("SELECT document_id FROM theory_claims WHERE id = CAST(:claim_id AS uuid)"),
            {"claim_id": claim_id},
        ).fetchone()
    finally:
        session.close()
    if not existing:
        raise HTTPException(status_code=404, detail="Claim not found")
    _ensure_document_editable(existing[0] or "", current_user)
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("""
                UPDATE theory_claims
                SET source_scope = CAST(:source_scope AS jsonb),
                    claim_type = :claim_type,
                    text = :text,
                    normalized_text = :normalized_text,
                    concepts = CAST(:concepts AS jsonb),
                    support_status = :support_status,
                    evidence_text = :evidence_text,
                    review_status = :review_status,
                    updated_at = now()
                WHERE id = CAST(:claim_id AS uuid)
                RETURNING id, document_id, chunk_id, source_scope, claim_type, text,
                          normalized_text, concepts, support_status, evidence_text,
                          review_status, created_by, created_at, updated_at
            """),
            {
                "claim_id": claim_id,
                "source_scope": json.dumps(payload.get("source_scope") or {}, ensure_ascii=False),
                "claim_type": payload.get("claim_type") or "diagnostic_claim",
                "text": payload.get("text") or "",
                "normalized_text": payload.get("normalized_text") or payload.get("text") or "",
                "concepts": json.dumps(payload.get("concepts") or [], ensure_ascii=False),
                "support_status": payload.get("support_status") or "source_backed",
                "evidence_text": payload.get("evidence_text") or "",
                "review_status": payload.get("review_status") or "teacher_review_required",
            },
        ).fetchone()
        session.commit()
        return _row_to_claim(row)
    except Exception:
        session.rollback()
        logger.exception("Failed to update claim %s", claim_id)
        raise HTTPException(status_code=500, detail="Failed to update claim")
    finally:
        session.close()


@router.get("/documents/{document_id}/sections/{section_id}/components", response_model=list[TheoryComponentOut])
def list_section_components(
    document_id: str,
    section_id: str,
    current_user: dict = Depends(_require_teacher),
) -> list[TheoryComponentOut]:
    _ensure_document_viewable(document_id, current_user)
    session = _pg_session()
    try:
        rows = session.execute(
            sa_text(_select_components_sql(
                "source_scope->>'document_id' = :document_id AND source_scope->>'section_id' = :section_id"
            )),
            {"document_id": document_id, "section_id": section_id},
        ).fetchall()
        return [_row_to_out(row) for row in rows]
    finally:
        session.close()


@router.post("/documents/{document_id}/sections/{section_id}/components/assemble", response_model=ComponentAssembleResponse)
def assemble_section_components(
    document_id: str,
    section_id: str,
    body: ComponentAssembleRequest,
    current_user: dict = Depends(_require_teacher),
) -> ComponentAssembleResponse:
    chunks = _ensure_document_editable(document_id, current_user)
    course_id = None
    for chunk in chunks:
        course_id = _find_course_for_chunk(chunk, current_user)
        if course_id:
            break
    if not course_id:
        raise HTTPException(status_code=404, detail="Course not found")
    claims = _claim_rows_for_section(document_id, section_id)
    section_chunks = [c for c in chunks if _section_id_for_chunk(c) == section_id]
    if not claims:
        for chunk in section_chunks:
            for payload in _extract_claim_candidates(chunk):
                claims.append(_insert_claim(document_id, chunk["id"], payload, current_user["id"]))
    existing = list_section_components(document_id, section_id, current_user)
    if existing and not body.force:
        return ComponentAssembleResponse(section_id=section_id, components=existing)
    evidence_claims = [claim.claim_id for claim in claims]
    component_name = "Section Component"
    if section_id:
        component_name = f"Component for {section_id.rsplit(':', 1)[-1].replace('_', ' ')}"
    source_scope = {
        "level": "section",
        "document_id": document_id,
        "section_id": section_id,
        "chunks": [c["id"] for c in section_chunks],
        "pages": sorted({p for c in section_chunks for p in (c.get("page_start"), c.get("page_end")) if p is not None}),
        "equations": [],
        "claims": evidence_claims,
    }
    inputs = [{
        "label": claim.normalized_text or claim.text,
        "name": claim.normalized_text or claim.text,
        "type": "Concept",
        "concept_type": "Concept",
        "description": claim.text,
        "support_status": claim.support_status,
        "evidence_claims": [claim.claim_id],
        "source_refs": [{"chunk_id": claim.source_scope.chunk_id, "quote": claim.evidence_text}],
        "needs_source": False,
    } for claim in claims if claim.claim_type in ("definition", "assumption", "approximation", "observable_definition")][:6]
    outputs = [{
        "label": claim.normalized_text or claim.text,
        "name": claim.normalized_text or claim.text,
        "type": "Concept",
        "concept_type": "Relation" if claim.claim_type in ("relation", "equation") else "Concept",
        "description": claim.text,
        "support_status": claim.support_status,
        "evidence_claims": [claim.claim_id],
        "source_refs": [{"chunk_id": claim.source_scope.chunk_id, "quote": claim.evidence_text}],
        "needs_source": False,
    } for claim in claims if claim.claim_type in ("equation", "relation", "result", "derivation_step")][:6]
    preconditions = [{
        "label": claim.normalized_text or claim.text,
        "condition": claim.normalized_text or claim.text,
        "description": claim.text,
        "support_status": claim.support_status,
        "evidence_claims": [claim.claim_id],
        "source_refs": [{"chunk_id": claim.source_scope.chunk_id, "quote": claim.evidence_text}],
        "needs_source": False,
    } for claim in claims if claim.claim_type in ("assumption", "approximation")][:4]
    cautions = [{
        "label": claim.normalized_text or claim.text,
        "condition": claim.normalized_text or claim.text,
        "description": claim.text,
        "support_status": claim.support_status,
        "evidence_claims": [claim.claim_id],
        "source_refs": [{"chunk_id": claim.source_scope.chunk_id, "quote": claim.evidence_text}],
        "needs_source": False,
    } for claim in claims if claim.claim_type in ("uncertainty", "limitation", "correction")][:4]
    payload = _normalize_payload(TheoryComponentUpsertRequest(**{
        "name": component_name,
        "component_type": "theory",
        "summary": "複数Claimを束ねた節単位の理論コンポーネント候補です。",
        "status": "candidate",
        "source_scope": source_scope,
        "evidence_claims": evidence_claims,
        "maturity_level": "paper_claim",
        "maturity_source": "llm_proposed",
        "review_status": "teacher_review_required",
        "source_chunks": [_source_ref_for_chunk(c) for c in section_chunks],
        "inputs": inputs or outputs[:1],
        "outputs": outputs or inputs[:1],
        "preconditions": preconditions,
        "cautions": cautions,
        "constraints": [],
        "invalid_conditions": cautions,
        "dependencies": [],
        "connectors": {
            "requires_before_use": [],
            "can_accept": [],
            "can_output_to": [],
            "may_conflict_with": [],
        },
        "blackbox_policy": {
            "default_level": "summary",
            "expand_if_unlearned": True,
            "requires_source_display": True,
            "io_summary": "section claims -> component candidate",
        },
    }))
    saved = _insert_component(course_id, section_chunks[0]["id"] if section_chunks else None, payload, current_user["id"])
    return ComponentAssembleResponse(section_id=section_id, components=[saved])


@router.get("/documents/{document_id}/component-graph", response_model=ComponentGraphResponse)
def get_component_graph(
    document_id: str,
    current_user: dict = Depends(_require_teacher),
) -> ComponentGraphResponse:
    _ensure_document_viewable(document_id, current_user)
    return ComponentGraphResponse(**_build_component_graph_payload(document_id, _components_for_document(document_id)))


@router.post("/courses/{course_id}/claims/extract-all", status_code=202)
def extract_all_claims(
    course_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    course_data = _editable_course_data(course_id, current_user)
    task_id = str(uuid.uuid4())[:12]
    create_background_task(task_id, "claim_extraction", current_user["id"])
    threading.Thread(
        target=_worker_wrapper,
        args=(task_id, _run_claim_extraction, course_id, course_data, current_user["id"]),
        daemon=True,
    ).start()
    return {"task_id": task_id, "course_id": course_id, "status": "pending"}


@router.post("/courses/{course_id}/components/assemble-all", status_code=202)
def assemble_all_components(
    course_id: str,
    body: ComponentAssembleRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    course_data = _editable_course_data(course_id, current_user)
    task_id = str(uuid.uuid4())[:12]
    create_background_task(task_id, "component_assembly", current_user["id"])
    threading.Thread(
        target=_component_assembly_worker,
        args=(task_id, course_id, course_data, current_user["id"], body.force),
        daemon=True,
    ).start()
    return {"task_id": task_id, "course_id": course_id, "status": "pending"}


@router.post("/courses/{course_id}/component-graph/update", status_code=202)
def update_course_component_graph(
    course_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    course_data = _editable_course_data(course_id, current_user)
    task_id = str(uuid.uuid4())[:12]
    create_background_task(task_id, "component_graph_update", current_user["id"])
    threading.Thread(
        target=_worker_wrapper,
        args=(task_id, _run_graph_update, course_id, course_data, current_user["id"]),
        daemon=True,
    ).start()
    return {"task_id": task_id, "course_id": course_id, "status": "pending"}


def _analysis_pipeline_worker(task_id: str, course_id: str, course_data: dict, user_id: str) -> None:
    try:
        update_background_task(task_id, "processing", result_data={
            "course_id": course_id, "progress": 0, "stage": "structure",
            "steps": {"structure": "processing", "claims": "pending", "components": "pending", "graph": "pending"},
        })
        structure_task = str(uuid.uuid4())[:12]
        create_background_task(structure_task, "structure_reanalysis", user_id)
        reanalyze_course_structure_background(course_id, course_data, structure_task)
        update_background_task(task_id, "processing", result_data={
            "course_id": course_id, "progress": 25, "stage": "claims",
            "steps": {"structure": "completed", "claims": "processing", "components": "pending", "graph": "pending"},
        })
        _run_claim_extraction(course_id, course_data, user_id)
        update_background_task(task_id, "processing", result_data={
            "course_id": course_id, "progress": 50, "stage": "components",
            "steps": {"structure": "completed", "claims": "completed", "components": "processing", "graph": "pending"},
        })
        _run_component_assembly(course_id, course_data, user_id)
        update_background_task(task_id, "processing", result_data={
            "course_id": course_id, "progress": 75, "stage": "graph",
            "steps": {"structure": "completed", "claims": "completed", "components": "completed", "graph": "processing"},
        })
        _run_graph_update(course_id, course_data, user_id)
        update_background_task(task_id, "completed", result_data={
            "course_id": course_id, "progress": 100, "stage": "completed",
            "steps": {"structure": "completed", "claims": "completed", "components": "completed", "graph": "completed"},
        })
    except Exception as exc:
        logger.exception("Analysis pipeline failed for course %s", course_id)
        update_background_task(task_id, "failed", error_message=str(exc))


@router.post("/courses/{course_id}/analysis/run-all", status_code=202)
def run_course_analysis_pipeline(
    course_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    course_data = _editable_course_data(course_id, current_user)
    task_id = str(uuid.uuid4())[:12]
    create_background_task(task_id, "analysis_pipeline", current_user["id"])
    threading.Thread(
        target=_analysis_pipeline_worker,
        args=(task_id, course_id, course_data, current_user["id"]),
        daemon=True,
    ).start()
    return {"task_id": task_id, "course_id": course_id, "status": "pending"}


@router.get("/courses/{course_id}/theory-components", response_model=list[TheoryComponentOut])
def list_theory_components(
    course_id: str,
    chunk_id: str | None = None,
    current_user: dict = Depends(_require_teacher),
) -> list[TheoryComponentOut]:
    _ensure_viewable(course_id, current_user)
    session = _pg_session()
    try:
        params = {"course_id": course_id, "chunk_id": chunk_id}
        if chunk_id:
            where = """
                course_id = :course_id
                AND (
                    primary_chunk_id = CAST(:chunk_id AS uuid)
                    OR source_chunks @> CAST(:source_chunk_filter AS jsonb)
                )
            """
            params["source_chunk_filter"] = json.dumps([{"chunk_id": chunk_id}])
        else:
            where = "course_id = :course_id"
        rows = session.execute(sa_text(_select_components_sql(where)), params).fetchall()
        return [_row_to_out(row) for row in rows]
    finally:
        session.close()


@router.post("/chunks/{chunk_id}/theory-components/extract", response_model=TheoryComponentExtractResponse)
def extract_theory_components(
    chunk_id: str,
    body: TheoryComponentExtractRequest,
    current_user: dict = Depends(_require_teacher),
) -> TheoryComponentExtractResponse:
    chunk = _chunk_row(chunk_id)
    if not chunk:
        raise HTTPException(status_code=404, detail="Chunk not found")
    course_id = _find_course_for_chunk(chunk, current_user)
    if not course_id:
        raise HTTPException(status_code=404, detail="Course not found")

    structural_components = extract_theory_components_from_dsl(chunk)
    raw_components = structural_components
    if body.use_llm and raw_components:
        raw_components = _preserve_structural_io(
            enrich_theory_components_with_llm(chunk, raw_components),
            structural_components,
        )
    saved: list[TheoryComponentOut] = []
    for raw in raw_components:
        payload = _normalize_payload(_raw_component_to_request(raw, chunk), chunk)
        status_filter = "status != 'rejected'" if body.force else "status IN ('candidate', 'draft')"
        session = _pg_session()
        try:
            existing = session.execute(
                sa_text(f"""
                    SELECT id FROM theory_components
                    WHERE course_id = :course_id
                      AND primary_chunk_id = CAST(:chunk_id AS uuid)
                      AND lower(name) = lower(:name)
                      AND {status_filter}
                    LIMIT 1
                """),
                {"course_id": course_id, "chunk_id": chunk_id, "name": payload["name"]},
            ).fetchone()
        finally:
            session.close()
        should_update_existing = bool(body.force or body.use_llm)
        if existing and not should_update_existing:
            component = _get_component(str(existing[0]))
            if component:
                saved.append(component)
            continue
        if existing:
            saved.append(_update_component(str(existing[0]), payload))
        else:
            saved.append(_insert_component(course_id, chunk_id, payload, current_user["id"]))
    return TheoryComponentExtractResponse(chunk_id=chunk_id, components=saved)


@router.post("/courses/{course_id}/theory-components", response_model=TheoryComponentOut)
def create_theory_component(
    course_id: str,
    body: TheoryComponentUpsertRequest,
    current_user: dict = Depends(_require_teacher),
) -> TheoryComponentOut:
    _ensure_editable(course_id, current_user)
    payload = _normalize_payload(body)
    return _insert_component(course_id, None, payload, current_user["id"])


@router.put("/theory-components/{component_id}", response_model=TheoryComponentOut)
def update_theory_component(
    component_id: str,
    body: TheoryComponentUpsertRequest,
    current_user: dict = Depends(_require_teacher),
) -> TheoryComponentOut:
    existing = _get_component(component_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Theory component not found")
    _ensure_editable(existing.course_id, current_user)
    payload = _normalize_payload(body)
    return _update_component(component_id, payload)


@router.post("/theory-components/{component_id}/reject", response_model=TheoryComponentOut)
def reject_theory_component(
    component_id: str,
    current_user: dict = Depends(_require_teacher),
) -> TheoryComponentOut:
    existing = _get_component(component_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Theory component not found")
    _ensure_editable(existing.course_id, current_user)
    payload = _dump_model(existing)
    payload["status"] = "rejected"
    return _update_component(component_id, payload)


@router.post("/courses/{course_id}/theory-components/validate-connection")
def validate_connection(
    course_id: str,
    body: TheoryConnectionValidateRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    _ensure_viewable(course_id, current_user)
    source = _get_component(body.source_component_id)
    target = _get_component(body.target_component_id)
    if not source or not target or source.course_id != course_id or target.course_id != course_id:
        raise HTTPException(status_code=404, detail="Theory component not found")

    source_labels = [str(item.label).lower() for item in source.outputs]
    source_labels += [str(item.label).lower() for item in source.constraints]
    target_inputs = [str(item.label).lower() for item in target.inputs]
    matched = [
        {"source": s, "target": t}
        for s in source_labels
        for t in target_inputs
        if s and t and (s == t or s in t or t in s)
    ]
    warnings = []
    if not matched:
        warnings.append("source.outputs と target.inputs に明確な一致がありません。")
    for pre in target.preconditions:
        label = pre.label.lower()
        if label and not any(label == s or label in s or s in label for s in source_labels):
            warnings.append(f"target の成立条件 `{pre.label}` を source 側で満たせるか未確認です。")
    for invalid in source.invalid_conditions:
        ilabel = invalid.label.lower()
        for constraint in target.constraints:
            clabel = constraint.label.lower()
            if ilabel and clabel and (ilabel == clabel or ilabel in clabel or clabel in ilabel):
                warnings.append(f"`{invalid.label}` と `{constraint.label}` が衝突する可能性があります。")
    return {
        "status": "valid" if matched and not warnings else "warning",
        "matched": matched,
        "warnings": warnings,
    }
