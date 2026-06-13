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
from routes.export_artifacts import (
    build_derivation_chains_export,
    build_document_boundary,
    build_document_completeness,
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
    """Load `_artifacts` payload from document_analysis_runs for each document.

    Returns: {document_id: {stage_name: artifact_dict, ...}, ...}.
    Picks the most recent run per document so resumed runs surface the
    latest artifacts. Defensive: never raises on malformed JSONB.
    """
    if not document_ids:
        return {}
    placeholders = ", ".join(f":doc_{i}" for i in range(len(document_ids)))
    params = {f"doc_{i}": did for i, did in enumerate(document_ids)}
    rows = session.execute(
        sa_text(f"""
            SELECT DISTINCT ON (document_id)
                   document_id, stage_outputs, status, created_at
            FROM document_analysis_runs
            WHERE document_id IN ({placeholders})
            ORDER BY document_id, created_at DESC
        """),
        params,
    ).fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        doc_id = str(r[0]) if r[0] else ""
        if not doc_id:
            continue
        artifacts = get_artifacts(r[1])
        if artifacts:
            out[doc_id] = artifacts
    return out


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
        out.append(build_document_boundary(structure, document_id=doc_id))
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
        out.append(build_document_completeness(structure, document_id=doc_id))
    return out


def _enrich_course_for_export(
    course: dict,
    artifacts_by_doc: dict[str, dict],
    document_ids: list[str],
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
            comp["evidence_claims"] = _map_ref_list(
                comp["evidence_claims"],
                claim_map,
                known_ids=known_claim_ids,
                drop_unresolved=True,
            )
        for key in ("inputs", "outputs", "preconditions", "cautions", "constraints", "invalid_conditions"):
            if key in comp:
                comp[key] = _normalize_claim_refs_in_items(comp[key], claim_map, known_claim_ids)
        for dep in comp.get("dependencies") or []:
            if isinstance(dep, dict) and isinstance(dep.get("component_refs"), list):
                dep["component_refs"] = _map_ref_list(
                    dep["component_refs"],
                    component_map,
                    known_ids=known_component_ids,
                    drop_unresolved=True,
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
    """Aggregate per-document completeness into the export_validation block (#366).

    Returns a JSON-serialisable summary listing, per incomplete document, the
    missing equation labels, the un-ingested page coverage, and whether a
    terminal (Conclusion/Summary) section was found. Incomplete documents are
    reported so they are not promoted to publish_ready.
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
        coverage = rep.get("page_coverage") or {}
        documents.append({
            "document_id": rep.get("document_id"),
            "complete": complete,
            "review_reasons": list(rep.get("review_reasons") or []),
            "missing_equation_labels": list(eq.get("missing_labels") or []),
            "terminal_section_present": bool(terminal.get("present")),
            "ingested_pages": list(coverage.get("ingested_pages") or []),
            "pages_total": coverage.get("pages_total"),
            "page_coverage_ratio": coverage.get("coverage_ratio"),
        })
    return {
        "checked": bool(documents),
        "all_documents_complete": all_complete,
        "documents": documents,
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
) -> dict:
    errors: list[dict] = []
    warnings: list[dict] = []
    derivation_chains = derivation_chains or []
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

    for idx, comp in enumerate(components):
        if not isinstance(comp, dict):
            continue
        check_refs(comp.get("evidence_claims"), claim_ids, "components/components.json", f"$.components[{idx}].evidence_claims", "claim")
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

    # Document completeness gate (#366): a truncated ingest (missing equation
    # labels, absent Conclusion, low page coverage) is surfaced as warnings so
    # the bundle is exportable for review but never promoted to publish_ready.
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
        if "page_coverage_insufficient" in doc["review_reasons"]:
            warn(
                "DOCUMENT_PAGE_COVERAGE_INSUFFICIENT",
                (
                    f"document {doc_id!r} ingested pages {doc['ingested_pages']} cover only "
                    f"{doc['page_coverage_ratio']} of {doc['pages_total']} pages"
                ),
                "document_boundary.json",
                "$.completeness.page_coverage",
                doc_id,
            )

    publish_ready = not errors and not warnings and completeness["all_documents_complete"]

    return {
        "status": "failed_validation" if errors else "passed",
        "exportable": not errors,
        "publish_ready": publish_ready,
        "errors": errors,
        "warnings": warnings,
        "completeness": completeness,
        "summary": {"error_count": len(errors), "warning_count": len(warnings), "unresolved_reference_count": len(errors)},
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
) -> dict:
    equations = equations or []
    equation_candidates = equation_candidates or []
    derivation_chains = derivation_chains or []
    document_boundaries = document_boundaries or []
    return {
        "export_schema_version": "0.2.0",
        "exported_at": _now_iso(),
        "export_id": export_id,
        "app": {"name": "episteme-graph", "version": "unknown", "git_commit": "unknown"},
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
- `graph/component_graph.json`: graph connections between components (Component Graph)
- `evidence/evidence_snippets.json`: source-backed Evidence (PDF spans). `evidence_text` is PDF-derived text; LLM commentary is kept in `analysis_note` / `review_note`. `extraction_source`, `extraction_status`, `needs_review`, `review_reason` audit the provenance.
- `equations/equations.json`: first-class equation registry. Each entry has `latex`, `plain_text`, `source_location`, `equation_type`, `defined_symbols`, `used_symbols`, `input_equation_ids`, `output_equation_ids`, `extraction_source`, `extraction_status`, `needs_math_review`, `review_reason`, `candidate_trace_ids`.
- `equations/equation_candidates.json`: audit trail for equation candidate detection. Each entry records `raw_text`, `source_location`, `detection_method`, `matched_label`, `acceptance_status`, `accepted_equation_id`, `rejection_reason`.
- `derivations/derivation_chains.json`: derivation steps linking equations / claims with `operation`, `input_equation_ids`, `output_equation_ids`, `assumption_refs`.
- `document_boundary.json`: per-document active article boundary (page_start, page_end, confidence, needs_review). Useful for multi-article PDFs (journal scans, conference proceedings). A collapsed boundary (span ≪ pages_total) or reference-author contamination drops confidence below 1.0 and raises needs_review. A `completeness` block reports equation-label continuity, terminal-section presence, and page coverage.
- `export_validation.json`: deterministic cross-artifact validation. The `completeness` section aggregates per-document ingest completeness (missing equation labels, un-ingested page coverage, terminal-section presence); an incomplete document keeps the bundle out of `publish_ready`.

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
) -> bytes:
    equations = equations or []
    equation_candidates = equation_candidates or []
    derivation_chains = derivation_chains or []
    document_boundaries = document_boundaries or []
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

        claims = _load_claims_for_course(session, course_id, document_ids)
        dsl_graph = _load_dsl_graph_for_course(session, course_id, document_ids)
        components = _load_components_for_course(session, course_id, document_ids)
        component_graph = _load_component_graph_for_course(session, course_id, document_ids)

        artifacts_by_doc = _load_analysis_artifacts(session, document_ids)
        evidence_snippets = (
            _build_evidence_for_documents(artifacts_by_doc, document_ids, claims)
            if req.include_source_snippets else []
        )
        equations = _build_equations_for_documents(artifacts_by_doc, document_ids)
        equation_candidates = _build_equation_candidates_for_documents(artifacts_by_doc, document_ids)
        derivation_chains = _build_derivations_for_documents(artifacts_by_doc, document_ids)
        document_boundaries = _build_document_boundaries(artifacts_by_doc, document_ids)
        completeness_reports = _build_document_completeness_reports(artifacts_by_doc, document_ids)
        course = _enrich_course_for_export(course, artifacts_by_doc, document_ids)
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
        export_validation = _validate_export_references(
            claims=claims,
            equations=equations,
            components=components,
            component_graph=component_graph,
            course_info=course,
            evidence_snippets=evidence_snippets,
            derivation_chains=derivation_chains,
            completeness_reports=completeness_reports,
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

        claims = _load_claims_for_document(session, document_id)
        dsl_graph = _load_dsl_graph_for_document(session, document_id)
        components = _load_components_for_document(session, document_id)
        component_graph = _load_component_graph_for_document(session, document_id)

        document_ids = [document_id]
        artifacts_by_doc = _load_analysis_artifacts(session, document_ids)
        evidence_snippets = (
            _build_evidence_for_documents(artifacts_by_doc, document_ids, claims)
            if req.include_source_snippets else []
        )
        equations = _build_equations_for_documents(artifacts_by_doc, document_ids)
        equation_candidates = _build_equation_candidates_for_documents(artifacts_by_doc, document_ids)
        derivation_chains = _build_derivations_for_documents(artifacts_by_doc, document_ids)
        document_boundaries = _build_document_boundaries(artifacts_by_doc, document_ids)
        completeness_reports = _build_document_completeness_reports(artifacts_by_doc, document_ids)
        document = _enrich_course_for_export(document, artifacts_by_doc, document_ids)
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
        export_validation = _validate_export_references(
            claims=claims,
            equations=equations,
            components=components,
            component_graph=component_graph,
            course_info=document,
            evidence_snippets=evidence_snippets,
            derivation_chains=derivation_chains,
            completeness_reports=completeness_reports,
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
