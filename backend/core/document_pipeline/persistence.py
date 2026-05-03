"""Adapters from agent results to existing Postgres tables (issue #226)."""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from sqlalchemy import text as sa_text

from core.llm import generate_embeddings
from core.postgres import get_session as _pg_session

logger = logging.getLogger(__name__)


def _strip_nuls(value: Any) -> Any:
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, list):
        return [_strip_nuls(v) for v in value]
    if isinstance(value, dict):
        return {str(k).replace("\x00", ""): _strip_nuls(v) for k, v in value.items()}
    return value


def _json_dumps(value: Any) -> str:
    return json.dumps(_strip_nuls(value), ensure_ascii=False)


# ---------------------------------------------------------------------------
# chunks
# ---------------------------------------------------------------------------


def persist_source_chunks(
    *,
    document_id: str,
    material_id: str,
    chunks: list,
) -> list[dict]:
    """source chunk を embedding して `chunks` テーブルに保存する。

    旧実装と同じカラム (text/embedding/material_id/document_id/page_start/page_end)
    に加え、issue #226 で追加された section_id / block_ids / source_metadata を
    埋める。

    Returns:
        各 chunk について {chunk_id, chunk_index, section_id, block_ids,
        page_start, page_end, text} の dict を返す。後段 agent の evidence ref
        を解決する際に使う。
    """
    if not chunks:
        return []

    texts = [str(c.text or "") for c in chunks]
    # 空テキストを embedding に投げるとエラーになるため、空でもプレースホルダ化
    safe_texts = [t if t.strip() else " " for t in texts]
    embeddings = generate_embeddings(safe_texts)

    saved: list[dict] = []
    session = _pg_session()
    try:
        # 同じ document の既存 chunk を消してから入れ直す（再実行時の整合のため）
        session.execute(
            sa_text("DELETE FROM chunks WHERE document_id = CAST(:doc_id AS uuid)"),
            {"doc_id": document_id},
        )
        for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
            chunk_id = uuid.uuid4()
            session.execute(
                sa_text(
                    """
                    INSERT INTO chunks (
                        id, document_id, chunk_index, text, embedding,
                        material_id, page_start, page_end,
                        section_id, block_ids, source_metadata
                    )
                    VALUES (
                        :id, CAST(:doc_id AS uuid), :idx, :text, :embedding,
                        :material_id, :page_start, :page_end,
                        :section_id, CAST(:block_ids AS jsonb),
                        CAST(:metadata AS jsonb)
                    )
                    """
                ),
                {
                    "id": chunk_id,
                    "doc_id": document_id,
                    "idx": chunk.chunk_index,
                    "text": _strip_nuls(chunk.text or ""),
                    "embedding": str(list(embedding)),
                    "material_id": material_id,
                    "page_start": chunk.page_start,
                    "page_end": chunk.page_end,
                    "section_id": chunk.section_id,
                    "block_ids": _json_dumps(chunk.block_ids),
                    "metadata": _json_dumps(chunk.metadata),
                },
            )
            saved.append({
                "chunk_id": str(chunk_id),
                "chunk_index": chunk.chunk_index,
                "section_id": chunk.section_id,
                "block_ids": list(chunk.block_ids),
                "page_start": chunk.page_start,
                "page_end": chunk.page_end,
                "text": chunk.text,
            })
        session.commit()
        logger.info(
            "Persisted %d source chunks for document %s", len(saved), document_id
        )
        return saved
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# theory_claims
# ---------------------------------------------------------------------------


_VALID_CLAIM_TYPES = {
    "definition", "assumption", "approximation", "equation", "relation",
    "derivation_step", "observable_definition", "correction",
    "uncertainty", "limitation", "result", "diagnostic_claim",
    "equation_definition", "equation_relation", "equation_transformation",
    "equation_approximation", "equation_constraint",
}


