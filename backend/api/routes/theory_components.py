"""Lecture Studio theory component APIs."""

from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path
import random
import re
import threading
import time
import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field
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
from core.config import get_settings
from core.concept_normalizer import normalize_concept, normalize_concepts, normalize_key
from core.document_sections import build_document_structure, detect_section_heading, enrich_chunks_with_sections
from core.postgres import get_session as _pg_session
from core.cartridges import load_cartridge
from core.llm import generate_text, generate_text_with_structured_output, get_llm_params
from core.theory_components import (
    enrich_theory_components_with_llm,
    extract_theory_components_from_dsl,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Theory Components"])


class _LLMClaimConcept(BaseModel):
    name: str = ""
    concept_type: str = ""


class _LLMClaimEquation(BaseModel):
    equation_id: str = ""
    label: str = ""
    latex: str = ""
    defined_symbols: list[str] = Field(default_factory=list)
    relation_type: str = ""


class _LLMClaimSourceScope(BaseModel):
    level: str = "chunk"
    document_id: str = ""
    section_id: str = ""
    chunk_id: str = ""


class _LLMClaimItem(BaseModel):
    claim_type: str = "relation"
    text: str = ""
    normalized_text: str = ""
    concepts: list[_LLMClaimConcept] = Field(default_factory=list)
    equation: _LLMClaimEquation | None = None
    support_status: str = "source_backed"
    evidence_text: str = ""
    review_status: str = "teacher_review_required"
    source_scope: _LLMClaimSourceScope | None = None


class _LLMClaimExtractionResult(BaseModel):
    claims: list[_LLMClaimItem] = Field(default_factory=list)


class _LLMComponentItemRef(BaseModel):
    name: str = ""
    concept_type: str = "Concept"
    support_status: str = "source_backed"
    evidence_claims: list[str] = Field(default_factory=list)
    description: str = ""


class _LLMComponentConditionRef(BaseModel):
    name: str = ""
    support_status: str = "source_backed"
    evidence_claims: list[str] = Field(default_factory=list)
    description: str = ""


class _LLMComponentConnectors(BaseModel):
    requires_before_use: list[str] = Field(default_factory=list)
    can_accept: list[str] = Field(default_factory=list)
    can_output_to: list[str] = Field(default_factory=list)
    may_conflict_with: list[str] = Field(default_factory=list)


class _LLMComponentItem(BaseModel):
    name: str = ""
    component_type: str = ""
    summary: str = ""
    maturity_level: str = ""
    maturity_source: str = "llm_proposed"
    review_status: str = "teacher_review_required"
    evidence_claims: list[str] = Field(default_factory=list)
    inputs: list[_LLMComponentItemRef] = Field(default_factory=list)
    outputs: list[_LLMComponentItemRef] = Field(default_factory=list)
    preconditions: list[_LLMComponentConditionRef] = Field(default_factory=list)
    cautions: list[_LLMComponentConditionRef] = Field(default_factory=list)
    dependencies: list[_LLMComponentItemRef] = Field(default_factory=list)
    internal_flow: list[str] = Field(default_factory=list)
    connectors: _LLMComponentConnectors | None = None
    io_summary: str = ""


class _LLMComponentAssembleResult(BaseModel):
    components: list[_LLMComponentItem] = Field(default_factory=list)


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
    "internal_flow",
    "duplicate_candidates",
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
    "equation_definition",
    "equation_relation",
    "equation_transformation",
    "equation_approximation",
    "equation_constraint",
}

_LEGACY_COMPONENT_TYPES = {"theory", "concept", "law", "mechanism", "operator", "observation"}
_CLAIM_EXTRACTABLE_ROLES = {"body", "abstract", "equation_block", "caption"}
_CLAIM_SKIP_ROLES = {"front_matter", "metadata", "references", "acknowledgement"}
_SUPPORT_STATUS_VALUES = {"source_backed", "source_inferred", "domain_inferred", "design_inferred", "unsupported"}
_PROHIBITED_COMPONENT_NAME_RE = re.compile(
    r"^(component for (page|section|chunk_group)|untitled component|generic component|summary component)\b",
    re.IGNORECASE,
)
_GRAPH_RELATIONS = {
    "ENABLES",
    "PRODUCES_FOR",
    "REQUIRES",
    "QUALIFIES",
    "LIMITS",
    "CHECKED_BY",
    "CONFLICTS_WITH",
    "RELATED_TO",
}
_MAX_COMPONENT_ASSEMBLY_CLAIMS = 40
_MAX_COMPONENTS_PER_SECTION = 3
_CLAIM_PRIORITY_TYPES = (
    "equation_relation",
    "equation_definition",
    "derivation_step",
    "assumption",
    "approximation",
    "correction",
    "limitation",
    "diagnostic_claim",
    "result",
)
_MEANINGFUL_REQUIREMENTS_BY_TYPE: dict[str, list[str]] = {
    "DomainConceptComponent": ["outputs", "preconditions", "cautions", "internal_flow"],
    "DomainAssumptionComponent": ["preconditions", "outputs", "cautions"],
    "DomainMethodComponent": ["inputs", "outputs", "preconditions", "internal_flow"],
    "DomainTheoryComponent": ["inputs", "outputs", "preconditions", "dependencies", "internal_flow"],
    "PaperRelationComponent": ["inputs", "outputs", "internal_flow"],
    "PaperCorrectionComponent": ["inputs", "outputs", "cautions"],
    "PaperDiagnosticComponent": ["inputs", "outputs", "cautions"],
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


def _normalize_io_items(value: Any) -> list[dict]:
    items = _json_value(value, [])
    if not isinstance(items, list):
        return []
    normalized = []
    for item in items:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or item.get("name") or item.get("text") or item.get("condition") or "").strip()
        if not label:
            continue
        copied = dict(item)
        copied["label"] = label
        copied["name"] = str(copied.get("name") or label)
        copied["type"] = str(copied.get("type") or copied.get("concept_type") or "Concept")
        copied["concept_type"] = str(copied.get("concept_type") or copied.get("type") or "Concept")
        copied["required"] = bool(copied.get("required", True))
        copied["description"] = str(copied.get("description") or copied.get("evidence_text") or "")
        copied["support_status"] = str(copied.get("support_status") or "source_backed")
        copied["evidence_claims"] = copied.get("evidence_claims") if isinstance(copied.get("evidence_claims"), list) else []
        copied["source_refs"] = copied.get("source_refs") if isinstance(copied.get("source_refs"), list) else []
        copied["needs_source"] = bool(copied.get("needs_source", False))
        normalized.append(copied)
    return normalized


def _normalize_condition_items(value: Any) -> list[dict]:
    items = _json_value(value, [])
    if not isinstance(items, list):
        return []
    normalized = []
    for item in items:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or item.get("condition") or item.get("text") or item.get("name") or "").strip()
        if not label:
            continue
        copied = dict(item)
        copied["label"] = label
        copied["condition"] = str(copied.get("condition") or copied.get("text") or label)
        copied["description"] = str(copied.get("description") or copied.get("evidence_text") or "")
        copied["support_status"] = str(copied.get("support_status") or "source_backed")
        copied["evidence_claims"] = copied.get("evidence_claims") if isinstance(copied.get("evidence_claims"), list) else []
        copied["source_refs"] = copied.get("source_refs") if isinstance(copied.get("source_refs"), list) else []
        copied["needs_source"] = bool(copied.get("needs_source", False))
        normalized.append(copied)
    return normalized


def _normalize_internal_flow_items(value: Any) -> list[dict]:
    items = _json_value(value, [])
    if not isinstance(items, list):
        return []
    normalized = []
    for item in items:
        if isinstance(item, dict):
            normalized.append(item)
        elif isinstance(item, str) and item.strip():
            normalized.append({"description": item.strip()})
    return normalized


