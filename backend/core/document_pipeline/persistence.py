"""Adapters from agent results to existing Postgres tables (issue #226)."""
from __future__ import annotations

import copy
import json
import logging
import re
import uuid
from typing import Any

from sqlalchemy import text as sa_text

from core.llm import generate_embeddings
from core.postgres import get_session as _pg_session

logger = logging.getLogger(__name__)

# Stage-outputs sub-key holding the per-stage agent artifacts (mirrors
# orchestrator.ARTIFACTS_KEY). Used for deep-merge of revision artifacts.
ARTIFACTS_KEY = "_artifacts"


def merge_revision_stage_outputs(existing: dict | None, payload: dict | None) -> dict:
    """Pure model of update_revision_status's JSONB merge (#410 P0).

    Mirrors the SQL exactly: non-``_artifacts`` keys are shallow-merged onto the
    existing stage_outputs, while ``_artifacts`` is merged key-by-key into the
    existing ``_artifacts`` so previously-stored artifacts are preserved. Kept as
    a pure function so the merge semantics are unit-testable without a database.
    """
    existing = dict(existing or {})
    payload = dict(payload or {})
    artifacts_delta = payload.pop(ARTIFACTS_KEY, None)
    merged = {**existing, **payload}
    if artifacts_delta is not None:
        merged[ARTIFACTS_KEY] = {
            **dict(existing.get(ARTIFACTS_KEY) or {}),
            **dict(artifacts_delta or {}),
        }
    return merged


class DeterministicFallbackPersistError(RuntimeError):
    """component_assembly の deterministic-fallback 結果を persist しようとした (#347)。

    ExportValidationGate を通らない経路（resume / 古い export_validation
    artifact）でも、fallback-only の結果を theory_components へ反映させず、
    かつ既存の通常成果物を silent に残さないために hard fail する。
    """


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


def _claim_legacy_keys(claim: dict) -> set[str]:
    keys: set[str] = set()
    claim_id = claim.get("claim_id")
    span_id = claim.get("span_id")
    if claim_id:
        keys.add(str(claim_id))
    if span_id:
        safe = re.sub(r"[^A-Za-z0-9_]+", "_", str(span_id))
        keys.add(str(span_id))
        keys.add(f"claim_{safe}")
    return keys


def _remap_string_list(values: Any, id_map: dict[str, str]) -> list[str]:
    out: list[str] = []
    for value in values or []:
        mapped = id_map.get(str(value), str(value))
        if mapped not in out:
            out.append(mapped)
    return out