def _normalize_claim_type(qualification: dict | None) -> str:
    if not isinstance(qualification, dict):
        return "diagnostic_claim"
    for key in ("claim_type", "tier", "type"):
        v = qualification.get(key)
        if isinstance(v, str) and v in _VALID_CLAIM_TYPES:
            return v
    return "diagnostic_claim"


def persist_qualified_claims(
    *,
    document_id: str,
    qualified_result,
    chunk_index: list[dict],
) -> list[dict]:
    """ClaimQualificationResult.qualified_spans を `theory_claims` に保存する。

    Args:
        chunk_index: persist_source_chunks の戻り値。block_id → chunk_id の
            解決に使う。

    Returns:
        [{claim_id, span_id, chunk_id, text}] のリスト。
    """
    block_to_chunk: dict[str, str] = {}
    for ch in chunk_index:
        for bid in ch.get("block_ids") or []:
            block_to_chunk.setdefault(bid, ch["chunk_id"])

    spans = list(getattr(qualified_result, "qualified_spans", []) or [])
    if not spans:
        return []

    saved: list[dict] = []
    session = _pg_session()
    try:
        # 同 document の既存 claim を削除（pipeline 再実行時の整合確保）
        session.execute(
            sa_text("DELETE FROM theory_claims WHERE document_id = :doc_id"),
            {"doc_id": document_id},
        )
        for span in spans:
            qualification = getattr(span, "qualification", {}) or {}
            decision = qualification.get("decision") if isinstance(qualification, dict) else None
            if decision == "rejected":
                continue
            chunk_id = block_to_chunk.get(getattr(span, "block_id", ""))
            params = {
                "document_id": _strip_nuls(document_id),
                "chunk_id": chunk_id,
                "source_scope": _json_dumps({
                    "section_id": getattr(span, "section_id", None),
                    "block_id": getattr(span, "block_id", None),
                    "span_id": getattr(span, "span_id", None),
                }),
                "claim_type": _normalize_claim_type(qualification),
                "text": _strip_nuls(getattr(span, "text", "") or ""),
                "normalized_text": _strip_nuls(getattr(span, "text", "") or ""),
                "concepts": _json_dumps([]),
                "equation": _json_dumps({}),
                "support_status": "source_backed",
                "evidence_text": _strip_nuls(getattr(span, "reason", "") or ""),
                "review_status": "teacher_review_required",
            }
            row = session.execute(
                sa_text(
                    """
                    INSERT INTO theory_claims (
                        document_id, chunk_id, source_scope, claim_type, text,
                        normalized_text, concepts, equation, support_status,
                        evidence_text, review_status
                    )
                    VALUES (
                        :document_id, CAST(:chunk_id AS uuid), CAST(:source_scope AS jsonb),
                        :claim_type, :text, :normalized_text, CAST(:concepts AS jsonb),
                        CAST(:equation AS jsonb), :support_status, :evidence_text, :review_status
                    )
                    RETURNING id
                    """
                ),
                params,
            ).fetchone()
            saved.append({
                "claim_id": str(row[0]),
                "span_id": getattr(span, "span_id", None),
                "chunk_id": chunk_id,
                "text": getattr(span, "text", ""),
            })
        session.commit()
        logger.info(
            "Persisted %d theory_claims for document %s", len(saved), document_id
        )
        return saved
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# theory_components & links
# ---------------------------------------------------------------------------


