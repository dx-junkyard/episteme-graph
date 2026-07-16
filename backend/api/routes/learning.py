"""Episteme Graph — 学習エンドポイント (/api/learning)。"""

from __future__ import annotations

import base64
import logging
import re
import threading
import uuid
from dataclasses import asdict

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import text as sa_text

from dependencies import _get_current_user
from schemas import (
    ChunkContent,
    CourseCreateRequest,
    CourseUpdateRequest,
    LearningChatHistoryResponse,
    LearningChatRequest,
    LearningChatResponse,
    LearningCheckQuestionRequest,
    LearningCheckQuestionResponse,
    LearningCourseDetail,
    LearningCourseLayeredResponse,
    LearningCourseOut,
    LearningProgress,
    PersonalLayer,
    TopicMaterialResponse,
)
from services import (
    calculate_progress,
    check_prerequisites,
    confirm_anchor_trace,
    confirm_tension_trace,
    connect_tension_trace,
    detect_and_record_misconception,
    dismiss_anchor_trace,
    dismiss_tension_trace,
    get_anchor_digest,
    get_tension_digest,
    course_deletion_notice,
    enroll_user_in_course,
    get_course_chunks_ordered,
    get_course_data,
    get_editable_course_data,
    get_viewable_course_data,
    get_chunk_passage,
    get_graph_element_context,
    get_interest_traces,
    get_personal_layer,
    get_trace_map_exclusion_flags,
    get_user_group_ids,
    log_unanswered_query,
    persist_chat_history,
    truncate_chat_and_supersede,
    record_internalization,
    record_interest_trace,
    record_student_stumble_event,
    resolve_interest_trace,
    save_course_data,
    set_trace_map_exclusion,
    delete_course_data,
    search_chunks_with_metadata,
    user_can_access_group,
    user_can_view_course,
)
from pydantic import BaseModel
from core.course_data import (
    course_source_material_ids,
    course_title as _course_title,
    course_topics,
    find_course_topic,
)
from core.llm import generate_text, get_llm_params, transcribe_audio
from core.llm_usage.context import usage_context
from core.tts import generate_tts_audio, strip_text_for_speech
from core.learning_experience import (
    TIER_OUT_OF_SOURCE,
    aggregate_overall_tier,
    build_position_anchor,
    out_of_source_guard_instruction,
    out_of_source_notice,
)
from core.learning_support_agent import (
    LearningSupportAgent,
    LearningSupportResult,
    extract_inline_actions,
)
from core.personas import course_persona_settings, persona_prompt
from core.postgres import get_session as _pg_session
from core.course_content_builder import build_course_content_background
from core.atlas_path import build_learning_path_card
from core.tension.prefilter import judge_tension_hint
from core.tension.worker import maybe_schedule_tension_mining
from core.structure_anchor.schema import (
    ATTRIBUTION_LEARNER_SELECTED,
    DOUBT_TYPE_LABELS,
    anchor_type_for_element,
    build_anchor_payload,
)
from core.structure_anchor.worker import (
    check_and_count_confirm_prompt,
    maybe_schedule_anchor_mining,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/learning", tags=["Learning"])

# ---------------------------------------------------------------------------
# Course CRUD
# ---------------------------------------------------------------------------


def _validate_visibility(visibility: str, group_id: str | None, user_id: str) -> None:
    """visibility の値と group_id の整合性を検証する。"""
    if visibility not in ("public", "group", "private"):
        raise HTTPException(status_code=400, detail=f"Invalid visibility: {visibility}")
    if visibility == "group":
        if not group_id:
            raise HTTPException(status_code=400, detail="visibility='group' requires group_id")
        if not user_can_access_group(user_id, group_id):
            raise HTTPException(
                status_code=403,
                detail="指定されたグループに参加していません",
            )


@router.post("/courses", response_model=LearningCourseOut, status_code=201)
def create_course(
    body: CourseCreateRequest,
    current_user: dict = Depends(_get_current_user),
) -> LearningCourseOut:
    """新しいコースを作成する。"""
    _validate_visibility(body.visibility, body.group_id, current_user["id"])
    course_id = str(uuid.uuid4())[:8]

    data = {
        "id": course_id,
        "title": body.title,
        "chapters": [ch.model_dump() for ch in body.chapters],
        "topics": [t.model_dump() for t in body.topics],
        "concepts": [c.model_dump() for c in body.concepts],
        "sources": [s.model_dump() for s in body.sources],
        "referenced_sections": [],
    }

    save_course_data(
        current_user["id"],
        course_id,
        data,
        is_template=body.is_template,
        visibility=body.visibility,
        group_id=body.group_id if body.visibility == "group" else None,
        description=body.description,
    )

    threading.Thread(
        target=build_course_content_background,
        args=(current_user["id"], course_id),
        daemon=True,
    ).start()

    logger.info("Created course '%s' (id=%s) for user=%s", body.title, course_id, current_user["id"])
    return LearningCourseOut(
        id=course_id,
        title=body.title,
        is_template=body.is_template,
        visibility=body.visibility,
        group_id=body.group_id if body.visibility == "group" else None,
        description=body.description,
    )


@router.get("/courses", response_model=list[LearningCourseOut])
def list_courses(
    current_user: dict = Depends(_get_current_user),
) -> list[LearningCourseOut]:
    """ユーザーが登録しているコース一覧を返す。

    Issue #133: 「1 つの不変なマスターコース」+「ユーザー個別の learning_states」
    モデルに変更。受講時にコースをクローンせず、learning_states にレコードを作る。

    以下を返す:
    - 自分が所有するコース（learning_courses.user_id = 自分）
    - 自分が受講済みのマスターコース（learning_states 経由）
    - visibility='public' かつ公開テンプレートで未受講のコース（受講可能）
    - 自分が参加するグループに共有されているマスターコースで未受講のもの（受講可能）
    """
    user_groups = get_user_group_ids(current_user["id"])

    session = _pg_session()
    try:
        own_records = session.execute(
            sa_text("""
                SELECT id, title,
                       COALESCE(is_template, false) AS is_template,
                       COALESCE(is_published, false) AS is_published,
                       COALESCE(visibility, 'private') AS visibility,
                       group_id,
                       COALESCE(description, '') AS description
                FROM learning_courses
                WHERE user_id = CAST(:user_id AS uuid)
            """),
            {"user_id": current_user["id"]},
        ).fetchall()

        # 受講中のマスターコース（learning_states 経由）
        enrolled_records = session.execute(
            sa_text("""
                SELECT lc.id, lc.title,
                       COALESCE(lc.is_template, false) AS is_template,
                       COALESCE(lc.is_published, false) AS is_published,
                       COALESCE(lc.visibility, 'private') AS visibility,
                       lc.group_id,
                       COALESCE(lc.description, '') AS description
                FROM learning_courses lc
                JOIN learning_states ls ON ls.course_id = lc.id
                WHERE ls.user_id = CAST(:user_id AS uuid)
                  AND lc.user_id != CAST(:user_id AS uuid)
            """),
            {"user_id": current_user["id"]},
        ).fetchall()

        # 公開テンプレート（未受講のみ）
        public_records = session.execute(
            sa_text("""
                SELECT lc.id, lc.title,
                       COALESCE(lc.visibility, 'private'),
                       lc.group_id,
                       COALESCE(lc.description, '')
                FROM learning_courses lc
                WHERE lc.is_published = true AND lc.is_template = true
                  AND COALESCE(lc.visibility, 'public') = 'public'
                  AND lc.user_id != CAST(:user_id AS uuid)
                  AND NOT EXISTS (
                      SELECT 1 FROM learning_states ls
                      WHERE ls.user_id = CAST(:user_id AS uuid)
                        AND ls.course_id = lc.id
                  )
            """),
            {"user_id": current_user["id"]},
        ).fetchall()

        # グループ共有コース（自分が参加するグループ、かつ未受講のみ）
        # - 旧: learning_courses.group_id + visibility='group' を参照
        # - 新: object_group_permissions 多対多マッピング (viewer/editor、object_type='course')
        if user_groups:
            # UUID リストを展開
            gph = ", ".join(f"CAST(:g_{i} AS uuid)" for i in range(len(user_groups)))
            params: dict = {"user_id": current_user["id"]}
            for i, gid in enumerate(user_groups):
                params[f"g_{i}"] = gid
            group_records = session.execute(
                sa_text(f"""
                    SELECT DISTINCT lc.id, lc.title,
                           COALESCE(lc.is_template, false) AS is_template,
                           COALESCE(lc.is_published, false) AS is_published,
                           COALESCE(lc.visibility, 'private'),
                           lc.group_id,
                           COALESCE(lc.description, '')
                    FROM learning_courses lc
                    LEFT JOIN object_group_permissions cgp
                        ON cgp.object_type = 'course' AND cgp.object_id = lc.id
                    WHERE lc.user_id != CAST(:user_id AS uuid)
                      AND (
                          (lc.visibility = 'group' AND lc.group_id IN ({gph}))
                          OR (cgp.group_id IN ({gph})
                              AND cgp.permission IN ('viewer', 'editor'))
                      )
                      AND NOT EXISTS (
                          SELECT 1 FROM learning_states ls
                          WHERE ls.user_id = CAST(:user_id AS uuid)
                            AND ls.course_id = lc.id
                      )
                """),
                params,
            ).fetchall()
        else:
            group_records = []
    finally:
        session.close()

    courses = [
        LearningCourseOut(
            id=r[0],
            title=r[1],
            is_template=bool(r[2]),
            is_published=bool(r[3]),
            is_enrollable=False,
            visibility=r[4] or "private",
            group_id=str(r[5]) if r[5] else None,
            description=r[6] or "",
        )
        for r in own_records
    ]
    # 受講中のマスターコースも「マイコース」として並べる（is_enrollable=False）
    courses.extend(
        LearningCourseOut(
            id=r[0],
            title=r[1],
            is_template=bool(r[2]),
            is_published=bool(r[3]),
            is_enrollable=False,
            visibility=r[4] or "private",
            group_id=str(r[5]) if r[5] else None,
            description=r[6] or "",
        )
        for r in enrolled_records
    )
    courses.extend(
        LearningCourseOut(
            id=r[0],
            title=r[1],
            is_template=True,
            is_published=True,
            is_enrollable=True,
            visibility=r[2] or "public",
            group_id=str(r[3]) if r[3] else None,
            description=r[4] or "",
        )
        for r in public_records
    )
    courses.extend(
        LearningCourseOut(
            id=r[0],
            title=r[1],
            is_template=bool(r[2]),
            is_published=bool(r[3]),
            is_enrollable=True,
            visibility=r[4] or "group",
            group_id=str(r[5]) if r[5] else None,
            description=r[6] or "",
        )
        for r in group_records
    )

    # 同一マスターコースが own / enrolled / public / group 経由で複数ヒットする場合は
    # own > enrolled > public > group の優先順位で先勝ちで重複排除する。
    seen_ids: set[str] = set()
    unique_courses: list[LearningCourseOut] = []
    for c in courses:
        if c.id in seen_ids:
            continue
        seen_ids.add(c.id)
        unique_courses.append(c)
    return unique_courses


@router.get("/courses/{course_id}", response_model=LearningCourseLayeredResponse)
def get_course(
    course_id: str,
    current_user: dict = Depends(_get_current_user),
) -> LearningCourseLayeredResponse:
    """コースの詳細データをレイヤー分離形式で返す（Issue #145）。

    マスター教材（不変）と個人レイヤー（誤解・注釈）を分離して返す。
    オーナー（教員）も一般学生と同じ学習体験を得る。
    """
    data = get_course_data(current_user["id"], course_id)
    if not data:
        raise HTTPException(status_code=404, detail="Course not found")

    personal = get_personal_layer(current_user["id"], course_id)
    return LearningCourseLayeredResponse(
        master_course=LearningCourseDetail(**data),
        personal_layer=PersonalLayer(**personal),
    )


@router.get("/courses/{course_id}/version-notice")
def get_course_version_notice(
    course_id: str,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """受講者向け: コースの削除予定など版ライフサイクルの一行通知（V層, migration 037）。

    受講/アクセス可能なコースのみ。コース自体の削除予約に加え、元教材の削除予約（教材 purge は
    所有者のコースを巻き添え削除する）も検出して猶予期限を返し、学習 UI がバナー表示する。
    版が無い / エラー時は lifecycle='active' として静かに返す（fail-open で学習を止めない）。
    """
    if not get_course_data(current_user["id"], course_id):
        raise HTTPException(status_code=404, detail="Course not found")
    try:
        notice = course_deletion_notice(course_id)
    except Exception:  # noqa: BLE001 — fail-open
        notice = None
    if notice:
        return notice
    return {"lifecycle": "active", "delete_purge_after": None, "delete_reason": ""}


@router.put("/courses/{course_id}", response_model=LearningCourseDetail)
def update_course(
    course_id: str,
    body: CourseUpdateRequest,
    current_user: dict = Depends(_get_current_user),
) -> LearningCourseDetail:
    """コースを部分更新する。指定されたフィールドのみ上書き。

    Issue #133: 受講者はマスターコースを改変できない（learning_states に個別の
    差分を持つのみ）。所有者または editor 権限グループのメンバーのみ編集可能。
    """
    data = get_editable_course_data(current_user["id"], course_id)
    if not data:
        raise HTTPException(status_code=404, detail="Course not found")

    if body.title is not None:
        data["title"] = body.title
    if body.chapters is not None:
        data["chapters"] = [ch.model_dump() for ch in body.chapters]
    if body.topics is not None:
        data["topics"] = [t.model_dump() for t in body.topics]
    if body.concepts is not None:
        data["concepts"] = [c.model_dump() for c in body.concepts]
    if body.sources is not None:
        data["sources"] = [s.model_dump() for s in body.sources]

    save_course_data(current_user["id"], course_id, data)
    logger.info("Updated course %s for user=%s", course_id, current_user["id"])

    return LearningCourseDetail(**data)


@router.delete("/courses/{course_id}", status_code=204)
def delete_course(
    course_id: str,
    current_user: dict = Depends(_get_current_user),
) -> None:
    """コースを削除する。"""
    deleted = delete_course_data(current_user["id"], course_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Course not found")

    logger.info("Deleted course %s for user=%s", course_id, current_user["id"])


# ---------------------------------------------------------------------------
# Progress
# ---------------------------------------------------------------------------


@router.get("/courses/{course_id}/progress", response_model=LearningProgress)
def get_progress(
    course_id: str,
    current_user: dict = Depends(_get_current_user),
) -> LearningProgress:
    """コースの進捗データを計算して返す。"""
    data = get_course_data(current_user["id"], course_id)
    if not data:
        raise HTTPException(status_code=404, detail="Course not found")

    progress = calculate_progress(current_user["id"], course_id, data)
    return LearningProgress(**progress)


# ---------------------------------------------------------------------------
# Enroll
# ---------------------------------------------------------------------------


@router.post("/courses/{course_id}/enroll", response_model=LearningCourseOut, status_code=201)
def enroll_course(
    course_id: str,
    current_user: dict = Depends(_get_current_user),
) -> LearningCourseOut:
    """受講可能なコース（公開/グループ共有）に受講登録する。

    Issue #133: 旧仕様ではマスターコースを丸ごとクローンしていたが、
    learning_states にレコードを作成する方式に変更。マスターコースは
    不変に保たれ、ユーザーの学習状態のみが差分として管理される。
    UNIQUE (user_id, course_id) により二重受講はDBレベルでブロックされる。
    """
    session = _pg_session()
    try:
        record = session.execute(
            sa_text("""
                SELECT title, COALESCE(visibility, 'private'), group_id,
                       COALESCE(is_published, false), COALESCE(is_template, false)
                FROM learning_courses
                WHERE id = :course_id
                LIMIT 1
            """),
            {"course_id": course_id},
        ).fetchone()
    finally:
        session.close()

    if not record:
        raise HTTPException(status_code=404, detail="Course not found")

    title, visibility, group_id, is_published, is_template = record
    enrollable = False
    if visibility == "public" and is_published and is_template:
        enrollable = True
    elif visibility == "group" and group_id and user_can_access_group(
        current_user["id"], str(group_id)
    ):
        enrollable = True
    elif user_can_view_course(current_user["id"], course_id):
        # object_group_permissions（object_type='course'）経由で viewer/editor 権限を持つ場合
        enrollable = True

    if not enrollable:
        raise HTTPException(status_code=403, detail="このコースを受講する権限がありません")

    enroll_user_in_course(current_user["id"], course_id)

    logger.info(
        "User=%s enrolled in master course %s (learning_states row created)",
        current_user["id"], course_id,
    )
    return LearningCourseOut(id=course_id, title=title or "")


# ---------------------------------------------------------------------------
# Chat (RAG)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Greeting / Meta-dialogue detection
# ---------------------------------------------------------------------------

_GREETING_PATTERNS = [
    "こんにちは", "こんばんは", "おはよう", "はじめまして",
    "よろしくお願い", "学習を始め", "学習を開始", "勉強を始め",
    "始めたい", "開始したい", "スタート",
    "第1章の学習を開始する", "前提知識を確認する",
]


def _is_greeting(message: str) -> bool:
    """メッセージが挨拶・メタ対話かどうかを判定する。"""
    msg = message.strip()
    if len(msg) < 30 and any(p in msg for p in _GREETING_PATTERNS):
        return True
    return False


# UI ボタン由来の型付きアクションは、自然文の intent 分類を経由せず決定論的に
# ルートへ割り当てる。これにより日本語ラベル（や壊れたトークン）が CHIT_CHAT へ
# 誤分類される事故を防ぐ。
_TYPED_ACTION_INTENT: dict[str, str] = {
    "check_prerequisites": "LEARNING_ADVICE",
    "review_prerequisite": "LEARNING_ADVICE",
    "prerequisite_review": "LEARNING_ADVICE",
    "drilldown": "DOMAIN_RAG",
    "ask_question": "DOMAIN_RAG",
    "continue_detail": "DOMAIN_RAG",
    "start_topic": "DOMAIN_RAG",
}

_PREREQUISITE_ACTIONS = {"check_prerequisites", "review_prerequisite", "prerequisite_review"}


def _route_for_typed_action(support_action: str | None) -> str | None:
    """型付き support_action に対応する確定ルートを返す（無ければ None）。"""
    if not support_action:
        return None
    return _TYPED_ACTION_INTENT.get(support_action.strip())


def _classify_intent(message: str, course_title: str) -> str:
    """ユーザーメッセージの意図を分類する (Intent Routing)。

    Returns
    -------
    str
        ``'CHIT_CHAT'`` | ``'LEARNING_ADVICE'`` | ``'DOMAIN_RAG'``
    """
    if _is_greeting(message):
        return "LEARNING_ADVICE"
    if "はい" in message and "理解" in message:
        return "DOMAIN_RAG"

    params = get_llm_params("fast")
    prompt = (
        f"学習コース「{course_title}」の学習支援AIとして、学生からの質問を3つのルートに分類します。\n\n"
        "分類ルート:\n"
        "- CHIT_CHAT: 学習と無関係な雑談・日常会話（天気、食事、娯楽、個人的な話題など）\n"
        "- LEARNING_ADVICE: 学習の進め方・方法に関するメタ質問（どう進めるか、何から学ぶか、学習計画の相談など）\n"
        "- DOMAIN_RAG: 物理学・数学などの専門知識・概念に関する質問\n\n"
        f"質問: {message}\n\n"
        "上記のルートの中から最も適切な1つだけを返してください（説明不要）:"
    )

    try:
        result = generate_text(
            messages=[{"role": "user", "content": prompt}],
            model=params["model"],
            reasoning_effort=params["reasoning_effort"],
        ).strip().upper()
        for label in ("CHIT_CHAT", "LEARNING_ADVICE", "DOMAIN_RAG"):
            if label in result:
                return label
    except Exception:
        logger.warning("Intent classification failed, defaulting to DOMAIN_RAG")

    return "DOMAIN_RAG"


def _generate_learning_advice_response(
    course_title: str,
    topic_title: str,
    message: str,
    *,
    topic_info: dict | None = None,
    course_data: dict | None = None,
) -> str:
    """学習相談・メタ質問・学習開始への応答を生成する（ルート②: ナビゲーター）。

    コース全体の構造と現在のトピックをベースに、学習アドバイスや導入メッセージを提供する。
    """
    params = get_llm_params("standard")

    prerequisites: list[str] = []
    concepts: list[str] = []

    if topic_info:
        for p in topic_info.get("prerequisites", []):
            name = p.get("name", p) if isinstance(p, dict) else str(p)
            if name:
                prerequisites.append(name)

    if course_data:
        for c in course_data.get("concepts", []):
            name = c.get("name", c) if isinstance(c, dict) else str(c)
            if name:
                concepts.append(name)

    # コース全体のトピック一覧（最大10件）
    topics_block = ""
    if course_data:
        topics = course_topics(course_data)
        if topics:
            topics_list = "\n".join(
                f"  - {t.get('title', t.get('id', ''))}" for t in topics[:10]
            )
            topics_block = f"■ コースのトピック一覧:\n{topics_list}\n\n"

    concepts_block = ""
    if concepts:
        concepts_block = "■ このトピックで習得すべき主要概念:\n" + "\n".join(
            f"  - {c}" for c in concepts
        ) + "\n\n"

    prereqs_block = ""
    if prerequisites:
        prereqs_block = "■ このトピックに必要な前提知識:\n" + "\n".join(
            f"  - {p}" for p in prerequisites
        ) + "\n\n"

    response_persona = course_persona_settings(course_data).get("response_persona") if course_data else ""
    persona_instruction = persona_prompt(response_persona, target="response")
    persona_block = f"■ 口調設定:\n{persona_instruction}\n\n" if persona_instruction else ""

    prompt = (
        f"あなたは「{course_title}」の学習をサポートするナビゲーター教授です。\n"
        f"学生は現在「{topic_title}」のトピックを学習しています。\n\n"
        f"{persona_block}"
        f"{topics_block}"
        f"{concepts_block}"
        f"{prereqs_block}"
        f"学生からのメッセージ: {message}\n\n"
        "コース全体の構造と学生の現在位置を踏まえ、以下の構成で回答してください:\n"
        "1. 【歓迎と目標】このトピックで学ぶことの全体像と、最終的な学習目標を簡潔に説明する。\n"
        "2. 【構成要素】習得すべき主要な概念をリストアップする。\n"
        "3. 【前提知識の確認】このトピックを学ぶために必要な前提知識を提示する。\n"
        "4. 【次の一歩】最後に、学生がこの後どう進めばよいかを1〜2文で促す。\n\n"
        "※注意: ここでは具体的な解説（数式展開など）はまだ行わないこと。\n"
        "※注意: 選択肢ボタンはシステムが自動付与するので、本文に [ ] 形式のボタン記法は書かないこと。"
    )

    try:
        return generate_text(
            messages=[{"role": "user", "content": prompt}],
            model=params["model"],
            reasoning_effort=params["reasoning_effort"],
        )
    except Exception:
        logger.warning("Learning advice response LLM call failed, returning fallback")
        return (
            f"「{course_title}」の学習サポートへようこそ！\n\n"
            f"これから「{topic_title}」の学習を始めます。\n\n"
            + (f"**習得すべき主要概念:** {', '.join(concepts)}\n\n" if concepts else "")
            + (f"**必要な前提知識:** {', '.join(prerequisites)}\n\n" if prerequisites else "")
            + "下の選択肢から、前提知識の確認に進むか、最初の概念の説明に進むかを選んでください。"
        )


def _get_integrated_tutor_system_prompt(domain: str, response_persona: str | None = None) -> str:
    """知識統合型チューターのシステムプロンプトを生成する。

    Parameters
    ----------
    domain : str
        コースの専門分野。空文字の場合は「このコースの専門分野」でフォールバック。
    """
    domain_label = domain.strip() if domain.strip() else "このコースの専門分野"
    persona_instruction = persona_prompt(response_persona, target="response")
    persona_block = f"\n\n**口調設定:**\n{persona_instruction}" if persona_instruction else ""
    return f"""あなたは{domain_label}の学習をサポートする「親切な専属チューター」です。
学生の疑問に対して、手元の教材とあなた自身の専門知識をシームレスに統合して、即座に分かりやすい解説を提供することが使命です。

**チューターとしての役割と回答ルール:**
1. 【知識の統合】提供される「教材からのコンテキスト」を最優先で参照してください。ただし、コンテキストに十分な情報がない場合は、突き放したり「教材にありません」と謝罪したりせず、あなたの一般的な学術知識を用いて自然に解説を補完してください。
2. 【自然な対話】回答の冒頭に「【基礎知識の補足】」のようなシステム的な警告ラベルは絶対に付けないでください。
3. 【誤解の訂正】学生に誤解がある場合は、「訂正：」という冷たい表現は避け、「この点については、〇〇と考えるとより正確です」のように教育的配慮を持って導いてください。
4. 【解説の深さ】前提知識の確認で長々と引き留めず、まずは直球で疑問に答えてください。必要に応じて数式（LaTeX）や具体例を交えてください。
5. 【ドリルダウン】回答の末尾に、関連して深掘りできそうなトピックを `[〇〇について詳しく聞く]` の形式で1〜2つ提示してください。
   ただし、クリック可能なボタンとして提示したい場合は必ず `[ACTION_BUTTON: 〇〇について聞く]` の形式を使ってください。

**フォーマット要件:**
- 数式は必ず LaTeX 記法で記述（インラインは $...$、ディスプレイは $$...$$）
- 教材を参照した場合は、コンテキストに付された番号付き出典マーカー `[出典1]` `[出典2]` … を本文に自然に挿入して言及すること。番号は提示された出典に対応させ、独自の番号や『書籍名』形式は使わないこと。{persona_block}"""


def _get_casual_teacher_system_prompt(domain: str, response_persona: str | None = None) -> str:
    """カジュアル対話モード（気軽に話せる先生）のシステムプロンプトを生成する。

    ハンズフリー音声会話が主用途のため、短い会話調・記号なしの応答を強制する。
    根拠の一線（教材コンテキスト優先・断定回避）はチューターモードと同じに保つ。
    """
    domain_label = domain.strip() if domain.strip() else "このコースの専門分野"
    persona_instruction = persona_prompt(response_persona, target="response")
    persona_block = f"\n\n**口調設定:**\n{persona_instruction}" if persona_instruction else ""
    return f"""あなたは{domain_label}が大好きで、学生と雑談するのが楽しみな「気軽に話せる先生」です。
研究室の廊下やゼミ後の立ち話のように、教材で扱っている題材について肩の力を抜いて一緒に面白がってください。

**会話のルール:**
1. 【会話調】音声で読み上げられます。1回の応答は2〜4文の短い話し言葉にしてください。
   箇条書き・見出し・記号・絵文字は使わないでください。
2. 【一緒に面白がる】採点や訂正を急がず、学生の言葉をまず受け止めてください。
   「たしかにそう見えるよね」「いいところに気づいたね」のような相づちから入って構いません。
3. 【聞き返す】ときどき「きみはどう思う?」「どこが引っかかった?」と軽く聞き返し、
   学生が自分の言葉で話す余地を残してください。毎回はしつこいので2〜3往復に1回程度。
4. 【根拠は正直に】提供される「教材からのコンテキスト」があればそれに沿って話してください。
   教材に無い話題は、想像や一般論であることが伝わる言い方（「たぶん」「一般には」）で話してください。
5. 【出さないもの】数式の羅列・LaTeX・出典番号マーカー・`[ACTION_BUTTON: ...]` などの
   システム記法は一切出力しないでください。数式が必要なら言葉で言い換えてください。{persona_block}"""


def _learner_selected_anchor(body: LearningChatRequest) -> dict | None:
    """発話時の明示アンカー（構造帰属・方法A）を非LLMで構築する。無ければ None。

    「どこ（anchor）」はこの操作で確定するが「どう（doubt_type）」までは分からないため
    unclassified のまま保持する（P4。方法B/C が後から補い得る）。
    """
    if body.element_id:
        atype = anchor_type_for_element(body.element_type)
        # 出典系（reference/citation→chunk）はタップ元チャンクをアンカーにする
        anchor_id = body.chunk_id if (atype == "chunk" and body.chunk_id) else body.element_id
        return build_anchor_payload(
            anchor_type=atype,
            anchor_id=anchor_id,
            anchor_label=body.element_label or body.element_id,
            doubt_type="unclassified",
            attribution_source=ATTRIBUTION_LEARNER_SELECTED,
            evidence_quote="",
            reason="element_tap",
            confidence=1.0,
        )
    sel = (body.selection_text or "").strip()
    if sel:
        seg = body.selection_segment_id
        return build_anchor_payload(
            anchor_type="segment",
            anchor_id=f"seg_{int(seg)}" if seg is not None else "",
            anchor_label=(sel[:40] + "…") if len(sel) > 40 else sel,
            doubt_type="unclassified",
            attribution_source=ATTRIBUTION_LEARNER_SELECTED,
            evidence_quote=sel,
            reason="text_selection",
            confidence=1.0,
        )
    return None


# 方法C の1タップ選択肢（unclassified は「その他」として提示しない — 未選択のまま
# 閉じれば unclassified が保たれる）
_ANCHOR_CONFIRM_DOUBT_OPTIONS = [
    {"doubt_type": d, "label": DOUBT_TYPE_LABELS[d]}
    for d in ("definition", "justification_gap", "premise", "prior_conflict", "scope", "connection")
]


def _generate_graph_element_explanation(
    *,
    user_id: str,
    course_id: str,
    topic_id: str,
    course_title: str,
    topic_title: str,
    course_data: dict,
    body: LearningChatRequest,
) -> LearningChatResponse:
    """グラフ要素サジェストのクリックを、通常チャットとは独立して処理する。"""
    if not body.chunk_id or not body.element_id:
        raise HTTPException(status_code=400, detail="EXPLAIN_GRAPH_ELEMENT requires chunk_id and element_id")

    context = get_graph_element_context(
        course_data,
        body.chunk_id,
        body.element_id,
        body.element_type,
        body.element_label,
    )
    if not context:
        raise HTTPException(status_code=404, detail="Chunk not found")

    element_label = context.get("element_label") or body.element_label or body.element_id
    instructor_id = context.get("instructor_id")
    material_id = context.get("material_id")
    source_title = context.get("source_title") or "教材"
    user_message = body.message or f"{element_label}を説明"

    record_student_stumble_event(
        instructor_id=instructor_id,
        student_id=user_id,
        course_id=course_id,
        material_id=material_id,
        chunk_id=body.chunk_id,
        element_id=body.element_id,
        element_label=element_label,
        event_type="clicked_explain",
        user_message=user_message,
    )

    graph_description = (context.get("graph_description") or "").strip()
    related_chunks = context.get("related_chunks") or []
    target_formula = context.get("target_formula") or {}
    target_formula_latex = str(target_formula.get("latex") or "").strip() if isinstance(target_formula, dict) else ""

    if graph_description:
        answer = (
            f"**{element_label}** について説明します。\n\n"
            f"{graph_description}\n\n"
            f"現在のチャンクでは、この要素が周辺の議論を理解するための足場になります。"
            f"[出典: 『{source_title}』]"
        )
    else:
        if not related_chunks:
            record_student_stumble_event(
                instructor_id=instructor_id,
                student_id=user_id,
                course_id=course_id,
                material_id=material_id,
                chunk_id=body.chunk_id,
                element_id=body.element_id,
                element_label=element_label,
                event_type="explanation_missing",
                user_message=user_message,
            )

        related_block = "\n\n".join(
            f"[出典: 『{r.get('source_title') or source_title}』]\n{r.get('text', '')[:1200]}"
            for r in related_chunks[:3]
        )
        personal = get_personal_layer(user_id, course_id)
        recent_history = "\n".join(
            f"{h.get('role')}: {str(h.get('content', ''))[:240]}"
            for h in (body.history or [])[-6:]
        )
        response_persona = course_persona_settings(course_data)["response_persona"]
        persona_instruction = persona_prompt(response_persona, target="response")
        params = get_llm_params("standard")
        prompt = (
            f"あなたは「{course_title}」の学習を支援するチューターです。\n"
            f"現在のトピック: {topic_title}\n"
            f"説明対象: {element_label} ({context.get('element_type')})\n\n"
            + (f"口調設定:\n{persona_instruction}\n\n" if persona_instruction else "")
            + (
                f"対象数式（この式を必ずそのまま使って説明すること）:\n"
                f"$${target_formula_latex}$$\n\n"
                if target_formula_latex else ""
            )
            + f"現在表示中のチャンク:\n{context.get('chunk_text', '')[:2400]}\n\n"
            f"関連教材:\n{related_block or '明示的な説明は見つかりませんでした。'}\n\n"
            f"学習者の個人レイヤー:\n{personal}\n\n"
            f"直近の会話:\n{recent_history}\n\n"
            "上記を踏まえ、学生に合わせて説明してください。"
            "既存教材に明示的な説明がない場合は、現在のチャンクの文脈から補って説明してください。"
            "数式が関係する場合は、インライン数式は必ず $...$、別行数式は必ず $$...$$ で囲んでください。"
            "裸の \\mathcal や \\frac など、区切り文字のないLaTeXコマンドは出力しないでください。"
            "[[FORMULA_0]] のようなプレースホルダー名は説明文に出さないでください。"
            "最後に短い確認文を1つ添えてください。"
        )
        answer = generate_text(
            messages=[{"role": "user", "content": prompt}],
            model=params["model"],
            reasoning_effort=params["reasoning_effort"],
            temperature=0.3,
        )
        if target_formula_latex:
            formula_id = str(target_formula.get("id") or "").strip() if isinstance(target_formula, dict) else ""
            if formula_id:
                answer = answer.replace(formula_id, f"${target_formula_latex}$")
            if target_formula_latex not in answer:
                answer = f"対象の数式は次の式です。\n\n$${target_formula_latex}$$\n\n" + answer
        record_student_stumble_event(
            instructor_id=instructor_id,
            student_id=user_id,
            course_id=course_id,
            material_id=material_id,
            chunk_id=body.chunk_id,
            element_id=body.element_id,
            element_label=element_label,
            event_type="generated_for_student",
            user_message=user_message,
            generated_explanation=answer[:4000],
        )

    persist_chat_history(
        user_id, course_id, topic_id,
        body.history, user_message, answer,
    )
    return LearningChatResponse(answer=answer, course_update=None)


def _topic_student_material(topic: dict) -> str:
    material = topic.get("student_material")
    if isinstance(material, dict):
        text = str(material.get("source_text") or "").strip()
        if text:
            return text
    return str(topic.get("content") or topic.get("summary") or "").strip()


def _normalize_check_question_item(item: object) -> dict:
    if isinstance(item, dict):
        question = str(item.get("question") or item.get("text") or "").strip()
        requirements = item.get("answer_requirements") or item.get("required_elements") or []
        if isinstance(requirements, str):
            requirements = [line.strip() for line in requirements.splitlines() if line.strip()]
        elif isinstance(requirements, list):
            requirements = [str(v).strip() for v in requirements if str(v).strip()]
        else:
            requirements = []
        return {
            "question": question,
            "model_answer": str(item.get("model_answer") or item.get("answer") or "").strip(),
            "answer_requirements": requirements,
            "explanation": str(item.get("explanation") or item.get("rationale") or "").strip(),
        }
    return {
        "question": str(item or "").strip(),
        "model_answer": "",
        "answer_requirements": [],
        "explanation": "",
    }


def _select_check_question(topic: dict, requested_question: str = "", request_item: dict | None = None) -> dict:
    if request_item:
        normalized = _normalize_check_question_item(request_item)
        if normalized.get("question"):
            return normalized
    questions = topic.get("check_questions") or topic.get("assessment_prompts") or []
    normalized_questions = [_normalize_check_question_item(item) for item in questions]
    requested = (requested_question or "").strip()
    if requested:
        for item in normalized_questions:
            if item.get("question") == requested:
                return item
        return _normalize_check_question_item(requested)
    for item in normalized_questions:
        if item.get("question"):
            return item
    return _normalize_check_question_item("このセクションの要点を説明してください。")


def _topic_formulas_from_content_blocks(topic: dict) -> list[dict]:
    """topic.content_blocks の equations から、UIの数式埋め込み解決用 formulas を作る。

    未解決の数式 fix: LaTeX が無くても reading(plain_text) や原文(raw_text) があれば
    数式項目として渡す（フロントは latex → plain_text → raw_text の順にフォールバック
    描画する）。これで `![[equation:id]]` 埋め込みが「未解決」にならずに済む。描画材料が
    一切無い項目だけを除外する。
    """
    formulas: list[dict] = []
    for block in topic.get("content_blocks") or []:
        if not isinstance(block, dict) or block.get("type") != "equations":
            continue
        for item in block.get("items") or []:
            if not isinstance(item, dict):
                continue
            latex = item.get("latex") or ""
            plain_text = item.get("plain_text") or ""
            raw_text = item.get("raw_text") or ""
            if not (latex or plain_text or raw_text):
                continue
            formulas.append({
                "id": item.get("equation_id") or f"TOPIC_FORMULA_{len(formulas)}",
                "latex": latex,
                "label": item.get("label") or "",
                "plain_text": plain_text,
                "raw_text": raw_text,
                "is_display": True,
            })
    return formulas


@router.get(
    "/courses/{course_id}/topics/{topic_id}/material",
    response_model=TopicMaterialResponse,
)
def get_topic_material(
    course_id: str,
    topic_id: str,
    current_user: dict = Depends(_get_current_user),
) -> TopicMaterialResponse:
    """トピック本文を受講画面用の教材として返す。

    受講体験の主ソースは ``learning_courses.data.topics[].content``。
    PDF復元チャンクは、topic content が未生成の場合だけ後方互換のフォールバックに使う。
    """
    course_data = get_course_data(current_user["id"], course_id)
    if not course_data:
        raise HTTPException(status_code=404, detail="Course not found")

    topics = course_topics(course_data)
    topic_index = None
    topic = None
    for i, t in enumerate(topics):
        if t.get("id") == topic_id:
            topic_index = i
            topic = t
            break
    if topic_index is None:
        raise HTTPException(status_code=404, detail="Topic not found")

    topic_text = _topic_student_material(topic or {})
    if topic_text.strip():
        formulas = _topic_formulas_from_content_blocks(topic or {})
        chunks = [ChunkContent(
            id=f"topic:{topic_id}",
            text=topic_text,
            chunk_index=topic_index,
            formulas=formulas,
            chapter=None,
            section=(topic or {}).get("title"),
            material_id=None,
            graph_mentions=[],
        )]
        return TopicMaterialResponse(topic_id=topic_id, chunks=chunks)

    all_chunks = get_course_chunks_ordered(course_data)
    if topic_index < len(all_chunks):
        raw = all_chunks[topic_index]
        chunks = [ChunkContent(
            id=raw["id"],
            text=raw["text"],
            chunk_index=raw["chunk_index"],
            formulas=raw.get("formulas", []),
            chapter=raw["chapter"],
            section=raw["section"],
            material_id=raw.get("material_id"),
            graph_mentions=raw.get("graph_mentions", []),
        )]
    else:
        chunks = []

    return TopicMaterialResponse(topic_id=topic_id, chunks=chunks)


@router.post(
    "/courses/{course_id}/topics/{topic_id}/check",
    response_model=LearningCheckQuestionResponse,
)
def check_topic_understanding(
    course_id: str,
    topic_id: str,
    body: LearningCheckQuestionRequest,
    current_user: dict = Depends(_get_current_user),
) -> LearningCheckQuestionResponse:
    """次セクションへ進む前の確認問題を採点し、未理解ならつまづきとして記録する。"""
    course_data = get_course_data(current_user["id"], course_id)
    if not course_data:
        raise HTTPException(status_code=404, detail="Course not found")

    topic = find_course_topic(course_data, topic_id)
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found")

    check_item = _select_check_question(topic, body.question, body.check_question)
    question = check_item.get("question") or "このセクションの要点を説明してください。"
    expected_model_answer = str(check_item.get("model_answer") or "").strip()
    answer_requirements = [
        str(v).strip() for v in (check_item.get("answer_requirements") or [])
        if str(v).strip()
    ]
    explanation = str(check_item.get("explanation") or "").strip()

    material_text = _topic_student_material(topic)
    requirements_text = "\n".join(f"- {r}" for r in answer_requirements) or "(未設定)"
    params = get_llm_params("fast")
    prompt = (
        "あなたは確認問題を採点する大学教員です。JSONのみを返してください。\n"
        "形式: {\"passed\": true/false, \"feedback\": \"短い講評\", \"model_answer\": \"模範解答\", \"explanation\": \"必要なら解説\"}\n\n"
        f"コース: {_course_title(course_data, default=course_id)}\n"
        f"セクション: {topic.get('title', topic_id)}\n"
        f"教材:\n{material_text[:5000]}\n\n"
        f"確認問題: {question}\n"
        f"模範解答（設定済みの場合はこれを基準にする）:\n{expected_model_answer or '(未設定)'}\n\n"
        f"回答に必要な要素:\n{requirements_text}\n\n"
        f"解説（設定済みの場合はフィードバックに反映する）:\n{explanation or '(未設定)'}\n\n"
        f"受講者の回答: {body.answer}\n\n"
        "判定基準: 回答に必要な要素を概ね満たし、自分の言葉で説明できていれば passed=true。"
        "核心が抜けている、逆に理解している、空欄に近い場合は false。"
    )

    parsed: dict = {}
    try:
        raw = generate_text(
            messages=[{"role": "user", "content": prompt}],
            model=params["model"],
            reasoning_effort=params["reasoning_effort"],
            temperature=0.1,
        )
        import json
        import re
        match = re.search(r"\{[\s\S]*\}", raw or "")
        parsed = json.loads(match.group(0) if match else raw)
    except Exception:
        logger.warning("Check question grading failed; using conservative fallback", exc_info=True)
        passed = len((body.answer or "").strip()) >= 40
        parsed = {
            "passed": passed,
            "feedback": "回答の具体性をもとに暫定判定しました。",
            "model_answer": expected_model_answer or material_text[:800],
            "explanation": explanation,
        }

    passed = bool(parsed.get("passed"))
    feedback = str(parsed.get("feedback") or "")
    model_answer = str(parsed.get("model_answer") or expected_model_answer or material_text[:800])
    response_explanation = str(parsed.get("explanation") or explanation or "")

    if not passed:
        instructor_id = None
        session = _pg_session()
        try:
            row = session.execute(
                sa_text("SELECT user_id FROM learning_courses WHERE id = :course_id LIMIT 1"),
                {"course_id": course_id},
            ).fetchone()
            instructor_id = str(row[0]) if row and row[0] else None
        finally:
            session.close()
        record_student_stumble_event(
            instructor_id=instructor_id,
            student_id=current_user["id"],
            course_id=course_id,
            material_id=None,
            chunk_id=None,
            element_id=topic_id,
            element_label=topic.get("title", topic_id),
            event_type="misconception",
            user_message=f"確認問題: {question}\n回答: {body.answer}",
            generated_explanation=model_answer[:4000],
        )

    return LearningCheckQuestionResponse(
        passed=passed,
        feedback=feedback,
        model_answer=model_answer,
        answer_requirements=answer_requirements,
        explanation=response_explanation,
    )


@router.get(
    "/courses/{course_id}/topics/{topic_id}/chat",
    response_model=LearningChatHistoryResponse,
)
def get_chat_history(
    course_id: str,
    topic_id: str,
    current_user: dict = Depends(_get_current_user),
) -> LearningChatHistoryResponse:
    """トピックのチャット履歴を返す。"""
    session = _pg_session()
    try:
        record = session.execute(
            sa_text("""
                SELECT history FROM learning_chat_history
                WHERE user_id = CAST(:user_id AS uuid) AND course_id = :course_id AND topic_id = :topic_id
                LIMIT 1
            """),
            {"user_id": current_user["id"], "course_id": course_id, "topic_id": topic_id},
        ).fetchone()
    finally:
        session.close()

    if not record or not record[0]:
        return LearningChatHistoryResponse(history=[])

    history = record[0] if isinstance(record[0], list) else []
    return LearningChatHistoryResponse(history=history)


@router.delete(
    "/courses/{course_id}/topics/{topic_id}/chat",
)
def delete_chat_history(
    course_id: str,
    topic_id: str,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """受講者本人のトピック別チャット履歴だけを削除する。

    質疑応答から派生した個人レイヤー、誤解記録、未回答ログなどは削除しない。
    """
    course_data = get_course_data(current_user["id"], course_id)
    if not course_data:
        raise HTTPException(status_code=404, detail="Course not found")

    session = _pg_session()
    try:
        session.execute(
            sa_text("""
                DELETE FROM learning_chat_history
                WHERE user_id = CAST(:user_id AS uuid)
                  AND course_id = :course_id
                  AND topic_id = :topic_id
            """),
            {"user_id": current_user["id"], "course_id": course_id, "topic_id": topic_id},
        )
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("Failed to delete learning chat history for user=%s topic=%s", current_user["id"], topic_id)
        raise HTTPException(status_code=500, detail="Failed to delete chat history")
    finally:
        session.close()

    return {"status": "deleted"}


@router.delete(
    "/courses/{course_id}/topics/{topic_id}/chat/messages/{message_id}",
)
def delete_chat_message_from(
    course_id: str,
    topic_id: str,
    message_id: str,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """機能3（削除）: 指定メッセージ以降の往復を本人の履歴から取り除く。

    書き直しと同じ ``truncate_chat_and_supersede`` を使い、当該メッセージ・その回答・以降の
    往復を履歴から削除し、派生 interest_traces を status='superseded' にする（保持はする。P4）。
    再送は行わない（純粋な削除）。
    """
    course_data = get_course_data(current_user["id"], course_id)
    if not course_data:
        raise HTTPException(status_code=404, detail="Course not found")

    try:
        result = truncate_chat_and_supersede(
            current_user["id"], course_id, topic_id, message_id
        )
    except Exception:
        logger.exception(
            "Failed to delete chat message for user=%s topic=%s msg=%s",
            current_user["id"], topic_id, message_id,
        )
        raise HTTPException(status_code=500, detail="Failed to delete chat message")

    if result is None:
        raise HTTPException(status_code=404, detail="Message not found")

    return {"status": "deleted", "removed_count": result["removed_count"]}


# ---------------------------------------------------------------------------
# 分野の地図 (Issue C-2/C-3) — ↗ アクションの型付き処理
# ---------------------------------------------------------------------------

def _atlas_safe_int(value, default: int = 1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _atlas_step_dicts(raw) -> list[dict]:
    """クライアント添付の related / juxtapose を検証済みの dict 列に正規化する。"""
    out: list[dict] = []
    if isinstance(raw, list):
        for e in raw:
            if isinstance(e, dict):
                out.append({
                    "node_id": str(e.get("node_id") or ""),
                    "label": str(e.get("label") or ""),
                    "status": str(e.get("status") or ""),
                    "pill": str(e.get("pill") or ""),
                })
    return out


def _atlas_attribution(ctx: dict) -> dict:
    """帰属つき記録に焼き込む構造化ペイロード (自由文のみに依存しない)。"""
    return {
        "node_id": str(ctx.get("node_id") or ""),
        "level": _atlas_safe_int(ctx.get("level")),
        "skeleton_version": str(ctx.get("skeleton_version") or ""),
        "action": str(ctx.get("action") or ""),
        "node_label": str(ctx.get("node_label") or ""),
    }


def _atlas_topic_attribution(course_data: dict, topic_info: dict | None) -> dict | None:
    """通常学習 (地図アクション以外) の往復から topic → 骨格概念を解決し、個人層の
    「いまここ」を動かすための atlas 帰属を返す (gap1)。

    cheap path: 明示 binding + ラベル一致のみで解決する (corpus 経路は使わない)。
    骨格が無い / 対応概念が引けない場合は None。best-effort — 例外はチャットを止めない。
    """
    if not isinstance(topic_info, dict):
        return None
    try:
        from core import atlas as atlas_module
        from core import atlas_state
        from core import atlas_store

        session = _pg_session()
        try:
            cartridge_id = atlas_state.resolve_course_cartridge(session, course_data)
            if not cartridge_id:
                return None
            # migration 027: 骨格は DB 凍結版が正本 (同梱ファイルはフォールバック)
            skeleton = atlas_store.load_learner_skeleton(cartridge_id, session)
        finally:
            session.close()
        if skeleton is None:
            return None
        node_id = atlas_module.match_topic_to_concept(topic_info, skeleton)
        if not node_id:
            return None
        return {
            "node_id": node_id,
            "level": 1,
            "skeleton_version": skeleton.version,
            "action": "study",
            "node_label": str(topic_info.get("title") or ""),
        }
    except Exception:  # noqa: BLE001
        logger.warning("atlas topic attribution failed", exc_info=True)
        return None


def _atlas_action_response(
    user_id: str,
    course_id: str,
    topic_id: str,
    body: LearningChatRequest,
    course_data: dict,
    ctx: dict,
) -> LearningChatResponse | None:
    """地図の ↗ アクションのうち、決定論的に応答するもの (mind / learn) を処理する。

    - mind (気になる ↗): 学習者本人が宣言した違和感。既存 tension 記録経路
      (interest_traces kind='tension') に帰属つきで記録する。本人発の宣言なので
      candidate ではなく open (P1: 違和感を生成するのは人間 — ここでは人間が押している)。
    - learn (ここから学ぶ ↗): 学習パス提案カード (§8) を決定論的に生成して返す。
      リアルタイム LLM 生成はしない。
    - evid ほかは None を返し、通常の RAG フローに流す (帰属は呼び出し側で焼き込む)。
    """
    action = str(ctx.get("action") or "")
    node_label = str(ctx.get("node_label") or ctx.get("node_id") or "")
    attribution = _atlas_attribution(ctx)

    if action == "mind":
        record_interest_trace(
            user_id, course_id, topic_id,
            kind="tension",
            text=body.message,
            context_label=node_label,
            extra_payload={"atlas": attribution, "origin": "atlas_mind"},
            status="open",
        )
        answer = (
            f"「{node_label}」への引っかかりを、あなたの違和感として帰属つきで記録しました。\n"
            "記録は「問いの軌跡」に残ります。言葉にできるようになったら、"
            "いつでも自分の言葉で書き直せます。"
        )
        persist_chat_history(
            user_id, course_id, topic_id,
            body.history, body.message, answer,
            user_message_id=body.message_id or None,
        )
        return LearningChatResponse(answer=answer, course_update=None)

    if action == "learn":
        interest_view = get_interest_traces(user_id, course_id, topic_id)
        card = build_learning_path_card(
            node_id=str(ctx.get("node_id") or ""),
            node_label=node_label,
            level=_atlas_safe_int(ctx.get("level")),
            skeleton_version=str(ctx.get("skeleton_version") or ""),
            node_status=str(ctx.get("node_status") or ""),
            node_pill=str(ctx.get("node_pill") or ""),
            related=_atlas_step_dicts(ctx.get("related")),
            juxtapose=_atlas_step_dicts(ctx.get("juxtapose")),
            course_topics=course_topics(course_data),
            interest_traces=(interest_view or {}).get("traces") or [],
        )
        answer = (
            f"「{node_label}」からの学習パスの候補です。"
            "各ステップに出所（教材 / AI一般知識）と台帳の状態を添えています。"
        )
        record_interest_trace(
            user_id, course_id, topic_id,
            kind="question",
            text=body.message,
            context_label=node_label,
            extra_payload={"atlas": attribution, "atlas_path_proposed": True},
        )
        persist_chat_history(
            user_id, course_id, topic_id,
            body.history, body.message, answer,
            user_message_id=body.message_id or None,
        )
        return LearningChatResponse(answer=answer, course_update=None, atlas_path_card=card)

    return None


@router.post(
    "/courses/{course_id}/topics/{topic_id}/chat",
    response_model=LearningChatResponse,
)
def learning_chat(
    course_id: str,
    topic_id: str,
    body: LearningChatRequest,
    current_user: dict = Depends(_get_current_user),
) -> LearningChatResponse:
    """RAG統合された学習チャットエンドポイント（意図分類ルーティング付き）。"""
    # 1. コースデータを取得
    course_data = get_course_data(current_user["id"], course_id)
    if not course_data:
        raise HTTPException(status_code=404, detail="Course not found")

    # 機能3（書き直し）: replace_message_id 指定時は、その往復以降をサーバ正本の履歴から
    # 取り除き、派生 interest_traces を supersede してから、message を同じ位置から再処理する。
    # サーバの履歴を正本にするため、切り詰め済みの履歴で body.history を上書きし、
    # 以降の文脈構築・永続化（persist_chat_history が全体を UPSERT）を一貫させる。
    if body.replace_message_id:
        _trunc = truncate_chat_and_supersede(
            current_user["id"], course_id, topic_id, body.replace_message_id
        )
        if _trunc is not None:
            body.history = _trunc["truncated_history"]

    topic_info = find_course_topic(course_data, topic_id)
    topic_title = topic_info["title"] if topic_info else topic_id
    course_title = _course_title(course_data, default=course_id)
    # domain が未設定の場合は course_title にフォールバック
    domain = course_data.get("domain") or course_title
    response_persona = course_persona_settings(course_data)["response_persona"]
    support_agent = LearningSupportAgent(course_id, course_data)
    # L2 位置・復帰: クライアントが報告した現在位置（segment/scroll）を origin に取り込み、
    # 寄り道に入っても同じ位置へ正確に復帰できるようにする。
    _anchor = body.position_anchor or {}
    _seg = int(_anchor.get("segment_id") or 0)
    _scroll = int(_anchor.get("scroll_offset") or 0)
    support_origin = support_agent.origin_for_topic(
        topic_id, topic_info, segment_id=_seg, scroll_offset=_scroll
    )

    if body.support_action == "return_to_learning_path":
        result = support_agent.return_to_path_result((body.support_context or {}).get("origin"))
        persist_chat_history(
            current_user["id"], course_id, topic_id,
            body.history, body.message, result.answer,
        )
        return LearningChatResponse(**result.model_dump(), course_update=None)

    # UIサジェスト由来の明示アクションは自然文の意図分類より優先する。
    if body.action == "EXPLAIN_GRAPH_ELEMENT":
        with usage_context("learning:chat", user_id=current_user["id"], course_id=course_id):
            graph_response = _generate_graph_element_explanation(
                user_id=current_user["id"],
                course_id=course_id,
                topic_id=topic_id,
                course_title=course_title,
                topic_title=topic_title,
                course_data=course_data,
                body=body,
            )
        # グラフ要素の説明は常に detour（origin=現在アンカー）として扱い、
        # どの入口由来でも「学習パスに戻る」を提示する。
        clean_answer, inline_actions = extract_inline_actions(graph_response.answer)
        result = support_agent.with_learning_actions(
            answer=clean_answer,
            mode="detail_explanation",
            origin=support_origin,
            include_continue=False,
            extra_actions=inline_actions,
        )
        # 構造帰属（方法A）: 要素タップは ground truth のアンカー。問いとして
        # learner_selected 帰属付きで記録する（同期・非LLM）。
        _tap_anchor = _learner_selected_anchor(body)
        record_interest_trace(
            current_user["id"], course_id, topic_id,
            kind="question",
            text=body.message or f"{body.element_label or body.element_id}について質問",
            context_label=" · ".join(
                [s for s in [support_origin.chapter_title, topic_title] if s]
            ),
            extra_payload={
                "position_anchor": build_position_anchor(topic_id, _seg, _scroll),
                "structure_anchor": _tap_anchor,
            } if _tap_anchor else None,
        )
        return LearningChatResponse(
            **result.model_dump(),
            course_update=graph_response.course_update,
            structure_anchor=_tap_anchor,
        )

    # カジュアル対話モード（気軽に話せる先生・ハンズフリー音声会話）:
    # 意図分類（雑談拒否）・前提知識ゲート・誤解検出をバイパスし、RAG検索と
    # tier 集約（根拠の一線）はそのまま通す。
    _is_casual = (body.intent_mode or "").strip() == "casual"

    # 分野の地図 (Issue C-2/C-3): ↗ アクションは型付きなので意図分類を経由しない。
    # mind / learn は決定論的に応答し、evid ほかは通常の RAG フローへ流す。
    _atlas_ctx = body.atlas_context if isinstance(body.atlas_context, dict) else None
    if _atlas_ctx:
        _atlas_response = _atlas_action_response(
            current_user["id"], course_id, topic_id, body, course_data, _atlas_ctx
        )
        if _atlas_response is not None:
            return _atlas_response

    # 2. 意図分類（Intent Routing）— UI ボタン由来の型付きアクションは分類を経由しない。
    with usage_context("learning:chat", user_id=current_user["id"], course_id=course_id):
        intent = None if (_is_casual or _atlas_ctx) else (
            _route_for_typed_action(body.support_action) or _classify_intent(body.message, course_title)
        )

    # ルート①: 雑談・無関係な質問 → 学習に関する質問を促す
    if intent == "CHIT_CHAT":
        chit_chat_answer = (
            "申し訳ありませんが、私は物理学の学習支援に特化したAIです。\n\n"
            "物理学・数学の概念についての質問や、学習の進め方についての相談でしたら、"
            "喜んでお答えします。学習に関する質問をぜひ聞かせてください！"
        )
        persist_chat_history(
            current_user["id"], course_id, topic_id,
            body.history, body.message, chit_chat_answer,
        )
        return LearningChatResponse(answer=chit_chat_answer, course_update=None)

    # ルート②: 学習相談・メタ質問 → RAGをスキップし、コース情報をベースにアドバイス
    if intent == "LEARNING_ADVICE":
        with usage_context("learning:chat", user_id=current_user["id"], course_id=course_id):
            advice_answer = _generate_learning_advice_response(
                course_title, topic_title, body.message,
                topic_info=topic_info, course_data=course_data,
            )
        advice_answer, inline_actions = extract_inline_actions(advice_answer)
        is_prereq = (
            body.support_action in _PREREQUISITE_ACTIONS
            or LearningSupportAgent.is_prerequisite_request(body.message)
        )
        if is_prereq:
            # 前提確認は detour（origin=現在アンカー）。復帰導線を必ず付ける。
            result = support_agent.with_learning_actions(
                answer=advice_answer,
                mode="prerequisite_review",
                origin=support_origin,
                extra_actions=inline_actions,
            )
            persist_chat_history(
                current_user["id"], course_id, topic_id,
                body.history, body.message, result.answer,
            )
            return LearningChatResponse(**result.model_dump(), course_update=None)
        # 学習開始・一般アドバイスはパス上（detour ではない）。前進アクションを型付きで提示。
        persist_chat_history(
            current_user["id"], course_id, topic_id,
            body.history, body.message, advice_answer,
        )
        first_concept = ""
        for _c in (course_data.get("concepts") or []):
            _name = _c.get("name", _c) if isinstance(_c, dict) else str(_c)
            if _name:
                first_concept = str(_name)
                break
        advice_next = support_agent.advice_actions(support_origin, topic_title, first_concept)
        return LearningChatResponse(
            answer=advice_answer,
            course_update=None,
            origin=asdict(support_origin),
            next_actions=[asdict(a) for a in (advice_next + inline_actions)],
        )

    # 3. Adaptive Routing: 前提知識の自動判定 (ルート③/④の前に実行)
    # casual モードでは会話を止めない（前提確認の逆質問ゲートを挟まない）。
    prerequisite_intervention = None if (_is_casual or _atlas_ctx) else check_prerequisites(
        current_user["id"], course_id, course_data, topic_title, body.message
    )
    if prerequisite_intervention:
        choice_actions = support_agent.prerequisite_choice_actions(
            prerequisite_intervention.get("first_prerequisite", "")
        )
        result = support_agent.with_learning_actions(
            answer=prerequisite_intervention["message"],
            mode="prerequisite_review",
            origin=support_origin,
            include_continue=False,
            extra_actions=choice_actions,
        )
        persist_chat_history(
            current_user["id"], course_id, topic_id,
            body.history, body.message, result.answer,
        )
        return LearningChatResponse(**result.model_dump(), course_update=None)

    # 4. RAG: システム全域のチャンクを検索し、コンテキストを構築
    #    search_chunks_with_metadata は各チャンクに tier(L1信頼性) を付与して返す。
    # このコース自身の教材（material_id）の集合。出典が「教材」か「別の資料」かの分類に使う。
    course_material_ids = set(course_source_material_ids(course_data))
    chunk_results = search_chunks_with_metadata(body.message, top_k=8)
    cited_chunks = []
    cited_sources: list[dict] = []  # L1: 文脈に採用した根拠の tier 一覧
    has_topic_material = False
    if topic_info:
        topic_material = _topic_student_material(topic_info)
        if topic_material:
            has_topic_material = True
            cited_chunks.append(f"[現在表示中の教材]\n{topic_material[:5000]}")
    for r in chunk_results:
        if r["score"] >= 0.30:
            _n = len(cited_sources) + 1  # 連番出典 [出典N]（cited_sources と 1 対 1）
            cited_chunks.append(f"[出典{_n}] 『{r['source_title']}』\n{r['text']}")
            _quote = (r.get("text") or "").strip().replace("\n", " ")
            _origin = "course_material" if r.get("material_id") in course_material_ids else "other_material"
            cited_sources.append({
                "index": _n,
                "chunk_id": r.get("id", ""),
                "source_title": r.get("source_title", "不明な教材"),
                "tier": r.get("tier", TIER_OUT_OF_SOURCE),
                "score": round(float(r.get("score", 0.0)), 3),
                "quote": (_quote[:80] + "…") if len(_quote) > 80 else _quote,
                "meta": r.get("source_file") or "",
                "origin": _origin,
            })

    # L1: 回答全体の格を最弱根拠へ安全側集約。採用根拠が無ければ未踏(out_of_source)。
    overall_tier = aggregate_overall_tier([s["tier"] for s in cited_sources])
    # 回答内容の出所分類（tier=教員承認状況とは別軸）:
    #   教材(このコース) > 別の資料 > 出典を追えないモデル生成、の優先度で決める。
    if has_topic_material or any(s["origin"] == "course_material" for s in cited_sources):
        content_grounding = "course_material"
    elif cited_sources:
        content_grounding = "other_material"
    else:
        content_grounding = "model_generated"

    if cited_chunks:
        context_block = "## 関連する教材のコンテキスト\n" + "\n---\n".join(cited_chunks)
    else:
        context_block = "※この質問に直接関連する教材セクションは見つかりませんでした。一般的な学術知識を用いて回答してください。"
        log_unanswered_query(current_user["id"], course_id, topic_id, body.message)

    # 5. 回答の生成（ルート統合）
    # L1 OutOfSourceGuard: 未踏なら生成前に順序ゲート（断定回避・予想促し）を system へ注入する。
    # casual モードでも guard の注入（振る舞い）は維持する — 気軽さ≠根拠の放棄。
    if _is_casual:
        _system_prompt = _get_casual_teacher_system_prompt(domain, response_persona)
    else:
        _system_prompt = _get_integrated_tutor_system_prompt(domain, response_persona)
    if overall_tier == TIER_OUT_OF_SOURCE:
        _system_prompt += "\n\n" + out_of_source_guard_instruction()
    messages: list[dict] = [
        {"role": "system", "content": _system_prompt},
        {"role": "user", "content": (
            f"コース: {course_title}\n"
            f"現在のトピック: {topic_title}\n\n"
            f"{context_block}\n\n"
            "上記のコンテキストを踏まえ（不足している場合は補完して）、以下の質問に答えてください。"
        )},
        {"role": "assistant", "content": (
            f"はい、「{topic_title}」についてですね。お答えします。"
        )},
    ]
    for turn in body.history:
        messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": body.message})

    _chat_feature = "learning:chat_casual" if _is_casual else "learning:chat"
    try:
        with usage_context(_chat_feature, user_id=current_user["id"], course_id=course_id):
            answer = generate_text(messages=messages, temperature=0.3)
    except Exception as exc:
        logger.exception("Learning chat LLM call failed for topic %s", topic_id)
        raise HTTPException(status_code=500, detail=f"Chat failed: {exc}") from exc

    # L1 OutOfSourceGuard: 未踏なら断定せず、根拠が弱い旨を先頭に明示する。
    # casual では可視プレフィックスのみ省略（音声で毎回読み上げると会話が壊れるため）。
    # tier 自体はレスポンスで返し、UI のバッジ表示で担保する。
    if overall_tier == TIER_OUT_OF_SOURCE and not _is_casual:
        answer = out_of_source_notice() + "\n\n" + answer

    # 誤解検出（マイルドな表現にも対応）。casual では採点・訂正の圧を掛けない。
    course_update = None
    if not _is_casual and topic_info and any(kw in answer for kw in ["訂正", "より正確です", "誤解"]):
        course_update = detect_and_record_misconception(
            current_user["id"], course_id, course_data, topic_id, body.message, answer
        )

    _persisted = persist_chat_history(
        current_user["id"], course_id, topic_id,
        body.history, body.message, answer,
        user_message_id=body.message_id or None,
    )
    # L2: クライアント報告の実位置で position_anchor を構築（mock ではない）。
    position_anchor = build_position_anchor(topic_id, _seg, _scroll)
    # L3 資産化: この往復を関心痕跡として安価に記録（LLM不使用）。
    # kind は既存シグナルから決定: 誤解検出→misconception / それ以外→question。
    _trace_kind = "misconception" if course_update else "question"
    _ctx_label = " · ".join([s for s in [support_origin.chapter_title, topic_title] if s])
    # Stage 0 TensionPrefilter（同期・非LLM・数ms）: ヘッジ/逆接マーカーと直近3往復の
    # 同語再訪でヒントを立てるだけ。LLM 分類は非同期バッチ（P6: 応答を遅延させない）。
    _recent_user_texts = [t.get("content", "") for t in body.history if t.get("role") == "user"][-3:]
    _tension_hint = judge_tension_hint(body.message, _recent_user_texts)
    # 構造帰属（方法A・同期・非LLM）: テキスト選択・要素タップの明示アンカーがあれば
    # learner_selected で確定記録する。無ければ方法B（非同期LLM）の帰属対象になる。
    _sel_anchor = _learner_selected_anchor(body)
    _trace_payload = {
        "overall_tier": overall_tier,
        "position_anchor": position_anchor,
        "tension_hint": _tension_hint,
        "casual": _is_casual,
        # 「この問いに戻る」で元の往復へジャンプするための逆引き（この問いを発した user メッセージ id）。
        "message_id": _persisted.get("user_message_id"),
        # 方法Bの帰属コンテキスト用: この回答が実際に引用したチャンク（上位3件）。
        "cited_chunk_ids": [s["chunk_id"] for s in cited_sources[:3] if s.get("chunk_id")],
        # 分野の地図由来の質問 (根拠を見る ↗ など) は帰属を構造化して焼き込む (Issue C-2)
        **({"atlas": _atlas_attribution(_atlas_ctx)} if _atlas_ctx else {}),
    }
    # gap1: 地図アクション由来でない通常学習でも、topic → 骨格概念を解決して atlas 帰属を
    # 焼き込む (個人層の「いまここ」を動かす)。地図由来 (_atlas_ctx) は上書きしない。
    if not _atlas_ctx:
        _topic_atlas = _atlas_topic_attribution(course_data, topic_info)
        if _topic_atlas:
            _trace_payload["atlas"] = _topic_atlas
    if _sel_anchor:
        _trace_payload["structure_anchor"] = _sel_anchor
    _trace_id = record_interest_trace(
        current_user["id"], course_id, topic_id,
        kind=_trace_kind,
        text=body.message,
        context_label=_ctx_label,
        extra_payload=_trace_payload,
    )
    # ヒント累積が閾値に達していればバックグラウンドで TensionMiningAgent を起動
    # （best-effort: 失敗してもチャット応答を止めない）。
    if _tension_hint:
        maybe_schedule_tension_mining(current_user["id"], course_id, topic_id)
    # 未帰属の問いが累積していればバックグラウンドで StructureAnchorAgent を起動
    # （方法B・非同期。明示アンカー付きの問いは最初から対象外）。
    if _trace_kind == "question" and not _sel_anchor:
        maybe_schedule_anchor_mining(current_user["id"], course_id, topic_id)
    # 方法C: 回答末尾の帰属確認プロンプト。tension_hint が立った往復か、明示アンカーは
    # あるが疑いの様相が未分類の往復に限り、セッション内上限までゲートして提示する（P7）。
    _anchor_confirm = None
    if (
        _trace_id
        and not _is_casual
        and (_tension_hint or _sel_anchor is not None)
        and check_and_count_confirm_prompt(current_user["id"], course_id, topic_id)
    ):
        _anchor_confirm = {
            "trace_id": _trace_id,
            "question": (body.message or "")[:120],
            "options": _ANCHOR_CONFIRM_DOUBT_OPTIONS,
        }
    # 本文中のドリルダウンマーカーは構造化アクションへ正規化する。
    clean_answer, inline_actions = extract_inline_actions(answer)

    # 送信意図で分岐（教材/チャット2区画 UX）:
    #  - on_path : 本筋維持。detour にせず origin/status_label を返さない（フロントは寄り道化しない）
    #  - casual  : 気軽に話せる先生。detour 化も復帰導線も付けない（会話を UI 遷移で邪魔しない）
    #  - explore : 従来どおり寄り道（detail_explanation, 復帰導線つき）
    if (body.intent_mode or "").strip() in ("on_path", "casual"):
        result = LearningSupportResult(
            answer=clean_answer,
            mode="normal",
            origin=None,
            next_actions=inline_actions,
        )
    else:
        result = support_agent.with_learning_actions(
            answer=clean_answer,
            mode="detail_explanation",
            origin=support_origin,
            include_continue=False,
            extra_actions=inline_actions,
        )
    # L1 tier・L2 位置ともに実データ化済み（Stage 1/2）。チャット応答に mock は含まれない。
    return LearningChatResponse(
        **result.model_dump(),
        course_update=course_update,
        sources=cited_sources,
        overall_tier=overall_tier,
        content_grounding=content_grounding,
        position_anchor=position_anchor,
        structure_anchor=_sel_anchor,
        anchor_confirm=_anchor_confirm,
        mock=False,
    )


@router.get("/courses/{course_id}/source-chunk/{chunk_id}")
def get_source_chunk_route(
    course_id: str,
    chunk_id: str,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """出典ポップアップ用: チャンク本文（数式プレースホルダ正規化済み）と数式を返す（L1）。"""
    passage = get_chunk_passage(chunk_id)
    if not passage:
        raise HTTPException(status_code=404, detail="Source chunk not found")
    return passage


@router.get("/courses/{course_id}/interest-traces")
def get_interest_traces_route(
    course_id: str,
    topic_id: str | None = None,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """問いの軌跡（未解決の問い・寄り道・誤答）を実データで返す（L3 資産化）。

    interest_traces から本人の痕跡を status 主役で返す。個人特定情報は含めない。
    """
    view = get_interest_traces(current_user["id"], course_id, topic_id)
    # 個人知識ネットワーク（わたしの地図）への表示除外フラグを付与する（UX proposal §6:
    # 地図には反映しない/地図に戻す）。既存の get_interest_traces は変更せず、
    # ここで1フィールド足すだけの最小変更にする。
    exclusion_flags = get_trace_map_exclusion_flags(current_user["id"], course_id)
    for trace in view.get("traces") or []:
        trace["map_excluded"] = exclusion_flags.get(trace["id"], False)
    return view


@router.post("/courses/{course_id}/interest-traces/{trace_id}/resolve")
def resolve_interest_trace_route(
    course_id: str,
    trace_id: str,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """痕跡を「解決済み」にする（本人の痕跡のみ）。"""
    ok = resolve_interest_trace(current_user["id"], trace_id, status="resolved")
    if not ok:
        raise HTTPException(status_code=404, detail="Trace not found")
    return {"ok": True, "trace_id": trace_id, "status": "resolved"}


class InternalizationRequest(BaseModel):
    reason: str = ""


@router.post("/courses/{course_id}/interest-traces/{trace_id}/internalize")
def internalize_interest_trace_route(
    course_id: str,
    trace_id: str,
    body: InternalizationRequest,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """「なぜ自分に重要か」(Internalization Prompt) を痕跡へ保存する（L3・内発的動機）。"""
    ok = record_internalization(current_user["id"], trace_id, body.reason)
    if not ok:
        raise HTTPException(status_code=400, detail="Could not save internalization")
    return {"ok": True, "trace_id": trace_id}


# ---------------------------------------------------------------------------
# 分野の地図 — 学習パス提案カードの三択記録 (Issue C-3)
# ---------------------------------------------------------------------------


class AtlasPathDecisionRequest(BaseModel):
    node_id: str = ""
    node_label: str = ""
    level: int = 1
    skeleton_version: str = ""
    decision: str = ""  # proceed | edit | dismiss | connect
    learner_text: str = ""
    steps: list[str] = []
    topic_id: str | None = None


# 三択 + 「自分で繋ぐ」→ interest_traces の status。
# 却下 (dismiss) も status='dismissed' で保持し、削除しない (情報を落とさない §1.2)。
# connect は本人の言葉での記録なので articulated。
_ATLAS_PATH_DECISIONS = {
    "proceed": ("resolved", "この糸で進む"),
    "edit": ("resolved", "編集する"),
    "dismiss": ("dismissed", "今はやめる"),
    "connect": ("articulated", "自分で繋ぐ"),
}


@router.post("/courses/{course_id}/atlas/path-decision")
def record_atlas_path_decision(
    course_id: str,
    body: AtlasPathDecisionRequest,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """学習パス提案カードの選択を interest_traces に帰属つきで記録する (Issue C-3)。

    [この糸で進む] [編集する] [今はやめる] と「自分で繋ぐ」入力のすべてを記録する。
    却下 (今はやめる) も記録する — 情報を落とさない。
    """
    if body.decision not in _ATLAS_PATH_DECISIONS:
        raise HTTPException(status_code=400, detail="Unknown decision")
    status, decision_label = _ATLAS_PATH_DECISIONS[body.decision]
    text = f"学習パス提案（「{body.node_label or body.node_id}」から）: {decision_label}"
    if body.decision == "connect" and body.learner_text.strip():
        # 「自分で繋ぐ」は本人の言葉をそのまま主文に残す (§1.2-5)
        text = body.learner_text.strip()
    record_interest_trace(
        current_user["id"], course_id, body.topic_id,
        kind="raw",
        text=text,
        context_label=body.node_label,
        extra_payload={
            "atlas": {
                "node_id": body.node_id,
                "level": body.level,
                "skeleton_version": body.skeleton_version,
                "action": "path_decision",
                "node_label": body.node_label,
            },
            "decision": body.decision,
            "path_steps": body.steps[:12],
        },
        status=status,
    )
    return {"ok": True, "decision": body.decision, "status": status}


# ---------------------------------------------------------------------------
# 違和感（tension）— TensionMiningAgent Stage 2: ダイジェスト・本人確定
# ---------------------------------------------------------------------------
# 権限: すべて本人（user_id 一致）のみ。教員・管理者は個別行にアクセス不可（P3）。


class TensionConfirmRequest(BaseModel):
    learner_text: str = ""


class TensionConnectRequest(BaseModel):
    component_id: str = ""
    edge_id: str = ""


@router.get("/courses/{course_id}/tension/digest")
def get_tension_digest_route(
    course_id: str,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """本人の tension 候補ダイジェスト（candidate・confidence>=0.55・新しい順に最大3件）。

    confidence の数値は返さない（学習者に数値スコアを見せない原則の準用）。
    セッション終了（20分無活動）後の未解析ヒントがあれば、ここで遅延起動する
    （best-effort・非同期。今回のレスポンスには間に合わなくてよい）。
    """
    session = _pg_session()
    try:
        topic_rows = session.execute(
            sa_text("""
                SELECT DISTINCT topic_id FROM interest_traces
                WHERE user_id = CAST(:uid AS uuid) AND course_id = :cid
                  AND payload->>'tension_hint' = 'true' AND analyzed_at IS NULL
            """),
            {"uid": current_user["id"], "cid": course_id},
        ).fetchall()
    except Exception:
        topic_rows = []
    finally:
        session.close()
    for (tid,) in topic_rows:
        maybe_schedule_tension_mining(current_user["id"], course_id, tid, session_end_check=True)

    return get_tension_digest(current_user["id"], course_id)


@router.post("/tension/{trace_id}/confirm")
def confirm_tension_route(
    trace_id: str,
    body: TensionConfirmRequest,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """候補を本人が引き受ける: candidate → open（learner_text ありなら articulated）。

    違和感を生成するのは人間であり、この操作だけが候補を tension として確定する（P1）。
    """
    result = confirm_tension_trace(current_user["id"], trace_id, body.learner_text)
    if result is None:
        raise HTTPException(status_code=404, detail="Tension candidate not found")
    return {"ok": True, **result}


@router.post("/tension/{trace_id}/dismiss")
def dismiss_tension_route(
    trace_id: str,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """本人が「違う」と判定: candidate → dismissed（行は残す。P4）。"""
    result = dismiss_tension_trace(current_user["id"], trace_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Tension candidate not found")
    return {"ok": True, **result}


# ---------------------------------------------------------------------------
# 構造帰属（structure_anchor）— StructureAnchorAgent Stage 2: ダイジェスト・本人確定
# ---------------------------------------------------------------------------
# 権限: すべて本人（user_id 一致）のみ。教員・管理者は個別行にアクセス不可（P3）。
# 行の status は変えない（問い自体は確定済み。候補なのは帰属だけ）。


class AnchorConfirmRequest(BaseModel):
    doubt_type: str = ""
    anchor_type: str = ""
    anchor_id: str = ""
    anchor_label: str = ""


@router.get("/courses/{course_id}/anchors/digest")
def get_anchor_digest_route(
    course_id: str,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """本人の帰属候補ダイジェスト（llm_candidate・confidence>=0.55・新しい順に最大3件）。

    「この疑問は◯◯についてでしたか？」の確認カード用。confidence の数値は返さない。
    セッション終了（20分無活動）後の未帰属の問いがあれば、ここで遅延起動する
    （best-effort・非同期。今回のレスポンスには間に合わなくてよい）。
    """
    session = _pg_session()
    try:
        topic_rows = session.execute(
            sa_text("""
                SELECT DISTINCT topic_id FROM interest_traces
                WHERE user_id = CAST(:uid AS uuid) AND course_id = :cid
                  AND kind = 'question'
                  AND payload->'structure_anchor' IS NULL
                  AND payload->>'anchor_analyzed_at' IS NULL
            """),
            {"uid": current_user["id"], "cid": course_id},
        ).fetchall()
    except Exception:
        topic_rows = []
    finally:
        session.close()
    for (tid,) in topic_rows:
        maybe_schedule_anchor_mining(current_user["id"], course_id, tid, session_end_check=True)

    return get_anchor_digest(current_user["id"], course_id)


@router.post("/anchors/{trace_id}/confirm")
def confirm_anchor_route(
    trace_id: str,
    body: AnchorConfirmRequest,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """帰属を本人が確定/訂正する: → attribution_source='confirmed'。

    帰属を確定するのは人間であり、この操作だけが LLM 候補を帰属として確定する（P1）。
    doubt_type / anchor_type / anchor_id を与えればその値で訂正して確定する。
    帰属が未生成の痕跡（方法Cの1タップ申告）には segment 縮退の最小アンカーを作る。
    """
    result = confirm_anchor_trace(
        current_user["id"], trace_id,
        doubt_type=body.doubt_type,
        anchor_type=body.anchor_type,
        anchor_id=body.anchor_id,
        anchor_label=body.anchor_label,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Anchor trace not found")
    # D層 (D3-6): 確定した anchor が分野で明示化済みの前提に対応していれば、
    # 事後に静かに併記する（通知しない・押し付けない。best-effort、失敗は無視）。
    try:
        from core.doubt.open_assumptions import related_confirmed_assumption
        from core.postgres import get_session as _doubt_session

        anchor = result.get("structure_anchor") or {}
        anchor_id = str(anchor.get("anchor_id") or "")
        if anchor_id:
            _ds = _doubt_session()
            try:
                related = related_confirmed_assumption(_ds, anchor_id)
            finally:
                _ds.close()
            if related:
                result["related_assumption"] = related
    except Exception:
        logger.debug("related assumption lookup skipped", exc_info=True)
    return {"ok": True, **result}


@router.post("/anchors/{trace_id}/dismiss")
def dismiss_anchor_route(
    trace_id: str,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """本人が帰属候補を「違う」と判定: structure_anchor.status='dismissed'（保持する。P4）。

    問い自体（行）は有効なまま残る。
    """
    result = dismiss_anchor_trace(current_user["id"], trace_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Anchor candidate not found")
    return {"ok": True, **result}


# ---------------------------------------------------------------------------
# 個人知識ネットワーク（わたしの地図）— 表示除外/復帰 (UX proposal §6)
# ---------------------------------------------------------------------------
# 「地図には反映しない」「地図に戻す」操作。痕跡は削除されず（P4）、地図の導出
# （core/personal_graph/derive.py）から外れるだけ。tension/anchor の dismiss（候補の
# 当落判定）とは独立で、status には触れない。本人のみ（current_user 以外の
# user_id を受けない）。


@router.post("/traces/{trace_id}/map-exclude")
def map_exclude_trace_route(
    trace_id: str,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """個人知識ネットワークへの表示から本人の痕跡を除外する（削除ではない。P4）。"""
    result = set_trace_map_exclusion(current_user["id"], trace_id, True)
    if result is None:
        raise HTTPException(status_code=404, detail="Trace not found")
    return {"ok": True, **result}


@router.post("/traces/{trace_id}/map-restore")
def map_restore_trace_route(
    trace_id: str,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """表示除外していた痕跡を個人知識ネットワークの表示へ戻す。"""
    result = set_trace_map_exclusion(current_user["id"], trace_id, False)
    if result is None:
        raise HTTPException(status_code=404, detail="Trace not found")
    return {"ok": True, **result}


# ---------------------------------------------------------------------------
# ハンズフリー音声会話（カジュアル対話モード用）
# ---------------------------------------------------------------------------

# アップロード音声の上限（無音区切りの1発話分。長くても数十秒を想定）
_VOICE_MAX_AUDIO_BYTES = 10 * 1024 * 1024


class VoiceSpeakRequest(BaseModel):
    text: str


@router.post("/voice/transcribe")
async def voice_transcribe_route(
    audio: UploadFile = File(...),
    language: str = "ja",
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """音声（1発話分）を Whisper 系モデルでテキストに文字起こしする。

    フロントの無音検知が区切った短い音声チャンクを受ける。openai プロバイダ以外では 503。
    """
    data = await audio.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty audio")
    if len(data) > _VOICE_MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Audio too large")
    try:
        with usage_context("learning:voice_stt", user_id=current_user["id"]):
            text = transcribe_audio(data, audio.filename or "audio.webm", language=language)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("voice transcribe failed")
        raise HTTPException(status_code=500, detail=f"Transcription failed: {exc}") from exc
    return {"text": text}


@router.post("/voice/speak")
def voice_speak_route(
    body: VoiceSpeakRequest,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """回答テキストを TTS で MP3(base64) に変換する（ハンズフリー会話の読み上げ用）。

    読み上げ前に LaTeX・markdown 記号・出典マーカーを除去する。
    """
    spoken = strip_text_for_speech(body.text)
    if not spoken:
        raise HTTPException(status_code=400, detail="Nothing to speak")
    try:
        with usage_context("learning:voice_tts", user_id=current_user["id"]):
            audio_bytes = generate_tts_audio(spoken)
    except Exception as exc:
        logger.exception("voice speak failed")
        raise HTTPException(status_code=500, detail=f"TTS failed: {exc}") from exc
    if audio_bytes is None:
        raise HTTPException(status_code=503, detail="TTS provider is not available")
    return {"audio_base64": base64.b64encode(audio_bytes).decode("ascii"), "format": "mp3"}


@router.post("/tension/{trace_id}/connect")
def connect_tension_route(
    trace_id: str,
    body: TensionConnectRequest,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """確定済み tension をグラフ上の node/edge に接続する（後続フェーズ）。"""
    result = connect_tension_trace(
        current_user["id"], trace_id,
        component_id=body.component_id, edge_id=body.edge_id,
    )
    if result is None:
        raise HTTPException(status_code=400, detail="Could not connect tension trace")
    return {"ok": True, **result}


@router.get("/courses/{course_id}/components/{component_id}/explanations")
def get_component_explanations_for_learner(
    course_id: str,
    component_id: str,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """学習者向け: 1つの理論・概念の説明バージョン(標準/各教員の説明)を返す(C層 Phase 2)。

    承認済み(teacher_approved)の説明のみを返す。標準説明は kind='standard'。
    承認の厚みは段階ラベルで示し、数値スコアは学習者に提示しない(点数化を避ける)。
    """
    from routes.theory_components import _endorsement_label  # 遅延 import(循環回避)

    if not get_viewable_course_data(current_user["id"], course_id):
        raise HTTPException(status_code=404, detail="Course not found")
    session = _pg_session()
    try:
        rows = session.execute(
            sa_text("""
                SELECT e.id, e.kind, COALESCE(u.display_name, ''), e.title, e.body,
                       COALESCE(s.endorser_count, 0), COALESCE(s.strong_count, 0),
                       COALESCE(s.provisional_count, 0), COALESCE(s.expertise_breadth, 0)
                FROM component_explanations e
                LEFT JOIN users u ON u.id = e.author_id
                LEFT JOIN component_explanation_endorsement_summary s ON s.explanation_id = e.id
                WHERE e.component_id = CAST(:cid AS uuid)
                  AND e.course_id = :course_id
                  AND e.review_status = 'teacher_approved'
                ORDER BY (e.kind = 'standard') DESC, COALESCE(s.endorser_count, 0) DESC, e.created_at ASC
            """),
            {"cid": component_id, "course_id": course_id},
        ).fetchall()
    finally:
        session.close()
    explanations = []
    for r in rows:
        summary = {
            "endorser_count": int(r[5] or 0),
            "strong_count": int(r[6] or 0),
            "provisional_count": int(r[7] or 0),
            "expertise_breadth": int(r[8] or 0),
        }
        explanations.append({
            "id": str(r[0]),
            "kind": str(r[1] or "personal"),
            "author_name": str(r[2] or ""),
            "title": str(r[3] or ""),
            "body": str(r[4] or ""),
            "endorsement_label": _endorsement_label(summary),
        })
    return {"component_id": component_id, "explanations": explanations}
