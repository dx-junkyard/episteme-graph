"""Episteme Graph — 学習エンドポイント (/api/learning)。"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
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
    detect_and_record_misconception,
    enroll_user_in_course,
    get_course_chunks_ordered,
    get_course_data,
    get_editable_course_data,
    get_graph_element_context,
    get_personal_layer,
    get_user_group_ids,
    log_unanswered_query,
    persist_chat_history,
    record_student_stumble_event,
    save_course_data,
    delete_course_data,
    search_chunks_with_metadata,
    user_can_access_group,
    user_can_view_course,
)
from core.llm import generate_text, get_llm_params
from core.learning_experience import (
    TIER_OUT_OF_SOURCE,
    aggregate_overall_tier,
    build_position_anchor,
    mock_interest_traces,
    out_of_source_notice,
)
from core.learning_support_agent import LearningSupportAgent, extract_inline_actions
from core.personas import course_persona_settings, persona_prompt
from core.postgres import get_session as _pg_session
from core.course_content_builder import build_course_content_background

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
        # - 新: course_group_permissions 多対多マッピング (viewer/editor)
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
                    LEFT JOIN course_group_permissions cgp ON cgp.course_id = lc.id
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
        # course_group_permissions 経由で viewer/editor 権限を持つ場合
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
        topics = course_data.get("topics", [])
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
- 教材を参照した場合は [出典: 『書籍名』] を文脈に自然に混ぜて言及すること。{persona_block}"""


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

    topics = course_data.get("topics", [])
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

    topic = next((t for t in course_data.get("topics", []) if t.get("id") == topic_id), None)
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
        f"コース: {course_data.get('title', course_id)}\n"
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

    topic_info = None
    for t in course_data.get("topics", []):
        if t.get("id") == topic_id:
            topic_info = t
            break
    topic_title = topic_info["title"] if topic_info else topic_id
    course_title = course_data.get("title", course_id)
    # domain が未設定の場合は course_title にフォールバック
    domain = course_data.get("domain") or course_title
    response_persona = course_persona_settings(course_data)["response_persona"]
    support_agent = LearningSupportAgent(course_id, course_data)
    support_origin = support_agent.origin_for_topic(topic_id, topic_info)

    if body.support_action == "return_to_learning_path":
        result = support_agent.return_to_path_result((body.support_context or {}).get("origin"))
        persist_chat_history(
            current_user["id"], course_id, topic_id,
            body.history, body.message, result.answer,
        )
        return LearningChatResponse(**result.model_dump(), course_update=None)

    # UIサジェスト由来の明示アクションは自然文の意図分類より優先する。
    if body.action == "EXPLAIN_GRAPH_ELEMENT":
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
        return LearningChatResponse(**result.model_dump(), course_update=graph_response.course_update)

    # 2. 意図分類（Intent Routing）— UI ボタン由来の型付きアクションは分類を経由しない。
    intent = _route_for_typed_action(body.support_action) or _classify_intent(body.message, course_title)

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
    prerequisite_intervention = check_prerequisites(
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
    chunk_results = search_chunks_with_metadata(body.message, top_k=8)
    cited_chunks = []
    cited_sources: list[dict] = []  # L1: 文脈に採用した根拠の tier 一覧
    if topic_info:
        topic_material = _topic_student_material(topic_info)
        if topic_material:
            cited_chunks.append(f"[現在表示中の教材]\n{topic_material[:5000]}")
    for r in chunk_results:
        if r["score"] >= 0.30:
            cited_chunks.append(f"[出典: 『{r['source_title']}』]\n{r['text']}")
            _quote = (r.get("text") or "").strip().replace("\n", " ")
            cited_sources.append({
                "source_title": r.get("source_title", "不明な教材"),
                "tier": r.get("tier", TIER_OUT_OF_SOURCE),
                "score": round(float(r.get("score", 0.0)), 3),
                "quote": (_quote[:80] + "…") if len(_quote) > 80 else _quote,
                "meta": r.get("source_file") or "",
            })

    # L1: 回答全体の格を最弱根拠へ安全側集約。採用根拠が無ければ未踏(out_of_source)。
    overall_tier = aggregate_overall_tier([s["tier"] for s in cited_sources])

    if cited_chunks:
        context_block = "## 関連する教材のコンテキスト\n" + "\n---\n".join(cited_chunks)
    else:
        context_block = "※この質問に直接関連する教材セクションは見つかりませんでした。一般的な学術知識を用いて回答してください。"
        log_unanswered_query(current_user["id"], course_id, topic_id, body.message)

    # 5. 回答の生成（ルート統合）
    messages: list[dict] = [
        {"role": "system", "content": _get_integrated_tutor_system_prompt(domain, response_persona)},
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

    try:
        answer = generate_text(messages=messages, temperature=0.3)
    except Exception as exc:
        logger.exception("Learning chat LLM call failed for topic %s", topic_id)
        raise HTTPException(status_code=500, detail=f"Chat failed: {exc}") from exc

    # L1 OutOfSourceGuard: 未踏なら断定せず、根拠が弱い旨を先頭に明示する。
    if overall_tier == TIER_OUT_OF_SOURCE:
        answer = out_of_source_notice() + "\n\n" + answer

    # 誤解検出（マイルドな表現にも対応）
    course_update = None
    if topic_info and any(kw in answer for kw in ["訂正", "より正確です", "誤解"]):
        course_update = detect_and_record_misconception(
            current_user["id"], course_id, course_data, topic_id, body.message, answer
        )

    persist_chat_history(
        current_user["id"], course_id, topic_id,
        body.history, body.message, answer,
    )
    # ドメイン質問は常に detour（origin=現在アンカー）として扱う。本文中のドリルダウン
    # マーカーは構造化アクションへ正規化し、復帰導線と合わせて next_actions に集約する。
    clean_answer, inline_actions = extract_inline_actions(answer)
    result = support_agent.with_learning_actions(
        answer=clean_answer,
        mode="detail_explanation",
        origin=support_origin,
        include_continue=False,
        extra_actions=inline_actions,
    )
    # EPISTEME_MOCK[M04] L2位置復帰: position_anchor は仮値(segment/scroll=0)。
    # — replace in Stage 1
    position_anchor = build_position_anchor(topic_id)
    return LearningChatResponse(
        **result.model_dump(),
        course_update=course_update,
        sources=cited_sources,
        overall_tier=overall_tier,
        position_anchor=position_anchor,
        mock=True,  # 🚧 tier 判定と position_anchor は Stage M の mock を含む
    )


@router.get("/courses/{course_id}/interest-traces")
def get_interest_traces(
    course_id: str,
    topic_id: str | None = None,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """UnfinishedQuestionBox（未解決の問い・寄り道・誤答）を返す。

    EPISTEME_MOCK[M05] L3資産化: interest_traces テーブル未作成のため固定 mock を返す。
    レスポンスは `_mock: true` を含み、フロントは 🚧MOCK バッジを描画する。
    — replace in Stage 3 (db/020_interest_trace.sql + 安価な生記録)
    """
    return mock_interest_traces(course_id, topic_id)