def persist_components(
    *,
    document_id: str,
    component_result,
    course_id: str | None = None,
) -> dict[str, str]:
    """ComponentAssemblyResult.components を `theory_components` に保存する。

    Returns:
        agent component_id → DB UUID のマッピング（dependency 解決用）。
    """
    components = list(getattr(component_result, "components", []) or [])
    if not components:
        return {}

    id_map: dict[str, str] = {}
    session = _pg_session()
    try:
        # 同 document の既存 component と link を削除
        session.execute(
            sa_text("DELETE FROM theory_component_links WHERE document_id = :doc_id"),
            {"doc_id": document_id},
        )
        session.execute(
            sa_text("DELETE FROM theory_components WHERE document_id = :doc_id"),
            {"doc_id": document_id},
        )
        for comp in components:
            params = {
                "course_id": course_id,
                "document_id": document_id,
                "name": _strip_nuls(getattr(comp, "label", "") or "Untitled"),
                "component_type": "theory",
                "component_type_text": _strip_nuls(
                    getattr(comp, "component_type", "") or ""
                ),
                "summary": _strip_nuls(getattr(comp, "summary", "") or ""),
                "status": "candidate",
                "source_chunks": _json_dumps(
                    (getattr(comp, "evidence_refs", {}) or {}).get("source_chunks") or []
                ),
                "inputs": _json_dumps(getattr(comp, "inputs", []) or []),
                "outputs": _json_dumps(getattr(comp, "outputs", []) or []),
                "preconditions": _json_dumps(getattr(comp, "preconditions", []) or []),
                "constraints": _json_dumps([]),
                "invalid_conditions": _json_dumps([]),
                "dependencies": _json_dumps(getattr(comp, "dependencies", []) or []),
                "blackbox_policy": _json_dumps({
                    "default_level": "summary",
                    "expand_if_unlearned": True,
                }),
                "validation_warnings": _json_dumps([]),
                "teacher_notes": "",
                "source_scope": _json_dumps({"document_id": document_id}),
                "evidence_claims": _json_dumps(
                    (getattr(comp, "evidence_refs", {}) or {}).get("claim_ids") or []
                ),
                "maturity_level": "paper_claim",
                "maturity_source": "llm_proposed",
                "review_status": "teacher_review_required",
                "cautions": _json_dumps(getattr(comp, "cautions", []) or []),
                "connectors": _json_dumps({}),
                "internal_flow": _json_dumps([]),
                "duplicate_candidates": _json_dumps([]),
            }
            row = session.execute(
                sa_text(
                    """
                    INSERT INTO theory_components (
                        course_id, document_id, name, component_type,
                        component_type_text, summary, status,
                        source_chunks, inputs, outputs, preconditions, constraints,
                        invalid_conditions, dependencies, blackbox_policy,
                        validation_warnings, teacher_notes, source_scope,
                        evidence_claims, maturity_level, maturity_source,
                        review_status, cautions, connectors, internal_flow,
                        duplicate_candidates
                    )
                    VALUES (
                        :course_id, :document_id, :name, :component_type,
                        :component_type_text, :summary, :status,
                        CAST(:source_chunks AS jsonb), CAST(:inputs AS jsonb),
                        CAST(:outputs AS jsonb), CAST(:preconditions AS jsonb),
                        CAST(:constraints AS jsonb), CAST(:invalid_conditions AS jsonb),
                        CAST(:dependencies AS jsonb), CAST(:blackbox_policy AS jsonb),
                        CAST(:validation_warnings AS jsonb), :teacher_notes,
                        CAST(:source_scope AS jsonb), CAST(:evidence_claims AS jsonb),
                        :maturity_level, :maturity_source, :review_status,
                        CAST(:cautions AS jsonb), CAST(:connectors AS jsonb),
                        CAST(:internal_flow AS jsonb), CAST(:duplicate_candidates AS jsonb)
                    )
                    RETURNING id
                    """
                ),
                params,
            ).fetchone()
            db_id = str(row[0])
            id_map[getattr(comp, "component_id", db_id)] = db_id

        # 依存リンクを生成
        for comp in components:
            src_db = id_map.get(getattr(comp, "component_id", ""))
            if not src_db:
                continue
            for dep in getattr(comp, "dependencies", []) or []:
                if not isinstance(dep, dict):
                    continue
                refs = dep.get("component_refs") or []
                dep_type = dep.get("dependency_type") or "depends_on"
                link_type = (
                    "requires" if dep_type == "requires"
                    else "depends_on" if dep_type in ("depends_on", "qualifies", "refines", "supports")
                    else "depends_on"
                )
                for ref in refs:
                    dst_db = id_map.get(ref)
                    if not dst_db or dst_db == src_db:
                        continue
                    session.execute(
                        sa_text(
                            """
                            INSERT INTO theory_component_links (
                                course_id, document_id,
                                source_component_id, target_component_id,
                                link_type, status, validation_result
                            )
                            VALUES (
                                :course_id, :document_id,
                                CAST(:src AS uuid), CAST(:dst AS uuid),
                                :link_type, 'candidate', CAST(:validation AS jsonb)
                            )
                            """
                        ),
                        {
                            "course_id": course_id,
                            "document_id": document_id,
                            "src": src_db,
                            "dst": dst_db,
                            "link_type": link_type,
                            "validation": _json_dumps({
                                "agent_dependency_type": dep_type,
                                "reason": dep.get("reason"),
                            }),
                        },
                    )
        session.commit()
        logger.info(
            "Persisted %d theory_components and dependency links for document %s",
            len(id_map), document_id,
        )
        return id_map
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# theory_component_graphs
# ---------------------------------------------------------------------------


