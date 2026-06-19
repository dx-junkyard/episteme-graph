"""Export Bundle API — DSL / Claim / Component / Graph を ZIP でエクスポートする。"""

from __future__ import annotations

import io
import json
import re
import zipfile
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import text as sa_text

from dependencies import _require_teacher
from core.postgres import get_session as _pg_session
from core.document_pipeline.persistence import resolve_artifact_runs
from routes.export_artifacts import (
    build_claims_export,
    build_component_graph_export,
    build_components_export,
    build_derivation_chains_export,
    build_document_boundary,
    build_document_completeness,
    build_system_operations_export,
    build_equation_candidates_export,
    build_equations_export,
    build_evidence_export,
    enrich_course_topics,
    get_artifacts,
)

router = APIRouter(tags=["Export"])


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class ExportBundleRequest(BaseModel):
    scope: str = Field("course", description="'course' or 'document'")
    include_source_snippets: bool = True
    include_review_fields: bool = True
    include_debug_data: bool = False
    include_llm_raw_outputs: bool = False
    include_ndjson: bool = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _json_bytes(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")


def _ndjson_bytes(items: list[dict]) -> bytes:
    return "\n".join(json.dumps(item, ensure_ascii=False) for item in items).encode("utf-8")


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


# Two-layer operation model (issues #393 / #394). A broad ``operation_family``
# (always one of CORE_OPERATION_FAMILIES) plus an optional ``operation_subtype``
# whose ``subtype_source`` identifies honest provenance. These are domain-neutral.
_UNKNOWN_OPERATION_FAMILY = "unknown_specific_operation"
_SUBTYPE_SOURCES = {"source_text", "equation_semantics", "cartridge", "llm", "unknown"}


def _core_operation_families() -> set[str]:
    """The stable generic operation-family set (issue #398). Empty if unavailable."""
    try:
        from episteme_graph.agents.theory_operations import CORE_OPERATION_FAMILIES
        return set(CORE_OPERATION_FAMILIES)
    except Exception:
        return set()


def _export_id(scope_type: str, scope_id: str) -> str:
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe_id = scope_id.replace("-", "")[:16]
    return f"export_{ts}_{scope_type}_{safe_id}"


def _zip_filename(scope_type: str, scope_id: str) -> str:
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
    safe_id = scope_id.replace("-", "")[:16]
    return f"episteme_export_{scope_type}_{safe_id}_{ts}.zip"


def _load_json_field(value: Any, default: Any) -> Any:
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


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------


def _load_course(session: Any, course_id: str) -> dict | None:
    row = session.execute(
        sa_text(
            "SELECT id, title, description, is_template, is_published, created_at, data"
            " FROM learning_courses WHERE id = :id"
        ),
        {"id": course_id},
    ).fetchone()
    if not row:
        return None
    course_data = _load_json_field(row[6], {})
    topics_raw = course_data.get("topics", [])
    chapters_raw = course_data.get("chapters", [])
    sources_raw = course_data.get("sources", [])

    topics_out = []
    for t in (topics_raw if isinstance(topics_raw, list) else []):
        if not isinstance(t, dict):
            continue
        topics_out.append({
            "title": t.get("title", ""),
            "description": t.get("description", ""),
            "prerequisites": t.get("prerequisites", []),
            "linked_component_ids": t.get("linked_component_ids", []),
        })

    chapters_out = []
    for ch in (chapters_raw if isinstance(chapters_raw, list) else []):
        if not isinstance(ch, dict):
            continue
        chapters_out.append({
            "title": ch.get("title", ""),
            "topics": ch.get("topics", []),
        })

    sources_out = []
    for s in (sources_raw if isinstance(sources_raw, list) else []):
        if not isinstance(s, dict):
            continue
        sources_out.append({
            "material_id": s.get("material_id", ""),
            "title": s.get("title", ""),
        })

    return {
        "course_id": str(row[0]),
        "title": row[1] or "",
        "description": row[2] or "",
        "is_template": bool(row[3]),
        "is_published": bool(row[4]),
        "created_at": row[5].isoformat() if row[5] else "",
        "topics": topics_out,
        "chapters": chapters_out,
        "sources": sources_out,
    }


def _load_document(session: Any, document_id: str) -> dict | None:
    row = session.execute(
        sa_text("SELECT id, title, status, created_at FROM documents WHERE id = :id"),
        {"id": document_id},
    ).fetchone()
    if not row:
        return None
    return {
        "document_id": str(row[0]),
        "title": row[1] or "",
        "status": row[2] or "",
        "created_at": row[3].isoformat() if row[3] else "",
    }


def _get_document_ids_for_course(session: Any, course_id: str) -> list[str]:
    """コースに紐づく全document_idを返す。"""
    row = session.execute(
        sa_text("SELECT data FROM learning_courses WHERE id = :course_id LIMIT 1"),
        {"course_id": course_id},
    ).fetchone()
    if not row or not row[0]:
        return []
    course_data = _load_json_field(row[0], {})
    sources = course_data.get("sources", [])
    material_ids = [s.get("material_id") for s in sources if isinstance(s, dict) and s.get("material_id")]
    if not material_ids:
        return []
    placeholders = ", ".join(f":mid_{i}" for i in range(len(material_ids)))
    params = {f"mid_{i}": mid for i, mid in enumerate(material_ids)}
    doc_rows = session.execute(
        sa_text(f"SELECT DISTINCT document_id::text FROM chunks WHERE material_id IN ({placeholders})"),
        params,
    ).fetchall()
    return [str(r[0]) for r in doc_rows if r[0]]


def _load_analysis_artifacts(session: Any, document_ids: list[str]) -> dict[str, dict]:
    """Load `_artifacts` payload for each document's **adopted (active) run** (#408).

    Returns: {document_id: {stage_name: artifact_dict, ...}, ...}.
    Prefers the document's active_analysis_run_id; falls back to the latest
    *completed* run for legacy documents. A latest running/failed (or rejected
    candidate) run never overrides the adopted artifacts. Defensive: never raises
    on malformed JSONB.
    """
    if not document_ids:
        return {}
    try:
        resolved = resolve_artifact_runs(session, document_ids)
    except Exception:
        return {}
    out: dict[str, dict] = {}
    for doc_id, info in resolved.items():
        artifacts = get_artifacts(info.get("stage_outputs"))
        if artifacts:
            out[doc_id] = artifacts
    return out


def _load_latest_run_ids(session: Any, document_ids: list[str]) -> dict[str, str]:
    """Map each document to its **adopted (active) run id** for export metadata (#383/#408).

    Uses the active run (artifact source-of-truth), falling back to the latest
    completed run — not the latest running run, so processing status and artifact
    provenance are never conflated.
    """
    if not document_ids:
        return {}
    try:
        resolved = resolve_artifact_runs(session, document_ids)
    except Exception:
        return {}
    return {doc_id: info["run_id"] for doc_id, info in resolved.items() if info.get("run_id")}


# Artifact-first stage → fallback DB source mapping (issue #383). When a stage
# artifact is missing for a document we fall back to the DB-persisted object and
# record which fallback source was used so the export metadata is honest.
_FALLBACK_SOURCE = {
    "claim_object_builder": "theory_claims",
    "component_assembly": "theory_components",
    "component_graph": "theory_component_graphs",
}


def _group_by_document(rows: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("document_id") or ""), []).append(row)
    return grouped


def _resolve_artifact_first_claims(
    artifacts_by_doc: dict[str, dict],
    document_ids: list[str],
    db_claims: list[dict],
    fallback_sources: list[dict],
) -> list[dict]:
    """Prefer claim_object_builder artifacts; fall back to DB theory_claims (#383)."""
    db_by_doc = _group_by_document(db_claims)
    out: list[dict] = []
    for doc_id in document_ids:
        artifact = (artifacts_by_doc.get(doc_id) or {}).get("claim_object_builder")
        if artifact:
            out.extend(build_claims_export(artifact, document_id=doc_id))
        else:
            doc_claims = db_by_doc.get(doc_id, [])
            out.extend(doc_claims)
            fallback_sources.append({
                "artifact": "claim_object_builder",
                "document_id": doc_id,
                "fallback_to": _FALLBACK_SOURCE["claim_object_builder"],
            })
    # Claims not bound to any requested document_id (legacy DB rows) are appended
    # so nothing is silently dropped relative to the prior DB-only behaviour.
    requested = set(document_ids)
    for doc_id, rows in db_by_doc.items():
        if doc_id not in requested and rows:
            out.extend(rows)
    return out


def _resolve_artifact_first_components(
    artifacts_by_doc: dict[str, dict],
    document_ids: list[str],
    db_components: list[dict],
    fallback_sources: list[dict],
) -> list[dict]:
    """Prefer component_assembly artifacts; fall back to DB theory_components (#383)."""
    db_by_doc = _group_by_document(db_components)
    out: list[dict] = []
    for doc_id in document_ids:
        artifact = (artifacts_by_doc.get(doc_id) or {}).get("component_assembly")
        if artifact:
            out.extend(build_components_export(artifact, document_id=doc_id))
        else:
            out.extend(db_by_doc.get(doc_id, []))
            fallback_sources.append({
                "artifact": "component_assembly",
                "document_id": doc_id,
                "fallback_to": _FALLBACK_SOURCE["component_assembly"],
            })
    requested = set(document_ids)
    for doc_id, rows in db_by_doc.items():
        if doc_id not in requested and rows:
            out.extend(rows)
    return out


def _resolve_artifact_first_graph(
    artifacts_by_doc: dict[str, dict],
    document_ids: list[str],
    components: list[dict],
    db_component_graph: dict,
    fallback_sources: list[dict],
    load_db_graph=None,
) -> dict:
    """Prefer component_graph artifacts (issue #383), splitting component vs
    operation graphs (issue #387).

    Resolution is per document: a document that carries a current-run
    component_graph artifact uses it; a document that does not falls back to its
    own DB-persisted graph, loaded via ``load_db_graph(doc_id)`` and merged in.
    This holds for both a *partial* fallback (some documents missing) and a
    *complete* fallback (every document missing), so no requested document's
    graph is silently omitted and the recorded ``fallback_sources`` provenance
    matches the real data carried (issues #390 / #400).

    ``db_component_graph`` is only used as a back-compat fallback when no
    ``load_db_graph`` loader is supplied and every document fell back.

    Returns ``{"component_graph", "operation_graph", "component_operation_links"}``.
    """
    known_component_ids = {str(c.get("component_id")) for c in components if c.get("component_id")}

    component_nodes: list[dict] = []
    component_edges: list[dict] = []
    operation_nodes: list[dict] = []
    operation_edges: list[dict] = []
    links: list[dict] = []

    def _merge(split: dict) -> None:
        component_nodes.extend(split["component_graph"]["nodes"])
        component_edges.extend(split["component_graph"]["edges"])
        operation_nodes.extend(split["operation_graph"]["nodes"])
        operation_edges.extend(split["operation_graph"]["edges"])
        links.extend(split["component_operation_links"])

    any_db_fallback = False
    for doc_id in document_ids:
        artifact = (artifacts_by_doc.get(doc_id) or {}).get("component_graph")
        if artifact:
            _merge(build_component_graph_export(
                artifact, document_id=doc_id, known_component_ids=known_component_ids
            ))
            continue
        # Fallback (issues #390 / #400): this document has no current-run
        # component_graph artifact. Record the fallback per document AND actually
        # load and merge THIS document's DB-persisted graph — both on a partial
        # fallback and when every document falls back — so no requested document's
        # graph is silently omitted and provenance matches the real data carried.
        any_db_fallback = True
        fallback_sources.append({
            "artifact": "component_graph",
            "document_id": doc_id,
            "fallback_to": _FALLBACK_SOURCE["component_graph"],
        })
        db_graph = load_db_graph(doc_id) if callable(load_db_graph) else None
        if isinstance(db_graph, dict) and (db_graph.get("nodes") or db_graph.get("edges")):
            _merge(build_component_graph_export(
                db_graph, document_id=doc_id, known_component_ids=known_component_ids
            ))

    # Back-compat: when no per-document loader is supplied but every document fell
    # back, split the course-wide ``db_component_graph`` argument so callers that
    # do not provide ``load_db_graph`` still export the DB graph (issue #387 split).
    if any_db_fallback and not callable(load_db_graph) and not component_nodes and not operation_nodes:
        return build_component_graph_export(
            db_component_graph, document_id="", known_component_ids=known_component_ids
        )
    return {
        "component_graph": {
            "graph_schema_version": "0.1.0",
            "nodes": component_nodes,
            "edges": component_edges,
        },
        "operation_graph": {
            "graph_schema_version": "0.1.0",
            "nodes": operation_nodes,
            "edges": operation_edges,
        },
        "component_operation_links": links,
    }


_COMPONENT_ASSEMBLY_DIAG_KEY_PREFIXES = (
    "fallback_reason",
    "original_failure_codes",
    "component_assembly_input_validation",
    "initial_llm_exception",
    "initial_llm_raw_output",
    "initial_validation_issues",
    "initial_error_codes",
    "initial_llm_output_component_count",
    "parsed_component_count",
    "cleanup_component_count",
    "enriched_component_count",
    "repair_attempt_",
)


def _build_component_assembly_debug(
    artifacts_by_doc: dict[str, dict],
    document_ids: list[str],
    include_llm_raw: bool = False,
) -> list[dict]:
    """component_assembly artifact の diagnostics を debug export 用に整形する (#347)。

    fallback 原因調査に必要な issue code / count / validation_issues を含める。
    raw LLM テキストはサイズ・機微情報の懸念があるため include_llm_raw が
    真のときのみ含める（parsed JSON の component_count は常に含める）。
    """
    out: list[dict] = []
    for doc_id in document_ids:
        artifact = (artifacts_by_doc.get(doc_id) or {}).get("component_assembly")
        if not isinstance(artifact, dict):
            continue
        diagnostics_raw = artifact.get("diagnostics") or {}
        diagnostics: dict[str, Any] = {}
        for key, value in (diagnostics_raw.items() if isinstance(diagnostics_raw, dict) else []):
            if not any(
                key == prefix or key.startswith(prefix)
                for prefix in _COMPONENT_ASSEMBLY_DIAG_KEY_PREFIXES
            ):
                continue
            if isinstance(value, dict) and ("raw_text" in value or "parsed" in value):
                sanitized = {
                    "component_count": value.get("component_count"),
                    "parse_error": value.get("parse_error"),
                }
                if include_llm_raw:
                    sanitized["parsed"] = value.get("parsed")
                    sanitized["raw_text"] = value.get("raw_text")
                diagnostics[key] = sanitized
            else:
                diagnostics[key] = value
        components = artifact.get("components") or []
        fallback_component_ids = [
            c.get("component_id")
            for c in components
            if isinstance(c, dict) and c.get("maturity_source") == "deterministic_fallback"
        ]
        out.append({
            "document_id": doc_id,
            "component_count": len(components) if isinstance(components, list) else 0,
            "fallback_component_ids": fallback_component_ids,
            "validation_issues": artifact.get("validation_issues") or [],
            "diagnostics": diagnostics,
        })
    return out