def _remap_nested_claim_refs(value: Any, id_map: dict[str, str]) -> Any:
    if isinstance(value, list):
        return [_remap_nested_claim_refs(item, id_map) for item in value]
    if not isinstance(value, dict):
        return value
    out = {
        key: _remap_nested_claim_refs(item, id_map)
        for key, item in value.items()
    }
    for key in ("claim_ids", "evidence_claims", "linked_claim_ids"):
        if isinstance(out.get(key), list):
            out[key] = _remap_string_list(out[key], id_map)
    return out


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
                        display_text, spoken_text, formulas, latex_formulas,
                        material_id, page_start, page_end,
                        section_id, block_ids, source_metadata
                    )
                    VALUES (
                        :id, CAST(:doc_id AS uuid), :idx, :text, :embedding,
                        :display_text, :spoken_text, CAST(:formulas AS jsonb),
                        :latex_formulas,
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
                    "display_text": _strip_nuls(chunk.text or ""),
                    "spoken_text": _strip_nuls(_spoken_text_from_formulas(chunk.text or "", getattr(chunk, "formulas", []) or [])),
                    "formulas": _json_dumps(getattr(chunk, "formulas", []) or []),
                    "latex_formulas": [
                        _strip_nuls(str(f.get("latex") or ""))
                        for f in (getattr(chunk, "formulas", []) or [])
                        if isinstance(f, dict) and f.get("latex")
                    ],
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
                "formulas": list(getattr(chunk, "formulas", []) or []),
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


def persist_equation_previews_to_chunks(document_id: str, equations: Any) -> int:
    """Persist reconstructed equation previews into chunk display_text/formulas.

    ``chunks.text`` remains the raw source chunk for auditability. This updates
    display-oriented fields so other UI/API consumers that use chunks directly
    see formula placeholders instead of broken PDF math fragments.
    """
    previews = _equation_previews(equations)
    if not previews:
        return 0

    session = _pg_session()
    updated = 0
    try:
        rows = session.execute(
            sa_text(
                """
                SELECT id, display_text, spoken_text, formulas, page_start, page_end
                FROM chunks
                WHERE document_id = CAST(:doc_id AS uuid)
                ORDER BY chunk_index
                """
            ),
            {"doc_id": document_id},
        ).fetchall()
        for row in rows:
            chunk_id = str(row[0])
            display_text = row[1] or ""
            spoken_text = row[2] or display_text
            formulas = row[3] if isinstance(row[3], list) else []
            page_start = row[4]
            page_end = row[5]

            merged = _merge_equation_previews_for_chunk(formulas, previews, page_start, page_end)
            patched_display = _replace_equation_preview_text(display_text, merged)
            patched_spoken = _spoken_text_from_formulas(patched_display, merged)
            if patched_display == display_text and _json_dumps(merged) == _json_dumps(formulas):
                continue

            session.execute(
                sa_text(
                    """
                    UPDATE chunks
                    SET display_text = :display_text,
                        spoken_text = :spoken_text,
                        formulas = CAST(:formulas AS jsonb),
                        latex_formulas = :latex_formulas
                    WHERE id = CAST(:chunk_id AS uuid)
                    """
                ),
                {
                    "chunk_id": chunk_id,
                    "display_text": _strip_nuls(patched_display),
                    "spoken_text": _strip_nuls(patched_spoken),
                    "formulas": _json_dumps(merged),
                    "latex_formulas": [
                        _strip_nuls(str(f.get("latex") or ""))
                        for f in merged
                        if isinstance(f, dict) and f.get("latex")
                    ],
                },
            )
            updated += 1
        session.commit()
        if updated:
            logger.info("Persisted equation previews into %d chunks for document %s", updated, document_id)
        return updated
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _equation_previews(equations: Any) -> list[dict]:
    records = getattr(equations, "equations", []) or []
    previews: list[dict] = []
    for record in records:
        src = getattr(record, "source_extraction", None)
        rec = getattr(record, "reconstruction", None)
        if not src:
            continue
        source_location = dict(getattr(src, "source_location", {}) or {})
        source_image = getattr(src, "source_image", None)
        latex = getattr(rec, "latex", None) if rec and getattr(rec, "status", "none") != "none" else getattr(src, "latex", None)
        plain_text = getattr(rec, "plain_text", None) if rec and getattr(rec, "status", "none") != "none" else getattr(src, "plain_text", None)
        previews.append({
            "id": getattr(record, "equation_id", "") or f"eq_{len(previews)}",
            "latex": latex or "",
            "spoken": plain_text or "",
            "is_display": True,
            "label": getattr(record, "label", None),
            "block_id": source_location.get("block_id"),
            "source_location": source_location,
            "source_image": source_image if isinstance(source_image, dict) else None,
            "raw_text": getattr(src, "raw_text", "") or "",
            "needs_math_review": bool(getattr(src, "needs_math_review", False)),
            "review_reason": list(getattr(src, "review_reason", []) or []),
        })
    return previews


def _merge_equation_previews_for_chunk(
    formulas: list[dict],
    previews: list[dict],
    page_start: int | None,
    page_end: int | None,
) -> list[dict]:
    merged = [dict(f) for f in formulas if isinstance(f, dict)]
    by_block = {str(f.get("block_id")): f for f in merged if f.get("block_id")}
    by_id = {str(f.get("id")): f for f in merged if f.get("id")}
    for preview in previews:
        if not _preview_matches_page(preview, page_start, page_end):
            continue
        target = by_block.get(str(preview.get("block_id") or "")) or by_id.get(str(preview.get("id") or ""))
        if target is None:
            merged.append(dict(preview))
        else:
            target.update({k: v for k, v in preview.items() if v not in (None, "", [])})
    return merged


def _preview_matches_page(preview: dict, page_start: int | None, page_end: int | None) -> bool:
    loc = preview.get("source_location") if isinstance(preview.get("source_location"), dict) else {}
    try:
        page = int(loc.get("page"))
    except Exception:
        return False
    if page_start is None and page_end is None:
        return False
    start = page_start if page_start is not None else page
    end = page_end if page_end is not None else start
    return int(start) <= page <= int(end)


def _replace_equation_preview_text(display_text: str, formulas: list[dict]) -> str:
    updated = display_text or ""
    for formula in formulas or []:
        if not isinstance(formula, dict):
            continue
        raw_text = str(formula.get("raw_text") or "").strip()
        formula_id = str(formula.get("id") or "").strip()
        if not raw_text or not formula_id:
            continue
        placeholder = formula_id if formula_id.startswith("[[") else f"[[{formula_id}]]"
        for variant in sorted(_equation_text_variants(raw_text), key=len, reverse=True):
            if len(variant.strip()) >= 4:
                updated = updated.replace(variant, placeholder)
    return updated


def _equation_text_variants(raw_text: str) -> list[str]:
    lines = [ln.strip() for ln in str(raw_text or "").strip().splitlines() if ln.strip()]
    if not lines:
        return []
    return list({
        str(raw_text).strip(),
        "\n".join(lines),
        "\n\n".join(lines),
        " ".join(lines),
    })


def _spoken_text_from_formulas(text: str, formulas: list[dict]) -> str:
    spoken = text or ""
    for idx, formula in enumerate(formulas or []):
        if not isinstance(formula, dict):
            continue
        placeholder = str(formula.get("id") or f"[[FORMULA_{idx}]]")
        replacement = str(formula.get("spoken") or "")
        if not replacement:
            label = str(formula.get("label") or "").strip()
            replacement = f"equation {label}" if label else f"equation {idx + 1}"
        spoken = spoken.replace(placeholder, replacement)
    return spoken


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
            span_id = getattr(span, "span_id", None)
            params = {
                "document_id": _strip_nuls(document_id),
                "chunk_id": chunk_id,
                "source_scope": _json_dumps({
                    "section_id": getattr(span, "section_id", None),
                    "block_id": getattr(span, "block_id", None),
                    "span_id": span_id,
                    "legacy_ids": sorted(_claim_legacy_keys({"span_id": span_id})),
                    # span.reason は LLM 判定理由 (review note) であり PDF 原文根拠ではない。
                    # source-backed 判定の根拠として evidence_text に保存しない (#257)。
                    "qualification_reason": _strip_nuls(getattr(span, "reason", "") or ""),
                }),
                "claim_type": _normalize_claim_type(qualification),
                "text": _strip_nuls(getattr(span, "text", "") or ""),
                "normalized_text": _strip_nuls(getattr(span, "text", "") or ""),
                "concepts": _json_dumps([]),
                "equation": _json_dumps({}),
                "support_status": "source_backed",
                # PDF 原文根拠は EvidenceRegistry artifact に委任する (#257)。
                # span.reason を evidence_text に保存すると LLM 判定理由が
                # source-backed evidence として扱われるため空文字に変更。
                "evidence_text": "",
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
    claim_id_map: dict[str, str] | None = None,
) -> dict[str, str]:
    """ComponentAssemblyResult.components を `theory_components` に保存する。

    Returns:
        agent component_id → DB UUID のマッピング（dependency 解決用）。
    """
    components = list(getattr(component_result, "components", []) or [])
    if not components:
        return {}

    # deterministic_fallback component は通常成果物として永続化しない (#347)。
    # ExportValidationGate が persist 前にブロックするのが正規経路だが、
    # 旧 artifact からの resume 等でここまで届いた場合の最終ガード。
    # 全件 fallback の場合は hard fail にする: silently return すると
    # 同 document の既存 theory_components（前回 run の通常成果物）が
    # 削除も置換もされず「成功」扱いのまま downstream に見え続けるため、
    # 例外で run を failed にして再処理が必要なことを明示する。
    fallback_components = [
        c for c in components
        if str(getattr(c, "maturity_source", "") or "") == "deterministic_fallback"
    ]
    if fallback_components:
        fallback_reason = str(
            getattr(fallback_components[0], "fallback_reason", "") or "unknown"
        )
        components = [c for c in components if c not in fallback_components]
        if not components:
            raise DeterministicFallbackPersistError(
                f"persist_components blocked for document={document_id}: all "
                f"{len(fallback_components)} component(s) are deterministic-fallback "
                f"(fallback_reason={fallback_reason!r}); rerun component_assembly "
                "instead of persisting. Existing theory_components for this "
                "document are left untouched."
            )
        logger.warning(
            "persist_components: skipping %d deterministic-fallback component(s) "
            "for document=%s (fallback_reason=%r); they remain in the stage "
            "artifact only and are not persisted to theory_components",
            len(fallback_components),
            document_id,
            fallback_reason,
        )

    id_map: dict[str, str] = {}
    claim_id_map = claim_id_map or {}
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
            evidence_refs = dict(getattr(comp, "evidence_refs", {}) or {})
            if isinstance(evidence_refs.get("claim_ids"), list):
                evidence_refs["claim_ids"] = _remap_string_list(evidence_refs["claim_ids"], claim_id_map)
            inputs = _remap_nested_claim_refs(getattr(comp, "inputs", []) or [], claim_id_map)
            outputs = _remap_nested_claim_refs(getattr(comp, "outputs", []) or [], claim_id_map)
            preconditions = _remap_nested_claim_refs(getattr(comp, "preconditions", []) or [], claim_id_map)
            cautions = _remap_nested_claim_refs(getattr(comp, "cautions", []) or [], claim_id_map)
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
                    evidence_refs.get("source_chunks") or []
                ),
                "inputs": _json_dumps(inputs),
                "outputs": _json_dumps(outputs),
                "preconditions": _json_dumps(preconditions),
                "constraints": _json_dumps([]),
                "invalid_conditions": _json_dumps([]),
                "dependencies": _json_dumps(getattr(comp, "dependencies", []) or []),
                "blackbox_policy": _json_dumps({
                    "default_level": "summary",
                    "expand_if_unlearned": True,
                }),
                "validation_warnings": _json_dumps([]),
                "teacher_notes": "",
                "source_scope": _json_dumps({
                    "document_id": document_id,
                    "legacy_ids": [getattr(comp, "component_id", "")],
                }),
                "evidence_claims": _json_dumps(
                    evidence_refs.get("claim_ids") or []
                ),
                "maturity_level": "paper_claim",
                "maturity_source": _strip_nuls(
                    getattr(comp, "maturity_source", "") or "llm_proposed"
                ),
                "review_status": _strip_nuls(
                    getattr(comp, "review_status", "") or "teacher_review_required"
                ),
                "cautions": _json_dumps(cautions),
                "connectors": _json_dumps({}),
                "internal_flow": _json_dumps(getattr(comp, "internal_flow", []) or []),
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


