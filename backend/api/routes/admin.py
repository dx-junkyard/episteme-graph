"""Episteme Graph — 管理者エンドポイント (/api/admin)。"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
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
    CourseGroupPermissionOut,
    CourseGroupPermissionUpsertRequest,
    CreateUserRequest,
    DeleteConfirmRequest,
    MaterialOut,
    ReextractionJobOut,
    SimulationResponse,
    SchemaProposalOut,
    SchemaTypeCreateRequest,
    SchemaTypeOut,
    UserOut,
    VisibilityUpdateRequest,
)
from services import (
    _material_lock,
    _material_status,
    create_background_task,
    get_background_task,
    get_course_group_permissions,
    get_user_group_ids,
    process_material_background,
    save_cb_session,
    user_can_access_group,
    user_can_edit_course,
    user_can_view_course,
    user_owns_course,
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
from core.storage import get_storage_client

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
    pdf_object_name = f"uploads/{material_id}.pdf"
    doc_id = uuid.uuid4()
    task_id = str(uuid.uuid4())[:12]
    now = datetime.datetime.utcnow().isoformat()

    try:
        get_storage_client().upload_pdf("raw-papers", pdf_object_name, pdf_bytes)
    except Exception:
        logger.exception("Failed to store uploaded PDF for material %s", material_id)
        raise HTTPException(status_code=500, detail="PDF storage failed")

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
    """アップロード済み教材の一覧を返す。

    Issue #128: 「自分がアップロードしたもの」に加え、以下を含める。
    - visibility='public' の教材
    - visibility='group' かつ自分の参加グループで共有された教材
    - 自分が editor/viewer 権限を持つコースで参照されている教材
      （course_group_permissions 経由）
    """
    user_groups = get_user_group_ids(current_user["id"])

    params: dict = {"user_id": current_user["id"]}
    group_clause = "FALSE"
    course_mat_clause = "FALSE"

    if user_groups:
        gph = ", ".join(f"CAST(:g_{i} AS uuid)" for i in range(len(user_groups)))
        for i, gid in enumerate(user_groups):
            params[f"g_{i}"] = gid
        group_clause = f"(visibility = 'group' AND group_id IN ({gph}))"
        course_mat_clause = f"""
            source_path IN (
                SELECT (jsonb_array_elements(lc.data->'sources')->>'material_id')
                FROM learning_courses lc
                JOIN course_group_permissions cgp ON cgp.course_id = lc.id
                JOIN group_members gm ON gm.group_id = cgp.group_id
                WHERE gm.user_id = CAST(:user_id AS uuid)
                  AND cgp.group_id IN ({gph})
                  AND cgp.permission IN ('viewer', 'editor')
                  AND lc.data IS NOT NULL
                  AND jsonb_typeof(lc.data->'sources') = 'array'
            )
        """

    session = _pg_session()
    try:
        records = session.execute(
            sa_text(f"""
                SELECT source_path, filename, title, status, created_at, knowledge_graph,
                       COALESCE(visibility, 'private'), group_id
                FROM documents
                WHERE filename IS NOT NULL
                  AND (
                      uploaded_by = CAST(:user_id AS uuid)
                      OR visibility = 'public'
                      OR {group_clause}
                      OR {course_mat_clause}
                  )
                ORDER BY created_at DESC
            """),
            params,
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
            visibility=r[6] or "private",
            group_id=str(r[7]) if r[7] else None,
        ))

    return materials


@router.get("/materials/{material_id}", response_model=MaterialOut)
def get_material(
    material_id: str,
    current_user: dict = Depends(_require_teacher),
) -> MaterialOut:
    """教材の詳細情報（ナレッジグラフ含む）を返す。

    Issue #128: list_materials と同じアクセスポリシーで判定する。
    """
    user_groups = get_user_group_ids(current_user["id"])

    params: dict = {"user_id": current_user["id"], "material_id": material_id}
    group_clause = "FALSE"
    course_mat_clause = "FALSE"

    if user_groups:
        gph = ", ".join(f"CAST(:g_{i} AS uuid)" for i in range(len(user_groups)))
        for i, gid in enumerate(user_groups):
            params[f"g_{i}"] = gid
        group_clause = f"(visibility = 'group' AND group_id IN ({gph}))"
        course_mat_clause = f"""
            source_path IN (
                SELECT (jsonb_array_elements(lc.data->'sources')->>'material_id')
                FROM learning_courses lc
                JOIN course_group_permissions cgp ON cgp.course_id = lc.id
                JOIN group_members gm ON gm.group_id = cgp.group_id
                WHERE gm.user_id = CAST(:user_id AS uuid)
                  AND cgp.group_id IN ({gph})
                  AND cgp.permission IN ('viewer', 'editor')
                  AND lc.data IS NOT NULL
                  AND jsonb_typeof(lc.data->'sources') = 'array'
            )
        """

    session = _pg_session()
    try:
        record = session.execute(
            sa_text(f"""
                SELECT source_path, filename, title, status, created_at, knowledge_graph,
                       COALESCE(visibility, 'private'), group_id
                FROM documents
                WHERE source_path = :material_id
                  AND (
                      uploaded_by = CAST(:user_id AS uuid)
                      OR visibility = 'public'
                      OR {group_clause}
                      OR {course_mat_clause}
                  )
                LIMIT 1
            """),
            params,
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
        visibility=record[6] or "private",
        group_id=str(record[7]) if record[7] else None,
    )


@router.get("/materials/{material_id}/pdf")
def get_material_pdf(
    material_id: str,
    current_user: dict = Depends(_require_teacher),
) -> Response:
    """認証済みユーザーに教材PDFをプロキシ配信する。"""
    material = get_material(material_id, current_user)
    object_candidates = [
        f"uploads/{material_id}.pdf",
        material.filename,
        material.material_id,
    ]
    storage = get_storage_client()
    for object_name in object_candidates:
        if not object_name:
            continue
        try:
            pdf_bytes = storage.get_object("raw-papers", object_name)
            return Response(
                content=pdf_bytes,
                media_type="application/pdf",
                headers={
                    "Content-Disposition": f'inline; filename="{material_id}.pdf"',
                    "Cache-Control": "private, max-age=300",
                },
            )
        except Exception as exc:
            logger.debug(
                "PDF not found in MinIO: bucket=raw-papers object=%s error=%s",
                object_name, exc,
            )
            continue
    logger.warning("PDF object not found for material=%s candidates=%s", material_id, object_candidates)
    raise HTTPException(status_code=404, detail="PDF object not found")


@router.put("/materials/{material_id}/visibility")
def update_material_visibility(
    material_id: str,
    body: VisibilityUpdateRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """教材の開示範囲を更新する。"""
    if body.visibility not in ("public", "group", "private"):
        raise HTTPException(status_code=400, detail=f"Invalid visibility: {body.visibility}")
    if body.visibility == "group":
        if not body.group_id:
            raise HTTPException(status_code=400, detail="visibility='group' requires group_id")
        if not user_can_access_group(current_user["id"], body.group_id):
            raise HTTPException(status_code=403, detail="指定されたグループに参加していません")

    session = _pg_session()
    try:
        result = session.execute(
            sa_text("""
                UPDATE documents
                SET visibility = :visibility,
                    group_id = CAST(:group_id AS uuid),
                    updated_at = now()
                WHERE source_path = :material_id
                  AND uploaded_by = CAST(:user_id AS uuid)
                RETURNING id
            """),
            {
                "visibility": body.visibility,
                "group_id": body.group_id if body.visibility == "group" else None,
                "material_id": material_id,
                "user_id": current_user["id"],
            },
        ).fetchone()
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    if not result:
        raise HTTPException(status_code=404, detail="Material not found")
    logger.info(
        "Material %s visibility=%s group=%s by user=%s",
        material_id, body.visibility, body.group_id, current_user["id"],
    )
    return {
        "material_id": material_id,
        "visibility": body.visibility,
        "group_id": body.group_id if body.visibility == "group" else None,
    }


@router.put("/courses/{course_id}/visibility")
def update_course_visibility(
    course_id: str,
    body: VisibilityUpdateRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """コースの開示範囲を更新する。"""
    if body.visibility not in ("public", "group", "private"):
        raise HTTPException(status_code=400, detail=f"Invalid visibility: {body.visibility}")
    if body.visibility == "group":
        if not body.group_id:
            raise HTTPException(status_code=400, detail="visibility='group' requires group_id")
        if not user_can_access_group(current_user["id"], body.group_id):
            raise HTTPException(status_code=403, detail="指定されたグループに参加していません")

    session = _pg_session()
    try:
        result = session.execute(
            sa_text("""
                UPDATE learning_courses
                SET visibility = :visibility,
                    group_id = CAST(:group_id AS uuid),
                    is_published = CASE WHEN :visibility = 'public' THEN true ELSE is_published END,
                    is_template = CASE WHEN :visibility = 'public' THEN true ELSE is_template END,
                    updated_at = now()
                WHERE id = :course_id AND user_id = CAST(:user_id AS uuid)
                RETURNING id
            """),
            {
                "visibility": body.visibility,
                "group_id": body.group_id if body.visibility == "group" else None,
                "course_id": course_id,
                "user_id": current_user["id"],
            },
        ).fetchone()
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    if not result:
        raise HTTPException(status_code=404, detail="Course not found")
    logger.info(
        "Course %s visibility=%s group=%s by user=%s",
        course_id, body.visibility, body.group_id, current_user["id"],
    )
    return {
        "course_id": course_id,
        "visibility": body.visibility,
        "group_id": body.group_id if body.visibility == "group" else None,
    }


# ---------------------------------------------------------------------------
# Material Deletion
# ---------------------------------------------------------------------------


@router.delete("/materials/{material_id}")
def delete_material(
    material_id: str,
    body: DeleteConfirmRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """教材を削除する。紐づくコースも同時に削除される。

    confirm_name がタイトルと一致しない場合は 400 を返す。
    """
    session = _pg_session()
    try:
        # 1) 教材の存在確認 & タイトル照合
        doc = session.execute(
            sa_text("""
                SELECT id, title, filename, source_path
                FROM documents
                WHERE source_path = :material_id
                  AND uploaded_by = CAST(:user_id AS uuid)
                LIMIT 1
            """),
            {"material_id": material_id, "user_id": current_user["id"]},
        ).fetchone()

        if not doc:
            raise HTTPException(status_code=404, detail="Material not found")

        doc_id = doc[0]
        doc_title = doc[1] or doc[2] or ""

        if body.confirm_name != doc_title:
            raise HTTPException(
                status_code=400,
                detail="確認用の名前が一致しません。正確な教材名を入力してください。",
            )

        # 2) この教材を sources に含むコースを特定して削除
        course_rows = session.execute(
            sa_text("""
                SELECT id FROM learning_courses
                WHERE user_id = CAST(:user_id AS uuid)
            """),
            {"user_id": current_user["id"]},
        ).fetchall()

        deleted_course_ids: list[str] = []
        for row in course_rows:
            course_id = row[0]
            course_data_row = session.execute(
                sa_text("SELECT data FROM learning_courses WHERE id = :cid"),
                {"cid": course_id},
            ).fetchone()
            if not course_data_row or not course_data_row[0]:
                continue
            data = course_data_row[0] if isinstance(course_data_row[0], dict) else json.loads(course_data_row[0])
            sources = data.get("sources", [])
            linked = any(
                s.get("material_id") == material_id for s in sources if isinstance(s, dict)
            )
            if linked:
                # 関連する学習チャット履歴を削除
                session.execute(
                    sa_text("DELETE FROM learning_chat_history WHERE course_id = :cid"),
                    {"cid": course_id},
                )
                # コース削除
                session.execute(
                    sa_text("DELETE FROM learning_courses WHERE id = :cid"),
                    {"cid": course_id},
                )
                deleted_course_ids.append(course_id)

        # 3) チャンク削除
        session.execute(
            sa_text("DELETE FROM chunks WHERE document_id = :doc_id"),
            {"doc_id": doc_id},
        )

        # 4) ドキュメント削除
        session.execute(
            sa_text("DELETE FROM documents WHERE id = :doc_id"),
            {"doc_id": doc_id},
        )

        session.commit()
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    logger.info(
        "Material %s (%s) deleted by user=%s, cascade-deleted courses: %s",
        material_id, doc_title, current_user["id"], deleted_course_ids,
    )
    return {
        "material_id": material_id,
        "deleted": True,
        "deleted_courses": deleted_course_ids,
    }


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

**【最重要】ソース文献への厳密な準拠:**
- 提案するすべての章・トピック・概念は、提供された教材の記述内容に基づかなければならない
- 教材に記載されていないトピックを「一般的だから」「基礎的だから」という理由で追加してはならない
- 教材の内容を超えた一般知識に基づく補足は、明示的に「教材外の補足」として区別すること
- 教材のチャンク（テキスト断片）やナレッジグラフが提供された場合、それらの情報を最大限活用してコースを設計すること

**【重要】段階的構造化の原則:**
- チャンクの並び順（chunk_index）は教材内の論理的な流れを反映しているため、この順序を尊重してコースの章立てを構成する
- 前提知識が必要な概念は、その前提となる概念より後の章・トピックに配置する（依存関係に基づいたシラバス）
- 初学者がその分野に初めて触れることを想定し、専門用語は最初に出現する章で定義・説明するよう構成する

**コース構成のJSONスキーマ (course_draft):**
生成するコース構成案は以下の形式に従ってください:
{
  "title": "コースタイトル",
  "domain": "コースの専門分野（例: 素粒子物理学、経済学、機械学習など）",
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
2. 提供された教材の内容（ナレッジグラフ、テキストチャンク）を精査し、教材の範囲と構成を把握する
3. 教材の内容に基づいた初期のコース構成案を提示する
4. 教員のフィードバックに基づいて構成案を改善する
5. 前提知識の不足や学習順序の問題があれば指摘する

**重要なルール:**
- 応答は必ず日本語で行う
- コース構成案を提示・更新する場合は、応答テキストの最後に `---COURSE_DRAFT_JSON---` という区切り文字の後にJSONを出力する
- JSONは上記スキーマに従った valid JSON であること
- 構成案がまだ不完全な場合でも、現時点の案を出力する
- 教員が単なる質問をしている場合（構成変更を伴わない場合）は区切り文字とJSONを出力しない
- topics[].prerequisites には、そのトピックを学ぶ前に習得しておくべき**同コース内の**トピックのタイトルを列挙する
  - 例: 第2章のトピックは第1章のトピックタイトルを prerequisites に入れる
  - 最初のトピックや前提知識不要なトピックは prerequisites を空配列 [] にする
- domain フィールドは教材のナレッジグラフに記載されている「**分野:**」から引き継ぐこと
  - 教材の分野情報がなければ、コースの内容を踏まえて適切な専門分野名を設定する
- sources フィールドは常に空配列 [] のままにすること（教材はシステムが自動的に設定する）"""


