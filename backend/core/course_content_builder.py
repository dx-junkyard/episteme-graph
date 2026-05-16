"""Build course topic content from document pipeline artifacts."""

from __future__ import annotations

import datetime as _dt
import json
import logging
import re
from collections import defaultdict
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import text as sa_text

from core.llm import generate_text, generate_text_with_structured_output, get_llm_params
from core.postgres import get_session as _pg_session

logger = logging.getLogger(__name__)


def _strip_nuls(value: Any) -> Any:
    """Postgres jsonb cannot store \u0000; remove NULs from generated content."""
    if isinstance(value, str):
        return value.replace("\x00", "").replace("\\u0000", "")
    if isinstance(value, list):
        return [_strip_nuls(item) for item in value]
    if isinstance(value, dict):
        return {str(key).replace("\x00", "").replace("\\u0000", ""): _strip_nuls(item) for key, item in value.items()}
    return value


def _json_dumps(value: Any) -> str:
    return json.dumps(_strip_nuls(value), ensure_ascii=False)


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
        draft_result = _generate_course_topic_drafts(course, enriched_topics)
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
                "drafted_topics": draft_result["drafted_topics"],
                "draft_errors": draft_result["draft_errors"],
            },
        )
        _save_course(session, course_id, course)
        return {
            "status": "completed",
            "updated_topics": len(enriched_topics),
            "drafted_topics": draft_result["drafted_topics"],
            "draft_errors": draft_result["draft_errors"],
        }
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


_COURSE_CONTENT_DRAFT_PROMPT = """あなたは大学教員の授業用ドラフト作成を支援するアシスタントです。

目的:
- コース全体の章立て、前後の説明順序、現在セクションが果たす教育上の役割を考慮する
- 現在セクションだけで閉じた説明にせず、前のセクションから何を受け取り、次へ何を渡すかを明確にする
- Claim / コンポーネント / 数式 / 原文抜粋は、本文の主役ではなく根拠材料として使う
- 教材欄と本文説明を分離する

教材欄の表記:
- Markdown風の軽量表記を使う
- インライン数式は `$...$`
- ブロック数式は `$$...$$`
- 埋め込みは `![[equation:id]]`, `![[figure:id]]`, `![[source:id]]`, `![[claim:id]]`, `![[component:id]]`

出力は必ずJSONのみ。
JSON文字列内のLaTeXバックスラッシュは必ず `\\Lambda` のように二重化してください。
形式:
{{
  "key_concepts": ["重要概念"],
  "student_material": {{"source_format": "eg-markdown-v1", "source_text": "学生に見せる教材"}},
  "spoken_script": "教員が話せる自然文。音声読み上げ対象。",
  "cautions": ["注意点"],
  "check_questions": ["確認問題"]
}}

コース全体:
{course_json}

現在のセクション:
{topic_json}

前後関係:
{sequence_json}

根拠候補:
{evidence_json}

現在の下書き:
{draft_json}

依頼:
授業用ドラフトを作成してください。
"""


class _CourseContentStudentMaterialDraft(BaseModel):
    source_format: str = "eg-markdown-v1"
    source_text: str = ""


class _CourseContentDraftResponse(BaseModel):
    key_concepts: list[str] = Field(default_factory=list)
    student_material: _CourseContentStudentMaterialDraft = Field(default_factory=_CourseContentStudentMaterialDraft)
    spoken_script: str = ""
    cautions: list[str] = Field(default_factory=list)
    check_questions: list[str] = Field(default_factory=list)


def _generate_course_topic_drafts(course: dict, topics: list[dict]) -> dict:
    if not topics:
        return {"drafted_topics": 0, "draft_errors": 0}
    course_context = _course_context_for_prompt(course, topics)
    params = get_llm_params("fast")
    drafted = 0
    errors = 0
    for index, topic in enumerate(topics):
        try:
            result = _generate_single_topic_draft(
                course_context=course_context,
                topics=topics,
                topic=topic,
                index=index,
                model=params["model"],
                reasoning_effort=params["reasoning_effort"],
            )
            topic["key_concepts"] = result["key_concepts"]
            topic["student_material"] = result["student_material"]
            topic["spoken_script"] = result["spoken_script"]
            topic["cautions"] = result["cautions"]
            topic["check_questions"] = result["check_questions"]
            topic["draft_source"] = "course_content_generation"
            drafted += 1
        except Exception:
            errors += 1
            logger.exception(
                "Failed to generate course topic draft: course=%s topic=%s",
                course.get("id") or course.get("title"),
                topic.get("id") or topic.get("title"),
            )
            _apply_deterministic_topic_draft_fallback(topic)
    return {"drafted_topics": drafted, "draft_errors": errors}