def _split_main_label(label: str) -> tuple[str, str]:
    """Lazy wrapper around the shared stage-label splitter (issue #319)."""
    try:
        from episteme_graph.agents.component_graph.schema import split_main_label
    except Exception:  # pragma: no cover - agents package optional in some contexts
        return str(label or "").strip(), ""
    return split_main_label(label)


def _narrative_payload(narrative_result, component_id_map: dict[str, str]) -> dict:
    """NarrativeAnnotator output keyed by stored DB component ids (issue #360)."""
    if narrative_result is None:
        return {}
    node_map: dict[str, dict] = {}
    for narrative in getattr(narrative_result, "node_narratives", []) or []:
        agent_id = str(getattr(narrative, "component_id", "") or "")
        if not agent_id:
            continue
        db_id = component_id_map.get(agent_id, agent_id)
        node_map[db_id] = {
            "narrative_role": str(getattr(narrative, "narrative_role", "") or ""),
            "reason": str(getattr(narrative, "reason", "") or ""),
            "confidence": float(getattr(narrative, "confidence", 0.0) or 0.0),
        }
    edge_map: dict[str, dict] = {}
    for narrative in getattr(narrative_result, "edge_narratives", []) or []:
        edge_id = str(getattr(narrative, "edge_id", "") or "")
        if not edge_id:
            continue
        edge_map[edge_id] = {
            "transition_text": str(getattr(narrative, "transition_text", "") or ""),
            "reason": str(getattr(narrative, "reason", "") or ""),
            "confidence": float(getattr(narrative, "confidence", 0.0) or 0.0),
        }
    graph_summary = str(getattr(narrative_result, "graph_summary", "") or "")
    if not graph_summary and not node_map and not edge_map:
        return {}
    return {
        "graph_summary": graph_summary,
        "maturity_source": str(
            getattr(narrative_result, "maturity_source", "") or "llm_proposed"
        ),
        "node_narratives": node_map,
        "edge_narratives": edge_map,
    }


def _normalize_graph_payload_for_persist(graph: dict) -> dict:
    """Guarantee the persisted graph satisfies the stored-graph invariants (issue #319).

    Before save we enforce, deterministically:
      * main ``TheoryOperationNode`` labels are short stage names only;
      * the long label remainder is preserved in ``description``;
      * every node / edge carries a ``source_backing_status`` key;
      * ``review_required`` / ``inferred`` nodes & edges have non-empty
        ``review_reasons``.
    """
    for node in graph.get("nodes", []) or []:
        if not isinstance(node, dict):
            continue
        layer = str(node.get("graph_layer") or "main")
        ctype = str(node.get("component_type") or node.get("type") or "")
        if layer == "main" and ctype == "TheoryOperationNode":
            short_label, remainder = _split_main_label(str(node.get("label") or ""))
            if short_label:
                node["label"] = short_label
            if not str(node.get("description") or "") and remainder:
                node["description"] = remainder
        label = str(node.get("label") or "")
        theory_object = str(node.get("theory_object") or "")
        if not str(node.get("display_label") or ""):
            node["display_label"] = f"{label}: {theory_object}" if theory_object else label
        for key in (
            "linked_component_ids",
            "detail_node_ids",
            "supporting_derivation_ids",
        ):
            if not isinstance(node.get(key), list):
                node[key] = []
        node["representative_component_id"] = str(node.get("representative_component_id") or "")
        backing = str(node.get("source_backing_status") or "")
        node["source_backing_status"] = backing
        reasons = node.get("review_reasons") if isinstance(node.get("review_reasons"), list) else []
        review_status = str(node.get("review_status") or "")
        if not reasons and (backing in ("review_required", "inferred") or review_status == "review_required"):
            node["review_reasons"] = [
                "fallback_or_inferred_node" if backing == "inferred" else "missing_evidence_link"
            ]
        else:
            node["review_reasons"] = reasons
    for edge in graph.get("edges", []) or []:
        if not isinstance(edge, dict):
            continue
        backing = str(edge.get("source_backing_status") or "")
        edge["source_backing_status"] = backing
        reasons = edge.get("review_reasons") if isinstance(edge.get("review_reasons"), list) else []
        review_status = str(edge.get("review_status") or "")
        if not reasons and (backing in ("review_required", "inferred") or review_status == "review_required"):
            edge["review_reasons"] = ["edge_not_source_backed"]
        else:
            edge["review_reasons"] = reasons
    return graph


def persist_component_graph(
    *,
    document_id: str,
    component_id_map: dict[str, str],
    component_result,
    dsl_result,
    course_id: str | None = None,
    component_graph_result=None,
    claim_id_map: dict[str, str] | None = None,
    narrative_result=None,
) -> str | None:
    """document scope の component graph を `theory_component_graphs` に保存。

    component_graph_result が指定された場合（ComponentGraphAgent の出力）は
    そのエッジ情報を優先し、source_component_id/target_component_id/relation/
    edge_type/evidence スキーマで保存する。
    指定されない場合は component_result.components[].dependencies から
    フォールバックエッジを生成する。
    """
    claim_id_map = claim_id_map or {}
    if component_graph_result is not None:
        # ComponentGraphAgent の結果を使い、エージェントIDをDB IDに変換してエッジ化
        payload = component_graph_result.to_graph_payload()
        nodes = []
        seen_node_ids: set[str] = set()
        for node in payload.get("nodes", []):
            if not isinstance(node, dict):
                continue
            agent_id = str(node.get("component_id") or node.get("id") or "").strip()
            if not agent_id:
                continue
            db_id = component_id_map.get(agent_id, agent_id)
            if db_id in seen_node_ids:
                continue
            seen_node_ids.add(db_id)
            stored_node = dict(node)
            stored_node["id"] = db_id
            stored_node["component_id"] = db_id
            stored_node["agent_component_id"] = agent_id
            stored_node.setdefault("type", "component")
            nodes.append(stored_node)
        edges = []
        for e in payload.get("edges", []):
            src_agent_id = e.get("source_component_id", "")
            dst_agent_id = e.get("target_component_id", "")
            src_db = component_id_map.get(src_agent_id, src_agent_id)
            dst_db = component_id_map.get(dst_agent_id, dst_agent_id)
            evidence = _remap_nested_claim_refs(
                e.get("evidence", {"evidence_claims": [], "reason": ""}),
                claim_id_map,
            )
            edges.append({
                "source_component_id": src_db,
                "target_component_id": dst_db,
                "relation": e.get("relation", "RELATED_TO"),
                "edge_type": e.get("edge_type", e.get("relation", "RELATED_TO")),
                "support_status": e.get("support_status", "llm_inferred"),
                "confidence": e.get("confidence", 0.0),
                # node と同じ source-backing 語彙を edge にも保存する (issue #311/#319)。
                # 落とすと API/UI 側で source_backing_status を表示・検証できなくなる。
                "source_backing_status": e.get("source_backing_status", ""),
                "review_status": e.get("review_status", "teacher_review_required"),
                # review_required edge の理由 (issue #302/#304) を保存する。
                # 落とすと API/UI 側で review 理由を表示・検証できなくなる。
                "review_reasons": list(e.get("review_reasons") or []),
                "evidence": evidence,
                "edge_id": e.get("edge_id", ""),
            })
        validation_issues = [
            {"rule_id": v.rule_id, "severity": v.severity, "message": v.message}
            for v in getattr(component_graph_result, "validation_issues", [])
        ]
    else:
        nodes = []
        for agent_id, db_id in component_id_map.items():
            nodes.append({
                "id": db_id,
                "agent_component_id": agent_id,
                "component_id": db_id,
                "type": "component",
            })
        # フォールバック: dependencies ベースの確定的エッジ生成
        edges = []
        for comp in getattr(component_result, "components", []) or []:
            src_db = component_id_map.get(getattr(comp, "component_id", ""))
            if not src_db:
                continue
            for dep in getattr(comp, "dependencies", []) or []:
                if not isinstance(dep, dict):
                    continue
                dep_type = dep.get("dependency_type") or "depends_on"
                relation = (
                    "REQUIRES" if dep_type in ("requires", "depends_on")
                    else "TRANSFORMS" if dep_type == "transforms"
                    else "ENABLES" if dep_type in ("enables", "supports")
                    else "RELATED_TO"
                )
                for ref in dep.get("component_refs") or []:
                    dst_db = component_id_map.get(ref)
                    if not dst_db:
                        continue
                    edges.append({
                        "source_component_id": src_db,
                        "target_component_id": dst_db,
                        "relation": relation,
                        "edge_type": relation,
                        "support_status": "dependency_declared",
                        "confidence": 1.0,
                        "review_status": "teacher_review_required",
                        "evidence": {
                            "evidence_claims": [],
                            "reason": dep.get("reason") or "",
                        },
                    })
        validation_issues = []

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

    graph = _normalize_graph_payload_for_persist({
        "graph_id": f"graph_{document_id}",
        "document_id": document_id,
        "graph_schema_version": "0.1.0",
        "scope": {"level": "paper"},
        "nodes": nodes,
        "edges": edges,
        "dsl": {"nodes": dsl_nodes, "edges": dsl_edges},
        "validation_results": validation_issues,
    })
    # Reading layer from NarrativeAnnotator (issue #360): stored as a sibling
    # block, never merged into nodes/edges, always provisional.
    narrative_payload = _narrative_payload(narrative_result, component_id_map)
    if narrative_payload:
        graph["narrative"] = narrative_payload

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