def _build_evidence_for_documents(
    artifacts_by_doc: dict[str, dict],
    document_ids: list[str],
    fallback_claims: list[dict],
) -> list[dict]:
    out: list[dict] = []
    for doc_id in document_ids:
        artifacts = artifacts_by_doc.get(doc_id, {})
        evidence_artifact = artifacts.get("evidence_registry")
        doc_claims = [c for c in fallback_claims if c.get("document_id") == doc_id]
        out.extend(build_evidence_export(
            evidence_artifact,
            document_id=doc_id,
            fallback_claims=doc_claims,
        ))
    return out


def _build_equations_for_documents(
    artifacts_by_doc: dict[str, dict],
    document_ids: list[str],
) -> list[dict]:
    out: list[dict] = []
    for doc_id in document_ids:
        artifacts = artifacts_by_doc.get(doc_id, {})
        evidence_index: dict[str, list[str]] = {}
        evidence_records = (artifacts.get("evidence_registry") or {}).get("records") or []
        for ev in evidence_records if isinstance(evidence_records, list) else []:
            if not isinstance(ev, dict):
                continue
            src = ev.get("source") or {}
            block_id = src.get("block_id") if isinstance(src, dict) else None
            evidence_id = ev.get("evidence_id")
            if block_id and evidence_id:
                evidence_index.setdefault(block_id, []).append(evidence_id)

        claim_index: dict[str, list[str]] = {}
        claim_records = (artifacts.get("claim_object_builder") or {}).get("claims") or []
        for claim in claim_records if isinstance(claim_records, list) else []:
            if not isinstance(claim, dict):
                continue
            claim_id = claim.get("claim_id")
            if not claim_id:
                continue
            for eq_id in claim.get("equation_ids") or []:
                if eq_id:
                    claim_index.setdefault(str(eq_id), []).append(str(claim_id))

        out.extend(build_equations_export(
            artifacts.get("equation_semantics"),
            document_id=doc_id,
            structure_artifact=artifacts.get("document_structure"),
            evidence_index=evidence_index,
            claim_index=claim_index,
        ))
    return out


def _build_equation_candidates_for_documents(
    artifacts_by_doc: dict[str, dict],
    document_ids: list[str],
) -> list[dict]:
    out: list[dict] = []
    for doc_id in document_ids:
        artifacts = artifacts_by_doc.get(doc_id, {})
        out.extend(build_equation_candidates_export(
            artifacts.get("equation_semantics"),
            document_id=doc_id,
            structure_artifact=artifacts.get("document_structure"),
        ))
    return out


def _build_derivations_for_documents(
    artifacts_by_doc: dict[str, dict],
    document_ids: list[str],
) -> list[dict]:
    out: list[dict] = []
    for doc_id in document_ids:
        artifacts = artifacts_by_doc.get(doc_id, {})
        out.extend(build_derivation_chains_export(
            artifacts.get("derivation_chain"),
            document_id=doc_id,
            equation_artifact=artifacts.get("equation_semantics"),
            evidence_artifact=artifacts.get("evidence_registry"),
        ))
    return out


def _build_document_boundaries(
    artifacts_by_doc: dict[str, dict],
    document_ids: list[str],
) -> list[dict]:
    out: list[dict] = []
    for doc_id in document_ids:
        artifacts = artifacts_by_doc.get(doc_id, {})
        structure = artifacts.get("document_structure")
        if not structure:
            continue
        out.append(build_document_boundary(
            structure,
            document_id=doc_id,
            evidence_artifact=artifacts.get("evidence_registry"),
        ))
    return out


def _build_document_completeness_reports(
    artifacts_by_doc: dict[str, dict],
    document_ids: list[str],
) -> list[dict]:
    """Deterministic per-document completeness reports for export_validation (#366)."""
    out: list[dict] = []
    for doc_id in document_ids:
        artifacts = artifacts_by_doc.get(doc_id, {})
        structure = artifacts.get("document_structure")
        if not structure:
            continue
        out.append(build_document_completeness(
            structure,
            document_id=doc_id,
            evidence_artifact=artifacts.get("evidence_registry"),
        ))
    return out


def _enrich_course_for_export(
    course: dict,
    artifacts_by_doc: dict[str, dict],
    document_ids: list[str],
    components: list[dict] | None = None,
    component_graph: dict | None = None,
) -> dict:
    # Pick the first document's artifacts (single-paper scoped courses are the
    # common case). Multi-document course mapping is a pipeline concern.
    course_mapping = None
    blueprint = None
    for doc_id in document_ids:
        artifacts = artifacts_by_doc.get(doc_id, {})
        if course_mapping is None:
            course_mapping = artifacts.get("course_mapping")
        if blueprint is None:
            blueprint = artifacts.get("blueprint")
    return enrich_course_topics(
        course,
        course_mapping_artifact=course_mapping,
        blueprint_artifact=blueprint,
        components=components or [],
        component_graph=component_graph or {},
    )


def _load_course_documents(session: Any, course_id: str, document_ids: list[str]) -> list[dict]:
    if not document_ids:
        return []
    placeholders = ", ".join(f":doc_{i}" for i in range(len(document_ids)))
    params = {f"doc_{i}": did for i, did in enumerate(document_ids)}
    rows = session.execute(
        sa_text(f"SELECT id, title, status, created_at FROM documents WHERE id::text IN ({placeholders})"),
        params,
    ).fetchall()
    return [{"document_id": str(r[0]), "title": r[1] or "", "status": r[2] or ""} for r in rows]


def _load_claims_for_course(session: Any, course_id: str, document_ids: list[str]) -> list[dict]:
    rows_by_course: list = []
    # course_idに紐づくcomponentsのchunk経由で取得
    rows_by_course = session.execute(
        sa_text("""
            SELECT DISTINCT tc.id, tc.document_id, tc.source_scope, tc.claim_type,
                   tc.text, tc.normalized_text, tc.concepts, tc.equation,
                   tc.support_status, tc.evidence_text, tc.review_status,
                   tc.created_at
            FROM theory_claims tc
            JOIN chunks c ON c.id = tc.chunk_id
            JOIN theory_components tcomp ON tcomp.primary_chunk_id = c.id
            WHERE tcomp.course_id = :course_id
        """),
        {"course_id": course_id},
    ).fetchall()

    rows_by_doc: list = []
    if document_ids:
        placeholders = ", ".join(f":doc_{i}" for i in range(len(document_ids)))
        params: dict = {f"doc_{i}": did for i, did in enumerate(document_ids)}
        rows_by_doc = session.execute(
            sa_text(f"""
                SELECT DISTINCT tc.id, tc.document_id, tc.source_scope, tc.claim_type,
                       tc.text, tc.normalized_text, tc.concepts, tc.equation,
                       tc.support_status, tc.evidence_text, tc.review_status,
                       tc.created_at
                FROM theory_claims tc
                WHERE tc.document_id IN ({placeholders})
                   OR tc.chunk_id IN (
                       SELECT id FROM chunks WHERE document_id::text IN ({placeholders})
                   )
            """),
            params,
        ).fetchall()

    return _rows_to_claims(list(rows_by_course) + list(rows_by_doc))


def _load_claims_for_document(session: Any, document_id: str) -> list[dict]:
    rows = session.execute(
        sa_text("""
            SELECT tc.id, tc.document_id, tc.source_scope, tc.claim_type,
                   tc.text, tc.normalized_text, tc.concepts, tc.equation,
                   tc.support_status, tc.evidence_text, tc.review_status,
                   tc.created_at
            FROM theory_claims tc
            WHERE tc.document_id = :document_id
               OR tc.chunk_id IN (SELECT id FROM chunks WHERE document_id::text = :document_id)
            ORDER BY tc.created_at
        """),
        {"document_id": document_id},
    ).fetchall()
    return _rows_to_claims(rows)


def _rows_to_claims(rows: list) -> list[dict]:
    claims = []
    seen = set()
    for r in rows:
        claim_id = str(r[0])
        if claim_id in seen:
            continue
        seen.add(claim_id)
        claims.append({
            "claim_id": claim_id,
            "document_id": r[1] or "",
            "source_scope": _load_json_field(r[2], {}),
            "claim_type": r[3] or "diagnostic_claim",
            "text": r[4] or "",
            "normalized_text": r[5] or "",
            "concepts": _load_json_field(r[6], []),
            "equation": _load_json_field(r[7], {}),
            "support_status": r[8] or "source_backed",
            "evidence_text": r[9] or "",
            "review_status": r[10] or "teacher_review_required",
        })
    return claims


def _load_dsl_graph_for_course(session: Any, course_id: str, document_ids: list[str]) -> dict:
    # 1. theory_component_graphs.graph_json.dsl から取得（主ルート）
    graph = _load_dsl_from_component_graphs(session, course_id=course_id, document_ids=document_ids)
    if graph["nodes"] or graph["edges"]:
        return graph

    # 2. chunks.smiles_dsl から取得（フォールバック）
    where_parts = ["tc.course_id = :course_id"]
    params: dict = {"course_id": course_id}
    if document_ids:
        placeholders = ", ".join(f":doc_{i}" for i in range(len(document_ids)))
        params.update({f"doc_{i}": did for i, did in enumerate(document_ids)})
        where_parts.append(f"(tc.document_id IN ({placeholders}) OR c.document_id::text IN ({placeholders}))")
    where_clause = " OR ".join(f"({p})" for p in where_parts)

    rows = session.execute(
        sa_text(f"""
            SELECT DISTINCT c.id::text, c.smiles_dsl, c.variables, c.ancestors, c.document_id
            FROM chunks c
            JOIN theory_components tc ON tc.primary_chunk_id = c.id
            WHERE ({where_clause})
              AND c.smiles_dsl IS NOT NULL AND c.smiles_dsl != ''
        """),
        params,
    ).fetchall()
    return _rows_to_dsl_graph(rows)


def _load_dsl_graph_for_document(session: Any, document_id: str) -> dict:
    # 1. theory_component_graphs.graph_json.dsl から取得
    graph = _load_dsl_from_component_graphs(session, document_ids=[document_id])
    if graph["nodes"] or graph["edges"]:
        return graph

    # 2. chunks.smiles_dsl から取得（フォールバック）
    rows = session.execute(
        sa_text("""
            SELECT c.id::text, c.smiles_dsl, c.variables, c.ancestors, c.document_id
            FROM chunks c
            WHERE c.document_id::text = :document_id
              AND c.smiles_dsl IS NOT NULL AND c.smiles_dsl != ''
        """),
        {"document_id": document_id},
    ).fetchall()
    return _rows_to_dsl_graph(rows)


def _load_dsl_from_component_graphs(
    session: Any,
    course_id: str | None = None,
    document_ids: list[str] | None = None,
) -> dict:
    """theory_component_graphs.graph_json.dsl からDSLグラフを構築する。"""
    where_parts: list[str] = []
    params: dict = {}
    if course_id:
        where_parts.append("course_id = :course_id")
        params["course_id"] = course_id
    if document_ids:
        placeholders = ", ".join(f":doc_{i}" for i in range(len(document_ids)))
        params.update({f"doc_{i}": did for i, did in enumerate(document_ids)})
        where_parts.append(f"document_id IN ({placeholders})")
    if not where_parts:
        return {"dsl_schema_version": "0.1.0", "nodes": [], "edges": []}

    where_clause = " OR ".join(f"({p})" for p in where_parts)
    rows = session.execute(
        sa_text(f"SELECT document_id, graph_json FROM theory_component_graphs WHERE {where_clause}"),
        params,
    ).fetchall()

    nodes: list[dict] = []
    edges: list[dict] = []
    node_set: set[str] = set()
    edge_counter = 0

    for row in rows:
        doc_id = str(row[0]) if row[0] else ""
        gj = _load_json_field(row[1], {})
        dsl = gj.get("dsl") if isinstance(gj, dict) else {}
        if not isinstance(dsl, dict):
            dsl = {}

        raw_nodes = dsl.get("nodes", []) if isinstance(dsl.get("nodes"), list) else []
        raw_edges = dsl.get("edges", []) if isinstance(dsl.get("edges"), list) else []

        for n in raw_nodes:
            if not isinstance(n, dict):
                continue
            node_id = str(n.get("id") or n.get("node_id") or "").strip()
            value = str(n.get("value") or n.get("label") or node_id).strip()
            node_type = str(n.get("node_type") or n.get("type") or "Node").strip()
            if not node_id:
                continue
            key = f"{doc_id}:{node_id}"
            if key not in node_set:
                node_set.add(key)
                nodes.append({
                    "node_id": key,
                    "var_id": node_id,
                    "node_type": node_type,
                    "value": value,
                    "chunk_id": "",
                    "document_id": doc_id,
                    "smiles_dsl": "",
                })

        for edge in raw_edges:
            if not isinstance(edge, dict):
                continue
            source = str(edge.get("from") or edge.get("source") or "").strip()
            target = str(edge.get("to") or edge.get("target") or "").strip()
            predicate = str(edge.get("predicate") or edge.get("relation") or "RELATED_TO").strip()
            verb = str(edge.get("verb") or "").strip()
            polarity = str(edge.get("polarity") or "+").strip()
            if not source or not target:
                continue
            edge_counter += 1
            edges.append({
                "edge_id": f"edge_{doc_id[:8]}_{edge_counter:04d}",
                "source": f"{doc_id}:{source}",
                "target": f"{doc_id}:{target}",
                "core_predicate": predicate,
                "domain_verb": verb,
                "polarity": polarity,
                "chunk_id": "",
            })

    return {"dsl_schema_version": "0.1.0", "nodes": nodes, "edges": edges}


