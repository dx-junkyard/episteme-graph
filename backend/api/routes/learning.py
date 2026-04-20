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
    ensure_learning_state,
    get_course_data,
    get_course_master,
    get_course_owner,
    log_unanswered_query,
    persist_chat_history,
    save_course_data,
    delete_course_data,
    search_chunks_with_metadata,
)
from core.llm import generate_text, get_llm_params
from core.postgres import get_session as _pg_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/learning", tags=["Learning"])

# ---------------------------------------------------------------------------
# Course CRUD
# ---------------------------------------------------------------------------


@router.post("/courses", response_model=LearningCourseOut, status_code=201)
def create_course(
    body: CourseCreateRequest,
    current_user: dict = Depends(_get_current_user),
) -> LearningCourseOut:
    """新しいコースを作成する。"""
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

    save_course_data(current_user["id"], course_id, data, is_template=body.is_template)

    logger.info("Created course '%s' (id=%s) for user=%s", body.title, course_id, current_user["id"])
    return LearningCourseOut(id=course_id, title=body.title, is_template=body.is_template)


@router.get("/courses", response_model=list[LearningCourseOut])
def list_courses(
    current_user: dict = Depends(_get_current_user),
) -> list[LearningCourseOut]:
    """ユーザーがアクセス可能なコース一覧を返す。

    Issue #133 以降、学生は learning_states で受講中のコースのみを「マイコース」として扱い、
    まだ受講していない公開テンプレートは「受講可能」として提示する。教員は自分が所有する
    コース (is_template=true のもの含む) も合わせて返す。
    """
    session = _pg_session()
    try:
        own_records = session.execute(
            sa_text("""
                SELECT lc.id, lc.title,
                       COALESCE(lc.is_template, false) AS is_template,
                       COALESCE(lc.is_published, false) AS is_published
                FROM learning_courses lc
                WHERE lc.owner_id = CAST(:user_id AS uuid)
                   OR lc.user_id = CAST(:user_id AS uuid)
            """),
            {"user_id": current_user["id"]},
        ).fetchall()

        enrolled_records = session.execute(
            sa_text("""
                SELECT lc.id, lc.title,
                       COALESCE(lc.is_template, false) AS is_template,
                       COALESCE(lc.is_published, false) AS is_published
                FROM learning_states ls
                JOIN learning_courses lc ON lc.id = ls.course_id
                WHERE ls.user_id = CAST(:user_id AS uuid)
                  AND COALESCE(lc.owner_id, lc.user_id) != CAST(:user_id AS uuid)
            """),
            {"user_id": current_user["id"]},
        ).fetchall()

        template_records = session.execute(
            sa_text("""
                SELECT lc.id, lc.title
                FROM learning_courses lc
                WHERE lc.is_published = true AND lc.is_template = true
                  AND COALESCE(lc.owner_id, lc.user_id) != CAST(:user_id AS uuid)
                  AND NOT EXISTS (
                      SELECT 1 FROM learning_states ls
                      WHERE ls.user_id = CAST(:user_id AS uuid)
                        AND ls.course_id = lc.id
                  )
            """),
            {"user_id": current_user["id"]},
        ).fetchall()
    finally:
        session.close()

    seen: set[str] = set()
    courses: list[LearningCourseOut] = []
    for r in list(own_records) + list(enrolled_records):
        if r[0] in seen:
            continue
        seen.add(r[0])
        courses.append(LearningCourseOut(
            id=r[0],
            title=r[1],
            is_template=bool(r[2]),
            is_published=bool(r[3]),
            is_enrollable=False,
        ))
    for r in template_records:
        if r[0] in seen:
            continue
        seen.add(r[0])
        courses.append(LearningCourseOut(
            id=r[0],
            title=r[1],
            is_template=True,
            is_published=True,
            is_enrollable=True,
        ))
    return courses


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
    """コース原本を部分更新する。コース所有者のみ許可。"""
    owner_id = get_course_owner(course_id)
    if not owner_id:
        raise HTTPException(status_code=404, detail="Course not found")
    if owner_id != current_user["id"]:
        raise HTTPException(
            status_code=403,
            detail="コース原本を編集できるのは所有者のみです。",
        )

    data = get_course_master(course_id) or {}
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
    logger.info("Updated course %s for owner=%s", course_id, current_user["id"])

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
    """公開テンプレートコースを受講登録する (Issue #133)。

    コースのクローンは作成せず、learning_states に (user_id, course_id) の状態行を
    登録するのみ。UNIQUE 制約により重複受講は DB レベルでブロックされる。
    """
    session = _pg_session()
    try:
        record = session.execute(
            sa_text("""
                SELECT id, title FROM learning_courses
                WHERE id = :course_id AND is_published = true AND is_template = true
                LIMIT 1
            """),
            {"course_id": course_id},
        ).fetchone()
    finally:
        session.close()

    if not record:
        raise HTTPException(status_code=404, detail="Published course not found")

    ensure_learning_state(current_user["id"], course_id)

    logger.info(
        "User=%s enrolled in course %s", current_user["id"], course_id,
    )
    return LearningCourseOut(
        id=record[0],
        title=record[1] or "",
        is_template=True,
        is_published=True,
        is_enrollable=False,
    )


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


