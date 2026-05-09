"""Build course topic content from document pipeline artifacts."""

from __future__ import annotations

import datetime as _dt
import json
import logging
import re
from collections import defaultdict
from typing import Any

from sqlalchemy import text as sa_text

from core.postgres import get_session as _pg_session

logger = logging.getLogger(__name__)


def build_course_content_background(user_id: str, course_id: str) -> None:
    """Best-effort background entrypoint for course registration."""
    try:
        build_course_content(user_id, course_id)
    except Exception:
        logger.exception("Course content build failed: course_id=%s user_id=%s", course_id, user_id)


def build_course_content(user_id: str, course_id: str) -> dict:
    """Populate learning_courses.data.topics with structured pipeline content.

    The course builder creates the outline first. This function enriches that
    outline from the latest document pipeline artifacts tied to the course's
    source materials.
    """
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("""
                SELECT data, user_id
                FROM learning_courses
                WHERE id = :course_id
                LIMIT 1
            """),
            {"course_id": course_id},
        ).fetchone()
        if not row or not row[0]:
            return {"status": "not_found", "updated_topics": 0}
        if str(row[1]) != str(user_id):
            return {"status": "forbidden", "updated_topics": 0}

        course = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        _set_content_status(course, "processing")

        material_ids = _course_material_ids(course)
        document_ids = _load_document_ids(session, material_ids)
        if not document_ids:
            _set_content_status(
                course,
                "waiting_for_pipeline",
                "コースの教材に紐づく解析済みドキュメントが見つかりません。",
            )
            _save_course(session, course_id, course)
            return {"status": "waiting_for_pipeline", "updated_topics": 0}

        artifacts_by_doc = _load_latest_artifacts(session, document_ids)
        bundle = _collect_structured_content(artifacts_by_doc)
        if not bundle["mapping_topics"] and not bundle["components"]:
            _set_content_status(
                course,
                "waiting_for_pipeline",
                "CourseMappingAgent または ComponentAssemblyAgent の成果物がまだありません。",
            )
            _save_course(session, course_id, course)
            return {"status": "waiting_for_pipeline", "updated_topics": 0}

        chunks_by_material = _load_chunks(session, material_ids)
        enriched_topics = _enrich_topics(course.get("topics") or [], bundle, chunks_by_material)
        course["topics"] = enriched_topics
        course["referenced_sections"] = _referenced_sections_from_topics(enriched_topics)
        _set_content_status(
            course,
            "completed",
            "",
            {
                "document_ids": document_ids,
                "updated_topics": len(enriched_topics),
                "mapping_topics": len(bundle["mapping_topics"]),
                "components": len(bundle["components"]),
                "equations": len(bundle["equations"]),
            },
        )
        _save_course(session, course_id, course)
        return {"status": "completed", "updated_topics": len(enriched_topics)}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _course_material_ids(course: dict) -> list[str]:
    ids: list[str] = []
    for source in course.get("sources") or []:
        if isinstance(source, dict) and source.get("material_id"):
            ids.append(str(source["material_id"]))
    return list(dict.fromkeys(ids))


def _load_document_ids(session, material_ids: list[str]) -> list[str]:
    if not material_ids:
        return []
    params = {f"mid_{idx}": mid for idx, mid in enumerate(material_ids)}
    placeholders = ", ".join(f":mid_{idx}" for idx in range(len(material_ids)))
    rows = session.execute(
        sa_text(f"""
            SELECT DISTINCT c.document_id::text
            FROM chunks c
            WHERE c.material_id IN ({placeholders})
              AND c.document_id IS NOT NULL
        """),
        params,
    ).fetchall()
    return [str(row[0]) for row in rows if row[0]]


def _load_latest_artifacts(session, document_ids: list[str]) -> dict[str, dict]:
    artifacts: dict[str, dict] = {}
    for document_id in document_ids:
        row = session.execute(
            sa_text("""
                SELECT stage_outputs
                FROM document_analysis_runs
                WHERE document_id::text = :document_id
                ORDER BY created_at DESC
                LIMIT 1
            """),
            {"document_id": document_id},
        ).fetchone()
        if not row or not row[0]:
            continue
        stage_outputs = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        doc_artifacts = stage_outputs.get("_artifacts") if isinstance(stage_outputs, dict) else None
        if isinstance(doc_artifacts, dict):
            artifacts[document_id] = doc_artifacts
    return artifacts


