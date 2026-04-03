"""Episteme Graph — 管理者エンドポイント (/api/admin)。"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import text as sa_text

from dependencies import (
    _get_current_user,
    _hash_password,
    _require_system_admin,
    _require_teacher,
    ROLE_STUDENT,
    ROLE_TEACHER,
)
from schemas import (
    ApproveWithScopeRequest,
    BackgroundTaskOut,
    CourseBuilderChatRequest,
    CourseBuilderChatResponse,
    CourseBuilderSessionCreate,
    CourseBuilderSessionOut,
    CourseBuilderSessionUpdate,
    CreateUserRequest,
    MaterialOut,
    ReextractionJobOut,
    SimulationResponse,
    SchemaProposalOut,
    SchemaTypeCreateRequest,
    SchemaTypeOut,
    UserOut,
)
from services import (
    _material_lock,
    _material_status,
    create_background_task,
    get_background_task,
    process_material_background,
    save_cb_session,
)
from core.llm import generate_text
from core.meta_analyzer import (
    analyze_unanswered_queries,
    approve_proposal,
    get_proposals,
    reject_proposal,
)
from core.postgres import get_session as _pg_session
from core.reextractor import enqueue_reextraction, get_jobs as get_reextraction_jobs
from core.schema_registry import (
    add_ontology_type,
    add_predicate,
    get_ontology_types,
    get_predicates,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["Admin"])


# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------


@router.post("/materials/upload", status_code=202)
def upload_material(
    file: UploadFile = File(...),
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """PDF教材をアップロードし、バックグラウンドでグラフ化処理を開始する。

    即座に task_id を返却し、処理完了はポーリングで確認する。
    """
    import datetime

    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    pdf_bytes = file.file.read()
    if len(pdf_bytes) == 0:
        raise HTTPException(status_code=400, detail="Empty file")

    material_id = str(uuid.uuid4())[:12]
    doc_id = uuid.uuid4()
    task_id = str(uuid.uuid4())[:12]
    now = datetime.datetime.utcnow().isoformat()

    session = _pg_session()
    try:
        session.execute(
            sa_text("""
                INSERT INTO documents (id, title, filename, status, uploaded_by, doc_type, source_path)
                VALUES (:id, :title, :filename, 'uploaded', CAST(:uploaded_by AS uuid), 'textbook', :material_id)
            """),
            {
                "id": doc_id,
                "title": os.path.splitext(file.filename)[0],
                "filename": file.filename,
                "uploaded_by": current_user["id"],
                "material_id": material_id,
            },
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    create_background_task(task_id, "material_processing", current_user["id"])

    thread = threading.Thread(
        target=process_material_background,
        args=(material_id, str(doc_id), file.filename, pdf_bytes, task_id),
        daemon=True,
    )
    thread.start()

    logger.info(
        "Material upload accepted: %s (%s) task=%s by user=%s",
        material_id, file.filename, task_id, current_user["id"],
    )

    return {
        "task_id": task_id,
        "material_id": material_id,
        "filename": file.filename,
        "title": os.path.splitext(file.filename)[0],
        "status": "pending",
        "uploaded_at": now,
    }


@router.get("/materials", response_model=list[MaterialOut])
def list_materials(
    current_user: dict = Depends(_require_teacher),
) -> list[MaterialOut]:
    """アップロード済み教材の一覧を返す。"""
    session = _pg_session()
    try:
        records = session.execute(
            sa_text("""
                SELECT source_path, filename, title, status, created_at, knowledge_graph
                FROM documents
                WHERE uploaded_by = CAST(:user_id AS uuid) AND filename IS NOT NULL
                ORDER BY created_at DESC
            """),
            {"user_id": current_user["id"]},
        ).fetchall()
    finally:
        session.close()

    materials = []
    for r in records:
        mid = r[0] or ""
        kg = r[5] if r[5] else None

        status = r[3] or "uploaded"
        with _material_lock:
            if mid in _material_status:
                status = _material_status[mid].get("status", status)

        uploaded_at = r[4].isoformat() if r[4] else ""
        materials.append(MaterialOut(
            material_id=mid,
            filename=r[1] or "",
            title=r[2] or "",
            status=status,
            uploaded_at=uploaded_at,
            knowledge_graph=kg,
        ))

    return materials


@router.get("/materials/{material_id}", response_model=MaterialOut)
def get_material(
    material_id: str,
    current_user: dict = Depends(_require_teacher),
) -> MaterialOut:
    """教材の詳細情報（ナレッジグラフ含む）を返す。"""
    session = _pg_session()
    try:
        record = session.execute(
            sa_text("""
                SELECT source_path, filename, title, status, created_at, knowledge_graph
                FROM documents
                WHERE uploaded_by = CAST(:user_id AS uuid) AND source_path = :material_id
                LIMIT 1
            """),
            {"user_id": current_user["id"], "material_id": material_id},
        ).fetchone()
    finally:
        session.close()

    if not record:
        raise HTTPException(status_code=404, detail="Material not found")

    kg = record[5] if record[5] else None

    status = record[3] or "uploaded"
    with _material_lock:
        if material_id in _material_status:
            status = _material_status[material_id].get("status", status)

    uploaded_at = record[4].isoformat() if record[4] else ""
    return MaterialOut(
        material_id=record[0] or "",
        filename=record[1] or "",
        title=record[2] or "",
        status=status,
        uploaded_at=uploaded_at,
        knowledge_graph=kg,
    )


# ---------------------------------------------------------------------------
# Background Task Polling (Issue #63)
# ---------------------------------------------------------------------------


@router.get("/tasks/{task_id}", response_model=BackgroundTaskOut)
def get_task_status(
    task_id: str,
    current_user: dict = Depends(_require_teacher),
) -> BackgroundTaskOut:
    """バックグラウンドタスクのステータスを返す。ポーリング用エンドポイント。"""
    task = get_background_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return BackgroundTaskOut(**task)


# ---------------------------------------------------------------------------
# Course Builder Chat
# ---------------------------------------------------------------------------

_COURSE_BUILDER_SYSTEM_PROMPT = """あなたは大学教員が学習コース（シラバス）を設計するのを支援するAIアシスタントです。