def _generate_greeting_response(
    course_title: str,
    topic_title: str,
    message: str,
    *,
    topic_info: dict | None = None,
    course_data: dict | None = None,
) -> str:
    """学習開始時のオーバービューを生成する (Standard モード)。

    topic_info / course_data からトピックの主要概念・前提知識を抽出し、
    学習の全体像を提示するメッセージを生成する。
    """
    params = get_llm_params("standard")

    # トピックメタ情報を抽出
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

    # プロンプト用のメタ情報ブロック構築
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
        f"あなたは「{course_title}」の学習をサポートするAIチューターです。\n"
        f"学生が新しく「{topic_title}」の学習を開始しようとしています。\n\n"
        f"{concepts_block}"
        f"{prereqs_block}"
        "以下の構成で、学習の導入となる最初のメッセージを作成してください:\n"
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
        logger.warning("Greeting response LLM call failed, returning fallback")
        return (
            f"「{course_title}」の学習サポートへようこそ！\n\n"
            f"これから「{topic_title}」の学習を始めます。\n\n"
            + (f"**習得すべき主要概念:** {', '.join(concepts)}\n\n" if concepts else "")
            + (f"**必要な前提知識:** {', '.join(prerequisites)}\n\n" if prerequisites else "")
            + "まずは前提知識の復習から始めますか？ それとも最初の概念の説明に進みますか？"
        )


