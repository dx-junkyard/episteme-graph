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
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text as sa_text

from dependencies import _get_current_user
from schemas import (
    LectureFigureItem,
    LectureFormulaItem,
    LectureInterruptRequest,
    LectureInterruptResponse,
    LectureSegment,
    LectureSequenceResponse,
    LectureSlide,
    LectureTTSRequest,
    LectureTTSResponse,
)
from services import (
    detect_and_record_misconception,
    get_course_data,
    list_course_source_document_ids,
    list_visible_document_ids,
    persist_chat_history,
    search_chunks_with_metadata,
)
from core.course_data import (
    course_source_material_ids,
    course_title as _course_title,
    find_course_topic,
)
from core.lecture import (
    build_lecture_sequence,
    build_topic_slides,
    compute_material_audio_readiness,
    compute_topic_audio_readiness,
    generate_spoken_text_and_formulas,
    get_course_lecture_language,
    get_user_mastered_concepts,
    lecture_uses_topic_material,
    normalize_to_placeholder_format,
    split_slides,
)
from core.learning_support_agent import extract_inline_actions
from core.llm import generate_text, get_llm_params
# 教材図スタジオ（teaching_figure_studio_design.md §7.1）: 採用済み生成図を既存の
# figures_by_id マップへ合流させる（記法・解決・配信は既存資産に相乗り・FG9）。
from core.teaching_figures.store import adopted_figures_map
from core.personas import course_persona_settings, persona_prompt
from core.postgres import get_session as _pg_session
from core import element_explanations

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
    topic_info = find_course_topic(course_data, topic_id)
    if not topic_info:
        raise HTTPException(status_code=404, detail="Topic not found")

    # レクチャーの表示ソースは、受講画面の非レクチャー教材表示（get_topic_material）と
    # 同じ優先順位に揃える: トピックが授業用の教材本文（student_material）／読み上げ原稿
    # （spoken_script）を持つなら、それを1トピック分のレクチャー教材として使う（表示＝
    # 非レクチャー時と一致させる）。実チャンク教材しか無いトピックだけがチャンク経路
    # （下記・PDF由来チャンク）へフォールバックする。音声はスライド単位で
    # topic_lecture_audio_cache から解決する（_build_topic_draft_segment 内）。
    # 述語・スライド分割の正本は core/lecture.py（lecture_uses_topic_material /
    # build_topic_slides）。
    topic_segment = None
    if lecture_uses_topic_material(topic_info):
        topic_segment = _build_topic_draft_segment(course_id, topic_id, topic_info, course_data)
    if topic_segment:
        return LectureSequenceResponse(
            course_id=course_id,
            topic_id=topic_id,
            segments=[topic_segment],
            total_segments=1,
            total_duration_ms=0,
            total_slides=len(topic_segment.slides),
        )

    # コースのソース教材 (material_id) を収集
    material_ids = course_source_material_ids(course_data)

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
                       c.chapter, c.section, c.spoken_language
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
    narration_persona = course_persona_settings(course_data)["narration_persona"]
    lecture_language = get_course_lecture_language(course_data)
    chunks = []
    chunks_to_update = []
    language_by_chunk_id: dict[str, str] = {}
    for row in rows:
        chunk_id = str(row[0])
        chunk_index = row[1]
        text = row[2]
        display_text = row[3] or text
        spoken_text = row[4]
        formulas = row[5] if row[5] else []
        # spoken_language は migration 040 で追加された列。古いモック行 (len<=8) は
        # 未指定として扱う（後方互換）。NULL は既存データとみなし "ja" とする。
        spoken_language = row[8] if len(row) > 8 else None
        language_by_chunk_id[chunk_id] = spoken_language or "ja"

        if not spoken_text:
            result = generate_spoken_text_and_formulas(
                text,
                chunk_index=chunk_index,
                course_data=course_data,
                persona_id=narration_persona,
                language=lecture_language,
            )
            display_text = result.get("display_text") or text
            spoken_text = result["spoken_text"]
            formulas = result["formulas"]
            chunks_to_update.append({
                "id": chunk_id,
                "display_text": display_text,
                "spoken_text": spoken_text,
                "formulas": formulas,
                "spoken_language": lecture_language,
            })
            language_by_chunk_id[chunk_id] = lecture_language

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

    # スライド単位の音声キャッシュ有無を一括取得 (chunk_id, slide_index) -> duration_ms
    slide_audio_map = _get_slide_audio_map([s["chunk_id"] for s in segments])

    lecture_segments = []
    total_slides = 0
    for s in segments:
        segment_mode = s.get("segment_mode", "full")
        slides = _build_slides_for_segment(
            s["text"], s["spoken_text"], s["formulas"], segment_mode,
            s["chunk_id"], slide_audio_map,
        )
        total_slides += len(slides)
        lecture_segments.append(LectureSegment(
            chunk_id=s["chunk_id"],
            chunk_index=s["chunk_index"],
            text=s["text"],
            spoken_text=s["spoken_text"],
            formulas=[LectureFormulaItem(**f) for f in s["formulas"]],
            has_audio=s["has_audio"],
            duration_ms=s["duration_ms"],
            segment_mode=segment_mode,
            slides=slides,
            language=language_by_chunk_id.get(s["chunk_id"], "ja"),
        ))

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
        total_slides=total_slides,
    )