**あなたの役割:**
- 教員の要望に基づいて、体系的な学習コースの構成案を段階的に作成・改善する
- 不足している前提知識や学習順序の問題を指摘する
- 各章・トピックが論理的に繋がるよう構成を提案する

**コース構成のJSONスキーマ (course_draft):**
生成するコース構成案は以下の形式に従ってください:
{
  "title": "コースタイトル",
  "target_audience": "対象者（例: 物理学専攻の大学院1年生）",
  "goal": "到達目標",
  "prerequisites": ["前提知識1", "前提知識2"],
  "chapters": [
    {
      "title": "章タイトル",
      "topics": [
        {"title": "トピック名", "prerequisites": []},
        {"title": "トピック名", "prerequisites": ["前のトピック名"]}
      ]
    }
  ],
  "concepts": [
    {"name": "概念名", "children": ["子概念1", "子概念2"]}
  ],
  "sources": []
}

**対話の進め方:**
1. まず教員の要望（テーマ、対象者、使用教材等）をヒアリングする
2. 初期のコース構成案を提示する
3. 教員のフィードバックに基づいて構成案を改善する
4. 前提知識の不足や学習順序の問題があれば指摘する

**重要なルール:**
- 応答は必ず日本語で行う
- コース構成案を提示・更新する場合は、応答テキストの最後に `---COURSE_DRAFT_JSON---` という区切り文字の後にJSONを出力する
- JSONは上記スキーマに従った valid JSON であること
- 構成案がまだ不完全な場合でも、現時点の案を出力する
- 教員が単なる質問をしている場合（構成変更を伴わない場合）は区切り文字とJSONを出力しない
- topics[].prerequisites には、そのトピックを学ぶ前に習得しておくべき**同コース内の**トピックのタイトルを列挙する
  - 例: 第2章のトピックは第1章のトピックタイトルを prerequisites に入れる
  - 最初のトピックや前提知識不要なトピックは prerequisites を空配列 [] にする