def _collect_structured_content(artifacts_by_doc: dict[str, dict]) -> dict:
    mapping_topics: list[dict] = []
    components: dict[str, dict] = {}
    equations: dict[str, dict] = {}

    for document_id, artifacts in artifacts_by_doc.items():
        mapping = _as_dict(artifacts.get("course_mapping"))
        for topic in _as_list(mapping.get("topics")):
            if isinstance(topic, dict):
                topic = dict(topic)
                topic.setdefault("document_id", document_id)
                mapping_topics.append(topic)

        assembly = _as_dict(artifacts.get("component_assembly"))
        for component in _as_list(assembly.get("components")):
            if isinstance(component, dict) and component.get("component_id"):
                item = dict(component)
                item.setdefault("document_id", document_id)
                components[str(item["component_id"])] = item

        eq_artifact = _as_dict(artifacts.get("equation_semantics"))
        for equation in _as_list(eq_artifact.get("equations")):
            if not isinstance(equation, dict):
                continue
            eq = dict(equation)
            eq.setdefault("document_id", document_id)
            eq_id = str(eq.get("equation_id") or eq.get("id") or "")
            if eq_id:
                equations[eq_id] = eq

    return {
        "mapping_topics": mapping_topics,
        "components": components,
        "equations": equations,
    }


def _load_chunks(session, material_ids: list[str]) -> dict[str, list[dict]]:
    if not material_ids:
        return {}
    params = {f"mid_{idx}": mid for idx, mid in enumerate(material_ids)}
    placeholders = ", ".join(f":mid_{idx}" for idx in range(len(material_ids)))
    rows = session.execute(
        sa_text(f"""
            SELECT id::text, material_id, chunk_index, display_text, text, formulas, chapter, section
            FROM chunks
            WHERE material_id IN ({placeholders})
            ORDER BY chunk_index ASC
        """),
        params,
    ).fetchall()
    chunks: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        material_id = row[1] or ""
        chunks[material_id].append({
            "id": row[0],
            "material_id": material_id,
            "chunk_index": row[2],
            "text": (row[3] or row[4] or "").strip(),
            "formulas": row[5] if isinstance(row[5], list) else [],
            "chapter": row[6],
            "section": row[7],
        })
    return chunks


def _enrich_topics(topics: list[dict], bundle: dict, chunks_by_material: dict[str, list[dict]]) -> list[dict]:
    enriched: list[dict] = []
    all_chunks = [chunk for chunks in chunks_by_material.values() for chunk in chunks]
    for index, raw_topic in enumerate(topics):
        topic = dict(raw_topic) if isinstance(raw_topic, dict) else {"title": str(raw_topic)}
        mapping, mapping_confidence = _best_mapping(topic, bundle["mapping_topics"], index)
        component_ids = _component_ids_for_topic(topic, mapping, bundle["components"])
        components = [bundle["components"][cid] for cid in component_ids if cid in bundle["components"]]
        equations = _equations_for_components(components, bundle["equations"])
        fallback_chunk = _fallback_chunk_for_topic(all_chunks, index, len(topics))

        summary = _topic_summary(mapping, components, fallback_chunk)
        learning_objectives = _as_str_list(mapping.get("learning_objectives") if mapping else [])
        assessment_prompts = _as_str_list(mapping.get("assessment_prompts") if mapping else [])
        prerequisite_concepts = _as_str_list(mapping.get("prerequisite_concepts") if mapping else [])
        teaching_takeaways = _as_str_list([c.get("teaching_takeaway") for c in components if c.get("teaching_takeaway")])
        evidence_ids = _linked_ids(components, "linked_evidence_ids")

        fallback_formulas = _fallback_formulas(fallback_chunk)

        topic.update({
            "summary": summary,
            "content": _compose_topic_content(
                summary,
                learning_objectives,
                components,
                equations,
                assessment_prompts,
            ),
            "content_blocks": _content_blocks(
                summary,
                learning_objectives,
                components,
                equations,
                assessment_prompts,
                fallback_formulas,
            ),
            "learning_objectives": learning_objectives,
            "prerequisite_concepts": prerequisite_concepts,
            "blackbox_policy": mapping.get("blackbox_policy") if isinstance(mapping, dict) else {},
            "assessment_prompts": assessment_prompts,
            "expected_misconceptions": _as_str_list(mapping.get("expected_misconceptions") if mapping else []),
            "linked_component_ids": component_ids,
            "linked_equation_ids": [str(e.get("equation_id") or e.get("id")) for e in equations if e.get("equation_id") or e.get("id")],
            "source_evidence_ids": evidence_ids,
            "teaching_takeaways": teaching_takeaways,
            "material_chunk_ids": [fallback_chunk["id"]] if fallback_chunk else [],
            "source_excerpt": _short_excerpt(fallback_chunk.get("text", "")) if fallback_chunk else "",
            "content_source": "agent_mapping" if mapping_confidence != "none" or components else "source_excerpt",
            "content_confidence": mapping_confidence,
        })
        enriched.append(topic)
    return enriched


