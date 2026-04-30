"""Lecture Studio theory component APIs."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
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
from core.cartridges import load_cartridge
from core.llm import generate_text, get_llm_params
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
    "internal_flow",
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

_LEGACY_COMPONENT_TYPES = {"theory", "concept", "law", "mechanism", "operator", "observation"}
_CLAIM_EXTRACTABLE_ROLES = {"body", "abstract", "equation_block", "caption"}
_CLAIM_SKIP_ROLES = {"front_matter", "metadata", "references", "acknowledgement"}


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
        "component_type": row[26] or row[4],
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
        "internal_flow": _json_value(row[27], []),
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
               component_type_text, internal_flow
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
    heading = _detect_section_heading(chunk.get("raw_text") or chunk.get("text") or "")
    if heading:
        sec_num = re.sub(r"[^0-9A-Za-z]+", "_", heading["number"]).strip("_").lower()
        return f"{document_id}:sec_{sec_num}"
    index = int(chunk.get("chunk_index") or 0)
    return f"{document_id}:chunk_group_{max(1, (index // 4) + 1)}"


def _section_title_for_chunk(chunk: dict) -> str:
    heading = _detect_section_heading(chunk.get("raw_text") or chunk.get("text") or "")
    if heading:
        return heading["title"]
    return ""


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


def _detect_section_heading(text: str) -> dict[str, str] | None:
    for line in (text or "").splitlines()[:12]:
        line = line.strip()
        match = re.match(r"^((?:[1-9]\d*)(?:\.[1-9]\d*)*)\s+(.{3,120})$", line)
        if not match:
            continue
        title = match.group(2).strip()
        lower = title.lower()
        if lower.startswith(("arxiv", "jhep", "doi", "received", "accepted", "published")):
            continue
        return {"number": match.group(1), "title": f"{match.group(1)} {title}"}
    return None


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


def _normalize_claim_payload(raw: dict, chunk: dict) -> dict | None:
    if not isinstance(raw, dict):
        return None
    claim_type = str(raw.get("claim_type") or "diagnostic_claim").strip()
    if claim_type not in _CLAIM_TYPES:
        claim_type = "diagnostic_claim"
    text = str(raw.get("text") or raw.get("normalized_text") or "").strip()
    if _is_low_value_claim_text(text):
        return None
    evidence = str(raw.get("evidence_text") or "").strip()
    if not evidence:
        evidence = text[:360]
    concepts = raw.get("concepts") if isinstance(raw.get("concepts"), list) else []
    normalized_concepts = []
    for concept in concepts[:8]:
        if not isinstance(concept, dict):
            continue
        name = str(concept.get("name") or "").strip()
        if not name:
            continue
        normalized_concepts.append({
            "name": name,
            "concept_type": str(concept.get("concept_type") or "Concept").strip() or "Concept",
        })
    if not normalized_concepts:
        normalized_concepts = _fallback_concepts(text)
    scope = _source_scope_for_chunk(chunk, "chunk")
    if isinstance(raw.get("source_scope"), dict):
        scope.update({k: v for k, v in raw["source_scope"].items() if v not in (None, "", [])})
    return {
        "claim_type": claim_type,
        "text": text[:1200],
        "normalized_text": str(raw.get("normalized_text") or text).strip()[:1200],
        "concepts": normalized_concepts,
        "support_status": str(raw.get("support_status") or "source_backed").strip() or "source_backed",
        "evidence_text": evidence[:500],
        "review_status": "teacher_review_required",
        "source_scope": scope,
    }


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


def _semantic_claims_with_llm(chunk: dict) -> list[dict]:
    text = (chunk.get("raw_text") or chunk.get("text") or "").strip()
    cartridge = load_cartridge()
    params = get_llm_params("fast")
    prompt = f"""このチャンクから理論コンポーネントを直接作らない。
まず、論理形成に使えるClaimだけを抽出する。

