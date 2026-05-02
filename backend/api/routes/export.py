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
        sa_text("SELECT id, title, description, is_template, is_published, created_at FROM learning_courses WHERE id = :id"),
        {"id": course_id},
    ).fetchone()
    if not row:
        return None
    return {
        "course_id": str(row[0]),
        "title": row[1] or "",
        "description": row[2] or "",
        "is_template": bool(row[3]),
        "is_published": bool(row[4]),
        "created_at": row[5].isoformat() if row[5] else "",
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


def _load_course_documents(session: Any, course_id: str) -> list[dict]:
    rows = session.execute(
        sa_text("""
            SELECT DISTINCT d.id, d.title, d.status, d.created_at
            FROM documents d
            JOIN chunks c ON c.document_id = d.id
            JOIN theory_claims tc ON tc.chunk_id = c.id
            WHERE tc.document_id IN (
                SELECT id::text FROM documents
            )
            AND EXISTS (
                SELECT 1 FROM learning_courses lc WHERE lc.id = :course_id
            )
            UNION
            SELECT DISTINCT d.id, d.title, d.status, d.created_at
            FROM documents d
            JOIN theory_components tc ON tc.course_id = :course_id
            JOIN chunks c ON c.document_id = d.id
            WHERE tc.primary_chunk_id = c.id
        """),
        {"course_id": course_id},
    ).fetchall()
    return [{"document_id": str(r[0]), "title": r[1] or "", "status": r[2] or ""} for r in rows]


def _load_claims_for_course(session: Any, course_id: str) -> list[dict]:
    rows = session.execute(
        sa_text("""
            SELECT tc.id, tc.document_id, tc.source_scope, tc.claim_type,
                   tc.text, tc.normalized_text, tc.concepts, tc.equation,
                   tc.support_status, tc.evidence_text, tc.review_status,
                   tc.created_at
            FROM theory_claims tc
            JOIN chunks c ON c.id = tc.chunk_id
            JOIN theory_components tcomp ON tcomp.primary_chunk_id = c.id
            WHERE tcomp.course_id = :course_id
            UNION
            SELECT DISTINCT tc.id, tc.document_id, tc.source_scope, tc.claim_type,
                   tc.text, tc.normalized_text, tc.concepts, tc.equation,
                   tc.support_status, tc.evidence_text, tc.review_status,
                   tc.created_at
            FROM theory_claims tc
            JOIN chunks c ON c.id = tc.chunk_id
            WHERE c.document_id IN (
                SELECT DISTINCT c2.document_id
                FROM chunks c2
                JOIN theory_components tcomp2 ON tcomp2.primary_chunk_id = c2.id
                WHERE tcomp2.course_id = :course_id
            )
        """),
        {"course_id": course_id},
    ).fetchall()
    return _rows_to_claims(rows)


def _load_claims_for_document(session: Any, document_id: str) -> list[dict]:
    rows = session.execute(
        sa_text("""
            SELECT tc.id, tc.document_id, tc.source_scope, tc.claim_type,
                   tc.text, tc.normalized_text, tc.concepts, tc.equation,
                   tc.support_status, tc.evidence_text, tc.review_status,
                   tc.created_at
            FROM theory_claims tc
            WHERE tc.document_id = :document_id
            ORDER BY tc.created_at
        """),
        {"document_id": document_id},
    ).fetchall()
    return _rows_to_claims(rows)


def _rows_to_claims(rows: list) -> list[dict]:
    claims = []
    seen = set()
    for i, r in enumerate(rows):
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


def _load_dsl_graph_for_course(session: Any, course_id: str) -> dict:
    rows = session.execute(
        sa_text("""
            SELECT c.id::text, c.smiles_dsl, c.variables, c.ancestors, c.document_id
            FROM chunks c
            JOIN theory_components tc ON tc.primary_chunk_id = c.id
            WHERE tc.course_id = :course_id
              AND c.smiles_dsl IS NOT NULL AND c.smiles_dsl != ''
        """),
        {"course_id": course_id},
    ).fetchall()
    return _rows_to_dsl_graph(rows)


def _load_dsl_graph_for_document(session: Any, document_id: str) -> dict:
    rows = session.execute(
        sa_text("""
            SELECT c.id::text, c.smiles_dsl, c.variables, c.ancestors, c.document_id
            FROM chunks c
            WHERE c.document_id = :document_id
              AND c.smiles_dsl IS NOT NULL AND c.smiles_dsl != ''
        """),
        {"document_id": document_id},
    ).fetchall()
    return _rows_to_dsl_graph(rows)


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


def _load_components_for_course(session: Any, course_id: str) -> list[dict]:
    rows = session.execute(
        sa_text("""
            SELECT id, course_id, name, component_type, component_type_text,
                   summary, status, source_scope, evidence_claims, maturity_level,
                   maturity_source, review_status, inputs, outputs, preconditions,
                   cautions, constraints, invalid_conditions, dependencies,
                   connectors, internal_flow, teacher_notes, created_at
            FROM theory_components
            WHERE course_id = :course_id
            ORDER BY created_at
        """),
        {"course_id": course_id},
    ).fetchall()
    return _rows_to_components(rows)


def _load_components_for_document(session: Any, document_id: str) -> list[dict]:
    rows = session.execute(
        sa_text("""
            SELECT tc.id, tc.course_id, tc.name, tc.component_type, tc.component_type_text,
                   tc.summary, tc.status, tc.source_scope, tc.evidence_claims, tc.maturity_level,
                   tc.maturity_source, tc.review_status, tc.inputs, tc.outputs, tc.preconditions,
                   tc.cautions, tc.constraints, tc.invalid_conditions, tc.dependencies,
                   tc.connectors, tc.internal_flow, tc.teacher_notes, tc.created_at
            FROM theory_components tc
            JOIN chunks c ON c.id = tc.primary_chunk_id
            WHERE c.document_id = :document_id
            ORDER BY tc.created_at
        """),
        {"document_id": document_id},
    ).fetchall()
    return _rows_to_components(rows)


def _rows_to_components(rows: list) -> list[dict]:
    components = []
    for r in rows:
        comp_id = str(r[0])
        source_scope = _load_json_field(r[7], {})
        evidence_claims = _load_json_field(r[8], [])
        source_scope["document_id"] = source_scope.get("document_id", "")
        components.append({
            "component_id": comp_id,
            "course_id": str(r[1]),
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
        })
    return components


def _load_component_graph_for_course(session: Any, course_id: str) -> dict:
    row = session.execute(
        sa_text("""
            SELECT id, document_id, scope, graph_json
            FROM theory_component_graphs
            WHERE course_id = :course_id
            ORDER BY updated_at DESC
            LIMIT 1
        """),
        {"course_id": course_id},
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
) -> dict:
    return {
        "export_schema_version": "0.1.0",
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
            "claims": "claims/claims.json",
            "dsl_graph": "dsl/dsl_graph.json",
            "components": "components/components.json",
            "component_graph": "graph/component_graph.json",
            "evidence_snippets": "evidence/evidence_snippets.json",
        },
        "counts": {
            "claims": len(claims),
            "dsl_nodes": len(dsl_graph.get("nodes", [])),
            "dsl_edges": len(dsl_graph.get("edges", [])),
            "components": len(components),
            "component_edges": len(component_graph.get("edges", [])),
            "evidence_snippets": len(evidence_snippets),
        },
        "options": options,
    }