def _rows_to_dsl_graph(rows: list) -> dict:
    nodes: list[dict] = []
    edges: list[dict] = []
    node_set: set[str] = set()
    edge_counter = 0

    for row in rows:
        chunk_id, smiles_dsl, variables_raw, ancestors_raw, doc_id = row
        variables = _load_json_field(variables_raw, {})
        ancestors = _load_json_field(ancestors_raw, [])

        for var_id, var_info in (variables.items() if isinstance(variables, dict) else {}.items()):
            node_id = f"node_{chunk_id[:8]}_{var_id}"
            if node_id not in node_set:
                node_set.add(node_id)
                nodes.append({
                    "node_id": node_id,
                    "var_id": var_id,
                    "node_type": var_info.get("ontology_type", "") if isinstance(var_info, dict) else "",
                    "value": var_info.get("value", var_id) if isinstance(var_info, dict) else str(var_info),
                    "chunk_id": chunk_id,
                    "document_id": str(doc_id) if doc_id else "",
                    "smiles_dsl": smiles_dsl or "",
                })

        for anc in (ancestors if isinstance(ancestors, list) else []):
            if not isinstance(anc, dict):
                continue
            src = anc.get("source", "")
            tgt = anc.get("target", "")
            pred = anc.get("predicate", "") or anc.get("core_predicate", "")
            if src and tgt:
                edge_counter += 1
                edges.append({
                    "edge_id": f"edge_{chunk_id[:8]}_{edge_counter:04d}",
                    "source": src,
                    "target": tgt,
                    "core_predicate": pred,
                    "domain_verb": anc.get("verb", ""),
                    "polarity": anc.get("polarity", "+"),
                    "chunk_id": chunk_id,
                })

    return {"dsl_schema_version": "0.1.0", "nodes": nodes, "edges": edges}


def _load_components_for_course(session: Any, course_id: str, document_ids: list[str]) -> list[dict]:
    where_parts = ["tc.course_id = :course_id"]
    params: dict = {"course_id": course_id}
    if document_ids:
        placeholders = ", ".join(f":doc_{i}" for i in range(len(document_ids)))
        params.update({f"doc_{i}": did for i, did in enumerate(document_ids)})
        where_parts.append(f"tc.document_id IN ({placeholders})")
    where_clause = " OR ".join(f"({p})" for p in where_parts)

    rows = session.execute(
        sa_text(f"""
            SELECT tc.id, tc.course_id, tc.name, tc.component_type, tc.component_type_text,
                   tc.summary, tc.status, tc.source_scope, tc.evidence_claims, tc.maturity_level,
                   tc.maturity_source, tc.review_status, tc.inputs, tc.outputs, tc.preconditions,
                   tc.cautions, tc.constraints, tc.invalid_conditions, tc.dependencies,
                   tc.connectors, tc.internal_flow, tc.teacher_notes, tc.created_at,
                   tc.document_id,
                   ch.smiles_dsl
            FROM theory_components tc
            LEFT JOIN chunks ch ON ch.id = tc.primary_chunk_id
            WHERE {where_clause}
            ORDER BY tc.created_at
        """),
        params,
    ).fetchall()
    return _rows_to_components(rows)


def _load_components_for_document(session: Any, document_id: str) -> list[dict]:
    rows = session.execute(
        sa_text("""
            SELECT tc.id, tc.course_id, tc.name, tc.component_type, tc.component_type_text,
                   tc.summary, tc.status, tc.source_scope, tc.evidence_claims, tc.maturity_level,
                   tc.maturity_source, tc.review_status, tc.inputs, tc.outputs, tc.preconditions,
                   tc.cautions, tc.constraints, tc.invalid_conditions, tc.dependencies,
                   tc.connectors, tc.internal_flow, tc.teacher_notes, tc.created_at,
                   tc.document_id,
                   ch.smiles_dsl
            FROM theory_components tc
            LEFT JOIN chunks ch ON ch.id = tc.primary_chunk_id
            WHERE tc.document_id = :document_id
               OR ch.document_id::text = :document_id
            ORDER BY tc.created_at
        """),
        {"document_id": document_id},
    ).fetchall()
    return _rows_to_components(rows)


def _rows_to_components(rows: list) -> list[dict]:
    components = []
    seen: set[str] = set()
    for r in rows:
        comp_id = str(r[0])
        if comp_id in seen:
            continue
        seen.add(comp_id)
        source_scope = _load_json_field(r[7], {})
        evidence_claims = _load_json_field(r[8], [])
        document_id = r[23] if len(r) > 23 and r[23] is not None else ""
        smiles_dsl = r[24] if len(r) > 24 and r[24] is not None else ""
        source_scope["document_id"] = source_scope.get("document_id", "") or document_id
        components.append({
            "component_id": comp_id,
            "course_id": str(r[1]) if r[1] else "",
            "document_id": document_id,
            "name": r[2] or "",
            "component_type": r[4] or r[3] or "theory",
            "origin": "paper",
            "source_scope": source_scope,
            "evidence_claims": evidence_claims,
            "maturity_level": r[9] or "paper_claim",
            "maturity_source": r[10] or "llm_proposed",
            "review_status": r[11] or "teacher_review_required",
            "summary": r[5] or "",
            "inputs": _load_json_field(r[12], []),
            "outputs": _load_json_field(r[13], []),
            "preconditions": _load_json_field(r[14], []),
            "cautions": _load_json_field(r[15], []),
            "constraints": _load_json_field(r[16], []),
            "invalid_conditions": _load_json_field(r[17], []),
            "dependencies": _load_json_field(r[18], []),
            "connectors": _load_json_field(r[19], {}),
            "internal_flow": _load_json_field(r[20], []),
            "teacher_notes": r[21] or "",
            "smiles_dsl": smiles_dsl,
        })
    return components


def _load_component_graph_for_course(session: Any, course_id: str, document_ids: list[str]) -> dict:
    where_parts = ["course_id = :course_id"]
    params: dict = {"course_id": course_id}
    if document_ids:
        placeholders = ", ".join(f":doc_{i}" for i in range(len(document_ids)))
        params.update({f"doc_{i}": did for i, did in enumerate(document_ids)})
        where_parts.append(f"document_id IN ({placeholders})")
    where_clause = " OR ".join(f"({p})" for p in where_parts)

    row = session.execute(
        sa_text(f"""
            SELECT id, document_id, scope, graph_json
            FROM theory_component_graphs
            WHERE {where_clause}
            ORDER BY updated_at DESC
            LIMIT 1
        """),
        params,
    ).fetchone()
    if not row:
        return {"graph_schema_version": "0.1.0", "nodes": [], "edges": []}
    return _row_to_component_graph(row)


def _load_component_graph_for_document(session: Any, document_id: str) -> dict:
    row = session.execute(
        sa_text("""
            SELECT id, document_id, scope, graph_json
            FROM theory_component_graphs
            WHERE document_id = :document_id
            ORDER BY updated_at DESC
            LIMIT 1
        """),
        {"document_id": document_id},
    ).fetchone()
    if not row:
        return {"graph_schema_version": "0.1.0", "nodes": [], "edges": []}
    return _row_to_component_graph(row)


def _row_to_component_graph(row: Any) -> dict:
    graph_json = _load_json_field(row[3], {})
    raw_nodes = graph_json.get("nodes", [])
    raw_edges = graph_json.get("edges", [])

    nodes = []
    for n in raw_nodes:
        if not isinstance(n, dict):
            continue
        node_id = n.get("component_id", n.get("id", ""))
        legacy_ids = []
        for key in ("agent_component_id", "legacy_component_id"):
            value = n.get(key)
            if value and str(value) != str(node_id):
                legacy_ids.append(str(value))
        nodes.append({
            "node_id": node_id,
            "node_type": "component",
            "label": n.get("label", n.get("name", "")),
            "component_type": n.get("component_type", ""),
            "review_status": n.get("review_status", "teacher_review_required"),
            "legacy_ids": legacy_ids,
        })

    edges = []
    for i, e in enumerate(raw_edges):
        if not isinstance(e, dict):
            continue
        evidence = e.get("evidence", {}) if isinstance(e.get("evidence"), dict) else {}
        edges.append({
            "edge_id": e.get("edge_id", f"component_edge_{i+1:04d}"),
            "source": e.get("source_component_id", e.get("source", e.get("from", ""))),
            "target": e.get("target_component_id", e.get("target", e.get("to", ""))),
            "edge_type": e.get("relation", e.get("edge_type", e.get("type", "RELATED_TO"))),
            "support_status": e.get("support_status", "source_inferred"),
            "evidence_claims": _load_json_field(e.get("evidence_claims", evidence.get("evidence_claims", [])), []),
            "review_status": e.get("review_status", "teacher_review_required"),
        })

    return {"graph_schema_version": "0.1.0", "nodes": nodes, "edges": edges}


def _build_evidence_snippets(claims: list[dict]) -> list[dict]:
    snippets = []
    for i, claim in enumerate(claims):
        if not claim.get("evidence_text"):
            continue
        scope = claim.get("source_scope", {})
        snippets.append({
            "evidence_id": f"evidence_{i+1:04d}",
            "claim_id": claim["claim_id"],
            "document_id": claim.get("document_id", ""),
            "section_id": scope.get("section_id", ""),
            "chunk_id": scope.get("chunk_id", ""),
            "page": scope.get("page"),
            "text": claim["evidence_text"],
        })
    return snippets


_LEGACY_REF_PATTERNS = [
    re.compile(r"^claim_span_"),
    re.compile(r"^claim:[^:]+:[^:]+$"),
    re.compile(r"^comp_\d+$"),
]