def get_latest_analysis_run(
    *,
    document_id: str,
    material_id: str | None = None,
) -> dict | None:
    """Return the latest analysis run for resume/status inspection."""
    session = _pg_session()
    try:
        row = session.execute(
            sa_text(
                """
                SELECT id::text, document_id::text, material_id, cartridge_id, status,
                       current_stage, error_message, stage_outputs, started_at,
                       completed_at, created_at, updated_at
                FROM document_analysis_runs
                WHERE document_id = :document_id
                  AND (:material_id IS NULL OR material_id = :material_id)
                ORDER BY created_at DESC
                LIMIT 1
                """
            ),
            {"document_id": document_id, "material_id": material_id},
        ).mappings().fetchone()
        return dict(row) if row else None
    finally:
        session.close()


# ----------------------------------------------------------------------------
# Run version management (#402)
#
# 「最新Run」(get_latest_analysis_run) と「採用Run」(get_active_analysis_run)
# を明確に区別する。export/API は成果物参照には active run を、処理状況確認には
# latest run を使う。revision run は必ず base_run_id を持つ。
# ----------------------------------------------------------------------------

# Column list for analysis-run SELECTs, qualified with the ``r`` table alias so
# it is unambiguous when the run table is joined with ``documents`` (which shares
# column names like ``id`` / ``document_id``) — see get_active_analysis_run.
_RUN_COLUMNS = (
    "r.id::text, r.document_id::text, r.material_id, r.cartridge_id, r.status, "
    "r.current_stage, r.error_message, r.stage_outputs, r.started_at, r.completed_at, "
    "r.created_at, r.updated_at, r.run_type, r.base_run_id::text, "
    "r.parent_revision_id::text, r.revision_status, r.created_by::text"
)


def get_analysis_run(*, run_id: str) -> dict | None:
    """Fetch a single analysis run by id (latest *or* candidate)."""
    session = _pg_session()
    try:
        row = session.execute(
            sa_text(
                f"""
                SELECT {_RUN_COLUMNS}
                FROM document_analysis_runs r
                WHERE r.id = CAST(:run_id AS uuid)
                """
            ),
            {"run_id": run_id},
        ).mappings().fetchone()
        return dict(row) if row else None
    finally:
        session.close()


def get_active_analysis_run(*, document_id: str) -> dict | None:
    """Return the *adopted* (active) analysis run for a document.

    成果物参照に使う採用Run。latest run とは別物であり、混同しないこと。
    active run が未設定の document（旧データ）では None を返す。呼び出し側は
    必要なら get_latest_analysis_run() への後方互換 fallback を判断する。
    """
    session = _pg_session()
    try:
        row = session.execute(
            sa_text(
                f"""
                SELECT {_RUN_COLUMNS}
                FROM document_analysis_runs r
                JOIN documents d ON d.active_analysis_run_id = r.id
                WHERE d.id = CAST(:document_id AS uuid)
                """
            ),
            {"document_id": document_id},
        ).mappings().fetchone()
        return dict(row) if row else None
    finally:
        session.close()


def get_active_analysis_run_id(*, document_id: str) -> str | None:
    """Return only documents.active_analysis_run_id (no run join)."""
    session = _pg_session()
    try:
        row = session.execute(
            sa_text(
                """
                SELECT active_analysis_run_id::text
                FROM documents
                WHERE id = CAST(:document_id AS uuid)
                """
            ),
            {"document_id": document_id},
        ).fetchone()
        return str(row[0]) if row and row[0] else None
    finally:
        session.close()


def resolve_artifact_run(*, document_id: str, material_id: str | None = None) -> dict | None:
    """Return the run whose artifacts should be exported/used.

    採用Run優先。active が無い旧 document は最新 completed run へ後方互換 fallback。
    """
    active = get_active_analysis_run(document_id=document_id)
    if active is not None:
        return active
    latest = get_latest_analysis_run(document_id=document_id, material_id=material_id)
    if latest is not None and latest.get("status") == "completed":
        return latest
    return None


