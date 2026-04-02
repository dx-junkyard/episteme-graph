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
import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text as sa_text

from dependencies import _require_teacher
from schemas import (
    LectureAudioGenerateResponse,
    LectureFormulaItem,
    LectureScriptChunkOut,
    LectureScriptGenerateRequest,
    LectureScriptGenerateResponse,
    LectureScriptRewriteRequest,
    LectureScriptRewriteResponse,
    LectureScriptSaveRequest,
    LectureScriptSaveResponse,
)
from services import get_course_data
from core.lecture import generate_spoken_text_and_formulas, estimate_word_timestamps
from core.llm import generate_text, get_llm_params
from core.postgres import get_session as _pg_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["Lecture Script Studio"])


# ---------------------------------------------------------------------------
# Helper: コースに紐づくチャンクを取得
# ---------------------------------------------------------------------------


def _get_course_chunks(course_data: dict) -> list[dict]:
    """コースのソース教材からチャンクを取得する。"""
    sources = course_data.get("sources", [])
    material_ids = [s.get("material_id") for s in sources if s.get("material_id")]
    arxiv_ids = [s.get("arxiv_id") for s in sources if s.get("arxiv_id")]

    if not material_ids and not arxiv_ids:
        return []

    session = _pg_session()
    try:
        conditions = []
        params: dict = {}

        if material_ids:
            mid_placeholders = ", ".join(f":mid_{i}" for i in range(len(material_ids)))
            conditions.append(f"c.material_id IN ({mid_placeholders})")
            for i, mid in enumerate(material_ids):
                params[f"mid_{i}"] = mid

        if arxiv_ids:
            aid_placeholders = ", ".join(f":aid_{i}" for i in range(len(arxiv_ids)))
            conditions.append(f"c.arxiv_id IN ({aid_placeholders})")
            for i, aid in enumerate(arxiv_ids):
                params[f"aid_{i}"] = aid

        where_clause = " OR ".join(conditions)
        rows = session.execute(
            sa_text(f"""
                SELECT c.id, c.chunk_index, c.text, c.spoken_text, c.formulas
                FROM chunks c
                WHERE ({where_clause})
                  AND c.text IS NOT NULL AND c.text != ''
                ORDER BY c.chunk_index
            """),
            params,
        ).fetchall()

        return [
            {
                "id": str(row[0]),
                "chunk_index": row[1],
                "text": row[2],
                "spoken_text": row[3] or "",
                "formulas": row[4] if row[4] else [],
            }
            for row in rows
        ]
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