Claimは単なる文ではなく、理論・定義・式・仮定・近似・補正・不確実性・制限・観測量に関する意味ある主張である。
出版情報、著者情報、所属、ジャーナル情報、受理日、ページ番号、参考文献、謝辞、単なる見出しはClaimにしない。

claim_type候補:
{", ".join(sorted(_CLAIM_TYPES))}

concept_type候補:
{", ".join(cartridge.ontology.concept_type_ids)}

support_status候補:
{", ".join(cartridge.support_status_ids)}

チャンク情報:
document_id={chunk.get("document_id") or chunk.get("material_id") or ""}
chunk_id={chunk.get("id")}
page={chunk.get("page_start")}
section_id={_section_id_for_chunk(chunk)}
section_title={_section_title_for_chunk(chunk)}

本文:
{text[:6000]}

JSONのみ:
{{
  "claims": [
    {{
      "claim_type": "relation",
      "text": "logical claim",
      "normalized_text": "self-contained logical claim",
      "concepts": [{{"name": "concept", "concept_type": "Relation"}}],
      "support_status": "source_backed",
      "evidence_text": "short source quote"
    }}
  ]
}}
"""
    raw = generate_text(
        [{"role": "system", "content": "You extract source-grounded scientific claims as strict JSON."},
         {"role": "user", "content": prompt}],
        model=params.get("model"),
        reasoning_effort=params.get("reasoning_effort"),
        max_tokens=1800,
    )
    parsed = _parse_json_object(raw)
    items = parsed.get("claims") if isinstance(parsed.get("claims"), list) else []
    return [payload for item in items if (payload := _normalize_claim_payload(item, chunk))]


def _fallback_semantic_claims(chunk: dict) -> list[dict]:
    text = (chunk.get("raw_text") or chunk.get("text") or "").strip()
    if not text:
        return []
    pieces = [p.strip() for p in re.split(r"(?<=[。.!?])\s+|\n+", text) if p.strip()]
    candidates: list[dict] = []
    useful_terms = re.compile(
        r"sum rule|relation|derive|assum|limit|approx|correction|uncertain|form factor|observable|decay|rate|operator|coefficient|hamiltonian|constraint|prediction|result",
        re.IGNORECASE,
    )
    for sentence in pieces[:20]:
        if not useful_terms.search(sentence) or _is_low_value_claim_text(sentence):
            continue
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
        payload = _normalize_claim_payload({
            "claim_type": claim_type,
            "text": sentence[:1200],
            "normalized_text": sentence[:1200],
            "concepts": _fallback_concepts(sentence),
            "support_status": "source_backed",
            "evidence_text": sentence[:360],
            "review_status": "teacher_review_required",
            "source_scope": _source_scope_for_chunk(chunk, "chunk"),
        }, chunk)
        if payload:
            candidates.append(payload)
    return candidates[:8]


def _extract_claim_candidates(chunk: dict) -> list[dict]:
    role = _classify_chunk_role(chunk)
    if role in _CLAIM_SKIP_ROLES:
        return []
    try:
        claims = _semantic_claims_with_llm(chunk)
    except Exception:
        logger.warning("LLM claim extraction failed; using semantic fallback", exc_info=True)
        claims = _fallback_semantic_claims(chunk)
    return claims


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
                "created_by": user_id or None,
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
        "internal_flow": raw.get("internal_flow") if isinstance(raw.get("internal_flow"), list) else [],
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
                    review_status, cautions, connectors, component_type_text, internal_flow, created_by
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
                    component_type_text = :component_type_text,
                    internal_flow = CAST(:internal_flow AS jsonb),
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


def _in_params(prefix: str, values: list[str], cast: str | None = None) -> tuple[str, dict[str, str]]:
    params: dict[str, str] = {}
    placeholders = []
    for idx, value in enumerate(values):
        key = f"{prefix}_{idx}"
        params[key] = value
        token = f":{key}"
        placeholders.append(f"CAST({token} AS {cast})" if cast else token)
    return ", ".join(placeholders), params


def _delete_claims_for_chunks(chunk_ids: list[str]) -> None:
    if not chunk_ids:
        return
    placeholders, params = _in_params("chunk_id", chunk_ids, "uuid")
    session = _pg_session()
    try:
        session.execute(
            sa_text(f"""
                DELETE FROM theory_claims
                WHERE chunk_id IN ({placeholders})
            """),
            params,
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _delete_components_for_sections(course_id: str, section_ids: list[str]) -> None:
    if not section_ids:
        return
    placeholders, params = _in_params("section_id", section_ids)
    session = _pg_session()
    try:
        session.execute(
            sa_text(f"""
                DELETE FROM theory_components
                WHERE course_id = :course_id
                  AND source_scope->>'section_id' IN ({placeholders})
            """),
            {"course_id": course_id, **params},
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _analysis_status(course_id: str, course_data: dict) -> dict:
    chunks = _course_chunks(course_data)
    claim_target_chunks = [chunk for chunk in chunks if _classify_chunk_role(chunk) not in _CLAIM_SKIP_ROLES]
    chunk_ids = [chunk["id"] for chunk in claim_target_chunks]
    grouped = _group_sections(chunks)
    section_ids = [section_id for (_document_id, section_id) in grouped.keys()]
    document_ids = sorted({c.get("document_id") or c.get("material_id") for c in chunks if c.get("document_id") or c.get("material_id")})
    session = _pg_session()
    try:
        claim_chunks = 0
        component_sections = 0
        graph_documents = 0
        if chunk_ids:
            placeholders, params = _in_params("chunk_id", chunk_ids, "uuid")
            claim_chunks = int(session.execute(
                sa_text(f"SELECT COUNT(DISTINCT chunk_id) FROM theory_claims WHERE chunk_id IN ({placeholders})"),
                params,
            ).scalar() or 0)
        if section_ids:
            placeholders, params = _in_params("section_id", section_ids)
            component_sections = int(session.execute(
                sa_text(f"""
                    SELECT COUNT(DISTINCT source_scope->>'section_id')
                    FROM theory_components
                    WHERE course_id = :course_id
                      AND source_scope->>'section_id' IN ({placeholders})
                """),
                {"course_id": course_id, **params},
            ).scalar() or 0)
        if document_ids:
            placeholders, params = _in_params("document_id", document_ids)
            graph_documents = int(session.execute(
                sa_text(f"""
                    SELECT COUNT(DISTINCT document_id)
                    FROM theory_component_graphs
                    WHERE course_id = :course_id
                      AND document_id IN ({placeholders})
                """),
                {"course_id": course_id, **params},
            ).scalar() or 0)
    finally:
        session.close()
    return {
        "course_id": course_id,
        "claims": {
            "total": len(chunk_ids),
            "completed": claim_chunks,
            "complete": bool(chunk_ids) and claim_chunks >= len(chunk_ids),
        },
        "components": {
            "total": len(section_ids),
            "completed": component_sections,
            "complete": bool(section_ids) and component_sections >= len(section_ids),
        },
        "graph": {
            "total": len(document_ids),
            "completed": graph_documents,
            "complete": bool(document_ids) and graph_documents >= len(document_ids),
        },
    }


def _run_claim_extraction(course_id: str, course_data: dict, user_id: str | None, task_id: str | None = None, force: bool = False) -> dict:
    chunks = _course_chunks(course_data)
    total = len(chunks)
    created = 0
    skipped = 0
    if force:
        _delete_claims_for_chunks([chunk["id"] for chunk in chunks])
    if task_id:
        update_background_task(task_id, "processing", result_data={
            "course_id": course_id, "total_chunks": total, "generated": 0,
            "skipped": 0, "progress": 0, "stage": "claims",
            "force": force,
        })
    for idx, chunk in enumerate(chunks):
        document_id = chunk.get("document_id") or chunk.get("material_id") or ""
        if not document_id:
            skipped += 1
        elif _claim_rows_for_chunk(chunk["id"]):
            skipped += 1
        else:
            payloads = _extract_claim_candidates(chunk)
            if not payloads:
                skipped += 1
            for payload in payloads:
                _insert_claim(document_id, chunk["id"], payload, user_id or "")
                created += 1
        if task_id:
            update_background_task(task_id, "processing", result_data={
                "course_id": course_id, "total_chunks": total, "generated": created,
                "skipped": skipped, "progress": int((idx + 1) * 100 / total) if total else 100,
                "stage": "claims", "force": force,
            })
    return {"total_chunks": total, "generated": created, "skipped": skipped, "progress": 100, "force": force}


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


def _normalize_component_candidate(raw: dict, document_id: str, section_id: str, section_chunks: list[dict], claims: list[ClaimOut]) -> dict | None:
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()
    if not name or re.search(r"component for (page|section|chunk_group)", name, re.IGNORECASE):
        return None
    evidence_claims = [str(cid) for cid in raw.get("evidence_claims", []) if str(cid)]
    valid_claim_ids = {claim.claim_id for claim in claims}
    evidence_claims = [cid for cid in evidence_claims if cid in valid_claim_ids]
    if not evidence_claims:
        evidence_claims = [claim.claim_id for claim in claims[:3]]
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
            item_claims = [str(cid) for cid in item.get("evidence_claims", []) if str(cid) in valid_claim_ids]
            name_value = str(item.get("name") or item.get("label") or item.get("condition") or "").strip()
            if not name_value:
                continue
            support = str(item.get("support_status") or "source_backed")
            if field in ("preconditions", "cautions"):
                normalized.append(_condition_item(name_value, support, item_claims or evidence_claims[:1], str(item.get("description") or "")))
            else:
                normalized.append(_claim_item(
                    name_value,
                    support,
                    item_claims or evidence_claims[:1],
                    str(item.get("description") or ""),
                    str(item.get("concept_type") or item.get("type") or "Concept"),
                ))
        return normalized

    return {
        "name": name,
        "component_type": str(raw.get("component_type") or "PaperClaimComponent").strip(),
        "summary": str(raw.get("summary") or ""),
        "status": "candidate",
        "source_scope": source_scope,
        "evidence_claims": evidence_claims,
        "maturity_level": str(raw.get("maturity_level") or "paper_claim"),
        "maturity_source": "llm_proposed",
        "review_status": "teacher_review_required",
        "source_chunks": [_source_ref_for_chunk(c) for c in section_chunks],
        "inputs": normalize_items("inputs"),
        "outputs": normalize_items("outputs"),
        "preconditions": normalize_items("preconditions"),
        "cautions": normalize_items("cautions"),
        "constraints": [],
        "invalid_conditions": normalize_items("cautions"),
        "dependencies": normalize_items("dependencies"),
        "internal_flow": raw.get("internal_flow") if isinstance(raw.get("internal_flow"), list) else [],
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


def _semantic_components_with_llm(document_id: str, section_id: str, section_chunks: list[dict], claims: list[ClaimOut]) -> list[dict]:
    cartridge = load_cartridge()
    params = get_llm_params("fast")
    claim_lines = "\n".join(
        f"- {claim.claim_id} [{claim.claim_type}] {claim.normalized_text or claim.text}"
        for claim in claims
    )
    section_title = _section_title_for_chunk(section_chunks[0]) if section_chunks else ""
    prompt = f"""Claim群から意味のある理論Component候補を組み立てる。
