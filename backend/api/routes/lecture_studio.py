"""Episteme Graph — Lecture Script Studio (/api/admin) (Issue #70).

教員向けレクチャー原稿の事前構築・AI補正エディタ。
- バッチスクリプト生成
- 手動スクリプト保存
- AIスクリプト書き換え
- バッチ音声生成
"""

from __future__ import annotations

import base64
import json
import logging
import re
import threading
import time
import uuid

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text as sa_text

from dependencies import ROLE_SYSTEM_ADMIN, _require_teacher
from schemas import (
    LectureAudioGenerateRequest,
    LectureAudioGenerateResponse,
    LectureAudioGenerateStartResponse,
    LectureFormulaItem,
    LecturePreviewSplitRequest,
    LecturePreviewSplitResponse,
    LectureScriptChunkOut,
    LectureScriptGenerateRequest,
    LectureScriptGenerateStartResponse,
    LectureScriptRewriteRequest,
    LectureScriptRewriteResponse,
    LectureScriptSaveRequest,
    LectureScriptSaveResponse,
    LectureSlide,
    LectureStudioSettings,
    LectureTTSResponse,
)
from schemas import BackgroundTaskOut
from services import (
    create_background_task,
    get_active_task_for_course,
    get_course_data,
    get_editable_course_data,
    get_viewable_course_data,
    reanalyze_course_structure_background,
    update_background_task,
    user_can_edit_course,
)
from core.lecture import (
    count_slide_marker_segments,
    generate_spoken_text_and_formulas,
    get_course_lecture_language,
    normalize_to_placeholder_format,
    split_slides,
)
from core.document_sections import enrich_chunks_with_sections
from core.llm import generate_text, generate_text_with_structured_output, get_llm_params
from core.llm_usage.context import bind_usage_context, usage_context
from core.personas import course_persona_settings, normalize_persona_id, persona_prompt
from core.postgres import get_session as _pg_session
from core.storage import get_storage_client
from core.tts import TtsFatalError, generate_tts_audio

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Lecture Script Studio"])


DOCUMENT_PIPELINE_STAGE_LABELS: dict[str, str] = {
    "document_structure": "DocumentStructureAgent",
    "paper_skeleton": "PaperSkeletonAgent",
    "rhetorical_role": "RhetoricalRoleAgent",
    "claim_qualification": "ClaimQualificationAgent",
    "equation_semantics": "EquationSemanticsAgent",
    "evidence_registry": "EvidenceRegistryBuilder",
    "claim_object_builder": "ClaimObjectBuilder",
    "symbol_registry": "SymbolRegistryBuilder",
    "derivation_chain": "DerivationChainAgent",
    "figure_table_semantics": "FigureTableSemanticsAgent",
    "thesis_reconstruction": "ThesisReconstructionAgent",
    "dsl_linking": "DSLLinkingAgent",
    "component_assembly": "ComponentAssemblyAgent",
    "component_graph": "ComponentGraphAgent",
    "narrative_annotator": "NarrativeAnnotator",
    "course_mapping": "CourseMappingAgent",
    "blueprint": "BlueprintAgent",
    "export_validation": "ExportValidationGate",
}


def _normalize_lecture_language(value: str | None, default: str = "ja") -> str:
    """読み上げ言語を ``"ja"``/``"en"`` に正規化する（不正値は default にフォールバック）。"""
    return value if value in ("ja", "en") else default


def _save_lecture_studio_settings(course_id: str, course_data: dict, settings: dict[str, str]) -> dict:
    """原稿スタジオのコース単位設定（口調 + 読み上げ言語）を保存する。

    口調 (narration_persona/response_persona) または lecture_language が変わった場合は
    ``scripts_need_regeneration`` を立て、次回のバッチ生成で全チャンクを再生成させる。
    更新後の course_data を返す（呼び出し側が続けて最新設定を使えるように）。
    """
    updated = dict(course_data)
    previous = updated.get("lecture_studio_settings") or {}
    if not isinstance(previous, dict):
        previous = {}
    previous_language = _normalize_lecture_language(previous.get("lecture_language"))
    normalized = {
        "narration_persona": normalize_persona_id(settings.get("narration_persona")),
        "response_persona": normalize_persona_id(settings.get("response_persona")),
        "lecture_language": _normalize_lecture_language(
            settings.get("lecture_language"), default=previous_language,
        ),
    }
    settings_changed = (
        normalize_persona_id(previous.get("narration_persona")) != normalized["narration_persona"]
        or normalize_persona_id(previous.get("response_persona")) != normalized["response_persona"]
        or previous_language != normalized["lecture_language"]
    )
    updated["lecture_studio_settings"] = {
        **normalized,
        "scripts_need_regeneration": bool(previous.get("scripts_need_regeneration")) or settings_changed,
    }
    session = _pg_session()
    try:
        session.execute(
            sa_text("""
                UPDATE learning_courses
                SET data = CAST(:data AS jsonb),
                    updated_at = now()
                WHERE id = :course_id
            """),
            {
                "course_id": course_id,
                "data": json.dumps(updated, ensure_ascii=False),
            },
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    return updated


def _save_lecture_language(course_id: str, course_data: dict, language: str) -> dict:
    """コースの ``lecture_language`` のみを更新する（口調設定は保持する）。

    音声生成ダイアログでの言語切替チェーン (§3-3) から呼ばれる。narration/response
    persona は既存値をそのまま引き継ぐため、ここでは persona 側の
    scripts_need_regeneration 判定を再利用して整合させる。
    """
    previous = course_data.get("lecture_studio_settings") or {}
    if not isinstance(previous, dict):
        previous = {}
    return _save_lecture_studio_settings(course_id, course_data, {
        "narration_persona": previous.get("narration_persona"),
        "response_persona": previous.get("response_persona"),
        "lecture_language": language,
    })


def _clear_script_regeneration_flag(course_id: str, course_data: dict) -> None:
    updated = dict(course_data)
    settings = updated.get("lecture_studio_settings") or {}
    if not isinstance(settings, dict):
        return
    if not settings.get("scripts_need_regeneration"):
        return
    updated["lecture_studio_settings"] = {
        **settings,
        "scripts_need_regeneration": False,
    }
    session = _pg_session()
    try:
        session.execute(
            sa_text("""
                UPDATE learning_courses
                SET data = CAST(:data AS jsonb),
                    updated_at = now()
                WHERE id = :course_id
            """),
            {
                "course_id": course_id,
                "data": json.dumps(updated, ensure_ascii=False),
            },
        )
        session.commit()
    except Exception:
        session.rollback()
        logger.warning("Failed to clear script regeneration flag for course %s", course_id, exc_info=True)
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Helper: コースに紐づくチャンクを取得
# ---------------------------------------------------------------------------


def _get_course_chunks(course_data: dict) -> list[dict]:
    """コースのソース教材からチャンクを取得する。"""
    sources = course_data.get("sources", [])
    material_ids = [s.get("material_id") for s in sources if s.get("material_id")]

    if not material_ids:
        return []

    session = _pg_session()
    try:
        mid_placeholders = ", ".join(f":mid_{i}" for i in range(len(material_ids)))
        params: dict = {}
        for i, mid in enumerate(material_ids):
            params[f"mid_{i}"] = mid

        where_clause = f"c.material_id IN ({mid_placeholders})"
        rows = session.execute(
            sa_text(f"""
                SELECT c.id, c.chunk_index, c.text, c.display_text, c.spoken_text, c.formulas,
                       c.material_id, c.document_id, c.page_start, c.page_end,
                       c.smiles_dsl, c.variables, c.ancestors,
                       d.knowledge_graph, c.spoken_language
                FROM chunks c
                LEFT JOIN documents d ON c.document_id = d.id
                WHERE ({where_clause})
                  AND c.text IS NOT NULL AND c.text != ''
                ORDER BY c.chunk_index
            """),
            params,
        ).fetchall()

        chunks = []
        graph_structure_cache: dict[str, dict] = {}
        document_ids = sorted({str(row[7]) for row in rows if row[7]})
        equation_previews = _load_equation_formula_previews(session, document_ids)
        for row in rows:
            raw_text = row[2] or ""
            display_text = row[3] or raw_text
            spoken_text = row[4] or display_text
            formulas = row[5] if row[5] else []
            # 旧フォーマット（$...$）のデータをプレースホルダー方式に正規化
            display_text, formulas = normalize_to_placeholder_format(display_text, formulas)
            material_id = row[6] or ""
            document_id = str(row[7]) if row[7] else ""
            formulas = _merge_equation_formula_previews(
                formulas,
                document_id=document_id,
                page_start=row[8],
                page_end=row[9],
                equation_previews=equation_previews,
            )
            display_text = _replace_equation_preview_text(display_text, formulas)
            knowledge_graph = _json_obj(row[13])
            graph_elements = _derive_chunk_graph_elements(
                f"{raw_text}\n{display_text}",
                knowledge_graph,
                formulas,
            )
            graph_structure = {}
            if document_id:
                if document_id not in graph_structure_cache:
                    graph_structure_cache[document_id] = _extract_component_graph_structure(document_id)
                graph_structure = graph_structure_cache.get(document_id) or {}
            variables = (
                row[11]
                if row[11] is not None
                else _extract_document_variables(knowledge_graph) or graph_structure.get("variables")
            )
            ancestors = (
                row[12]
                if isinstance(row[12], list)
                else _extract_document_edges(knowledge_graph) or graph_structure.get("ancestors") or []
            )
            smiles_dsl = row[10] or _extract_document_dsl(knowledge_graph) or graph_structure.get("smiles_dsl", "")
            chunks.append({
                "id": str(row[0]),
                "chunk_index": row[1],
                "text": display_text,
                "raw_text": raw_text,
                "display_text": display_text,
                "spoken_text": spoken_text,
                "stored_spoken_text": row[4] or "",
                "formulas": formulas,
                "material_id": material_id,
                "document_id": document_id,
                "page_start": row[8],
                "page_end": row[9],
                "pdf_url": f"/admin/materials/{material_id}/pdf" if material_id else None,
                "smiles_dsl": smiles_dsl,
                "variables": variables,
                "ancestors": ancestors,
                "graph_elements": graph_elements,
                # レクチャースライド同期 (migration 040): 原稿の生成言語。
                # len<=14 の古いモック行は未指定として扱う（後方互換）。
                "spoken_language": (row[14] if len(row) > 14 else None) or "ja",
            })
        return enrich_chunks_with_sections(chunks)
    finally:
        session.close()


def _load_equation_formula_previews(session, document_ids: list[str]) -> dict[str, list[dict]]:
    """Load EquationSemanticAgent crop previews from the latest artifacts."""
    if not document_ids:
        return {}
    placeholders = ", ".join(f":doc_{i}" for i in range(len(document_ids)))
    params = {f"doc_{i}": doc_id for i, doc_id in enumerate(document_ids)}
    rows = session.execute(
        sa_text(f"""
            SELECT DISTINCT ON (document_id)
                   document_id, stage_outputs
            FROM document_analysis_runs
            WHERE document_id IN ({placeholders})
            ORDER BY document_id, created_at DESC
        """),
        params,
    ).fetchall()
    out: dict[str, list[dict]] = {}
    for row in rows:
        doc_id = str(row[0]) if row[0] else ""
        stage_outputs = _json_obj(row[1])
        artifacts = stage_outputs.get("_artifacts") if isinstance(stage_outputs.get("_artifacts"), dict) else {}
        eq_artifact = artifacts.get("equation_semantics") if isinstance(artifacts, dict) else None
        if not isinstance(eq_artifact, dict):
            continue
        previews: list[dict] = []
        for record in eq_artifact.get("equations") or []:
            if not isinstance(record, dict):
                continue
            src = record.get("source_extraction") or {}
            if not isinstance(src, dict):
                continue
            source_image = src.get("source_image")
            if not isinstance(source_image, dict) or not source_image.get("data_base64"):
                continue
            rec = record.get("reconstruction") or {}
            if not isinstance(rec, dict):
                rec = {}
            source_location = src.get("source_location") if isinstance(src.get("source_location"), dict) else {}
            previews.append({
                "id": record.get("equation_id") or f"eq_{len(previews)}",
                "latex": rec.get("latex") or src.get("latex") or src.get("plain_text") or "",
                "spoken": rec.get("plain_text") or src.get("plain_text") or "",
                "is_display": True,
                "label": record.get("label"),
                "block_id": source_location.get("block_id"),
                "source_location": source_location,
                "source_image": source_image,
                "raw_text": src.get("raw_text") or "",
                "needs_math_review": bool(src.get("needs_math_review", False)),
                "review_reason": list(src.get("review_reason", [])) if isinstance(src.get("review_reason"), list) else [],
            })
        if previews:
            out[doc_id] = previews
    return out


def _merge_equation_formula_previews(
    formulas: list[dict],
    *,
    document_id: str,
    page_start: int | None,
    page_end: int | None,
    equation_previews: dict[str, list[dict]],
) -> list[dict]:
    if not document_id:
        return formulas
    previews = equation_previews.get(document_id) or []
    if not previews:
        return formulas
    merged = [dict(f) for f in formulas if isinstance(f, dict)]
    by_block = {str(f.get("block_id")): f for f in merged if f.get("block_id")}
    by_id = {str(f.get("id")): f for f in merged if f.get("id")}
    by_label = {str(f.get("label")): f for f in merged if f.get("label")}
    for preview in previews:
        if not _equation_preview_matches_chunk(preview, page_start, page_end, by_block):
            continue
        target = None
        block_id = str(preview.get("block_id") or "")
        label = str(preview.get("label") or "")
        eq_id = str(preview.get("id") or "")
        if block_id:
            target = by_block.get(block_id)
        if target is None and eq_id:
            target = by_id.get(eq_id)
        if target is None and label:
            target = by_label.get(label)
        if target is None:
            merged.append(dict(preview))
            continue
        target["source_image"] = preview.get("source_image")
        target["source_location"] = preview.get("source_location")
        target["needs_math_review"] = preview.get("needs_math_review", False)
        target["review_reason"] = preview.get("review_reason") or []
        if not target.get("latex") and preview.get("latex"):
            target["latex"] = preview.get("latex")
        if not target.get("spoken") and preview.get("spoken"):
            target["spoken"] = preview.get("spoken")
    return merged


def _equation_preview_matches_chunk(
    preview: dict,
    page_start: int | None,
    page_end: int | None,
    formulas_by_block: dict[str, dict],
) -> bool:
    block_id = str(preview.get("block_id") or "")
    if block_id and block_id in formulas_by_block:
        return True
    loc = preview.get("source_location") if isinstance(preview.get("source_location"), dict) else {}
    page = loc.get("page")
    try:
        page_num = int(page)
    except Exception:
        return False
    if page_start is None and page_end is None:
        return False
    start = page_start if page_start is not None else page_num
    end = page_end if page_end is not None else start
    return int(start) <= page_num <= int(end)


def _replace_equation_preview_text(display_text: str, formulas: list[dict]) -> str:
    """Replace broken PDF math snippets in display text with formula placeholders."""
    if not display_text or not formulas:
        return display_text
    updated = display_text
    for formula in formulas:
        if not isinstance(formula, dict):
            continue
        raw_text = str(formula.get("raw_text") or "").strip()
        formula_id = str(formula.get("id") or "").strip()
        if not raw_text or not formula_id:
            continue
        placeholder = formula_id if formula_id.startswith("[[") else f"[[{formula_id}]]"
        variants = _equation_text_variants(raw_text)
        # Replace longer variants first so multi-line equations collapse before
        # shorter subfragments can leave broken residue in the text.
        for variant in sorted(variants, key=len, reverse=True):
            if len(variant.strip()) < 4:
                continue
            updated = updated.replace(variant, placeholder)
    return updated


def _equation_text_variants(raw_text: str) -> list[str]:
    text = str(raw_text or "").strip()
    if not text:
        return []
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    variants = {
        text,
        "\n".join(lines),
        "\n\n".join(lines),
        " ".join(lines),
    }
    # PyMuPDF block text is sometimes persisted after chunk joining with blank
    # lines around each block; keep variants intentionally small and exact.
    return [v for v in variants if v]


def _json_obj(value: object) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _normalize_rewrite_result(parsed: object, studio_view: str) -> dict:
    """LLM の rewrite 応答を dict に正規化する。

    LLM が JSON オブジェクトではなくトップレベル配列で返してくることがある。
    studio_view="theory" の場合のみ、その配列を ``theory_components`` として扱う。
    それ以外は空 dict を返してフォールバック値で穴埋めする。
    """
    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, list) and studio_view == "theory":
        return {"theory_components": parsed}
    return {}