def _is_legacy_export_ref(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    return any(pattern.match(value) for pattern in _LEGACY_REF_PATTERNS)


def _claim_ref_map(claims: list[dict]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for claim in claims:
        claim_id = str(claim.get("claim_id") or "")
        if not claim_id:
            continue
        mapping[claim_id] = claim_id
        scope = claim.get("source_scope") or {}
        if not isinstance(scope, dict):
            continue
        span_id = scope.get("span_id")
        block_id = scope.get("block_id")
        if span_id:
            safe = re.sub(r"[^A-Za-z0-9_]+", "_", str(span_id))
            mapping[str(span_id)] = claim_id
            mapping[f"claim_{safe}"] = claim_id
        if block_id and span_id:
            mapping[f"claim:{block_id}:{span_id}"] = claim_id
    return mapping


def _component_ref_map(components: list[dict], component_graph: dict) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for comp in components:
        component_id = str(comp.get("component_id") or "")
        if not component_id:
            continue
        mapping[component_id] = component_id
        source_scope = comp.get("source_scope") or {}
        legacy_ids = source_scope.get("legacy_ids", []) if isinstance(source_scope, dict) else []
        for legacy_id in legacy_ids if isinstance(legacy_ids, list) else []:
            if legacy_id:
                mapping[str(legacy_id)] = component_id
    for node in component_graph.get("nodes", []) or []:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("node_id") or "")
        if not node_id:
            continue
        mapping[node_id] = node_id
        for legacy_id in node.get("legacy_ids") or []:
            if legacy_id:
                mapping[str(legacy_id)] = node_id
    return mapping


def _map_ref_list(
    values: Any,
    id_map: dict[str, str],
    *,
    known_ids: set[str] | None = None,
    drop_unresolved: bool = False,
) -> list[str]:
    out: list[str] = []
    for value in values or []:
        mapped = id_map.get(str(value), str(value))
        if drop_unresolved and known_ids is not None and mapped not in known_ids:
            continue
        if mapped not in out:
            out.append(mapped)
    return out


def _normalize_claim_refs_in_items(
    items: Any,
    claim_map: dict[str, str],
    known_claim_ids: set[str],
) -> Any:
    if isinstance(items, list):
        return [_normalize_claim_refs_in_items(item, claim_map, known_claim_ids) for item in items]
    if not isinstance(items, dict):
        return items
    out = dict(items)
    for key in ("claim_ids", "evidence_claims", "linked_claim_ids"):
        if isinstance(out.get(key), list):
            out[key] = _map_ref_list(
                out[key],
                claim_map,
                known_ids=known_claim_ids,
                drop_unresolved=True,
            )
    return out


def _normalize_export_references(
    *,
    claims: list[dict],
    equations: list[dict],
    components: list[dict],
    component_graph: dict,
    course_info: dict | None,
) -> None:
    claim_map = _claim_ref_map(claims)
    component_map = _component_ref_map(components, component_graph)
    known_claim_ids = {str(c.get("claim_id")) for c in claims if c.get("claim_id")}
    known_component_ids = {
        str(c.get("component_id")) for c in components if c.get("component_id")
    }

    for eq in equations:
        if isinstance(eq, dict) and isinstance(eq.get("linked_claim_ids"), list):
            eq["linked_claim_ids"] = _map_ref_list(
                eq["linked_claim_ids"],
                claim_map,
                known_ids=known_claim_ids,
                drop_unresolved=True,
            )

    for comp in components:
        if not isinstance(comp, dict):
            continue
        if isinstance(comp.get("evidence_claims"), list):
            # Map legacy/provisional claim IDs to canonical, but do NOT drop
            # unresolved ones (issue #400): dropping here would hide a dangling
            # claim reference from _validate_export_references, letting a
            # component with only missing claims pass as publish_ready.
            comp["evidence_claims"] = _map_ref_list(
                comp["evidence_claims"],
                claim_map,
                known_ids=known_claim_ids,
                drop_unresolved=False,
            )
        for key in ("inputs", "outputs", "preconditions", "cautions", "constraints", "invalid_conditions"):
            if key in comp:
                comp[key] = _normalize_claim_refs_in_items(comp[key], claim_map, known_claim_ids)
        for dep in comp.get("dependencies") or []:
            if isinstance(dep, dict) and isinstance(dep.get("component_refs"), list):
                # Map but preserve unresolved component refs so validation can
                # hard-error on a dependency pointing at a missing component (#400).
                dep["component_refs"] = _map_ref_list(
                    dep["component_refs"],
                    component_map,
                    known_ids=known_component_ids,
                    drop_unresolved=False,
                )

    for edge in component_graph.get("edges", []) or []:
        if not isinstance(edge, dict):
            continue
        for key in ("source", "target"):
            if edge.get(key) is not None:
                edge[key] = component_map.get(str(edge[key]), str(edge[key]))
        if isinstance(edge.get("evidence_claims"), list):
            edge["evidence_claims"] = _map_ref_list(
                edge["evidence_claims"],
                claim_map,
                known_ids=known_claim_ids,
                drop_unresolved=True,
            )

    if not isinstance(course_info, dict):
        return
    known_equation_ids = {str(e.get("equation_id")) for e in equations if isinstance(e, dict) and e.get("equation_id")}
    for topic in course_info.get("topics") or []:
        if not isinstance(topic, dict):
            continue
        if isinstance(topic.get("linked_component_ids"), list):
            topic["linked_component_ids"] = _map_ref_list(
                topic["linked_component_ids"],
                component_map,
                known_ids=known_component_ids,
                drop_unresolved=True,
            )
        # Derived topic claim/equation links (issue #389) are dropped when they
        # cannot be resolved so the validation gate reports real artifact issues,
        # not derived-link drift.
        if isinstance(topic.get("linked_claim_ids"), list):
            topic["linked_claim_ids"] = _map_ref_list(
                topic["linked_claim_ids"], claim_map, known_ids=known_claim_ids, drop_unresolved=True,
            )
        if isinstance(topic.get("linked_equation_ids"), list):
            topic["linked_equation_ids"] = [
                str(v) for v in topic["linked_equation_ids"] if str(v) in known_equation_ids
            ]
        for step in topic.get("visualization_plan") or []:
            if isinstance(step, dict) and isinstance(step.get("linked_component_ids"), list):
                step["linked_component_ids"] = _map_ref_list(
                    step["linked_component_ids"],
                    component_map,
                    known_ids=known_component_ids,
                    drop_unresolved=True,
                )


def _normalize_derivation_references(
    *,
    claims: list[dict],
    equations: list[dict],
    derivation_chains: list[dict],
) -> None:
    claim_map = _claim_ref_map(claims)
    known_claim_ids = {str(c.get("claim_id")) for c in claims if c.get("claim_id")}
    equation_ids = {str(e.get("equation_id")) for e in equations if e.get("equation_id")}
    for chain in derivation_chains:
        if not isinstance(chain, dict):
            continue
        for step in chain.get("steps") or []:
            if not isinstance(step, dict):
                continue
            for key in ("claim_ids", "required_claim_ids", "input_claim_ids", "output_claim_ids"):
                if isinstance(step.get(key), list):
                    step[key] = _map_ref_list(
                        step[key],
                        claim_map,
                        known_ids=known_claim_ids,
                        drop_unresolved=True,
                    )
            for key in ("input_equation_ids", "output_equation_ids"):
                if isinstance(step.get(key), list):
                    step[key] = [str(v) for v in step[key] if str(v) in equation_ids]


def _chain_claim_ids(chain: dict) -> set[str]:
    ids: set[str] = set()
    for step in chain.get("steps") or []:
        if not isinstance(step, dict):
            continue
        for key in ("claim_ids", "required_claim_ids", "input_claim_ids", "output_claim_ids"):
            ids.update(str(v) for v in (step.get(key) or []) if v)
    return ids


def _topic_mentions_derivation(topic: dict) -> bool:
    text = " ".join(str(topic.get(k) or "") for k in ("title", "description"))
    if re.search(r"deriv|導出|導く|derive", text, re.IGNORECASE):
        return True
    policy = topic.get("blackbox_policy") or {}
    if isinstance(policy, dict):
        return bool(policy.get("show_derivation") or policy.get("show_derivation_depth"))
    return False


def _link_derivations_to_export_context(
    *,
    components: list[dict],
    course_info: dict | None,
    derivation_chains: list[dict],
) -> None:
    if not derivation_chains:
        return
    chain_ids = [str(c.get("derivation_id")) for c in derivation_chains if c.get("derivation_id")]
    if not chain_ids:
        return
    for comp in components:
        if not isinstance(comp, dict):
            continue
        comp_claims = {str(v) for v in comp.get("evidence_claims") or [] if v}
        linked = list(comp.get("linked_derivation_ids") or [])
        for chain in derivation_chains:
            derivation_id = str(chain.get("derivation_id") or "")
            if derivation_id and comp_claims and comp_claims.intersection(_chain_claim_ids(chain)):
                linked.append(derivation_id)
        if linked:
            comp["linked_derivation_ids"] = sorted(set(linked))

    if not isinstance(course_info, dict):
        return
    for topic in course_info.get("topics") or []:
        if not isinstance(topic, dict) or not _topic_mentions_derivation(topic):
            continue
        existing = [str(v) for v in topic.get("linked_derivation_ids") or [] if v]
        topic["linked_derivation_ids"] = sorted(set(existing + chain_ids))


def _apply_confidence_gates_to_export(
    *,
    claims: list[dict],
    components: list[dict],
    component_graph: dict,
    course_info: dict | None,
    derivation_chains: list[dict],
    equations: list[dict],
) -> None:
    equation_gate_index = _blocked_equation_gate_index(equations)
    if not equation_gate_index:
        return

    for claim in claims:
        if not isinstance(claim, dict):
            continue
        gate = _confidence_gate_for_refs(_claim_equation_refs(claim), equation_gate_index, semantic_hint=True)
        claim["confidence_gate"] = gate
        if gate["blocked_by_equation_ids"]:
            claim["review_status"] = "teacher_review_required"

    component_gate_by_id: dict[str, dict] = {}
    for comp in components:
        if not isinstance(comp, dict):
            continue
        gate = _confidence_gate_for_refs(_component_equation_refs_export(comp), equation_gate_index, semantic_hint=True)
        comp["confidence_gate"] = gate
        component_gate_by_id[str(comp.get("component_id") or "")] = gate
        if gate["blocked_by_equation_ids"]:
            comp["review_status"] = "review_required"
            comp["publish_ready"] = False
            reasons = comp.get("review_reason") or comp.get("review_reasons") or []
            if isinstance(reasons, str):
                reasons = [reasons]
            if gate["blocked_reason"] not in reasons:
                reasons.append(gate["blocked_reason"])
            comp["review_reason"] = reasons
            existing = [str(v) for v in comp.get("review_required_equation_ids") or [] if v]
            comp["review_required_equation_ids"] = sorted(set(existing + gate["blocked_by_equation_ids"]))

    for chain in derivation_chains:
        if not isinstance(chain, dict):
            continue
        step_gates: list[dict] = []
        for step in chain.get("steps") or []:
            if not isinstance(step, dict):
                continue
            refs = list(step.get("input_equation_ids") or []) + list(step.get("output_equation_ids") or [])
            gate = _confidence_gate_for_refs(refs, equation_gate_index, semantic_hint=False)
            step["confidence_gate"] = gate
            step_gates.append(gate)
            if gate["blocked_by_equation_ids"]:
                step["review_status"] = "teacher_review_required"
                step["review_reason"] = gate["blocked_reason"]
        chain_gate = _merge_confidence_gates(step_gates, semantic_hint=False)
        chain["confidence_gate"] = chain_gate
        if chain_gate["blocked_by_equation_ids"]:
            chain["review_status"] = "teacher_review_required"
            chain["review_reason"] = chain_gate["blocked_reason"]

    if isinstance(component_graph, dict):
        for node in component_graph.get("nodes") or []:
            if not isinstance(node, dict):
                continue
            comp_id = str(node.get("component_id") or node.get("node_id") or node.get("id") or "")
            gate = component_gate_by_id.get(comp_id)
            if gate:
                node["confidence_gate"] = gate
                if gate["blocked_by_equation_ids"]:
                    node["review_status"] = "review_required"
        for edge in component_graph.get("edges") or []:
            if not isinstance(edge, dict):
                continue
            source_gate = component_gate_by_id.get(str(edge.get("source") or ""))
            target_gate = component_gate_by_id.get(str(edge.get("target") or ""))
            gate = _merge_confidence_gates([g for g in (source_gate, target_gate) if g], semantic_hint=True)
            if gate["blocked_by_equation_ids"]:
                edge["confidence_gate"] = gate
                edge["review_status"] = "review_required"

    if isinstance(course_info, dict):
        for topic in course_info.get("topics") or []:
            if not isinstance(topic, dict):
                continue
            gates = [component_gate_by_id.get(str(cid)) for cid in topic.get("linked_component_ids") or []]
            for derivation_id in topic.get("linked_derivation_ids") or []:
                for chain in derivation_chains:
                    if str(chain.get("derivation_id") or "") == str(derivation_id):
                        gates.append(chain.get("confidence_gate") if isinstance(chain.get("confidence_gate"), dict) else None)
            gate = _merge_confidence_gates([g for g in gates if g], semantic_hint=True)
            topic["confidence_gate"] = gate
            if gate["blocked_by_equation_ids"]:
                topic["final_formula_rendering_allowed"] = False
            for step in topic.get("visualization_plan") or []:
                if not isinstance(step, dict):
                    continue
                step_gates = [component_gate_by_id.get(str(cid)) for cid in step.get("linked_component_ids") or []]
                step_gate = _merge_confidence_gates([g for g in step_gates if g], semantic_hint=True)
                step["confidence_gate"] = step_gate
                if step_gate["blocked_by_equation_ids"]:
                    step["final_formula_rendering_allowed"] = False


def _blocked_equation_gate_index(equations: list[dict]) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for eq in equations:
        if not isinstance(eq, dict):
            continue
        eq_id = str(eq.get("equation_id") or "")
        if not eq_id:
            continue
        gate = eq.get("confidence_gate") if isinstance(eq.get("confidence_gate"), dict) else {}
        blocked = list(gate.get("blocked_by_equation_ids") or [])
        policy = eq.get("confidence_policy") if isinstance(eq.get("confidence_policy"), dict) else {}
        if not blocked and policy.get("can_support_claim") is False and policy.get("can_be_used_in_derivation") is False:
            blocked = [eq_id]
        if not blocked and eq.get("latex") is None and eq.get("plain_text") is None and eq.get("extraction_status") in ("partial", "fragment_only", "label_only", "missing", "unparsed"):
            blocked = [eq_id]
        if blocked:
            index[eq_id] = {
                "blocked_by_equation_ids": [str(v) for v in blocked],
                "blocked_reason": gate.get("blocked_reason") or "linked equation cannot support claim or derivation",
                "downstream_allowed_use": gate.get("downstream_allowed_use") or "semantic_hint_only",
            }
    return index


def _confidence_gate_for_refs(refs: list[str], equation_gate_index: dict[str, dict], *, semantic_hint: bool) -> dict:
    gates = [equation_gate_index.get(str(ref)) for ref in refs if equation_gate_index.get(str(ref))]
    return _merge_confidence_gates(gates, semantic_hint=semantic_hint)


def _merge_confidence_gates(gates: list[dict], *, semantic_hint: bool) -> dict:
    blocked: list[str] = []
    for gate in gates:
        blocked.extend(str(v) for v in (gate or {}).get("blocked_by_equation_ids") or [] if v)
    seen: set[str] = set()
    unique = [v for v in blocked if not (v in seen or seen.add(v))]
    if not unique:
        return {"blocked_by_equation_ids": [], "blocked_reason": "", "downstream_allowed_use": "display_with_warning"}
    return {
        "blocked_by_equation_ids": unique,
        "blocked_reason": "linked equation cannot support claim or derivation",
        "downstream_allowed_use": "semantic_hint_only" if semantic_hint else "blocked",
    }


def _claim_equation_refs(claim: dict) -> list[str]:
    equation = claim.get("equation") if isinstance(claim.get("equation"), dict) else {}
    values: list[str] = []
    for key in ("equation_id", "id"):
        if equation.get(key):
            values.append(str(equation[key]))
    for key in ("equation_ids", "linked_equation_ids"):
        values.extend(str(v) for v in equation.get(key) or [] if v)
        values.extend(str(v) for v in claim.get(key) or [] if v)
    return sorted(set(values))


def _component_equation_refs_export(comp: dict) -> list[str]:
    values: list[str] = []
    refs = comp.get("evidence_refs") if isinstance(comp.get("evidence_refs"), dict) else {}
    values.extend(str(v) for v in refs.get("equation_ids") or [] if v)
    for key in (
        "linked_equation_ids", "input_equation_ids", "intermediate_equation_ids",
        "output_equation_ids", "constraint_equation_ids", "definition_equation_ids",
        "review_required_equation_ids",
    ):
        values.extend(str(v) for v in comp.get(key) or [] if v)
    for key in ("inputs", "outputs", "preconditions", "cautions", "constraints"):
        for item in comp.get(key) or []:
            if not isinstance(item, dict):
                continue
            for item_key in ("equation_ids", "equations"):
                raw = item.get(item_key) or []
                if isinstance(raw, str):
                    values.append(raw)
                else:
                    values.extend(str(v) for v in raw if v)
    return sorted(set(values))


def _build_completeness_report(completeness_reports: list[dict] | None) -> dict:
    """Aggregate per-document completeness into the export_validation block (#366 / #371).

    Returns a JSON-serialisable summary listing, per incomplete document, the
    missing equation labels, whether the DocumentStructure ingest reached the
    document end (with any trailing un-ingested page ranges), and whether a
    terminal (Conclusion/Summary) section was found. The EvidenceRegistry page
    distribution is carried as audit-only metadata (issue #371) and never sets
    ``complete=false``. Incomplete documents are reported so they are not
    promoted to publish_ready.
    """
    reports = completeness_reports or []
    documents: list[dict] = []
    all_complete = True
    for rep in reports:
        if not isinstance(rep, dict):
            continue
        complete = bool(rep.get("complete", True))
        all_complete = all_complete and complete
        eq = rep.get("equation_label_continuity") or {}
        terminal = rep.get("terminal_section") or {}
        ingest = rep.get("ingest_coverage") or {}
        evidence_dist = rep.get("evidence_page_distribution") or {}
        tail = rep.get("tail_truncation") or {}
        documents.append({
            "document_id": rep.get("document_id"),
            "complete": complete,
            "review_reasons": list(rep.get("review_reasons") or []),
            "missing_equation_labels": list(eq.get("missing_labels") or []),
            "internal_gap_labels": list(eq.get("internal_gaps") or []),
            "referenced_missing_labels": list(eq.get("referenced_missing_labels") or []),
            # Tail truncation is a *suspicion* (issue #373), kept distinct from
            # confirmed missing labels.
            "tail_truncation_suspected": bool(tail.get("suspected")),
            "tail_truncation_confidence": tail.get("confidence"),
            "tail_truncation_signals": list(tail.get("signals") or []),
            "terminal_section_present": bool(terminal.get("present")),
            "reached_document_end": bool(ingest.get("reached_document_end", True)),
            "last_ingested_page": ingest.get("last_ingested_page"),
            "trailing_uningested_page_ranges": list(
                ingest.get("trailing_uningested_page_ranges") or []
            ),
            "pages_total": ingest.get("pages_total"),
            "structure_page_coverage_ratio": ingest.get("structure_page_coverage_ratio"),
            "ingest_sufficient": bool(ingest.get("sufficient", True)),
            # Audit-only (issue #371): never blocks publish_ready on its own.
            "evidence_pages": list(evidence_dist.get("pages") or []),
            "evidence_distribution_ratio": evidence_dist.get("distribution_ratio"),
            "evidence_sparse": bool(evidence_dist.get("sparse")),
        })
    return {
        "checked": bool(documents),
        "all_documents_complete": all_complete,
        "documents": documents,
    }


def _validate_system_operations(
    system_operations: list[dict],
    *,
    core_families: set[str],
    equation_ids: set[str],
    claim_ids: set[str],
    evidence_ids: set[str],
    derivation_ids: set[str],
    component_ids: set[str],
    operation_ids: set[str],
    add,
    warn,
    review,
) -> None:
    """Validate system-level operation artifacts (issue #394).

    A system operation bundles a *group* of equations / claims / derivations /
    operations into one explainable artifact. It must keep honest source backing
    (at least one equation / claim / evidence / derivation reference) and a
    generic ``system_family`` from CORE_OPERATION_FAMILIES. Domain-specific
    structure is carried only as an optional, provenance-tagged subtype.
    """
    for idx, sys_op in enumerate(system_operations or []):
        if not isinstance(sys_op, dict):
            continue
        sys_id = str(sys_op.get("system_id") or "")
        path = f"$.system_operations[{idx}]"
        if not sys_id:
            add(
                "SYSTEM_OPERATION_MISSING_ID",
                f"system operation at index {idx} has no system_id",
                "graph/system_operations.json",
                f"{path}.system_id",
                "",
            )
        eq_refs = [str(v) for v in (sys_op.get("equation_ids") or []) if v]
        claim_refs = [str(v) for v in (sys_op.get("claim_ids") or []) if v]
        ev_refs = [str(v) for v in (sys_op.get("source_evidence_ids") or []) if v]
        deriv_refs = [str(v) for v in (sys_op.get("derivation_ids") or []) if v]
        if not (eq_refs or claim_refs or ev_refs or deriv_refs):
            add(
                "SYSTEM_OPERATION_NO_SOURCE_REFS",
                f"system operation {sys_id!r} references no equation/claim/evidence/derivation",
                "graph/system_operations.json",
                path,
                sys_id,
            )
        # Cross-artifact ID integrity (issue #394 traceability). A system-level
        # operation must be traceable to real artifacts: a reference that does not
        # resolve is a dangling ref even when the target artifact is entirely empty
        # (an export with zero equations cannot back an equation reference). So we
        # do NOT skip validation when the known set is empty.
        for ref in eq_refs:
            if ref not in equation_ids:
                add("UNRESOLVED_EXPORT_REF", f"{path}.equation_ids references missing equation {ref!r}", "graph/system_operations.json", f"{path}.equation_ids", ref)
        for ref in claim_refs:
            if ref not in claim_ids:
                add("UNRESOLVED_EXPORT_REF", f"{path}.claim_ids references missing claim {ref!r}", "graph/system_operations.json", f"{path}.claim_ids", ref)
        for ref in ev_refs:
            if ref not in evidence_ids:
                add("UNRESOLVED_EXPORT_REF", f"{path}.source_evidence_ids references missing evidence {ref!r}", "graph/system_operations.json", f"{path}.source_evidence_ids", ref)
        for ref in deriv_refs:
            if ref not in derivation_ids:
                add("UNRESOLVED_EXPORT_REF", f"{path}.derivation_ids references missing derivation {ref!r}", "graph/system_operations.json", f"{path}.derivation_ids", ref)
        for ref in (str(v) for v in (sys_op.get("component_ids") or []) if v):
            if ref not in component_ids:
                add("UNRESOLVED_EXPORT_REF", f"{path}.component_ids references missing component {ref!r}", "graph/system_operations.json", f"{path}.component_ids", ref)
        # operation_ids must resolve against the operation graph (issue #400): a
        # system operation that references an operation node which is not exported
        # is not traceable and must be a hard error.
        for ref in (str(v) for v in (sys_op.get("operation_ids") or []) if v):
            if ref not in operation_ids:
                add("UNRESOLVED_EXPORT_REF", f"{path}.operation_ids references missing operation {ref!r}", "graph/system_operations.json", f"{path}.operation_ids", ref)
        # Generic family + provenance-tagged optional subtype (#394).
        fam = str(sys_op.get("system_family") or "")
        if core_families and fam and fam not in core_families:
            warn(
                "NON_GENERIC_SYSTEM_FAMILY",
                f"system operation {sys_id!r} has a non-generic system_family {fam!r}",
                "graph/system_operations.json",
                f"{path}.system_family",
                fam,
            )
        subtype = sys_op.get("system_subtype")
        if subtype and str(subtype) != _UNKNOWN_OPERATION_FAMILY:
            src = str(sys_op.get("subtype_source") or "")
            if src not in _SUBTYPE_SOURCES or src in ("", "unknown"):
                review(
                    "SYSTEM_SUBTYPE_MISSING_PROVENANCE",
                    f"system operation {sys_id!r} has system_subtype {subtype!r} but "
                    f"subtype_source {src!r} is missing or not a known provenance",
                    "graph/system_operations.json",
                    f"{path}.subtype_source",
                    sys_id,
                )
        # Unknown / uncertain system semantics must stay reviewable (#394).
        if fam == _UNKNOWN_OPERATION_FAMILY or sys_op.get("review_required"):
            if not (sys_op.get("review_reasons") or []):
                review(
                    "SYSTEM_OPERATION_REVIEW_WITHOUT_REASON",
                    f"system operation {sys_id!r} requires review but lists no review_reasons",
                    "graph/system_operations.json",
                    f"{path}.review_reasons",
                    sys_id,
                )


def _coverage_metrics(
    *,
    claims: list[dict],
    equations: list[dict],
    components: list[dict],
    derivation_chains: list[dict],
    course_info: dict | None,
    operation_graph: dict,
    component_operation_links: list[dict],
) -> dict:
    """Compute source-link coverage metrics for the export (issue #390).

    Each metric is ``{"total": N, "covered": M}`` so a reviewer can see how much
    of each artifact is actually backed / linked, independent of warnings.
    """
    def ratio(items, predicate) -> dict:
        items = [i for i in (items or []) if isinstance(i, dict)]
        total = len(items)
        covered = sum(1 for i in items if predicate(i))
        return {"total": total, "covered": covered}

    def claim_has_source(c: dict) -> bool:
        return bool(
            c.get("source_evidence_ids")
            or c.get("equation_ids")
            or c.get("linked_equation_ids")
            or c.get("derivation_ids")
            or c.get("source_refs")
        )

    def component_has_evidence(c: dict) -> bool:
        refs = c.get("evidence_refs") if isinstance(c.get("evidence_refs"), dict) else {}
        return bool(
            c.get("evidence_claims")
            or c.get("linked_claim_ids")
            or c.get("linked_equation_ids")
            or refs.get("claim_ids")
            or refs.get("evidence_ids")
        )

    def equation_has_location(e: dict) -> bool:
        loc = e.get("source_location") if isinstance(e.get("source_location"), dict) else {}
        return bool(loc.get("block_id") or loc.get("page") or e.get("source_evidence_ids"))

    def derivation_has_io(c: dict) -> bool:
        if c.get("input_equation_ids") and c.get("output_equation_ids"):
            return True
        for step in c.get("steps") or []:
            if isinstance(step, dict) and step.get("input_equation_ids") and step.get("output_equation_ids"):
                return True
        return False

    topics = (course_info or {}).get("topics") if isinstance(course_info, dict) else []
    op_nodes = [n for n in operation_graph.get("nodes", []) or [] if isinstance(n, dict)]
    linked_op_ids = {
        str(l.get("operation_id") or "")
        for l in (component_operation_links or [])
        if isinstance(l, dict) and l.get("operation_id")
    }
    core_families = _core_operation_families()

    return {
        "claims_with_source_links": ratio(claims, claim_has_source),
        "components_with_evidence_links": ratio(components, component_has_evidence),
        "equations_with_source_locations": ratio(equations, equation_has_location),
        "derivations_with_inputs_outputs": ratio(derivation_chains, derivation_has_io),
        "topics_with_component_links": ratio(topics, lambda t: bool(t.get("linked_component_ids"))),
        "operation_nodes_with_component_links": {
            "total": len(op_nodes),
            "covered": sum(1 for n in op_nodes if str(n.get("operation_id") or "") in linked_op_ids),
        },
        "operation_nodes_with_generic_family": {
            "total": len(op_nodes),
            "covered": sum(
                1 for n in op_nodes
                if str(n.get("operation_family") or "") in core_families
            ) if core_families else 0,
        },
        "unknown_operations_requiring_review": {
            "total": len(op_nodes),
            "covered": sum(
                1 for n in op_nodes
                if str(n.get("operation_family") or "") == _UNKNOWN_OPERATION_FAMILY
                and n.get("review_required")
            ),
        },
    }


def _validate_export_references(
    *,
    claims: list[dict],
    equations: list[dict],
    components: list[dict],
    component_graph: dict,
    course_info: dict | None,
    evidence_snippets: list[dict],
    derivation_chains: list[dict] | None = None,
    completeness_reports: list[dict] | None = None,
    operation_graph: dict | None = None,
    component_operation_links: list[dict] | None = None,
    system_operations: list[dict] | None = None,
    artifact_sources: dict | None = None,
) -> dict:
    errors: list[dict] = []
    warnings: list[dict] = []
    review_items: list[dict] = []
    derivation_chains = derivation_chains or []
    operation_graph = operation_graph or {"nodes": [], "edges": []}
    component_operation_links = component_operation_links or []
    system_operations = system_operations or []
    claim_ids = {str(c.get("claim_id")) for c in claims if c.get("claim_id")}
    component_ids = {str(c.get("component_id")) for c in components if c.get("component_id")}
    evidence_ids = {str(e.get("evidence_id")) for e in evidence_snippets if e.get("evidence_id")}
    equation_ids = {str(e.get("equation_id")) for e in equations if e.get("equation_id")}
    equation_index = {str(e.get("equation_id")): e for e in equations if isinstance(e, dict) and e.get("equation_id")}
    derivation_ids = {str(c.get("derivation_id")) for c in derivation_chains if c.get("derivation_id")}

    def add(code: str, message: str, artifact: str, path: str, ref_id: str) -> None:
        errors.append({"code": code, "message": message, "artifact": artifact, "path": path, "ref_id": ref_id})

    def warn(code: str, message: str, artifact: str, path: str, ref_id: str) -> None:
        warnings.append({"code": code, "message": message, "artifact": artifact, "path": path, "ref_id": ref_id})

    def review(code: str, message: str, artifact: str, path: str, ref_id: str) -> None:
        review_items.append({"code": code, "message": message, "artifact": artifact, "path": path, "ref_id": ref_id})

    def check_refs(values: Any, known: set[str], artifact: str, path: str, target: str) -> None:
        for ref in values or []:
            ref_id = str(ref)
            if _is_legacy_export_ref(ref_id):
                add("LEGACY_EXPORT_REF", f"{path} contains provisional ID {ref_id!r}", artifact, path, ref_id)
            if ref_id not in known:
                add("UNRESOLVED_EXPORT_REF", f"{path} references missing {target} {ref_id!r}", artifact, path, ref_id)

    for idx, eq in enumerate(equations):
        if isinstance(eq, dict):
            check_refs(eq.get("linked_claim_ids"), claim_ids, "equations/equations.json", f"$.equations[{idx}].linked_claim_ids", "claim")
            check_refs(eq.get("source_evidence_ids"), evidence_ids, "equations/equations.json", f"$.equations[{idx}].source_evidence_ids", "evidence")

    # Claim cross-artifact ID integrity (issue #390 / #400). A claim whose only
    # links are dangling IDs must be a hard error, not a silently publish-ready
    # claim. Validate every forward/back reference a claim can carry.
    for idx, claim in enumerate(claims):
        if not isinstance(claim, dict):
            continue
        cpath = f"$.claims[{idx}]"
        check_refs(claim.get("source_evidence_ids"), evidence_ids, "claims/claims.json", f"{cpath}.source_evidence_ids", "evidence")
        # _claim_equation_refs() aggregates the nested ``equation.equation_ids`` /
        # ``equation.equation_id`` as well as the top-level equation_ids /
        # linked_equation_ids, so DB-fallback claims that only carry the nested
        # form are validated too (issue #400). inferred_equation_ids are checked
        # separately so weak/inferred links cannot reference missing equations.
        check_refs(_claim_equation_refs(claim), equation_ids, "claims/claims.json", f"{cpath}.equation_refs", "equation")
        check_refs(claim.get("inferred_equation_ids"), equation_ids, "claims/claims.json", f"{cpath}.inferred_equation_ids", "equation")
        check_refs(claim.get("derivation_ids"), derivation_ids, "claims/claims.json", f"{cpath}.derivation_ids", "derivation")
        check_refs(claim.get("linked_component_ids"), component_ids, "claims/claims.json", f"{cpath}.linked_component_ids", "component")

    for idx, comp in enumerate(components):
        if not isinstance(comp, dict):
            continue
        check_refs(comp.get("evidence_claims"), claim_ids, "components/components.json", f"$.components[{idx}].evidence_claims", "claim")
        # Component cross-artifact ID integrity (issue #390 / #400): a component
        # must reference only real claims / equations / evidence / derivations,
        # including nested evidence_refs and every equation-role field.
        cbase = f"$.components[{idx}]"
        check_refs(comp.get("linked_claim_ids"), claim_ids, "components/components.json", f"{cbase}.linked_claim_ids", "claim")
        check_refs(comp.get("linked_evidence_ids"), evidence_ids, "components/components.json", f"{cbase}.linked_evidence_ids", "evidence")
        check_refs(comp.get("linked_derivation_ids"), derivation_ids, "components/components.json", f"{cbase}.linked_derivation_ids", "derivation")
        # _component_equation_refs_export() aggregates every equation reference a
        # component can carry — the equation-role fields, nested
        # ``evidence_refs.equation_ids`` and inputs/outputs equation IDs — so a
        # DB-fallback component referencing missing equations is caught (#400).
        check_refs(_component_equation_refs_export(comp), equation_ids, "components/components.json", f"{cbase}.equation_refs", "equation")
        comp_evidence_refs = comp.get("evidence_refs") if isinstance(comp.get("evidence_refs"), dict) else {}
        check_refs(comp_evidence_refs.get("claim_ids"), claim_ids, "components/components.json", f"{cbase}.evidence_refs.claim_ids", "claim")
        check_refs(comp_evidence_refs.get("evidence_ids"), evidence_ids, "components/components.json", f"{cbase}.evidence_refs.evidence_ids", "evidence")
        comp_gate = comp.get("confidence_gate") if isinstance(comp.get("confidence_gate"), dict) else {}
        if comp_gate.get("blocked_by_equation_ids"):
            warn(
                "COMPONENT_CONFIDENCE_GATE_BLOCKED",
                f"component {comp.get('component_id')!r} is downgraded by low-confidence equations",
                "components/components.json",
                f"$.components[{idx}].confidence_gate",
                str(comp.get("component_id") or ""),
            )
            if comp.get("review_status") in ("source_backed", "auto_accepted", "teacher_reviewed"):
                add(
                    "SOURCE_BACKED_COMPONENT_USES_BLOCKED_EQUATION",
                    f"component {comp.get('component_id')!r} cannot be source_backed with blocked equations",
                    "components/components.json",
                    f"$.components[{idx}].review_status",
                    str(comp.get("component_id") or ""),
                )
        output_eqs = [str(v) for v in comp.get("output_equation_ids") or [] if v]
        if comp.get("component_type") in ("RelationComponent", "PaperRelationComponent") and comp.get("publish_ready"):
            for eq_id in output_eqs:
                eq = equation_index.get(eq_id) or {}
                src_loc = eq.get("source_location") if isinstance(eq.get("source_location"), dict) else {}
                if not eq.get("latex") or not src_loc.get("block_id"):
                    add(
                        "FINAL_RELATION_COMPONENT_UNCONFIRMED_EQUATION",
                        f"publish-ready relation component {comp.get('component_id')!r} has output equation {eq_id!r} without confirmed LaTeX/source location",
                        "components/components.json",
                        f"$.components[{idx}].output_equation_ids",
                        eq_id,
                    )
        for dep_idx, dep in enumerate(comp.get("dependencies") or []):
            if isinstance(dep, dict):
                check_refs(dep.get("component_refs"), component_ids, "components/components.json", f"$.components[{idx}].dependencies[{dep_idx}].component_refs", "component")

        # Component granularity / responsibility coverage (issue #392). A component
        # spanning several distinct generic operation families without a split
        # recommendation has likely collapsed multiple responsibilities (definition
        # + model + derivation + ...) and must be reviewed (or split) before it is
        # trusted as a single reusable unit. Domain-neutral: families only.
        comp_id = str(comp.get("component_id") or "")
        families = {
            str(f) for f in (
                [comp.get("primary_operation") or comp.get("operation")]
                + list(comp.get("secondary_operations") or [])
            )
            if f and str(f) not in ("", _UNKNOWN_OPERATION_FAMILY)
        }
        split_rec = comp.get("split_recommendation") if isinstance(comp.get("split_recommendation"), dict) else {}
        if len(families) > 1 and not split_rec.get("required"):
            review(
                "COMPONENT_MULTIPLE_RESPONSIBILITIES",
                f"component {comp_id!r} spans multiple operation families {sorted(families)} "
                "without a split recommendation; distinct responsibilities may be collapsed",
                "components/components.json",
                f"$.components[{idx}].secondary_operations",
                comp_id,
            )

    for idx, edge in enumerate(component_graph.get("edges", []) or []):
        if not isinstance(edge, dict):
            continue
        for key in ("source", "target"):
            ref_id = str(edge.get(key) or "")
            if not ref_id:
                add("EMPTY_COMPONENT_GRAPH_ENDPOINT", f"$.edges[{idx}].{key} is empty", "graph/component_graph.json", f"$.edges[{idx}].{key}", ref_id)
            elif _is_legacy_export_ref(ref_id):
                add("LEGACY_EXPORT_REF", f"$.edges[{idx}].{key} contains provisional ID {ref_id!r}", "graph/component_graph.json", f"$.edges[{idx}].{key}", ref_id)
            elif ref_id not in component_ids:
                add("UNRESOLVED_EXPORT_REF", f"$.edges[{idx}].{key} references missing component {ref_id!r}", "graph/component_graph.json", f"$.edges[{idx}].{key}", ref_id)
        check_refs(edge.get("evidence_claims"), claim_ids, "graph/component_graph.json", f"$.edges[{idx}].evidence_claims", "claim")

    # Operation-graph separation (issue #387): operation IDs must never appear as
    # component-graph node IDs, and component_operation_links endpoints must both
    # resolve. The component_graph is built component-only by construction; these
    # checks catch regressions / mixed legacy graphs.
    operation_ids = {
        str(n.get("operation_id") or n.get("node_id") or n.get("id") or "")
        for n in operation_graph.get("nodes", []) or []
        if isinstance(n, dict)
    }
    operation_ids.discard("")
    for idx, node in enumerate(component_graph.get("nodes", []) or []):
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("node_id") or node.get("component_id") or "")
        if not node_id:
            add(
                "EMPTY_COMPONENT_GRAPH_NODE_ID",
                f"component graph node at index {idx} has no node_id",
                "graph/component_graph.json",
                f"$.nodes[{idx}].node_id",
                "",
            )
        elif node_id in operation_ids:
            add(
                "OPERATION_ID_IN_COMPONENT_GRAPH",
                f"component graph node {node_id!r} is an operation ID and must live in operation_graph.json",
                "graph/component_graph.json",
                f"$.nodes[{idx}].node_id",
                node_id,
            )
        elif _is_legacy_export_ref(node_id):
            add(
                "LEGACY_EXPORT_REF",
                f"component graph node {node_id!r} is a provisional ID",
                "graph/component_graph.json",
                f"$.nodes[{idx}].node_id",
                node_id,
            )
        elif node_id not in component_ids:
            # Hard error (issues #390 / #393): a component_graph node must be a
            # known component_id from components/components.json — never a ghost.
            # No empty-set guard: if no components were exported, every graph node
            # is a ghost by definition.
            add(
                "COMPONENT_GRAPH_NODE_NOT_A_COMPONENT",
                f"component graph node {node_id!r} is not a known component in components/components.json",
                "graph/component_graph.json",
                f"$.nodes[{idx}].node_id",
                node_id,
            )
    for idx, link in enumerate(component_operation_links):
        if not isinstance(link, dict):
            continue
        comp_id = str(link.get("component_id") or "")
        op_id = str(link.get("operation_id") or "")
        # Empty endpoints (issue #400): a link with an empty component_id or
        # operation_id references neither a valid component nor a valid operation
        # and must be a hard error, not silently skipped.
        if not comp_id:
            add(
                "EMPTY_COMPONENT_OPERATION_LINK_ENDPOINT",
                f"component_operation_links[{idx}].component_id is empty",
                "graph/component_operation_links.json",
                f"$.links[{idx}].component_id",
                "",
            )
        elif comp_id not in component_ids:
            add(
                "UNRESOLVED_EXPORT_REF",
                f"component_operation_links[{idx}].component_id references missing component {comp_id!r}",
                "graph/component_operation_links.json",
                f"$.links[{idx}].component_id",
                comp_id,
            )
        if not op_id:
            add(
                "EMPTY_COMPONENT_OPERATION_LINK_ENDPOINT",
                f"component_operation_links[{idx}].operation_id is empty",
                "graph/component_operation_links.json",
                f"$.links[{idx}].operation_id",
                "",
            )
        elif op_id not in operation_ids:
            add(
                "UNRESOLVED_EXPORT_REF",
                f"component_operation_links[{idx}].operation_id references missing operation {op_id!r}",
                "graph/component_operation_links.json",
                f"$.links[{idx}].operation_id",
                op_id,
            )

    # Operation-graph connectivity (issue #398): a graph with many isolated
    # operation nodes is incomplete even if extraction succeeded, and equations /
    # evidence should be able to reach a claim/component through operation links.
    op_nodes = [n for n in operation_graph.get("nodes", []) or [] if isinstance(n, dict)]
    if op_nodes:
        linked_op_ids: set[str] = set()
        for edge in operation_graph.get("edges", []) or []:
            if isinstance(edge, dict):
                linked_op_ids.add(str(edge.get("source") or ""))
                linked_op_ids.add(str(edge.get("target") or ""))
        for link in component_operation_links:
            if isinstance(link, dict):
                linked_op_ids.add(str(link.get("operation_id") or ""))
        isolated = [
            str(n.get("operation_id") or "")
            for n in op_nodes
            if str(n.get("operation_id") or "") and str(n.get("operation_id")) not in linked_op_ids
        ]
        # Isolated above ~half of all operations (and at least 3) signals a sparse,
        # incomplete operation graph.
        if len(isolated) >= 3 and len(isolated) * 2 >= len(op_nodes):
            warn(
                "ISOLATED_OPERATION_NODES",
                f"{len(isolated)} of {len(op_nodes)} operation nodes are isolated "
                "(no operation edge and no component link); operation graph looks incomplete",
                "graph/operation_graph.json",
                "$.nodes",
                str(len(isolated)),
            )
        # Operations exist but none connect to a component: equations/evidence
        # carried by operations cannot reach any component.
        if not any(isinstance(l, dict) and l.get("operation_id") for l in component_operation_links):
            op_equations = sorted({
                str(e) for n in op_nodes for e in (n.get("linked_equation_ids") or []) if e
            })
            if op_equations:
                warn(
                    "EQUATIONS_UNREACHABLE_THROUGH_OPERATIONS",
                    "operation nodes carry equations but no component_operation_links exist; "
                    "equations/evidence cannot reach any component through operations",
                    "graph/component_operation_links.json",
                    "$.links",
                    str(len(op_equations)),
                )
        # Operation families must come from the stable, generic core set (never a
        # paper-specific core key, issue #398).
        core_families = _core_operation_families()
        for idx, n in enumerate(op_nodes):
            op_id = str(n.get("operation_id") or "")
            fam = str(n.get("operation_family") or "")
            if core_families and fam and fam not in core_families:
                warn(
                    "NON_GENERIC_OPERATION_FAMILY",
                    f"operation node {op_id!r} has a non-generic operation_family {fam!r}",
                    "graph/operation_graph.json",
                    f"$.nodes[{idx}].operation_family",
                    fam,
                )
            # Two-layer operation model (issues #393 / #390): an operation_subtype
            # is optional metadata, but when present it must carry honest
            # provenance. A subtype without a valid subtype_source is a review item
            # (fabricated / unverifiable subtype provenance must not pass silently).
            subtype = n.get("operation_subtype")
            if subtype and str(subtype) != _UNKNOWN_OPERATION_FAMILY:
                src = str(n.get("subtype_source") or "")
                if src not in _SUBTYPE_SOURCES or src in ("", "unknown"):
                    review(
                        "OPERATION_SUBTYPE_MISSING_PROVENANCE",
                        f"operation node {op_id!r} has operation_subtype {subtype!r} "
                        f"but subtype_source {src!r} is missing or not a known provenance",
                        "graph/operation_graph.json",
                        f"$.nodes[{idx}].subtype_source",
                        op_id,
                    )
            # Unknown operation families must remain reviewable: they may be kept
            # (information is never dropped) but only with review_required +
            # review_reasons, never silently passed as source-backed (#393).
            if fam == _UNKNOWN_OPERATION_FAMILY:
                if not n.get("review_required") or not (n.get("review_reasons") or []):
                    review(
                        "UNKNOWN_OPERATION_NOT_REVIEWABLE",
                        f"operation node {op_id!r} has unknown operation_family but is not "
                        "marked review_required with review_reasons",
                        "graph/operation_graph.json",
                        f"$.nodes[{idx}].review_required",
                        op_id,
                    )

    # Component coverage / granularity (issue #392): a theory-heavy paper with
    # many source-backed equations but only a handful of components is almost
    # certainly over-compressed (definition / model / equation system / derivation
    # / constraint / application collapsed into one). Surfaced as a review warning
    # so the bundle is exportable but flagged for refinement.
    source_backed_equation_count = 0
    for eq in equations:
        if not isinstance(eq, dict):
            continue
        src_loc = eq.get("source_location") if isinstance(eq.get("source_location"), dict) else {}
        if eq.get("latex") and src_loc.get("block_id"):
            source_backed_equation_count += 1
    if source_backed_equation_count >= 15 and len(components) <= 4:
        warn(
            "FEW_COMPONENTS_FOR_SOURCE_BACKED_EQUATIONS",
            (
                f"{source_backed_equation_count} source-backed equations but only "
                f"{len(components)} component(s); distinct responsibilities are "
                "likely collapsed and should be refined"
            ),
            "components/components.json",
            "$.components",
            str(len(components)),
        )

    if isinstance(course_info, dict):
        has_derivation_topic = False
        for idx, topic in enumerate(course_info.get("topics") or []):
            if not isinstance(topic, dict):
                continue
            has_derivation_topic = has_derivation_topic or _topic_mentions_derivation(topic)
            topic_gate = topic.get("confidence_gate") if isinstance(topic.get("confidence_gate"), dict) else {}
            if topic_gate.get("blocked_by_equation_ids"):
                warn(
                    "COURSE_TOPIC_CONFIDENCE_GATE_BLOCKED",
                    f"course topic {idx} references low-confidence equations; final formula rendering is not allowed",
                    "course_info.json",
                    f"$.topics[{idx}].confidence_gate",
                    str(topic_gate.get("blocked_by_equation_ids")),
                )
            check_refs(topic.get("linked_component_ids"), component_ids, "course_info.json", f"$.topics[{idx}].linked_component_ids", "component")
            check_refs(topic.get("linked_derivation_ids"), derivation_ids, "course_info.json", f"$.topics[{idx}].linked_derivation_ids", "derivation")
            # Every topic must link to at least one component (issue #389). An
            # empty-component topic is a teaching-export warning, not a hard error.
            if not (topic.get("linked_component_ids") or []):
                warn(
                    "COURSE_TOPIC_WITHOUT_COMPONENT",
                    f"course topic {idx} ({topic.get('title')!r}) has no linked components",
                    "course_info.json",
                    f"$.topics[{idx}].linked_component_ids",
                    str(topic.get("topic_id") or idx),
                )
            check_refs(topic.get("linked_claim_ids"), claim_ids, "course_info.json", f"$.topics[{idx}].linked_claim_ids", "claim")
            check_refs(topic.get("linked_equation_ids"), equation_ids, "course_info.json", f"$.topics[{idx}].linked_equation_ids", "equation")
            for step_idx, step in enumerate(topic.get("visualization_plan") or []):
                if isinstance(step, dict):
                    check_refs(step.get("linked_component_ids"), component_ids, "course_info.json", f"$.topics[{idx}].visualization_plan[{step_idx}].linked_component_ids", "component")
        if equation_ids and has_derivation_topic and not derivation_chains:
            add("EMPTY_DERIVATION_CHAINS", "course has derivation topic and equations, but derivation chains are empty", "derivations/derivation_chains.json", "$.chains", "")

    for chain_idx, chain in enumerate(derivation_chains):
        if not isinstance(chain, dict):
            continue
        steps = chain.get("steps") or []
        if not steps:
            add("EMPTY_DERIVATION_STEPS", f"derivation {chain.get('derivation_id')!r} has no steps", "derivations/derivation_chains.json", f"$.chains[{chain_idx}].steps", str(chain.get("derivation_id") or ""))
            continue
        for step_idx, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            path = f"$.chains[{chain_idx}].steps[{step_idx}]"
            input_eqs = step.get("input_equation_ids") or []
            output_eqs = step.get("output_equation_ids") or []
            step_gate = step.get("confidence_gate") if isinstance(step.get("confidence_gate"), dict) else {}
            if step_gate.get("blocked_by_equation_ids"):
                warn(
                    "DERIVATION_STEP_CONFIDENCE_GATE_BLOCKED",
                    f"{path} uses equations that cannot support derivation",
                    "derivations/derivation_chains.json",
                    f"{path}.confidence_gate",
                    str(step_gate.get("blocked_by_equation_ids")),
                )
                if step.get("review_status") in ("auto_accepted", "source_backed"):
                    add(
                        "PUBLISH_READY_DERIVATION_STEP_USES_BLOCKED_EQUATION",
                        f"{path} is publish-ready/source-backed but uses blocked equations",
                        "derivations/derivation_chains.json",
                        f"{path}.review_status",
                        str(step_gate.get("blocked_by_equation_ids")),
                    )
            step_claims = []
            for key in ("claim_ids", "required_claim_ids", "input_claim_ids", "output_claim_ids"):
                step_claims.extend(step.get(key) or [])
            step_evidence = step.get("source_evidence_ids") or []
            if not (input_eqs or output_eqs or step_claims or step_evidence):
                add("DERIVATION_STEP_UNLINKED", f"{path} has no equation, claim, or evidence links", "derivations/derivation_chains.json", path, "")
            check_refs(input_eqs, equation_ids, "derivations/derivation_chains.json", f"{path}.input_equation_ids", "equation")
            check_refs(output_eqs, equation_ids, "derivations/derivation_chains.json", f"{path}.output_equation_ids", "equation")
            check_refs(step_claims, claim_ids, "derivations/derivation_chains.json", f"{path}.claim_ids", "claim")
            check_refs(step_evidence, evidence_ids, "derivations/derivation_chains.json", f"{path}.source_evidence_ids", "evidence")

    # Document completeness gate (#366 / #371): a truncated ingest (missing
    # equation labels, absent Conclusion, or a body that never reached the
    # document end) is surfaced as warnings so the bundle is exportable for
    # review but never promoted to publish_ready. EvidenceRegistry sparseness is
    # audit-only (issue #371) and is never raised as a blocking warning here.
    completeness = _build_completeness_report(completeness_reports)
    for doc in completeness["documents"]:
        if doc["complete"]:
            continue
        doc_id = doc.get("document_id") or ""
        if doc["missing_equation_labels"]:
            warn(
                "DOCUMENT_EQUATION_LABEL_DISCONTINUITY",
                f"document {doc_id!r} is missing equation labels {doc['missing_equation_labels']}",
                "document_boundary.json",
                "$.completeness.equation_label_continuity",
                doc_id,
            )
        if not doc["terminal_section_present"]:
            warn(
                "DOCUMENT_TERMINAL_SECTION_MISSING",
                f"document {doc_id!r} has no terminal (Conclusion/Summary) section; ingest may be truncated",
                "document_boundary.json",
                "$.completeness.terminal_section",
                doc_id,
            )
        if "ingest_incomplete" in doc["review_reasons"]:
            warn(
                "DOCUMENT_INGEST_INCOMPLETE",
                (
                    f"document {doc_id!r} ingest did not reach the document end: last "
                    f"ingested page {doc['last_ingested_page']} of {doc['pages_total']}; "
                    f"trailing un-ingested ranges {doc['trailing_uningested_page_ranges']}"
                ),
                "document_boundary.json",
                "$.completeness.ingest_coverage",
                doc_id,
            )
        if doc["tail_truncation_suspected"]:
            warn(
                "DOCUMENT_TAIL_TRUNCATION_SUSPECTED",
                (
                    f"document {doc_id!r} tail truncation suspected (confidence "
                    f"{doc['tail_truncation_confidence']}); signals "
                    f"{doc['tail_truncation_signals']}"
                ),
                "document_boundary.json",
                "$.completeness.tail_truncation",
                doc_id,
            )

    # System-level operation artifacts (issue #394): a first-class artifact that
    # bundles several equations / claims / derivations / operations must stay
    # source-backed and traceable. Domain-neutral: families are validated against
    # CORE_OPERATION_FAMILIES, never a paper-specific taxonomy.
    _validate_system_operations(
        system_operations,
        core_families=_core_operation_families(),
        equation_ids=equation_ids,
        claim_ids=claim_ids,
        evidence_ids=evidence_ids,
        derivation_ids=derivation_ids,
        component_ids=component_ids,
        operation_ids=operation_ids,
        add=add,
        warn=warn,
        review=review,
    )

    # Fallback provenance (issue #390): DB-persisted / fallback data was used in
    # place of a current-run artifact. This is a quality warning (the export is
    # not built purely from current-run analysis), so it must keep the run out of
    # publish_ready while remaining exportable for review.
    sources = artifact_sources if isinstance(artifact_sources, dict) else {}
    fallback_used = bool(sources.get("fallback_used")) or bool(sources.get("fallback_sources"))
    if fallback_used:
        fb = sources.get("fallback_sources") or []
        artifacts_fellback = sorted({str((f or {}).get("artifact") or "") for f in fb if isinstance(f, dict)})
        warn(
            "FALLBACK_DATA_USED",
            "export used fallback/DB-persisted data instead of current-run artifacts"
            + (f" for {artifacts_fellback}" if artifacts_fellback else ""),
            "manifest.json",
            "$.fallback_sources",
            ",".join(artifacts_fellback),
        )

    # Coverage metrics (issue #390): explain *why* an export is or is not
    # publish-ready by reporting how much of each artifact is source-linked.
    coverage = _coverage_metrics(
        claims=claims,
        equations=equations,
        components=components,
        derivation_chains=derivation_chains,
        course_info=course_info,
        operation_graph=operation_graph,
        component_operation_links=component_operation_links,
    )

    # publish_ready is the strict gate: no hard errors, no quality warnings, no
    # outstanding review items (#390), and a complete ingest. exportable is the
    # looser gate (no hard errors) so review bundles can still be downloaded.
    publish_ready = (
        not errors
        and not warnings
        and not review_items
        and completeness["all_documents_complete"]
    )
    if errors:
        status = "failed_validation"
    elif review_items:
        status = "needs_review"
    elif warnings or not completeness["all_documents_complete"]:
        status = "passed_with_warnings"
    else:
        status = "passed"

    return {
        "status": status,
        "exportable": not errors,
        "publish_ready": publish_ready,
        "errors": errors,
        "warnings": warnings,
        "review_items": review_items,
        "completeness": completeness,
        "coverage": coverage,
        "artifact_sources": artifact_sources or {},
        "summary": {
            "error_count": len(errors),
            "warning_count": len(warnings),
            "review_required_count": len(review_items),
            "unresolved_reference_count": len(errors),
        },
    }