1節から必ず1つのComponentを作る必要はない。0個、1個、複数個のいずれでもよい。
Component名は内容に基づいて付ける。"Component for page N" や "Component for section" は禁止。
内容ベースで名前を付けられない場合は components=[] を返す。
Claimにない情報を補完する場合は support_status を domain_inferred または design_inferred にする。
teacher_approved は付けてはいけない。

component_type候補:
{", ".join(cartridge.component_type_ids)}

maturity_level候補:
{", ".join(cartridge.maturity_level_ids)}

section_id={section_id}
section_title={section_title}
document_id={document_id}

Claims:
{claim_lines}

JSONのみ:
{{
  "components": [
    {{
      "name": "Total Decay Rate Sum Rule Component",
      "component_type": "PaperRelationComponent",
      "summary": "what this component does",
      "maturity_level": "paper_claim",
      "evidence_claims": ["claim id"],
      "inputs": [{{"name": "input", "concept_type": "Relation", "support_status": "source_backed", "evidence_claims": ["claim id"]}}],
      "outputs": [],
      "preconditions": [],
      "cautions": [],
      "dependencies": [],
      "internal_flow": [],
      "connectors": {{"requires_before_use": [], "can_accept": [], "can_output_to": [], "may_conflict_with": []}}
    }}
  ]
}}
"""
    raw = generate_text(
        [{"role": "system", "content": "You assemble source-grounded theory components as strict JSON."},
         {"role": "user", "content": prompt}],
        model=params.get("model"),
        reasoning_effort=params.get("reasoning_effort"),
        max_tokens=2200,
    )
    parsed = _parse_json_object(raw)
    items = parsed.get("components") if isinstance(parsed.get("components"), list) else []
    return [
        payload for item in items
        if (payload := _normalize_component_candidate(item, document_id, section_id, section_chunks, claims))
    ]


def _fallback_component_candidates(document_id: str, section_id: str, section_chunks: list[dict], claims: list[ClaimOut]) -> list[dict]:
    if not claims:
        return []
    relation_claims = [c for c in claims if c.claim_type in {"relation", "equation", "result", "derivation_step"}]
    assumption_claims = [c for c in claims if c.claim_type in {"assumption", "approximation"}]
    caution_claims = [c for c in claims if c.claim_type in {"uncertainty", "limitation", "correction"}]
    if not relation_claims and not assumption_claims and not caution_claims:
        return []
    concept_counts: dict[str, tuple[str, int]] = {}
    for claim in claims:
        for concept in claim.concepts:
            key = concept.name.lower()
            concept_counts[key] = (concept.name, concept_counts.get(key, (concept.name, 0))[1] + 1)
    title = sorted(concept_counts.values(), key=lambda x: x[1], reverse=True)[0][0] if concept_counts else "Theory Claim"
    if len(title) < 4:
        return []
    evidence = [c.claim_id for c in (relation_claims + assumption_claims + caution_claims)[:6]]
    raw = {
        "name": f"{title[:80]} Component",
        "component_type": "PaperRelationComponent" if relation_claims else "PaperClaimComponent",
        "summary": "Claim群から組み立てた内容ベースの理論コンポーネント候補です。",
        "maturity_level": "paper_claim",
        "evidence_claims": evidence,
        "inputs": [{"name": c.normalized_text or c.text, "concept_type": "Concept", "support_status": c.support_status, "evidence_claims": [c.claim_id]} for c in assumption_claims[:4]],
        "outputs": [{"name": c.normalized_text or c.text, "concept_type": "Relation", "support_status": c.support_status, "evidence_claims": [c.claim_id]} for c in relation_claims[:4]],
        "preconditions": [{"name": c.normalized_text or c.text, "support_status": c.support_status, "evidence_claims": [c.claim_id]} for c in assumption_claims[:4]],
        "cautions": [{"name": c.normalized_text or c.text, "support_status": c.support_status, "evidence_claims": [c.claim_id]} for c in caution_claims[:4]],
    }
    payload = _normalize_component_candidate(raw, document_id, section_id, section_chunks, claims)
    return [payload] if payload else []


def _assemble_section(
    course_id: str,
    document_id: str,
    section_id: str,
    section_chunks: list[dict],
    user_id: str | None,
    force: bool = False,
) -> list[TheoryComponentOut]:
    existing = list_section_components(document_id, section_id, {"role": ROLE_SYSTEM_ADMIN, "id": user_id or ""})
    if existing and not force:
        return existing
    claims = _claim_rows_for_section(document_id, section_id)
    if not claims:
        for chunk in section_chunks:
            for payload in _extract_claim_candidates(chunk):
                claims.append(_insert_claim(document_id, chunk["id"], payload, user_id or ""))
    if not claims:
        return []
    try:
        candidates = _semantic_components_with_llm(document_id, section_id, section_chunks, claims)
    except Exception:
        logger.warning("LLM component assembly failed; using semantic fallback", exc_info=True)
        candidates = _fallback_component_candidates(document_id, section_id, section_chunks, claims)
    saved: list[TheoryComponentOut] = []
    for candidate in candidates:
        payload = _normalize_payload(TheoryComponentUpsertRequest(**candidate))
        saved.append(_insert_component(course_id, section_chunks[0]["id"] if section_chunks else None, payload, user_id or ""))
    return saved


def _run_component_assembly(course_id: str, course_data: dict, user_id: str | None, task_id: str | None = None, force: bool = False) -> dict:
    grouped = _group_sections(_course_chunks(course_data))
    total = len(grouped)
    generated = 0
    skipped = 0
    if force:
        _delete_components_for_sections(course_id, [section_id for (_document_id, section_id) in grouped.keys()])
    if task_id:
        update_background_task(task_id, "processing", result_data={
            "course_id": course_id, "total_sections": total, "generated": 0,
            "skipped": 0, "progress": 0, "stage": "components", "force": force,
        })
    for idx, ((document_id, section_id), chunks) in enumerate(grouped.items()):
        components = _assemble_section(course_id, document_id, section_id, chunks, user_id, force=force)
        if components:
            generated += len(components)
        else:
            skipped += 1
        if task_id:
            update_background_task(task_id, "processing", result_data={
                "course_id": course_id, "total_sections": total, "generated": generated,
                "skipped": skipped, "progress": int((idx + 1) * 100 / total) if total else 100,
                "stage": "components", "force": force,
            })
    return {"total_sections": total, "generated": generated, "skipped": skipped, "progress": 100, "force": force}


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


def _worker_wrapper(task_id: str, fn, *args, **kwargs) -> None:
    try:
        result = fn(*args, task_id=task_id, **kwargs)
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
    chunk_role = _classify_chunk_role(chunk)
    if chunk_role in _CLAIM_SKIP_ROLES:
        return ClaimExtractResponse(
            chunk_id=chunk_id,
            claims=[],
            chunk_role=chunk_role,
            skip_reason=f"{chunk_role}_not_claim_extractable",
        )
    existing = _claim_rows_for_chunk(chunk_id)
    if existing:
        return ClaimExtractResponse(chunk_id=chunk_id, claims=existing, chunk_role=chunk_role)
    saved = [
        _insert_claim(document_id, chunk_id, payload, current_user["id"])
        for payload in _extract_claim_candidates(chunk)
    ]
    return ClaimExtractResponse(
        chunk_id=chunk_id,
        claims=saved,
        chunk_role=chunk_role,
        skip_reason="" if saved else "no_logical_claims_found",
    )


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
    section_chunks = [c for c in chunks if _section_id_for_chunk(c) == section_id]
    existing = list_section_components(document_id, section_id, current_user)
    if existing and not body.force:
        return ComponentAssembleResponse(section_id=section_id, components=existing)
    saved = _assemble_section(course_id, document_id, section_id, section_chunks, current_user["id"], force=body.force)
    return ComponentAssembleResponse(section_id=section_id, components=saved)


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
    body: dict | None = Body(default=None),
    current_user: dict = Depends(_require_teacher),
) -> dict:
    course_data = _editable_course_data(course_id, current_user)
    force = bool((body or {}).get("force"))
    task_id = str(uuid.uuid4())[:12]
    create_background_task(task_id, "claim_extraction", current_user["id"])
    threading.Thread(
        target=_worker_wrapper,
        args=(task_id, _run_claim_extraction, course_id, course_data, current_user["id"]),
        kwargs={"force": force},
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
    body: dict | None = Body(default=None),
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


@router.get("/courses/{course_id}/analysis-status")
def get_course_analysis_status(
    course_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    course_data = _editable_course_data(course_id, current_user)
    return _analysis_status(course_id, course_data)


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
