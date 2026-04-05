"""Episteme Graph — インタラクティブ・レクチャーモード (/api/learning/lecture)。

Issue #66: 段階的音声解説と対話の統合
- レクチャーシーケンス取得 (トポロジカルソート)
- TTS 音声生成
- レクチャー中断チャット (コンテキスト保持)
"""

from __future__ import annotations

import base64
import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text as sa_text

from dependencies import _get_current_user
from schemas import (
    LectureFormulaItem,
    LectureInterruptRequest,
    LectureInterruptResponse,
    LectureSegment,
    LectureSequenceResponse,
    LectureTTSRequest,
    LectureTTSResponse,
)
from services import (
    detect_and_record_misconception,
    get_course_data,
    persist_chat_history,
    search_chunks_with_metadata,
)
from core.lecture import (
    build_lecture_sequence,
    generate_spoken_text_and_formulas,
    get_user_mastered_concepts,
    normalize_to_placeholder_format,
)
from core.llm import generate_text, get_llm_params
from core.postgres import get_session as _pg_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/learning/lecture", tags=["Lecture"])


# ---------------------------------------------------------------------------
# 1. レクチャーシーケンス取得
# ---------------------------------------------------------------------------


@router.get(
    "/courses/{course_id}/topics/{topic_id}/sequence",
    response_model=LectureSequenceResponse,
)
def get_lecture_sequence(
    course_id: str,
    topic_id: str,
    current_user: dict = Depends(_get_current_user),
) -> LectureSequenceResponse:
    """トピックに紐づくチャンクをトポロジカルソートしてレクチャーシーケンスを返す。

    チャンクに spoken_text がない場合はオンデマンドで生成・保存する。
    """
    course_data = get_course_data(current_user["id"], course_id)
    if not course_data:
        raise HTTPException(status_code=404, detail="Course not found")

    # トピック情報を取得
    topic_info = None
    for t in course_data.get("topics", []):
        if t.get("id") == topic_id:
            topic_info = t
            break
    if not topic_info:
        raise HTTPException(status_code=404, detail="Topic not found")

    # コースのソース教材 (material_id) を収集
    sources = course_data.get("sources", [])
    material_ids = [s.get("material_id") for s in sources if s.get("material_id")]

    if not material_ids:
        # ソースが指定されていない場合はシステム全域から検索
        topic_title = topic_info.get("title", topic_id)
        return _generate_sequence_from_search(
            course_id, topic_id, topic_title, course_data, current_user,
        )

    # DB からチャンクを取得
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
                       c.chapter, c.section
                FROM chunks c
                WHERE ({where_clause})
                  AND c.text IS NOT NULL AND c.text != ''
                ORDER BY c.chunk_index
            """),
            params,
        ).fetchall()
    finally:
        session.close()

    if not rows:
        return _generate_sequence_from_search(
            course_id, topic_id, topic_info.get("title", topic_id),
            course_data, current_user,
        )

    # チャンクデータを構築し、spoken_text がなければ生成
    chunks = []
    chunks_to_update = []
    for row in rows:
        chunk_id = str(row[0])
        chunk_index = row[1]
        text = row[2]
        display_text = row[3] or text
        spoken_text = row[4]
        formulas = row[5] if row[5] else []

        if not spoken_text:
            result = generate_spoken_text_and_formulas(text)
            display_text = result.get("display_text") or text
            spoken_text = result["spoken_text"]
            formulas = result["formulas"]
            chunks_to_update.append({
                "id": chunk_id,
                "display_text": display_text,
                "spoken_text": spoken_text,
                "formulas": formulas,
            })

        # 旧フォーマット（$...$）のデータをプレースホルダー方式に正規化
        display_text, formulas = normalize_to_placeholder_format(display_text, formulas)

        # 音声キャッシュの有無を確認
        has_audio = _check_audio_cache(chunk_id)

        chunks.append({
            "id": chunk_id,
            "chunk_index": chunk_index,
            "text": display_text,
            "spoken_text": spoken_text,
            "formulas": formulas,
            "has_audio": has_audio,
            "duration_ms": 0,
        })

    # spoken_text を DB に永続化
    if chunks_to_update:
        _persist_spoken_text(chunks_to_update)

    # 受講者の習得済み概念を取得し、適応的シーケンスを構築
    mastered_concepts = get_user_mastered_concepts(
        current_user["id"], course_id, course_data,
    )
    segments = build_lecture_sequence(topic_id, course_data, chunks, mastered_concepts)

    lecture_segments = [
        LectureSegment(
            chunk_id=s["chunk_id"],
            chunk_index=s["chunk_index"],
            text=s["text"],
            spoken_text=s["spoken_text"],
            formulas=[LectureFormulaItem(**f) for f in s["formulas"]],
            has_audio=s["has_audio"],
            duration_ms=s["duration_ms"],
            segment_mode=s.get("segment_mode", "full"),
        )
        for s in segments
    ]

    total_duration = sum(s.duration_ms for s in lecture_segments)
    summary_count = sum(1 for s in lecture_segments if s.segment_mode == "summary")
    # skipped segments were already removed by build_lecture_sequence;
    # compute how many were dropped
    skipped_count = len(chunks) - len(segments)

    return LectureSequenceResponse(
        course_id=course_id,
        topic_id=topic_id,
        segments=lecture_segments,
        total_segments=len(lecture_segments),
        total_duration_ms=total_duration,
        skipped_segments=skipped_count,
        summary_segments=summary_count,
    )


def _generate_sequence_from_search(
    course_id: str,
    topic_id: str,
    topic_title: str,
    course_data: dict,
    current_user: dict,
) -> LectureSequenceResponse:
    """ソース教材が直接指定されていない場合、ベクトル検索でチャンクを取得してシーケンス構築。"""
    chunk_results = search_chunks_with_metadata(topic_title, top_k=10)
    if not chunk_results:
        return LectureSequenceResponse(
            course_id=course_id,
            topic_id=topic_id,
            segments=[],
            total_segments=0,
            total_duration_ms=0,
        )

    segments = []
    chunks_to_update = []
    for i, cr in enumerate(chunk_results):
        if cr["score"] < 0.3:
            continue
        chunk_id = cr.get("id", f"search_{i}")
        result = generate_spoken_text_and_formulas(cr["text"])
        display_text = result.get("display_text") or cr["text"]
        is_valid_uuid = _is_valid_uuid(chunk_id)

        if is_valid_uuid:
            chunks_to_update.append({
                "id": chunk_id,
                "display_text": display_text,
                "spoken_text": result["spoken_text"],
                "formulas": result["formulas"],
            })

        segments.append(LectureSegment(
            chunk_id=chunk_id,
            chunk_index=i,
            text=display_text,
            spoken_text=result["spoken_text"],
            formulas=[LectureFormulaItem(**f) for f in result["formulas"]],
            has_audio=_check_audio_cache(chunk_id) if is_valid_uuid else False,
            duration_ms=0,
        ))

    if chunks_to_update:
        _persist_spoken_text(chunks_to_update)

    return LectureSequenceResponse(
        course_id=course_id,
        topic_id=topic_id,
        segments=segments,
        total_segments=len(segments),
        total_duration_ms=0,
    )


# ---------------------------------------------------------------------------
# 2. TTS 音声生成
# ---------------------------------------------------------------------------


@router.post(
    "/courses/{course_id}/topics/{topic_id}/tts",
    response_model=LectureTTSResponse,
)
def generate_tts(
    course_id: str,
    topic_id: str,
    body: LectureTTSRequest,
    current_user: dict = Depends(_get_current_user),
) -> LectureTTSResponse:
    """チャンクの spoken_text から TTS 音声を生成する。キャッシュがあればそれを返す。"""
    chunk_id = body.chunk_id

    # キャッシュ確認
    cached = _get_audio_cache(chunk_id, body.voice)
    if cached:
        return LectureTTSResponse(
            chunk_id=chunk_id,
            audio_base64=cached["audio_base64"],
            duration_ms=cached["duration_ms"],
            word_timestamps=cached["word_timestamps"],
        )

    # spoken_text を取得
    spoken_text = _get_chunk_spoken_text(chunk_id)
    if not spoken_text:
        raise HTTPException(status_code=404, detail="Chunk not found or has no spoken text")

    # OpenAI TTS API で音声生成
    try:
        from core.config import get_settings
        import openai

        settings = get_settings()
        client = openai.OpenAI(api_key=settings.llm_api_key)

        response = client.audio.speech.create(
            model="tts-1",
            voice=body.voice,
            input=spoken_text[:4096],
            response_format="mp3",
        )

        audio_bytes = response.content
        audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")

        # 簡易的な再生時間推定（MP3: ~128kbps → bytes / 16000 * 1000 ms）
        estimated_duration_ms = max(1000, len(audio_bytes) * 8 // 128)

        # キャッシュに保存
        _save_audio_cache(
            chunk_id, body.voice, audio_bytes,
            estimated_duration_ms, [],
        )

        return LectureTTSResponse(
            chunk_id=chunk_id,
            audio_base64=audio_b64,
            duration_ms=estimated_duration_ms,
            word_timestamps=[],
        )

    except ImportError:
        raise HTTPException(status_code=501, detail="OpenAI SDK not available for TTS")
    except Exception as exc:
        logger.exception("TTS generation failed for chunk %s", chunk_id)
        raise HTTPException(status_code=500, detail=f"TTS generation failed: {exc}") from exc


# ---------------------------------------------------------------------------
# 3. レクチャー中断チャット
# ---------------------------------------------------------------------------


_LECTURE_INTERRUPT_SYSTEM_PROMPT = """あなたは学術講義中に学生からの質問に回答するAIチューターです。