def _build_manifest(
    export_id: str,
    scope_type: str,
    scope_id: str,
    material_ids: list[str],
    document_ids: list[str],
    claims: list[dict],
    dsl_graph: dict,
    components: list[dict],
    component_graph: dict,
    evidence_snippets: list[dict],
    options: dict,
    equations: list[dict] | None = None,
    equation_candidates: list[dict] | None = None,
    derivation_chains: list[dict] | None = None,
    document_boundaries: list[dict] | None = None,
    operation_graph: dict | None = None,
    component_operation_links: list[dict] | None = None,
    system_operations: list[dict] | None = None,
    export_source: dict | None = None,
) -> dict:
    equations = equations or []
    equation_candidates = equation_candidates or []
    derivation_chains = derivation_chains or []
    document_boundaries = document_boundaries or []
    operation_graph = operation_graph or {"nodes": [], "edges": []}
    component_operation_links = component_operation_links or []
    system_operations = system_operations or []
    return {
        "export_schema_version": "0.2.0",
        "exported_at": _now_iso(),
        "export_id": export_id,
        "app": {"name": "episteme-graph", "version": "unknown", "git_commit": "unknown"},
        # Artifact-first export provenance (issue #383). ``fallback_used`` /
        # ``fallback_sources`` make it explicit which artifacts were missing and
        # what DB object the dump fell back to.
        "export_source_policy": (export_source or {}).get("export_source_policy", "artifact_first"),
        "artifact_run_id": (export_source or {}).get("artifact_run_id", ""),
        "artifact_run_ids": (export_source or {}).get("artifact_run_ids", {}),
        "fallback_used": (export_source or {}).get("fallback_used", False),
        "fallback_sources": (export_source or {}).get("fallback_sources", []),
        "scope": {
            "type": scope_type,
            f"{scope_type}_id": scope_id,
            "material_ids": material_ids,
            "document_ids": document_ids,
        },
        "files": {
            "course_info": "course_info.json",
            "export_validation": "export_validation.json",
            "claims": "claims/claims.json",
            "dsl_graph": "dsl/dsl_graph.json",
            "components": "components/components.json",
            "component_graph": "graph/component_graph.json",
            "operation_graph": "graph/operation_graph.json",
            "component_operation_links": "graph/component_operation_links.json",
            "system_operations": "graph/system_operations.json",
            "evidence_snippets": "evidence/evidence_snippets.json",
            "equations": "equations/equations.json",
            "equation_candidates": "equations/equation_candidates.json",
            "derivation_chains": "derivations/derivation_chains.json",
            "document_boundary": "document_boundary.json",
        },
        "counts": {
            "claims": len(claims),
            "dsl_nodes": len(dsl_graph.get("nodes", [])),
            "dsl_edges": len(dsl_graph.get("edges", [])),
            "components": len(components),
            "component_edges": len(component_graph.get("edges", [])),
            "operation_nodes": len(operation_graph.get("nodes", [])),
            "operation_edges": len(operation_graph.get("edges", [])),
            "component_operation_links": len(component_operation_links),
            "system_operations": len(system_operations),
            "evidence_snippets": len(evidence_snippets),
            "equations": len(equations),
            "equation_candidates": len(equation_candidates),
            "derivation_chains": len(derivation_chains),
            "document_boundaries": len(document_boundaries),
        },
        "options": options,
    }