def resolve_artifact_runs(session, document_ids: list[str]) -> dict[str, dict]:
    """Resolve the *artifact* (adopted) run per document, using a caller session.

    For each document, prefer ``documents.active_analysis_run_id``; otherwise fall
    back to the latest **completed** run (never a running/failed latest run, so a
    fresh in-flight or rejected revision can't override the adopted artifacts).
    Returns ``{document_id: {"run_id", "stage_outputs", "status"}}`` for documents
    that have a resolvable artifact run (#408).
    """
    if not document_ids:
        return {}
    placeholders = ", ".join(f":doc_{i}" for i in range(len(document_ids)))
    params = {f"doc_{i}": did for i, did in enumerate(document_ids)}
    rows = session.execute(
        sa_text(
            f"""
            WITH targets AS (
                SELECT d.id::text AS document_id,
                       COALESCE(
                           d.active_analysis_run_id,
                           (SELECT r2.id FROM document_analysis_runs r2
                            WHERE r2.document_id = d.id::text AND r2.status = 'completed'
                            ORDER BY r2.completed_at DESC NULLS LAST,
                                     r2.created_at DESC, r2.id DESC
                            LIMIT 1)
                       ) AS run_id
                FROM documents d
                WHERE d.id::text IN ({placeholders})
            )
            SELECT t.document_id, r.id::text, r.stage_outputs, r.status
            FROM targets t
            JOIN document_analysis_runs r ON r.id = t.run_id
            """
        ),
        params,
    ).fetchall()
    out: dict[str, dict] = {}
    for row in rows:
        doc_id = str(row[0]) if row[0] else ""
        if not doc_id:
            continue
        stage_outputs = row[2]
        if isinstance(stage_outputs, str):
            try:
                stage_outputs = json.loads(stage_outputs)
            except (ValueError, TypeError):
                stage_outputs = {}
        out[doc_id] = {
            "run_id": str(row[1]) if row[1] else None,
            "stage_outputs": stage_outputs if isinstance(stage_outputs, dict) else {},
            "status": row[3],
        }
    return out