@router.post(
    "/courses/{course_id}/lecture-scripts/generate",
    response_model=LectureScriptGenerateResponse,
)
def batch_generate_scripts(
    course_id: str,
    body: LectureScriptGenerateRequest,
    current_user: dict = Depends(_require_teacher),
) -> LectureScriptGenerateResponse:
    """コースの全チャンクに対して spoken_text と formulas を一括生成する。

    既存スクリプトがある場合は override=true でのみ上書きする。
    TTS音声はこの段階では生成しない。
    """
    course_data = get_course_data(current_user["id"], course_id)
    if not course_data:
        raise HTTPException(status_code=404, detail="Course not found")

    chunks = _get_course_chunks(course_data)
    if not chunks:
        raise HTTPException(status_code=404, detail="No chunks found for this course")

    generated = 0
    skipped = 0
    result_chunks: list[LectureScriptChunkOut] = []

    session = _pg_session()
    try:
        for chunk in chunks:
            if chunk["spoken_text"] and not body.override:
                skipped += 1
                result_chunks.append(LectureScriptChunkOut(
                    chunk_id=chunk["id"],
                    chunk_index=chunk["chunk_index"],
                    text=chunk["text"],
                    spoken_text=chunk["spoken_text"],
                    formulas=[LectureFormulaItem(**f) for f in chunk["formulas"]] if chunk["formulas"] else [],
                    status=_chunk_status(chunk),
                ))
                continue

            result = generate_spoken_text_and_formulas(chunk["text"])
            spoken_text = result["spoken_text"]
            formulas = result["formulas"]

            session.execute(
                sa_text("""
                    UPDATE chunks
                    SET spoken_text = :spoken_text,
                        formulas = CAST(:formulas AS jsonb)
                    WHERE id = CAST(:id AS uuid)
                """),
                {
                    "id": chunk["id"],
                    "spoken_text": spoken_text,
                    "formulas": json.dumps(formulas, ensure_ascii=False),
                },
            )
            generated += 1
            result_chunks.append(LectureScriptChunkOut(
                chunk_id=chunk["id"],
                chunk_index=chunk["chunk_index"],
                text=chunk["text"],
                spoken_text=spoken_text,
                formulas=[LectureFormulaItem(**f) for f in formulas],
                status="generated",
            ))

        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    return LectureScriptGenerateResponse(
        course_id=course_id,
        total_chunks=len(chunks),
        generated=generated,
        skipped=skipped,
        chunks=result_chunks,
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

以下のソーステキストと現在の音声読み上げ原稿、そして教員からの指示に基づいて、
音声原稿を書き換えてください。

**重要:**
- 教員の指示に従い、必要に応じて一般的な物理学・数学の知識を補足してください
- ソーステキストに限定されず、教員が指示する内容を反映させてください
- LaTeX 数式は自然言語に変換してください（例: `$E = mc^2$` → 「Eイコールmcの二乗」）
- 自然な日本語の講義調で書いてください
- 数式メタデータも更新してください

## ソーステキスト:
{source_text}

## 現在の音声原稿:
{current_spoken_text}

## 教員からの指示:
{instructor_prompt}

## 出力形式 (厳密にJSON):
{{
  "spoken_text": "書き換えた音声原稿",
  "formulas": [
    {{"id": "formula_0", "latex": "E = mc^2", "spoken": "Eイコールmcの二乗"}}
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
            sa_text("SELECT text, spoken_text FROM chunks WHERE id = CAST(:cid AS uuid)"),
            {"cid": chunk_id},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Chunk not found")

        source_text = row[0] or ""
        current_spoken = row[1] or source_text
    finally:
        session.close()

    prompt = _REWRITE_PROMPT.format(
        source_text=source_text[:4000],
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
                SET spoken_text = :spoken_text,
                    formulas = CAST(:formulas AS jsonb)
                WHERE id = CAST(:cid AS uuid)
            """),
            {
                "cid": chunk_id,
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


@router.post(
    "/courses/{course_id}/lecture-audio/generate",
    response_model=LectureAudioGenerateResponse,
)
def batch_generate_audio(
    course_id: str,
    current_user: dict = Depends(_require_teacher),
) -> LectureAudioGenerateResponse:
    """コースの全スクリプトに対して TTS 音声を一括生成する。

    レート制限を考慮して各チャンク間に遅延を入れる。
    """
    course_data = get_course_data(current_user["id"], course_id)
    if not course_data:
        raise HTTPException(status_code=404, detail="Course not found")

    chunks = _get_course_chunks(course_data)
    if not chunks:
        raise HTTPException(status_code=404, detail="No chunks found for this course")

    try:
        from core.config import get_settings
        import openai
        settings = get_settings()
        client = openai.OpenAI(api_key=settings.llm_api_key)
    except ImportError:
        raise HTTPException(status_code=501, detail="OpenAI SDK not available for TTS")

    generated = 0
    skipped = 0
    errors = 0

    for chunk in chunks:
        spoken_text = chunk.get("spoken_text")
        if not spoken_text:
            skipped += 1
            continue

        # キャッシュ確認
        session = _pg_session()
        try:
            cached = session.execute(
                sa_text(
                    "SELECT 1 FROM lecture_audio_cache WHERE chunk_id = CAST(:cid AS uuid) LIMIT 1"
                ),
                {"cid": chunk["id"]},
            ).fetchone()
            if cached:
                skipped += 1
                continue
        finally:
            session.close()

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
            word_timestamps = estimate_word_timestamps(spoken_text, duration_ms)

            session = _pg_session()
            try:
                session.execute(
                    sa_text("""
                        INSERT INTO lecture_audio_cache
                            (chunk_id, voice, audio_data, duration_ms, word_timestamps)
                        VALUES
                            (CAST(:cid AS uuid), :voice, :audio_data, :duration_ms,
                             CAST(:wt AS jsonb))
                        ON CONFLICT (chunk_id, voice) DO UPDATE
                        SET audio_data = EXCLUDED.audio_data,
                            duration_ms = EXCLUDED.duration_ms,
                            word_timestamps = EXCLUDED.word_timestamps,
                            created_at = now()
                    """),
                    {
                        "cid": chunk["id"],
                        "voice": "alloy",
                        "audio_data": audio_bytes,
                        "duration_ms": duration_ms,
                        "wt": json.dumps(word_timestamps, ensure_ascii=False),
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

    return LectureAudioGenerateResponse(
        course_id=course_id,
        total_chunks=len(chunks),
        generated=generated,
        skipped=skipped,
        errors=errors,
    )