def _extract_document_dsl(knowledge_graph: dict) -> str:
    abstract = knowledge_graph.get("abstract_structure")
    if isinstance(abstract, dict):
        dsl = str(abstract.get("smiles_dsl") or "").strip()
        if dsl:
            return dsl
    return str(knowledge_graph.get("smiles_dsl") or "").strip()


def _extract_component_graph_structure(document_id: str) -> dict:
    """Return document-level DSL saved by the agent pipeline for structure views."""
    if not document_id:
        return {}
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("""
                SELECT graph_json
                FROM theory_component_graphs
                WHERE document_id = :document_id
                ORDER BY updated_at DESC
                LIMIT 1
            """),
            {"document_id": document_id},
        ).fetchone()
    finally:
        session.close()
    graph = _json_obj(row[0]) if row else {}
    dsl = graph.get("dsl") if isinstance(graph.get("dsl"), dict) else {}
    nodes = dsl.get("nodes") if isinstance(dsl.get("nodes"), list) else []
    edges = dsl.get("edges") if isinstance(dsl.get("edges"), list) else []
    if not nodes and not edges:
        return {}
    node_lines = []
    variables = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or "").strip()
        value = str(node.get("value") or "").strip()
        node_type = str(node.get("node_type") or "Node").strip()
        if not node_id and not value:
            continue
        node_lines.append(f"{node_id}:{node_type}({value})")
        variables.append({
            "id": node_id or value,
            "name": value or node_id,
            "type": node_type,
            "description": "",
        })
    edge_lines = []
    ancestors = []
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        source = str(edge.get("from") or edge.get("source") or "").strip()
        target = str(edge.get("to") or edge.get("target") or "").strip()
        predicate = str(edge.get("predicate") or edge.get("relation") or "RELATED_TO").strip()
        verb = str(edge.get("verb") or "").strip()
        polarity = str(edge.get("polarity") or "").strip()
        if not source or not target:
            continue
        label = "/".join(part for part in (predicate, verb, polarity) if part)
        edge_lines.append(f"{source} -[{label}]-> {target}")
        ancestors.append({
            "source": source,
            "target": target,
            "relation": predicate,
            "verb": verb,
            "polarity": polarity,
        })
    smiles_dsl = "\n".join([*node_lines, *edge_lines]).strip()
    return {"smiles_dsl": smiles_dsl, "variables": variables or None, "ancestors": ancestors}


def _extract_document_variables(knowledge_graph: dict) -> dict | list | None:
    abstract = knowledge_graph.get("abstract_structure")
    if isinstance(abstract, dict) and abstract.get("variables"):
        return abstract.get("variables")
    concepts = knowledge_graph.get("concepts")
    if isinstance(concepts, list) and concepts:
        return [
            {
                "id": c.get("id") or c.get("name"),
                "name": c.get("name") or c.get("id"),
                "type": c.get("type"),
                "description": c.get("description"),
            }
            for c in concepts
            if isinstance(c, dict)
        ]
    return None


def _extract_document_edges(knowledge_graph: dict) -> list:
    abstract = knowledge_graph.get("abstract_structure")
    if isinstance(abstract, dict) and isinstance(abstract.get("edges"), list):
        return abstract.get("edges") or []
    relationships = knowledge_graph.get("relationships")
    return relationships if isinstance(relationships, list) else []


def _derive_chunk_graph_elements(
    text: str,
    knowledge_graph: object,
    formulas: list[dict] | None = None,
) -> list[dict]:
    """チャンク本文に現れる knowledge_graph 要素を構造確認用に返す。"""
    graph = _json_obj(knowledge_graph)
    concepts = graph.get("concepts", []) if isinstance(graph.get("concepts"), list) else []
    relationships = graph.get("relationships", []) if isinstance(graph.get("relationships"), list) else []

    concept_by_id: dict[str, dict] = {}
    elements: list[dict] = []
    seen_concepts: set[str] = set()

    for concept in concepts:
        if not isinstance(concept, dict):
            continue
        cid = str(concept.get("id") or concept.get("name") or "").strip()
        name = str(concept.get("name") or cid).strip()
        if not cid or not name:
            continue
        concept_by_id[cid] = concept
        if name in text or cid in text:
            seen_concepts.add(cid)
            elements.append({
                "type": "concept",
                "id": cid,
                "label": name,
                "description": concept.get("description") or "",
                "status": "registered",
            })

    for rel in relationships:
        if not isinstance(rel, dict):
            continue
        source = str(rel.get("source") or "").strip()
        target = str(rel.get("target") or "").strip()
        relation = str(rel.get("relation") or "RELATED_TO").strip()
        if not source or not target:
            continue
        source_name = str(concept_by_id.get(source, {}).get("name") or source)
        target_name = str(concept_by_id.get(target, {}).get("name") or target)
        if source in seen_concepts and target in seen_concepts:
            elements.append({
                "type": "relationship",
                "id": f"{source}:{relation}:{target}",
                "label": f"{source_name} -[{relation}]-> {target_name}",
                "description": rel.get("description") or "",
                "status": "registered",
            })

    for formula in (formulas or [])[:8]:
        if not isinstance(formula, dict):
            continue
        formula_id = str(formula.get("id") or "").strip()
        latex = str(formula.get("latex") or "").strip()
        if not formula_id and not latex:
            continue
        elements.append({
            "type": "formula",
            "id": formula_id or latex[:80],
            "label": formula_id or "formula",
            "description": latex,
            "status": "chunk_formula",
        })

    if not elements:
        for concept in concepts[:8]:
            if not isinstance(concept, dict):
                continue
            cid = str(concept.get("id") or concept.get("name") or "").strip()
            name = str(concept.get("name") or cid).strip()
            if not cid or not name:
                continue
            elements.append({
                "type": "concept",
                "id": cid,
                "label": name,
                "description": concept.get("description") or "",
                "status": "document_graph",
            })

    return elements[:12]


def _chunk_status(chunk: dict) -> str:
    """チャンクのスクリプトステータスを判定する。"""
    if not chunk.get("stored_spoken_text"):
        return "ungenerated"
    # 音声キャッシュがあれば audio_ready
    session = _pg_session()
    try:
        row = session.execute(
            sa_text(
                "SELECT 1 FROM lecture_audio_cache WHERE chunk_id = CAST(:cid AS uuid) LIMIT 1"
            ),
            {"cid": chunk["id"]},
        ).fetchone()
        if row:
            return "audio_ready"
    except Exception:
        pass
    finally:
        session.close()
    return "generated"


def _get_system_admin_course_data(course_id: str) -> dict | None:
    """SYSTEM_ADMIN のシステム統計画面用に course_id だけでコースデータを取得する。"""
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("SELECT data FROM learning_courses WHERE id = :course_id LIMIT 1"),
            {"course_id": course_id},
        ).fetchone()
    finally:
        session.close()
    if not row or not row[0]:
        return None
    return row[0] if isinstance(row[0], dict) else json.loads(row[0])


# ---------------------------------------------------------------------------
# 1. バッチスクリプト生成
# ---------------------------------------------------------------------------


@router.get(
    "/courses/{course_id}/lecture-studio/settings",
    response_model=LectureStudioSettings,
)
def get_lecture_studio_settings(
    course_id: str,
    current_user: dict = Depends(_require_teacher),
) -> LectureStudioSettings:
    """原稿スタジオのコース単位設定を取得する。"""
    course_data = get_viewable_course_data(current_user["id"], course_id)
    if not course_data and current_user.get("role") == ROLE_SYSTEM_ADMIN:
        course_data = _get_system_admin_course_data(course_id)
    if not course_data:
        raise HTTPException(status_code=404, detail="Course not found")
    lecture_language = _normalize_lecture_language(get_course_lecture_language(course_data))
    return LectureStudioSettings(
        **course_persona_settings(course_data),
        lecture_language=lecture_language,
    )


@router.put(
    "/courses/{course_id}/lecture-studio/settings",
    response_model=LectureStudioSettings,
)
def update_lecture_studio_settings(
    course_id: str,
    body: LectureStudioSettings,
    current_user: dict = Depends(_require_teacher),
) -> LectureStudioSettings:
    """原稿スタジオのコース単位設定を保存する。"""
    course_data = get_editable_course_data(current_user["id"], course_id)
    if not course_data and current_user.get("role") == ROLE_SYSTEM_ADMIN:
        course_data = _get_system_admin_course_data(course_id)
    if not course_data:
        raise HTTPException(status_code=404, detail="Course not found")

    settings = {
        "narration_persona": normalize_persona_id(body.narration_persona),
        "response_persona": normalize_persona_id(body.response_persona),
        "lecture_language": body.lecture_language,
    }
    _save_lecture_studio_settings(course_id, course_data, settings)
    return LectureStudioSettings(**settings)


def _batch_generate_worker(
    task_id: str,
    course_id: str,
    chunks: list[dict],
    override: bool,
    course_data: dict,
    auto_audio: bool = False,
    user_id: str | None = None,
    language: str | None = None,
) -> None:
    """バックグラウンドスレッドでスクリプトを一括生成する。

    auto_audio=True の場合、完了後に音声生成タスクを自動的にキックし、
    結果データの ``next_task_id`` に新タスクIDを格納する (Issue #139)。
    ``language`` 省略時はコース設定 (``lecture_language``) を使う (migration 040 Phase 4)。
    生成した各チャンクには ``chunks.spoken_language`` として生成言語を記録する。
    """
    bind_usage_context("admin:lecture_generate", user_id=user_id, course_id=course_id)
    total = len(chunks)
    generated = 0
    skipped = 0
    settings = course_persona_settings(course_data)
    narration_persona = settings["narration_persona"]
    effective_language = _normalize_lecture_language(
        language, default=get_course_lecture_language(course_data),
    )

    update_background_task(task_id, "processing", result_data={
        "course_id": course_id,
        "phase": "script",
        "total_chunks": total,
        "generated": 0,
        "skipped": 0,
        "progress": 0,
    })

    session = _pg_session()
    try:
        for i, chunk in enumerate(chunks):
            if chunk["spoken_text"] and not override:
                skipped += 1
            else:
                result = generate_spoken_text_and_formulas(
                    chunk_text=chunk["text"],
                    chunk_index=chunk["chunk_index"],
                    course_data=course_data,
                    persona_id=narration_persona,
                    language=effective_language,
                )
                display_text = result.get("display_text") or chunk["text"]
                spoken_text = result["spoken_text"]
                formulas = result["formulas"]

                session.execute(
                    sa_text("""
                        UPDATE chunks
                        SET display_text = :display_text,
                            spoken_text = :spoken_text,
                            formulas = CAST(:formulas AS jsonb),
                            spoken_language = :spoken_language
                        WHERE id = CAST(:id AS uuid)
                    """),
                    {
                        "id": chunk["id"],
                        "display_text": display_text,
                        "spoken_text": spoken_text,
                        "formulas": json.dumps(formulas, ensure_ascii=False),
                        "spoken_language": effective_language,
                    },
                )
                session.commit()
                generated += 1
                # レート制限対策: 生成チャンク間に短い待機を挟む
                time.sleep(1.5)

            # チャンクごとに進捗を更新
            processed = generated + skipped
            update_background_task(task_id, "processing", result_data={
                "course_id": course_id,
                "phase": "script",
                "total_chunks": total,
                "generated": generated,
                "skipped": skipped,
                "progress": int(processed * 100 / total) if total > 0 else 100,
            })

    except Exception as exc:
        session.rollback()
        error_msg = str(exc)
        logger.error("batch_generate_worker failed for task %s: %s", task_id, error_msg)
        update_background_task(task_id, "failed", error_message=error_msg)
        return
    finally:
        session.close()

    # 自動パイプライン: 完了時に音声生成タスクをチェイン (Issue #139)
    next_task_id: str | None = None
    if auto_audio:
        try:
            fresh_chunks = _get_course_chunks(course_data)
            audio_task_id = str(uuid.uuid4())[:12]
            create_background_task(audio_task_id, "audio_generation", user_id)
            threading.Thread(
                target=_batch_audio_worker,
                args=(audio_task_id, course_id, fresh_chunks, effective_language),
                daemon=True,
            ).start()
            next_task_id = audio_task_id
            logger.info(
                "auto_audio chain: script task=%s -> audio task=%s (course=%s)",
                task_id, audio_task_id, course_id,
            )
        except Exception:
            logger.exception("Failed to auto-chain audio task after script task %s", task_id)

    completion_data = {
        "course_id": course_id,
        "phase": "script",
        "total_chunks": total,
        "generated": generated,
        "skipped": skipped,
        "progress": 100,
    }
    if next_task_id:
        completion_data["next_task_id"] = next_task_id
        completion_data["next_task_type"] = "audio_generation"

    update_background_task(task_id, "completed", result_data=completion_data)
    if override:
        _clear_script_regeneration_flag(course_id, course_data)
    logger.info(
        "batch_generate_worker completed: task=%s course=%s generated=%d skipped=%d",
        task_id, course_id, generated, skipped,
    )


