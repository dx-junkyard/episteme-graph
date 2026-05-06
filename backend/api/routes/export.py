"""Export Bundle API — DSL / Claim / Component / Graph を ZIP でエクスポートする。"""

from __future__ import annotations

import io
import json
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
        nodes.append({
            "node_id": n.get("component_id", n.get("id", "")),
            "node_type": "component",
            "label": n.get("label", n.get("name", "")),
            "component_type": n.get("component_type", ""),
            "review_status": n.get("review_status", "teacher_review_required"),
        })

    edges = []
    for i, e in enumerate(raw_edges):
        if not isinstance(e, dict):
            continue
        edges.append({
            "edge_id": e.get("edge_id", f"component_edge_{i+1:04d}"),
            "source": e.get("source_component_id", e.get("source", "")),
            "target": e.get("target_component_id", e.get("target", "")),
            "edge_type": e.get("relation", e.get("edge_type", "RELATED_TO")),
            "support_status": e.get("support_status", "source_inferred"),
            "evidence_claims": _load_json_field(e.get("evidence", {}).get("evidence_claims", []), []),
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
- `document_boundary.json`: per-document active article boundary (page_start, page_end, confidence, needs_review). Useful for multi-article PDFs (journal scans, conference proceedings).

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
) -> bytes:
    equations = equations or []
    equation_candidates = equation_candidates or []
    derivation_chains = derivation_chains or []
    document_boundaries = document_boundaries or []
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("README.md", _README_TEMPLATE)
        zf.writestr("manifest.json", _json_bytes(manifest))
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
        course = _enrich_course_for_export(course, artifacts_by_doc, document_ids)

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
            debug_data={"validation_errors": [], "pipeline_logs": []} if req.include_debug_data else None,
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
        document = _enrich_course_for_export(document, artifacts_by_doc, document_ids)

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
            debug_data={"validation_errors": [], "pipeline_logs": []} if req.include_debug_data else None,
        )

        filename = _zip_filename("document", document_id)
        return StreamingResponse(
            io.BytesIO(zip_bytes),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    finally:
        session.close()