def _generate_single_topic_draft(
    *,
    course_context: dict,
    topics: list[dict],
    topic: dict,
    index: int,
    model: str,
    reasoning_effort: str | None,
) -> dict:
    prompt = _COURSE_CONTENT_DRAFT_PROMPT.format(
        course_json=json.dumps(course_context, ensure_ascii=False, indent=2)[:8000],
        topic_json=json.dumps(_topic_context_for_prompt(topic), ensure_ascii=False, indent=2)[:4000],
        sequence_json=json.dumps(_topic_sequence_context(topics, index), ensure_ascii=False, indent=2)[:3000],
        evidence_json=json.dumps(_topic_evidence_for_prompt(topic), ensure_ascii=False, indent=2)[:8000],
        draft_json=json.dumps(_topic_existing_draft(topic), ensure_ascii=False, indent=2)[:6000],
    )
    parsed: object
    try:
        parsed = generate_text_with_structured_output(
            messages=[{"role": "user", "content": prompt}],
            response_format=_CourseContentDraftResponse,
            model=model,
        )
    except Exception:
        raw = generate_text(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            reasoning_effort=reasoning_effort,
        )
        parsed = _parse_topic_draft_json(raw)
    result = _normalize_topic_draft_response(parsed)
    if not any([
        result["key_concepts"],
        result["student_material"]["source_text"].strip(),
        result["spoken_script"].strip(),
        result["cautions"],
        result["check_questions"],
    ]):
        raise ValueError("empty draft response")
    return result


def _course_context_for_prompt(course: dict, topics: list[dict]) -> dict:
    chapters = course.get("chapters") or []
    grouped: dict[int, list[dict]] = defaultdict(list)
    for idx, topic in enumerate(topics):
        grouped[int(topic.get("chapter_index") or 0)].append({
            "order": idx + 1,
            "id": topic.get("id") or "",
            "title": topic.get("title") or "",
            "summary": topic.get("summary") or "",
            "prerequisites": topic.get("prerequisites") or [],
        })
    return {
        "title": course.get("title") or "",
        "goal": course.get("goal") or course.get("description") or "",
        "target_audience": course.get("target_audience") or "",
        "chapters": [
            {
                "chapter_index": idx,
                "title": ch.get("title") if isinstance(ch, dict) else str(ch),
                "topics": grouped.get(idx, []),
            }
            for idx, ch in enumerate(chapters)
        ] or [{"chapter_index": 0, "title": "コース", "topics": grouped.get(0, [])}],
    }


def _topic_context_for_prompt(topic: dict) -> dict:
    return {
        "id": topic.get("id") or "",
        "title": topic.get("title") or "",
        "chapter_index": topic.get("chapter_index", 0),
        "prerequisites": topic.get("prerequisites") or [],
        "learning_objectives": topic.get("learning_objectives") or [],
        "expected_misconceptions": topic.get("expected_misconceptions") or [],
        "content_confidence": topic.get("content_confidence") or "",
    }


def _topic_sequence_context(topics: list[dict], index: int) -> dict:
    def compact(topic: dict | None) -> dict | None:
        if not topic:
            return None
        return {
            "id": topic.get("id") or "",
            "title": topic.get("title") or "",
            "summary": topic.get("summary") or "",
            "key_concepts": topic.get("key_concepts") or [],
        }

    return {
        "current_order": index + 1,
        "total_sections": len(topics),
        "previous": compact(topics[index - 1] if index > 0 else None),
        "current": compact(topics[index]),
        "next": compact(topics[index + 1] if index + 1 < len(topics) else None),
    }