_LEARNING_SYSTEM_PROMPT = """あなたは素粒子物理学・場の量子論を専門とする学習者の深い理解を支援する家庭教師です。
以下の原則に従ってください。

**教育方針:**
1. 学生の誤解を発見したら「訂正：」と明記し、**誤謬の構造的理由**を必ず含めてください。
   - 「数式のこの項を見落としがちですが…」のように、数式レベルで誤解の原因を指摘
   - 「〇〇と△△を混同しやすいですが…」のように、類似概念との混同パターンを説明
   - 正答だけでなく、なぜ間違えやすいかの構造を必ず示す
2. 概念の説明は具体的な数式（LaTeX）、ファインマン図の説明、または物理的直観を使って行ってください。
3. 教材から引用できる場合は出典（セクション番号等）を明記してください。
4. 説明の最後に、理解を確認するための質問をしてください。
5. 関連する概念へのドリルダウン選択肢を提示してください。

**数式の導出サポート:**
- 数式の行間（導出）に関する質問には、前提となる数学的公式・定理を最初に提示してください。
- ステップ・バイ・ステップで、各変形の物理的意味を添えて説明してください。
- 例: 「ここで部分積分を使い、表面項が消えることを仮定すると…」

**RAGコンテキスト利用:**
- 提供される「教材チャンク」はシステム全域のベクトル検索で取得した関連箇所です。各チャンクには `[出典: 『書籍名』]` の形式で出典が付いています。
- 回答中で教材を参照する際は必ず「『書籍名』によれば…」「『書籍名』では…と述べられています」のように出典を明記してください。
- 複数の教材から情報を統合する場合は、それぞれの出典を区別して示してください。
- コンテキストに含まれない情報について推測する場合はその旨を明記してください。

**フォーマット:**
- 数式は必ず LaTeX 記法で記述（インラインは `$...$`、ディスプレイは `$$...$$`）
- 誤解の訂正が必要な場合は最初に「訂正：」と記述
- 参照した教材のセクションがあれば言及
- 回答の末尾に深掘りできるトピックを `[〇〇について詳しく聞く]` の形式で提示（必ずこのフォーマットを使用）"""


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
    """RAG統合された学習チャットエンドポイント。"""
    # 1. コースデータを取得
    course_data = get_course_data(current_user["id"], course_id)
    if not course_data:
        raise HTTPException(status_code=404, detail="Course not found")

    # トピック情報を取得
    topic_info = None
    for t in course_data.get("topics", []):
        if t.get("id") == topic_id:
            topic_info = t
            break
    topic_title = topic_info["title"] if topic_info else topic_id

    # 2. Greeting / メタ対話のバイパス: RAG検索をスキップ → オーバービュー提示
    course_title = course_data.get("title", course_id)
    if _is_greeting(body.message):
        greeting_answer = _generate_greeting_response(
            course_title, topic_title, body.message,
            topic_info=topic_info, course_data=course_data,
        )
        persist_chat_history(
            current_user["id"], course_id, topic_id,
            body.history, body.message, greeting_answer,
        )
        return LearningChatResponse(answer=greeting_answer, course_update=None)

    # 3. Adaptive Routing: 前提知識の自動判定
    prerequisite_intervention = check_prerequisites(
        current_user["id"], course_id, course_data, topic_title, body.message
    )
    if prerequisite_intervention:
        persist_chat_history(
            current_user["id"], course_id, topic_id,
            body.history, body.message, prerequisite_intervention,
        )
        return LearningChatResponse(answer=prerequisite_intervention, course_update=None)

    # 4. RAG: システム全域のチャンクを検索（出典メタデータ付き）
    _RELEVANCE_THRESHOLD = 0.35
    chunk_results = search_chunks_with_metadata(body.message, top_k=8)

    # 4a. フェイルセーフ: 閾値以上のチャンクが0件の場合
    above_threshold = [r for r in chunk_results if r["score"] >= _RELEVANCE_THRESHOLD]
    if not above_threshold:
        log_unanswered_query(current_user["id"], course_id, topic_id, body.message)
        no_answer = (
            "申し訳ありませんが、ご質問の内容はシステムに登録された教材には見つかりませんでした。\n\n"
            "教材に含まれていない内容についてはお答えできません。\n"
            "別の表現で質問するか、担当教員にお問い合わせください。"
        )
        persist_chat_history(
            current_user["id"], course_id, topic_id,
            body.history, body.message, no_answer,
        )
        return LearningChatResponse(answer=no_answer, course_update=None)

    # 出典情報を付けてコンテキストブロックを構築
    cited_chunks = []
    for r in above_threshold:
        cited_chunks.append(f"[出典: 『{r['source_title']}』]\n{r['text']}")

    context_parts: list[str] = [
        "## 教材から検索された関連箇所（出典付き）\n" + "\n---\n".join(cited_chunks)
    ]

    context_block = "\n\n".join(context_parts)

    # 6. LLM メッセージ構築
    messages: list[dict] = [
        {"role": "system", "content": _LEARNING_SYSTEM_PROMPT},
        {"role": "user", "content": (
            f"コース: {course_title}\n"
            f"現在のトピック: {topic_title}\n\n"
            f"{context_block}\n\n"
            "上記のコンテキストを念頭に置いて質問に回答してください。"
        )},
        {"role": "assistant", "content": (
            f"了解しました。「{topic_title}」について、教材を参照しながら学習を進めましょう。"
        )},
    ]

    for turn in body.history:
        messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": body.message})

    # 7. LLM 呼び出し
    try:
        answer = generate_text(messages=messages, temperature=0.3)
    except Exception as exc:
        logger.exception("Learning chat LLM call failed for topic %s", topic_id)
        raise HTTPException(status_code=500, detail=f"Chat failed: {exc}") from exc

    # 8. 誤解検出
    course_update = None
    if topic_info and any(kw in answer for kw in ["訂正：", "訂正:", "【訂正】"]):
        course_update = detect_and_record_misconception(
            current_user["id"], course_id, course_data, topic_id, body.message, answer
        )

    # 9. チャット履歴を永続化
    persist_chat_history(
        current_user["id"], course_id, topic_id,
        body.history, body.message, answer,
    )

    return LearningChatResponse(answer=answer, course_update=course_update)
