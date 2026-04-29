"""Lecture Studio theory component APIs."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text as sa_text

from dependencies import ROLE_SYSTEM_ADMIN, _require_teacher
from schemas import (
    TheoryComponentExtractRequest,
    TheoryComponentExtractResponse,
    TheoryComponentOut,
    TheoryComponentUpsertRequest,
    TheoryConnectionValidateRequest,
)
from services import get_editable_course_data, get_viewable_course_data
from core.postgres import get_session as _pg_session
from core.theory_components import (
    enrich_theory_components_with_llm,
    extract_theory_components_from_dsl,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Theory Components"])


_JSON_FIELDS = (
    "source_chunks",
    "inputs",
    "outputs",
    "preconditions",
    "constraints",
    "invalid_conditions",
    "dependencies",
    "blackbox_policy",
    "validation_warnings",
)


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
        "created_at": row[17].isoformat() if row[17] else "",
        "updated_at": row[18].isoformat() if row[18] else "",
    }
    return TheoryComponentOut(**data)


def _select_components_sql(where: str) -> str:
    return f"""
        SELECT id, course_id, primary_chunk_id, name, component_type, summary, status,
               source_chunks, inputs, outputs, preconditions, constraints,
               invalid_conditions, dependencies, blackbox_policy, validation_warnings,
               teacher_notes, created_at, updated_at
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


def _source_ref_for_chunk(chunk: dict) -> dict:
    quote = (chunk.get("raw_text") or chunk.get("text") or "").strip()
    return {
        "chunk_id": chunk["id"],
        "page_start": chunk.get("page_start"),
        "page_end": chunk.get("page_end"),
        "quote": quote[:240],
    }


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
    for field in ("source_chunks", "inputs", "outputs", "preconditions", "constraints", "invalid_conditions", "dependencies"):
        payload[field] = payload.get(field) or []
    payload["blackbox_policy"] = payload.get("blackbox_policy") or {
        "default_level": "summary",
        "expand_if_unlearned": True,
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
        "source_chunks": fixed_source_chunks or [_source_ref_for_chunk(chunk)],
        "inputs": _raw_items_with_chunk_refs(raw.get("inputs"), chunk),
        "outputs": _raw_items_with_chunk_refs(raw.get("outputs"), chunk),
        "preconditions": _raw_items_with_chunk_refs(raw.get("preconditions"), chunk),
        "constraints": _raw_items_with_chunk_refs(raw.get("constraints"), chunk),
        "invalid_conditions": _raw_items_with_chunk_refs(raw.get("invalid_conditions"), chunk),
        "dependencies": _raw_items_with_chunk_refs(raw.get("dependencies"), chunk),
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
                    teacher_notes, created_by
                )
                VALUES (
                    :course_id, CAST(:primary_chunk_id AS uuid), :name, :component_type, :summary, :status,
                    CAST(:source_chunks AS jsonb), CAST(:inputs AS jsonb), CAST(:outputs AS jsonb),
                    CAST(:preconditions AS jsonb), CAST(:constraints AS jsonb),
                    CAST(:invalid_conditions AS jsonb), CAST(:dependencies AS jsonb),
                    CAST(:blackbox_policy AS jsonb), CAST(:validation_warnings AS jsonb),
                    :teacher_notes, CAST(:created_by AS uuid)
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
