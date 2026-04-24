"""Episteme Graph — 学習エンドポイント (/api/learning)。"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text as sa_text

from dependencies import _get_current_user
from schemas import (
    CourseCreateRequest,
    CourseUpdateRequest,
    LearningChatHistoryResponse,
    LearningChatRequest,
    LearningChatResponse,
    LearningCourseDetail,
    LearningCourseOut,
    LearningProgress,
)
from services import (
    calculate_progress,
    check_prerequisites,
    detect_and_record_misconception,
    enroll_user_in_course,
    get_course_data,
    get_editable_course_data,
    get_user_group_ids,
    log_unanswered_query,
    persist_chat_history,
    save_course_data,
    delete_course_data,
    search_chunks_with_metadata,
    user_can_access_group,
    user_can_view_course,
)
from core.llm import generate_text, get_llm_params
from core.postgres import get_session as _pg_session

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


@router.get("/courses/{course_id}", response_model=LearningCourseDetail)
def get_course(
    course_id: str,
    current_user: dict = Depends(_get_current_user),
) -> LearningCourseDetail:
    """コースの詳細データを返す。"""
    data = get_course_data(current_user["id"], course_id)
    if not data:
        raise HTTPException(status_code=404, detail="Course not found")

    return LearningCourseDetail(**data)


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


def _classify_intent(message: str, course_title: str) -> str:
    """ユーザーメッセージの意図を分類する (Intent Routing)。

    Returns
    -------
    str
        ``'CHIT_CHAT'`` | ``'LEARNING_ADVICE'`` | ``'DOMAIN_RAG'``
    """
    if _is_greeting(message):
        return "LEARNING_ADVICE"

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

    prompt = (
        f"あなたは「{course_title}」の学習をサポートするナビゲーター教授です。\n"
        f"学生は現在「{topic_title}」のトピックを学習しています。\n\n"
        f"{topics_block}"
        f"{concepts_block}"
        f"{prereqs_block}"
        f"学生からのメッセージ: {message}\n\n"
        "コース全体の構造と学生の現在位置を踏まえ、以下の構成で回答してください:\n"
        "1. 【歓迎と目標】このトピックで学ぶことの全体像と、最終的な学習目標を簡潔に説明する。\n"
        "2. 【構成要素】習得すべき主要な概念をリストアップする。\n"
        "3. 【前提知識の確認】このトピックを学ぶために必要な前提知識を提示する。\n"
        "4. 【ネクストアクション】「まずは前提知識の復習から始めますか？ それとも最初の概念の説明に進みますか？」と、学生に次の行動を選ばせる質問で締めくくる。\n\n"
        "※注意: ここでは具体的な解説（数式展開など）はまだ行わないこと。"
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
            + "まずは前提知識の復習から始めますか？ それとも最初の概念の説明に進みますか？"
        )


def _get_integrated_tutor_system_prompt(domain: str) -> str:
    """知識統合型チューターのシステムプロンプトを生成する。

    Parameters
    ----------
    domain : str
        コースの専門分野。空文字の場合は「このコースの専門分野」でフォールバック。
    """
    domain_label = domain.strip() if domain.strip() else "このコースの専門分野"
    return f"""あなたは{domain_label}の学習をサポートする「親切な専属チューター」です。
学生の疑問に対して、手元の教材とあなた自身の専門知識をシームレスに統合して、即座に分かりやすい解説を提供することが使命です。

**チューターとしての役割と回答ルール:**
1. 【知識の統合】提供される「教材からのコンテキスト」を最優先で参照してください。ただし、コンテキストに十分な情報がない場合は、突き放したり「教材にありません」と謝罪したりせず、あなたの一般的な学術知識を用いて自然に解説を補完してください。
2. 【自然な対話】回答の冒頭に「【基礎知識の補足】」のようなシステム的な警告ラベルは絶対に付けないでください。
3. 【誤解の訂正】学生に誤解がある場合は、「訂正：」という冷たい表現は避け、「この点については、〇〇と考えるとより正確です」のように教育的配慮を持って導いてください。
4. 【解説の深さ】前提知識の確認で長々と引き留めず、まずは直球で疑問に答えてください。必要に応じて数式（LaTeX）や具体例を交えてください。
5. 【ドリルダウン】回答の末尾に、関連して深掘りできそうなトピックを `[〇〇について詳しく聞く]` の形式で1〜2つ提示してください。

**フォーマット要件:**
- 数式は必ず LaTeX 記法で記述（インラインは $...$、ディスプレイは $$...$$）
- 教材を参照した場合は [出典: 『書籍名』] を文脈に自然に混ぜて言及すること。"""


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

    # 2. 意図分類（Intent Routing）
    intent = _classify_intent(body.message, course_title)

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
        persist_chat_history(
            current_user["id"], course_id, topic_id,
            body.history, body.message, advice_answer,
        )
        return LearningChatResponse(answer=advice_answer, course_update=None)

    # 3. Adaptive Routing: 前提知識の自動判定 (ルート③/④の前に実行)
    prerequisite_intervention = check_prerequisites(
        current_user["id"], course_id, course_data, topic_title, body.message
    )
    if prerequisite_intervention:
        persist_chat_history(
            current_user["id"], course_id, topic_id,
            body.history, body.message, prerequisite_intervention,
        )
        return LearningChatResponse(answer=prerequisite_intervention, course_update=None)

    # 4. RAG: システム全域のチャンクを検索し、コンテキストを構築
    chunk_results = search_chunks_with_metadata(body.message, top_k=8)
    cited_chunks = []
    for r in chunk_results:
        if r["score"] >= 0.30:
            cited_chunks.append(f"[出典: 『{r['source_title']}』]\n{r['text']}")

    if cited_chunks:
        context_block = "## 関連する教材のコンテキスト\n" + "\n---\n".join(cited_chunks)
    else:
        context_block = "※この質問に直接関連する教材セクションは見つかりませんでした。一般的な学術知識を用いて回答してください。"
        log_unanswered_query(current_user["id"], course_id, topic_id, body.message)

    # 5. 回答の生成（ルート統合）
    messages: list[dict] = [
        {"role": "system", "content": _get_integrated_tutor_system_prompt(domain)},
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
    return LearningChatResponse(answer=answer, course_update=course_update)
