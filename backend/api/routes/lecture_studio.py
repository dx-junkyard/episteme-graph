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
import threading
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text as sa_text

from dependencies import ROLE_SYSTEM_ADMIN, _require_teacher
from schemas import (
    LectureAudioGenerateResponse,
    LectureAudioGenerateStartResponse,
    LectureFormulaItem,
    LectureScriptChunkOut,
    LectureScriptGenerateRequest,
    LectureScriptGenerateStartResponse,
    LectureScriptRewriteRequest,
    LectureScriptRewriteResponse,
    LectureScriptSaveRequest,
    LectureScriptSaveResponse,
    LectureStudioSettings,
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
from core.lecture import generate_spoken_text_and_formulas, normalize_to_placeholder_format
from core.llm import generate_text, get_llm_params
from core.personas import course_persona_settings, normalize_persona_id, persona_prompt
from core.postgres import get_session as _pg_session
from core.tts import TtsFatalError, generate_tts_audio

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Lecture Script Studio"])


def _save_lecture_studio_settings(course_id: str, course_data: dict, settings: dict[str, str]) -> None:
    updated = dict(course_data)
    previous = updated.get("lecture_studio_settings") or {}
    if not isinstance(previous, dict):
        previous = {}
    normalized = {
        "narration_persona": normalize_persona_id(settings.get("narration_persona")),
        "response_persona": normalize_persona_id(settings.get("response_persona")),
    }
    settings_changed = (
        normalize_persona_id(previous.get("narration_persona")) != normalized["narration_persona"]
        or normalize_persona_id(previous.get("response_persona")) != normalized["response_persona"]
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
                       c.smiles_dsl, c.variables, c.ancestors, c.neo4j_node_id,
                       d.knowledge_graph, d.neo4j_node_id
                FROM chunks c
                LEFT JOIN documents d ON c.document_id = d.id
                WHERE ({where_clause})
                  AND c.text IS NOT NULL AND c.text != ''
                ORDER BY c.chunk_index
            """),
            params,
        ).fetchall()

        chunks = []
        for row in rows:
            raw_text = row[2] or ""
            display_text = row[3] or raw_text
            spoken_text = row[4] or display_text
            formulas = row[5] if row[5] else []
            # 旧フォーマット（$...$）のデータをプレースホルダー方式に正規化
            display_text, formulas = normalize_to_placeholder_format(display_text, formulas)
            knowledge_graph = _json_obj(row[14])
            graph_elements = _derive_chunk_graph_elements(
                f"{raw_text}\n{display_text}",
                knowledge_graph,
                formulas,
            )
            material_id = row[6] or ""
            variables = row[11] if row[11] is not None else _extract_document_variables(knowledge_graph)
            ancestors = row[12] if isinstance(row[12], list) else _extract_document_edges(knowledge_graph)
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
                "document_id": str(row[7]) if row[7] else "",
                "page_start": row[8],
                "page_end": row[9],
                "pdf_url": f"/admin/materials/{material_id}/pdf" if material_id else None,
                "smiles_dsl": row[10] or _extract_document_dsl(knowledge_graph),
                "variables": variables,
                "ancestors": ancestors,
                "neo4j_node_id": row[13] or row[15] or "",
                "graph_elements": graph_elements,
            })
        return chunks
    finally:
        session.close()


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


def _extract_document_dsl(knowledge_graph: dict) -> str:
    abstract = knowledge_graph.get("abstract_structure")
    if isinstance(abstract, dict):
        dsl = str(abstract.get("smiles_dsl") or "").strip()
        if dsl:
            return dsl
    return str(knowledge_graph.get("smiles_dsl") or "").strip()


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
    return LectureStudioSettings(**course_persona_settings(course_data))


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
) -> None:
    """バックグラウンドスレッドでスクリプトを一括生成する。

    auto_audio=True の場合、完了後に音声生成タスクを自動的にキックし、
    結果データの ``next_task_id`` に新タスクIDを格納する (Issue #139)。
    """
    total = len(chunks)
    generated = 0
    skipped = 0
    settings = course_persona_settings(course_data)
    narration_persona = settings["narration_persona"]

    update_background_task(task_id, "processing", result_data={
        "course_id": course_id,
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
                )
                display_text = result.get("display_text") or chunk["text"]
                spoken_text = result["spoken_text"]
                formulas = result["formulas"]

                session.execute(
                    sa_text("""
                        UPDATE chunks
                        SET display_text = :display_text,
                            spoken_text = :spoken_text,
                            formulas = CAST(:formulas AS jsonb)
                        WHERE id = CAST(:id AS uuid)
                    """),
                    {
                        "id": chunk["id"],
                        "display_text": display_text,
                        "spoken_text": spoken_text,
                        "formulas": json.dumps(formulas, ensure_ascii=False),
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
                args=(audio_task_id, course_id, fresh_chunks),
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
    return [
        LectureScriptChunkOut(
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
            pdf_url=c.get("pdf_url"),
            smiles_dsl=c.get("smiles_dsl", ""),
            variables=c.get("variables"),
            ancestors=c.get("ancestors"),
            neo4j_node_id=c.get("neo4j_node_id", ""),
            graph_elements=c.get("graph_elements", []),
        )
        for c in chunks
    ]


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
        result = json.loads(cleaned, strict=False)
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
) -> None:
    """バックグラウンドスレッドで TTS 音声を一括生成する。"""
    total = len(chunks)
    generated = 0
    skipped = 0
    errors = 0

    update_background_task(task_id, "processing", result_data={
        "course_id": course_id,
        "total_chunks": total,
        "generated": 0,
        "skipped": 0,
        "errors": 0,
        "progress": 0,
    })

    for chunk in chunks:
        spoken_text = chunk.get("spoken_text")
        if not spoken_text:
            skipped += 1
        else:
            # キャッシュ確認
            session = _pg_session()
            try:
                cached = session.execute(
                    sa_text(
                        "SELECT 1 FROM lecture_audio_cache WHERE chunk_id = CAST(:cid AS uuid) LIMIT 1"
                    ),
                    {"cid": chunk["id"]},
                ).fetchone()
            finally:
                session.close()

            if cached:
                skipped += 1
            else:
                # TTS 生成（プロバイダは generate_tts_audio が自動選択）
                try:
                    audio_bytes = generate_tts_audio(spoken_text)
                    if audio_bytes is None:
                        errors += 1
                        logger.warning(
                            "TTS audio generation returned None for chunk %s (no provider available)",
                            chunk["id"],
                        )
                        # 進捗を更新して次のチャンクへ
                        processed = generated + skipped + errors
                        update_background_task(task_id, "processing", result_data={
                            "course_id": course_id,
                            "total_chunks": total,
                            "generated": generated,
                            "skipped": skipped,
                            "errors": errors,
                            "progress": int(processed * 100 / total) if total > 0 else 100,
                        })
                        continue

                    duration_ms = max(1000, len(audio_bytes) * 8 // 128)

                    session = _pg_session()
                    try:
                        session.execute(
                            sa_text("""
                                INSERT INTO lecture_audio_cache
                                    (chunk_id, voice, audio_data, duration_ms )
                                VALUES
                                    (CAST(:cid AS uuid), :voice, :audio_data, :duration_ms)
                                ON CONFLICT (chunk_id, voice) DO UPDATE
                                SET audio_data = EXCLUDED.audio_data,
                                    duration_ms = EXCLUDED.duration_ms,
                                    created_at = now()
                            """),
                            {
                                "cid": chunk["id"],
                                "voice": "alloy",
                                "audio_data": audio_bytes,
                                "duration_ms": duration_ms,
                            },
                        )
                        session.commit()
                        generated += 1
                    except Exception:
                        session.rollback()
                        errors += 1
                        logger.warning("Failed to cache audio for chunk %s", chunk["id"], exc_info=True)
                    finally:
                        session.close()

                    # レート制限対策: チャンク間に 0.5 秒の遅延
                    time.sleep(0.5)
                except TtsFatalError as exc:
                    # API 未有効化・認証エラーなど恒久的な失敗: 残りチャンクを処理しても無駄なので即終了
                    error_msg = str(exc)
                    logger.error("TTS fatal error, aborting task %s: %s", task_id, error_msg)
                    update_background_task(task_id, "failed", error_message=error_msg)
                    return

                except Exception:
                    errors += 1
                    logger.warning("TTS generation failed for chunk %s", chunk["id"], exc_info=True)

        # チャンクごとに進捗を更新
        processed = generated + skipped + errors
        update_background_task(task_id, "processing", result_data={
            "course_id": course_id,
            "total_chunks": total,
            "generated": generated,
            "skipped": skipped,
            "errors": errors,
            "progress": int(processed * 100 / total) if total > 0 else 100,
        })

    update_background_task(task_id, "completed", result_data={
        "course_id": course_id,
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


@router.post(
    "/courses/{course_id}/lecture-audio/generate",
    response_model=LectureAudioGenerateStartResponse,
    status_code=202,
)
def batch_generate_audio(
    course_id: str,
    current_user: dict = Depends(_require_teacher),
) -> LectureAudioGenerateStartResponse:
    """コースの全スクリプトに対して TTS 音声を一括生成する（非同期）。

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
    create_background_task(task_id, "audio_generation", current_user["id"])

    thread = threading.Thread(
        target=_batch_audio_worker,
        args=(task_id, course_id, chunks),
        daemon=True,
    )
    thread.start()

    logger.info(
        "batch_generate_audio accepted: task=%s course=%s chunks=%d by user=%s",
        task_id, course_id, len(chunks), current_user["id"],
    )

    return LectureAudioGenerateStartResponse(
        task_id=task_id,
        course_id=course_id,
        total_chunks=len(chunks),
        status="pending",
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