def create_revision_run(
    *,
    document_id: str,
    base_run_id: str,
    material_id: str | None = None,
    cartridge_id: str | None = None,
    parent_revision_id: str | None = None,
    created_by: str | None = None,
    revision_status: str = "preparing",
    stage_outputs: dict | None = None,
) -> str:
    """Create a revision (candidate) run.

    revision run は必ず比較元の base_run_id を持つ。base_run_id を省略すると
    ValueError を送出する（DB CHECK 制約と二重防御）。
    candidate run は projection table を更新せず、artifact のみを保持する。
    """
    if not base_run_id:
        raise ValueError("create_revision_run requires a non-empty base_run_id")
    session = _pg_session()
    try:
        row = session.execute(
            sa_text(
                """
                INSERT INTO document_analysis_runs (
                    document_id, material_id, cartridge_id, status,
                    stage_outputs, run_type, base_run_id, parent_revision_id,
                    revision_status, created_by
                )
                VALUES (
                    :document_id, :material_id, :cartridge_id, 'pending',
                    CAST(:stage_outputs AS jsonb), 'revision',
                    CAST(:base_run_id AS uuid),
                    CAST(:parent_revision_id AS uuid),
                    :revision_status,
                    CAST(:created_by AS uuid)
                )
                RETURNING id::text
                """
            ),
            {
                "document_id": document_id,
                "material_id": material_id,
                "cartridge_id": cartridge_id,
                "stage_outputs": _json_dumps(stage_outputs or {}),
                "base_run_id": base_run_id,
                "parent_revision_id": parent_revision_id,
                "revision_status": revision_status,
                "created_by": created_by,
            },
        ).fetchone()
        session.commit()
        return str(row[0])
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def update_revision_status(
    *,
    run_id: str,
    revision_status: str,
    status: str | None = None,
    current_stage: str | None = None,
    error_message: str | None = None,
    stage_outputs: dict | None = None,
) -> None:
    """Update a revision run's revision_status (and optionally run status / artifacts).

    Artifact merge is **deep at the ``_artifacts`` level** (#410 P0): PostgreSQL's
    JSONB ``||`` only replaces top-level keys, so a shallow merge would wipe every
    previously-stored artifact (baseline_inventory / audit_results / candidate / …)
    each time a new stage writes ``{"_artifacts": {...}}``. We therefore merge the
    incoming ``_artifacts`` into the existing ``_artifacts`` (preserving sibling
    artifact keys) and shallow-merge any other top-level keys — all in a single
    atomic SQL statement so concurrent stage writes cannot lose keys.
    """
    payload = dict(stage_outputs or {})
    artifacts_delta = payload.pop(ARTIFACTS_KEY, None)
    session = _pg_session()
    try:
        session.execute(
            sa_text(
                f"""
                UPDATE document_analysis_runs SET
                    revision_status = :revision_status,
                    status = COALESCE(:status, status),
                    current_stage = COALESCE(:current_stage, current_stage),
                    error_message = COALESCE(:error_message, error_message),
                    stage_outputs = jsonb_set(
                        COALESCE(stage_outputs, '{{}}'::jsonb) || CAST(:other AS jsonb),
                        '{{{ARTIFACTS_KEY}}}',
                        COALESCE(stage_outputs->'{ARTIFACTS_KEY}', '{{}}'::jsonb)
                            || CAST(:artifacts AS jsonb)
                    ),
                    started_at = CASE WHEN :status = 'running' AND started_at IS NULL
                                      THEN now() ELSE started_at END,
                    completed_at = CASE WHEN :status IN ('completed', 'failed')
                                        THEN now() ELSE completed_at END,
                    updated_at = now()
                WHERE id = CAST(:run_id AS uuid)
                  AND run_type = 'revision'
                """
            ),
            {
                "run_id": run_id,
                "revision_status": revision_status,
                "status": status,
                "current_stage": current_stage,
                "error_message": error_message,
                "other": _json_dumps(payload),
                "artifacts": _json_dumps(artifacts_delta or {}),
            },
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def set_active_analysis_run(
    *,
    document_id: str,
    run_id: str,
    expected_run_id: str | None,
) -> bool:
    """Switch documents.active_analysis_run_id with optimistic concurrency.

    accept 時に base_run_id を expected として渡す。0件更新（=採用Runが既に
    別Runへ進んでいる）の場合は競合として False を返す。`IS NOT DISTINCT FROM`
    で NULL（active 未設定）も扱える。
    """
    session = _pg_session()
    try:
        result = session.execute(
            sa_text(
                """
                UPDATE documents
                SET active_analysis_run_id = CAST(:run_id AS uuid),
                    updated_at = now()
                WHERE id = CAST(:document_id AS uuid)
                  AND active_analysis_run_id IS NOT DISTINCT FROM CAST(:expected AS uuid)
                """
            ),
            {
                "run_id": run_id,
                "document_id": document_id,
                "expected": expected_run_id,
            },
        )
        session.commit()
        return result.rowcount == 1
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_run_lineage(*, document_id: str) -> dict:
    """Return run lineage for a document: every run + the current active run id.

    Output keeps latest/active distinct so callers never conflate them.
    """
    session = _pg_session()
    try:
        active_row = session.execute(
            sa_text(
                """
                SELECT active_analysis_run_id::text
                FROM documents
                WHERE id = CAST(:document_id AS uuid)
                """
            ),
            {"document_id": document_id},
        ).fetchone()
        active_run_id = str(active_row[0]) if active_row and active_row[0] else None

        rows = session.execute(
            sa_text(
                """
                SELECT id::text, status, run_type, base_run_id::text,
                       parent_revision_id::text, revision_status,
                       current_stage, created_by::text, created_at, completed_at
                FROM document_analysis_runs
                WHERE document_id = :document_id
                ORDER BY created_at ASC, id ASC
                """
            ),
            {"document_id": document_id},
        ).mappings().all()
        runs = [dict(r) for r in rows]
        latest_run_id = runs[-1]["id"] if runs else None
        for run in runs:
            run["is_active"] = run["id"] == active_run_id
            run["is_latest"] = run["id"] == latest_run_id
        return {
            "document_id": document_id,
            "active_run_id": active_run_id,
            "latest_run_id": latest_run_id,
            "runs": runs,
        }
    finally:
        session.close()


class RevisionConflictError(RuntimeError):
    """Raised when a revision accept loses the optimistic concurrency race."""


def _insert_revision_decision(
    session,
    *,
    run_id: str,
    old_status: str,
    new_status: str,
    changed_by: str | None,
    metadata: dict,
) -> None:
    session.execute(
        sa_text(
            """
            INSERT INTO theory_review_events (
                entity_type, entity_id, old_status, new_status, changed_by, metadata
            )
            VALUES (
                'revision_run', :entity_id, :old_status, :new_status,
                CAST(:changed_by AS uuid), CAST(:metadata AS jsonb)
            )
            """
        ),
        {
            "entity_id": run_id,
            "old_status": old_status or "",
            "new_status": new_status or "",
            "changed_by": changed_by,
            "metadata": _json_dumps(metadata or {}),
        },
    )


def get_revision_decisions(*, run_id: str) -> list[dict]:
    """Return the decision audit history for a revision run."""
    session = _pg_session()
    try:
        rows = session.execute(
            sa_text(
                """
                SELECT old_status, new_status, changed_by::text, metadata, created_at
                FROM theory_review_events
                WHERE entity_type = 'revision_run' AND entity_id = :run_id
                ORDER BY created_at ASC
                """
            ),
            {"run_id": run_id},
        ).mappings().all()
        return [dict(r) for r in rows]
    finally:
        session.close()


def load_revision_projection_overlay(*, document_id: str) -> dict:
    """Load the current editable projections and their review history.

    Revision inventory starts from immutable run artifacts, then overlays these
    rows so post-pipeline manual edits and teacher decisions are not lost.
    """
    session = _pg_session()
    try:
        claims = session.execute(
            sa_text(
                """
                SELECT id::text, source_scope, claim_type, text, normalized_text,
                       concepts, equation, support_status, evidence_text,
                       review_status, updated_at
                FROM theory_claims
                WHERE document_id = :doc
                """
            ),
            {"doc": document_id},
        ).mappings().all()
        components = session.execute(
            sa_text(
                """
                SELECT id::text, name, component_type_text, summary, status,
                       source_scope, evidence_claims, review_status, inputs,
                       outputs, preconditions, constraints, invalid_conditions,
                       dependencies, updated_at
                FROM theory_components
                WHERE document_id = :doc
                """
            ),
            {"doc": document_id},
        ).mappings().all()
        entity_ids = [
            str(row["id"]) for row in [*claims, *components] if row.get("id")
        ]
        events: list[dict] = []
        if entity_ids:
            events = [
                dict(row)
                for row in session.execute(
                    sa_text(
                        """
                        SELECT entity_type, entity_id, old_status, new_status,
                               metadata, created_at
                        FROM theory_review_events
                        WHERE entity_id = ANY(CAST(:entity_ids AS text[]))
                          AND entity_type IN ('claim', 'component')
                        ORDER BY created_at ASC, id ASC
                        """
                    ),
                    {"entity_ids": entity_ids},
                ).mappings().all()
            ]
        return {
            "claims": [dict(row) for row in claims],
            "components": [dict(row) for row in components],
            "review_events": events,
        }
    finally:
        session.close()


# Allowed theory_claims.claim_type values (migration 013 CHECK). Candidate
# claim types outside this set fall back to the generic diagnostic_claim.
_THEORY_CLAIM_TYPES = {
    "definition", "assumption", "approximation", "equation", "relation",
    "derivation_step", "observable_definition", "correction", "uncertainty",
    "limitation", "result", "diagnostic_claim", "equation_definition",
    "equation_relation", "equation_transformation", "equation_approximation",
    "equation_constraint",
}


def _rebuild_theory_claims_in_session(session, document_id: str, claims: list) -> dict[str, str]:
    """Rebuild theory_claims from candidate claim_object_builder claims (#410 P1-5).

    Operates on the caller's transaction (no commit). Returns agent claim_id → db id.
    """
    session.execute(
        sa_text("DELETE FROM theory_claims WHERE document_id = :doc"),
        {"doc": document_id},
    )
    id_map: dict[str, str] = {}
    for c in claims or []:
        if not isinstance(c, dict):
            continue
        agent_id = str(c.get("claim_id") or "")
        ctype = c.get("claim_type") or "diagnostic_claim"
        if ctype not in _THEORY_CLAIM_TYPES:
            ctype = "diagnostic_claim"
        equation_ids = [str(v) for v in (c.get("equation_ids") or []) if v]
        source_scope = dict(c.get("source_scope") or {"section_id": c.get("section_id")})
        legacy_ids = [str(v) for v in (source_scope.get("legacy_ids") or []) if v]
        if agent_id and agent_id not in legacy_ids:
            legacy_ids.append(agent_id)
        source_scope["legacy_ids"] = legacy_ids
        row = session.execute(
            sa_text(
                """
                INSERT INTO theory_claims (
                    document_id, source_scope, claim_type, text, normalized_text,
                    concepts, equation, support_status, evidence_text, review_status
                )
                VALUES (
                    :document_id, CAST(:source_scope AS jsonb), :claim_type, :text,
                    :normalized_text, CAST(:concepts AS jsonb), CAST(:equation AS jsonb),
                    :support_status, :evidence_text, :review_status
                )
                RETURNING id
                """
            ),
            {
                "document_id": _strip_nuls(document_id),
                "source_scope": _json_dumps(source_scope),
                "claim_type": ctype,
                "text": _strip_nuls(c.get("text") or ""),
                "normalized_text": _strip_nuls(c.get("normalized_text") or c.get("text") or ""),
                "concepts": _json_dumps(c.get("concepts") or []),
                "equation": _json_dumps({"equation_ids": equation_ids} if equation_ids else {}),
                "support_status": c.get("support_status") or "source_backed",
                "evidence_text": "",
                "review_status": c.get("review_status") or "teacher_review_required",
            },
        ).fetchone()
        if agent_id:
            id_map[agent_id] = str(row[0])
    return id_map


def _rebuild_theory_components_in_session(
    session, document_id: str, components: list, claim_id_map: dict[str, str]
) -> dict[str, str]:
    """Rebuild theory_components + theory_component_links from candidate components."""
    session.execute(
        sa_text("DELETE FROM theory_component_links WHERE document_id = :doc"),
        {"doc": document_id},
    )
    session.execute(
        sa_text("DELETE FROM theory_components WHERE document_id = :doc"),
        {"doc": document_id},
    )
    claim_id_map = claim_id_map or {}
    id_map: dict[str, str] = {}
    for comp in components or []:
        if not isinstance(comp, dict):
            continue
        agent_id = str(comp.get("component_id") or "")
        evidence_refs = comp.get("evidence_refs") if isinstance(comp.get("evidence_refs"), dict) else {}
        linked_claim_ids = [str(v) for v in (comp.get("linked_claim_ids") or []) if v]
        evidence_claims_agent = list(dict.fromkeys(
            linked_claim_ids + [str(v) for v in (evidence_refs.get("claim_ids") or []) if v]
        ))
        evidence_claims_db = [claim_id_map.get(a, a) for a in evidence_claims_agent]
        row = session.execute(
            sa_text(
                """
                INSERT INTO theory_components (
                    course_id, document_id, name, component_type, component_type_text,
                    summary, status, source_chunks, inputs, outputs, preconditions,
                    constraints, invalid_conditions, dependencies, blackbox_policy,
                    validation_warnings, teacher_notes, source_scope, evidence_claims,
                    maturity_level, maturity_source, review_status, cautions,
                    connectors, internal_flow, duplicate_candidates
                )
                VALUES (
                    NULL, :document_id, :name, 'theory', :component_type_text,
                    :summary, 'candidate', CAST(:source_chunks AS jsonb),
                    CAST(:inputs AS jsonb), CAST(:outputs AS jsonb),
                    CAST(:preconditions AS jsonb), CAST(:constraints AS jsonb),
                    CAST(:invalid_conditions AS jsonb), CAST(:dependencies AS jsonb),
                    CAST(:blackbox_policy AS jsonb), CAST(:validation_warnings AS jsonb),
                    :teacher_notes, CAST(:source_scope AS jsonb),
                    CAST(:evidence_claims AS jsonb), :maturity_level, :maturity_source,
                    :review_status, CAST(:cautions AS jsonb), CAST(:connectors AS jsonb),
                    CAST(:internal_flow AS jsonb), CAST(:duplicate_candidates AS jsonb)
                )
                RETURNING id
                """
            ),
            {
                "document_id": document_id,
                "name": _strip_nuls(comp.get("label") or comp.get("name") or "Untitled"),
                "component_type_text": _strip_nuls(comp.get("component_type") or comp.get("responsibility_type") or ""),
                "summary": _strip_nuls(comp.get("summary") or ""),
                "source_chunks": _json_dumps(evidence_refs.get("source_chunks") or []),
                "inputs": _json_dumps(comp.get("inputs") or []),
                "outputs": _json_dumps(comp.get("outputs") or []),
                "preconditions": _json_dumps(comp.get("preconditions") or []),
                "constraints": _json_dumps(comp.get("constraints") or []),
                "invalid_conditions": _json_dumps(comp.get("invalid_conditions") or []),
                "dependencies": _json_dumps(comp.get("dependencies") or []),
                "blackbox_policy": _json_dumps({"default_level": "summary", "expand_if_unlearned": True}),
                "validation_warnings": _json_dumps([]),
                "teacher_notes": _strip_nuls(comp.get("teaching_takeaway") or ""),
                "source_scope": _json_dumps({"document_id": document_id, "legacy_ids": [agent_id]}),
                "evidence_claims": _json_dumps(evidence_claims_db),
                "maturity_level": comp.get("maturity_level") or "paper_claim",
                "maturity_source": comp.get("maturity_source") or "llm_proposed",
                "review_status": comp.get("review_status") or "teacher_review_required",
                "cautions": _json_dumps(comp.get("cautions") or []),
                "connectors": _json_dumps(comp.get("connectors") or {}),
                "internal_flow": _json_dumps(comp.get("internal_flow") or []),
                "duplicate_candidates": _json_dumps([]),
            },
        ).fetchone()
        if agent_id:
            id_map[agent_id] = str(row[0])

    # Dependency links.
    for comp in components or []:
        if not isinstance(comp, dict):
            continue
        src_db = id_map.get(str(comp.get("component_id") or ""))
        if not src_db:
            continue
        for dep in comp.get("dependencies") or []:
            if not isinstance(dep, dict):
                continue
            dep_type = dep.get("dependency_type") or "depends_on"
            link_type = "requires" if dep_type == "requires" else "depends_on"
            for ref in dep.get("component_refs") or []:
                dst_db = id_map.get(str(ref))
                if not dst_db or dst_db == src_db:
                    continue
                session.execute(
                    sa_text(
                        """
                        INSERT INTO theory_component_links (
                            course_id, document_id, source_component_id,
                            target_component_id, link_type, status, validation_result
                        )
                        VALUES (
                            NULL, :document_id, CAST(:src AS uuid), CAST(:dst AS uuid),
                            :link_type, 'candidate', CAST(:validation AS jsonb)
                        )
                        """
                    ),
                    {
                        "document_id": document_id, "src": src_db, "dst": dst_db,
                        "link_type": link_type,
                        "validation": _json_dumps({"agent_dependency_type": dep_type,
                                                   "reason": dep.get("reason")}),
                    },
                )
    return id_map


def _remap_revision_graph(
    graph_payload: dict,
    component_id_map: dict[str, str],
    claim_id_map: dict[str, str],
) -> dict:
    """Map candidate agent ids to the freshly-created projection UUIDs.

    Component nodes and all graph endpoints must resolve to stored nodes. Claim
    references embedded in nodes/edges are remapped recursively. Unknown
    component ids are rejected instead of being persisted as ghost nodes.
    """
    graph = copy.deepcopy(graph_payload or {})
    nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
    node_id_map: dict[str, str] = {}
    stored_node_ids: set[str] = set()

    for node in nodes:
        if not isinstance(node, dict):
            continue
        agent_id = str(
            node.get("component_id") or node.get("node_id") or node.get("id") or ""
        ).strip()
        if not agent_id:
            raise ValueError("component graph node is missing an id")
        is_component_node = (
            bool(node.get("component_id"))
            or agent_id in component_id_map
            or str(node.get("type") or "").lower() == "component"
        )
        if is_component_node:
            db_id = component_id_map.get(agent_id)
            if not db_id:
                raise ValueError(f"component graph references unknown component id: {agent_id}")
        else:
            db_id = agent_id
        if db_id in stored_node_ids:
            raise ValueError(f"component graph contains duplicate node id: {db_id}")
        stored_node_ids.add(db_id)
        node_id_map[agent_id] = db_id
        if "id" in node or "node_id" not in node:
            node["id"] = db_id
        if "node_id" in node:
            node["node_id"] = db_id
        if is_component_node:
            node["component_id"] = db_id
            node["agent_component_id"] = agent_id
        remapped = _remap_nested_claim_refs(node, claim_id_map)
        node.clear()
        node.update(remapped)

    edges = graph.get("edges") if isinstance(graph.get("edges"), list) else []
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        for key in (
            "source", "target", "from", "to",
            "source_component_id", "target_component_id",
        ):
            if key not in edge or edge[key] in (None, ""):
                continue
            agent_id = str(edge[key])
            db_id = node_id_map.get(agent_id)
            if not db_id or db_id not in stored_node_ids:
                raise ValueError(
                    f"component graph edge references unknown node id: {agent_id}"
                )
            edge[key] = db_id
        remapped = _remap_nested_claim_refs(edge, claim_id_map)
        edge.clear()
        edge.update(remapped)

    return _normalize_graph_payload_for_persist(graph)


def _rebuild_component_graph_in_session(
    session,
    document_id: str,
    graph_payload: dict,
    component_id_map: dict[str, str],
    claim_id_map: dict[str, str],
) -> None:
    remapped_graph = _remap_revision_graph(
        graph_payload, component_id_map or {}, claim_id_map or {}
    )
    session.execute(
        sa_text(
            """
            INSERT INTO theory_component_graphs (document_id, graph_json, scope, updated_at)
            VALUES (:doc, CAST(:graph AS jsonb), CAST(:scope AS jsonb), now())
            ON CONFLICT (document_id)
            DO UPDATE SET graph_json = EXCLUDED.graph_json, updated_at = now()
            """
        ),
        {"doc": document_id, "graph": _json_dumps(remapped_graph),
         "scope": _json_dumps({"level": "paper"})},
    )


_REVISION_ARTIFACT_KEYS = {
    "baseline_inventory",
    "audit_checkpoints",
    "audit_results",
    "proposed_operations",
    "revision_operations",
    "candidate",
    "candidate_validation",
    "diff_report",
}


def _materializable_candidate_artifacts(candidate_artifacts: dict) -> dict:
    """Return normal pipeline artifacts safe to promote on accept."""
    return {
        str(key): value
        for key, value in (candidate_artifacts or {}).items()
        if key not in _REVISION_ARTIFACT_KEYS
    }


def accept_revision(
    *,
    document_id: str,
    run_id: str,
    expected_base_run_id: str | None,
    changed_by: str | None = None,
    comment: str = "",
    candidate_artifacts: dict | None = None,
) -> dict:
    """Accept a candidate revision in a single transaction (#407 / #410 P1-5).

    Switches the document active run base→candidate with optimistic concurrency,
    rebuilds ALL document-scoped projections (theory_claims, theory_components,
    theory_component_links, theory_component_graphs) from the candidate artifacts,
    marks the candidate accepted and the superseded revision base, and records a
    decision event — all in one transaction. Any projection failure rolls the
    whole thing back (active pointer + projections unchanged). Raises
    ``RevisionConflictError`` (→ 409) when the active run moved since the candidate
    was proposed, or the candidate is not ``proposed``.
    """
    candidate_artifacts = candidate_artifacts or {}
    promoted_artifacts = _materializable_candidate_artifacts(candidate_artifacts)
    session = _pg_session()
    try:
        row = session.execute(
            sa_text(
                """
                SELECT status, revision_status, base_run_id::text, run_type
                FROM document_analysis_runs
                WHERE id = CAST(:rid AS uuid)
                FOR UPDATE
                """
            ),
            {"rid": run_id},
        ).fetchone()
        if not row:
            raise ValueError(f"revision run {run_id} not found")
        _status, rev_status, base_id, run_type = row
        if run_type != "revision":
            raise ValueError(f"run {run_id} is not a revision run")
        if rev_status != "proposed":
            raise RevisionConflictError(
                f"revision {run_id} is not in 'proposed' state (revision_status={rev_status})"
            )
        if expected_base_run_id and base_id and expected_base_run_id != base_id:
            raise RevisionConflictError("base_run_id mismatch with candidate")

        # Materialize the adopted candidate into the normal artifact namespace.
        # Keep `_artifacts.candidate.candidate_artifacts` and all revision audit
        # metadata intact for lineage/diff inspection, while making the accepted
        # run readable by existing Export/Course Builder consumers.
        session.execute(
            sa_text(
                f"""
                UPDATE document_analysis_runs
                SET stage_outputs = jsonb_set(
                        COALESCE(stage_outputs, '{{}}'::jsonb),
                        '{{{ARTIFACTS_KEY}}}',
                        COALESCE(stage_outputs->'{ARTIFACTS_KEY}', '{{}}'::jsonb)
                            || CAST(:promoted AS jsonb)
                    ),
                    updated_at = now()
                WHERE id = CAST(:rid AS uuid)
                """
            ),
            {"rid": run_id, "promoted": _json_dumps(promoted_artifacts)},
        )

        switched = session.execute(
            sa_text(
                """
                UPDATE documents
                SET active_analysis_run_id = CAST(:cand AS uuid), updated_at = now()
                WHERE id = CAST(:doc AS uuid)
                  AND active_analysis_run_id IS NOT DISTINCT FROM CAST(:base AS uuid)
                """
            ),
            {"cand": run_id, "doc": document_id, "base": base_id},
        ).rowcount
        if switched != 1:
            raise RevisionConflictError(
                "active run changed since the candidate was proposed; refusing to accept"
            )

        # Projection rebuild (in-transaction): claims, components+links, graph —
        # all from the candidate artifacts. Any failure here rolls back the active
        # switch too (single transaction), so projections + active never diverge.
        claims = ((candidate_artifacts.get("claim_object_builder") or {}).get("claims")) or []
        components = ((candidate_artifacts.get("component_assembly") or {}).get("components")) or []
        graph_payload = candidate_artifacts.get("component_graph")
        claim_id_map = _rebuild_theory_claims_in_session(session, document_id, claims)
        component_id_map = _rebuild_theory_components_in_session(
            session, document_id, components, claim_id_map
        )
        if graph_payload is not None:
            _rebuild_component_graph_in_session(
                session,
                document_id,
                graph_payload,
                component_id_map,
                claim_id_map,
            )

        session.execute(
            sa_text(
                """
                UPDATE document_analysis_runs
                SET revision_status = 'accepted', status = 'completed',
                    completed_at = now(), updated_at = now()
                WHERE id = CAST(:rid AS uuid)
                """
            ),
            {"rid": run_id},
        )
        # The superseded base — only mark revisions; an initial base keeps NULL.
        if base_id:
            session.execute(
                sa_text(
                    """
                    UPDATE document_analysis_runs
                    SET revision_status = 'superseded', updated_at = now()
                    WHERE id = CAST(:base AS uuid) AND run_type = 'revision'
                    """
                ),
                {"base": base_id},
            )
        _insert_revision_decision(
            session, run_id=run_id, old_status="proposed", new_status="accepted",
            changed_by=changed_by,
            metadata={"decision": "accept", "comment": comment,
                      "document_id": document_id, "base_run_id": base_id},
        )
        session.commit()
        return {"accepted": True, "active_run_id": run_id, "superseded_run_id": base_id}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reject_revision(
    *,
    run_id: str,
    changed_by: str | None = None,
    comment: str = "",
) -> dict:
    """Reject a candidate revision. Never touches the active run or projections."""
    session = _pg_session()
    try:
        row = session.execute(
            sa_text(
                """
                SELECT revision_status, run_type
                FROM document_analysis_runs
                WHERE id = CAST(:rid AS uuid)
                FOR UPDATE
                """
            ),
            {"rid": run_id},
        ).fetchone()
        if not row:
            raise ValueError(f"revision run {run_id} not found")
        rev_status, run_type = row
        if run_type != "revision":
            raise ValueError(f"run {run_id} is not a revision run")
        if rev_status in ("accepted", "superseded"):
            raise RevisionConflictError(
                f"cannot reject revision in '{rev_status}' state"
            )
        session.execute(
            sa_text(
                """
                UPDATE document_analysis_runs
                SET revision_status = 'rejected', status = 'completed',
                    completed_at = now(), updated_at = now()
                WHERE id = CAST(:rid AS uuid)
                """
            ),
            {"rid": run_id},
        )
        _insert_revision_decision(
            session, run_id=run_id, old_status=rev_status or "proposed",
            new_status="rejected", changed_by=changed_by,
            metadata={"decision": "reject", "comment": comment},
        )
        session.commit()
        return {"rejected": True, "run_id": run_id}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def load_source_chunk_index(*, document_id: str) -> list[dict]:
    """Load persisted source chunk metadata for downstream evidence resolution."""
    session = _pg_session()
    try:
        rows = session.execute(
            sa_text(
                """
                SELECT id::text AS chunk_id, chunk_index, section_id, block_ids,
                       page_start, page_end, text
                FROM chunks
                WHERE document_id = CAST(:document_id AS uuid)
                ORDER BY chunk_index ASC
                """
            ),
            {"document_id": document_id},
        ).mappings().all()
        return [dict(row) for row in rows]
    finally:
        session.close()