@router.post(
    "/courses/{course_id}/lecture-scripts/generate",
    response_model=LectureScriptGenerateStartResponse,
    status_code=202,
)
def batch_generate_scripts(
    course_id: str,
    body: LectureScriptGenerateRequest,
    current_user: dict = Depends(_require_teacher),
) -> LectureScriptGenerateStartResponse:
    """コースの全チャンクに対して spoken_text と formulas を一括生成する（非同期）。

    即座に task_id を返し、処理はバックグラウンドで実行される。
    進捗は GET /api/admin/tasks/{task_id} でポーリングして確認する。
    result_data.progress (0-100) で進捗率を取得できる。
    """
    course_data = get_editable_course_data(current_user["id"], course_id)
    if not course_data and current_user.get("role") == ROLE_SYSTEM_ADMIN:
        course_data = _get_system_admin_course_data(course_id)
    if not course_data:
        raise HTTPException(status_code=404, detail="Course not found")

    chunks = _get_course_chunks(course_data)
    if not chunks:
        raise HTTPException(
            status_code=422,
            detail=(
                "このコースに紐づくテキストチャンクが見つかりません。"
                "教材がコースに設定されているか、またはPDF解析が完了しているかを確認してください。"
            ),
        )

    task_id = str(uuid.uuid4())[:12]
    create_background_task(task_id, "script_generation", current_user["id"])
    settings = course_data.get("lecture_studio_settings") or {}
    force_regenerate = isinstance(settings, dict) and bool(settings.get("scripts_need_regeneration"))
    effective_override = body.override or force_regenerate

    thread = threading.Thread(
        target=_batch_generate_worker,
        args=(
            task_id,
            course_id,
            chunks,
            effective_override,
            course_data,
            body.auto_audio,
            current_user["id"],
            body.language,
        ),
        daemon=True,
    )
    thread.start()

    logger.info(
        "batch_generate_scripts accepted: task=%s course=%s chunks=%d override=%s auto_audio=%s by user=%s",
        task_id, course_id, len(chunks), effective_override, body.auto_audio, current_user["id"],
    )

    return LectureScriptGenerateStartResponse(
        task_id=task_id,
        course_id=course_id,
        total_chunks=len(chunks),
        status="pending",
    )


# ---------------------------------------------------------------------------
# GET: コースのスクリプト一覧取得
# ---------------------------------------------------------------------------


@router.get(
    "/courses/{course_id}/lecture-scripts",
    response_model=list[LectureScriptChunkOut],
)
def get_course_scripts(
    course_id: str,
    current_user: dict = Depends(_require_teacher),
) -> list[LectureScriptChunkOut]:
    """コースに紐づくチャンクのスクリプト一覧を取得する。

    閲覧権限（オーナー / editor / viewer グループ）で許可する。
    """
    course_data = get_viewable_course_data(current_user["id"], course_id)
    if not course_data:
        raise HTTPException(status_code=404, detail="Course not found")

    chunks = _get_course_chunks(course_data)
    # スライド単位の音声キャッシュ有無をチャンク数分の N+1 クエリではなく1クエリで取得する。
    audio_slide_map = _load_chunk_slide_audio_map([c["id"] for c in chunks])

    result = []
    for c in chunks:
        slides, mismatch = split_slides(
            c.get("display_text") or c.get("text", ""),
            c.get("spoken_text"),
            c.get("formulas") or [],
        )
        ready_slide_indices = audio_slide_map.get(c["id"], set())
        audio_ready_slides = sum(
            1 for slide in slides if slide["slide_index"] in ready_slide_indices
        )
        result.append(LectureScriptChunkOut(
            chunk_id=c["id"],
            chunk_index=c["chunk_index"],
            text=c["text"],
            raw_text=c.get("raw_text", ""),
            display_text=c.get("display_text", ""),
            spoken_text=c.get("spoken_text", ""),
            formulas=[LectureFormulaItem(**f) for f in c["formulas"]] if c.get("formulas") else [],
            status=_chunk_status(c),
            material_id=c.get("material_id", ""),
            document_id=c.get("document_id", ""),
            page_start=c.get("page_start"),
            page_end=c.get("page_end"),
            section_id=c.get("section_id", ""),
            section_title=c.get("section_title", ""),
            section_level=c.get("section_level", 0),
            section_order=c.get("section_order", 0),
            pdf_url=c.get("pdf_url"),
            smiles_dsl=c.get("smiles_dsl", ""),
            variables=c.get("variables"),
            ancestors=c.get("ancestors"),
            graph_elements=c.get("graph_elements", []),
            spoken_language=c.get("spoken_language") or "ja",
            slide_count=len(slides),
            slide_mismatch=mismatch,
            audio_ready_slides=audio_ready_slides,
        ))
    return result


# ---------------------------------------------------------------------------
# POST: プレビュー用スライド分割（DB 非変更, Tier2-11: 講義系の判定共通化 提案11）
# ---------------------------------------------------------------------------


@router.post(
    "/lecture-studio/preview-split",
    response_model=LecturePreviewSplitResponse,
)
def preview_split_slides(
    body: LecturePreviewSplitRequest,
    current_user: dict = Depends(_require_teacher),
) -> LecturePreviewSplitResponse:
    """原稿スタジオのプレビュー用スライド分割を返す（DB は一切変更しない）。

    ``core.lecture.split_slides`` をそのまま呼ぶことで、プレビュー（本エンドポイント）と
    配信（``get_lecture_sequence`` 等）が同一の分割ロジックを共有する
    （docs/features/lecture_slide_sync_design.md の設計原則）。admin.js の
    `lsSplitSlides` ローカル実装（クライアント側の並行再実装）はこの API 呼び出しに
    置き換え、削除する（Tier2-11）。教員（``_require_teacher``）のみ利用可能。
    """
    slides, mismatch = split_slides(body.display_text, body.spoken_text, body.formulas)
    display_count, spoken_count = count_slide_marker_segments(body.display_text, body.spoken_text)
    return LecturePreviewSplitResponse(
        slides=[
            LectureSlide(
                slide_index=sd["slide_index"],
                display_text=sd["display_text"],
                spoken_text=sd["spoken_text"],
                formulas=[LectureFormulaItem(**f) for f in sd["formulas"]],
            )
            for sd in slides
        ],
        mismatch=mismatch,
        display_segment_count=display_count,
        spoken_segment_count=spoken_count,
    )


def _load_chunk_slide_audio_map(chunk_ids: list[str]) -> dict[str, set[int]]:
    """指定チャンク群のスライド単位音声キャッシュ有無を1クエリで一括取得する。

    Returns
    -------
    dict[str, set[int]]
        ``chunk_id -> {キャッシュ済み slide_index, ...}``（voice='alloy' のみ対象）。
    """
    if not chunk_ids:
        return {}
    session = _pg_session()
    try:
        placeholders = ", ".join(f":cid_{i}" for i in range(len(chunk_ids)))
        params = {f"cid_{i}": cid for i, cid in enumerate(chunk_ids)}
        rows = session.execute(
            sa_text(f"""
                SELECT chunk_id, slide_index
                FROM lecture_audio_cache
                WHERE chunk_id IN ({placeholders}) AND voice = 'alloy'
            """),
            params,
        ).fetchall()
    except Exception:
        logger.warning("Failed to load slide audio map for %d chunks", len(chunk_ids), exc_info=True)
        return {}
    finally:
        session.close()

    out: dict[str, set[int]] = {}
    for row in rows:
        out.setdefault(str(row[0]), set()).add(int(row[1]))
    return out


# ---------------------------------------------------------------------------
# 2. 手動スクリプト保存
# ---------------------------------------------------------------------------


@router.put(
    "/chunks/{chunk_id}/lecture-script",
    response_model=LectureScriptSaveResponse,
)
def save_lecture_script(
    chunk_id: str,
    body: LectureScriptSaveRequest,
    current_user: dict = Depends(_require_teacher),
) -> LectureScriptSaveResponse:
    """教員が編集した spoken_text とメタデータを DB に保存する。"""
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("SELECT 1 FROM chunks WHERE id = CAST(:cid AS uuid)"),
            {"cid": chunk_id},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Chunk not found")

        session.execute(
            sa_text("""
                UPDATE chunks
                SET display_text = :display_text,
                    spoken_text = :spoken_text,
                    formulas = CAST(:formulas AS jsonb)
                WHERE id = CAST(:cid AS uuid)
            """),
            {
                "cid": chunk_id,
                "display_text": body.display_text if body.display_text is not None else body.spoken_text,
                "spoken_text": body.spoken_text,
                "formulas": json.dumps(body.formulas, ensure_ascii=False),
            },
        )

        # 音声キャッシュを無効化（スクリプトが変更されたため）
        session.execute(
            sa_text("DELETE FROM lecture_audio_cache WHERE chunk_id = CAST(:cid AS uuid)"),
            {"cid": chunk_id},
        )
        session.commit()
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        logger.exception("Failed to save lecture script for chunk %s", chunk_id)
        raise HTTPException(status_code=500, detail="Failed to save script")
    finally:
        session.close()

    return LectureScriptSaveResponse(chunk_id=chunk_id, status="edited")


# ---------------------------------------------------------------------------
# 3. AI スクリプト書き換え
# ---------------------------------------------------------------------------

_REWRITE_PROMPT = """あなたは大学講義の音声原稿を改善するアシスタントです。

以下のソーステキストと現在の表示テキスト・音声読み上げ原稿、そして教員からの指示に基づいて、
画面表示テキストと音声原稿を書き換えてください。

**重要:**
- 教員の指示に従い、必要に応じて一般的な物理学・数学の知識を補足してください
- ソーステキストに限定されず、教員が指示する内容を反映させてください
- display_text では数式を `[[FORMULA_0]]`, `[[FORMULA_1]]` のようなプレースホルダーで表現してください。`$...$` や `$$...$$` は使わないでください
- spoken_text では LaTeX 数式を自然言語に変換してください（例: `E = mc^2` → 「Eイコールmcの二乗」）
- ソーステキストが日本語の場合、自然な日本語の講義調で書いてください。
- ソーステキストが英語の場合、無理にカタカナや全角に変換せず、**自然な半角英語の文章（Natural English sentences）**として書いてください
- 数式メタデータも更新してください

## ソーステキスト:
{source_text}

## 現在の表示テキスト:
{current_display_text}

## 現在の音声原稿:
{current_spoken_text}

## 教員からの指示:
{instructor_prompt}

## 読み上げテキストの語り口設定:
{persona_instruction}

## 出力形式 (厳密にJSON):
{{
  "display_text": "エネルギーは [[FORMULA_0]] で表される。",
  "spoken_text": "エネルギーは Eイコールmcの二乗 で表される。",
  "formulas": [
    {{"id": "[[FORMULA_0]]", "latex": "E = mc^2", "spoken": "Eイコールmcの二乗", "is_display": false}}
  ]
}}

重要: JSON のみを出力してください。マークダウンコードフェンスは不要です。"""

_THEORY_ASSIST_PROMPT = """あなたは原稿スタジオの理論コンポーネント編集アシスタントです。

現在のタブ: 理論

目的:
- 既存コンポーネントの inputs / outputs は維持してください。
- summary, preconditions, constraints, invalid_conditions, dependencies, teacher_notes を改善してください。
- ソース本文・DSL・既存JSONを優先してください。
- 一般的な素粒子物理学・場の理論・有効理論の知識で妥当に補える場合は補ってください。
- 一般知識で補った項目は needs_source: true, source_refs: [] にしてください。
- 推測にしかならない場合、label は「未確定」にしてください。
- 「DSLから生成した候補」のような実装説明は summary に入れないでください。
- 出力JSONには inputs と outputs を含めないでください。
- JSONのみを出力してください。

ソース本文:
{source_text}

表示テキスト:
{current_display_text}

構造情報:
smiles_dsl:
{smiles_dsl}

variables:
{variables}

ancestors:
{ancestors}

現在の理論コンポーネントJSON:
{theory_components}

教員からの指示:
{instructor_prompt}

出力JSON:
{{
  "theory_components": [
    {{
      "id": "既存ID",
      "name": "既存名",
      "component_type": "theory",
      "summary": "",
      "status": "candidate",
      "source_chunks": [],
      "preconditions": [],
      "constraints": [],
      "invalid_conditions": [],
      "dependencies": [],
      "blackbox_policy": {{"default_level": "summary", "expand_if_unlearned": true}},
      "teacher_notes": ""
    }}
  ]
}}
"""

_AUDIO_ASSIST_PROMPT = """あなたは大学講義の読み上げ原稿を改善するアシスタントです。

現在のタブ: 音声

目的:
- 表示テキストや数式プレースホルダーは変更せず、spoken_text だけを改善してください。
- 音声で自然に理解できる文にしてください。
- 数式・記号は必要に応じて自然な読みへ変換してください。
- JSONのみを出力してください。

ソース本文:
{source_text}

表示テキスト:
{current_display_text}

現在の読み上げテキスト:
{current_spoken_text}

教員からの指示:
{instructor_prompt}

読み上げテキストの語り口設定:
{persona_instruction}

出力JSON:
{{
  "display_text": "{current_display_text}",
  "spoken_text": "",
  "formulas": []
}}
"""