_README_TEMPLATE = """\
# episteme-graph Export Bundle

This ZIP contains machine-readable outputs generated by episteme-graph.

## Contents

- `manifest.json`: index of this export bundle
- `claims/claims.json`: extracted claims from source documents
- `dsl/dsl_graph.json`: lightweight logical graph representation (DSL)
- `components/components.json`: reusable logical/theory components (Component)
- `graph/component_graph.json`: graph connections between components (Component Graph)
- `evidence/evidence_snippets.json`: source snippets supporting claims (Evidence)

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
    include_ndjson: bool = False,
    include_debug: bool = False,
    debug_data: dict | None = None,
) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("README.md", _README_TEMPLATE)
        zf.writestr("manifest.json", _json_bytes(manifest))
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
        zf.writestr("evidence/evidence_snippets.json", _json_bytes({"snippets": evidence_snippets}))

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

        claims = _load_claims_for_course(session, course_id)
        dsl_graph = _load_dsl_graph_for_course(session, course_id)
        components = _load_components_for_course(session, course_id)
        component_graph = _load_component_graph_for_course(session, course_id)
        evidence_snippets = _build_evidence_snippets(claims) if req.include_source_snippets else []

        docs = _load_course_documents(session, course_id)
        document_ids = [d["document_id"] for d in docs]

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
            material_ids=[],
            document_ids=document_ids,
            claims=claims,
            dsl_graph=dsl_graph,
            components=components,
            component_graph=component_graph,
            evidence_snippets=evidence_snippets,
            options=options,
        )

        zip_bytes = _build_zip(
            manifest=manifest,
            claims=claims,
            dsl_graph=dsl_graph,
            components=components,
            component_graph=component_graph,
            evidence_snippets=evidence_snippets,
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
        evidence_snippets = _build_evidence_snippets(claims) if req.include_source_snippets else []

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
            document_ids=[document_id],
            claims=claims,
            dsl_graph=dsl_graph,
            components=components,
            component_graph=component_graph,
            evidence_snippets=evidence_snippets,
            options=options,
        )

        zip_bytes = _build_zip(
            manifest=manifest,
            claims=claims,
            dsl_graph=dsl_graph,
            components=components,
            component_graph=component_graph,
            evidence_snippets=evidence_snippets,
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