def _best_mapping(topic: dict, mapping_topics: list[dict], index: int) -> tuple[dict, str]:
    if not mapping_topics:
        return {}, "none"
    title = str(topic.get("title") or "")
    for mapping in mapping_topics:
        if _norm_title(mapping.get("title")) == _norm_title(title):
            return mapping, "exact_title"
    scored = sorted(
        ((_overlap_score(title, f"{m.get('title', '')} {m.get('description', '')}"), m) for m in mapping_topics),
        key=lambda item: item[0],
        reverse=True,
    )
    if scored and scored[0][0] >= 0.18:
        return scored[0][1], "title_similarity"
    return {}, "none"


def _component_ids_for_topic(topic: dict, mapping: dict, components: dict[str, dict]) -> list[str]:
    ids = [str(cid) for cid in _as_list(mapping.get("linked_component_ids") if mapping else []) if cid]
    if ids:
        return list(dict.fromkeys(ids))
    title = str(topic.get("title") or "")
    scored = sorted(
        (
            (_overlap_score(title, f"{c.get('label', '')} {c.get('summary', '')} {c.get('teaching_takeaway', '')}"), cid)
            for cid, c in components.items()
        ),
        key=lambda item: item[0],
        reverse=True,
    )
    return [cid for score, cid in scored[:3] if score >= 0.12]


def _equations_for_components(components: list[dict], equations: dict[str, dict]) -> list[dict]:
    ids = _linked_ids(components, "linked_equation_ids")
    for component in components:
        evidence_refs = component.get("evidence_refs") if isinstance(component.get("evidence_refs"), dict) else {}
        ids.extend(str(eid) for eid in _as_list(evidence_refs.get("equation_ids")) if eid)
    seen: set[str] = set()
    out: list[dict] = []
    for eq_id in ids:
        if eq_id in seen or eq_id not in equations:
            continue
        seen.add(eq_id)
        out.append(equations[eq_id])
    return out[:5]


def _topic_summary(mapping: dict, components: list[dict], fallback_chunk: dict | None) -> str:
    if isinstance(mapping, dict) and mapping.get("description"):
        return str(mapping["description"]).strip()
    for component in components:
        if component.get("summary"):
            return str(component["summary"]).strip()
    if fallback_chunk and fallback_chunk.get("text"):
        return _short_excerpt(fallback_chunk["text"], limit=420)
    return ""


def _fallback_chunk_for_topic(chunks: list[dict], topic_index: int, topic_count: int) -> dict | None:
    if not chunks:
        return None
    if topic_index < len(chunks):
        return chunks[topic_index]
    mapped_index = min(int(topic_index * len(chunks) / max(topic_count, 1)), len(chunks) - 1)
    return chunks[mapped_index] if mapped_index >= 0 else None


def _fallback_formulas(fallback_chunk: dict | None) -> list[dict]:
    if not fallback_chunk:
        return []
    formulas = fallback_chunk.get("formulas")
    return [dict(f) for f in formulas if isinstance(f, dict)] if isinstance(formulas, list) else []


def _compose_topic_content(
    summary: str,
    learning_objectives: list[str],
    components: list[dict],
    equations: list[dict],
    assessment_prompts: list[str],
) -> str:
    lines: list[str] = []
    if summary:
        lines.extend(["概要", summary, ""])
    if learning_objectives:
        lines.append("学習目標")
        lines.extend(f"- {item}" for item in learning_objectives)
        lines.append("")
    if components:
        lines.append("論理要素")
        for component in components[:5]:
            label = component.get("label") or component.get("component_id") or ""
            text = component.get("teaching_takeaway") or component.get("summary") or ""
            lines.append(f"- {label}: {text}" if text else f"- {label}")
        lines.append("")
    if equations:
        lines.append("重要な数式")
        for equation in equations:
            label = equation.get("label") or equation.get("equation_id") or equation.get("id") or ""
            latex = equation.get("latex") or equation.get("latex_canonical") or equation.get("normalized_latex") or ""
            lines.append(f"- {label}: {latex}" if label else f"- {latex}")
        lines.append("")
    if assessment_prompts:
        lines.append("確認問題")
        lines.extend(f"- {item}" for item in assessment_prompts)
    return "\n".join(line for line in lines if line is not None).strip()