_DISPLAY_ASSIST_PROMPT = """あなたは原稿スタジオの表示テキスト・数式編集アシスタントです。

現在のタブ: {studio_view}

目的:
- 表示テキストと数式メタデータを改善してください。
- display_text では数式を [[FORMULA_0]], [[FORMULA_1]] のようなプレースホルダーで表現してください。
- formulas には各プレースホルダーの latex / spoken / is_display を入れてください。
- spoken_text は表示テキストに対応する自然な読み上げ文にしてください。
- JSONのみを出力してください。

ソース本文:
{source_text}

現在の表示テキスト:
{current_display_text}

現在の読み上げテキスト:
{current_spoken_text}

現在の数式:
{current_formulas}

教員からの指示:
{instructor_prompt}

読み上げテキストの語り口設定:
{persona_instruction}

出力JSON:
{{
  "display_text": "",
  "spoken_text": "",
  "formulas": []
}}
"""


@router.post(
    "/chunks/{chunk_id}/lecture-script/rewrite",
    response_model=LectureScriptRewriteResponse,
)
def rewrite_lecture_script(
    chunk_id: str,
    body: LectureScriptRewriteRequest,
    current_user: dict = Depends(_require_teacher),
) -> LectureScriptRewriteResponse:
    """教員の指示に基づいて AI でスクリプトを書き換える。

    ソーステキストに限定せず、教員の指示に従い一般知識も活用して書き換える。
    """
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("""
                SELECT text, display_text, spoken_text, formulas, smiles_dsl, variables, ancestors
                FROM chunks
                WHERE id = CAST(:cid AS uuid)
            """),
            {"cid": chunk_id},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Chunk not found")

        source_text = row[0] or ""
        current_display = row[1] or source_text
        current_spoken = row[2] or source_text
        current_formulas = row[3] if row[3] else []
        smiles_dsl = row[4] or ""
        variables = row[5] if row[5] is not None else None
        ancestors = row[6] if row[6] is not None else None
    finally:
        session.close()

    studio_view = (body.studio_view or "edit").strip().lower()
    persona_instruction = persona_prompt(body.narration_persona, target="narration") or "指定なし。通常の自然な講義調で書き換えてください。"
    if studio_view == "theory":
        prompt = _THEORY_ASSIST_PROMPT.format(
            source_text=source_text[:4000],
            current_display_text=current_display[:4000],
            smiles_dsl=smiles_dsl[:3000],
            variables=json.dumps(variables, ensure_ascii=False)[:3000],
            ancestors=json.dumps(ancestors, ensure_ascii=False)[:3000],
            theory_components=json.dumps(body.theory_components, ensure_ascii=False, indent=2)[:8000],
            instructor_prompt=body.prompt[:2000],
        )
    elif studio_view == "audio":
        prompt = _AUDIO_ASSIST_PROMPT.format(
            source_text=source_text[:4000],
            current_display_text=current_display[:4000],
            current_spoken_text=current_spoken[:4000],
            instructor_prompt=body.prompt[:2000],
            persona_instruction=persona_instruction,
        )
    elif studio_view in ("compare", "edit"):
        prompt = _DISPLAY_ASSIST_PROMPT.format(
            studio_view=studio_view,
            source_text=source_text[:4000],
            current_display_text=current_display[:4000],
            current_spoken_text=current_spoken[:4000],
            current_formulas=json.dumps(current_formulas, ensure_ascii=False)[:3000],
            instructor_prompt=body.prompt[:2000],
            persona_instruction=persona_instruction,
        )
    else:
        prompt = _REWRITE_PROMPT.format(
            source_text=source_text[:4000],
            current_display_text=current_display[:4000],
            current_spoken_text=current_spoken[:4000],
            instructor_prompt=body.prompt[:2000],
            persona_instruction=persona_instruction,
        )

    params = get_llm_params("fast")

    try:
        with usage_context("admin:lecture_rewrite", user_id=current_user["id"]):
            raw = generate_text(
                messages=[{"role": "user", "content": prompt}],
                model=params["model"],
                reasoning_effort=params["reasoning_effort"],
            )
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = [ln for ln in lines if not ln.strip().startswith("```")]
            cleaned = "\n".join(lines)
        result = _normalize_rewrite_result(json.loads(cleaned, strict=False), studio_view)
        theory_components = result.get("theory_components", [])
        display_text = result.get("display_text") or current_display
        spoken_text = result.get("spoken_text", current_spoken)
        if studio_view == "theory":
            display_text = current_display
            spoken_text = current_spoken
            formulas = current_formulas
        elif studio_view == "audio":
            display_text = current_display
            formulas = current_formulas
        else:
            formulas = result.get("formulas", [])
    except Exception:
        logger.exception("AI rewrite failed for chunk %s", chunk_id)
        raise HTTPException(status_code=500, detail="AI rewrite failed")

    if studio_view != "theory":
        session = _pg_session()
        try:
            session.execute(
                sa_text("""
                    UPDATE chunks
                    SET display_text = :display_text,
                        spoken_text = :spoken_text,
                        formulas = CAST(:formulas AS jsonb)
                    WHERE id = CAST(:cid AS uuid)
                """),
                {
                    "cid": chunk_id,
                    "display_text": display_text,
                    "spoken_text": spoken_text,
                    "formulas": json.dumps(formulas, ensure_ascii=False),
                },
            )
            # 音声キャッシュを無効化
            session.execute(
                sa_text("DELETE FROM lecture_audio_cache WHERE chunk_id = CAST(:cid AS uuid)"),
                {"cid": chunk_id},
            )
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    return LectureScriptRewriteResponse(
        chunk_id=chunk_id,
        display_text=display_text,
        spoken_text=spoken_text,
        formulas=[LectureFormulaItem(**f) for f in formulas],
        theory_components=theory_components if isinstance(theory_components, list) else [],
    )


# ---------------------------------------------------------------------------
# 4. バッチ音声生成
# ---------------------------------------------------------------------------