def _topic_evidence_for_prompt(topic: dict) -> dict:
    return {
        "summary": topic.get("summary") or "",
        "content": topic.get("content") or "",
        "content_blocks": topic.get("content_blocks") or [],
        "source_excerpt": topic.get("source_excerpt") or "",
        "linked_component_ids": topic.get("linked_component_ids") or [],
        "linked_equation_ids": topic.get("linked_equation_ids") or [],
        "source_evidence_ids": topic.get("source_evidence_ids") or [],
        "assessment_prompts": topic.get("assessment_prompts") or [],
        "teaching_takeaways": topic.get("teaching_takeaways") or [],
    }


def _topic_existing_draft(topic: dict) -> dict:
    return {
        "key_concepts": topic.get("key_concepts") or [],
        "student_material": topic.get("student_material") or {},
        "spoken_script": topic.get("spoken_script") or topic.get("content") or "",
        "cautions": topic.get("cautions") or [],
        "check_questions": topic.get("check_questions") or topic.get("assessment_prompts") or [],
    }


def _apply_deterministic_topic_draft_fallback(topic: dict) -> None:
    topic["key_concepts"] = _as_str_list(topic.get("learning_objectives"))[:6] or _tokens_as_list(topic.get("title") or "")
    topic["student_material"] = {
        "source_format": "eg-markdown-v1",
        "source_text": _fallback_student_material(topic),
    }
    topic["spoken_script"] = topic.get("content") or topic.get("summary") or ""
    topic["cautions"] = _as_str_list(topic.get("expected_misconceptions"))[:4]
    topic["check_questions"] = _as_str_list(topic.get("assessment_prompts"))[:4]
    topic["draft_source"] = "course_content_generation_fallback"


def _fallback_student_material(topic: dict) -> str:
    lines = []
    if topic.get("title"):
        lines.append("## " + str(topic["title"]))
    if topic.get("summary"):
        lines.extend(["", str(topic["summary"])])
    for eq_id in topic.get("linked_equation_ids") or []:
        lines.extend(["", f"![[equation:{eq_id}]]"])
    return "\n".join(lines).strip()


def _tokens_as_list(text: str) -> list[str]:
    return list(_tokens(text))[:6]


def _parse_topic_draft_json(raw: str) -> dict:
    cleaned = (raw or "").strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        cleaned = "\n".join(lines).strip()
    candidates = [cleaned]
    match = re.search(r"\{[\s\S]*\}", cleaned)
    if match and match.group() != cleaned:
        candidates.append(match.group())
    for candidate in list(candidates):
        repaired = re.sub(r'\\(?!["\\/bfnrtu])', r"\\\\", candidate)
        if repaired != candidate:
            candidates.append(repaired)
    for candidate in candidates:
        try:
            parsed = json.loads(candidate, strict=False)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            continue
    return {}


def _normalize_topic_draft_response(parsed: object) -> dict:
    if isinstance(parsed, BaseModel):
        parsed = parsed.model_dump()
    if not isinstance(parsed, dict):
        parsed = {}
    student_material = parsed.get("student_material")
    if isinstance(student_material, BaseModel):
        student_material = student_material.model_dump()
    if not isinstance(student_material, dict):
        student_material = {"source_format": "eg-markdown-v1", "source_text": str(student_material or "")}
    return {
        "key_concepts": _clean_str_list(parsed.get("key_concepts")),
        "student_material": {
            "source_format": student_material.get("source_format") or "eg-markdown-v1",
            "source_text": str(student_material.get("source_text") or ""),
        },
        "spoken_script": str(parsed.get("spoken_script") or ""),
        "cautions": _clean_str_list(parsed.get("cautions")),
        "check_questions": _clean_str_list(parsed.get("check_questions")),
    }


def _clean_str_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        return [line.strip("- ・\t ") for line in value.splitlines() if line.strip("- ・\t ")]
    return []


def _set_content_status(course: dict, status: str, message: str = "", extra: dict | None = None) -> None:
    payload = dict(extra or {})
    payload.update({
        "status": status,
        "message": message,
        "updated_at": _dt.datetime.now(_dt.UTC).isoformat(),
    })
    course["course_content_status"] = payload


def _save_course(session, course_id: str, course: dict) -> None:
    clean_course = _strip_nuls(course)
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
            "title": str(clean_course.get("title") or course_id).replace("\x00", "").replace("\\u0000", ""),
            "data": _json_dumps(clean_course),
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