_README_TEMPLATE = """\
# episteme-graph Export Bundle

This ZIP contains machine-readable outputs generated by episteme-graph.

## Contents

- `manifest.json`: index of this export bundle
- `course_info.json`: course metadata, topics (with learning_objectives, prerequisite_concepts, blackbox_policy, expected_misconceptions, assessment_prompts, visualization_plan), chapters, and source materials
- `claims/claims.json`: extracted claims from source documents
- `dsl/dsl_graph.json`: lightweight logical graph representation (DSL)
- `components/components.json`: reusable logical/theory components (Component)
- `graph/component_graph.json`: reusable-component dependency graph. Nodes are component IDs only (issue #387); operation-level nodes are kept out.
- `graph/operation_graph.json`: lower-level operation/equation-operation graph (nodes are operation IDs). Related to but distinct from the component graph.
- `graph/component_operation_links.json`: optional mapping from component IDs to operation IDs (`links[].component_id` / `links[].operation_id`).
- `graph/system_operations.json`: first-class system-level operation artifacts. Each bundles a group of equations/claims/derivations into one explainable operation with a generic `system_family` (+ optional provenance-tagged `system_subtype`). Domain-neutral; unknown semantics stay `review_required`.
- `evidence/evidence_snippets.json`: source-backed Evidence (PDF spans). `evidence_text` is PDF-derived text; LLM commentary is kept in `analysis_note` / `review_note`. `extraction_source`, `extraction_status`, `needs_review`, `review_reason` audit the provenance.
- `equations/equations.json`: first-class equation registry. Each entry has `latex`, `plain_text`, `source_location`, `equation_type`, `defined_symbols`, `used_symbols`, `input_equation_ids`, `output_equation_ids`, `extraction_source`, `extraction_status`, `needs_math_review`, `review_reason`, `candidate_trace_ids`.
- `equations/equation_candidates.json`: audit trail for equation candidate detection. Each entry records `raw_text`, `source_location`, `detection_method`, `matched_label`, `acceptance_status`, `accepted_equation_id`, `rejection_reason`.
- `derivations/derivation_chains.json`: derivation steps linking equations / claims with `operation`, `input_equation_ids`, `output_equation_ids`, `assumption_refs`.
- `document_boundary.json`: per-document active article boundary (page_start, page_end, confidence, needs_review). Useful for multi-article PDFs (journal scans, conference proceedings). A collapsed boundary (span ≪ pages_total) drops confidence below 1.0 and raises needs_review. `authors` are extracted at parse time from front-matter / structured metadata only and are surfaced verbatim with their `author_extraction` provenance (source / confidence / needs_review); the export layer never deletes authors, only flags anomalies (count over limit, missing provenance). A `completeness` block reports equation-label continuity, terminal-section presence, and `ingest_coverage` (did the DocumentStructure ingest reach the document end). The EvidenceRegistry page distribution is reported separately under `evidence_page_distribution` as audit-only metadata.
- `export_validation.json`: deterministic cross-artifact validation. The `completeness` section aggregates per-document ingest reachability (missing equation labels, trailing un-ingested page ranges, terminal-section presence); a document whose ingest did not reach the document end keeps the bundle out of `publish_ready`. EvidenceRegistry sparseness is audit-only and never blocks publish on its own.

## Data Model Summary

The pipeline is:

Source Document
→ Claim Extraction
→ DSL Extraction
→ Component Assembly
→ Component Graph Generation
→ Review

## Important Distinctions

- **Claim**: minimal source-backed statement extracted from a document
- **DSL**: lightweight intermediate graph representation of logical relations
- **Component**: reusable knowledge unit with inputs, outputs, preconditions, cautions, and dependencies
- **Component Graph**: connections among components

## Recommended External AI Prompt

Please review this episteme-graph export bundle.

Focus on:

1. Whether claims are grounded in evidence snippets
2. Whether DSL edges correctly represent claim-level logical relations
3. Whether components are reusable knowledge units rather than summaries
4. Whether component inputs, outputs, preconditions, cautions, and dependencies are meaningful
5. Whether graph edges between components are valid
6. Whether any claims or components should be merged, split, rejected, or revised

Return your review as JSON with:

- issues
- suggested_fixes
- confidence
- affected_claim_ids
- affected_component_ids
- affected_edge_ids

## Notes

- LLM-generated review fields should be treated as provisional.
- `teacher_review_required` means the item has not yet been approved by a human expert.
- `source_backed` means the item is explicitly supported by source text.
- `source_inferred` means the item is naturally inferred from source text.
"""


