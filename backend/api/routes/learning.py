"""Episteme Graph — 学習エンドポイント (/api/learning)。"""

from __future__ import annotations

import json
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
    get_course_data,
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
    """ユーザーが登録しているコース一覧を返す。公開テンプレートも含む。"""
    session = _pg_session()
    try:
        own_records = session.execute(
            sa_text("""
                SELECT id, title,
                       COALESCE(is_template, false) AS is_template,
                       COALESCE(is_published, false) AS is_published
                FROM learning_courses
                WHERE user_id = CAST(:user_id AS uuid)
            """),
            {"user_id": current_user["id"]},
        ).fetchall()

        template_records = session.execute(
            sa_text("""
                SELECT lc.id, lc.title
                FROM learning_courses lc
                WHERE lc.is_published = true AND lc.is_template = true
                  AND lc.user_id != CAST(:user_id AS uuid)
                  AND NOT EXISTS (
                      SELECT 1 FROM learning_courses lc2
                      WHERE lc2.user_id = CAST(:user_id AS uuid)
                        AND lc2.cloned_from = lc.id
                  )
            """),
            {"user_id": current_user["id"]},
        ).fetchall()
    finally:
        session.close()

    courses = [
        LearningCourseOut(
            id=r[0],
            title=r[1],
            is_template=bool(r[2]),
            is_published=bool(r[3]),
            is_enrollable=False,
        )
        for r in own_records
    ]
    courses.extend(
        LearningCourseOut(
            id=r[0],
            title=r[1],
            is_template=True,
            is_published=True,
            is_enrollable=True,
        )
        for r in template_records
    )
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
    """コースを部分更新する。指定されたフィールドのみ上書き。"""
    data = get_course_data(current_user["id"], course_id)
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
    """公開テンプレートコースをクローンして自分のコースとして登録する。"""
    session = _pg_session()
    try:
        record = session.execute(
            sa_text("""
                SELECT data FROM learning_courses
                WHERE id = :course_id AND is_published = true AND is_template = true
                LIMIT 1
            """),
            {"course_id": course_id},
        ).fetchone()
    finally:
        session.close()

    if not record or not record[0]:
        raise HTTPException(status_code=404, detail="Published course not found")

    template_data = record[0] if isinstance(record[0], dict) else json.loads(record[0])

    new_course_id = str(uuid.uuid4())[:8]
    cloned_data = dict(template_data)
    cloned_data["id"] = new_course_id

    save_course_data(current_user["id"], new_course_id, cloned_data)

    session = _pg_session()
    try:
        session.execute(
            sa_text("UPDATE learning_courses SET cloned_from = :original_id WHERE id = :id"),
            {"id": new_course_id, "original_id": course_id},
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    logger.info(
        "User=%s enrolled in course %s (cloned as %s)",
        current_user["id"], course_id, new_course_id,
    )
    return LearningCourseOut(id=new_course_id, title=cloned_data.get("title", ""))


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
    course_title: str, topic_title: str, message: str,
) -> str:
    """挨拶・メタ対話に対するLLM応答を生成する (Fast モード)。"""
    params = get_llm_params("fast")
    prompt = (
        f"あなたは「{course_title}」の学習をサポートするAIチューターです。\n"
        f"学生から「{message}」というメッセージを受け取りました。\n\n"
        "以下のルールで返答してください:\n"
        "1. 学生を歓迎し、学習意欲を肯定する短い挨拶をする。\n"
        f"2. 現在のトピックが「{topic_title}」であることを踏まえ、「まずは〇〇について確認しましょうか？」と最初のステップを提案する。\n"
        "3. 物理学の具体的な解説はこの時点では行わない。\n"
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
            f"こんにちは！「{course_title}」の学習サポートへようこそ。\n\n"
            f"現在のトピックは「{topic_title}」です。まずはこのトピックについて確認しましょうか？"
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

    # 2. Greeting / メタ対話のバイパス: RAG検索をスキップ
    course_title = course_data.get("title", course_id)
    if _is_greeting(body.message):
        greeting_answer = _generate_greeting_response(course_title, topic_title, body.message)
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