class LLMStructuredOutputError(RuntimeError):
    def __init__(self, code: str, message: str, attempts: int, **context: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.attempts = attempts
        self.context = context

    def detail(self) -> dict:
        return {"code": self.code, "message": self.message, "attempts": self.attempts, **self.context}


def _llm_retry_policy() -> tuple[int, float]:
    settings = get_settings()
    return max(1, int(settings.llm_max_retries)), max(0.0, float(settings.llm_retry_backoff_seconds))


def _is_resource_exhausted(exc: Exception) -> bool:
    """Return True if the exception indicates a 429 / resource exhausted error."""
    msg = str(exc).lower()
    class_name = type(exc).__name__.lower()
    return (
        "429" in msg
        or "resource exhausted" in msg
        or "resourceexhausted" in class_name
        or "rate limit" in msg
        or "ratelimit" in class_name
        or "quota" in msg
    )


def _backoff_seconds(attempt: int, base: float, is_rate_limited: bool) -> float:
    """Compute wait time: exponential for 429, linear otherwise, always with jitter."""
    if is_rate_limited:
        wait = min(60.0, base * (2 ** (attempt - 1)))
    else:
        wait = base * attempt
    return wait + random.random()


def _row_to_out(row: Any) -> TheoryComponentOut:
    data = {
        "id": str(row[0]),
        "course_id": row[1] or "",
        "primary_chunk_id": str(row[2]) if row[2] else None,
        "name": row[3],
        "component_type": row[26] or row[4],
        "summary": row[5] or "",
        "status": row[6],
        "source_chunks": _json_value(row[7], []),
        "inputs": _normalize_io_items(row[8]),
        "outputs": _normalize_io_items(row[9]),
        "preconditions": _normalize_condition_items(row[10]),
        "constraints": _normalize_condition_items(row[11]),
        "invalid_conditions": _normalize_condition_items(row[12]),
        "dependencies": _normalize_condition_items(row[13]),
        "blackbox_policy": _json_value(row[14], {}),
        "validation_warnings": _json_value(row[15], []),
        "teacher_notes": row[16] or "",
        "source_scope": _json_value(row[17], {}),
        "evidence_claims": _json_value(row[18], []),
        "maturity_level": row[19] or "paper_claim",
        "maturity_source": row[20] or "llm_proposed",
        "review_status": row[21] or "teacher_review_required",
        "cautions": _normalize_condition_items(row[22]),
        "connectors": _json_value(row[23], {}),
        "internal_flow": _normalize_internal_flow_items(row[27]),
        "duplicate_candidates": _json_value(row[28], []),
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
               review_status, cautions, connectors, created_at, updated_at,
               component_type_text, internal_flow, duplicate_candidates
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
        chunks = [{
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
        return enrich_chunks_with_sections(chunks)
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
        chunks = [{
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
        return enrich_chunks_with_sections(chunks)
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
    if chunk.get("section_id"):
        return str(chunk["section_id"])
    document_id = chunk.get("document_id") or chunk.get("material_id") or "document"
    heading = _detect_section_heading(chunk.get("raw_text") or chunk.get("text") or "")
    if heading:
        sec_num = re.sub(r"[^0-9A-Za-z]+", "_", heading["number"]).strip("_").lower()
        return f"{document_id}:sec_{sec_num}"
    index = int(chunk.get("chunk_index") or 0)
    return f"{document_id}:chunk_group_{max(1, (index // 4) + 1)}"


def _section_title_for_chunk(chunk: dict) -> str:
    if chunk.get("section_title"):
        return str(chunk["section_title"])
    heading = _detect_section_heading(chunk.get("raw_text") or chunk.get("text") or "")
    if heading:
        return heading["title"]
    return ""


def _extract_equation_labels(text: str) -> list[str]:
    labels = []
    for pattern in (r"\((\d+(?:\.\d+)+)\)", r"Eq(?:uation)?\.?\s*\(?(\d+(?:\.\d+)+)\)?", r"\\label\{([^}]+)\}"):
        for match in re.finditer(pattern, text or "", re.IGNORECASE):
            label = match.group(1).strip()
            if label and label not in labels:
                labels.append(label)
    return labels[:8]


def _equation_payload_from_claim(raw: dict, chunk: dict) -> dict:
    equation = raw.get("equation") if isinstance(raw.get("equation"), dict) else {}
    labels = _extract_equation_labels((chunk.get("raw_text") or chunk.get("text") or "") + "\n" + str(raw.get("text") or ""))
    if not equation and not labels:
        return {}
    label = str(equation.get("label") or (labels[0] if labels else "")).strip()
    latex = str(equation.get("latex") or raw.get("latex") or "").strip()
    symbols = equation.get("defined_symbols") if isinstance(equation.get("defined_symbols"), list) else []
    return {
        "equation_id": equation.get("equation_id") or (f"eq_{label.replace('.', '_')}" if label else ""),
        "label": label,
        "latex": latex,
        "plain_text": equation.get("plain_text") or re.sub(r"\\[A-Za-z]+", "", latex),
        "defined_symbols": [str(s) for s in symbols if str(s).strip()],
        "relation_type": equation.get("relation_type") or "",
        "from_equation": raw.get("from_equation") or equation.get("from_equation") or "",
        "to_equation": raw.get("to_equation") or equation.get("to_equation") or "",
        "transformation_type": raw.get("transformation_type") or equation.get("transformation_type") or "",
        "assumptions": raw.get("assumptions") if isinstance(raw.get("assumptions"), list) else [],
    }


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
        "section_title": _section_title_for_chunk(chunk),
        "section_level": int(chunk.get("section_level") or 0),
        "section_order": int(chunk.get("section_order") or 0),
        "chunk_id": chunk_id if level == "chunk" else "",
        "chunks": [chunk_id] if chunk_id else [],
        "pages": pages,
        "equations": _extract_equation_labels(chunk.get("raw_text") or chunk.get("text") or ""),
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
        "equation": _json_value(row[8], {}),
        "support_status": row[9] or "source_backed",
        "evidence_text": row[10] or "",
        "review_status": row[11] or "teacher_review_required",
        "created_by": str(row[12]) if row[12] else None,
        "created_at": row[13].isoformat() if row[13] else "",
        "updated_at": row[14].isoformat() if row[14] else "",
    })


def _claim_rows_for_chunk(chunk_id: str) -> list[ClaimOut]:
    session = _pg_session()
    try:
        rows = session.execute(
            sa_text("""
                SELECT id, document_id, chunk_id, source_scope, claim_type, text,
                       normalized_text, concepts, equation, support_status, evidence_text,
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


def _claim_rows_for_document(document_id: str) -> list[ClaimOut]:
    session = _pg_session()
    try:
        rows = session.execute(
            sa_text("""
                SELECT id, document_id, chunk_id, source_scope, claim_type, text,
                       normalized_text, concepts, equation, support_status, evidence_text,
                       review_status, created_by, created_at, updated_at
                FROM theory_claims
                WHERE document_id = :document_id
                ORDER BY created_at ASC
            """),
            {"document_id": document_id},
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
                       normalized_text, concepts, equation, support_status, evidence_text,
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


def _parse_json_object(raw: str) -> dict[str, Any]:
    cleaned = (raw or "").strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        parsed = json.loads(cleaned, strict=False)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        match = re.search(r"\{[\s\S]*\}", cleaned)
        if not match:
            return {}
        try:
            parsed = json.loads(match.group(), strict=False)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}


def _redact_for_log(value: str, limit: int = 700) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    return text[:limit] + ("..." if len(text) > limit else "")


def _strip_nul(value: Any) -> Any:
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, list):
        return [_strip_nul(item) for item in value]
    if isinstance(value, dict):
        return {str(_strip_nul(key)): _strip_nul(val) for key, val in value.items()}
    return value


def _clean_pg_text(value: Any, default: str = "") -> str:
    return str(value if value is not None else default).replace("\x00", "")


def _json_dumps_pg_safe(value: Any, default: Any) -> str:
    cleaned = _strip_nul(value if value is not None else default)
    dumped = json.dumps(cleaned, ensure_ascii=False)
    return dumped.replace("\\u0000", "")


def _contains_nul(value: Any) -> bool:
    if isinstance(value, str):
        return "\x00" in value
    if isinstance(value, list):
        return any(_contains_nul(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_nul(k) or _contains_nul(v) for k, v in value.items())
    return False


def _nul_fields(payload: dict) -> list[str]:
    return [key for key, value in payload.items() if _contains_nul(value)]


def _llm_response_debug(raw: str, parsed: dict | None = None) -> dict[str, Any]:
    parsed = parsed if isinstance(parsed, dict) else {}
    return {
        "raw_chars": len(raw or ""),
        "raw_preview": _redact_for_log(raw or ""),
        "parsed_keys": sorted(parsed.keys())[:12],
        "claims_type": type(parsed.get("claims")).__name__ if "claims" in parsed else "missing",
        "components_type": type(parsed.get("components")).__name__ if "components" in parsed else "missing",
    }


def _detect_section_heading(text: str) -> dict[str, str] | None:
    heading = detect_section_heading(text)
    return dict(heading) if heading else None


def _classify_chunk_role(chunk: dict) -> str:
    text = (chunk.get("raw_text") or chunk.get("text") or "").strip()
    lower = text.lower()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    first_lines = "\n".join(lines[:12]).lower()
    if not text:
        return "unknown"
    if re.search(r"^\s*(references|bibliography)\s*$", text, re.IGNORECASE | re.MULTILINE):
        return "references"
    if re.search(r"^\s*(acknowledg(e)?ments?)\s*$", text, re.IGNORECASE | re.MULTILINE):
        return "acknowledgement"
    if any(token in first_lines for token in (
        "published for sissa", "received:", "accepted:", "published:", "springer",
        "arxiv:", "doi:", "journal of high energy physics", "jhep",
    )):
        return "front_matter"
    if len(lines) <= 8 and any(token in lower for token in ("received:", "published", "doi", "copyright", "open access")):
        return "metadata"
    if "abstract" in first_lines[:160]:
        return "abstract"
    if any(token in text for token in ("\\begin{equation}", "\\[", "$$")):
        return "equation_block"
    return "body"


def _is_low_value_claim_text(text: str) -> bool:
    lower = (text or "").strip().lower()
    if not lower:
        return True
    if len(lower) < 24:
        return True
    bad_patterns = [
        r"^jhep\d+",
        r"^published for",
        r"^springer$",
        r"^received:",
        r"^accepted:",
        r"^published:",
        r"^doi:",
        r"^arxiv:",
        r"^references$",
        r"^bibliography$",
    ]
    return any(re.search(pattern, lower) for pattern in bad_patterns)


def _normalize_claim_payload(raw: dict, chunk: dict, strict: bool = True) -> dict | None:
    if not isinstance(raw, dict):
        raise ValueError("claim item must be an object")
    required = ("claim_type", "text", "support_status", "evidence_text")
    missing = [field for field in required if raw.get(field) in (None, "")]
    if strict and missing:
        raise ValueError(f"claim missing required fields: {', '.join(missing)}")
    claim_type = str(raw.get("claim_type") or "").strip()
    if claim_type not in _CLAIM_TYPES:
        raise ValueError(f"invalid claim_type: {claim_type}")
    text = _clean_pg_text(raw.get("text") or raw.get("normalized_text") or "").strip()
    if _is_low_value_claim_text(text):
        return None
    evidence = _clean_pg_text(raw.get("evidence_text") or "").strip()
    if not evidence and strict:
        raise ValueError("claim missing required field: evidence_text")
    if not evidence and not strict:
        evidence = text[:360]
    support_status = str(raw.get("support_status") or "").strip()
    if support_status not in _SUPPORT_STATUS_VALUES:
        raise ValueError(f"invalid support_status: {support_status}")
    review_status = str(raw.get("review_status") or "teacher_review_required").strip()
    if review_status not in ("teacher_review_required", "needs_revision", "rejected"):
        review_status = "teacher_review_required"
    concepts = raw.get("concepts") if isinstance(raw.get("concepts"), list) else []
    normalized_concepts = []
    for concept in concepts[:8]:
        if not isinstance(concept, dict):
            continue
        name = _clean_pg_text(concept.get("name") or "").strip()
        if not name:
            continue
        normalized_concepts.append({
            "name": name,
            "concept_type": str(concept.get("concept_type") or "Concept").strip() or "Concept",
        })
    if not normalized_concepts and not strict:
        normalized_concepts = _fallback_concepts(text)
    normalized_concepts = normalize_concepts(normalized_concepts)
    equation = _equation_payload_from_claim(raw, chunk)
    if claim_type.startswith("equation_") and not equation:
        raise ValueError("equation claim missing equation payload")
    scope = _source_scope_for_chunk(chunk, "chunk")
    if isinstance(raw.get("source_scope"), dict):
        scope.update({k: v for k, v in raw["source_scope"].items() if v not in (None, "", [])})
    return _strip_nul({
        "claim_type": claim_type,
        "text": text[:1200],
        "normalized_text": _clean_pg_text(raw.get("normalized_text") or text).strip()[:1200],
        "concepts": normalized_concepts,
        "equation": equation,
        "support_status": support_status,
        "evidence_text": evidence[:500],
        "review_status": review_status or "teacher_review_required",
        "source_scope": scope,
    })


def _fallback_concepts(text: str) -> list[dict]:
    candidates = []
    patterns = [
        (r"heavy quark limit", "Approximation"),
        (r"zero[- ]recoil limit", "Approximation"),
        (r"sum rule", "Relation"),
        (r"form factor[s]?", "UncertaintySource"),
        (r"uncertaint(?:y|ies)", "Uncertainty"),
        (r"correction[s]?", "CorrectionSource"),
        (r"R[Λ\\A-Za-z0-9*_]+", "Observable"),
    ]
    for pattern, concept_type in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            name = match.group(0)
            if not any(c["name"].lower() == name.lower() for c in candidates):
                candidates.append({"name": name, "concept_type": concept_type})
    return candidates[:6]



def _insert_claim(document_id: str, chunk_id: str, payload: dict, user_id: str) -> ClaimOut:
    if payload.get("claim_type") not in _CLAIM_TYPES:
        raise HTTPException(status_code=422, detail="Invalid claim_type")
    nul_fields = _nul_fields(payload)
    if nul_fields:
        logger.warning(
            "NUL characters detected in claim payload before insert: fields=%s document_id=%s chunk_id=%s",
            nul_fields,
            _clean_pg_text(document_id),
            chunk_id,
        )
    session = _pg_session()
    try:
        params = {
            "document_id": _clean_pg_text(document_id),
            "chunk_id": chunk_id,
            "source_scope": _json_dumps_pg_safe(payload.get("source_scope"), {}),
            "claim_type": _clean_pg_text(payload.get("claim_type") or "diagnostic_claim"),
            "text": _clean_pg_text(payload.get("text")),
            "normalized_text": _clean_pg_text(payload.get("normalized_text") or payload.get("text")),
            "concepts": _json_dumps_pg_safe(payload.get("concepts"), []),
            "equation": _json_dumps_pg_safe(payload.get("equation"), {}),
            "support_status": _clean_pg_text(payload.get("support_status") or "source_backed"),
            "evidence_text": _clean_pg_text(payload.get("evidence_text")),
            "review_status": _clean_pg_text(payload.get("review_status") or "teacher_review_required"),
            "created_by": user_id or None,
        }
        row = session.execute(
            sa_text("""
                INSERT INTO theory_claims (
                    document_id, chunk_id, source_scope, claim_type, text, normalized_text,
                    concepts, equation, support_status, evidence_text, review_status, created_by
                )
                VALUES (
                    :document_id, CAST(:chunk_id AS uuid), CAST(:source_scope AS jsonb),
                    :claim_type, :text, :normalized_text, CAST(:concepts AS jsonb),
                    CAST(:equation AS jsonb), :support_status, :evidence_text, :review_status, CAST(:created_by AS uuid)
                )
                RETURNING id, document_id, chunk_id, source_scope, claim_type, text,
                          normalized_text, concepts, equation, support_status, evidence_text,
                          review_status, created_by, created_at, updated_at
            """),
            params,
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


def _record_review_event(entity_type: str, entity_id: str, old_status: str, new_status: str, user_id: str | None, metadata: dict | None = None) -> None:
    session = _pg_session()
    try:
        session.execute(
            sa_text("""
                INSERT INTO theory_review_events (entity_type, entity_id, old_status, new_status, changed_by, metadata)
                VALUES (:entity_type, :entity_id, :old_status, :new_status, CAST(:changed_by AS uuid), CAST(:metadata AS jsonb))
            """),
            {
                "entity_type": entity_type,
                "entity_id": entity_id,
                "old_status": old_status or "",
                "new_status": new_status or "",
                "changed_by": user_id or None,
                "metadata": json.dumps(metadata or {}, ensure_ascii=False),
            },
        )
        session.commit()
    except Exception:
        session.rollback()
        logger.warning("Failed to record review event for %s %s", entity_type, entity_id, exc_info=True)
    finally:
        session.close()


def _propagate_rejected_claim(claim_id: str) -> None:
    session = _pg_session()
    try:
        session.execute(
            sa_text("""
                UPDATE theory_components
                SET review_status = CASE WHEN review_status = 'teacher_approved' THEN 'needs_revision' ELSE review_status END,
                    validation_warnings = validation_warnings || CAST(:warning AS jsonb),
                    updated_at = now()
                WHERE evidence_claims ? :claim_id
            """),
            {"claim_id": claim_id, "warning": json.dumps([{"severity": "warning", "code": "rejected_evidence_claim", "message": "A supporting Claim was rejected; this Component needs review.", "claim_id": claim_id}], ensure_ascii=False)},
        )
        rows = session.execute(sa_text("SELECT id, graph_json FROM theory_component_graphs")).fetchall()
        for row in rows:
            graph = _json_value(row[1], {})
            changed = False
            for edge in graph.get("edges", []) if isinstance(graph, dict) else []:
                evidence = edge.get("evidence") or {}
                evidence_claims = list(evidence.get("source_claims") or []) + list(evidence.get("target_claims") or [])
                if claim_id in evidence_claims:
                    edge["review_status"] = "needs_revision"
                    changed = True
            if changed:
                session.execute(
                    sa_text("UPDATE theory_component_graphs SET graph_json = CAST(:graph AS jsonb), validation_results = CAST(:validation AS jsonb), updated_at = now() WHERE id = CAST(:id AS uuid)"),
                    {"id": str(row[0]), "graph": json.dumps(graph, ensure_ascii=False), "validation": json.dumps(graph.get("validation_results") or [], ensure_ascii=False)},
                )
        session.commit()
    except Exception:
        session.rollback()
        logger.warning("Failed to propagate rejected claim %s", claim_id, exc_info=True)
    finally:
        session.close()


def _propagate_rejected_component(component_id: str) -> None:
    session = _pg_session()
    try:
        rows = session.execute(sa_text("SELECT id, graph_json FROM theory_component_graphs")).fetchall()
        for row in rows:
            graph = _json_value(row[1], {})
            changed = False
            for edge in graph.get("edges", []) if isinstance(graph, dict) else []:
                if component_id in (edge.get("source_component_id"), edge.get("target_component_id")):
                    edge["review_status"] = "needs_revision"
                    edge["disabled"] = True
                    changed = True
            if changed:
                session.execute(
                    sa_text("UPDATE theory_component_graphs SET graph_json = CAST(:graph AS jsonb), validation_results = CAST(:validation AS jsonb), updated_at = now() WHERE id = CAST(:id AS uuid)"),
                    {"id": str(row[0]), "graph": json.dumps(graph, ensure_ascii=False), "validation": json.dumps(graph.get("validation_results") or [], ensure_ascii=False)},
                )
        session.commit()
    except Exception:
        session.rollback()
        logger.warning("Failed to propagate rejected component %s", component_id, exc_info=True)
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
    component_type = str(payload.get("component_type") or "theory").strip()
    payload["component_type_text"] = component_type
    payload["component_type"] = component_type if component_type in _LEGACY_COMPONENT_TYPES else "theory"
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
    payload["internal_flow"] = payload.get("internal_flow") or []
    payload["duplicate_candidates"] = payload.get("duplicate_candidates") or []
    payload["maturity_level"] = payload.get("maturity_level") or "paper_claim"
    payload["maturity_source"] = payload.get("maturity_source") or "llm_proposed"
    if payload.get("status") == "teacher_reviewed":
        payload["review_status"] = "teacher_approved"
        payload["maturity_source"] = "teacher_reviewed"
    elif payload.get("status") == "rejected":
        payload["review_status"] = "rejected"
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


def _component_terms_for_duplicate(payload: dict) -> set[str]:
    terms = {normalize_key(payload.get("name") or "")}
    for field in ("inputs", "outputs", "preconditions", "dependencies", "cautions", "invalid_conditions"):
        for item in payload.get(field) or []:
            if isinstance(item, dict):
                terms.add(normalize_key(item.get("name") or item.get("label") or item.get("condition") or ""))
    return {term for term in terms if len(term) >= 4}


def _duplicate_candidates_for_component(course_id: str, payload: dict, exclude_component_id: str | None = None) -> list[dict]:
    candidate_terms = _component_terms_for_duplicate(payload)
    if not candidate_terms:
        return []
    session = _pg_session()
    try:
        rows = session.execute(
            sa_text(_select_components_sql("course_id = :course_id")),
            {"course_id": course_id},
        ).fetchall()
    finally:
        session.close()
    candidates = []
    for row in rows:
        existing = _row_to_out(row)
        if exclude_component_id and existing.id == exclude_component_id:
            continue
        existing_payload = _dump_model(existing)
        existing_terms = _component_terms_for_duplicate(existing_payload)
        overlap = candidate_terms & existing_terms
        if not overlap:
            continue
        reasons = [f"shared normalized term: {term}" for term in sorted(overlap)[:3]]
        if normalize_key(existing.component_type) == normalize_key(payload.get("component_type_text") or payload.get("component_type")):
            reasons.append(f"same component_type: {existing.component_type}")
        score = min(0.95, 0.45 + (len(overlap) / max(1, len(candidate_terms | existing_terms))) + (0.15 if len(reasons) > len(overlap) else 0))
        if score >= 0.5:
            candidates.append({
                "possible_duplicate_of": existing.id,
                "score": round(score, 2),
                "reasons": reasons,
                "review_status": "teacher_review_required",
            })
    return sorted(candidates, key=lambda item: item["score"], reverse=True)[:5]


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
    component_type = str(raw.get("component_type") or "theory").strip()
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
        "internal_flow": _normalize_internal_flow(raw.get("internal_flow")),
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
    payload["duplicate_candidates"] = payload.get("duplicate_candidates") or _duplicate_candidates_for_component(course_id, payload)
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("""
                INSERT INTO theory_components (
                    course_id, primary_chunk_id, name, component_type, summary, status,
                    source_chunks, inputs, outputs, preconditions, constraints,
                    invalid_conditions, dependencies, blackbox_policy, validation_warnings,
                    teacher_notes, source_scope, evidence_claims, maturity_level, maturity_source,
                    review_status, cautions, connectors, component_type_text, internal_flow, duplicate_candidates, created_by
                )
                VALUES (
                    :course_id, CAST(:primary_chunk_id AS uuid), :name, :component_type, :summary, :status,
                    CAST(:source_chunks AS jsonb), CAST(:inputs AS jsonb), CAST(:outputs AS jsonb),
                    CAST(:preconditions AS jsonb), CAST(:constraints AS jsonb),
                    CAST(:invalid_conditions AS jsonb), CAST(:dependencies AS jsonb),
                    CAST(:blackbox_policy AS jsonb), CAST(:validation_warnings AS jsonb),
                    :teacher_notes, CAST(:source_scope AS jsonb), CAST(:evidence_claims AS jsonb),
                    :maturity_level, :maturity_source, :review_status, CAST(:cautions AS jsonb),
                    CAST(:connectors AS jsonb), :component_type_text, CAST(:internal_flow AS jsonb),
                    CAST(:duplicate_candidates AS jsonb),
                    CAST(:created_by AS uuid)
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
                "component_type_text": payload.get("component_type_text") or payload.get("component_type") or "",
                "internal_flow": json.dumps(payload.get("internal_flow") or [], ensure_ascii=False),
                "duplicate_candidates": json.dumps(payload.get("duplicate_candidates") or [], ensure_ascii=False),
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
    existing_component = _get_component(component_id)
    payload["duplicate_candidates"] = payload.get("duplicate_candidates") or _duplicate_candidates_for_component(
        payload.get("course_id") or (existing_component.course_id if existing_component else ""),
        payload,
        exclude_component_id=component_id,
    )
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
                    component_type_text = :component_type_text,
                    internal_flow = CAST(:internal_flow AS jsonb),
                    duplicate_candidates = CAST(:duplicate_candidates AS jsonb),
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
                "component_type_text": payload.get("component_type_text") or payload.get("component_type") or "",
                "internal_flow": json.dumps(payload.get("internal_flow") or [], ensure_ascii=False),
                "duplicate_candidates": json.dumps(payload.get("duplicate_candidates") or [], ensure_ascii=False),
            },
        )
        session.commit()
        updated = _get_component(component_id)
        if existing_component and updated and existing_component.review_status != updated.review_status:
            _record_review_event("component", component_id, existing_component.review_status, updated.review_status, None)
            if updated.review_status == "rejected" or updated.status == "rejected":
                _propagate_rejected_component(component_id)
        return updated
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


def _document_id_for_chunk_id(chunk_id: str) -> str:
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("SELECT document_id FROM chunks WHERE id = CAST(:chunk_id AS uuid) LIMIT 1"),
            {"chunk_id": chunk_id},
        ).fetchone()
        return str(row[0]) if row and row[0] else ""
    finally:
        session.close()


def _domain_components_from_cartridge() -> list[TheoryComponentOut]:
    examples_dir = Path(__file__).resolve().parents[2] / "cartridges" / "particle_physics" / "examples"
    components: list[TheoryComponentOut] = []
    for path in examples_dir.glob("*domain_component.json"):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        def io_items(field: str) -> list[dict]:
            values = []
            for item in raw.get(field) or []:
                if not isinstance(item, dict):
                    continue
                label = item.get("label") or item.get("name") or item.get("condition") or ""
                values.append({
                    "label": label,
                    "name": label,
                    "type": item.get("type") or item.get("concept_type") or "Concept",
                    "concept_type": item.get("type") or item.get("concept_type") or "Concept",
                    "support_status": item.get("support_status") or "domain_inferred",
                    "evidence_claims": [],
                    "source_refs": [],
                    "needs_source": False,
                })
            return values
        def condition_items(field: str) -> list[dict]:
            values = []
            for item in raw.get(field) or []:
                if not isinstance(item, dict):
                    continue
                label = item.get("label") or item.get("condition") or item.get("component_id") or ""
                values.append({
                    "label": label,
                    "condition": label,
                    "description": item.get("description") or "",
                    "support_status": item.get("support_status") or "domain_inferred",
                    "evidence_claims": [],
                    "source_refs": [],
                    "needs_source": False,
                })
            return values
        components.append(TheoryComponentOut(**{
            "id": raw.get("component_id") or path.stem,
            "course_id": "domain",
            "name": raw.get("name") or path.stem,
            "component_type": raw.get("component_type") or "DomainConceptComponent",
            "domain": raw.get("domain") or "particle_physics",
            "origin": "domain",
            "summary": (raw.get("description") or {}).get("value") if isinstance(raw.get("description"), dict) else "",
            "status": "teacher_reviewed",
            "source_scope": {"level": "domain", "document_id": "domain_registry", "section_id": "domain"},
            "evidence_claims": [],
            "maturity_level": raw.get("maturity_level") or "canonical",
            "maturity_source": raw.get("maturity_source") or "curated_registry",
            "review_status": raw.get("review_status") or "teacher_approved",
            "inputs": io_items("inputs"),
            "outputs": io_items("outputs"),
            "preconditions": condition_items("preconditions"),
            "cautions": condition_items("cautions"),
            "constraints": condition_items("constraints"),
            "invalid_conditions": condition_items("cautions"),
            "dependencies": condition_items("dependencies"),
            "connectors": raw.get("connectors") if isinstance(raw.get("connectors"), dict) else {},
            "blackbox_policy": raw.get("blackbox_policy") if isinstance(raw.get("blackbox_policy"), dict) else {},
        }))
    return components


def _normalize_graph_term(value: Any) -> str:
    return normalize_key(value).replace("_", "")


def _graph_item_terms(items: list[Any]) -> dict[str, dict]:
    terms: dict[str, dict] = {}
    for item in items or []:
        raw = _dump_model(item) if hasattr(item, "model_dump") or hasattr(item, "dict") else item
        if not isinstance(raw, dict):
            continue
        label = raw.get("name") or raw.get("label") or raw.get("condition") or raw.get("description") or ""
        normalized = _normalize_graph_term(label)
        if len(normalized) < 4:
            continue
        terms[normalized] = {
            "label": label,
            "evidence_claims": raw.get("evidence_claims") or [],
            "support_status": raw.get("support_status") or "source_backed",
        }
    return terms


def _component_graph_vocab(component: TheoryComponentOut) -> dict[str, dict[str, dict]]:
    return {
        "name": {_normalize_graph_term(component.name): {"label": component.name, "evidence_claims": component.evidence_claims}},
        "inputs": _graph_item_terms(component.inputs),
        "outputs": _graph_item_terms(component.outputs),
        "preconditions": _graph_item_terms(component.preconditions),
        "dependencies": _graph_item_terms(component.dependencies),
        "cautions": _graph_item_terms(component.cautions + component.invalid_conditions),
    }


def _component_lookup_key(component: TheoryComponentOut) -> list[str]:
    terms = [_normalize_graph_term(component.id), _normalize_graph_term(component.name)]
    vocab = _component_graph_vocab(component)
    for group in ("outputs", "inputs", "preconditions", "dependencies", "cautions"):
        terms.extend(vocab[group].keys())
    return [term for term in terms if term]


def _add_graph_edge(edges: dict[tuple[str, str, str, str], dict], edge: dict) -> None:
    source = edge.get("source_component_id")
    target = edge.get("target_component_id")
    relation = edge.get("relation") or "RELATED_TO"
    edge_type = edge.get("edge_type") or "explicit_connector"
    if not source or not target or source == target or relation not in _GRAPH_RELATIONS:
        return
    key = (source, target, relation, edge_type)
    existing = edges.get(key)
    if existing:
        existing["confidence"] = max(float(existing.get("confidence") or 0), float(edge.get("confidence") or 0))
        existing.setdefault("evidence", {}).update(edge.get("evidence") or {})
        return
    edges[key] = {
        "source_component_id": source,
        "target_component_id": target,
        "relation": relation,
        "edge_type": edge_type,
        "confidence": float(edge.get("confidence") or 0.5),
        "support_status": edge.get("support_status") or "design_inferred",
        "review_status": edge.get("review_status") or "teacher_review_required",
        "evidence": edge.get("evidence") or {},
    }


def _iter_connector_values(connectors: dict, key: str) -> list[str]:
    values = connectors.get(key) if isinstance(connectors, dict) else []
    if isinstance(values, str):
        return [values]
    if isinstance(values, list):
        return [str(value) for value in values if str(value).strip()]
    return []


def _resolve_component_reference(reference: str, components: list[TheoryComponentOut]) -> TheoryComponentOut | None:
    term = _normalize_graph_term(reference)
    if not term:
        return None
    for component in components:
        if term in _component_lookup_key(component):
            return component
    return None


def _build_component_graph_payload(document_id: str, components: list[TheoryComponentOut]) -> dict:
    paper_components = list(components)
    components = paper_components + _domain_components_from_cartridge()
    nodes = [
        {
            "component_id": component.id,
            "label": component.name,
            "review_status": component.review_status,
            "display_order": idx,
            "origin": component.origin,
            "component_type": component.component_type,
        }
        for idx, component in enumerate(components)
    ]
    edges_by_key: dict[tuple[str, str, str, str], dict] = {}
    vocab = {component.id: _component_graph_vocab(component) for component in components}

    for component in components:
        for target_ref in _iter_connector_values(component.connectors, "can_output_to"):
            target = _resolve_component_reference(target_ref, components)
            if target:
                _add_graph_edge(edges_by_key, {
                    "source_component_id": component.id,
                    "target_component_id": target.id,
                    "relation": "PRODUCES_FOR",
                    "edge_type": "explicit_connector",
                    "confidence": 0.95,
                    "evidence": {"connector": "can_output_to", "target": target_ref, "source_claims": component.evidence_claims, "target_claims": target.evidence_claims},
                })
        for required_ref in _iter_connector_values(component.connectors, "requires_before_use"):
            required = _resolve_component_reference(required_ref, components)
            if required:
                _add_graph_edge(edges_by_key, {
                    "source_component_id": required.id,
                    "target_component_id": component.id,
                    "relation": "ENABLES",
                    "edge_type": "explicit_connector",
                    "confidence": 0.95,
                    "evidence": {"connector": "requires_before_use", "required": required_ref, "source_claims": required.evidence_claims, "target_claims": component.evidence_claims},
                })
        for conflict_ref in _iter_connector_values(component.connectors, "may_conflict_with"):
            target = _resolve_component_reference(conflict_ref, components)
            if target:
                _add_graph_edge(edges_by_key, {
                    "source_component_id": component.id,
                    "target_component_id": target.id,
                    "relation": "CONFLICTS_WITH",
                    "edge_type": "conflict_detected",
                    "confidence": 0.8,
                    "evidence": {"connector": "may_conflict_with", "target": conflict_ref},
                })

    for source in components:
        source_outputs = vocab[source.id]["outputs"]
        source_cautions = vocab[source.id]["cautions"]
        for target in components:
            if source.id == target.id:
                continue
            target_inputs = {**vocab[target.id]["inputs"], **vocab[target.id]["preconditions"]}
            for term, source_info in source_outputs.items():
                if term in target_inputs:
                    target_info = target_inputs[term]
                    is_diagnostic_target = "diagnostic" in (target.component_type or "").lower()
                    _add_graph_edge(edges_by_key, {
                        "source_component_id": source.id,
                        "target_component_id": target.id,
                        "relation": "CHECKED_BY" if is_diagnostic_target else "ENABLES",
                        "edge_type": "diagnostic_uses_output" if is_diagnostic_target else "input_output_match",
                        "confidence": 0.82 if not is_diagnostic_target else 0.78,
                        "evidence": {
                            "matched_output": source_info["label"],
                            "matched_input": target_info["label"],
                            "source_claims": source_info.get("evidence_claims") or source.evidence_claims,
                            "target_claims": target_info.get("evidence_claims") or target.evidence_claims,
                        },
                    })
            target_dependency_terms = vocab[target.id]["dependencies"]
            for term, dependency_info in target_dependency_terms.items():
                if term in source_outputs or term in vocab[source.id]["name"]:
                    _add_graph_edge(edges_by_key, {
                        "source_component_id": source.id,
                        "target_component_id": target.id,
                        "relation": "ENABLES",
                    "edge_type": "dependency_match",
                        "confidence": 0.78,
                        "evidence": {
                            "matched_dependency": dependency_info["label"],
                            "source_claims": source.evidence_claims,
                            "target_claims": dependency_info.get("evidence_claims") or target.evidence_claims,
                        },
                    })
            if target.origin == "paper" and source.origin == "domain":
                paper_need_terms = {**vocab[target.id]["inputs"], **vocab[target.id]["preconditions"], **vocab[target.id]["dependencies"]}
                domain_terms = {**vocab[source.id]["name"], **vocab[source.id]["outputs"], **vocab[source.id]["inputs"]}
                for term, paper_info in paper_need_terms.items():
                    if term in domain_terms:
                        _add_graph_edge(edges_by_key, {
                            "source_component_id": source.id,
                            "target_component_id": target.id,
                            "relation": "ENABLES",
                            "edge_type": "domain_prerequisite_match",
                            "confidence": 0.74,
                            "support_status": "domain_inferred",
                            "evidence": {
                                "matched_domain_component": source.name,
                                "matched_paper_precondition": paper_info["label"],
                                "target_claims": target.evidence_claims,
                            },
                        })
            source_type = (source.component_type or "").lower()
            if "uncertainty" in source_type or "correction" in source_type:
                for term, caution_info in source_cautions.items():
                    if term in vocab[target.id]["outputs"] or term in vocab[target.id]["cautions"]:
                        _add_graph_edge(edges_by_key, {
                            "source_component_id": source.id,
                            "target_component_id": target.id,
                            "relation": "QUALIFIES" if "correction" in source_type else "LIMITS",
                            "edge_type": "correction_qualifies_relation" if "correction" in source_type else "uncertainty_affects_prediction",
                            "confidence": 0.7,
                            "evidence": {"matched_caution": caution_info["label"], "source_claims": source.evidence_claims, "target_claims": target.evidence_claims},
                        })
            target_type = (target.component_type or "").lower()
            if "diagnostic" in target_type:
                for term, source_info in source_outputs.items():
                    if term in vocab[target.id]["inputs"]:
                        _add_graph_edge(edges_by_key, {
                            "source_component_id": source.id,
                            "target_component_id": target.id,
                            "relation": "CHECKED_BY",
                            "edge_type": "diagnostic_uses_output",
                            "confidence": 0.76,
                            "evidence": {"matched_output": source_info["label"], "source_claims": source.evidence_claims, "target_claims": target.evidence_claims},
                        })

    edges = list(edges_by_key.values())
    validation_results = []
    connected_ids = {edge["source_component_id"] for edge in edges} | {edge["target_component_id"] for edge in edges}
    for component in components:
        if component.id not in connected_ids and component.origin != "domain":
            validation_results.append({
                "severity": "warning",
                "code": "isolated_component",
                "message": "This component has no meaningful graph connection.",
                "component_id": component.id,
            })
        if component.review_status != "teacher_approved":
            validation_results.append({
                "severity": "info",
                "code": "unreviewed_component",
                "message": "This component is not teacher-approved.",
                "component_id": component.id,
            })
        if not component.evidence_claims and component.origin != "domain":
            validation_results.append({
                "severity": "warning",
                "code": "component_missing_evidence",
                "message": "根拠Claimが未設定です。",
                "component_id": component.id,
            })
        for missing in _iter_connector_values(component.connectors, "requires_before_use"):
            if not _resolve_component_reference(missing, components):
                validation_results.append({
                    "severity": "warning",
                    "code": "unresolved_dependency",
                    "message": "Required component or concept was not found in the graph.",
                    "component_id": component.id,
                    "missing": missing,
                })
    return {
        "graph_id": f"graph_{document_id}",
        "document_id": document_id,
        "scope": {"level": "paper"},
        "nodes": nodes,
        "edges": edges,
        "validation_results": validation_results,
    }


def _stored_component_graph(document_id: str) -> dict:
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("""
                SELECT graph_json, validation_results
                FROM theory_component_graphs
                WHERE document_id = :document_id
                ORDER BY updated_at DESC
                LIMIT 1
            """),
            {"document_id": document_id},
        ).fetchone()
    finally:
        session.close()
    if not row:
        return {}
    graph = _json_value(row[0], {})
    if isinstance(graph, dict) and "validation_results" not in graph:
        graph["validation_results"] = _json_value(row[1], [])
    return graph if isinstance(graph, dict) else {}


def _normalize_stored_component_graph(document_id: str, graph: dict, components: list[TheoryComponentOut]) -> dict:
    if not graph:
        return {}
    component_by_id = {component.id: component for component in components}
    normalized_nodes = []
    seen_nodes: set[str] = set()
    for idx, node in enumerate(graph.get("nodes") if isinstance(graph.get("nodes"), list) else []):
        if not isinstance(node, dict):
            continue
        component_id = str(node.get("component_id") or node.get("id") or "").strip()
        if not component_id or component_id in seen_nodes:
            continue
        component = component_by_id.get(component_id)
        normalized_nodes.append({
            "component_id": component_id,
            "label": component.name if component else str(node.get("label") or node.get("agent_component_id") or component_id),
            "review_status": component.review_status if component else str(node.get("review_status") or "teacher_review_required"),
            "display_order": int(node.get("display_order") or idx),
            "origin": component.origin if component else str(node.get("origin") or "paper"),
            "component_type": component.component_type if component else str(node.get("component_type") or node.get("type") or ""),
        })
        seen_nodes.add(component_id)
    normalized_edges = []
    relation_map = {
        "depends_on": "REQUIRES",
        "supports": "ENABLES",
        "corrects": "QUALIFIES",
        "requires": "REQUIRES",
        "produces": "PRODUCES_FOR",
        "related_to": "RELATED_TO",
        "conflicts_with": "CONFLICTS_WITH",
    }
    for edge in graph.get("edges") if isinstance(graph.get("edges"), list) else []:
        if not isinstance(edge, dict):
            continue
        source = str(edge.get("source_component_id") or edge.get("from") or "").strip()
        target = str(edge.get("target_component_id") or edge.get("to") or "").strip()
        if not source or not target:
            continue
        raw_relation = str(edge.get("relation") or edge.get("type") or "RELATED_TO").strip()
        relation = relation_map.get(raw_relation.lower(), raw_relation.upper())
        if relation not in _GRAPH_RELATIONS:
            relation = "RELATED_TO"
        normalized_edges.append({
            "source_component_id": source,
            "target_component_id": target,
            "relation": relation,
            "edge_type": str(edge.get("edge_type") or raw_relation or "stored_pipeline_edge"),
            "confidence": float(edge.get("confidence") or 0.8),
            "support_status": str(edge.get("support_status") or "source_inferred"),
            "review_status": str(edge.get("review_status") or "teacher_review_required"),
            "evidence": edge.get("evidence") if isinstance(edge.get("evidence"), dict) else {"reason": edge.get("reason") or ""},
        })
    if not normalized_nodes and not normalized_edges:
        return {}
    return {
        "graph_id": str(graph.get("graph_id") or f"graph_{document_id}"),
        "document_id": str(graph.get("document_id") or document_id),
        "scope": graph.get("scope") if isinstance(graph.get("scope"), dict) else {"level": "paper"},
        "nodes": normalized_nodes,
        "edges": normalized_edges,
        "validation_results": graph.get("validation_results") if isinstance(graph.get("validation_results"), list) else [],
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





def _claim_item(name: str, support_status: str, evidence_claims: list[str], description: str = "", concept_type: str = "Concept") -> dict:
    return {
        "label": name,
        "name": name,
        "type": concept_type,
        "concept_type": concept_type,
        "description": description,
        "support_status": support_status or "source_backed",
        "evidence_claims": evidence_claims,
        "source_refs": [],
        "needs_source": support_status in {"domain_inferred", "design_inferred"},
    }


def _condition_item(name: str, support_status: str, evidence_claims: list[str], description: str = "") -> dict:
    return {
        "label": name,
        "condition": name,
        "description": description,
        "support_status": support_status or "source_backed",
        "evidence_claims": evidence_claims,
        "source_refs": [],
        "needs_source": support_status in {"domain_inferred", "design_inferred"},
    }


def _normalize_internal_flow(value: Any) -> list[dict]:
    if not isinstance(value, list):
        return []
    normalized: list[dict] = []
    for item in value:
        if isinstance(item, dict):
            step = {
                "from": str(item.get("from") or item.get("source") or ""),
                "to": str(item.get("to") or item.get("target") or ""),
            }
            description = str(item.get("description") or item.get("step") or "").strip()
            if description:
                step["description"] = description
            if step["from"] or step["to"] or step.get("description"):
                normalized.append(step)
        elif isinstance(item, str):
            text = item.strip()
            if text:
                normalized.append({"from": "", "to": "", "description": text})
    return normalized


def _normalize_component_candidate(
    raw: dict,
    document_id: str,
    section_id: str,
    section_chunks: list[dict],
    claims: list[ClaimOut],
    strict: bool = True,
) -> dict | None:
    if not isinstance(raw, dict):
        raise ValueError("component item must be an object")
    name = str(raw.get("name") or "").strip()
    if not name:
        raise ValueError("component missing required fields: name")
    if _PROHIBITED_COMPONENT_NAME_RE.search(name):
        raise ValueError(f"prohibited component name: {name}")
    evidence_claims_raw = [str(cid) for cid in raw.get("evidence_claims", []) if str(cid)]
    valid_claim_ids = {claim.claim_id for claim in claims}
    unknown_evidence = set(evidence_claims_raw) - valid_claim_ids
    if unknown_evidence:
        msg = f"component '{name}' references unknown evidence_claims: {sorted(unknown_evidence)[:10]}"
        if strict:
            raise ValueError(msg)
        logger.warning("Component validation: %s (filtering unknown claims)", msg)
    evidence_claims = [cid for cid in evidence_claims_raw if cid in valid_claim_ids]
    if not evidence_claims and not strict:
        evidence_claims = [claim.claim_id for claim in claims[:3]]
    if not evidence_claims:
        raise ValueError("component missing required fields: evidence_claims")
    source_scope = {
        "level": "section" if _section_title_for_chunk(section_chunks[0]) else "chunk_group",
        "document_id": document_id,
        "section_id": section_id,
        "section_title": _section_title_for_chunk(section_chunks[0]) if section_chunks else "",
        "chunks": [c["id"] for c in section_chunks],
        "pages": sorted({p for c in section_chunks for p in (c.get("page_start"), c.get("page_end")) if p is not None}),
        "equations": [str(eq) for eq in raw.get("equations", []) if str(eq)] if isinstance(raw.get("equations"), list) else [],
        "claims": evidence_claims,
    }

    def normalize_items(field: str) -> list[dict]:
        items = raw.get(field) if isinstance(raw.get(field), list) else []
        normalized = []
        for item in items[:8]:
            if not isinstance(item, dict):
                continue
            item_claims_raw = [str(cid) for cid in item.get("evidence_claims", []) if str(cid)]
            unknown_item = set(item_claims_raw) - valid_claim_ids
            if unknown_item:
                logger.warning(
                    "Component validation: item '%s' in field '%s' references unknown evidence_claims: %s",
                    str(item.get("name") or "")[:60],
                    field,
                    sorted(unknown_item)[:5],
                )
            item_claims = [cid for cid in item_claims_raw if cid in valid_claim_ids]
            name_value = str(item.get("name") or item.get("label") or item.get("condition") or "").strip()
            if not name_value:
                continue
            support = str(item.get("support_status") or "source_backed")
            if support not in _SUPPORT_STATUS_VALUES:
                raise ValueError(f"invalid support_status: {support}")
            # description is optional — empty string is accepted (#198)
            description = str(item.get("description") or "")
            if field in ("preconditions", "cautions"):
                normalized.append(_condition_item(name_value, support, item_claims or evidence_claims[:1], description))
            else:
                normalized.append(_claim_item(
                    name_value,
                    support,
                    item_claims or evidence_claims[:1],
                    description,
                    str(item.get("concept_type") or item.get("type") or "Concept"),
                ))
        return normalized

    inputs = normalize_items("inputs")
    outputs = normalize_items("outputs")
    preconditions = normalize_items("preconditions")
    cautions = normalize_items("cautions")
    dependencies = normalize_items("dependencies")
    internal_flow = _normalize_internal_flow(raw.get("internal_flow"))

    component_type = str(raw.get("component_type") or "").strip()
    if not component_type:
        raise ValueError("component missing required fields: component_type")

    # Type-specific meaningful field check (#197)
    required_any = _MEANINGFUL_REQUIREMENTS_BY_TYPE.get(
        component_type,
        ["inputs", "outputs", "preconditions", "cautions", "dependencies"],
    )
    field_values: dict[str, list] = {
        "inputs": inputs,
        "outputs": outputs,
        "preconditions": preconditions,
        "cautions": cautions,
        "dependencies": dependencies,
        "internal_flow": internal_flow,
    }
    if not any(field_values.get(f) for f in required_any):
        raise ValueError(
            f"component type {component_type!r} has none of the required fields: {required_any!r}"
        )

    maturity_level = str(raw.get("maturity_level") or "").strip()
    if not maturity_level:
        raise ValueError("component missing required fields: maturity_level")
    maturity_source = str(raw.get("maturity_source") or "llm_proposed").strip()
    review_status = str(raw.get("review_status") or "teacher_review_required").strip()
    if review_status == "teacher_approved":
        raise ValueError("LLM-created components cannot be teacher_approved")
    return {
        "name": name,
        "component_type": component_type,
        "summary": str(raw.get("summary") or ""),
        "status": "candidate",
        "source_scope": source_scope,
        "evidence_claims": evidence_claims,
        "maturity_level": maturity_level,
        "maturity_source": maturity_source,
        "review_status": review_status or "teacher_review_required",
        "source_chunks": [_source_ref_for_chunk(c) for c in section_chunks],
        "inputs": inputs,
        "outputs": outputs,
        "preconditions": preconditions,
        "cautions": cautions,
        "constraints": [],
        "invalid_conditions": cautions,
        "dependencies": dependencies,
        "internal_flow": internal_flow,
        "connectors": raw.get("connectors") if isinstance(raw.get("connectors"), dict) else {
            "requires_before_use": [], "can_accept": [], "can_output_to": [], "may_conflict_with": [],
        },
        "blackbox_policy": {
            "default_level": "summary",
            "expand_if_unlearned": True,
            "requires_source_display": True,
            "io_summary": str(raw.get("io_summary") or ""),
        },
    }




@router.get("/documents/{document_id}/chunks/{chunk_id}/claims", response_model=list[ClaimOut])
def list_chunk_claims(
    document_id: str,
    chunk_id: str,
    current_user: dict = Depends(_require_teacher),
) -> list[ClaimOut]:
    _ensure_document_viewable(document_id, current_user)
    claims = _claim_rows_for_chunk(chunk_id)
    if claims:
        return claims
    return _claim_rows_for_document(document_id)


@router.get("/documents/{document_id}/structure")
def get_document_structure(
    document_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    chunks = _ensure_document_viewable(document_id, current_user)
    return {"document_id": document_id, "document_structure": build_document_structure(chunks)}


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
            sa_text("SELECT document_id, review_status FROM theory_claims WHERE id = CAST(:claim_id AS uuid)"),
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
                    equation = CAST(:equation AS jsonb),
                    support_status = :support_status,
                    evidence_text = :evidence_text,
                    review_status = :review_status,
                    updated_at = now()
                WHERE id = CAST(:claim_id AS uuid)
                RETURNING id, document_id, chunk_id, source_scope, claim_type, text,
                          normalized_text, concepts, equation, support_status, evidence_text,
                          review_status, created_by, created_at, updated_at
            """),
            {
                "claim_id": claim_id,
                "source_scope": json.dumps(payload.get("source_scope") or {}, ensure_ascii=False),
                "claim_type": payload.get("claim_type") or "diagnostic_claim",
                "text": payload.get("text") or "",
                "normalized_text": payload.get("normalized_text") or payload.get("text") or "",
                "concepts": json.dumps(normalize_concepts(payload.get("concepts") or []), ensure_ascii=False),
                "equation": json.dumps(payload.get("equation") or {}, ensure_ascii=False),
                "support_status": payload.get("support_status") or "source_backed",
                "evidence_text": payload.get("evidence_text") or "",
                "review_status": payload.get("review_status") or "teacher_review_required",
            },
        ).fetchone()
        session.commit()
        updated = _row_to_claim(row)
        if existing[1] != updated.review_status:
            _record_review_event("claim", claim_id, existing[1], updated.review_status, current_user.get("id"))
            if updated.review_status == "rejected":
                _propagate_rejected_claim(claim_id)
        return updated
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
        components = [_row_to_out(row) for row in rows]
        return components if components else _components_for_document(document_id)
    finally:
        session.close()



@router.get("/documents/{document_id}/component-graph", response_model=ComponentGraphResponse)
def get_component_graph(
    document_id: str,
    current_user: dict = Depends(_require_teacher),
) -> ComponentGraphResponse:
    _ensure_document_viewable(document_id, current_user)
    components = _components_for_document(document_id)
    stored_graph = _normalize_stored_component_graph(document_id, _stored_component_graph(document_id), components)
    if stored_graph:
        return ComponentGraphResponse(**stored_graph)
    return ComponentGraphResponse(**_build_component_graph_payload(document_id, components))



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
        components = [_row_to_out(row) for row in rows]
        if components:
            return components
        if chunk_id:
            document_id = _document_id_for_chunk_id(chunk_id)
            if document_id:
                return _components_for_document(document_id)
        course_data = get_viewable_course_data(current_user["id"], course_id) or _system_admin_course_data(course_id) or {}
        document_ids = []
        material_ids = []
        for source in course_data.get("sources", []) if isinstance(course_data, dict) else []:
            if isinstance(source, dict) and source.get("document_id"):
                document_ids.append(str(source["document_id"]))
            if isinstance(source, dict) and source.get("material_id"):
                material_ids.append(str(source["material_id"]))
        if material_ids:
            mid_placeholders = ", ".join(f":mid_{i}" for i in range(len(material_ids)))
            mid_rows = session.execute(
                sa_text(f"""
                    SELECT DISTINCT document_id
                    FROM chunks
                    WHERE material_id IN ({mid_placeholders}) AND document_id IS NOT NULL
                """),
                {f"mid_{i}": material_id for i, material_id in enumerate(material_ids)},
            ).fetchall()
            document_ids.extend(str(row[0]) for row in mid_rows if row[0])
        document_ids = list(dict.fromkeys(document_ids))
        if document_ids:
            placeholders = ", ".join(f":doc_{i}" for i in range(len(document_ids)))
            doc_params = {f"doc_{i}": doc_id for i, doc_id in enumerate(document_ids)}
            doc_rows = session.execute(
                sa_text(_select_components_sql(f"source_scope->>'document_id' IN ({placeholders})")),
                doc_params,
            ).fetchall()
            return [_row_to_out(row) for row in doc_rows]
        return []
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
    payload["review_status"] = "rejected"
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