# ---------------------------------------------------------------------------
# Course Builder: Material Context Builder
# ---------------------------------------------------------------------------

# チャンクテキストのサンプリング上限（1教材あたり）
_MAX_CHUNK_CHARS_PER_MATERIAL = 4000


def _build_material_context(
    material_ids: list[str],
    pg_session_factory=None,
) -> str | None:
    """選択された教材のナレッジグラフとチャンクテキストからコンテキスト文字列を構築する。

    Returns None if no usable context could be built.
    """
    if not material_ids:
        return None

    _get_pg = pg_session_factory or _pg_session
    session = _get_pg()
    try:
        # --- 1) ドキュメントメタデータ + knowledge_graph を取得 ---
        placeholders = ", ".join(f":mid_{i}" for i in range(len(material_ids)))
        params: dict = {}
        for i, mid in enumerate(material_ids):
            params[f"mid_{i}"] = mid

        doc_rows = session.execute(
            sa_text(f"""
                SELECT source_path, title, filename, knowledge_graph
                FROM documents
                WHERE source_path IN ({placeholders}) AND status = 'completed'
            """),
            params,
        ).fetchall()

        if not doc_rows:
            return None

        # --- 2) チャンクテキストを取得 (chunk_index 順) ---
        chunk_rows = session.execute(
            sa_text(f"""
                SELECT material_id, chunk_index, chapter, section, text
                FROM chunks
                WHERE material_id IN ({placeholders})
                ORDER BY material_id, chunk_index
            """),
            params,
        ).fetchall()
    finally:
        session.close()

    # --- 3) コンテキスト文字列を組み立て ---
    sections: list[str] = []
    sections.append("## 教材の内容詳細\n以下は選択された教材から抽出された詳細情報です。"
                     "コース設計はこの内容に基づいて行ってください。\n")

    for doc in doc_rows:
        mid = doc[0] or ""
        title = doc[1] or doc[2] or ""
        kg = doc[3] if doc[3] and isinstance(doc[3], dict) else {}

        sections.append(f"### 教材: {title}")

        # ナレッジグラフの概要
        if kg.get("summary"):
            sections.append(f"**概要:** {kg['summary']}")

        if kg.get("domain"):
            sections.append(f"**分野:** {kg['domain']}")

        # 主要概念
        concepts = kg.get("concepts", [])
        if concepts:
            concept_lines = []
            for c in concepts:
                name = c.get("name", "") if isinstance(c, dict) else str(c)
                desc = c.get("description", "") if isinstance(c, dict) else ""
                ctype = c.get("type", "") if isinstance(c, dict) else ""
                line = f"- {name}"
                if ctype:
                    line += f" ({ctype})"
                if desc:
                    line += f": {desc}"
                concept_lines.append(line)
            sections.append("**主要概念:**\n" + "\n".join(concept_lines))

        # チャンクテキストのサンプリング
        material_chunks = [r for r in chunk_rows if r[0] == mid]
        if material_chunks:
            sections.append("**教材テキスト（抜粋）:**")
            char_budget = _MAX_CHUNK_CHARS_PER_MATERIAL
            for chunk in material_chunks:
                if char_budget <= 0:
                    sections.append("... (以降省略)")
                    break
                idx = chunk[1]
                chapter = chunk[2] or ""
                section_ = chunk[3] or ""
                text = chunk[4] or ""
                header = f"[チャンク {idx}]"
                if chapter:
                    header += f" {chapter}"
                if section_:
                    header += f" / {section_}"
                snippet = text[:char_budget]
                if len(text) > char_budget:
                    snippet += "..."
                sections.append(f"{header}\n{snippet}")
                char_budget -= len(snippet)

        sections.append("")  # blank line separator

    return "\n".join(sections)


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

    # 選択教材のナレッジグラフ・チャンクテキストを含む詳細コンテキストを注入
    if body.selected_material_ids:
        try:
            material_context = _build_material_context(body.selected_material_ids)
            if material_context:
                messages.append({
                    "role": "user",
                    "content": material_context
                        + "\n\n上記の教材内容に基づいてコースを設計してください。"
                        "教材に記載されている内容の範囲内で、段階的に学べるコース構成を提案してください。",
                })
                messages.append({
                    "role": "assistant",
                    "content": "承知しました。提供された教材の内容を精査し、"
                        "教材の記述に基づいたコース設計を支援します。",
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
    """教員が管理できるコース一覧を返す。"""
    session = _pg_session()
    try:
        # 修正: LEFT JOINによる行増殖を防ぎ、権限の優先順位（owner > editor > viewer）を厳密化
        records = session.execute(
            sa_text("""
                SELECT lc.id, lc.title,
                       COALESCE(lc.is_template, false) AS is_template,
                       COALESCE(lc.is_published, false) AS is_published,
                       lc.created_at, lc.updated_at,
                       CASE 
                           WHEN lc.user_id = CAST(:user_id AS uuid) THEN 'owner'
                           WHEN EXISTS (
                               SELECT 1 FROM course_group_permissions cgp
                               JOIN group_members gm ON gm.group_id = cgp.group_id
                               WHERE cgp.course_id = lc.id 
                                 AND gm.user_id = CAST(:user_id AS uuid)
                                 AND cgp.permission = 'editor'
                           ) THEN 'editor'
                           ELSE 'viewer'
                       END AS role
                FROM learning_courses lc
                WHERE lc.user_id = CAST(:user_id AS uuid)
                   OR EXISTS (
                       SELECT 1 FROM course_group_permissions cgp
                       JOIN group_members gm ON gm.group_id = cgp.group_id
                       WHERE cgp.course_id = lc.id 
                         AND gm.user_id = CAST(:user_id AS uuid)
                         AND cgp.permission IN ('editor', 'viewer')
                   )
                ORDER BY lc.updated_at DESC
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
            "role": r[6],  # "owner" | "editor" | "viewer"
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
    所有者に加え、editor 権限グループのメンバーもアクセスできる。
    """
    if not user_can_edit_course(current_user["id"], course_id):
        raise HTTPException(status_code=404, detail="Course not found")

    session = _pg_session()
    try:
        record = session.execute(
            sa_text("""
                SELECT data, title FROM learning_courses
                WHERE id = :course_id
                LIMIT 1
            """),
            {"course_id": course_id},
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
        "domain": data.get("domain", ""),
        "target_audience": data.get("target_audience", ""),
        "goal": data.get("goal", ""),
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


# ---------------------------------------------------------------------------
# Course × Group Permissions (Issue #125)
# ---------------------------------------------------------------------------


@router.get(
    "/courses/{course_id}/groups",
    response_model=list[CourseGroupPermissionOut],
)
def list_course_group_permissions(
    course_id: str,
    current_user: dict = Depends(_require_teacher),
) -> list[CourseGroupPermissionOut]:
    """コースに紐づくグループ権限マッピングの一覧を返す。

    所有者、editor/viewer 権限グループのメンバーいずれもが現在の共有設定を
    参照できる（表示用）。変更は所有者のみ。
    """
    if not user_can_view_course(current_user["id"], course_id):
        raise HTTPException(status_code=404, detail="Course not found")
    rows = get_course_group_permissions(course_id)
    return [CourseGroupPermissionOut(**r) for r in rows]


@router.post(
    "/courses/{course_id}/groups",
    response_model=CourseGroupPermissionOut,
    status_code=201,
)
def upsert_course_group_permission(
    course_id: str,
    body: CourseGroupPermissionUpsertRequest,
    current_user: dict = Depends(_require_teacher),
) -> CourseGroupPermissionOut:
    """コースにグループ権限を付与する（既存なら権限を更新）。

    共有設定の変更はコースの所有者のみ可能。
    """
    if body.permission not in ("viewer", "editor"):
        raise HTTPException(
            status_code=400, detail="permission must be 'viewer' or 'editor'",
        )
    if not user_owns_course(current_user["id"], course_id):
        raise HTTPException(status_code=404, detail="Course not found")

    session = _pg_session()
    try:
        group_row = session.execute(
            sa_text("SELECT name FROM groups WHERE id = CAST(:gid AS uuid) LIMIT 1"),
            {"gid": body.group_id},
        ).fetchone()
        if not group_row:
            raise HTTPException(status_code=404, detail="Group not found")

        row = session.execute(
            sa_text("""
                INSERT INTO course_group_permissions (course_id, group_id, permission)
                VALUES (:course_id, CAST(:gid AS uuid), :permission)
                ON CONFLICT (course_id, group_id) DO UPDATE
                SET permission = EXCLUDED.permission,
                    updated_at = now()
                RETURNING course_id, group_id, permission, created_at, updated_at
            """),
            {
                "course_id": course_id,
                "gid": body.group_id,
                "permission": body.permission,
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

    logger.info(
        "Course %s group permission set: group=%s permission=%s by user=%s",
        course_id, body.group_id, body.permission, current_user["id"],
    )
    return CourseGroupPermissionOut(
        course_id=row[0],
        group_id=str(row[1]),
        group_name=group_row[0] or "",
        permission=row[2],
        created_at=row[3].isoformat() if row[3] else "",
        updated_at=row[4].isoformat() if row[4] else "",
    )


@router.delete("/courses/{course_id}/groups/{group_id}")
def delete_course_group_permission(
    course_id: str,
    group_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """コースからグループ権限マッピングを削除する。

    共有設定の変更はコースの所有者のみ可能。
    """
    if not user_owns_course(current_user["id"], course_id):
        raise HTTPException(status_code=404, detail="Course not found")

    session = _pg_session()
    try:
        result = session.execute(
            sa_text("""
                DELETE FROM course_group_permissions
                WHERE course_id = :course_id AND group_id = CAST(:gid AS uuid)
                RETURNING course_id
            """),
            {"course_id": course_id, "gid": group_id},
        ).fetchone()
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    if not result:
        raise HTTPException(status_code=404, detail="Permission mapping not found")
    logger.info(
        "Course %s group permission removed: group=%s by user=%s",
        course_id, group_id, current_user["id"],
    )
    return {"course_id": course_id, "group_id": group_id, "deleted": True}


@router.delete("/courses/{course_id}")
def delete_course(
    course_id: str,
    body: DeleteConfirmRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """コースを削除する。

    confirm_name がコースタイトルと一致しない場合は 400 を返す。
    所有者、または editor 権限グループのメンバーのみ削除可能。
    """
    if not user_can_edit_course(current_user["id"], course_id):
        raise HTTPException(status_code=404, detail="Course not found")

    session = _pg_session()
    try:
        record = session.execute(
            sa_text("""
                SELECT id, title FROM learning_courses
                WHERE id = :course_id
                LIMIT 1
            """),
            {"course_id": course_id},
        ).fetchone()

        if not record:
            raise HTTPException(status_code=404, detail="Course not found")

        course_title = record[1] or ""
        if body.confirm_name != course_title:
            raise HTTPException(
                status_code=400,
                detail="確認用の名前が一致しません。正確なコース名を入力してください。",
            )

        # 関連する学習チャット履歴を削除
        session.execute(
            sa_text("DELETE FROM learning_chat_history WHERE course_id = :cid"),
            {"cid": course_id},
        )
        # コース削除（CASCADE で course_group_permissions も削除される）
        session.execute(
            sa_text("DELETE FROM learning_courses WHERE id = :course_id"),
            {"course_id": course_id},
        )
        session.commit()
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    logger.info("Course %s (%s) deleted by user=%s", course_id, course_title, current_user["id"])
    return {"course_id": course_id, "deleted": True}


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
# System Statistics (Issue #144)
# ---------------------------------------------------------------------------


@router.get("/system/materials-stats")
def get_materials_stats(
    current_user: dict = Depends(_require_system_admin),
) -> list[dict]:
    """全コースのパイプライン進捗・利用統計を返す（SYSTEM_ADMIN 専用）。"""
    session = _pg_session()
    try:
        rows = session.execute(
            sa_text("""
                WITH CourseSources AS (
                    SELECT
                        lc.id::text AS course_id,
                        (src->>'material_id') AS material_id
                    FROM learning_courses lc,
                    LATERAL jsonb_array_elements(
                        CASE WHEN jsonb_typeof(lc.data->'sources') = 'array'
                             THEN lc.data->'sources'
                             ELSE '[]'::jsonb
                        END
                    ) AS src
                    WHERE src->>'material_id' IS NOT NULL
                ),
                ChunkStats AS (
                    SELECT
                        cs.course_id,
                        COUNT(DISTINCT c.id) AS total_chunks,
                        COUNT(DISTINCT CASE
                            WHEN c.smiles_dsl IS NOT NULL AND c.smiles_dsl != '' THEN c.id
                        END) AS structure_chunks,
                        COUNT(DISTINCT CASE
                            WHEN c.spoken_text IS NOT NULL AND c.spoken_text != '' THEN c.id
                        END) AS script_chunks,
                        COUNT(DISTINCT a.chunk_id) AS audio_chunks
                    FROM CourseSources cs
                    JOIN chunks c ON c.material_id = cs.material_id
                    LEFT JOIN lecture_audio_cache a ON a.chunk_id = c.id
                    WHERE c.text IS NOT NULL AND c.text != ''
                    GROUP BY cs.course_id
                ),
                EnrolledStats AS (
                    SELECT
                        ls.course_id,
                        COUNT(DISTINCT ls.user_id) AS enrolled_students
                    FROM learning_states ls
                    GROUP BY ls.course_id
                ),
                ChatStats AS (
                    SELECT
                        lch.course_id,
                        COALESCE(SUM(jsonb_array_length(lch.history)), 0) AS chat_count
                    FROM learning_chat_history lch
                    GROUP BY lch.course_id
                ),
                ActiveTasks AS (
                    SELECT DISTINCT ON (result_data->>'course_id')
                        result_data->>'course_id' AS course_id,
                        task_type
                    FROM background_tasks
                    WHERE status IN ('pending', 'processing')
                      AND result_data IS NOT NULL
                      AND result_data->>'course_id' IS NOT NULL
                    ORDER BY result_data->>'course_id', created_at DESC
                )
                SELECT
                    lc.id::text AS course_id,
                    lc.title,
                    u.display_name AS uploaded_by,
                    lc.created_at,
                    COALESCE(cs.total_chunks, 0) AS chunk_count,
                    CASE
                        WHEN COALESCE(cs.total_chunks, 0) = 0 THEN 0.0
                        ELSE ROUND(COALESCE(cs.structure_chunks, 0)::numeric / cs.total_chunks * 100, 1)
                    END AS structure_progress,
                    CASE
                        WHEN COALESCE(cs.total_chunks, 0) = 0 THEN 0.0
                        ELSE ROUND(cs.script_chunks::numeric / cs.total_chunks * 100, 1)
                    END AS script_progress,
                    CASE
                        WHEN COALESCE(cs.total_chunks, 0) = 0 THEN 0.0
                        ELSE ROUND(COALESCE(cs.audio_chunks, 0)::numeric / cs.total_chunks * 100, 1)
                    END AS audio_progress,
                    COALESCE(es.enrolled_students, 0) AS enrolled_students,
                    COALESCE(chats.chat_count, 0) AS chat_count,
                    active.task_type AS active_task_type
                FROM learning_courses lc
                LEFT JOIN users u ON u.id = lc.user_id
                LEFT JOIN ChunkStats cs ON cs.course_id = lc.id::text
                LEFT JOIN EnrolledStats es ON es.course_id = lc.id
                LEFT JOIN ChatStats chats ON chats.course_id = lc.id
                LEFT JOIN ActiveTasks active ON active.course_id = lc.id::text
                ORDER BY lc.created_at DESC
            """),
        ).fetchall()
    finally:
        session.close()

    return [
        {
            "course_id": r[0],
            "title": r[1] or "",
            "uploaded_by": r[2] or "",
            "created_at": r[3].isoformat() if r[3] else "",
            "chunk_count": int(r[4]) if r[4] else 0,
            "structure_progress": float(r[5]) if r[5] is not None else 0.0,
            "script_progress": float(r[6]) if r[6] is not None else 0.0,
            "audio_progress": float(r[7]) if r[7] is not None else 0.0,
            "enrolled_students": int(r[8]) if r[8] else 0,
            "chat_count": int(r[9]) if r[9] else 0,
            "active_task_type": r[10] or "",
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