def persist_component_graph(
    *,
    document_id: str,
    component_id_map: dict[str, str],
    component_result,
    dsl_result,
    course_id: str | None = None,
) -> str | None:
    """document scope の component graph を `theory_component_graphs` に保存。"""
    nodes = []
    for agent_id, db_id in component_id_map.items():
        nodes.append({
            "id": db_id,
            "agent_component_id": agent_id,
            "type": "component",
        })

    edges = []
    for comp in getattr(component_result, "components", []) or []:
        src_db = component_id_map.get(getattr(comp, "component_id", ""))
        if not src_db:
            continue
        for dep in getattr(comp, "dependencies", []) or []:
            if not isinstance(dep, dict):
                continue
            for ref in dep.get("component_refs") or []:
                dst_db = component_id_map.get(ref)
                if not dst_db:
                    continue
                edges.append({
                    "from": src_db,
                    "to": dst_db,
                    "type": dep.get("dependency_type") or "depends_on",
                    "reason": dep.get("reason") or "",
                })

    dsl_nodes = [
        {
            "id": getattr(n, "node_id", ""),
            "node_type": getattr(n, "node_type", ""),
            "value": getattr(n, "node_value", ""),
        }
        for n in (getattr(dsl_result, "nodes", []) or [])
    ]
    dsl_edges = [
        {
            "from": getattr(e, "from_node_id", ""),
            "to": getattr(e, "to_node_id", ""),
            "predicate": getattr(e, "core_predicate", ""),
            "verb": getattr(e, "domain_verb", ""),
            "polarity": getattr(e, "polarity", ""),
        }
        for e in (getattr(dsl_result, "edges", []) or [])
    ]

    graph = {
        "graph_id": f"graph_{document_id}",
        "document_id": document_id,
        "scope": {"level": "paper"},
        "nodes": nodes,
        "edges": edges,
        "dsl": {"nodes": dsl_nodes, "edges": dsl_edges},
        "validation_results": [],
    }

    session = _pg_session()
    try:
        row = session.execute(
            sa_text(
                """
                INSERT INTO theory_component_graphs (
                    course_id, document_id, scope, graph_json, validation_results
                )
                VALUES (
                    :course_id, :document_id, CAST(:scope AS jsonb),
                    CAST(:graph_json AS jsonb), CAST(:validation AS jsonb)
                )
                ON CONFLICT (document_id) DO UPDATE SET
                    course_id = EXCLUDED.course_id,
                    scope = EXCLUDED.scope,
                    graph_json = EXCLUDED.graph_json,
                    validation_results = EXCLUDED.validation_results,
                    updated_at = now()
                RETURNING id
                """
            ),
            {
                "course_id": course_id,
                "document_id": document_id,
                "scope": _json_dumps({"level": "paper"}),
                "graph_json": _json_dumps(graph),
                "validation": _json_dumps([]),
            },
        ).fetchone()
        session.commit()
        return str(row[0]) if row else None
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# document_embeddings
# ---------------------------------------------------------------------------