現在、音声による段階的な講義の途中で学生が一時停止して質問しています。

**重要なコンテキスト:**
- 学生は講義を聞きながら理解に疑問を持った点について質問しています
- 回答は簡潔で的を射たものにしてください（長すぎると講義の流れが失われます）
- 数式は LaTeX 記法で記述してください（$...$, $$...$$）
- 回答後に「講義を再開できます」と伝えてください
- 誤解がある場合は「訂正：」と明記してください

**回答のフォーマット:**
1. 質問に対する直接的な回答（2-3文）
2. 必要に応じて数式や具体例
3. 「講義を再開する場合は再生ボタンを押してください。」で締めくくる"""


@router.post(
    "/courses/{course_id}/topics/{topic_id}/interrupt",
    response_model=LectureInterruptResponse,
)
def lecture_interrupt_chat(
    course_id: str,
    topic_id: str,
    body: LectureInterruptRequest,
    current_user: dict = Depends(_get_current_user),
) -> LectureInterruptResponse:
    """レクチャー中の一時停止時に質問を受け付け、コンテキストを維持して回答する。"""
    course_data = get_course_data(current_user["id"], course_id)
    if not course_data:
        raise HTTPException(status_code=404, detail="Course not found")

    course_title = course_data.get("title", course_id)
    topic_info = None
    for t in course_data.get("topics", []):
        if t.get("id") == topic_id:
            topic_info = t
            break
    topic_title = topic_info["title"] if topic_info else topic_id

    # 現在再生中のチャンクのテキストを取得してコンテキストに含める
    chunk_context = _get_chunk_text(body.current_chunk_id)

    messages: list[dict] = [
        {"role": "system", "content": _LECTURE_INTERRUPT_SYSTEM_PROMPT},
        {"role": "user", "content": (
            f"コース: {course_title}\n"
            f"現在のトピック: {topic_title}\n\n"
            f"## 現在講義中の内容:\n{chunk_context}\n\n"
            "上記の講義内容を踏まえて質問に回答してください。"
        )},
        {"role": "assistant", "content": (
            f"はい、「{topic_title}」の講義中ですね。ご質問をどうぞ。"
        )},
    ]

    for turn in body.history:
        messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": body.message})

    try:
        answer = generate_text(messages=messages, temperature=0.3)
    except Exception as exc:
        logger.exception("Lecture interrupt chat failed for topic %s", topic_id)
        raise HTTPException(status_code=500, detail=f"Chat failed: {exc}") from exc

    # 誤解検出
    course_update = None
    if topic_info and any(kw in answer for kw in ["訂正：", "訂正:", "【訂正】"]):
        course_update = detect_and_record_misconception(
            current_user["id"], course_id, course_data, topic_id,
            body.message, answer,
        )

    # チャット履歴を永続化
    persist_chat_history(
        current_user["id"], course_id, topic_id,
        body.history, body.message, answer,
    )

    return LectureInterruptResponse(
        answer=answer,
        resume_chunk_id=body.current_chunk_id,
        resume_position_ms=body.pause_position_ms,
        course_update=course_update,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _is_valid_uuid(value: str) -> bool:
    """文字列が有効な UUID 形式かどうかを判定する。"""
    try:
        uuid.UUID(value)
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def _check_audio_cache(chunk_id: str) -> bool:
    """指定チャンクの音声キャッシュが存在するか確認する。"""
    if not _is_valid_uuid(chunk_id):
        return False
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("SELECT 1 FROM lecture_audio_cache WHERE chunk_id = CAST(:cid AS uuid) LIMIT 1"),
            {"cid": chunk_id},
        ).fetchone()
        return row is not None
    except Exception:
        return False
    finally:
        session.close()


def _get_audio_cache(chunk_id: str, voice: str) -> dict | None:
    """音声キャッシュを取得する。"""
    if not _is_valid_uuid(chunk_id):
        return None
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("""
                SELECT audio_data, duration_ms, word_timestamps
                FROM lecture_audio_cache
                WHERE chunk_id = CAST(:cid AS uuid) AND voice = :voice
                LIMIT 1
            """),
            {"cid": chunk_id, "voice": voice},
        ).fetchone()
        if not row:
            return None

        audio_bytes = row[0]
        if isinstance(audio_bytes, memoryview):
            audio_bytes = bytes(audio_bytes)
        return {
            "audio_base64": base64.b64encode(audio_bytes).decode("utf-8"),
            "duration_ms": row[1],
            "word_timestamps": row[2] if row[2] else [],
        }
    except Exception:
        logger.warning("Failed to get audio cache for chunk %s", chunk_id, exc_info=True)
        return None
    finally:
        session.close()


def _save_audio_cache(
    chunk_id: str,
    voice: str,
    audio_bytes: bytes,
    duration_ms: int,
    word_timestamps: list[dict],
) -> None:
    """音声キャッシュを保存する。"""
    if not _is_valid_uuid(chunk_id):
        logger.debug("Skipping audio cache save for non-UUID chunk_id: %s", chunk_id)
        return
    session = _pg_session()
    try:
        session.execute(
            sa_text("""
                INSERT INTO lecture_audio_cache (chunk_id, voice, audio_data, duration_ms, word_timestamps)
                VALUES (CAST(:cid AS uuid), :voice, :audio_data, :duration_ms, CAST(:wt AS jsonb))
                ON CONFLICT (chunk_id, voice) DO UPDATE
                SET audio_data = EXCLUDED.audio_data,
                    duration_ms = EXCLUDED.duration_ms,
                    word_timestamps = EXCLUDED.word_timestamps,
                    created_at = now()
            """),
            {
                "cid": chunk_id,
                "voice": voice,
                "audio_data": audio_bytes,
                "duration_ms": duration_ms,
                "wt": json.dumps(word_timestamps, ensure_ascii=False),
            },
        )
        session.commit()
    except Exception:
        session.rollback()
        logger.warning("Failed to save audio cache for chunk %s", chunk_id, exc_info=True)
    finally:
        session.close()


def _get_chunk_spoken_text(chunk_id: str) -> str | None:
    """チャンクの spoken_text を取得する。なければ text から生成。"""
    if not _is_valid_uuid(chunk_id):
        return None
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("SELECT spoken_text, text, display_text FROM chunks WHERE id = CAST(:cid AS uuid) LIMIT 1"),
            {"cid": chunk_id},
        ).fetchone()
        if not row:
            return None
        spoken = row[0]
        if spoken:
            return spoken
        # spoken_text がなければ生成
        text = row[1]
        if not text:
            return None
        result = generate_spoken_text_and_formulas(text)
        spoken = result["spoken_text"]
        # 永続化
        _persist_spoken_text([{
            "id": chunk_id,
            "display_text": result.get("display_text") or text,
            "spoken_text": spoken,
            "formulas": result["formulas"],
        }])
        return spoken
    finally:
        session.close()


def _get_chunk_text(chunk_id: str) -> str:
    """チャンクのテキストを取得する。"""
    if not _is_valid_uuid(chunk_id):
        return ""
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("SELECT text FROM chunks WHERE id = CAST(:cid AS uuid) LIMIT 1"),
            {"cid": chunk_id},
        ).fetchone()
        return row[0] if row and row[0] else ""
    except Exception:
        return ""
    finally:
        session.close()


def _persist_spoken_text(chunks: list[dict]) -> None:
    """チャンクの display_text / spoken_text / formulas を DB に永続化する。"""
    valid_chunks = [c for c in chunks if _is_valid_uuid(c["id"])]
    if not valid_chunks:
        return
    session = _pg_session()
    try:
        for chunk in valid_chunks:
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
                    "display_text": chunk.get("display_text", ""),
                    "spoken_text": chunk["spoken_text"],
                    "formulas": json.dumps(chunk["formulas"], ensure_ascii=False),
                },
            )
        session.commit()
    except Exception:
        session.rollback()
        logger.warning("Failed to persist spoken_text", exc_info=True)
    finally:
        session.close()