- sources フィールドは常に空配列 [] のままにすること（教材はシステムが自動的に設定する）"""


@router.post(
    "/course-builder/chat",
    response_model=CourseBuilderChatResponse,
)
def course_builder_chat(
    body: CourseBuilderChatRequest,
    current_user: dict = Depends(_require_teacher),
) -> CourseBuilderChatResponse:
    """教員がAIと対話しながらコースを設計するエンドポイント。"""
    messages: list[dict] = [
        {"role": "system", "content": _COURSE_BUILDER_SYSTEM_PROMPT},
    ]

    # 選択教材のタイトル・分野のみをコンテキストとして渡す（material_id は含めない）
    if body.selected_material_ids:
        try:
            pg_session = _pg_session()
            try:
                placeholders = ", ".join(f":mid_{i}" for i in range(len(body.selected_material_ids)))
                params: dict = {}
                for i, mid in enumerate(body.selected_material_ids):
                    params[f"mid_{i}"] = mid
                records = pg_session.execute(
                    sa_text(f"""
                        SELECT title, filename, knowledge_graph
                        FROM documents
                        WHERE source_path IN ({placeholders}) AND status = 'completed'
                    """),
                    params,
                ).fetchall()
            finally:
                pg_session.close()

            if records:
                materials_ctx = "## 使用する教材:\n"
                for r in records:
                    title = r[0] or r[1] or ""
                    materials_ctx += f"- {title}"
                    if r[2] and isinstance(r[2], dict) and r[2].get("domain"):
                        materials_ctx += f" (分野: {r[2]['domain']})"
                    materials_ctx += "\n"
                messages.append({
                    "role": "user",
                    "content": materials_ctx + "\n上記の教材を使ってコースを設計してください。",
                })
                messages.append({
                    "role": "assistant",
                    "content": "承知しました。これらの教材を踏まえてコース設計を支援します。",
                })
        except Exception:
            logger.warning("Failed to load selected materials context for course builder", exc_info=True)

    for turn in body.history:
        messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": body.message})

    try:
        raw_answer = generate_text(messages=messages, temperature=0.4)
    except Exception as exc:
        logger.exception("Course builder chat LLM call failed")
        raise HTTPException(status_code=500, detail=f"Chat failed: {exc}") from exc

    answer = raw_answer
    course_draft = None

    if "---COURSE_DRAFT_JSON---" in raw_answer:
        parts = raw_answer.split("---COURSE_DRAFT_JSON---", 1)
        answer = parts[0].strip()
        json_part = parts[1].strip()
        if json_part.startswith("```"):
            json_part = json_part.split("\n", 1)[1] if "\n" in json_part else json_part[3:]
        if json_part.endswith("```"):
            json_part = json_part[:-3]
        json_part = json_part.strip()
        if json_part.startswith("json"):
            json_part = json_part[4:].strip()
        try:
            course_draft = json.loads(json_part)
        except Exception:
            logger.warning("Failed to parse course_draft JSON: %s", json_part[:200])

    # 選択教材の正しい material_id を確定的に course_draft["sources"] に注入する
    if course_draft is not None and body.selected_material_ids:
        try:
            pg_session = _pg_session()
            try:
                placeholders = ", ".join(f":mid_{i}" for i in range(len(body.selected_material_ids)))
                params = {}
                for i, mid in enumerate(body.selected_material_ids):
                    params[f"mid_{i}"] = mid
                records = pg_session.execute(
                    sa_text(f"""
                        SELECT source_path, title, filename
                        FROM documents
                        WHERE source_path IN ({placeholders}) AND status = 'completed'
                    """),
                    params,
                ).fetchall()
            finally:
                pg_session.close()

            sources = []
            for r in records:
                sources.append({
                    "material_id": r[0] or "",
                    "title": r[1] or r[2] or "",
                    "subtitle": "",
                })
            course_draft["sources"] = sources
        except Exception:
            logger.warning("Failed to inject material sources into course_draft", exc_info=True)

    logger.info(
        "Course builder chat for user=%s, draft=%s",
        current_user["id"],
        "yes" if course_draft else "no",
    )

    if body.session_id:
        updated_history = body.history + [
            {"role": "user", "content": body.message},
            {"role": "assistant", "content": answer},
        ]
        save_cb_session(current_user["id"], body.session_id, updated_history, course_draft)

    return CourseBuilderChatResponse(answer=answer, course_draft=course_draft)


# ---------------------------------------------------------------------------
# Course Builder Sessions
# ---------------------------------------------------------------------------


@router.post("/course-builder/sessions", response_model=CourseBuilderSessionOut, status_code=201)
def create_cb_session(
    body: CourseBuilderSessionCreate,
    current_user: dict = Depends(_require_teacher),
) -> CourseBuilderSessionOut:
    """コース構築セッションを新規作成する。"""
    session_id = str(uuid.uuid4())[:12]
    session = _pg_session()
    try:
        session.execute(
            sa_text("""
                INSERT INTO course_builder_sessions (id, user_id, title, history, course_draft)
                VALUES (:session_id, CAST(:user_id AS uuid), :title, '[]', null)
            """),
            {"session_id": session_id, "user_id": current_user["id"], "title": body.title},
        )
        row = session.execute(
            sa_text("SELECT created_at, updated_at FROM course_builder_sessions WHERE id = :id"),
            {"id": session_id},
        ).fetchone()
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    created_at = row[0].isoformat() if row and row[0] else ""
    updated_at = row[1].isoformat() if row and row[1] else ""
    logger.info("Created course builder session %s for user=%s", session_id, current_user["id"])
    return CourseBuilderSessionOut(
        session_id=session_id, title=body.title,
        created_at=created_at, updated_at=updated_at,
    )


@router.get("/course-builder/sessions", response_model=list[CourseBuilderSessionOut])
def list_cb_sessions(
    current_user: dict = Depends(_require_teacher),
) -> list[CourseBuilderSessionOut]:
    """コース構築セッション一覧を返す（更新日時の降順）。"""
    session = _pg_session()
    try:
        records = session.execute(
            sa_text("""
                SELECT id, title, created_at, updated_at
                FROM course_builder_sessions
                WHERE user_id = CAST(:user_id AS uuid)
                ORDER BY updated_at DESC
            """),
            {"user_id": current_user["id"]},
        ).fetchall()
    finally:
        session.close()
    return [
        CourseBuilderSessionOut(
            session_id=r[0],
            title=r[1] or "新しいセッション",
            created_at=r[2].isoformat() if r[2] else "",
            updated_at=r[3].isoformat() if r[3] else "",
        )
        for r in records
    ]


@router.get("/course-builder/sessions/{session_id}")
def get_cb_session(
    session_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """コース構築セッションの詳細（履歴・draft含む）を返す。"""
    session = _pg_session()
    try:
        record = session.execute(
            sa_text("""
                SELECT id, title, history, course_draft, created_at, updated_at
                FROM course_builder_sessions
                WHERE id = :session_id AND user_id = CAST(:user_id AS uuid)
            """),
            {"session_id": session_id, "user_id": current_user["id"]},
        ).fetchone()
    finally:
        session.close()

    if not record:
        raise HTTPException(status_code=404, detail="Session not found")

    history = record[2] if isinstance(record[2], list) else (
        json.loads(record[2]) if record[2] else []
    )
    course_draft = record[3] if isinstance(record[3], dict) else (
        json.loads(record[3]) if record[3] else None
    )

    return {
        "session_id": record[0],
        "title": record[1] or "新しいセッション",
        "history": history,
        "course_draft": course_draft,
        "created_at": record[4].isoformat() if record[4] else "",
        "updated_at": record[5].isoformat() if record[5] else "",
    }


@router.put("/course-builder/sessions/{session_id}", response_model=CourseBuilderSessionOut)
def update_cb_session(
    session_id: str,
    body: CourseBuilderSessionUpdate,
    current_user: dict = Depends(_require_teacher),
) -> CourseBuilderSessionOut:
    """コース構築セッションのタイトル・履歴・draft を更新する。"""
    session = _pg_session()
    try:
        record = session.execute(
            sa_text("""
                SELECT title, history, course_draft, created_at
                FROM course_builder_sessions
                WHERE id = :session_id AND user_id = CAST(:user_id AS uuid)
            """),
            {"session_id": session_id, "user_id": current_user["id"]},
        ).fetchone()
        if not record:
            raise HTTPException(status_code=404, detail="Session not found")

        new_title = body.title if body.title is not None else (record[0] or "新しいセッション")
        new_history = (
            json.dumps(body.history, ensure_ascii=False)
            if body.history is not None
            else json.dumps(
                record[1] if isinstance(record[1], list) else (json.loads(record[1]) if record[1] else []),
                ensure_ascii=False,
            )
        )
        new_draft = (
            json.dumps(body.course_draft, ensure_ascii=False)
            if body.course_draft is not None
            else (json.dumps(record[2]) if record[2] else None)
        )

        updated = session.execute(
            sa_text("""
                UPDATE course_builder_sessions
                SET title = :title,
                    history = CAST(:history AS jsonb),
                    course_draft = CAST(:draft AS jsonb),
                    updated_at = now()
                WHERE id = :session_id AND user_id = CAST(:user_id AS uuid)
                RETURNING updated_at
            """),
            {
                "session_id": session_id,
                "user_id": current_user["id"],
                "title": new_title,
                "history": new_history,
                "draft": new_draft,
            },
        ).fetchone()
        session.commit()
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    created_at = record[3].isoformat() if record[3] else ""
    updated_at = updated[0].isoformat() if updated and updated[0] else ""
    return CourseBuilderSessionOut(
        session_id=session_id, title=new_title,
        created_at=created_at, updated_at=updated_at,
    )


# ---------------------------------------------------------------------------
# Course publish & unanswered queries
# ---------------------------------------------------------------------------


@router.get("/courses")
def list_teacher_courses(
    current_user: dict = Depends(_require_teacher),
) -> list[dict]:
    """教員が所有するコース一覧を返す（コースビルダーでのインポート用）。"""
    session = _pg_session()
    try:
        records = session.execute(
            sa_text("""
                SELECT id, title,
                       COALESCE(is_template, false) AS is_template,
                       COALESCE(is_published, false) AS is_published,
                       created_at, updated_at
                FROM learning_courses
                WHERE user_id = CAST(:user_id AS uuid)
                ORDER BY updated_at DESC
            """),
            {"user_id": current_user["id"]},
        ).fetchall()
    finally:
        session.close()

    return [
        {
            "id": r[0],
            "title": r[1],
            "is_template": bool(r[2]),
            "is_published": bool(r[3]),
            "created_at": r[4].isoformat() if r[4] else "",
            "updated_at": r[5].isoformat() if r[5] else "",
        }
        for r in records
    ]


@router.get("/courses/{course_id}/draft-format")
def get_course_as_draft(
    course_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """登録済みコースのデータを course_draft 形式に変換して返す。

    Course Builder にインポートするためのアダプタエンドポイント。
    """
    session = _pg_session()
    try:
        record = session.execute(
            sa_text("""
                SELECT data, title FROM learning_courses
                WHERE id = :course_id AND user_id = CAST(:user_id AS uuid)
                LIMIT 1
            """),
            {"course_id": course_id, "user_id": current_user["id"]},
        ).fetchone()
    finally:
        session.close()

    if not record:
        raise HTTPException(status_code=404, detail="Course not found")

    data = record[0] if isinstance(record[0], dict) else (
        json.loads(record[0]) if record[0] else {}
    )
    course_title = record[1] or data.get("title", "")

    # --- Convert registered course data → course_draft format ---
    topics = data.get("topics", [])
    chapters_raw = data.get("chapters", [])

    # Group topics by chapter_index
    chapter_topics: dict[int, list] = {}
    for t in topics:
        ci = t.get("chapter_index", 0)
        if ci not in chapter_topics:
            chapter_topics[ci] = []
        prereqs = []
        for p in t.get("prerequisites", []):
            name = p.get("name", "") if isinstance(p, dict) else str(p)
            if name:
                prereqs.append(name)
        chapter_topics[ci].append({
            "title": t.get("title", ""),
            "prerequisites": prereqs,
        })

    # Build chapters with topics
    chapters = []
    for ci, ch in enumerate(chapters_raw):
        chapters.append({
            "title": ch.get("title", ""),
            "topics": chapter_topics.get(ci, []),
        })

    # Build concepts
    concepts = []
    for c in data.get("concepts", []):
        concepts.append({
            "name": c.get("name", ""),
            "children": c.get("children", []),
        })

    # Build sources
    sources = []
    for s in data.get("sources", []):
        sources.append({
            "title": s.get("title", ""),
            "subtitle": s.get("subtitle", ""),
            "license": s.get("license", ""),
            "used_section": s.get("used_section", ""),
            "material_id": s.get("material_id", ""),
        })

    draft = {
        "title": course_title,
        "target_audience": "",
        "goal": "",
        "prerequisites": [],
        "chapters": chapters,
        "concepts": concepts,
        "sources": sources,
    }

    return {
        "course_id": course_id,
        "course_title": course_title,
        "course_draft": draft,
    }


@router.put("/courses/{course_id}/publish")
def publish_course(
    course_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """コースを学生に公開する。"""
    session = _pg_session()
    try:
        result = session.execute(
            sa_text("""
                UPDATE learning_courses
                SET is_published = true, is_template = true, updated_at = now()
                WHERE id = :course_id AND user_id = CAST(:user_id AS uuid)
                RETURNING id
            """),
            {"course_id": course_id, "user_id": current_user["id"]},
        ).fetchone()
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    if not result:
        raise HTTPException(status_code=404, detail="Course not found")
    logger.info("Course %s published by user=%s", course_id, current_user["id"])
    return {"course_id": course_id, "is_published": True}


@router.get("/courses/{course_id}/unanswered-queries")
def list_unanswered_queries(
    course_id: str,
    current_user: dict = Depends(_require_teacher),
) -> list[dict]:
    """コースに紐づくつまづきデータ（RAG未回答クエリ）を返す。"""
    session = _pg_session()
    try:
        rows = session.execute(
            sa_text("""
                SELECT uql.id, uql.topic_id, uql.question, uql.asked_at,
                       u.display_name AS student_name
                FROM unanswered_query_logs uql
                JOIN users u ON uql.user_id = u.id
                WHERE uql.course_id = :course_id
                ORDER BY uql.asked_at DESC
                LIMIT 200
            """),
            {"course_id": course_id},
        ).fetchall()
    finally:
        session.close()
    return [
        {
            "id": r[0],
            "topic_id": r[1],
            "question": r[2],
            "asked_at": r[3].isoformat() if r[3] else "",
            "student_name": r[4] or "",
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# User Management
# ---------------------------------------------------------------------------


@router.post("/users/student", response_model=UserOut, status_code=201)
def create_student(
    body: CreateUserRequest,
    current_user: dict = Depends(_require_teacher),
) -> UserOut:
    """教員が学生アカウントを作成する。"""
    session = _pg_session()
    try:
        existing = session.execute(
            sa_text("SELECT id FROM users WHERE display_name = :username LIMIT 1"),
            {"username": body.username},
        ).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="Username already taken")

        user_id = uuid.uuid4()
        hashed_pw = _hash_password(body.password)
        session.execute(
            sa_text("""
                INSERT INTO users (id, email, display_name, role, password_hash)
                VALUES (:id, :email, :username, 'learner', :pw)
            """),
            {"id": user_id, "email": body.email, "username": body.username, "pw": hashed_pw},
        )
        session.commit()
    finally:
        session.close()

    logger.info("Teacher '%s' created student '%s' (id=%s)", current_user["username"], body.username, user_id)
    return UserOut(id=str(user_id), username=body.username, email=body.email, role=ROLE_STUDENT)


@router.post("/users/teacher", response_model=UserOut, status_code=201)
def create_teacher(
    body: CreateUserRequest,
    current_user: dict = Depends(_require_system_admin),
) -> UserOut:
    """管理者が教員アカウントを作成する。"""
    session = _pg_session()
    try:
        existing = session.execute(
            sa_text("SELECT id FROM users WHERE display_name = :username LIMIT 1"),
            {"username": body.username},
        ).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="Username already taken")

        user_id = uuid.uuid4()
        hashed_pw = _hash_password(body.password)
        session.execute(
            sa_text("""
                INSERT INTO users (id, email, display_name, role, password_hash)
                VALUES (:id, :email, :username, 'instructor', :pw)
            """),
            {"id": user_id, "email": body.email, "username": body.username, "pw": hashed_pw},
        )
        session.commit()
    finally:
        session.close()

    logger.info("Admin '%s' created teacher '%s' (id=%s)", current_user["username"], body.username, user_id)
    return UserOut(id=str(user_id), username=body.username, email=body.email, role=ROLE_TEACHER)


# ---------------------------------------------------------------------------
# Schema Evolution (Issue #36)
# ---------------------------------------------------------------------------


@router.get("/schema/types", response_model=list[SchemaTypeOut])
def list_ontology_types(
    current_user: dict = Depends(_require_teacher),
) -> list[SchemaTypeOut]:
    """登録済みOntologyType一覧を返す。"""
    types = get_ontology_types()
    return [SchemaTypeOut(**t) for t in types]


@router.post("/schema/types", response_model=SchemaTypeOut, status_code=201)
def create_ontology_type(
    body: SchemaTypeCreateRequest,
    current_user: dict = Depends(_require_teacher),
) -> SchemaTypeOut:
    """新しいOntologyTypeを追加する。"""
    add_ontology_type(body.id, body.label, body.description)
    logger.info("OntologyType '%s' added by user=%s", body.id, current_user["id"])
    return SchemaTypeOut(id=body.id, label=body.label, description=body.description, is_builtin=False)


@router.get("/schema/predicates", response_model=list[SchemaTypeOut])
def list_predicates(
    current_user: dict = Depends(_require_teacher),
) -> list[SchemaTypeOut]:
    """登録済みCorePredicate一覧を返す。"""
    preds = get_predicates()
    return [SchemaTypeOut(**p) for p in preds]


@router.post("/schema/predicates", response_model=SchemaTypeOut, status_code=201)
def create_predicate(
    body: SchemaTypeCreateRequest,
    current_user: dict = Depends(_require_teacher),
) -> SchemaTypeOut:
    """新しいCorePredicateを追加する。"""
    add_predicate(body.id, body.label, body.description)
    logger.info("Predicate '%s' added by user=%s", body.id, current_user["id"])
    return SchemaTypeOut(id=body.id, label=body.label, description=body.description, is_builtin=False)


@router.get("/schema-proposals", response_model=list[SchemaProposalOut])
def list_schema_proposals(
    status: str | None = None,
    current_user: dict = Depends(_require_teacher),
) -> list[SchemaProposalOut]:
    """スキーマ拡張提案一覧を返す。"""
    proposals = get_proposals(status=status)
    return [SchemaProposalOut(**p) for p in proposals]


@router.post("/schema-proposals/analyze", response_model=SchemaProposalOut | dict)
def trigger_schema_analysis(
    current_user: dict = Depends(_require_teacher),
) -> SchemaProposalOut | dict:
    """未回答クエリを分析してスキーマ拡張提案を生成する。"""
    result = analyze_unanswered_queries()
    if result is None:
        return {"message": "分析の結果、スキーマ拡張の提案はありません。未回答クエリが不足しているか、現在のスキーマで十分カバーされています。"}
    return SchemaProposalOut(**result)


@router.put("/schema-proposals/{proposal_id}/approve")
def approve_schema_proposal(
    proposal_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """スキーマ拡張提案を承認し、新しいType/Predicateを登録する。"""
    success = approve_proposal(proposal_id, current_user["id"])
    if not success:
        raise HTTPException(status_code=404, detail="Proposal not found or already reviewed")

    # 再抽出ジョブをキューに追加
    job = enqueue_reextraction(proposal_id)
    logger.info(
        "Schema proposal %s approved by user=%s, reextraction job %s enqueued",
        proposal_id, current_user["id"], job["job_id"],
    )
    return {
        "proposal_id": proposal_id,
        "status": "approved",
        "reextraction_job": job,
    }


@router.put("/schema-proposals/{proposal_id}/reject")
def reject_schema_proposal(
    proposal_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """スキーマ拡張提案を却下する。"""
    success = reject_proposal(proposal_id, current_user["id"])
    if not success:
        raise HTTPException(status_code=404, detail="Proposal not found or already reviewed")
    return {"proposal_id": proposal_id, "status": "rejected"}


@router.get("/reextraction-jobs", response_model=list[ReextractionJobOut])
def list_reextraction_jobs(
    current_user: dict = Depends(_require_teacher),
) -> list[ReextractionJobOut]:
    """再抽出ジョブ一覧を返す。"""
    jobs = get_reextraction_jobs()
    return [ReextractionJobOut(**j) for j in jobs]


# ---------------------------------------------------------------------------
# Shadow Testing / Simulation (Issue #45)
# ---------------------------------------------------------------------------


@router.post(
    "/schema-proposals/{proposal_id}/simulate",
    response_model=SimulationResponse,
)
def simulate_schema_proposal(
    proposal_id: str,
    current_user: dict = Depends(_require_teacher),
) -> SimulationResponse:
    """スキーマ提案のShadow Testingシミュレーションを実行する。

    Target/Similar/Control の3層のドキュメントに対して新スキーマを
    テスト適用し、差分（Diff）を返す。
    """
    from core.simulator import run_simulation

    result = run_simulation(proposal_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Proposal not found",
        )
    return SimulationResponse(**result)


@router.put("/schema-proposals/{proposal_id}/approve-with-scope")
def approve_schema_proposal_with_scope(
    proposal_id: str,
    body: ApproveWithScopeRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """スキーマ提案をスコープ付きで承認する。

    scope="full": システム全体に適用
    scope="canary": 指定コースのみに適用（カナリアリリース）
    """
    from core.simulator import approve_with_scope

    result = approve_with_scope(
        proposal_id=proposal_id,
        reviewer_id=current_user["id"],
        scope=body.scope,
        course_ids=body.course_ids,
    )
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Proposal not found or already reviewed",
        )
    logger.info(
        "Schema proposal %s approved (scope=%s) by user=%s",
        proposal_id, body.scope, current_user["id"],
    )
    return result


# ---------------------------------------------------------------------------
# Lecture Script Studio (Issue #70) — サブルーターとしてインクルード
# ---------------------------------------------------------------------------
from routes.lecture_studio import router as _lecture_studio_router  # noqa: E402

router.include_router(_lecture_studio_router)