def _build_zip(
    manifest: dict,
    claims: list[dict],
    dsl_graph: dict,
    components: list[dict],
    component_graph: dict,
    evidence_snippets: list[dict],
    course_info: dict | None = None,
    include_ndjson: bool = False,
    include_debug: bool = False,
    debug_data: dict | None = None,
    equations: list[dict] | None = None,
    equation_candidates: list[dict] | None = None,
    derivation_chains: list[dict] | None = None,
    document_boundaries: list[dict] | None = None,
    export_validation: dict | None = None,
    operation_graph: dict | None = None,
    component_operation_links: list[dict] | None = None,
    system_operations: list[dict] | None = None,
) -> bytes:
    equations = equations or []
    equation_candidates = equation_candidates or []
    derivation_chains = derivation_chains or []
    document_boundaries = document_boundaries or []
    operation_graph = operation_graph or {"nodes": [], "edges": []}
    component_operation_links = component_operation_links or []
    system_operations = system_operations or []
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("README.md", _README_TEMPLATE)
        zf.writestr("manifest.json", _json_bytes(manifest))
        zf.writestr("export_validation.json", _json_bytes(export_validation if export_validation is not None else {}))
        zf.writestr("course_info.json", _json_bytes(course_info if course_info is not None else {}))
        zf.writestr("claims/claims.json", _json_bytes({"claims": claims}))
        zf.writestr(
            "dsl/dsl_graph.json",
            _json_bytes({**dsl_graph, "dsl_schema_version": dsl_graph.get("dsl_schema_version", "0.1.0")}),
        )
        zf.writestr("components/components.json", _json_bytes({"component_schema_version": "0.1.0", "components": components}))
        zf.writestr(
            "graph/component_graph.json",
            _json_bytes({**component_graph, "graph_schema_version": component_graph.get("graph_schema_version", "0.1.0")}),
        )
        # Operation-level graph kept separate from the component graph (issue #387)
        # so component_graph.json nodes are component IDs only.
        zf.writestr(
            "graph/operation_graph.json",
            _json_bytes({**operation_graph, "graph_schema_version": operation_graph.get("graph_schema_version", "0.1.0")}),
        )
        zf.writestr(
            "graph/component_operation_links.json",
            _json_bytes({"schema_version": "0.1.0", "links": component_operation_links}),
        )
        # System-level operation artifacts (issue #394): first-class artifacts that
        # bundle a group of equations/claims/derivations into one explainable
        # operation with the generic two-layer (family + optional subtype) model.
        zf.writestr(
            "graph/system_operations.json",
            _json_bytes({"schema_version": "0.1.0", "system_operations": system_operations}),
        )
        zf.writestr("evidence/evidence_snippets.json", _json_bytes({
            "evidence_schema_version": "0.2.0",
            "snippets": evidence_snippets,
        }))
        zf.writestr("equations/equations.json", _json_bytes({
            "equation_schema_version": "0.1.0",
            "equations": equations,
        }))
        zf.writestr("equations/equation_candidates.json", _json_bytes({
            "candidate_schema_version": "0.1.0",
            "candidates": equation_candidates,
        }))
        zf.writestr("derivations/derivation_chains.json", _json_bytes({
            "derivation_schema_version": "0.1.0",
            "chains": derivation_chains,
        }))
        zf.writestr("document_boundary.json", _json_bytes({
            "boundary_schema_version": "0.1.0",
            "boundaries": document_boundaries,
        }))

        if include_ndjson:
            zf.writestr("claims/claims.ndjson", _ndjson_bytes(claims))
            zf.writestr("components/components.ndjson", _ndjson_bytes(components))

        if include_debug and debug_data:
            zf.writestr("debug/validation_errors.json", _json_bytes(debug_data.get("validation_errors", [])))
            zf.writestr("debug/pipeline_logs.json", _json_bytes(debug_data.get("pipeline_logs", [])))
            if debug_data.get("component_assembly_artifacts") is not None:
                zf.writestr(
                    "debug/component_assembly_artifact.json",
                    _json_bytes({"documents": debug_data.get("component_assembly_artifacts", [])}),
                )
            if debug_data.get("llm_prompts") is not None:
                zf.writestr("debug/llm_prompts.json", _json_bytes(debug_data.get("llm_prompts", [])))
            if debug_data.get("llm_raw_outputs") is not None:
                zf.writestr("debug/llm_raw_outputs.json", _json_bytes(debug_data.get("llm_raw_outputs", [])))

    buffer.seek(0)
    return buffer.read()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/api/courses/{course_id}/export-bundle")