def _batch_audio_worker(
    task_id: str,
    course_id: str,
    chunks: list[dict],
    course_language: str = "ja",
) -> None:
    """バックグラウンドスレッドでスライド単位に TTS 音声を一括生成する (migration 040)。

    各チャンクを ``split_slides`` でスライドに分割し、スライドごとに
    ``lecture_audio_cache (chunk_id, slide_index, voice)`` へキャッシュする。
    進捗（generated/skipped/errors/progress）は従来どおりチャンク単位で集計する
    （1チャンク内で1枚でも生成できれば generated、全スライドスキップなら skipped、
    生成0件でエラーのみなら errors）。

    ``course_language`` はチャンク自身に ``spoken_language`` が無い場合のフォールバック
    （通常は原稿生成時に書き込まれているため、この引数はコース設定からの保険値）。
    言語切替チェーン (``_batch_generate_and_audio_worker``) から呼ばれる場合はこの
    タスク自体が既に ``phase: "script"`` を経ているため、ここでは ``phase: "audio"`` を
    result_data に出す (§3-3)。
    """
    bind_usage_context("admin:lecture_tts", course_id=course_id)
    total = len(chunks)
    generated = 0
    skipped = 0
    errors = 0

    update_background_task(task_id, "processing", result_data={
        "course_id": course_id,
        "phase": "audio",
        "total_chunks": total,
        "generated": 0,
        "skipped": 0,
        "errors": 0,
        "progress": 0,
    })

    for chunk in chunks:
        chunk_id = chunk["id"]
        spoken_text = chunk.get("spoken_text")

        if not spoken_text:
            skipped += 1
            processed = generated + skipped + errors
            update_background_task(task_id, "processing", result_data={
                "course_id": course_id,
                "phase": "audio",
                "total_chunks": total,
                "generated": generated,
                "skipped": skipped,
                "errors": errors,
                "progress": int(processed * 100 / total) if total > 0 else 100,
            })
            continue

        display_text = chunk.get("display_text") or chunk.get("text") or ""
        formulas = chunk.get("formulas") or []
        chunk_language = chunk.get("spoken_language") or course_language
        slides, _mismatch = split_slides(display_text, spoken_text, formulas)

        # 分割数が減った場合の残留スライド行を掃除する（生成前に一度だけ）
        session = _pg_session()
        try:
            session.execute(
                sa_text("""
                    DELETE FROM lecture_audio_cache
                    WHERE chunk_id = CAST(:cid AS uuid) AND slide_index >= :slide_count
                """),
                {"cid": chunk_id, "slide_count": len(slides)},
            )
            session.commit()
        except Exception:
            session.rollback()
            logger.warning(
                "Failed to clean up stale slide audio rows for chunk %s", chunk_id, exc_info=True,
            )
        finally:
            session.close()

        chunk_generated = 0
        chunk_errors = 0
        aborted = False

        for slide in slides:
            slide_spoken = slide.get("spoken_text")
            slide_index = slide["slide_index"]
            if not slide_spoken:
                # spoken_text の無いスライド（従来のチャンク単位スキップと同義）
                continue

            # スライド単位のキャッシュ確認
            session = _pg_session()
            try:
                cached = session.execute(
                    sa_text("""
                        SELECT 1 FROM lecture_audio_cache
                        WHERE chunk_id = CAST(:cid AS uuid) AND slide_index = :slide_index AND voice = :voice
                        LIMIT 1
                    """),
                    {"cid": chunk_id, "slide_index": slide_index, "voice": "alloy"},
                ).fetchone()
            finally:
                session.close()

            if cached:
                continue

            try:
                # TTS 生成（プロバイダは generate_tts_audio が自動選択。言語はチャンクの
                # spoken_language を最優先し、無ければコース設定にフォールバックする）
                audio_bytes = generate_tts_audio(slide_spoken, language=chunk_language)
                if audio_bytes is None:
                    chunk_errors += 1
                    logger.warning(
                        "TTS audio generation returned None for chunk %s slide %d (no provider available)",
                        chunk_id, slide_index,
                    )
                    continue

                duration_ms = max(1000, len(audio_bytes) * 8 // 128)

                session = _pg_session()
                try:
                    session.execute(
                        sa_text("""
                            INSERT INTO lecture_audio_cache
                                (chunk_id, slide_index, voice, audio_data, duration_ms, language)
                            VALUES
                                (CAST(:cid AS uuid), :slide_index, :voice, :audio_data, :duration_ms, :language)
                            ON CONFLICT (chunk_id, slide_index, voice) DO UPDATE
                            SET audio_data = EXCLUDED.audio_data,
                                duration_ms = EXCLUDED.duration_ms,
                                language = EXCLUDED.language,
                                created_at = now()
                        """),
                        {
                            "cid": chunk_id,
                            "slide_index": slide_index,
                            "voice": "alloy",
                            "audio_data": audio_bytes,
                            "duration_ms": duration_ms,
                            "language": chunk_language,
                        },
                    )
                    session.commit()
                    chunk_generated += 1
                except Exception:
                    session.rollback()
                    chunk_errors += 1
                    logger.warning(
                        "Failed to cache audio for chunk %s slide %d", chunk_id, slide_index, exc_info=True,
                    )
                finally:
                    session.close()

                # レート制限対策: スライド間に 0.5 秒の遅延
                time.sleep(0.5)
            except TtsFatalError as exc:
                # API 未有効化・認証エラーなど恒久的な失敗: 残りを処理しても無駄なので即終了
                error_msg = str(exc)
                logger.error("TTS fatal error, aborting task %s: %s", task_id, error_msg)
                update_background_task(task_id, "failed", error_message=error_msg)
                aborted = True
                break
            except Exception:
                chunk_errors += 1
                logger.warning(
                    "TTS generation failed for chunk %s slide %d", chunk_id, slide_index, exc_info=True,
                )

        if aborted:
            return

        # チャンク単位の集計（進捗率は従来どおりチャンク数ベース）
        if chunk_generated > 0:
            generated += 1
        elif chunk_errors > 0:
            errors += 1
        else:
            skipped += 1

        processed = generated + skipped + errors
        update_background_task(task_id, "processing", result_data={
            "course_id": course_id,
            "phase": "audio",
            "total_chunks": total,
            "generated": generated,
            "skipped": skipped,
            "errors": errors,
            "progress": int(processed * 100 / total) if total > 0 else 100,
        })

    update_background_task(task_id, "completed", result_data={
        "course_id": course_id,
        "phase": "audio",
        "total_chunks": total,
        "generated": generated,
        "skipped": skipped,
        "errors": errors,
        "progress": 100,
    })
    logger.info(
        "batch_audio_worker completed: task=%s course=%s generated=%d skipped=%d errors=%d",
        task_id, course_id, generated, skipped, errors,
    )


def _batch_generate_and_audio_worker(
    task_id: str,
    course_id: str,
    course_data: dict,
    target_language: str,
) -> None:
    """言語切替時の連鎖ワーカー: 原稿再生成 (phase=script) → 音声生成 (phase=audio) を
    1つの background task 内でフェーズ進行させる (§3-3)。

    設計上の判断（報告参照）: display_text は言語別に温存せず、``_batch_generate_worker``
    と同じ override 全再生成経路を再利用する（display_text はソース言語のまま生成される
    ため実害は無く、実装を単純に保てる）。各チャンク更新のたびに当該チャンクの音声
    キャッシュ（旧言語含む全スライド）を削除し、既存の「原稿変更時は音声キャッシュ無効化」
    ルールと同じ挙動にする。
    """
    bind_usage_context("admin:lecture_generate", course_id=course_id)
    chunks = _get_course_chunks(course_data)
    total = len(chunks)
    settings = course_persona_settings(course_data)
    narration_persona = settings["narration_persona"]

    update_background_task(task_id, "processing", result_data={
        "course_id": course_id,
        "phase": "script",
        "total_chunks": total,
        "generated": 0,
        "skipped": 0,
        "progress": 0,
    })

    generated = 0
    session = _pg_session()
    try:
        for chunk in chunks:
            result = generate_spoken_text_and_formulas(
                chunk_text=chunk["text"],
                chunk_index=chunk["chunk_index"],
                course_data=course_data,
                persona_id=narration_persona,
                language=target_language,
            )
            display_text = result.get("display_text") or chunk["text"]
            spoken_text = result["spoken_text"]
            formulas = result["formulas"]

            session.execute(
                sa_text("""
                    UPDATE chunks
                    SET display_text = :display_text,
                        spoken_text = :spoken_text,
                        formulas = CAST(:formulas AS jsonb),
                        spoken_language = :spoken_language
                    WHERE id = CAST(:id AS uuid)
                """),
                {
                    "id": chunk["id"],
                    "display_text": display_text,
                    "spoken_text": spoken_text,
                    "formulas": json.dumps(formulas, ensure_ascii=False),
                    "spoken_language": target_language,
                },
            )
            # 言語が変わったため旧言語の音声キャッシュ（当該チャンクの全スライド）を無効化する
            # （既存の「原稿変更時は音声キャッシュ無効化」ルールと同じ挙動。§2-3）
            session.execute(
                sa_text("DELETE FROM lecture_audio_cache WHERE chunk_id = CAST(:cid AS uuid)"),
                {"cid": chunk["id"]},
            )
            session.commit()
            generated += 1
            update_background_task(task_id, "processing", result_data={
                "course_id": course_id,
                "phase": "script",
                "total_chunks": total,
                "generated": generated,
                "skipped": 0,
                "progress": int(generated * 100 / total) if total > 0 else 100,
            })
            time.sleep(1.5)
    except Exception as exc:
        session.rollback()
        error_msg = str(exc)
        logger.error(
            "batch_generate_and_audio_worker (script phase) failed for task %s: %s",
            task_id, error_msg,
        )
        update_background_task(task_id, "failed", error_message=error_msg)
        return
    finally:
        session.close()

    _clear_script_regeneration_flag(course_id, course_data)

    # phase 2: 音声生成（更新済みの spoken_text / spoken_language で再取得する）
    fresh_chunks = _get_course_chunks(course_data)
    logger.info(
        "language switch chain: script phase done (task=%s course=%s language=%s), "
        "starting audio phase",
        task_id, course_id, target_language,
    )
    _batch_audio_worker(task_id, course_id, fresh_chunks, target_language)


@router.post(
    "/courses/{course_id}/lecture-audio/generate",
    response_model=LectureAudioGenerateStartResponse,
    status_code=202,
)
def batch_generate_audio(
    course_id: str,
    body: LectureAudioGenerateRequest | None = None,
    current_user: dict = Depends(_require_teacher),
) -> LectureAudioGenerateStartResponse:
    """コースの全スクリプトに対して TTS 音声を一括生成する（非同期）。

    即座に task_id を返し、処理はバックグラウンドで実行される。
    進捗は GET /api/admin/tasks/{task_id} でポーリングして確認する。
    result_data.progress (0-100) で進捗率を取得できる（言語切替時は result_data.phase が
    "script" → "audio" と遷移する。§3-3）。

    ``body.language`` がコース設定の ``lecture_language`` と異なる場合、またはいずれかの
    チャンクの ``spoken_language`` が指定言語と食い違う場合は、原稿再生成 → 音声生成の
    チェーンを1タスクで実行する（既存の音声は無効化されることが前提。§3-3 の確認ダイアログは
    フロント側の責務）。同一言語で全チャンクの spoken_language も一致していれば、
    従来どおり未生成スライドのみ生成する。
    """
    course_data = get_editable_course_data(current_user["id"], course_id)
    if not course_data and current_user.get("role") == ROLE_SYSTEM_ADMIN:
        course_data = _get_system_admin_course_data(course_id)
    if not course_data:
        raise HTTPException(status_code=404, detail="Course not found")

    current_language = _normalize_lecture_language(get_course_lecture_language(course_data))
    requested_language = body.language if body else None
    target_language = _normalize_lecture_language(requested_language, default=current_language)

    chunks = _get_course_chunks(course_data)
    if not chunks:
        raise HTTPException(
            status_code=422,
            detail=(
                "このコースに紐づくテキストチャンクが見つかりません。"
                "教材がコースに設定されているか、またはPDF解析が完了しているかを確認してください。"
            ),
        )

    language_mismatch = any(
        _normalize_lecture_language(c.get("spoken_language"), default=current_language) != target_language
        for c in chunks
    )
    needs_chain = target_language != current_language or language_mismatch

    task_id = str(uuid.uuid4())[:12]
    create_background_task(task_id, "audio_generation", current_user["id"])

    if needs_chain:
        if target_language != current_language:
            course_data = _save_lecture_language(course_id, course_data, target_language)
        thread = threading.Thread(
            target=_batch_generate_and_audio_worker,
            args=(task_id, course_id, course_data, target_language),
            daemon=True,
        )
    else:
        thread = threading.Thread(
            target=_batch_audio_worker,
            args=(task_id, course_id, chunks, target_language),
            daemon=True,
        )
    thread.start()

    logger.info(
        "batch_generate_audio accepted: task=%s course=%s chunks=%d language=%s chain=%s by user=%s",
        task_id, course_id, len(chunks), target_language, needs_chain, current_user["id"],
    )

    return LectureAudioGenerateStartResponse(
        task_id=task_id,
        course_id=course_id,
        total_chunks=len(chunks),
        status="pending",
    )


# ---------------------------------------------------------------------------
# 4b. スライド単位の試聴 (migration 040 Phase 4 §4-2)
# ---------------------------------------------------------------------------


@router.get(
    "/chunks/{chunk_id}/lecture-audio",
    response_model=LectureTTSResponse,
)
def preview_lecture_audio(
    chunk_id: str,
    slide_index: int = 0,
    voice: str = "alloy",
    current_user: dict = Depends(_require_teacher),
) -> LectureTTSResponse:
    """教員が原稿スタジオでスライド単位の生成済み音声を試聴する。

    受講画面と同じキャッシュキー (chunk_id, slide_index, voice) で
    ``lecture_audio_cache`` を引く。**キャッシュ配信のみ・生成は行わない**方針は学習者向け
    ``/tts`` と同じ（未生成の場合は 404）。認可は本ファイルの他のチャンク単位エンドポイント
    （``save_lecture_script`` 等）と同じパターン（``_require_teacher`` のみ、コース単位の
    追加チェックはしない）を踏襲する。
    """
    try:
        uuid.UUID(chunk_id)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(
            status_code=404,
            detail="この内容の音声はまだ生成されていません。",
        )

    session = _pg_session()
    try:
        row = session.execute(
            sa_text("""
                SELECT audio_data, duration_ms, word_timestamps
                FROM lecture_audio_cache
                WHERE chunk_id = CAST(:cid AS uuid) AND voice = :voice AND slide_index = :slide_index
                LIMIT 1
            """),
            {"cid": chunk_id, "voice": voice, "slide_index": slide_index},
        ).fetchone()
    except Exception:
        # DB エラーでも 500 で詳細を漏らさず「未生成」扱いにする（学習者向け
        # _get_audio_cache と同じ fail-safe な方針）。
        logger.warning(
            "Failed to look up preview audio cache for chunk %s slide %d", chunk_id, slide_index,
            exc_info=True,
        )
        raise HTTPException(
            status_code=404,
            detail="この内容の音声はまだ生成されていません。",
        )
    finally:
        session.close()

    if not row:
        raise HTTPException(
            status_code=404,
            detail="この内容の音声はまだ生成されていません。",
        )

    audio_bytes = row[0]
    if isinstance(audio_bytes, memoryview):
        audio_bytes = bytes(audio_bytes)

    return LectureTTSResponse(
        chunk_id=chunk_id,
        audio_base64=base64.b64encode(audio_bytes).decode("utf-8"),
        duration_ms=row[1] or 0,
        word_timestamps=row[2] if row[2] else [],
    )


# ---------------------------------------------------------------------------
# 5. コース教材の構造再解析
# ---------------------------------------------------------------------------


@router.post("/courses/{course_id}/structure/reanalyze")
def reanalyze_course_structure(
    course_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """既存チャンクを維持したまま、構造DSL/変数/ancestorsを再解析する。"""
    course_data = get_editable_course_data(current_user["id"], course_id)
    if not course_data and current_user.get("role") == ROLE_SYSTEM_ADMIN:
        course_data = _get_system_admin_course_data(course_id)
    if not course_data:
        raise HTTPException(status_code=404, detail="Course not found")

    sources = course_data.get("sources", []) if isinstance(course_data, dict) else []
    material_ids = [
        str(s.get("material_id")).strip()
        for s in sources
        if isinstance(s, dict) and s.get("material_id")
    ]
    material_ids = list(dict.fromkeys(material_ids))
    if not material_ids:
        raise HTTPException(status_code=400, detail="No source materials linked to this course")

    active = get_active_task_for_course(course_id)
    if active:
        raise HTTPException(status_code=409, detail="Another course task is already running")

    task_id = str(uuid.uuid4())[:12]
    create_background_task(task_id, "structure_reanalysis", current_user["id"])
    update_background_task(task_id, "pending", result_data={
        "course_id": course_id,
        "total_materials": len(material_ids),
        "processed_materials": 0,
        "updated_chunks": 0,
        "errors": 0,
        "progress": 0,
        "stage": "queued",
    })

    thread = threading.Thread(
        target=reanalyze_course_structure_background,
        args=(course_id, course_data, task_id),
        daemon=True,
    )
    thread.start()

    logger.info(
        "structure reanalysis accepted: task=%s course=%s materials=%d by user=%s",
        task_id, course_id, len(material_ids), current_user["id"],
    )
    return {
        "task_id": task_id,
        "course_id": course_id,
        "total_materials": len(material_ids),
        "status": "pending",
    }


# ---------------------------------------------------------------------------
# 5b. 新 Agent Pipeline のコース単位実行
# ---------------------------------------------------------------------------


def _course_pipeline_documents(course_data: dict) -> list[dict]:
    sources = course_data.get("sources", []) if isinstance(course_data, dict) else []
    material_ids = [
        str(s.get("material_id")).strip()
        for s in sources
        if isinstance(s, dict) and s.get("material_id")
    ]
    material_ids = list(dict.fromkeys(mid for mid in material_ids if mid))
    if not material_ids:
        return []

    params = {f"mid_{i}": mid for i, mid in enumerate(material_ids)}
    placeholders = ", ".join(f":mid_{i}" for i in range(len(material_ids)))
    session = _pg_session()
    try:
        rows = session.execute(
            sa_text(
                f"""
                SELECT id::text, COALESCE(source_path, ''), COALESCE(filename, title, 'document.pdf')
                FROM documents
                WHERE source_path IN ({placeholders})
                ORDER BY created_at ASC
                """
            ),
            params,
        ).fetchall()
    finally:
        session.close()
    return [
        {"document_id": row[0], "material_id": row[1], "filename": row[2] or "document.pdf"}
        for row in rows
        if row[0] and row[1]
    ]


def _pipeline_source_kind_from_name(name: str | None) -> str | None:
    lower = (name or "").lower()
    if lower.endswith(".tar.gz") or lower.endswith(".tgz"):
        return "tex_archive"
    if lower.endswith(".pdf"):
        return "pdf"
    return None


def _pipeline_source_candidates(material_id: str, filename: str) -> list[tuple[str, str]]:
    """Return storage object candidates paired with the source_kind they imply.

    Older rows may have a title-like filename without the original extension, so
    probing only by filename can incorrectly fall back to PDF. Always include the
    canonical upload object names and infer source_kind from the object found.
    """
    result: list[tuple[str, str]] = []

    def add(object_name: str | None, source_kind: str | None = None) -> None:
        if not object_name:
            return
        kind = source_kind or _pipeline_source_kind_from_name(object_name) or "pdf"
        pair = (object_name, kind)
        if pair not in result:
            result.append(pair)

    filename_kind = _pipeline_source_kind_from_name(filename)
    if filename_kind == "tex_archive":
        add(f"uploads/{material_id}.tar.gz", "tex_archive")
        add(f"uploads/{material_id}.tgz", "tex_archive")
        add(filename, "tex_archive")
        add(f"uploads/{material_id}.pdf", "pdf")
    elif filename_kind == "pdf":
        add(f"uploads/{material_id}.pdf", "pdf")
        add(filename, "pdf")
        add(f"uploads/{material_id}.tar.gz", "tex_archive")
        add(f"uploads/{material_id}.tgz", "tex_archive")
    else:
        add(f"uploads/{material_id}.tar.gz", "tex_archive")
        add(f"uploads/{material_id}.tgz", "tex_archive")
        add(f"uploads/{material_id}.pdf", "pdf")
        add(filename, None)
    add(material_id, filename_kind or "pdf")
    return result


def _load_pipeline_pdf(material_id: str, filename: str) -> bytes:
    storage = get_storage_client()
    for object_name, source_kind in _pipeline_source_candidates(material_id, filename):
        if source_kind != "pdf":
            continue
        try:
            return storage.get_object("raw-papers", object_name)
        except Exception:
            continue
    raise FileNotFoundError(f"PDF object not found for material {material_id}")


def _load_pipeline_source(material_id: str, filename: str) -> tuple[bytes, str]:
    """教材ファイルをストレージからロードし、(bytes, source_kind) を返す。

    filename 拡張子と canonical upload object 名から source_kind を判定する。
    """
    storage = get_storage_client()
    attempted: list[str] = []
    for object_name, source_kind in _pipeline_source_candidates(material_id, filename):
        attempted.append(f"{object_name}:{source_kind}")
        try:
            return storage.get_object("raw-papers", object_name), source_kind
        except Exception:
            continue
    raise FileNotFoundError(
        f"Source object not found for material {material_id} "
        f"(tried={', '.join(attempted)})"
    )


def _set_document_pipeline_status(document_id: str, status: str) -> None:
    session = _pg_session()
    try:
        session.execute(
            sa_text(
                "UPDATE documents SET status = :status, updated_at = now() "
                "WHERE id = CAST(:document_id AS uuid)"
            ),
            {"document_id": document_id, "status": status},
        )
        session.commit()
    except Exception:
        session.rollback()
        logger.warning("Failed to update document status: document=%s status=%s", document_id, status, exc_info=True)
    finally:
        session.close()


def _get_editable_material_document(material_id: str, current_user: dict) -> dict:
    session = _pg_session()
    try:
        row = session.execute(
            sa_text(
                """
                SELECT id::text, source_path, COALESCE(filename, title, 'document.pdf'), uploaded_by::text
                FROM documents
                WHERE source_path = :material_id
                ORDER BY created_at DESC
                LIMIT 1
                """
            ),
            {"material_id": material_id},
        ).fetchone()
    finally:
        session.close()
    if not row:
        raise HTTPException(status_code=404, detail="Material not found")
    if current_user.get("role") != ROLE_SYSTEM_ADMIN and str(row[3]) != str(current_user.get("id")):
        raise HTTPException(status_code=403, detail="Material is not editable")
    return {
        "document_id": row[0],
        "material_id": row[1] or material_id,
        "filename": row[2] or "document.pdf",
    }


def _get_active_task_for_material(material_id: str) -> dict | None:
    session = _pg_session()
    try:
        row = session.execute(
            sa_text(
                """
                SELECT id, task_type, status, result_data, error_message, created_at, updated_at
                FROM background_tasks
                WHERE status IN ('pending', 'processing')
                  AND result_data IS NOT NULL
                  AND result_data->>'material_id' = :material_id
                ORDER BY created_at DESC
                LIMIT 1
                """
            ),
            {"material_id": material_id},
        ).fetchone()
    finally:
        session.close()
    if not row:
        return None
    return {
        "task_id": row[0],
        "task_type": row[1],
        "status": row[2],
        "result_data": row[3] or {},
        "error_message": row[4],
        "created_at": row[5].isoformat() if row[5] else "",
        "updated_at": row[6].isoformat() if row[6] else "",
    }


def _material_pipeline_status(material_id: str, document_id: str) -> dict:
    stages = {stage: "not_started" for stage in DOCUMENT_PIPELINE_STAGE_LABELS}
    status = "not_started"
    current_stage = ""
    error_message = ""
    session = _pg_session()
    try:
        row = session.execute(
            sa_text(
                """
                SELECT status, current_stage, error_message, stage_outputs
                FROM document_analysis_runs
                WHERE (document_id = :document_id OR material_id = :material_id)
                  AND (run_type IS NULL OR run_type <> 'revision')
                ORDER BY created_at DESC
                LIMIT 1
                """
            ),
            {"document_id": document_id, "material_id": material_id},
        ).fetchone()
    finally:
        session.close()

    degraded_stages: list = []
    available_features: list = ["rag_chat"]  # chunks は常に使える
    if row:
        status = row[0] or "not_started"
        current_stage = row[1] or ""
        error_message = row[2] or ""
        stage_outputs = row[3] or {}
        if isinstance(stage_outputs, str):
            try:
                stage_outputs = json.loads(stage_outputs)
            except Exception:
                stage_outputs = {}
        if isinstance(stage_outputs, dict):
            for stage in stages:
                info = stage_outputs.get(stage)
                if isinstance(info, dict):
                    stages[stage] = info.get("status") or ("completed" if info.get("progress") == 100 else "not_started")
            # 縮退ステージ情報を persist artifact から取得
            persist_info = stage_outputs.get("persist_claims_components_graph") or {}
            if isinstance(persist_info, dict):
                degraded_stages = list(persist_info.get("degraded_stages") or [])
                if not persist_info.get("components_skipped"):
                    available_features.append("components")
                if not persist_info.get("graph_skipped"):
                    available_features.append("component_graph")
            elif status == "completed":
                # 縮退情報なし = 全機能利用可能
                available_features.extend(["components", "component_graph"])

    active = _get_active_task_for_material(material_id)
    if active:
        result_data = active.get("result_data") or {}
        status = "running"
        current_stage = result_data.get("stage") or current_stage
        target_stage = result_data.get("target_stage") or ""
        start_stage = result_data.get("start_stage") or ""
        if target_stage:
            stages[target_stage] = "running"
        elif start_stage:
            stages[start_stage] = "running"
        elif current_stage in stages:
            stages[current_stage] = "running"

    if status == "failed" and current_stage in stages:
        stages[current_stage] = "failed"

    is_degraded = bool(degraded_stages)
    # 縮退状態を retry ヒントとして提示（最初の縮退ステージから再実行を促す）
    retry_suggestion = degraded_stages[0] if degraded_stages else ""

    return {
        "material_id": material_id,
        "document_id": document_id,
        "status": status,
        "current_stage": current_stage,
        "error_message": error_message,
        "stages": stages,
        "active_task_id": active["task_id"] if active else "",
        "active_target_stage": ((active.get("result_data") or {}).get("target_stage") if active else "") or "",
        "active_start_stage": ((active.get("result_data") or {}).get("start_stage") if active else "") or "",
        # 縮退状態情報 (フロントエンドが「何が使えて何が使えないか」を表示するために使う)
        "is_degraded": is_degraded,
        "degraded_stages": degraded_stages,
        "available_features": available_features,
        "retry_suggestion": retry_suggestion,
    }


def _material_document_pipeline_worker(
    *,
    task_id: str,
    user_id: str,
    document: dict,
    target_stage: str | None,
    start_stage: str | None = None,
) -> None:
    from core.document_pipeline import PipelineStageError, run_document_pipeline

    material_id = document["material_id"]
    document_id = document["document_id"]
    label_stage = start_stage or target_stage or ""
    label = DOCUMENT_PIPELINE_STAGE_LABELS.get(label_stage, "Agent Pipeline")
    current_stage = start_stage or target_stage or "document_pipeline"

    def publish(status: str = "processing", progress: int = 0, error_message: str | None = None) -> None:
        update_background_task(
            task_id,
            status,
            result_data={
                "document_id": document_id,
                "material_id": material_id,
                "stage": current_stage,
                "target_stage": target_stage or "",
                "start_stage": start_stage or "",
                "label": label,
                "generated": 1 if status == "completed" else 0,
                "failed": 1 if status == "failed" else 0,
                "skipped": 0,
                "total_documents": 1,
                "total_chunks": 1,
                "progress": progress,
            },
            error_message=error_message,
        )

    publish("processing", 0)
    try:
        source_bytes, source_kind = _load_pipeline_source(material_id, document["filename"])
        if target_stage is None:
            _set_document_pipeline_status(document_id, "processing")

        def on_stage(stage: str, info: dict) -> None:
            nonlocal current_stage
            current_stage = stage
            publish("processing", int((info or {}).get("progress") or 0))

        run_document_pipeline(
            pdf_bytes=source_bytes,
            document_id=document_id,
            material_id=material_id,
            filename=document["filename"],
            source_kind=source_kind,
            course_id=None,
            progress_callback=on_stage,
            target_stage=target_stage,
            start_stage=start_stage,
            resume=target_stage is not None or start_stage is not None,
        )
        if target_stage is None:
            _set_document_pipeline_status(document_id, "completed")
        current_stage = target_stage or "completed"
        publish("completed", 100)
    except Exception as exc:
        stage = getattr(exc, "stage", current_stage) if isinstance(exc, PipelineStageError) else current_stage
        current_stage = stage or "failed"
        logger.exception("Material document pipeline failed: task=%s material=%s stage=%s", task_id, material_id, current_stage)
        if target_stage is None:
            _set_document_pipeline_status(document_id, "failed")
        publish("failed", 100, str(exc))


def _course_document_pipeline_worker(
    *,
    task_id: str,
    course_id: str,
    user_id: str,
    documents: list[dict],
    target_stage: str | None,
    start_stage: str | None = None,
) -> None:
    from core.course_content_builder import build_course_content
    from core.document_pipeline import PipelineStageError, run_document_pipeline

    total = len(documents)
    label_stage = start_stage or target_stage or ""
    label = DOCUMENT_PIPELINE_STAGE_LABELS.get(label_stage, "Agent Pipeline")
    generated = 0
    failed = 0
    current_document = ""
    current_stage = start_stage or target_stage or "started"

    def publish(status: str = "processing", error_message: str | None = None) -> None:
        progress = int((generated + failed) / total * 100) if total else 100
        update_background_task(
            task_id,
            status,
            result_data={
                "course_id": course_id,
                "stage": current_stage,
                "target_stage": target_stage or "",
                "start_stage": start_stage or "",
                "label": label,
                "current_document_id": current_document,
                "generated": generated,
                "failed": failed,
                "skipped": 0,
                "total_documents": total,
                "total_chunks": total,
                "progress": progress,
            },
            error_message=error_message,
        )

    publish("processing")
    try:
        for index, doc in enumerate(documents, start=1):
            current_document = doc["document_id"]
            current_stage = start_stage or target_stage or "document_pipeline"
            publish("processing")
            source_bytes, source_kind = _load_pipeline_source(doc["material_id"], doc["filename"])
            if target_stage is None:
                _set_document_pipeline_status(doc["document_id"], "processing")

            def on_stage(stage: str, info: dict) -> None:
                nonlocal current_stage
                current_stage = stage
                stage_progress = int(info.get("progress") or 0) if isinstance(info, dict) else 0
                overall = int(((index - 1) + (stage_progress / 100)) / total * 100) if total else 100
                update_background_task(
                    task_id,
                    "processing",
                    result_data={
                        "course_id": course_id,
                        "stage": stage,
                        "target_stage": target_stage or "",
                        "start_stage": start_stage or "",
                        "label": label,
                        "current_document_id": current_document,
                        "generated": generated,
                        "failed": failed,
                        "skipped": 0,
                        "total_documents": total,
                        "total_chunks": total,
                        "progress": overall,
                    },
                )

            run_document_pipeline(
                pdf_bytes=source_bytes,
                document_id=doc["document_id"],
                material_id=doc["material_id"],
                filename=doc["filename"],
                source_kind=source_kind,
                course_id=course_id,
                progress_callback=on_stage,
                target_stage=target_stage,
                start_stage=start_stage,
                resume=target_stage is not None or start_stage is not None,
            )
            if target_stage is None:
                _set_document_pipeline_status(doc["document_id"], "completed")
            generated += 1
            publish("processing")
    except Exception as exc:
        failed += 1
        stage = getattr(exc, "stage", current_stage) if isinstance(exc, PipelineStageError) else current_stage
        logger.exception("Course document pipeline failed: task=%s course=%s stage=%s", task_id, course_id, stage)
        current_stage = stage or "failed"
        if target_stage is None and current_document:
            _set_document_pipeline_status(current_document, "failed")
        publish("failed", str(exc))
        return

    if target_stage in (None, "equation_semantics", "component_assembly", "course_mapping"):
        current_stage = "course_content"
        publish("processing")
        try:
            build_course_content(user_id, course_id)
        except Exception:
            logger.warning("Course content build after pipeline failed: course=%s task=%s", course_id, task_id, exc_info=True)

    current_stage = target_stage or "completed"
    publish("completed")


@router.post("/courses/{course_id}/document-pipeline/run")
def run_course_document_pipeline(
    course_id: str,
    body: dict | None = Body(default=None),
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """コース配下の document-first Agent Pipeline を起動する。

    ``target_stage`` 指定時は、その stage だけを単独再実行してそこで終了する。
    """
    course_data = get_editable_course_data(current_user["id"], course_id)
    if not course_data and current_user.get("role") == ROLE_SYSTEM_ADMIN:
        course_data = _get_system_admin_course_data(course_id)
    if not course_data:
        raise HTTPException(status_code=404, detail="Course not found")

    target_stage = str((body or {}).get("target_stage") or "").strip() or None
    start_stage = str((body or {}).get("start_stage") or "").strip() or None
    if target_stage and target_stage not in DOCUMENT_PIPELINE_STAGE_LABELS:
        raise HTTPException(status_code=400, detail="Unknown pipeline stage")
    if start_stage and start_stage not in DOCUMENT_PIPELINE_STAGE_LABELS:
        raise HTTPException(status_code=400, detail="Unknown pipeline start stage")

    documents = _course_pipeline_documents(course_data)
    if not documents:
        raise HTTPException(status_code=400, detail="No source documents linked to this course")

    active = get_active_task_for_course(course_id)
    if active:
        raise HTTPException(status_code=409, detail="Another course task is already running")

    task_id = str(uuid.uuid4())[:12]
    create_background_task(task_id, "document_pipeline", current_user["id"])
    update_background_task(task_id, "pending", result_data={
        "course_id": course_id,
        "stage": start_stage or target_stage or "queued",
        "target_stage": target_stage or "",
        "start_stage": start_stage or "",
        "label": DOCUMENT_PIPELINE_STAGE_LABELS.get(start_stage or target_stage or "", "Agent Pipeline"),
        "generated": 0,
        "failed": 0,
        "skipped": 0,
        "total_documents": len(documents),
        "total_chunks": len(documents),
        "progress": 0,
    })

    thread = threading.Thread(
        target=_course_document_pipeline_worker,
        kwargs={
            "task_id": task_id,
            "course_id": course_id,
            "user_id": current_user["id"],
            "documents": documents,
            "target_stage": target_stage,
            "start_stage": start_stage,
        },
        daemon=True,
    )
    thread.start()

    logger.info(
        "course document pipeline accepted: task=%s course=%s docs=%d stage=%s by user=%s",
        task_id, course_id, len(documents), start_stage or target_stage or "full", current_user["id"],
    )
    return {
        "task_id": task_id,
        "course_id": course_id,
        "total_documents": len(documents),
        "target_stage": target_stage or "",
        "start_stage": start_stage or "",
        "status": "pending",
    }


@router.get("/materials/{material_id}/document-pipeline/status")
def get_material_document_pipeline_status(
    material_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """教材単位の document-first Agent Pipeline 状態を返す。"""
    document = _get_editable_material_document(material_id, current_user)
    return _material_pipeline_status(document["material_id"], document["document_id"])


@router.post("/materials/{material_id}/document-pipeline/run")
def run_material_document_pipeline(
    material_id: str,
    body: dict | None = Body(default=None),
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """教材単位の document-first Agent Pipeline を起動する。"""
    target_stage = str((body or {}).get("target_stage") or "").strip() or None
    start_stage = str((body or {}).get("start_stage") or "").strip() or None
    if target_stage and target_stage not in DOCUMENT_PIPELINE_STAGE_LABELS:
        raise HTTPException(status_code=400, detail="Unknown pipeline stage")
    if start_stage and start_stage not in DOCUMENT_PIPELINE_STAGE_LABELS:
        raise HTTPException(status_code=400, detail="Unknown pipeline start stage")

    document = _get_editable_material_document(material_id, current_user)
    active = _get_active_task_for_material(document["material_id"])
    if active:
        raise HTTPException(status_code=409, detail="Another material task is already running")

    task_id = str(uuid.uuid4())[:12]
    create_background_task(task_id, "material_document_pipeline", current_user["id"])
    update_background_task(task_id, "pending", result_data={
        "document_id": document["document_id"],
        "material_id": document["material_id"],
        "stage": start_stage or target_stage or "queued",
        "target_stage": target_stage or "",
        "start_stage": start_stage or "",
        "label": DOCUMENT_PIPELINE_STAGE_LABELS.get(start_stage or target_stage or "", "Agent Pipeline"),
        "generated": 0,
        "failed": 0,
        "skipped": 0,
        "total_documents": 1,
        "total_chunks": 1,
        "progress": 0,
    })

    thread = threading.Thread(
        target=_material_document_pipeline_worker,
        kwargs={
            "task_id": task_id,
            "user_id": current_user["id"],
            "document": document,
            "target_stage": target_stage,
            "start_stage": start_stage,
        },
        daemon=True,
    )
    thread.start()

    return {
        "task_id": task_id,
        "material_id": document["material_id"],
        "document_id": document["document_id"],
        "target_stage": target_stage or "",
        "start_stage": start_stage or "",
        "status": "pending",
    }


def _course_owner_id(course_id: str) -> str:
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("SELECT user_id::text FROM learning_courses WHERE id = :course_id LIMIT 1"),
            {"course_id": course_id},
        ).fetchone()
    finally:
        session.close()
    return str(row[0]) if row and row[0] else ""


def _course_content_generation_worker(task_id: str, course_id: str, owner_id: str) -> None:
    from core.course_content_builder import build_course_content

    update_background_task(task_id, "processing", result_data={
        "course_id": course_id,
        "stage": "course_content",
        "progress": 0,
    })
    try:
        result = build_course_content(owner_id, course_id)
        status = result.get("status") if isinstance(result, dict) else ""
        progress_status = "completed" if status == "completed" else "failed"
        update_background_task(task_id, progress_status, result_data={
            "course_id": course_id,
            "stage": "course_content",
            "progress": 100,
            "result": result,
        }, error_message="" if progress_status == "completed" else (result or {}).get("message", "コース内容生成に失敗しました"))
    except Exception as exc:
        logger.exception("Course content generation failed: task=%s course=%s", task_id, course_id)
        update_background_task(task_id, "failed", result_data={
            "course_id": course_id,
            "stage": "course_content",
            "progress": 100,
        }, error_message=str(exc))


@router.post("/courses/{course_id}/course-content/generate")
def generate_course_content(
    course_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """CourseMapping/ComponentAssembly 成果物からコース内容を再生成する。"""
    course_data = get_editable_course_data(current_user["id"], course_id)
    if not course_data and current_user.get("role") == ROLE_SYSTEM_ADMIN:
        course_data = _get_system_admin_course_data(course_id)
    if not course_data:
        raise HTTPException(status_code=404, detail="Course not found")

    active = get_active_task_for_course(course_id)
    if active:
        raise HTTPException(status_code=409, detail="Another course task is already running")

    owner_id = _course_owner_id(course_id)
    if not owner_id:
        raise HTTPException(status_code=404, detail="Course not found")

    task_id = str(uuid.uuid4())[:12]
    create_background_task(task_id, "course_content_generation", current_user["id"])
    update_background_task(task_id, "pending", result_data={
        "course_id": course_id,
        "stage": "course_content",
        "progress": 0,
    })
    thread = threading.Thread(
        target=_course_content_generation_worker,
        args=(task_id, course_id, owner_id),
        daemon=True,
    )
    thread.start()
    return {"task_id": task_id, "course_id": course_id, "status": "pending"}


# ---------------------------------------------------------------------------
# 6. コース単位のアクティブタスク照会 (Issue #139)
# ---------------------------------------------------------------------------


@router.get("/courses/{course_id}/tasks/active")
def get_course_active_task(
    course_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict | None:
    """コースに紐づく進行中タスク (pending/processing) のうち最新1件を返す。

    重複実行防止およびリロード後のポーリング再開に使用する。
    進行中タスクが無い場合は null を返す。
    """
    course_data = get_viewable_course_data(current_user["id"], course_id)
    if not course_data:
        raise HTTPException(status_code=404, detail="Course not found")

    task = get_active_task_for_course(course_id)
    if not task:
        return None
    return BackgroundTaskOut(**task).model_dump()


# ---------------------------------------------------------------------------
# 7. 原稿スタジオ — 左ペイン用 3系統データ取得 (Issue #232)
# ---------------------------------------------------------------------------


def _course_data_for_studio(course_id: str, current_user: dict) -> dict:
    """閲覧権限チェック付きでコースデータを返す。"""
    course_data = get_viewable_course_data(current_user["id"], course_id)
    if not course_data and current_user.get("role") == ROLE_SYSTEM_ADMIN:
        course_data = _get_system_admin_course_data(course_id)
    if not course_data:
        raise HTTPException(status_code=404, detail="Course not found")
    return course_data


@router.get("/courses/{course_id}/lecture-studio/course-structure")
def get_lecture_studio_course_structure(
    course_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """コース章・トピック構造を返す。

    ``learning_courses.data.chapters`` / ``data.topics`` を使い、
    各トピックのスクリプトステータスをチャンク情報から算出する。
    """
    course_data = _course_data_for_studio(course_id, current_user)

    # --- コースタイトル ---
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("SELECT title FROM learning_courses WHERE id = :cid LIMIT 1"),
            {"cid": course_id},
        ).fetchone()
    finally:
        session.close()
    course_title = (row[0] if row else None) or course_data.get("title", "")

    # --- チャンクのステータス集計 ---
    chunks = _get_course_chunks(course_data)
    total_chunks = len(chunks)
    generated_chunks = sum(1 for c in chunks if c.get("stored_spoken_text"))
    audio_chunks = 0
    if chunks:
        chunk_ids = [c["id"] for c in chunks]
        placeholders = ", ".join(f":cid_{i}" for i in range(len(chunk_ids)))
        params: dict = {f"cid_{i}": cid for i, cid in enumerate(chunk_ids)}
        session = _pg_session()
        try:
            rows = session.execute(
                sa_text(f"SELECT COUNT(*) FROM lecture_audio_cache WHERE chunk_id IN (SELECT unnest(ARRAY[{placeholders}]::uuid[]))"),
                params,
            ).fetchone()
            audio_chunks = rows[0] if rows else 0
        except Exception:
            pass
        finally:
            session.close()

    # --- コース全体ステータス ---
    if total_chunks == 0:
        course_status = "no_chunks"
    elif audio_chunks == total_chunks:
        course_status = "audio_generated"
    elif generated_chunks == total_chunks:
        course_status = "generated"
    elif generated_chunks > 0:
        course_status = "partial"
    else:
        course_status = "draft"

    # --- 章・トピック構造を構築 ---
    chapters_raw = course_data.get("chapters", [])
    topics_raw = course_data.get("topics", [])

    # topics_raw が chapter_index でグループ化されている形式に対応
    chapter_topics: dict[int, list] = {}
    for t in topics_raw:
        ci = t.get("chapter_index", 0)
        chapter_topics.setdefault(ci, []).append(t)

    chapters_out = []
    for ci, ch in enumerate(chapters_raw):
        topics_in_chapter = chapter_topics.get(ci, ch.get("topics", []))
        topics_out = []
        for ti, t in enumerate(topics_in_chapter):
            prereqs = []
            for p in t.get("prerequisites", []):
                name = p.get("name", "") if isinstance(p, dict) else str(p)
                if name:
                    prereqs.append(name)
            topics_out.append({
                "id": t.get("id", f"topic_{ci}_{ti}"),
                "topic_index": ti,
                "title": t.get("title", ""),
                "key_concepts": t.get("key_concepts", []),
                "student_material": t.get("student_material", {}),
                "spoken_script": t.get("spoken_script", ""),
                "cautions": t.get("cautions", []),
                "check_questions": t.get("check_questions", []),
                "evidence_links": t.get("evidence_links", []),
                "coverage": t.get("coverage", {}),
                "prerequisites": prereqs,
                "summary": t.get("summary", ""),
                "content": t.get("content", ""),
                "content_blocks": t.get("content_blocks", []),
                "content_source": t.get("content_source", ""),
                "content_confidence": t.get("content_confidence", ""),
                "linked_component_ids": t.get("linked_component_ids", []),
                "linked_equation_ids": t.get("linked_equation_ids", []),
                "source_evidence_ids": t.get("source_evidence_ids", []),
                "linked_chunk_ids": t.get("linked_chunk_ids", []) or t.get("material_chunk_ids", []),
                "status": "generated" if (t.get("content") or t.get("summary")) else "draft",
            })
        chapters_out.append({
            "chapter_index": ci,
            "title": ch.get("title", ""),
            "topics": topics_out,
        })

    return {
        "course_id": course_id,
        "title": course_title,
        "course_status": course_status,
        "course_content_status": course_data.get("course_content_status") or {},
        "total_chunks": total_chunks,
        "generated_chunks": generated_chunks,
        "audio_chunks": audio_chunks,
        "chapters": chapters_out,
    }


def _find_course_topic(course_data: dict, topic_id: str, chapter_index: object = None, topic_index: object = None) -> dict | None:
    topics = course_data.get("topics") or []
    for topic in topics:
        if isinstance(topic, dict) and str(topic.get("id", "")) == str(topic_id):
            return topic
    for idx, topic in enumerate(topics):
        if not isinstance(topic, dict):
            continue
        if topic.get("chapter_index") == chapter_index and topic.get("topic_index", idx) == topic_index:
            return topic
    return None


def _clean_str_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [line.strip() for line in value.splitlines() if line.strip()]
    return []


def _normalize_check_questions(value: object) -> list[dict]:
    """Normalize legacy string questions and detailed check-question objects."""
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    normalized: list[dict] = []
    for item in items:
        if isinstance(item, str):
            question = item.strip()
            if question:
                normalized.append({
                    "question": question,
                    "model_answer": "",
                    "answer_requirements": [],
                    "explanation": "",
                })
            continue
        if not isinstance(item, dict):
            continue
        question = str(item.get("question") or item.get("text") or "").strip()
        if not question:
            continue
        normalized.append({
            "question": question,
            "model_answer": str(item.get("model_answer") or item.get("answer") or "").strip(),
            "answer_requirements": _clean_str_list(
                item.get("answer_requirements")
                or item.get("required_elements")
                or item.get("requirements")
            ),
            "explanation": str(item.get("explanation") or item.get("rationale") or "").strip(),
        })
    return normalized


@router.put("/courses/{course_id}/lecture-studio/course-topics/{topic_id}")
def save_lecture_studio_course_topic(
    course_id: str,
    topic_id: str,
    body: dict,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """原稿スタジオで編集したコーストピックの授業用ドラフトを保存する。"""
    course_data = _course_data_for_studio(course_id, current_user)
    target = _find_course_topic(course_data, topic_id, body.get("chapter_index"), body.get("topic_index"))
    if target is None:
        raise HTTPException(status_code=404, detail="Topic not found")

    student_material = body.get("student_material")
    if isinstance(student_material, dict):
        source_text = str(student_material.get("source_text") or "")
        target["student_material"] = {
            "source_format": student_material.get("source_format") or "eg-markdown-v1",
            "source_text": source_text,
        }
    target["key_concepts"] = _clean_str_list(body.get("key_concepts"))
    target["spoken_script"] = str(body.get("spoken_script") or "")
    target["cautions"] = _clean_str_list(body.get("cautions"))
    target["check_questions"] = _normalize_check_questions(body.get("check_questions"))

    session = _pg_session()
    try:
        row = session.execute(
            sa_text("""
                SELECT user_id
                FROM learning_courses
                WHERE id = :course_id
                LIMIT 1
            """),
            {"course_id": course_id},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Course not found")
        if str(row[0]) != str(current_user["id"]) and current_user.get("role") != ROLE_SYSTEM_ADMIN:
            raise HTTPException(status_code=403, detail="Forbidden")
        session.execute(
            sa_text("""
                UPDATE learning_courses
                SET data = CAST(:data AS jsonb),
                    updated_at = now()
                WHERE id = :course_id
            """),
            {
                "course_id": course_id,
                "data": json.dumps(course_data, ensure_ascii=False),
            },
        )
        session.commit()
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        logger.exception("Failed to save course topic draft: course_id=%s topic_id=%s", course_id, topic_id)
        raise HTTPException(status_code=500, detail="Failed to save topic draft")
    finally:
        session.close()

    return {"course_id": course_id, "topic_id": topic_id, "status": "edited"}


_COURSE_TOPIC_DRAFT_PROMPT = """あなたは大学教員の授業用ドラフト作成を支援するアシスタントです。

目的:
- 教員が設定したコースの説明順序と想定受講者の理解を優先する
- Claim / コンポーネント / 数式 / 原文抜粋は、本文の主役ではなく根拠材料として使う
- 教材欄と本文説明を分離する

教材欄の表記:
- Markdown風の軽量表記を使う
- インライン数式は `$...$`
- ブロック数式は `$$...$$`
- `\(...\)` や `\[...\]` は使わず、必ず `$...$` / `$$...$$` を使う
- 埋め込みは `![[equation:id]]`, `![[figure:id]]`, `![[source:id]]`, `![[claim:id]]`, `![[component:id]]`

必ずJSONのみを返してください。
JSON文字列内のLaTeXバックスラッシュは必ず `\\Lambda` のように二重化してください。
形式:
{{
  "key_concepts": ["重要概念"],
  "student_material": {{"source_format": "eg-markdown-v1", "source_text": "学生に見せる教材"}},
  "spoken_script": "教員が話せる自然文。音声読み上げ対象。",
  "cautions": ["注意点"],
  "check_questions": [
    {{
      "question": "確認問題",
      "model_answer": "模範解答",
      "answer_requirements": ["回答に含めるべき要素"],
      "explanation": "難しい問題では、なぜそうなるかの解説"
    }}
  ]
}}

コーストピック:
{topic_json}

根拠候補:
{evidence_json}

現在の授業用ドラフト:
{draft_json}

教員の指示:
{instructor_prompt}
"""


class CourseTopicStudentMaterialDraft(BaseModel):
    source_format: str = "eg-markdown-v1"
    source_text: str = ""


class CourseTopicDraftLLMResponse(BaseModel):
    key_concepts: list[str] = Field(default_factory=list)
    student_material: CourseTopicStudentMaterialDraft = Field(default_factory=CourseTopicStudentMaterialDraft)
    spoken_script: str = ""
    cautions: list[str] = Field(default_factory=list)
    check_questions: list[dict] = Field(default_factory=list)


def _parse_course_topic_draft_json(raw: str) -> dict:
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
        # LLMs often emit LaTeX like \Lambda inside JSON strings. JSON only
        # allows a small set of backslash escapes, so preserve those and
        # double every other single backslash before a second parse attempt.
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


def _normalize_course_topic_draft_response(parsed: object) -> dict:
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
        "check_questions": _normalize_check_questions(parsed.get("check_questions")),
    }


@router.post("/courses/{course_id}/lecture-studio/course-topics/{topic_id}/draft/rewrite")
def rewrite_lecture_studio_course_topic(
    course_id: str,
    topic_id: str,
    body: dict,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """教員の指示に基づいてコーストピックの授業用ドラフトを生成する。"""
    course_data = _course_data_for_studio(course_id, current_user)
    topic = _find_course_topic(course_data, topic_id, body.get("chapter_index"), body.get("topic_index"))
    if topic is None:
        raise HTTPException(status_code=404, detail="Topic not found")

    draft = {
        "key_concepts": body.get("key_concepts") if body.get("key_concepts") is not None else topic.get("key_concepts", []),
        "student_material": body.get("student_material") if body.get("student_material") is not None else topic.get("student_material", {}),
        "spoken_script": body.get("spoken_script") if body.get("spoken_script") is not None else topic.get("spoken_script", ""),
        "cautions": body.get("cautions") if body.get("cautions") is not None else topic.get("cautions", []),
        "check_questions": body.get("check_questions") if body.get("check_questions") is not None else topic.get("check_questions", []),
    }
    evidence = {
        "summary": topic.get("summary", ""),
        "source_excerpt": topic.get("source_excerpt", ""),
        "linked_component_ids": topic.get("linked_component_ids", []),
        "linked_equation_ids": topic.get("linked_equation_ids", []),
        "source_evidence_ids": topic.get("source_evidence_ids", []),
        "evidence_links": topic.get("evidence_links", []),
        "content_blocks": topic.get("content_blocks", []),
        "coverage": topic.get("coverage", {}),
        "content_confidence": topic.get("content_confidence", ""),
    }
    prompt = _COURSE_TOPIC_DRAFT_PROMPT.format(
        topic_json=json.dumps({
            "id": topic.get("id", ""),
            "title": topic.get("title", ""),
            "prerequisites": topic.get("prerequisites", []),
        }, ensure_ascii=False, indent=2)[:3000],
        evidence_json=json.dumps(evidence, ensure_ascii=False, indent=2)[:8000],
        draft_json=json.dumps(draft, ensure_ascii=False, indent=2)[:6000],
        instructor_prompt=str(body.get("prompt") or "")[:2000],
    )

    params = get_llm_params("fast")
    parsed: object = {}
    with usage_context("admin:lecture_rewrite", user_id=current_user["id"], course_id=course_id):
        try:
            parsed = generate_text_with_structured_output(
                messages=[{"role": "user", "content": prompt}],
                response_format=CourseTopicDraftLLMResponse,
                model=params["model"],
            )
        except Exception as structured_exc:
            logger.warning(
                "Structured course topic draft failed; retrying text JSON parse course_id=%s topic_id=%s error=%s",
                course_id,
                topic_id,
                structured_exc,
            )
            try:
                raw = generate_text(
                    messages=[{"role": "user", "content": prompt}],
                    model=params["model"],
                    reasoning_effort=params["reasoning_effort"],
                )
                parsed = _parse_course_topic_draft_json(raw)
            except Exception:
                logger.exception("AI course topic draft retry failed for course_id=%s topic_id=%s", course_id, topic_id)
                raise HTTPException(status_code=502, detail="AI draft generation failed")
    result = _normalize_course_topic_draft_response(parsed)
    if not any([
        result["key_concepts"],
        result["student_material"]["source_text"].strip(),
        result["spoken_script"].strip(),
        result["cautions"],
        result["check_questions"],
    ]):
        logger.error("AI course topic draft returned empty result for course_id=%s topic_id=%s", course_id, topic_id)
        raise HTTPException(status_code=502, detail="AI draft response was empty")
    return result


@router.get("/courses/{course_id}/lecture-studio/document-structure")
def get_lecture_studio_document_structure(
    course_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """コースに紐づく教材の文書構造を返す。

    優先順: document_analysis_runs._artifacts.document_structure > chunks のセクション情報
    """
    course_data = _course_data_for_studio(course_id, current_user)
    sources = course_data.get("sources", [])
    material_ids = [s.get("material_id") for s in sources if s.get("material_id")]

    if not material_ids:
        return {"course_id": course_id, "documents": []}

    mid_placeholders = ", ".join(f":mid_{i}" for i in range(len(material_ids)))
    params: dict = {f"mid_{i}": mid for i, mid in enumerate(material_ids)}

    # --- 教材 → document_id マッピング + タイトル取得 ---
    session = _pg_session()
    try:
        mat_rows = session.execute(
            sa_text(f"""
                SELECT m.id AS material_id, m.title AS material_title,
                       d.id AS document_id, d.title AS doc_title
                FROM materials m
                LEFT JOIN documents d ON d.material_id = m.id
                WHERE m.id IN ({mid_placeholders})
                ORDER BY m.id
            """),
            params,
        ).fetchall()
    finally:
        session.close()

    # material_id → {document_id, title}
    mat_map: dict[str, dict] = {}
    for r in mat_rows:
        mid = str(r[0])
        if mid not in mat_map:
            mat_map[mid] = {
                "material_id": mid,
                "material_title": r[1] or "",
                "document_id": str(r[2]) if r[2] else "",
                "doc_title": r[3] or "",
            }

    documents_out = []
    for source in sources:
        mid = source.get("material_id", "")
        if not mid or mid not in mat_map:
            continue
        mat = mat_map[mid]
        doc_id = mat["document_id"]

        # --- Agent復元済み文書構造を取得 ---
        agent_structure = None
        analysis_status = "not_started"
        if doc_id:
            session = _pg_session()
            try:
                run_row = session.execute(
                    sa_text("""
                        SELECT status, stage_outputs
                        FROM document_analysis_runs
                        WHERE document_id = :doc_id
                        ORDER BY created_at DESC
                        LIMIT 1
                    """),
                    {"doc_id": doc_id},
                ).fetchone()
            finally:
                session.close()
            if run_row:
                analysis_status = run_row[0] or "not_started"
                stage_outputs = run_row[1] or {}
                if isinstance(stage_outputs, str):
                    try:
                        stage_outputs = json.loads(stage_outputs)
                    except Exception:
                        stage_outputs = {}
                artifacts = stage_outputs.get("_artifacts") or {}
                agent_structure = artifacts.get("document_structure")

        # --- チャンクベースのフォールバック構造 ---
        chunks = _get_course_chunks(course_data)
        doc_chunks = [c for c in chunks if (c.get("document_id") or c.get("material_id")) == (doc_id or mid)]
        sections: dict[str, dict] = {}
        for c in doc_chunks:
            sid = c.get("section_id") or "default"
            if sid not in sections:
                sections[sid] = {
                    "section_id": sid,
                    "title": c.get("section_title") or sid,
                    "level": c.get("section_level", 0),
                    "order": c.get("section_order", 0),
                    "chunks": [],
                }
            sections[sid]["chunks"].append({
                "chunk_id": c["id"],
                "chunk_index": c.get("chunk_index", 0),
                "text": (c.get("text") or "")[:200],
                "page_start": c.get("page_start"),
                "page_end": c.get("page_end"),
                "status": _chunk_status(c),
            })
        sections_list = sorted(sections.values(), key=lambda s: s["order"])

        documents_out.append({
            "material_id": mid,
            "material_title": source.get("title") or mat["material_title"],
            "document_id": doc_id,
            "analysis_status": analysis_status,
            "agent_structure": agent_structure,
            "sections": sections_list,
        })

    return {"course_id": course_id, "documents": documents_out}


@router.get("/courses/{course_id}/lecture-studio/components")
def get_lecture_studio_components(
    course_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """コースの理論コンポーネント一覧とグラフを返す。

    - ``theory_components`` から全コンポーネントを取得
    - ``theory_component_graphs`` からすべての依存グラフを取得
    - ``theory_claims`` から各コンポーネントに紐づくclaimを集約
    """
    course_data = _course_data_for_studio(course_id, current_user)
    sources = course_data.get("sources", [])
    source_document_ids: list[str] = []
    material_ids = [s.get("material_id", "") for s in sources if s.get("material_id")]
    if material_ids:
        mid_placeholders = ", ".join(f":mid_{i}" for i in range(len(material_ids)))
        params_mid: dict = {f"mid_{i}": mid for i, mid in enumerate(material_ids)}
        session = _pg_session()
        try:
            doc_rows = session.execute(
                sa_text(f"SELECT DISTINCT document_id FROM chunks WHERE material_id IN ({mid_placeholders})"),
                params_mid,
            ).fetchall()
            source_document_ids = [str(r[0]) for r in doc_rows if r[0]]
        finally:
            session.close()

    component_filter = "course_id = :course_id"
    component_params: dict = {"course_id": course_id}
    if source_document_ids:
        doc_placeholders = ", ".join(f":doc_{i}" for i in range(len(source_document_ids)))
        component_filter = f"({component_filter} OR document_id IN ({doc_placeholders}))"
        component_params.update({f"doc_{i}": doc_id for i, doc_id in enumerate(source_document_ids)})

    # --- theory_components ---
    session = _pg_session()
    try:
        comp_rows = session.execute(
            sa_text(f"""
                SELECT id, course_id, primary_chunk_id, name, component_type, summary, status,
                       source_chunks, inputs, outputs, preconditions, constraints,
                       invalid_conditions, dependencies, blackbox_policy, validation_warnings,
                       teacher_notes, source_scope, evidence_claims, maturity_level, maturity_source,
                       review_status, cautions, connectors, created_at, updated_at,
                       component_type_text, internal_flow, duplicate_candidates
                FROM theory_components
                WHERE {component_filter}
                ORDER BY updated_at DESC, created_at DESC
            """),
            component_params,
        ).fetchall()
    finally:
        session.close()

    def _jv(v: object, default: object) -> object:
        if v is None:
            return default
        if isinstance(v, (dict, list)):
            return v
        if isinstance(v, str) and v.strip():
            try:
                return json.loads(v)
            except Exception:
                pass
        return default

    components = []
    for row in comp_rows:
        components.append({
            "id": str(row[0]),
            "course_id": row[1],
            "primary_chunk_id": str(row[2]) if row[2] else None,
            "name": row[3] or "",
            "component_type": row[26] or row[4] or "",
            "summary": row[5] or "",
            "status": row[6] or "candidate",
            "source_chunks": _jv(row[7], []),
            "inputs": _jv(row[8], []),
            "outputs": _jv(row[9], []),
            "preconditions": _jv(row[10], []),
            "dependencies": _jv(row[13], []),
            "evidence_claims": _jv(row[18], []),
            "maturity_level": row[19] or "paper_claim",
            "review_status": row[21] or "teacher_review_required",
            "cautions": _jv(row[22], []),
        })

    # --- theory_component_graphs ---
    graph_edges: list[dict] = []
    graph_nodes: list[dict] = []
    session = _pg_session()
    try:
        graph_rows = session.execute(
            sa_text(f"""
                SELECT document_id, graph_json, validation_results
                FROM theory_component_graphs
                WHERE {component_filter}
                ORDER BY updated_at DESC
            """),
            component_params,
        ).fetchall()
    finally:
        session.close()
    graphs_by_doc: list[dict] = []
    for row in graph_rows:
        gj = _jv(row[1], {})
        graphs_by_doc.append({
            "document_id": row[0],
            "edges": _jv(gj.get("edges") if isinstance(gj, dict) else None, []),  # type: ignore[attr-defined]
            "nodes": _jv(gj.get("nodes") if isinstance(gj, dict) else None, []),  # type: ignore[attr-defined]
            "validation_results": _jv(row[2], []),
        })
        graph_edges.extend(_jv(gj.get("edges") if isinstance(gj, dict) else None, []))  # type: ignore[attr-defined]
        graph_nodes.extend(_jv(gj.get("nodes") if isinstance(gj, dict) else None, []))  # type: ignore[attr-defined]

    # --- analysis status ---
    analysis_status = "not_started"
    if source_document_ids:
        session = _pg_session()
        try:
            status_rows = session.execute(
                sa_text("""
                    SELECT status FROM document_analysis_runs
                    WHERE document_id = ANY(:doc_ids)
                    ORDER BY created_at DESC
                    LIMIT 10
                """),
                {"doc_ids": source_document_ids},
            ).fetchall()
        finally:
            session.close()
        statuses = [r[0] for r in status_rows]
        if "running" in statuses:
            analysis_status = "running"
        elif "completed" in statuses:
            analysis_status = "completed"
        elif "failed" in statuses:
            analysis_status = "failed"
        elif "pending" in statuses:
            analysis_status = "pending"

    return {
        "course_id": course_id,
        "analysis_status": analysis_status,
        "components": components,
        "graphs_by_document": graphs_by_doc,
        "all_edges": graph_edges,
        "all_nodes": graph_nodes,
    }