def persist_document_embedding(
    *,
    document_id: str,
    material_id: str | None,
    embedding_type: str,
    text: str,
    metadata: dict | None = None,
    source_version: str = "v1",
) -> str:
    """document-level の derived embedding を保存する（DSL graph など）。"""
    if not text or not text.strip():
        text = " "
    [embedding] = generate_embeddings([text])
    session = _pg_session()
    try:
        row = session.execute(
            sa_text(
                """
                INSERT INTO document_embeddings (
                    document_id, material_id, embedding_type, source_version,
                    text, embedding, metadata
                )
                VALUES (
                    :document_id, :material_id, :embedding_type, :source_version,
                    :text, :embedding, CAST(:metadata AS jsonb)
                )
                ON CONFLICT (document_id, embedding_type, source_version) DO UPDATE SET
                    material_id = EXCLUDED.material_id,
                    text = EXCLUDED.text,
                    embedding = EXCLUDED.embedding,
                    metadata = EXCLUDED.metadata,
                    updated_at = now()
                RETURNING id
                """
            ),
            {
                "document_id": document_id,
                "material_id": material_id,
                "embedding_type": embedding_type,
                "source_version": source_version,
                "text": _strip_nuls(text),
                "embedding": str(list(embedding)),
                "metadata": _json_dumps(metadata or {}),
            },
        ).fetchone()
        session.commit()
        return str(row[0]) if row else ""
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# document_analysis_runs
# ---------------------------------------------------------------------------


def upsert_analysis_run(
    *,
    document_id: str,
    material_id: str | None,
    cartridge_id: str | None,
    status: str,
    current_stage: str | None = None,
    error_message: str = "",
    stage_outputs: dict | None = None,
    run_id: str | None = None,
) -> str:
    """`document_analysis_runs` に upsert する。

    run_id を渡すとそのレコードを更新、None なら新規作成。
    """
    session = _pg_session()
    try:
        if run_id is None:
            row = session.execute(
                sa_text(
                    """
                    INSERT INTO document_analysis_runs (
                        document_id, material_id, cartridge_id, status,
                        current_stage, error_message, stage_outputs, started_at
                    )
                    VALUES (
                        :document_id, :material_id, :cartridge_id, :status,
                        :current_stage, :error_message, CAST(:stage_outputs AS jsonb),
                        CASE WHEN :status = 'running' THEN now() ELSE NULL END
                    )
                    RETURNING id
                    """
                ),
                {
                    "document_id": document_id,
                    "material_id": material_id,
                    "cartridge_id": cartridge_id,
                    "status": status,
                    "current_stage": current_stage,
                    "error_message": error_message or "",
                    "stage_outputs": _json_dumps(stage_outputs or {}),
                },
            ).fetchone()
            session.commit()
            return str(row[0])
        else:
            session.execute(
                sa_text(
                    """
                    UPDATE document_analysis_runs SET
                        status = :status,
                        current_stage = :current_stage,
                        error_message = :error_message,
                        stage_outputs = stage_outputs || CAST(:stage_outputs AS jsonb),
                        completed_at = CASE WHEN :status IN ('completed', 'failed')
                                            THEN now() ELSE completed_at END,
                        updated_at = now()
                    WHERE id = CAST(:id AS uuid)
                    """
                ),
                {
                    "id": run_id,
                    "status": status,
                    "current_stage": current_stage,
                    "error_message": error_message or "",
                    "stage_outputs": _json_dumps(stage_outputs or {}),
                },
            )
            session.commit()
            return run_id
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