def _content_blocks(
    summary: str,
    learning_objectives: list[str],
    components: list[dict],
    equations: list[dict],
    assessment_prompts: list[str],
    fallback_formulas: list[dict] | None = None,
) -> list[dict]:
    blocks: list[dict] = []
    if summary:
        blocks.append({"type": "summary", "text": summary})
    if learning_objectives:
        blocks.append({"type": "learning_objectives", "items": learning_objectives})
    if components:
        blocks.append({
            "type": "components",
            "items": [
                {
                    "component_id": c.get("component_id"),
                    "label": c.get("label"),
                    "summary": c.get("summary"),
                    "teaching_takeaway": c.get("teaching_takeaway"),
                }
                for c in components[:5]
            ],
        })
    equation_items = [
        {
            "equation_id": e.get("equation_id") or e.get("id"),
            "label": e.get("label"),
            "latex": e.get("latex") or e.get("latex_canonical") or e.get("normalized_latex"),
            "plain_text": e.get("plain_text") or e.get("reading"),
        }
        for e in equations
    ]
    existing_ids = {str(item.get("equation_id") or "") for item in equation_items if item.get("equation_id")}
    for f in fallback_formulas or []:
        formula_id = str(f.get("id") or f.get("equation_id") or "")
        latex = f.get("latex") or f.get("latex_canonical") or f.get("normalized_latex") or ""
        if not formula_id or not latex or formula_id in existing_ids:
            continue
        existing_ids.add(formula_id)
        equation_items.append({
            "equation_id": formula_id,
            "label": f.get("label") or "",
            "latex": latex,
            "plain_text": f.get("plain_text") or f.get("spoken") or f.get("reading") or "",
        })
    if equation_items:
        blocks.append({
            "type": "equations",
            "items": equation_items,
        })
    if assessment_prompts:
        blocks.append({"type": "assessment_prompts", "items": assessment_prompts})
    return blocks


def _referenced_sections_from_topics(topics: list[dict]) -> list[dict]:
    refs: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for topic in topics:
        for component_id in topic.get("linked_component_ids") or []:
            key = (str(topic.get("id") or topic.get("title") or ""), str(component_id))
            if key in seen:
                continue
            seen.add(key)
            refs.append({
                "source": "Agent pipeline",
                "section": str(component_id),
                "title": str(topic.get("title") or ""),
                "note": "CourseMappingAgent / ComponentAssemblyAgent から対応付け",
            })
    return refs[:100]


def _set_content_status(course: dict, status: str, message: str = "", extra: dict | None = None) -> None:
    payload = dict(extra or {})
    payload.update({
        "status": status,
        "message": message,
        "updated_at": _dt.datetime.now(_dt.UTC).isoformat(),
    })
    course["course_content_status"] = payload


def _save_course(session, course_id: str, course: dict) -> None:
    session.execute(
        sa_text("""
            UPDATE learning_courses
            SET data = CAST(:data AS jsonb),
                title = :title,
                updated_at = now()
            WHERE id = :course_id
        """),
        {
            "course_id": course_id,
            "title": course.get("title") or course_id,
            "data": json.dumps(course, ensure_ascii=False),
        },
    )
    session.commit()


def _as_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if hasattr(value, "to_dict"):
        return value.to_dict()
    return {}


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _as_str_list(value: Any) -> list[str]:
    return [str(item).strip() for item in _as_list(value) if str(item).strip()]


def _linked_ids(components: list[dict], field: str) -> list[str]:
    ids: list[str] = []
    for component in components:
        ids.extend(str(item) for item in _as_list(component.get(field)) if item)
    return list(dict.fromkeys(ids))


def _short_excerpt(text: str, limit: int = 280) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _norm_title(text: Any) -> str:
    return re.sub(r"\s+", "", str(text or "").casefold())


def _tokens(text: str) -> set[str]:
    ascii_tokens = {tok.casefold() for tok in re.findall(r"[A-Za-z0-9]{3,}", text or "")}
    jp_tokens = set(re.findall(r"[\u3040-\u30ff\u3400-\u9fff]{2,}", text or ""))
    return ascii_tokens | jp_tokens


def _overlap_score(left: str, right: str) -> float:
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / max(len(left_tokens), 1)