def export_course_bundle(
    course_id: str,
    req: ExportBundleRequest = ExportBundleRequest(),
    current_user: dict = Depends(_require_teacher),
) -> StreamingResponse:
    """コース単位でエクスポートZIPを生成してダウンロードする。"""
    session = _pg_session()
    try:
        course = _load_course(session, course_id)
        if not course:
            raise HTTPException(status_code=404, detail="Course not found")

        document_ids = _get_document_ids_for_course(session, course_id)

        dsl_graph = _load_dsl_graph_for_course(session, course_id, document_ids)
        db_claims = _load_claims_for_course(session, course_id, document_ids)
        db_components = _load_components_for_course(session, course_id, document_ids)
        db_component_graph = _load_component_graph_for_course(session, course_id, document_ids)

        artifacts_by_doc = _load_analysis_artifacts(session, document_ids)
        # Artifact-first export (issue #383): prefer latest-run stage artifacts,
        # falling back to DB-persisted objects only when an artifact is missing.
        fallback_sources: list[dict] = []
        claims = _resolve_artifact_first_claims(artifacts_by_doc, document_ids, db_claims, fallback_sources)
        components = _resolve_artifact_first_components(artifacts_by_doc, document_ids, db_components, fallback_sources)
        graph_bundle = _resolve_artifact_first_graph(
            artifacts_by_doc, document_ids, components, db_component_graph, fallback_sources,
            load_db_graph=lambda d: _load_component_graph_for_document(session, d),
        )
        component_graph = graph_bundle["component_graph"]
        operation_graph = graph_bundle["operation_graph"]
        component_operation_links = graph_bundle["component_operation_links"]
        run_ids = _load_latest_run_ids(session, document_ids)
        export_source = {
            "export_source_policy": "artifact_first",
            "artifact_run_ids": run_ids,
            "artifact_run_id": next(iter(run_ids.values()), ""),
            "fallback_used": bool(fallback_sources),
            "fallback_sources": fallback_sources,
        }
        evidence_snippets = (
            _build_evidence_for_documents(artifacts_by_doc, document_ids, claims)
            if req.include_source_snippets else []
        )
        equations = _build_equations_for_documents(artifacts_by_doc, document_ids)
        equation_candidates = _build_equation_candidates_for_documents(artifacts_by_doc, document_ids)
        derivation_chains = _build_derivations_for_documents(artifacts_by_doc, document_ids)
        document_boundaries = _build_document_boundaries(artifacts_by_doc, document_ids)
        completeness_reports = _build_document_completeness_reports(artifacts_by_doc, document_ids)
        course = _enrich_course_for_export(course, artifacts_by_doc, document_ids, components, component_graph)
        _normalize_export_references(
            claims=claims,
            equations=equations,
            components=components,
            component_graph=component_graph,
            course_info=course,
        )
        _normalize_derivation_references(
            claims=claims,
            equations=equations,
            derivation_chains=derivation_chains,
        )
        _link_derivations_to_export_context(
            components=components,
            course_info=course,
            derivation_chains=derivation_chains,
        )
        _apply_confidence_gates_to_export(
            claims=claims,
            components=components,
            component_graph=component_graph,
            course_info=course,
            derivation_chains=derivation_chains,
            equations=equations,
        )
        system_operations = build_system_operations_export(derivation_chains)
        export_validation = _validate_export_references(
            claims=claims,
            equations=equations,
            components=components,
            component_graph=component_graph,
            course_info=course,
            evidence_snippets=evidence_snippets,
            derivation_chains=derivation_chains,
            completeness_reports=completeness_reports,
            operation_graph=operation_graph,
            component_operation_links=component_operation_links,
            system_operations=system_operations,
            artifact_sources=export_source,
        )

        docs = _load_course_documents(session, course_id, document_ids)

        eid = _export_id("course", course_id)
        options = {
            "include_source_snippets": req.include_source_snippets,
            "include_review_fields": req.include_review_fields,
            "include_debug_data": req.include_debug_data,
            "include_llm_raw_outputs": req.include_llm_raw_outputs,
            "include_ndjson": req.include_ndjson,
        }
        manifest = _build_manifest(
            export_id=eid,
            scope_type="course",
            scope_id=course_id,
            material_ids=[s.get("material_id", "") for s in course.get("sources", [])],
            document_ids=document_ids,
            claims=claims,
            dsl_graph=dsl_graph,
            components=components,
            component_graph=component_graph,
            evidence_snippets=evidence_snippets,
            equations=equations,
            equation_candidates=equation_candidates,
            derivation_chains=derivation_chains,
            document_boundaries=document_boundaries,
            operation_graph=operation_graph,
            component_operation_links=component_operation_links,
            system_operations=system_operations,
            export_source=export_source,
            options=options,
        )

        zip_bytes = _build_zip(
            manifest=manifest,
            course_info=course,
            claims=claims,
            dsl_graph=dsl_graph,
            components=components,
            component_graph=component_graph,
            evidence_snippets=evidence_snippets,
            equations=equations,
            equation_candidates=equation_candidates,
            derivation_chains=derivation_chains,
            document_boundaries=document_boundaries,
            operation_graph=operation_graph,
            component_operation_links=component_operation_links,
            system_operations=system_operations,
            include_ndjson=req.include_ndjson,
            include_debug=req.include_debug_data,
            debug_data={
                "validation_errors": [],
                "pipeline_logs": [],
                "component_assembly_artifacts": _build_component_assembly_debug(
                    artifacts_by_doc, document_ids, include_llm_raw=req.include_llm_raw_outputs
                ),
            } if req.include_debug_data else None,
            export_validation=export_validation,
        )

        filename = _zip_filename("course", course_id)
        return StreamingResponse(
            io.BytesIO(zip_bytes),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    finally:
        session.close()


@router.post("/api/documents/{document_id}/export-bundle")
def export_document_bundle(
    document_id: str,
    req: ExportBundleRequest = ExportBundleRequest(),
    current_user: dict = Depends(_require_teacher),
) -> StreamingResponse:
    """ドキュメント単位でエクスポートZIPを生成してダウンロードする。"""
    session = _pg_session()
    try:
        document = _load_document(session, document_id)
        if not document:
            raise HTTPException(status_code=404, detail="Document not found")

        dsl_graph = _load_dsl_graph_for_document(session, document_id)
        db_claims = _load_claims_for_document(session, document_id)
        db_components = _load_components_for_document(session, document_id)
        db_component_graph = _load_component_graph_for_document(session, document_id)

        document_ids = [document_id]
        artifacts_by_doc = _load_analysis_artifacts(session, document_ids)
        # Artifact-first export (issue #383).
        fallback_sources: list[dict] = []
        claims = _resolve_artifact_first_claims(artifacts_by_doc, document_ids, db_claims, fallback_sources)
        components = _resolve_artifact_first_components(artifacts_by_doc, document_ids, db_components, fallback_sources)
        graph_bundle = _resolve_artifact_first_graph(
            artifacts_by_doc, document_ids, components, db_component_graph, fallback_sources,
            load_db_graph=lambda d: _load_component_graph_for_document(session, d),
        )
        component_graph = graph_bundle["component_graph"]
        operation_graph = graph_bundle["operation_graph"]
        component_operation_links = graph_bundle["component_operation_links"]
        run_ids = _load_latest_run_ids(session, document_ids)
        export_source = {
            "export_source_policy": "artifact_first",
            "artifact_run_ids": run_ids,
            "artifact_run_id": next(iter(run_ids.values()), ""),
            "fallback_used": bool(fallback_sources),
            "fallback_sources": fallback_sources,
        }
        evidence_snippets = (
            _build_evidence_for_documents(artifacts_by_doc, document_ids, claims)
            if req.include_source_snippets else []
        )
        equations = _build_equations_for_documents(artifacts_by_doc, document_ids)
        equation_candidates = _build_equation_candidates_for_documents(artifacts_by_doc, document_ids)
        derivation_chains = _build_derivations_for_documents(artifacts_by_doc, document_ids)
        document_boundaries = _build_document_boundaries(artifacts_by_doc, document_ids)
        completeness_reports = _build_document_completeness_reports(artifacts_by_doc, document_ids)
        document = _enrich_course_for_export(document, artifacts_by_doc, document_ids, components, component_graph)
        _normalize_export_references(
            claims=claims,
            equations=equations,
            components=components,
            component_graph=component_graph,
            course_info=document,
        )
        _normalize_derivation_references(
            claims=claims,
            equations=equations,
            derivation_chains=derivation_chains,
        )
        _link_derivations_to_export_context(
            components=components,
            course_info=document,
            derivation_chains=derivation_chains,
        )
        _apply_confidence_gates_to_export(
            claims=claims,
            components=components,
            component_graph=component_graph,
            course_info=document,
            derivation_chains=derivation_chains,
            equations=equations,
        )
        system_operations = build_system_operations_export(derivation_chains)
        export_validation = _validate_export_references(
            claims=claims,
            equations=equations,
            components=components,
            component_graph=component_graph,
            course_info=document,
            evidence_snippets=evidence_snippets,
            derivation_chains=derivation_chains,
            completeness_reports=completeness_reports,
            operation_graph=operation_graph,
            component_operation_links=component_operation_links,
            system_operations=system_operations,
            artifact_sources=export_source,
        )

        eid = _export_id("document", document_id)
        options = {
            "include_source_snippets": req.include_source_snippets,
            "include_review_fields": req.include_review_fields,
            "include_debug_data": req.include_debug_data,
            "include_llm_raw_outputs": req.include_llm_raw_outputs,
            "include_ndjson": req.include_ndjson,
        }
        manifest = _build_manifest(
            export_id=eid,
            scope_type="document",
            scope_id=document_id,
            material_ids=[],
            document_ids=document_ids,
            claims=claims,
            dsl_graph=dsl_graph,
            components=components,
            component_graph=component_graph,
            evidence_snippets=evidence_snippets,
            equations=equations,
            equation_candidates=equation_candidates,
            derivation_chains=derivation_chains,
            document_boundaries=document_boundaries,
            operation_graph=operation_graph,
            component_operation_links=component_operation_links,
            system_operations=system_operations,
            export_source=export_source,
            options=options,
        )

        zip_bytes = _build_zip(
            manifest=manifest,
            course_info=document,
            claims=claims,
            dsl_graph=dsl_graph,
            components=components,
            component_graph=component_graph,
            evidence_snippets=evidence_snippets,
            equations=equations,
            equation_candidates=equation_candidates,
            derivation_chains=derivation_chains,
            document_boundaries=document_boundaries,
            operation_graph=operation_graph,
            component_operation_links=component_operation_links,
            system_operations=system_operations,
            include_ndjson=req.include_ndjson,
            include_debug=req.include_debug_data,
            debug_data={
                "validation_errors": [],
                "pipeline_logs": [],
                "component_assembly_artifacts": _build_component_assembly_debug(
                    artifacts_by_doc, document_ids, include_llm_raw=req.include_llm_raw_outputs
                ),
            } if req.include_debug_data else None,
            export_validation=export_validation,
        )

        filename = _zip_filename("document", document_id)
        return StreamingResponse(
            io.BytesIO(zip_bytes),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    finally:
        session.close()
