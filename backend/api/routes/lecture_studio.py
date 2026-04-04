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

from dependencies import _require_teacher
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
)
from services import (
    create_background_task,
    get_course_data,
    update_background_task,
)
from core.lecture import generate_spoken_text_and_formulas, normalize_to_placeholder_format
from core.llm import generate_text, get_llm_params
from core.postgres import get_session as _pg_session

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Lecture Script Studio"])


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
                SELECT c.id, c.chunk_index, c.text, c.display_text, c.spoken_text, c.formulas
                FROM chunks c
                WHERE ({where_clause})
                  AND c.text IS NOT NULL AND c.text != ''
                ORDER BY c.chunk_index
            """),
            params,
        ).fetchall()

        chunks = []
        for row in rows:
            text = row[3] or row[2] or ""
            formulas = row[5] if row[5] else []
            # 旧フォーマット（$...$）のデータをプレースホルダー方式に正規化
            text, formulas = normalize_to_placeholder_format(text, formulas)
            chunks.append({
                "id": str(row[0]),
                "chunk_index": row[1],
                "text": text,
                "spoken_text": row[4] or "",
                "formulas": formulas,
            })
        return chunks
    finally:
        session.close()


def _chunk_status(chunk: dict) -> str:
    """チャンクのスクリプトステータスを判定する。"""
    if not chunk.get("spoken_text"):
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


# ---------------------------------------------------------------------------
# 1. バッチスクリプト生成
# ---------------------------------------------------------------------------


def _batch_generate_worker(
    task_id: str,
    course_id: str,
    chunks: list[dict],
    override: bool,
    course_data: dict,
) -> None:
    """バックグラウンドスレッドでスクリプトを一括生成する。"""
    total = len(chunks)
    generated = 0
    skipped = 0

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
                    course_data=course_data
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

    update_background_task(task_id, "completed", result_data={
        "course_id": course_id,
        "total_chunks": total,
        "generated": generated,
        "skipped": skipped,
        "progress": 100,
    })
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
    course_data = get_course_data(current_user["id"], course_id)
    if not course_data:
        raise HTTPException(status_code=404, detail="Course not found")

    chunks = _get_course_chunks(course_data)
    if not chunks:
        raise HTTPException(status_code=404, detail="No chunks found for this course")

    task_id = str(uuid.uuid4())[:12]
    create_background_task(task_id, "script_generation", current_user["id"])

    thread = threading.Thread(
        target=_batch_generate_worker,
        args=(task_id, course_id, chunks, body.override, course_data),
        daemon=True,
    )
    thread.start()

    logger.info(
        "batch_generate_scripts accepted: task=%s course=%s chunks=%d by user=%s",
        task_id, course_id, len(chunks), current_user["id"],
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
    """コースに紐づくチャンクのスクリプト一覧を取得する。"""
    course_data = get_course_data(current_user["id"], course_id)
    if not course_data:
        raise HTTPException(status_code=404, detail="Course not found")

    chunks = _get_course_chunks(course_data)
    return [
        LectureScriptChunkOut(
            chunk_id=c["id"],
            chunk_index=c["chunk_index"],
            text=c["text"],
            spoken_text=c["spoken_text"],
            formulas=[LectureFormulaItem(**f) for f in c["formulas"]] if c["formulas"] else [],
            status=_chunk_status(c),
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
                SET spoken_text = :spoken_text,
                    formulas = CAST(:formulas AS jsonb)
                WHERE id = CAST(:cid AS uuid)
            """),
            {
                "cid": chunk_id,
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
- 自然な日本語の講義調で書いてください
- 数式メタデータも更新してください

## ソーステキスト:
{source_text}

## 現在の表示テキスト:
{current_display_text}

## 現在の音声原稿:
{current_spoken_text}

## 教員からの指示:
{instructor_prompt}

## 出力形式 (厳密にJSON):
{{
  "display_text": "エネルギーは [[FORMULA_0]] で表される。",
  "spoken_text": "エネルギーは Eイコールmcの二乗 で表される。",
  "formulas": [
    {{"id": "[[FORMULA_0]]", "latex": "E = mc^2", "spoken": "Eイコールmcの二乗", "is_display": false}}
  ]
}}

重要: JSON のみを出力してください。マークダウンコードフェンスは不要です。"""


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
            sa_text("SELECT text, display_text, spoken_text FROM chunks WHERE id = CAST(:cid AS uuid)"),
            {"cid": chunk_id},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Chunk not found")

        source_text = row[0] or ""
        current_display = row[1] or source_text
        current_spoken = row[2] or source_text
    finally:
        session.close()

    prompt = _REWRITE_PROMPT.format(
        source_text=source_text[:4000],
        current_display_text=current_display[:4000],
        current_spoken_text=current_spoken[:4000],
        instructor_prompt=body.prompt[:2000],
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
        display_text = result.get("display_text") or current_display
        spoken_text = result.get("spoken_text", current_spoken)
        formulas = result.get("formulas", [])
    except Exception:
        logger.exception("AI rewrite failed for chunk %s", chunk_id)
        raise HTTPException(status_code=500, detail="AI rewrite failed")

    # DB に保存
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
        spoken_text=spoken_text,
        formulas=[LectureFormulaItem(**f) for f in formulas],
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

    try:
        from core.config import get_settings
        import openai
        settings = get_settings()
        client = openai.OpenAI(api_key=settings.llm_api_key)
    except Exception as exc:
        update_background_task(task_id, "failed", error_message=str(exc))
        return

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
                # TTS 生成
                try:
                    response = client.audio.speech.create(
                        model="tts-1",
                        voice="alloy",
                        input=spoken_text[:4096],
                        response_format="mp3",
                    )
                    audio_bytes = response.content
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
    course_data = get_course_data(current_user["id"], course_id)
    if not course_data:
        raise HTTPException(status_code=404, detail="Course not found")

    chunks = _get_course_chunks(course_data)
    if not chunks:
        raise HTTPException(status_code=404, detail="No chunks found for this course")

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