def _generate_sequence_from_search(
    course_id: str,
    topic_id: str,
    topic_title: str,
    course_data: dict,
    current_user: dict,
) -> LectureSequenceResponse:
    """ソース教材が直接指定されていない場合、ベクトル検索でチャンクを取得してシーケンス構築。"""
    # Phase 0（discuss モード設計書 §6.1）: 全域検索は本人が閲覧可能な document に fail-closed で絞る。
    allowed_document_ids = list_visible_document_ids(current_user["id"])
    chunk_results = search_chunks_with_metadata(
        topic_title, top_k=10, allowed_document_ids=allowed_document_ids,
    )
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
    search_chunk_ids = []
    for i, cr in enumerate(chunk_results):
        if cr["score"] < 0.3:
            continue
        chunk_id = cr.get("id", f"search_{i}")
        search_chunk_ids.append(chunk_id)
    slide_audio_map = _get_slide_audio_map(search_chunk_ids)
    lecture_language = get_course_lecture_language(course_data)

    total_slides = 0
    for i, cr in enumerate(chunk_results):
        if cr["score"] < 0.3:
            continue
        chunk_id = cr.get("id", f"search_{i}")
        result = generate_spoken_text_and_formulas(
            cr["text"],
            chunk_index=i,
            course_data=course_data,
            persona_id=course_persona_settings(course_data)["narration_persona"],
            language=lecture_language,
        )
        display_text = result.get("display_text") or cr["text"]
        is_valid_uuid = _is_valid_uuid(chunk_id)

        if is_valid_uuid:
            chunks_to_update.append({
                "id": chunk_id,
                "display_text": display_text,
                "spoken_text": result["spoken_text"],
                "formulas": result["formulas"],
                "spoken_language": lecture_language,
            })

        slides = _build_slides_for_segment(
            display_text, result["spoken_text"], result["formulas"], "full",
            chunk_id, slide_audio_map,
        )
        total_slides += len(slides)

        segments.append(LectureSegment(
            chunk_id=chunk_id,
            chunk_index=i,
            text=display_text,
            spoken_text=result["spoken_text"],
            formulas=[LectureFormulaItem(**f) for f in result["formulas"]],
            has_audio=_check_audio_cache(chunk_id) if is_valid_uuid else False,
            duration_ms=0,
            slides=slides,
            language=lecture_language,
        ))

    if chunks_to_update:
        _persist_spoken_text(chunks_to_update)

    return LectureSequenceResponse(
        course_id=course_id,
        topic_id=topic_id,
        segments=segments,
        total_segments=len(segments),
        total_duration_ms=0,
        total_slides=total_slides,
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
    """キャッシュ済みの TTS 音声を返す。

    音声の生成は管理画面のバッチ処理（``/admin/courses/{id}/lecture-audio/generate``）でのみ
    行う方針のため、学習画面からの本エンドポイントはキャッシュ済み音声の配信のみを担い、
    オンデマンド生成は行わない。キャッシュが無い場合は 404 を返す（呼び出し側はレクチャー
    ボタンを無効化して未生成トピックを再生不可にする）。
    """
    chunk_id = body.chunk_id

    # トピック教材ベースのレクチャー（chunk_id="topic:{topic_id}"）はトピック音声
    # キャッシュ (topic_lecture_audio_cache) から配信する。それ以外はチャンク音声。
    topic_ref = _parse_topic_ref(chunk_id)
    if topic_ref is not None:
        cached = _get_topic_audio_cache(course_id, topic_ref, body.voice, body.slide_index)
    elif _is_valid_uuid(chunk_id):
        cached = _get_audio_cache(chunk_id, body.voice, body.slide_index)
    else:
        # キャッシュ不可（非 UUID かつ topic 形式でもない）は音声未生成扱い。
        cached = None

    if not cached:
        raise HTTPException(
            status_code=404,
            detail="この内容の音声はまだ生成されていません。管理画面で音声を生成してください。",
        )

    return LectureTTSResponse(
        chunk_id=chunk_id,
        audio_base64=cached["audio_base64"],
        duration_ms=cached["duration_ms"],
        word_timestamps=cached["word_timestamps"],
    )


@router.get("/courses/{course_id}/topics/{topic_id}/audio-status")
def get_topic_audio_status(
    course_id: str,
    topic_id: str,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """トピックに再生可能（キャッシュ済み）な音声があるかを軽量に判定する。

    学習画面のレクチャーボタンの有効/無効を決めるための判定で、spoken_text や音声の
    生成は一切行わない。ドラフト原稿ベースのトピック（``topic:`` セグメントで再生され、
    音声をキャッシュできない）と、教材チャンクが無いトピックは ``has_audio=False`` を返す。
    """
    course_data = get_course_data(current_user["id"], course_id)
    if not course_data:
        raise HTTPException(status_code=404, detail="Course not found")

    topic_info = find_course_topic(course_data, topic_id)
    if not topic_info:
        raise HTTPException(status_code=404, detail="Topic not found")

    lecture_language = get_course_lecture_language(course_data)
    empty = {
        "course_id": course_id,
        "topic_id": topic_id,
        "has_audio": False,
        "ready_chunks": 0,
        "total_chunks": 0,
        "ready_slides": 0,
        "total_slides": 0,
        "language": lecture_language,
        "stale_language": False,
    }

    # トピック教材ベースのレクチャー（表示＝非レクチャー教材表示と一致）は、
    # トピック音声 (topic_lecture_audio_cache) の readiness で判定する。
    # get_lecture_sequence の表示ソース判定（lecture_uses_topic_material）と同じ
    # 述語を使い、ボタン活性・表示・音声生成の食い違いを防ぐ。判定の正本は
    # core/lecture.py::compute_topic_audio_readiness（状態投影と同じ判定を共有）。
    if lecture_uses_topic_material(topic_info):
        session = _pg_session()
        try:
            readiness = compute_topic_audio_readiness(
                session, course_id, topic_id, topic_info, lecture_language,
            )
        finally:
            session.close()
        ready_slides = readiness["ready_slides"]
        return {
            "course_id": course_id,
            "topic_id": topic_id,
            "has_audio": ready_slides > 0,
            "ready_chunks": 1 if ready_slides > 0 else 0,
            "total_chunks": 1,
            "ready_slides": ready_slides,
            "total_slides": readiness["total_slides"],
            "language": lecture_language,
            "stale_language": readiness["stale_language"],
        }

    material_ids = course_source_material_ids(course_data)
    if not material_ids:
        return empty

    session = _pg_session()
    try:
        # スライド単位 + 言語一致の readiness 判定は core.lecture.py が正本
        # （Tier2-11: core/status/projector.py と本関数の両方から同じ判定を呼ぶ）。
        readiness = compute_material_audio_readiness(session, material_ids, lecture_language)
    finally:
        session.close()

    return {
        "course_id": course_id,
        "topic_id": topic_id,
        "has_audio": readiness["ready_slides"] > 0,
        "ready_chunks": readiness["ready_chunks"],
        "total_chunks": readiness["total_chunks"],
        "ready_slides": readiness["ready_slides"],
        "total_slides": readiness["total_slides"],
        "language": lecture_language,
        "stale_language": readiness["stale_language"],
    }


# ---------------------------------------------------------------------------
# 3. レクチャー中断チャット
# ---------------------------------------------------------------------------


def _get_tutor_system_prompt(domain: str, response_persona: str | None = None) -> str:
    """チューター（解説者）ロールのシステムプロンプトを生成する。

    Parameters
    ----------
    domain : str
        コースの専門分野。空文字の場合は「このコースの専門分野」でフォールバック。
    """
    domain_label = domain.strip() if domain.strip() else "このコースの専門分野"
    persona_instruction = persona_prompt(response_persona, target="response")
    persona_block = f"\n\n**口調設定:**\n{persona_instruction}" if persona_instruction else ""
    return f"""あなたは{domain_label}の学術講義中に学生の疑問をその場で即座に解消する「チューター（解説者）」です。
今まさに再生されている講義の局所的な疑問をすぐに解決し、速やかに講義の再開を促すことが使命です。

**チューターとしての役割:**
1. 前提知識の確認等で長々と引き留めず、その場ですぐに結論と解説を提示する。
2. 回答は簡潔に（2〜4文を基本）。必要な場合のみ数式や例を使う。
3. 解説後は必ず「では、講義の続きに戻りましょうか」と促す。
4. 誤解がある場合のみ「訂正：」と明記する。

**現在のコンテキスト:**
- 学生は音声による段階的な講義の途中で一時停止して質問しています
- 回答後、学生は講義の続きに戻ることを想定しています

**回答のフォーマット:**
1. 質問に対する直接的な回答（2〜3文）
2. 必要に応じて数式（LaTeX: $...$, $$...$$）や具体例
3. 「講義を再開する場合は再生ボタンを押してください。」で締めくくる{persona_block}"""


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

    course_title = _course_title(course_data, default=course_id)
    topic_info = find_course_topic(course_data, topic_id)
    topic_title = topic_info["title"] if topic_info else topic_id
    # domain が未設定の場合は course_title にフォールバック
    domain = course_data.get("domain") or course_title
    response_persona = course_persona_settings(course_data)["response_persona"]

    # 現在再生中のチャンクのテキストを取得してコンテキストに含める
    chunk_context = _get_chunk_text(body.current_chunk_id)

    messages: list[dict] = [
        {"role": "system", "content": _get_tutor_system_prompt(domain, response_persona)},
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

    # 本文中のドリルダウンマーカーは構造化アクションへ正規化する（フロントは
    # next_actions のみ描画）。講義への復帰はポップアップの「講義を再開する」ボタンが担う。
    clean_answer, inline_actions = extract_inline_actions(answer)

    # チャット履歴は本トピックの会話と同一スレッドに永続化する（割込みも同じ会話）。
    persist_chat_history(
        current_user["id"], course_id, topic_id,
        body.history, body.message, clean_answer,
    )

    return LectureInterruptResponse(
        answer=clean_answer,
        resume_chunk_id=body.current_chunk_id,
        resume_position_ms=body.pause_position_ms,
        course_update=course_update,
        next_actions=[asdict(a) for a in inline_actions],
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


# トピック教材の表示ソース判定（lecture_uses_topic_material）・スライド分割
# （build_topic_slides）の正本は core/lecture.py に移設済み（N18: 状態投影
# core/status/projector.py と readiness 判定を共有するため。core から routes を
# import しない）。


def _resolve_course_document_ids(material_ids: list[str]) -> list[str]:
    """コースソースの ``material_id``（``documents.source_path``）を ``documents.id``
    （UUID 文字列）へ解決する（Phase 4 図のコース流通 §7.1/§7.2）。

    ``document_figures.document_id`` は常に ``documents.id`` で保存される
    （figure_image_extraction が orchestrator の document_id をそのまま使うため。
    routes/admin.py の図配信エンドポイントと同じ前提）。

    正本は ``services.list_course_source_document_ids``（discuss モード設計書 §6.2、
    Tier 3-20 の横断基盤ルールで重複実装を残さない）。単一 material_id リストを
    擬似 ``sources`` へ包んで渡すだけで、SQL 解決・fail-closed ロジックは共有する。
    """
    if not material_ids:
        return []
    return list(
        list_course_source_document_ids(
            {"sources": [{"material_id": mid} for mid in material_ids]}
        )
    )


def _course_document_ids(course_data: dict) -> list[str]:
    """コースの ``sources[]`` から document_id 集合を導出する（``documents.id`` 単位、
    重複除去。Phase 4 図のコース流通 §7.1/§7.3 条件2）。

    ``sources[].document_id``（書き手不在の読み取り専用フィールドだが、
    ``theory_components.py`` が既に読んでいる既存フィールドのため、明示された分は
    そのまま採用する）と、``material_id``（``documents.source_path``）から解決した
    document_id の両方を合わせる。正本は ``services.list_course_source_document_ids``
    （discuss モード設計書 §6.2）。
    """
    return list(list_course_source_document_ids(course_data))


def _load_course_teaching_figures(course_id: str) -> dict[str, dict]:
    """コースの**採用済み教材図**（``course_teaching_figures``、教材図スタジオ設計書 §7.1）を
    ``figure_id -> {"caption", "content_type", "title"}`` で返す（``status='adopted'`` のみ）。

    取得に失敗しても図の解決全体を止めない（fail-soft で空 dict）。
    """
    session = _pg_session()
    try:
        return dict(adopted_figures_map(session, course_id) or {})
    except Exception:
        logger.warning(
            "Failed to load teaching figures for course %s", course_id, exc_info=True
        )
        return {}
    finally:
        session.close()


def _course_figures_index(
    course_id: str,
    course_data: dict,
    *,
    teaching_image_url,
    extracted_image_url,
) -> dict[str, dict]:
    """コースで解決できる図（採用済み教材図 + ソース教材の抽出図）のマップを組み立てる。

    ``image_url`` の組み立てだけを注入で差し替えられるようにして、学習者向け
    （``_load_course_figures_by_id``）と管理画面向け（``load_course_figures_index_for_admin``）
    で解決規則そのものを二重実装しない（教材図スタジオ設計書 §7.3 の非対称解消）。

    **教材図のマージは document 走査より前**に行う（§7.1）。現実装は document_ids が
    空だと早期 return するため、後置するとソース教材の無いコースで生成図が解決不能に
    なる。DB 例外時のフォールバックでも教材図のマージ結果は保持する。
    """
    figures_by_id: dict[str, dict] = {}
    for fig_id, info in _load_course_teaching_figures(course_id).items():
        fig_id = str(fig_id)
        figures_by_id[fig_id] = {
            "caption": (info or {}).get("caption") or "",
            "image_url": teaching_image_url(fig_id),
            # 教材図は document に属さない（コース教材の付属物・FG1）。説明充填
            # （``_attach_figure_explanations``）は document 単位で読むため、None で
            # 「document スコープの説明を持たない」ことを明示する。
            "document_id": None,
            "teaching": True,
        }

    document_ids = _course_document_ids(course_data)
    if not document_ids:
        return figures_by_id

    session = _pg_session()
    try:
        placeholders = ", ".join(f":did_{i}" for i in range(len(document_ids)))
        params: dict = {f"did_{i}": did for i, did in enumerate(document_ids)}
        rows = session.execute(
            sa_text(f"""
                SELECT id::text, caption_text, document_id
                FROM document_figures
                WHERE document_id IN ({placeholders}) AND status = 'extracted'
            """),
            params,
        ).fetchall()
    except Exception:
        logger.warning("Failed to load course figures for course %s", course_id, exc_info=True)
        return figures_by_id
    finally:
        session.close()

    for row in rows:
        fig_id = str(row[0])
        document_id = str(row[2]) if row[2] else None
        figures_by_id[fig_id] = {
            "caption": row[1],
            "image_url": extracted_image_url(fig_id, document_id),
            "document_id": document_id,
            "teaching": False,
        }
    return figures_by_id


def _load_course_figures_by_id(course_id: str, course_data: dict) -> dict[str, dict]:
    """コースで解決できる図を、``core.lecture`` の記法解決（``resolve_figure_embeds``）が
    使う ``figure_id -> {"caption", "image_url"}`` へ変換する
    （Phase 4 図のコース流通 §7.1/§7.2 + 教材図スタジオ §7.1）。

    ``core/`` は DB を読まない方針のため、この供給は routes 層の責務。学習者向け
    ``image_url`` は 4条件 fail-closed ゲート付きの学習者エンドポイント
    （``GET /api/learning/courses/{course_id}/figures/{figure_id}/image``）を指す
    （抽出図・教材図で同一パス。図の出自は配信側が判定する）。

    ``document_id`` も同梱する（``core.lecture`` の記法解決自体は使わないが、
    Phase 2 §5.3 の説明充填 ``_attach_figure_explanations`` が element_explanations を
    document 単位でグルーピングして読むために必要）。採用済み教材図は
    ``document_id=None`` / ``teaching=True``。
    """
    learner_url = f"/api/learning/courses/{course_id}/figures/{{fid}}/image"
    return _course_figures_index(
        course_id,
        course_data,
        teaching_image_url=lambda fid: learner_url.format(fid=fid),
        extracted_image_url=lambda fid, _document_id: learner_url.format(fid=fid),
    )


def load_course_figures_index_for_admin(course_id: str, course_data: dict) -> dict[str, dict]:
    """管理画面（原稿スタジオプレビュー）向けの図マップ（教材図スタジオ設計書 §7.3）。

    学習画面と**同じ解決規則**だが ``image_url`` は admin 経路を指す:
    教材図は ``/api/admin/courses/{course_id}/teaching-figures/{fid}/image``、
    抽出図は ``/api/admin/documents/{document_id}/figures/{fid}/image``。
    document_id が引けない抽出図（通常起きない）は ``image_url`` を空にする
    （存在しない URL を捏造しない）。
    """
    return _course_figures_index(
        course_id,
        course_data,
        teaching_image_url=lambda fid: f"/api/admin/courses/{course_id}/teaching-figures/{fid}/image",
        extracted_image_url=lambda fid, document_id: (
            f"/api/admin/documents/{document_id}/figures/{fid}/image" if document_id else ""
        ),
    )


def _attach_figure_explanations(
    figures: list[dict],
    figures_by_id: dict[str, dict],
) -> None:
    """図デスクリプタ（dict、``resolve_figure_embeds``/``build_topic_slides`` 由来）に、
    承認済み contextual 説明を充填する（Phase 2 §5.3・element_explanations 由来）。

    ``figures`` の各要素を in-place で書き換える（``explanation`` フィールドのみ）。
    ``figures_by_id`` の各エントリの ``document_id``（``_load_course_figures_by_id`` が
    付与する）でグルーピングし、``element_explanations.approved_for_elements`` を
    document 単位で（コースのソース document 数ぶんだけ、通常1回）呼ぶ —
    スライド/チャンク単位ループでの N+1 呼び出しを避ける。

    approved の ``kind='contextual'`` のみを採用する（generic はここでは使わない —
    descriptor は「この論文での役割」を示す文脈説明の場であるため。学習者へは
    candidate/dismissed/superseded を一切出さない — ``approved_for_elements`` が
    ``status='approved'`` のみ返すため混入しない、E2）。
    """
    referenced_ids = {
        str(fig.get("figure_id") or "").strip()
        for fig in figures
        if isinstance(fig, dict) and fig.get("figure_id")
    }
    referenced_ids.discard("")
    if not referenced_ids:
        return

    by_document: dict[str, list[str]] = {}
    for fig_id in referenced_ids:
        doc_id = (figures_by_id.get(fig_id) or {}).get("document_id")
        if doc_id:
            by_document.setdefault(str(doc_id), []).append(fig_id)
    if not by_document:
        return

    explanations: dict[str, str] = {}
    session = _pg_session()
    try:
        for doc_id, fig_ids in by_document.items():
            refs = [(element_explanations.ELEMENT_TYPE_FIGURE, fid) for fid in fig_ids]
            approved = element_explanations.approved_for_elements(session, doc_id, refs)
            for (_etype, eid), rows in approved.items():
                for row in rows:
                    if row.get("kind") == element_explanations.KIND_CONTEXTUAL and row.get("body"):
                        explanations[eid] = row["body"]
                        break
    except Exception:
        logger.warning("Failed to load approved figure explanations", exc_info=True)
        return
    finally:
        session.close()

    if not explanations:
        return
    for fig in figures:
        if not isinstance(fig, dict):
            continue
        fig_id = str(fig.get("figure_id") or "").strip()
        if fig_id and fig_id in explanations:
            fig["explanation"] = explanations[fig_id]


def _build_topic_draft_segment(
    course_id: str,
    topic_id: str,
    topic: dict,
    course_data: dict,
) -> LectureSegment | None:
    """トピックの授業用教材（student_material / spoken_script）をレクチャー用セグメントに
    変換する。

    受講画面の非レクチャー教材表示（``get_topic_material``）と同じ student_material を
    ``display_text`` に、``spoken_script`` を ``spoken_text`` に割り当て、
    ``core.lecture.build_topic_slides`` でスライド分割する（長い教材は段落境界で自動ページ
    分割）。``![[figure:id]]`` 埋め込みはコースのソース教材に属する document_figures から
    解決し（Phase 4 §7.2）、各スライドの ``figures`` に割り当てる。各スライドの音声は
    ``topic_lecture_audio_cache``（``(course_id, topic_id, slide_index)``）から解決し、
    キャッシュ済みスライドは ``has_audio=True`` にする（教員が原稿スタジオで生成済みなら
    実声で読み上げ、無ければ受講側でタイマー送りにフォールバックする。§6-4）。
    """
    figures_by_id = _load_course_figures_by_id(course_id, course_data)
    slide_dicts, display_text, spoken_text, formulas = build_topic_slides(
        topic, figures_by_id=figures_by_id,
    )
    if not slide_dicts:
        return None

    # 図デスクリプタに承認済み contextual 説明を充填する（Phase 2 §5.3）。slide_dicts の
    # 各スライドの "figures" は同一 dict オブジェクトへの参照（コピーではない）なので、
    # ここで in-place に書き換えれば以降の segment_figures 集約にも反映される。
    _attach_figure_explanations(
        [fig for sd in slide_dicts for fig in sd.get("figures", [])],
        figures_by_id,
    )

    # 各スライドの音声は topic_lecture_audio_cache から解決する（教員が生成済みなら has_audio=True）。
    topic_audio_map = _get_topic_slide_audio_map(course_id, topic_id)
    slides = [
        LectureSlide(
            slide_index=sd["slide_index"],
            display_text=sd["display_text"],
            spoken_text=sd["spoken_text"],
            formulas=[LectureFormulaItem(**f) for f in sd["formulas"]],
            figures=[LectureFigureItem(**f) for f in sd.get("figures", [])],
            has_audio=sd["slide_index"] in topic_audio_map,
            duration_ms=topic_audio_map.get(sd["slide_index"], 0),
        )
        for sd in slide_dicts
    ]

    # セグメント全体の figures はスライド単位の割り当てから合成する（formulas と同じ規約:
    # build_topic_slides は display_text 全体から解決した figures をスライドに分配済み）。
    segment_figures = [fig for sd in slide_dicts for fig in sd.get("figures", [])]

    return LectureSegment(
        chunk_id=f"topic:{topic_id}",
        chunk_index=int(topic.get("topic_index") or 0),
        text=display_text,
        spoken_text=spoken_text,
        formulas=[LectureFormulaItem(**f) for f in formulas],
        figures=[LectureFigureItem(**f) for f in segment_figures],
        has_audio=bool(topic_audio_map),
        duration_ms=0,
        segment_mode="full",
        slides=slides,
        language=get_course_lecture_language(course_data),
    )


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


def _get_audio_cache(chunk_id: str, voice: str, slide_index: int = 0) -> dict | None:
    """音声キャッシュを取得する（chunk_id, slide_index, voice のキャッシュキー）。"""
    if not _is_valid_uuid(chunk_id):
        return None
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


def _get_slide_audio_map(chunk_ids: list[str]) -> dict[tuple[str, int], int]:
    """指定チャンク群のスライド単位音声キャッシュを一括取得する。

    Returns
    -------
    dict[tuple[str, int], int]
        ``(chunk_id, slide_index) -> duration_ms``。voice='alloy' のキャッシュのみ対象
        （学習画面の再生ボイスは alloy 固定。Issue #66 の既存挙動を踏襲）。
    """
    valid_ids = list(dict.fromkeys(cid for cid in chunk_ids if _is_valid_uuid(cid)))
    if not valid_ids:
        return {}
    session = _pg_session()
    try:
        placeholders = ", ".join(f":cid_{i}" for i in range(len(valid_ids)))
        params: dict = {f"cid_{i}": cid for i, cid in enumerate(valid_ids)}
        rows = session.execute(
            sa_text(f"""
                SELECT chunk_id, slide_index, duration_ms
                FROM lecture_audio_cache
                WHERE chunk_id IN ({placeholders}) AND voice = 'alloy'
            """),
            params,
        ).fetchall()
        return {(str(row[0]), int(row[1])): int(row[2] or 0) for row in rows}
    except Exception:
        logger.warning("Failed to load slide audio map for %d chunks", len(valid_ids), exc_info=True)
        return {}
    finally:
        session.close()


def _parse_topic_ref(chunk_id: str) -> str | None:
    """``"topic:{topic_id}"`` 形式のセグメント chunk_id からトピックIDを取り出す。

    トピック教材ベースのレクチャー（``_build_topic_draft_segment``）のセグメントは
    UUID チャンクではなく ``topic:{topic_id}`` を chunk_id に持つ。学習側の TTS 配信は
    この形式を検出して ``topic_lecture_audio_cache`` から音声を返す。
    """
    if isinstance(chunk_id, str) and chunk_id.startswith("topic:"):
        return chunk_id[len("topic:"):]
    return None


def _get_topic_slide_audio_map(course_id: str, topic_id: str) -> dict[int, int]:
    """トピックのスライド単位音声キャッシュ (``topic_lecture_audio_cache``) を一括取得する。

    Returns
    -------
    dict[int, int]
        ``slide_index -> duration_ms``（voice='alloy' のみ。学習画面の再生ボイス固定）。
    """
    session = _pg_session()
    try:
        rows = session.execute(
            sa_text("""
                SELECT slide_index, duration_ms
                FROM topic_lecture_audio_cache
                WHERE course_id = :course_id AND topic_id = :topic_id AND voice = 'alloy'
            """),
            {"course_id": course_id, "topic_id": topic_id},
        ).fetchall()
        return {int(r[0]): int(r[1] or 0) for r in rows}
    except Exception:
        logger.warning(
            "Failed to load topic slide audio map for %s/%s", course_id, topic_id, exc_info=True,
        )
        return {}
    finally:
        session.close()


def _get_topic_audio_cache(
    course_id: str, topic_id: str, voice: str, slide_index: int = 0,
) -> dict | None:
    """トピックのスライド音声キャッシュを取得する（``(course_id, topic_id, slide_index, voice)``）。"""
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("""
                SELECT audio_data, duration_ms, word_timestamps
                FROM topic_lecture_audio_cache
                WHERE course_id = :course_id AND topic_id = :topic_id
                  AND voice = :voice AND slide_index = :slide_index
                LIMIT 1
            """),
            {
                "course_id": course_id, "topic_id": topic_id,
                "voice": voice, "slide_index": slide_index,
            },
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
        logger.warning(
            "Failed to get topic audio cache for %s/%s", course_id, topic_id, exc_info=True,
        )
        return None
    finally:
        session.close()


# トピック教材ベースの音声 readiness 判定の正本は
# core/lecture.py::compute_topic_audio_readiness に移設済み（N18）。


def _build_slides_for_segment(
    display_text: str,
    spoken_text: str | None,
    formulas: list[dict],
    segment_mode: str,
    chunk_id: str,
    audio_map: dict[tuple[str, int], int],
) -> list[LectureSlide]:
    """セグメントをスライドに分割し、スライド単位の音声キャッシュ有無を付与する。

    ``segment_mode == "summary"`` のセグメントは分割せず1スライドにする
    （display はセグメント本文、spoken は要約テキスト）。
    """
    if segment_mode == "summary":
        slide_dicts = [{
            "slide_index": 0,
            "display_text": display_text,
            "spoken_text": spoken_text,
            "formulas": formulas,
        }]
    else:
        slide_dicts, _mismatch = split_slides(display_text, spoken_text, formulas)

    slides = []
    for sd in slide_dicts:
        key = (chunk_id, sd["slide_index"])
        duration_ms = audio_map.get(key)
        slides.append(LectureSlide(
            slide_index=sd["slide_index"],
            display_text=sd["display_text"],
            spoken_text=sd["spoken_text"],
            formulas=[LectureFormulaItem(**f) for f in sd["formulas"]],
            has_audio=key in audio_map,
            duration_ms=duration_ms or 0,
        ))
    return slides


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
    """チャンクの display_text / spoken_text / formulas を DB に永続化する。

    ``chunk["spoken_language"]`` が指定されていれば ``chunks.spoken_language`` も
    併せて書く（migration 040 Phase 4）。未指定（キー無し/None）の場合は既存値を
    保持する（``COALESCE`` で上書きしない）。
    """
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
                        formulas = CAST(:formulas AS jsonb),
                        spoken_language = COALESCE(:spoken_language, spoken_language)
                    WHERE id = CAST(:id AS uuid)
                """),
                {
                    "id": chunk["id"],
                    "display_text": chunk.get("display_text", ""),
                    "spoken_text": chunk["spoken_text"],
                    "formulas": json.dumps(chunk["formulas"], ensure_ascii=False),
                    "spoken_language": chunk.get("spoken_language"),
                },
            )
        session.commit()
    except Exception:
        session.rollback()
        logger.warning("Failed to persist spoken_text", exc_info=True)
    finally:
        session.close()
