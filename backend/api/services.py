"""Episteme Graph — ビジネスロジック関数。

ルーターから呼び出されるヘルパー関数群。循環インポートを防ぐため main.py から分離。
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import threading
import uuid
from functools import lru_cache

from neo4j import GraphDatabase
from sqlalchemy import text as sa_text

from core.config import get_settings as _get_settings
from core.lecture import normalize_to_placeholder_format as _normalize_formulas
from core.llm import generate_text, generate_text_with_structured_output, generate_embeddings, get_embedding_dim
from core.postgres import get_session as _pg_session
from core.schema import PaperStructure
from core.storage import get_storage_client as _get_storage

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Singletons
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _neo4j_driver():
    settings = _get_settings()
    user, password = settings.neo4j_auth.split("/", 1)
    return GraphDatabase.driver(settings.neo4j_uri, auth=(user, password))


# ---------------------------------------------------------------------------
# Background material processing state
# ---------------------------------------------------------------------------

_material_lock = threading.Lock()
_material_status: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Background task DB helpers (Issue #63)
# ---------------------------------------------------------------------------


def create_background_task(
    task_id: str,
    task_type: str = "material_processing",
    created_by: str | None = None,
) -> None:
    """background_tasks テーブルにタスクレコードを作成する。"""
    session = _pg_session()
    try:
        session.execute(
            sa_text("""
                INSERT INTO background_tasks (id, task_type, status, created_by)
                VALUES (:id, :task_type, 'pending', CAST(:created_by AS uuid))
            """),
            {"id": task_id, "task_type": task_type, "created_by": created_by},
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def update_background_task(
    task_id: str,
    status: str,
    result_data: dict | None = None,
    error_message: str | None = None,
) -> None:
    """background_tasks テーブルのステータスを更新する。"""
    session = _pg_session()
    try:
        params: dict = {"id": task_id, "status": status}
        set_clauses = ["status = :status", "updated_at = now()"]

        if result_data is not None:
            set_clauses.append("result_data = CAST(:result_data AS jsonb)")
            params["result_data"] = json.dumps(result_data, ensure_ascii=False)
        if error_message is not None:
            set_clauses.append("error_message = :error_message")
            params["error_message"] = error_message

        session.execute(
            sa_text(f"UPDATE background_tasks SET {', '.join(set_clauses)} WHERE id = :id"),
            params,
        )
        session.commit()
    except Exception:
        session.rollback()
        logger.warning("Failed to update background task %s: %s", task_id, status, exc_info=True)
    finally:
        session.close()


def get_background_task(task_id: str) -> dict | None:
    """background_tasks テーブルからタスク情報を取得する。"""
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("""
                SELECT id, task_type, status, result_data, error_message, created_at, updated_at
                FROM background_tasks WHERE id = :id
            """),
            {"id": task_id},
        ).fetchone()
        if not row:
            return None
        task_type = row[1]
        result_data = row[3] or {}
        # Revision-pipeline tasks carry their own stage from the worker and must
        # NOT be enriched from the latest initial/non-revision run, which would
        # overwrite the revision stage with the initial run's current_stage
        # (#414-2). Their progress is read via the revision status API instead.
        is_revision_task = (
            task_type == "revision_pipeline"
            or (isinstance(result_data, dict) and result_data.get("revision_run_id"))
        )
        if isinstance(result_data, dict) and result_data.get("document_id") and not is_revision_task:
            run = session.execute(
                sa_text(
                    """
                    SELECT status, current_stage, error_message, stage_outputs
                    FROM document_analysis_runs
                    WHERE document_id = :document_id
                      AND (run_type IS NULL OR run_type <> 'revision')
                    ORDER BY created_at DESC
                    LIMIT 1
                    """
                ),
                {"document_id": result_data["document_id"]},
            ).fetchone()
            if run:
                stage = run[1] or result_data.get("stage")
                stage_outputs = run[3] or {}
                stage_info = stage_outputs.get(stage) if isinstance(stage_outputs, dict) else {}
                if not isinstance(stage_info, dict):
                    stage_info = {}
                enriched = dict(result_data)
                enriched.update({
                    "stage": stage,
                    "analysis_status": run[0],
                })
                enriched.update(stage_info)
                result_data = enriched
        return {
            "task_id": row[0],
            "task_type": row[1],
            "status": row[2],
            "result_data": result_data,
            "error_message": row[4] or (run[2] if 'run' in locals() and run else None),
            "created_at": row[5].isoformat() if row[5] else "",
            "updated_at": row[6].isoformat() if row[6] else "",
        }
    finally:
        session.close()


def get_latest_revision_task(revision_run_id: str) -> dict | None:
    """Latest revision_pipeline task for a revision run (#414-4 reload restore).

    Returns id/status/stage/progress so the UI can restore polling after a reload
    without ever pulling in another revision's task. No initial-run enrichment is
    applied (revision tasks carry their own stage).
    """
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("""
                SELECT id, task_type, status, result_data, error_message, created_at, updated_at
                FROM background_tasks
                WHERE task_type = 'revision_pipeline'
                  AND result_data IS NOT NULL
                  AND result_data->>'revision_run_id' = :rev
                ORDER BY created_at DESC
                LIMIT 1
            """),
            {"rev": revision_run_id},
        ).fetchone()
        if not row:
            return None
        result_data = row[3] or {}
        return {
            "task_id": row[0],
            "task_type": row[1],
            "status": row[2],
            "stage": (result_data or {}).get("stage") if isinstance(result_data, dict) else None,
            "result_data": result_data,
            "error_message": row[4],
            "created_at": row[5].isoformat() if row[5] else "",
            "updated_at": row[6].isoformat() if row[6] else "",
        }
    finally:
        session.close()


def get_active_task_for_course(course_id: str) -> dict | None:
    """指定コースに紐づく進行中 (pending/processing) タスクを最新1件返す (Issue #139)。"""
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("""
                SELECT id, task_type, status, result_data, error_message, created_at, updated_at
                FROM background_tasks
                WHERE status IN ('pending', 'processing')
                  AND result_data IS NOT NULL
                  AND result_data->>'course_id' = :course_id
                ORDER BY created_at DESC
                LIMIT 1
            """),
            {"course_id": course_id},
        ).fetchone()
        if not row:
            return None
        return {
            "task_id": row[0],
            "task_type": row[1],
            "status": row[2],
            "result_data": row[3],
            "error_message": row[4],
            "created_at": row[5].isoformat() if row[5] else "",
            "updated_at": row[6].isoformat() if row[6] else "",
        }
    finally:
        session.close()

# ---------------------------------------------------------------------------
# Course CRUD helpers
# ---------------------------------------------------------------------------


def get_course_data(user_id: str, course_id: str) -> dict | None:
    """LearningCourse のマスターデータを返す（オーナー or 受講者）。

    Issue #145: オーナー特例を削除。教員であっても自身の learning_states を通じて
    アクセスし、一般学生と同じ学習体験を得る。
    - オーナーが learning_states を持っていない場合は自動作成する。
    - マスターデータのみを返す（個人レイヤーは get_personal_layer() で別途取得）。
    """
    session = _pg_session()
    try:
        record = session.execute(
            sa_text("""
                SELECT data, user_id FROM learning_courses
                WHERE id = :course_id
                LIMIT 1
            """),
            {"course_id": course_id},
        ).fetchone()
        if not record or not record[0]:
            return None

        data = record[0] if isinstance(record[0], dict) else json.loads(record[0])
        owner_id = record[1]

        if str(owner_id) == str(user_id):
            # Issue #145: オーナーも learning_states を保持する（自動作成）
            enroll_user_in_course(user_id, course_id)
            return data

        # 受講者チェック
        state_row = session.execute(
            sa_text("""
                SELECT 1 FROM learning_states
                WHERE user_id = CAST(:user_id AS uuid) AND course_id = :course_id
                LIMIT 1
            """),
            {"user_id": user_id, "course_id": course_id},
        ).fetchone()
        if not state_row:
            return None

        return data
    finally:
        session.close()


def get_personal_layer(user_id: str, course_id: str) -> dict:
    """ユーザー固有の学習レイヤーデータを返す。

    Issue #145: マスターコースとは分離して管理される個人データ。
    - misconceptions_by_topic: トピックIDをキーとした誤解リスト
    - chat_anchors: チャット履歴から生成された注釈データ（将来拡張用）
    """
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("""
                SELECT personal_graph FROM learning_states
                WHERE user_id = CAST(:user_id AS uuid) AND course_id = :course_id
                LIMIT 1
            """),
            {"user_id": user_id, "course_id": course_id},
        ).fetchone()
        if not row or not row[0]:
            return {"misconceptions_by_topic": {}, "chat_anchors": {}}
        personal = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        return {
            "misconceptions_by_topic": personal.get("misconceptions_by_topic", {}) or {},
            "chat_anchors": personal.get("chat_anchors", {}) or {},
        }
    finally:
        session.close()


def user_is_enrolled(user_id: str, course_id: str) -> bool:
    """ユーザーが learning_states を通じてコースに受講登録済みかを判定する。"""
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("""
                SELECT 1 FROM learning_states
                WHERE user_id = CAST(:user_id AS uuid) AND course_id = :course_id
                LIMIT 1
            """),
            {"user_id": user_id, "course_id": course_id},
        ).fetchone()
        return row is not None
    finally:
        session.close()


def enroll_user_in_course(user_id: str, course_id: str) -> None:
    """learning_states に受講レコードを作成する（UNIQUE で二重受講を防止）。"""
    session = _pg_session()
    try:
        session.execute(
            sa_text("""
                INSERT INTO learning_states (id, user_id, course_id)
                VALUES (gen_random_uuid(), CAST(:user_id AS uuid), :course_id)
                ON CONFLICT (user_id, course_id) DO NOTHING
            """),
            {"user_id": user_id, "course_id": course_id},
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def record_personal_misconception(
    user_id: str,
    course_id: str,
    topic_id: str,
    misconception: dict,
) -> None:
    """learning_states.personal_graph.misconceptions_by_topic に誤解を記録する。

    受講者が学習チャットで検出した誤解は、マスターコースではなくこの per-user 状態に保存する。
    per-topic で最新 5 件まで保持する（マスターの misconceptions と同じ上限）。
    未受講の場合はレコードを自動生成してから書き込む（オーナーが自コースで学習する場合など）。
    """
    session = _pg_session()
    try:
        session.execute(
            sa_text("""
                INSERT INTO learning_states (id, user_id, course_id)
                VALUES (gen_random_uuid(), CAST(:user_id AS uuid), :course_id)
                ON CONFLICT (user_id, course_id) DO NOTHING
            """),
            {"user_id": user_id, "course_id": course_id},
        )
        row = session.execute(
            sa_text("""
                SELECT personal_graph FROM learning_states
                WHERE user_id = CAST(:user_id AS uuid) AND course_id = :course_id
                LIMIT 1
            """),
            {"user_id": user_id, "course_id": course_id},
        ).fetchone()

        personal_raw = row[0] if row and row[0] is not None else {}
        personal = personal_raw if isinstance(personal_raw, dict) else json.loads(personal_raw)
        by_topic = personal.get("misconceptions_by_topic", {}) or {}
        current = by_topic.get(topic_id, []) or []
        current = [misconception] + current
        by_topic[topic_id] = current[:5]
        personal["misconceptions_by_topic"] = by_topic

        session.execute(
            sa_text("""
                UPDATE learning_states
                SET personal_graph = CAST(:personal AS jsonb),
                    updated_at = now()
                WHERE user_id = CAST(:user_id AS uuid) AND course_id = :course_id
            """),
            {
                "user_id": user_id,
                "course_id": course_id,
                "personal": json.dumps(personal, ensure_ascii=False),
            },
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _fetch_course_data_row(course_id: str) -> dict | None:
    """course_id だけから data 本体を取得する内部ヘルパ。"""
    session = _pg_session()
    try:
        record = session.execute(
            sa_text("SELECT data FROM learning_courses WHERE id = :course_id LIMIT 1"),
            {"course_id": course_id},
        ).fetchone()
    finally:
        session.close()
    if not record or not record[0]:
        return None
    return record[0] if isinstance(record[0], dict) else json.loads(record[0])


def get_editable_course_data(user_id: str, course_id: str) -> dict | None:
    """編集権限（オーナー or editor グループ）でコースデータを取得する。"""
    # user_can_edit_course は下で定義されているので関数内で参照される
    if not user_can_edit_course(user_id, course_id):
        return None
    return _fetch_course_data_row(course_id)


def get_viewable_course_data(user_id: str, course_id: str) -> dict | None:
    """閲覧権限（オーナー / editor / viewer グループ）でコースデータを取得する。"""
    if not user_can_view_course(user_id, course_id):
        return None
    return _fetch_course_data_row(course_id)


def get_accessible_course_data(user_id: str, course_id: str) -> dict | None:
    """visibility を考慮して、アクセス可能な LearningCourse データを返す。

    - オーナー本人: 常に可
    - visibility='public' かつ is_published かつ is_template: 可
    - visibility='group' かつ自分がそのグループのメンバー: 可
    """
    session = _pg_session()
    try:
        record = session.execute(
            sa_text("""
                SELECT data, user_id, COALESCE(visibility, 'private'),
                       group_id, COALESCE(is_published, false), COALESCE(is_template, false)
                FROM learning_courses
                WHERE id = :course_id
                LIMIT 1
            """),
            {"course_id": course_id},
        ).fetchone()
        if not record or not record[0]:
            return None

        data_raw, owner_id, visibility, group_id, is_published, is_template = record
        data = data_raw if isinstance(data_raw, dict) else json.loads(data_raw)

        if str(owner_id) == str(user_id):
            return data
        if visibility == "public" and is_published and is_template:
            return data
        if visibility == "group" and group_id:
            row = session.execute(
                sa_text("""
                    SELECT 1 FROM group_members
                    WHERE group_id = :gid AND user_id = CAST(:uid AS uuid)
                    LIMIT 1
                """),
                {"gid": group_id, "uid": user_id},
            ).fetchone()
            if row:
                return data
        return None
    finally:
        session.close()


def save_course_data(
    user_id: str,
    course_id: str,
    data: dict,
    is_template: bool = False,
    *,
    visibility: str = "private",
    group_id: str | None = None,
    description: str = "",
) -> None:
    """LearningCourse データを PostgreSQL に UPSERT する。"""
    session = _pg_session()
    try:
        session.execute(
            sa_text("""
                INSERT INTO learning_courses
                    (id, user_id, title, data, is_template, is_published, owner_id,
                     visibility, group_id, description)
                VALUES (
                    :course_id,
                    CAST(:user_id AS uuid),
                    :title,
                    CAST(:data AS jsonb),
                    :is_template,
                    false,
                    CAST(:user_id AS uuid),
                    :visibility,
                    CAST(:group_id AS uuid),
                    :description
                )
                ON CONFLICT (id) DO UPDATE
                SET data = CAST(EXCLUDED.data AS jsonb),
                    title = EXCLUDED.title,
                    visibility = EXCLUDED.visibility,
                    group_id = EXCLUDED.group_id,
                    description = EXCLUDED.description,
                    updated_at = now()
            """),
            {
                "course_id": course_id,
                "user_id": user_id,
                "title": data.get("title", course_id),
                "data": json.dumps(data, ensure_ascii=False),
                "is_template": is_template,
                "visibility": visibility,
                "group_id": group_id,
                "description": description,
            },
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_user_group_ids(user_id: str) -> list[str]:
    """ユーザーが参加しているグループIDのリストを返す。"""
    session = _pg_session()
    try:
        rows = session.execute(
            sa_text("""
                SELECT group_id FROM group_members
                WHERE user_id = CAST(:uid AS uuid)
            """),
            {"uid": user_id},
        ).fetchall()
        return [str(r[0]) for r in rows]
    finally:
        session.close()


def get_course_group_permissions(course_id: str) -> list[dict]:
    """コースに紐づくグループ権限マッピング一覧を返す（グループ名付き）。"""
    session = _pg_session()
    try:
        rows = session.execute(
            sa_text("""
                SELECT cgp.course_id,
                       cgp.group_id,
                       COALESCE(g.name, '') AS group_name,
                       cgp.permission,
                       cgp.created_at,
                       cgp.updated_at
                FROM course_group_permissions cgp
                LEFT JOIN groups g ON g.id = cgp.group_id
                WHERE cgp.course_id = :course_id
                ORDER BY cgp.permission, g.name
            """),
            {"course_id": course_id},
        ).fetchall()
    finally:
        session.close()
    return [
        {
            "course_id": r[0],
            "group_id": str(r[1]),
            "group_name": r[2] or "",
            "permission": r[3],
            "created_at": r[4].isoformat() if r[4] else "",
            "updated_at": r[5].isoformat() if r[5] else "",
        }
        for r in rows
    ]


def user_owns_course(user_id: str, course_id: str) -> bool:
    """ユーザーがコースの所有者（オーナー）かを判定する。

    共有設定の変更など、オーナー限定の操作の認可に使う。
    """
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("""
                SELECT 1 FROM learning_courses
                WHERE id = :course_id AND user_id = CAST(:uid AS uuid)
                LIMIT 1
            """),
            {"course_id": course_id, "uid": user_id},
        ).fetchone()
        return row is not None
    finally:
        session.close()


def user_can_edit_course(user_id: str, course_id: str) -> bool:
    """ユーザーがコースを編集できるかを判定する。

    - 所有者（learning_courses.user_id）
    - editor 権限のあるグループメンバー
    のいずれかに該当する場合に True。
    """
    if user_owns_course(user_id, course_id):
        return True
    session = _pg_session()
    try:
        editor_row = session.execute(
            sa_text("""
                SELECT 1
                FROM course_group_permissions cgp
                JOIN group_members gm ON gm.group_id = cgp.group_id
                WHERE cgp.course_id = :course_id
                  AND cgp.permission = 'editor'
                  AND gm.user_id = CAST(:uid AS uuid)
                LIMIT 1
            """),
            {"course_id": course_id, "uid": user_id},
        ).fetchone()
        return editor_row is not None
    finally:
        session.close()


def user_can_view_course(user_id: str, course_id: str) -> bool:
    """ユーザーがコースを受講（閲覧）できるかを判定する。

    - 所有者
    - editor 権限のあるグループメンバー
    - viewer 権限のあるグループメンバー
    のいずれかに該当する場合に True。
    """
    if user_can_edit_course(user_id, course_id):
        return True
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("""
                SELECT 1
                FROM course_group_permissions cgp
                JOIN group_members gm ON gm.group_id = cgp.group_id
                WHERE cgp.course_id = :course_id
                  AND cgp.permission = 'viewer'
                  AND gm.user_id = CAST(:uid AS uuid)
                LIMIT 1
            """),
            {"course_id": course_id, "uid": user_id},
        ).fetchone()
        return row is not None
    finally:
        session.close()


def user_can_access_group(user_id: str, group_id: str | None) -> bool:
    """ユーザーが指定のグループに属しているかを判定する。

    group_id が None/空の場合は False を返す。
    """
    if not group_id:
        return False
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("""
                SELECT 1 FROM group_members
                WHERE user_id = CAST(:uid AS uuid) AND group_id = CAST(:gid AS uuid)
                LIMIT 1
            """),
            {"uid": user_id, "gid": group_id},
        ).fetchone()
        return row is not None
    finally:
        session.close()


def delete_course_data(user_id: str, course_id: str) -> bool:
    """LearningCourse レコードを削除する。"""
    session = _pg_session()
    try:
        result = session.execute(
            sa_text("""
                DELETE FROM learning_courses
                WHERE id = :course_id AND user_id = CAST(:user_id AS uuid)
                RETURNING id
            """),
            {"course_id": course_id, "user_id": user_id},
        ).fetchone()
        session.commit()
        return result is not None
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Embedding / RAG helpers
# ---------------------------------------------------------------------------


def embed_text(text: str) -> list[float]:
    """テキストを embedding ベクトルに変換する。"""
    vectors = generate_embeddings([text])
    return vectors[0]


def search_relevant_chunks(
    query: str,
    material_ids: list[str],
    top_k: int = 5,
) -> list[str]:
    """PostgreSQL pgvector から関連チャンクをベクトル検索する。"""
    if not material_ids:
        return []

    try:
        query_vector = embed_text(query)
    except Exception as exc:
        logger.warning("Embedding failed: %s", exc)
        return []

    try:
        session = _pg_session()
        try:
            placeholders = ", ".join(f":mid_{i}" for i in range(len(material_ids)))
            params: dict = {"query_vector": str(query_vector), "limit": top_k}
            for i, mid in enumerate(material_ids):
                params[f"mid_{i}"] = mid

            dim = get_embedding_dim()
            rows = session.execute(
                sa_text(f"""
                    SELECT c.text
                    FROM chunks c
                    WHERE c.material_id IN ({placeholders})
                      AND c.embedding IS NOT NULL
                    ORDER BY c.embedding::halfvec({dim}) <=> CAST(:query_vector AS halfvec({dim}))
                    LIMIT :limit
                """),
                params,
            ).fetchall()
            return [row[0] for row in rows if row[0]]
        finally:
            session.close()
    except Exception as exc:
        logger.warning("pgvector search failed: %s", exc)
        return []


def search_relevant_chunks_with_scores(
    query: str,
    material_ids: list[str],
    top_k: int = 5,
) -> tuple[list[str], list[float]]:
    """PostgreSQL pgvector から関連チャンクをベクトル検索し、テキストとスコアの両方を返す。"""
    if not material_ids:
        return [], []

    try:
        query_vector = embed_text(query)
    except Exception as exc:
        logger.warning("Embedding failed: %s", exc)
        return [], []

    try:
        session = _pg_session()
        try:
            mid_placeholders = ", ".join(f":mid_{i}" for i in range(len(material_ids)))
            params: dict = {"query_vector": str(query_vector), "limit": top_k}
            for i, mid in enumerate(material_ids):
                params[f"mid_{i}"] = mid

            where_clause = f"c.material_id IN ({mid_placeholders})"
            dim = get_embedding_dim()

            rows = session.execute(
                sa_text(f"""
                    SELECT c.text,
                           1 - (c.embedding::halfvec({dim}) <=> CAST(:query_vector AS halfvec({dim}))) AS score
                    FROM chunks c
                    WHERE ({where_clause})
                      AND c.embedding IS NOT NULL
                    ORDER BY c.embedding::halfvec({dim}) <=> CAST(:query_vector AS halfvec({dim}))
                    LIMIT :limit
                """),
                params,
            ).fetchall()

            texts = [row[0] for row in rows if row[0]]
            scores = [float(row[1]) for row in rows]
            return texts, scores
        finally:
            session.close()
    except Exception as exc:
        logger.warning("pgvector search failed: %s", exc)
        return [], []


def search_chunks_with_metadata(
    query: str,
    top_k: int = 8,
) -> list[dict]:
    """システム全域の chunks をベクトル検索し、出典情報を付けて返す。"""
    try:
        query_vector = embed_text(query)
    except Exception as exc:
        logger.warning("Embedding failed: %s", exc)
        return []

    try:
        session = _pg_session()
        try:
            dim = get_embedding_dim()
            rows = session.execute(
                sa_text(f"""
                    SELECT c.id,
                           c.text,
                           COALESCE(d.title, '') AS source_title,
                           COALESCE(d.filename, '') AS source_file,
                           1 - (c.embedding::halfvec({dim}) <=> CAST(:query_vector AS halfvec({dim}))) AS score,
                           c.material_id
                    FROM chunks c
                    LEFT JOIN documents d ON c.document_id = d.id
                    WHERE c.embedding IS NOT NULL
                    ORDER BY c.embedding::halfvec({dim}) <=> CAST(:query_vector AS halfvec({dim}))
                    LIMIT :limit
                """),
                {"query_vector": str(query_vector), "limit": top_k},
            ).fetchall()
            from core.learning_experience import attach_tiers, approved_chunk_ids

            results = [
                {
                    "id": str(row[0]),
                    "text": row[1],
                    "source_title": row[2] or row[3] or "不明な教材",
                    "source_file": row[3],
                    "score": float(row[4]),
                    "material_id": str(row[5]) if row[5] else "",
                }
                for row in rows
                if row[1]
            ]
            # L1 信頼性: 教員承認(teacher_reviewed)に紐づく chunk へ approved を付与してから tier 判定。
            approved = approved_chunk_ids(session, [r["id"] for r in results])
            for r in results:
                r["approved"] = r["id"] in approved
            return attach_tiers(results)
        finally:
            session.close()
    except Exception as exc:
        logger.warning("System-wide pgvector search failed: %s", exc)
        return []


def _json_obj(value: object) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _find_text_offsets(text: str, needle: str) -> list[dict]:
    if not text or not needle:
        return []
    haystack = text.lower()
    target = needle.lower()
    offsets: list[dict] = []
    start = 0
    while True:
        idx = haystack.find(target, start)
        if idx < 0:
            break
        offsets.append({"start": idx, "end": idx + len(needle)})
        start = idx + len(needle)
        if len(offsets) >= 5:
            break
    return offsets


def _derive_graph_mentions(
    text: str,
    knowledge_graph: object,
    formulas: list[dict],
    source_metadata: object | None = None,
) -> list[dict]:
    """チャンク本文に現れる knowledge_graph 要素からサジェスト候補を作る。"""
    graph = _json_obj(knowledge_graph)
    concepts = graph.get("concepts", []) if isinstance(graph.get("concepts", []), list) else []
    relationships = (
        graph.get("relationships", [])
        if isinstance(graph.get("relationships", []), list)
        else []
    )

    concept_by_id: dict[str, dict] = {}
    mentions: list[dict] = []

    for c in concepts:
        if not isinstance(c, dict):
            continue
        cid = str(c.get("id") or c.get("name") or "").strip()
        name = str(c.get("name") or cid).strip()
        if not cid or not name:
            continue
        concept_by_id[cid] = c
        offsets = _find_text_offsets(text, name)
        if not offsets:
            continue
        importance = 0.9 if c.get("type") in ("definition", "theorem", "method") else 0.75
        mentions.append({
            "element_id": cid,
            "element_type": "concept",
            "label": name,
            "surface_text": name,
            "importance_score": importance,
            "offsets": offsets,
        })

    mentioned_ids = {m["element_id"] for m in mentions if m["element_type"] == "concept"}
    for rel in relationships:
        if not isinstance(rel, dict):
            continue
        source = str(rel.get("source") or "").strip()
        target = str(rel.get("target") or "").strip()
        relation = str(rel.get("relation") or "RELATED_TO").strip()
        if not source or not target:
            continue
        source_name = str(concept_by_id.get(source, {}).get("name") or source)
        target_name = str(concept_by_id.get(target, {}).get("name") or target)
        source_seen = source in mentioned_ids or bool(_find_text_offsets(text, source_name))
        target_seen = target in mentioned_ids or bool(_find_text_offsets(text, target_name))
        if not (source_seen and target_seen):
            continue
        mentions.append({
            "element_id": f"{source}:{relation}:{target}",
            "element_type": "relationship",
            "label": f"{source_name} と {target_name} の関係",
            "surface_text": f"{source_name} / {target_name}",
            "importance_score": 0.82,
            "offsets": [],
        })

    for f in formulas[:3]:
        formula_id = str(f.get("id") or "").strip() if isinstance(f, dict) else ""
        latex = str(f.get("latex") or "").strip() if isinstance(f, dict) else ""
        if not formula_id and not latex:
            continue
        mentions.append({
            "element_id": formula_id or f"formula:{len(mentions)}",
            "element_type": "formula",
            "label": "この数式",
            "surface_text": formula_id or latex[:60],
            "importance_score": 0.7,
            "offsets": _find_text_offsets(text, formula_id) if formula_id else [],
        })

    meta = _json_obj(source_metadata)
    for ref in meta.get("tex_refs", []) if isinstance(meta.get("tex_refs"), list) else []:
        if not isinstance(ref, dict):
            continue
        key = str(ref.get("key") or "").strip()
        if not key:
            continue
        mentions.append({
            "element_id": f"ref:{key}",
            "element_type": "reference",
            "label": f"参照 {key}",
            "surface_text": key,
            "importance_score": 0.68,
            "offsets": _find_text_offsets(text, key),
        })

    for citation in meta.get("tex_citations", []) if isinstance(meta.get("tex_citations"), list) else []:
        if not isinstance(citation, dict):
            continue
        key = str(citation.get("key") or "").strip()
        if not key:
            continue
        bib = citation.get("bib") if isinstance(citation.get("bib"), dict) else {}
        label = _citation_label(key, bib)
        mentions.append({
            "element_id": f"bib:{key}",
            "element_type": "citation",
            "label": label,
            "surface_text": key,
            "importance_score": 0.66,
            "offsets": _find_text_offsets(text, key),
        })

    mentions.sort(key=lambda m: m.get("importance_score", 0), reverse=True)
    deduped: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for mention in mentions:
        key = (mention["element_type"], mention["element_id"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(mention)
        if len(deduped) >= 6:
            break
    return deduped


def _get_or_create_chunk_graph_mentions(
    session,
    chunk_id: str,
    material_id: str,
    text: str,
    knowledge_graph: object,
    formulas: list[dict],
    source_metadata: object | None = None,
) -> list[dict]:
    rows = session.execute(
        sa_text("""
            SELECT element_id, element_type, surface_text, importance_score, offsets
            FROM chunk_graph_mentions
            WHERE chunk_id = CAST(:chunk_id AS uuid)
            ORDER BY importance_score DESC, created_at ASC
            LIMIT 6
        """),
        {"chunk_id": chunk_id},
    ).fetchall()
    if rows:
        return [
            {
                "element_id": row[0],
                "element_type": row[1],
                "label": row[2] or row[0],
                "surface_text": row[2] or "",
                "importance_score": float(row[3]),
                "offsets": row[4] if isinstance(row[4], list) else [],
            }
            for row in rows
        ]

    mentions = _derive_graph_mentions(text, knowledge_graph, formulas, source_metadata)
    for mention in mentions:
        session.execute(
            sa_text("""
                INSERT INTO chunk_graph_mentions (
                    chunk_id, material_id, element_id, element_type,
                    surface_text, importance_score, offsets
                )
                VALUES (
                    CAST(:chunk_id AS uuid), :material_id, :element_id, :element_type,
                    :surface_text, :importance_score, CAST(:offsets AS jsonb)
                )
                ON CONFLICT (chunk_id, element_id, element_type) DO NOTHING
            """),
            {
                "chunk_id": chunk_id,
                "material_id": material_id,
                "element_id": mention["element_id"],
                "element_type": mention["element_type"],
                "surface_text": mention.get("label") or mention.get("surface_text") or "",
                "importance_score": mention.get("importance_score", 0.5),
                "offsets": json.dumps(mention.get("offsets", []), ensure_ascii=False),
            },
        )
    if mentions:
        session.commit()
    return mentions


def _citation_label(key: str, bib: dict) -> str:
    title = str(bib.get("title") or "").strip()
    author = str(bib.get("author") or "").strip()
    year = str(bib.get("year") or "").strip()
    if author and year:
        first_author = author.split(" and ")[0].strip()
        return f"{first_author} ({year})"
    if title:
        return title[:80]
    return f"引用 {key}"


def _format_citation_description(key: str, bib: dict) -> str:
    if not bib:
        return f"TeX ソース内の引用です。BibTeX キー: {key}"
    parts = []
    title = str(bib.get("title") or "").strip()
    author = str(bib.get("author") or "").strip()
    year = str(bib.get("year") or "").strip()
    journal = str(bib.get("journal") or bib.get("booktitle") or "").strip()
    doi = str(bib.get("doi") or "").strip()
    arxiv = str(bib.get("eprint") or bib.get("archiveprefix") or "").strip()
    if title:
        parts.append(f"題名: {title}")
    if author:
        parts.append(f"著者: {author}")
    if year:
        parts.append(f"年: {year}")
    if journal:
        parts.append(f"掲載先: {journal}")
    if doi:
        parts.append(f"DOI: {doi}")
    if arxiv:
        parts.append(f"arXiv/eprint: {arxiv}")
    parts.append(f"BibTeX キー: {key}")
    return "\n".join(parts)


def get_course_chunks_ordered(course_data: dict) -> list[dict]:
    """コースのソース教材からチャンクをchunk_index順に全件取得する。

    lecture_studio._get_course_chunks と同じロジックで、学習ナビゲーション専用。
    N番目のトピックにはこのリストのN番目のチャンクをそのまま表示する。
    テキストは display_text（音声原稿用スクリプト）を優先し、旧フォーマットは
    normalize_to_placeholder_format で [[FORMULA_N]] 形式に正規化する。
    """
    sources = course_data.get("sources", [])
    material_ids = [s.get("material_id") for s in sources if s.get("material_id")]

    if not material_ids:
        return []

    try:
        session = _pg_session()
        try:
            placeholders = ", ".join(f":mid_{i}" for i in range(len(material_ids)))
            params: dict = {f"mid_{i}": mid for i, mid in enumerate(material_ids)}
            rows = session.execute(
                sa_text(f"""
                    SELECT c.id, c.chunk_index, c.text, c.display_text, c.formulas,
                           c.chapter, c.section, c.material_id, d.knowledge_graph,
                           c.source_metadata
                    FROM chunks c
                    LEFT JOIN documents d ON c.document_id = d.id
                    WHERE c.material_id IN ({placeholders})
                      AND c.text IS NOT NULL AND c.text != ''
                    ORDER BY c.chunk_index ASC
                """),
                params,
            ).fetchall()
            result = []
            for row in rows:
                raw_text = row[3] or row[2] or ""  # display_text → text
                raw_formulas = row[4] if row[4] else []
                text, formulas = _normalize_formulas(raw_text, raw_formulas)
                chunk_id = str(row[0])
                material_id = row[7] or ""
                result.append({
                    "id": chunk_id,
                    "chunk_index": row[1],
                    "text": text,
                    "formulas": formulas,
                    "chapter": row[5],
                    "section": row[6],
                    "material_id": material_id,
                    "graph_mentions": _get_or_create_chunk_graph_mentions(
                        session, chunk_id, material_id, text, row[8], formulas, row[9],
                    ),
                })
            return result
        finally:
            session.close()
    except Exception as exc:
        logger.warning("get_course_chunks_ordered failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Progress calculation
# ---------------------------------------------------------------------------


def calculate_progress(user_id: str, course_id: str, course_data: dict) -> dict:
    """コースデータとチャット履歴から進捗を計算する。"""
    topics = course_data.get("topics", [])
    concepts = course_data.get("concepts", [])

    mastered = sum(1 for c in concepts if c.get("status") == "mastered")
    learning = sum(1 for c in concepts if c.get("status") == "learning")

    # Issue #145: 個人誤解は personal_graph から取得（マスターデータには含まれない）
    personal = get_personal_layer(user_id, course_id)
    by_topic = personal.get("misconceptions_by_topic", {}) or {}
    total_misconceptions = sum(len(v) for v in by_topic.values())

    sessions_list = []
    pg_session = _pg_session()
    try:
        records = pg_session.execute(
            sa_text("""
                SELECT topic_id, history, updated_at
                FROM learning_chat_history
                WHERE user_id = CAST(:user_id AS uuid) AND course_id = :course_id
                ORDER BY updated_at DESC
                LIMIT 10
            """),
            {"user_id": user_id, "course_id": course_id},
        ).fetchall()
    finally:
        pg_session.close()

    for r in records:
        topic_id_val = r[0]
        history = r[1] if isinstance(r[1], list) else []

        topic_name = topic_id_val
        for t in topics:
            if t.get("id") == topic_id_val:
                topic_name = t.get("title", topic_name)
                break

        msg_count = len(history)
        duration_min = max(5, msg_count * 2)

        date_str = ""
        if r[2]:
            try:
                dt = r[2]
                date_str = f"{dt.month}/{dt.day}"
            except Exception:
                pass

        sessions_list.append({
            "date": date_str or "---",
            "topic": topic_name,
            "duration": f"{duration_min}分",
        })

    streak = calculate_streak(user_id, course_id)

    return {
        "mastered_concepts": mastered,
        "learning_concepts": learning,
        "misconceptions": total_misconceptions,
        "streak_days": streak,
        "sessions": sessions_list[:5],
    }


def calculate_streak(user_id: str, course_id: str) -> int:
    """チャット履歴の日付から連続学習日数を算出する。"""
    pg_session = _pg_session()
    try:
        records = pg_session.execute(
            sa_text("""
                SELECT DISTINCT DATE(updated_at) AS d
                FROM learning_chat_history
                WHERE user_id = CAST(:user_id AS uuid) AND course_id = :course_id
                ORDER BY d DESC
            """),
            {"user_id": user_id, "course_id": course_id},
        ).fetchall()
    finally:
        pg_session.close()

    if not records:
        return 0

    sorted_dates = [r[0] for r in records]
    today = datetime.date.today()

    if sorted_dates[0] < today - datetime.timedelta(days=1):
        return 0

    streak = 1
    for i in range(1, len(sorted_dates)):
        if sorted_dates[i] == sorted_dates[i - 1] - datetime.timedelta(days=1):
            streak += 1
        else:
            break

    return streak


# ---------------------------------------------------------------------------
# Prerequisites check (Adaptive Routing)
# ---------------------------------------------------------------------------


def check_prerequisites(
    user_id: str,
    course_id: str,
    course_data: dict,
    topic_title: str,
    user_message: str,
) -> dict | None:
    """コースデータの prerequisites フィールドを基に前提知識を確認し、未習得なら介入情報を返す。

    Returns
    -------
    dict | None
        未習得の前提知識がある場合は ``{"message", "first_prerequisite", "unlearned"}``。
        介入不要なら ``None``。選択肢ボタン（はい/いいえ）は呼び出し側（ルート）が
        構造化 ``next_actions`` として組み立てるため、ここでは本文のみを返す。
    """
    skip_keywords = ["理解", "わかります", "わかっています", "知っています", "できます", "学習済み"]
    if any(kw in user_message for kw in skip_keywords):
        return None

    try:
        current_topic = None
        for t in course_data.get("topics", []):
            if t.get("title") == topic_title or t.get("id") == topic_title:
                current_topic = t
                break

        if not current_topic:
            return None

        prereqs = current_topic.get("prerequisites", [])
        if not prereqs:
            return None

        prereq_names: list[str] = []
        for prereq in prereqs:
            prereq_name = prereq.get("name", "") if isinstance(prereq, dict) else str(prereq)
            prereq_name = prereq_name.strip()
            if prereq_name:
                prereq_names.append(prereq_name)

        if not prereq_names:
            return None

        explanation_keywords = ["教えて", "説明", "詳しく", "知りたい", "わからない", "分からない"]
        if any(name in user_message for name in prereq_names) and any(
            kw in user_message for kw in explanation_keywords
        ):
            return None

        pg = _pg_session()
        try:
            rows = pg.execute(
                sa_text("""
                    SELECT topic_id FROM learning_chat_history
                    WHERE user_id = CAST(:user_id AS uuid) AND course_id = :course_id
                """),
                {"user_id": user_id, "course_id": course_id},
            ).fetchall()
        finally:
            pg.close()
        topics_with_history: set[str] = {r[0] for r in rows}

        title_to_id: dict[str, str] = {}
        for t in course_data.get("topics", []):
            title = t.get("title", "").lower().strip()
            if title:
                title_to_id[title] = t.get("id", "")

        unlearned: list[str] = []
        for prereq_name in prereq_names:
            prereq_topic_id = title_to_id.get(prereq_name.lower(), "")
            if prereq_topic_id and prereq_topic_id in topics_with_history:
                continue
            unlearned.append(prereq_name)

        if not unlearned:
            return None

        prereq_list = "、".join(unlearned[:3])
        first_prereq = unlearned[0]
        message = (
            f"「{topic_title}」を理解するには、まず以下の前提知識を押さえる必要があります：\n\n"
            f"**{prereq_list}**\n\n"
            f"この前提知識を理解していますか？\n"
            f"理解している場合は、その前提で「{topic_title}」の説明に進みます。"
            f"理解できていない場合は、まず「{first_prereq}」から説明します。\n\n"
            f"（下の選択肢から進め方を選んでください。）"
        )
        return {
            "message": message,
            "first_prerequisite": first_prereq,
            "unlearned": unlearned,
        }
    except Exception:
        logger.warning("Prerequisite check failed, continuing without intervention", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Chat history persistence
# ---------------------------------------------------------------------------


def persist_chat_history(
    user_id: str,
    course_id: str,
    topic_id: str,
    history: list[dict],
    user_message: str,
    assistant_answer: str,
    user_message_id: str | None = None,
    assistant_message_id: str | None = None,
) -> dict:
    """チャット履歴を PostgreSQL に永続化し、付与したメッセージ id を返す。

    各メッセージに安定した ``id`` を焼き込む。これを interest_traces にも記録することで、
    「この問いに戻る」を同じ問いの再送信ではなく該当往復（元の Q/A）へのジャンプに使える。
    id は best-effort で、永続化に失敗しても返す（呼び出し側の in-memory 状態と一致させる）。
    """
    user_message_id = user_message_id or str(uuid.uuid4())
    assistant_message_id = assistant_message_id or str(uuid.uuid4())
    updated_history = history + [
        {"role": "user", "content": user_message, "id": user_message_id},
        {"role": "assistant", "content": assistant_answer, "id": assistant_message_id},
    ]
    try:
        session = _pg_session()
        try:
            session.execute(
                sa_text("""
                    INSERT INTO learning_chat_history (user_id, course_id, topic_id, history, updated_at)
                    VALUES (CAST(:user_id AS uuid), :course_id, :topic_id, CAST(:history AS jsonb), now())
                    ON CONFLICT (user_id, course_id, topic_id)
                    DO UPDATE SET history = CAST(:history AS jsonb), updated_at = now()
                """),
                {
                    "user_id": user_id,
                    "course_id": course_id,
                    "topic_id": topic_id,
                    "history": json.dumps(updated_history, ensure_ascii=False),
                },
            )
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
    except Exception:
        logger.exception(
            "Failed to persist learning chat for user=%s topic=%s",
            user_id, topic_id,
        )
    return {
        "user_message_id": user_message_id,
        "assistant_message_id": assistant_message_id,
    }


# ---------------------------------------------------------------------------
# Misconception detection
# ---------------------------------------------------------------------------


def detect_and_record_misconception(
    user_id: str,
    course_id: str,
    course_data: dict,
    topic_id: str,
    user_message: str,
    ai_response: str,
) -> dict | None:
    """AI応答から誤解を検出し、ユーザー個別の learning_states.personal_graph に記録する。

    Issue #133: マスターコースは不変に保ち、誤解はユーザーごとの learning_states に保存する。
    レスポンス用にはマージ済みの topics をコピーして返す。
    """
    wrong = user_message
    if len(wrong) > 60:
        wrong = wrong[:60] + "…"

    correct = ""
    _CORRECTION_MARKERS = ["訂正：", "訂正:", "【訂正】"]
    for line in ai_response.split("\n"):
        matched_marker = next((m for m in _CORRECTION_MARKERS if m in line), None)
        if matched_marker:
            correct = line.split(matched_marker, 1)[1].strip()
            break

    if not correct:
        correct = "（AIの応答を参照してください）"

    today = datetime.date.today()
    misconception = {
        "label": f"{today.month}/{today.day} の訂正",
        "wrong": wrong,
        "correct": correct,
    }

    try:
        record_personal_misconception(user_id, course_id, topic_id, misconception)
    except Exception:
        logger.exception(
            "Failed to record misconception to learning_states for user=%s course=%s",
            user_id, course_id,
        )

    # Issue #145: 個人レイヤー更新をそのまま返す（マスターデータは不変）
    personal = get_personal_layer(user_id, course_id)
    return {"personal_layer": personal}


# ---------------------------------------------------------------------------
# Unanswered query logging
# ---------------------------------------------------------------------------


def log_unanswered_query(user_id: str, course_id: str, topic_id: str, question: str) -> None:
    """RAG検索で回答できなかった質問をDBに記録する。"""
    session = _pg_session()
    try:
        session.execute(
            sa_text("""
                INSERT INTO unanswered_query_logs (id, user_id, course_id, topic_id, question)
                VALUES (:id, CAST(:user_id AS uuid), :course_id, :topic_id, :question)
            """),
            {
                "id": str(uuid.uuid4()),
                "user_id": user_id,
                "course_id": course_id,
                "topic_id": topic_id,
                "question": question,
            },
        )
        session.commit()
    except Exception as exc:
        session.rollback()
        logger.warning("Failed to log unanswered query: %s", exc)
    finally:
        session.close()


# ---------------------------------------------------------------------------
# 関心痕跡 InterestTraceStore (L3 資産化 / Stage 3) — 安価記録・LLM不使用
# ---------------------------------------------------------------------------

# tension は TensionMiningAgent (B層, backend/core/tension/) が追加する kind。
# LLM 出力は常に status='candidate' で、学習者本人の confirm/dismiss でのみ遷移する（P1）。
_INTEREST_KINDS = ("raw", "question", "detour", "misconception", "tension")
_TRACE_STATUSES = (
    "open", "revisited", "resolved",   # 既存
    "candidate",      # LLM提案・本人未確定（tension のみ）
    "dismissed",      # 本人が「違う」と判定（削除しない。P4）
    "articulated",    # 本人が自分の言葉で編集済み
    "connected",      # グラフ上の node/edge に接続済み
    "abstracted",     # 新しい抽象化・教材提案へ昇格
)


def record_interest_trace(
    user_id: str,
    course_id: str,
    topic_id: str | None,
    kind: str,
    text: str,
    context_label: str = "",
    extra_payload: dict | None = None,
    status: str = "open",
) -> None:
    """学習者の問い・寄り道・誤答を interest_traces に1行追記する（安価・LLM不使用）。

    失敗してもチャット応答を止めない（best-effort）。chat_anchors への二重書きはしない。
    """
    if kind not in _INTEREST_KINDS:
        kind = "raw"
    if status not in _TRACE_STATUSES:
        status = "open"
    payload = {"text": (text or "")[:500], "context_label": context_label or ""}
    if extra_payload:
        payload.update(extra_payload)
    session = _pg_session()
    try:
        session.execute(
            sa_text("""
                INSERT INTO interest_traces (id, user_id, course_id, topic_id, kind, payload, status)
                VALUES (gen_random_uuid(), CAST(:uid AS uuid), :cid, :tid, :kind,
                        CAST(:payload AS jsonb), :status)
            """),
            {
                "uid": user_id, "cid": course_id, "tid": topic_id, "kind": kind,
                "payload": json.dumps(payload, ensure_ascii=False), "status": status,
            },
        )
        session.commit()
    except Exception as exc:
        session.rollback()
        logger.warning("Failed to record interest trace: %s", exc)
    finally:
        session.close()


def get_interest_traces(user_id: str, course_id: str, topic_id: str | None = None, limit: int = 50) -> dict:
    """問いの軌跡（UnfinishedQuestionBox）を実データで返す（L3）。

    status を主役に、未解決(open)→再訪推奨(revisited)→解決済み(resolved) の順で返す。
    行→ビューの変換と再訪のころ合い算出は core.learning_experience.build_traces_view に集約。
    """
    from core.learning_experience import build_traces_view

    session = _pg_session()
    try:
        rows = session.execute(
            sa_text("""
                SELECT id, kind, status, payload, created_at
                FROM interest_traces
                WHERE user_id = CAST(:uid AS uuid) AND course_id = :cid
                  -- tension の未確定候補・棄却は問いの軌跡に出さない（提示はダイジェスト経由のみ。P1/P7）
                  AND NOT (kind = 'tension' AND status IN ('candidate', 'dismissed'))
                ORDER BY
                    CASE status WHEN 'revisited' THEN 0 WHEN 'open' THEN 1 ELSE 2 END,
                    created_at DESC
                LIMIT :lim
            """),
            {"uid": user_id, "cid": course_id, "lim": limit},
        ).fetchall()
    except Exception as exc:
        logger.warning("get_interest_traces failed: %s", exc)
        rows = []
    finally:
        session.close()

    view = build_traces_view(rows)
    view["course_id"] = course_id
    view["topic_id"] = topic_id
    return view


def aggregate_interest_dashboard(course_id: str, topic_title_map: dict | None = None, top_n: int = 10) -> dict:
    """教員向け InterestDashboard の集団集計（L4）。

    **個人を特定しない**: 返すのは件数・比率・関与人数（COUNT(DISTINCT user_id)）のみで、
    痕跡本文や user_id は一切出さない。集団集計を既定とする（仕様書 §6-5）。
    """
    title_map = topic_title_map or {}
    session = _pg_session()
    try:
        cohort = session.execute(
            sa_text("SELECT COUNT(DISTINCT user_id) FROM interest_traces WHERE course_id = :cid"),
            {"cid": course_id},
        ).scalar() or 0

        rows = session.execute(
            sa_text("""
                SELECT topic_id,
                       COUNT(*) AS cnt,
                       COUNT(*) FILTER (WHERE status IN ('open', 'revisited')) AS unfinished,
                       COUNT(DISTINCT user_id) AS learners
                FROM interest_traces
                WHERE course_id = :cid
                GROUP BY topic_id
                ORDER BY cnt DESC
                LIMIT :lim
            """),
            {"cid": course_id, "lim": top_n},
        ).fetchall()

        summary = session.execute(
            sa_text("""
                SELECT
                    COUNT(*) FILTER (WHERE kind = 'question' AND status IN ('open','revisited')) AS open_q,
                    COUNT(*) FILTER (WHERE kind = 'detour') AS detours,
                    COUNT(*) FILTER (WHERE kind = 'misconception') AS miscon
                FROM interest_traces
                WHERE course_id = :cid
            """),
            {"cid": course_id},
        ).fetchone()

        # 違和感ヒートマップ（TensionMiningAgent Stage 3）。
        # 対象は本人が引き受けた status のみ（candidate/dismissed は集計に入れない —
        # 本人が引き受けたものだけが違和感。P1/P3）。
        tension_rows = session.execute(
            sa_text("""
                SELECT topic_id,
                       COALESCE(payload->>'tension_type', 'unclassified') AS tension_type,
                       COUNT(*) AS cnt,
                       COUNT(DISTINCT user_id) AS learners
                FROM interest_traces
                WHERE course_id = :cid AND kind = 'tension'
                  AND status IN ('open', 'articulated', 'connected', 'abstracted')
                GROUP BY topic_id, COALESCE(payload->>'tension_type', 'unclassified')
                ORDER BY cnt DESC
            """),
            {"cid": course_id},
        ).fetchall()
    except Exception as exc:
        logger.warning("aggregate_interest_dashboard failed: %s", exc)
        return {"course_id": course_id, "cohort_size": 0, "hotspots": [],
                "unfinished_summary": {"open_questions": 0, "repeated_detours": 0, "recurring_misconceptions": 0},
                "tension_heatmap": []}
    finally:
        session.close()

    # k-匿名化: 関与人数 n<3 のセルは表示しない（個人を特定不可能にする）。
    # 痕跡本文・user_id は一切出さない（匿名件数 + tension_type 分布のみ）。
    _TENSION_K_ANONYMITY = 3
    tension_heatmap = []
    from core.tension.schema import TENSION_TYPE_LABELS as _TT_LABELS
    for r in tension_rows:
        tid, ttype, cnt, learners = r[0], str(r[1]), int(r[2]), int(r[3])
        if learners < _TENSION_K_ANONYMITY:
            continue
        tension_heatmap.append({
            "topic_title": title_map.get(tid) or tid or "(不明トピック)",
            "tension_type": ttype,
            "tension_type_label": _TT_LABELS.get(ttype, _TT_LABELS["unclassified"]),
            "count": cnt,
            "learners": learners,  # 関与人数（個人は特定しない）
        })

    hotspots = []
    for r in rows:
        tid, cnt, unfinished, learners = r[0], int(r[1]), int(r[2]), int(r[3])
        hotspots.append({
            "topic_title": title_map.get(tid) or tid or "(不明トピック)",
            "interest_count": cnt,
            "unfinished_ratio": round(unfinished / cnt, 2) if cnt else 0.0,
            "learners": learners,  # 関与人数（個人は特定しない）
        })

    return {
        "course_id": course_id,
        "cohort_size": int(cohort),
        "hotspots": hotspots,
        "unfinished_summary": {
            "open_questions": int(summary[0]) if summary else 0,
            "repeated_detours": int(summary[1]) if summary else 0,
            "recurring_misconceptions": int(summary[2]) if summary else 0,
        },
        "tension_heatmap": tension_heatmap,
    }


def resolve_interest_trace(user_id: str, trace_id: str, status: str = "resolved") -> bool:
    """痕跡の status を更新する（解決済み化など）。本人の痕跡のみ更新可。"""
    if status not in _TRACE_STATUSES:
        return False
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("""
                UPDATE interest_traces
                SET status = :status, last_seen_at = now()
                WHERE id = CAST(:tid AS uuid) AND user_id = CAST(:uid AS uuid)
                RETURNING id
            """),
            {"status": status, "tid": trace_id, "uid": user_id},
        ).fetchone()
        session.commit()
        return row is not None
    except Exception as exc:
        session.rollback()
        logger.warning("resolve_interest_trace failed: %s", exc)
        return False
    finally:
        session.close()


def get_chunk_passage(chunk_id: str) -> dict | None:
    """出典ポップアップ用に、チャンク本文（数式プレースホルダ正規化済み）と数式を返す。

    display_text を優先し、`normalize_to_placeholder_format` で [[FORMULA_N]] 形式へ正規化する
    （フロントの renderMaterialChunk がそのまま数式を KaTeX 描画できる形）。
    """
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("""
                SELECT c.text, c.display_text, c.formulas, c.chapter, c.section,
                       COALESCE(d.title, d.filename, '') AS source_title, d.filename
                FROM chunks c
                LEFT JOIN documents d ON c.document_id = d.id
                WHERE c.id = CAST(:cid AS uuid)
                LIMIT 1
            """),
            {"cid": chunk_id},
        ).fetchone()
    except Exception as exc:
        logger.warning("get_chunk_passage failed: %s", exc)
        return None
    finally:
        session.close()

    if not row:
        return None
    raw_text = row[1] or row[0] or ""
    raw_formulas = row[2] if row[2] else []
    text, formulas = _normalize_formulas(raw_text, raw_formulas)
    section = " · ".join([s for s in [row[3], row[4]] if s])
    return {
        "chunk_id": str(chunk_id),
        "text": text,
        "formulas": formulas,
        "source_title": row[5] or row[6] or "不明な教材",
        "source_file": row[6] or "",
        "section": section,
    }


def record_internalization(user_id: str, trace_id: str, reason: str) -> bool:
    """痕跡に「なぜ自分に重要か」(Internalization Prompt) を payload へ保存する（L3/内発的動機）。

    本人の痕跡のみ更新可。LLM は使わず、学習者の言語化テキストをそのまま保持する。
    """
    reason = (reason or "").strip()[:1000]
    if not reason:
        return False
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("""
                UPDATE interest_traces
                SET payload = payload || jsonb_build_object('internalization', :reason),
                    last_seen_at = now()
                WHERE id = CAST(:tid AS uuid) AND user_id = CAST(:uid AS uuid)
                RETURNING id
            """),
            {"reason": reason, "tid": trace_id, "uid": user_id},
        ).fetchone()
        session.commit()
        return row is not None
    except Exception as exc:
        session.rollback()
        logger.warning("record_internalization failed: %s", exc)
        return False
    finally:
        session.close()


# ---------------------------------------------------------------------------
# 違和感（tension）— TensionMiningAgent (B層) Stage 2: ダイジェスト・本人確定
# ---------------------------------------------------------------------------
# P1: 確定は学習者本人のみ。P3: 候補・確定とも本人のみ可視（教員は個別行に触れない）。
# P4: dismiss は削除でなく status='dismissed' で保持する。

# ダイジェスト提示条件（設計書 §8/§9）
_TENSION_DIGEST_MIN_CONFIDENCE = 0.55
_TENSION_DIGEST_MAX_ITEMS = 3


def _record_tension_event(
    trace_id: str, old_status: str, new_status: str, user_id: str, metadata: dict | None = None,
) -> None:
    """tension の状態変更を theory_review_events に監査記録する（entity_type='tension'）。"""
    session = _pg_session()
    try:
        session.execute(
            sa_text("""
                INSERT INTO theory_review_events (entity_type, entity_id, old_status, new_status, changed_by, metadata)
                VALUES ('tension', :eid, :old, :new, CAST(:uid AS uuid), CAST(:meta AS jsonb))
            """),
            {
                "eid": str(trace_id), "old": old_status, "new": new_status,
                "uid": user_id, "meta": json.dumps(metadata or {}, ensure_ascii=False),
            },
        )
        session.commit()
    except Exception as exc:
        session.rollback()
        logger.warning("_record_tension_event failed: %s", exc)
    finally:
        session.close()


def get_tension_digest(user_id: str, course_id: str) -> dict:
    """本人の status='candidate' かつ confidence>=0.55 を新しい順に最大3件返す。

    confidence の数値は返さない（学習者に数値スコアを見せない原則の準用）。
    件数バッジ・ランキング化はしない（P7）。
    """
    from core.tension.schema import TENSION_TYPE_LABELS

    session = _pg_session()
    try:
        rows = session.execute(
            sa_text("""
                SELECT id, payload
                FROM interest_traces
                WHERE user_id = CAST(:uid AS uuid) AND course_id = :cid
                  AND kind = 'tension' AND status = 'candidate'
                  AND COALESCE((payload->>'confidence')::float, 0.0) >= :minc
                ORDER BY created_at DESC
                LIMIT :lim
            """),
            {
                "uid": user_id, "cid": course_id,
                "minc": _TENSION_DIGEST_MIN_CONFIDENCE, "lim": _TENSION_DIGEST_MAX_ITEMS,
            },
        ).fetchall()
    except Exception as exc:
        logger.warning("get_tension_digest failed: %s", exc)
        rows = []
    finally:
        session.close()

    items = []
    for r in rows:
        payload = r[1] if isinstance(r[1], dict) else (json.loads(r[1]) if r[1] else {})
        ttype = payload.get("tension_type", "unclassified")
        items.append({
            "trace_id": str(r[0]),
            "paraphrase": payload.get("paraphrase", ""),
            "evidence_quote": payload.get("text", ""),
            "tension_type_label": TENSION_TYPE_LABELS.get(ttype, TENSION_TYPE_LABELS["unclassified"]),
            "context_label": payload.get("context_label", ""),
        })
    return {"course_id": course_id, "items": items}


def confirm_tension_trace(user_id: str, trace_id: str, learner_text: str = "") -> dict | None:
    """候補を本人が引き受ける: candidate → open（learner_text ありなら articulated）。

    本人の痕跡のみ更新可。状態変更は theory_review_events に監査記録する。
    """
    learner_text = (learner_text or "").strip()[:1000]
    new_status = "articulated" if learner_text else "open"
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("""
                UPDATE interest_traces
                SET status = :new_status,
                    payload = payload || jsonb_build_object('learner_text', CAST(:ltext AS text)),
                    last_seen_at = now()
                WHERE id = CAST(:tid AS uuid) AND user_id = CAST(:uid AS uuid)
                  AND kind = 'tension' AND status = 'candidate'
                RETURNING id
            """),
            {"new_status": new_status, "ltext": learner_text, "tid": trace_id, "uid": user_id},
        ).fetchone()
        session.commit()
    except Exception as exc:
        session.rollback()
        logger.warning("confirm_tension_trace failed: %s", exc)
        return None
    finally:
        session.close()
    if row is None:
        return None
    _record_tension_event(trace_id, "candidate", new_status, user_id,
                          {"articulated": bool(learner_text)})
    return {"trace_id": str(trace_id), "status": new_status}


def dismiss_tension_trace(user_id: str, trace_id: str) -> dict | None:
    """本人が「違う」と判定: candidate → dismissed（行は残す。P4）。"""
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("""
                UPDATE interest_traces
                SET status = 'dismissed', last_seen_at = now()
                WHERE id = CAST(:tid AS uuid) AND user_id = CAST(:uid AS uuid)
                  AND kind = 'tension' AND status = 'candidate'
                RETURNING id
            """),
            {"tid": trace_id, "uid": user_id},
        ).fetchone()
        session.commit()
    except Exception as exc:
        session.rollback()
        logger.warning("dismiss_tension_trace failed: %s", exc)
        return None
    finally:
        session.close()
    if row is None:
        return None
    _record_tension_event(trace_id, "candidate", "dismissed", user_id)
    return {"trace_id": str(trace_id), "status": "dismissed"}


def connect_tension_trace(
    user_id: str, trace_id: str, component_id: str = "", edge_id: str = "",
) -> dict | None:
    """確定済み tension をグラフ上の node/edge に接続する: → connected（後続フェーズ）。

    candidate からの直接 connect は許さない（本人の confirm を経ること。P1）。
    """
    component_id = (component_id or "").strip()
    edge_id = (edge_id or "").strip()
    if not component_id and not edge_id:
        return None
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("""
                UPDATE interest_traces
                SET status = 'connected',
                    payload = jsonb_set(
                        payload,
                        '{target_refs}',
                        COALESCE(payload->'target_refs', '{}'::jsonb) || jsonb_build_object(
                            'component_ids',
                            COALESCE(payload->'target_refs'->'component_ids', '[]'::jsonb)
                                || CASE WHEN :comp <> '' THEN jsonb_build_array(CAST(:comp AS text)) ELSE '[]'::jsonb END,
                            'edge_ids',
                            COALESCE(payload->'target_refs'->'edge_ids', '[]'::jsonb)
                                || CASE WHEN :edge <> '' THEN jsonb_build_array(CAST(:edge AS text)) ELSE '[]'::jsonb END
                        )
                    ),
                    last_seen_at = now()
                WHERE id = CAST(:tid AS uuid) AND user_id = CAST(:uid AS uuid)
                  AND kind = 'tension' AND status IN ('open', 'articulated')
                RETURNING status
            """),
            {"comp": component_id, "edge": edge_id, "tid": trace_id, "uid": user_id},
        ).fetchone()
        session.commit()
    except Exception as exc:
        session.rollback()
        logger.warning("connect_tension_trace failed: %s", exc)
        return None
    finally:
        session.close()
    if row is None:
        return None
    _record_tension_event(trace_id, "open/articulated", "connected", user_id,
                          {"component_id": component_id, "edge_id": edge_id})
    return {"trace_id": str(trace_id), "status": "connected"}


def get_graph_element_context(
    course_data: dict,
    chunk_id: str,
    element_id: str,
    element_type: str | None = None,
    element_label: str | None = None,
) -> dict:
    """EXPLAIN_GRAPH_ELEMENT 用にチャンク、グラフ説明、関連教材を集める。"""
    material_ids = [
        s.get("material_id")
        for s in course_data.get("sources", [])
        if isinstance(s, dict) and s.get("material_id")
    ]
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("""
                SELECT c.id, c.text, c.display_text, c.formulas, c.material_id,
                       COALESCE(d.title, d.filename, '') AS source_title,
                       d.knowledge_graph, d.uploaded_by, c.source_metadata
                FROM chunks c
                LEFT JOIN documents d ON c.document_id = d.id
                WHERE c.id = CAST(:chunk_id AS uuid)
                LIMIT 1
            """),
            {"chunk_id": chunk_id},
        ).fetchone()
        if not row:
            return {}

        raw_text = row[2] or row[1] or ""
        raw_formulas = row[3] if row[3] else []
        normalized_text, formulas = _normalize_formulas(raw_text, raw_formulas)
        graph = _json_obj(row[6])
        source_metadata = _json_obj(row[8])
        graph_description = ""
        resolved_label = element_label or element_id
        target_formula = None
        target_citation = None
        target_reference = None

        if element_type == "citation":
            key = element_id.removeprefix("bib:")
            for citation in source_metadata.get("tex_citations", []) if isinstance(source_metadata.get("tex_citations"), list) else []:
                if not isinstance(citation, dict) or str(citation.get("key") or "") != key:
                    continue
                target_citation = citation
                bib = citation.get("bib") if isinstance(citation.get("bib"), dict) else {}
                resolved_label = _citation_label(key, bib)
                graph_description = _format_citation_description(key, bib)
                break

        if element_type == "reference":
            key = element_id.removeprefix("ref:")
            for ref in source_metadata.get("tex_refs", []) if isinstance(source_metadata.get("tex_refs"), list) else []:
                if not isinstance(ref, dict) or str(ref.get("key") or "") != key:
                    continue
                target_reference = ref
                resolved_label = f"参照 {key}"
                graph_description = f"TeX ソース内の \\\\ref / \\\\eqref 参照です。参照キー: {key}"
                break

        if element_type == "formula":
            for formula in formulas:
                if not isinstance(formula, dict):
                    continue
                fid = str(formula.get("id") or "").strip()
                latex = str(formula.get("latex") or "").strip()
                if fid == element_id or fid == element_label or element_id in (fid, latex):
                    target_formula = {
                        "id": fid,
                        "latex": latex,
                        "is_display": formula.get("is_display", True),
                    }
                    resolved_label = "この数式"
                    break
            if target_formula is None and len(formulas) == 1 and isinstance(formulas[0], dict):
                formula = formulas[0]
                target_formula = {
                    "id": str(formula.get("id") or "").strip(),
                    "latex": str(formula.get("latex") or "").strip(),
                    "is_display": formula.get("is_display", True),
                }
                resolved_label = "この数式"

        if element_type == "relationship" or (element_type != "formula" and ":" in element_id):
            for rel in graph.get("relationships", []) or []:
                if not isinstance(rel, dict):
                    continue
                rel_id = f"{rel.get('source')}:{rel.get('relation', 'RELATED_TO')}:{rel.get('target')}"
                if rel_id == element_id:
                    graph_description = str(rel.get("description") or "")
                    resolved_label = resolved_label or rel_id
                    break
        else:
            for concept in graph.get("concepts", []) or []:
                if not isinstance(concept, dict):
                    continue
                cid = str(concept.get("id") or concept.get("name") or "")
                name = str(concept.get("name") or cid)
                if cid == element_id or name == element_id or name == element_label:
                    graph_description = str(concept.get("description") or "")
                    resolved_label = name
                    break

        related_chunks: list[dict] = []
        if material_ids:
            placeholders = ", ".join(f":mid_{i}" for i in range(len(material_ids)))
            params = {
                "chunk_id": chunk_id,
                "label": f"%{resolved_label}%",
                "limit": 3,
                **{f"mid_{i}": mid for i, mid in enumerate(material_ids)},
            }
            related_rows = session.execute(
                sa_text(f"""
                    SELECT c.id, c.text, COALESCE(d.title, d.filename, '') AS source_title
                    FROM chunks c
                    LEFT JOIN documents d ON c.document_id = d.id
                    WHERE c.material_id IN ({placeholders})
                      AND c.id != CAST(:chunk_id AS uuid)
                      AND c.text ILIKE :label
                    ORDER BY c.chunk_index ASC
                    LIMIT :limit
                """),
                params,
            ).fetchall()
            related_chunks = [
                {
                    "id": str(r[0]),
                    "text": r[1],
                    "source_title": r[2] or "教材",
                }
                for r in related_rows
                if r[1]
            ]

        return {
            "chunk_id": str(row[0]),
            "chunk_text": normalized_text,
            "formulas": formulas,
            "material_id": row[4] or "",
            "source_title": row[5] or "教材",
            "instructor_id": str(row[7]) if row[7] else None,
            "element_id": element_id,
            "element_type": element_type or "concept",
            "element_label": resolved_label,
            "target_formula": target_formula,
            "target_citation": target_citation,
            "target_reference": target_reference,
            "graph_description": graph_description,
            "related_chunks": related_chunks,
        }
    finally:
        session.close()


def record_student_stumble_event(
    *,
    instructor_id: str | None,
    student_id: str,
    course_id: str,
    material_id: str | None,
    chunk_id: str | None,
    element_id: str | None,
    element_label: str | None,
    event_type: str,
    user_message: str | None = None,
    generated_explanation: str | None = None,
) -> None:
    """教員向け教材改善フィードバックとして、説明要求や不足イベントを記録する。"""
    session = _pg_session()
    try:
        session.execute(
            sa_text("""
                INSERT INTO student_stumble_events (
                    instructor_id, student_id, course_id, material_id, chunk_id,
                    element_id, element_label, event_type, user_message, generated_explanation
                )
                VALUES (
                    CAST(:instructor_id AS uuid), CAST(:student_id AS uuid), :course_id,
                    :material_id, CAST(:chunk_id AS uuid), :element_id, :element_label,
                    :event_type, :user_message, :generated_explanation
                )
            """),
            {
                "instructor_id": instructor_id,
                "student_id": student_id,
                "course_id": course_id,
                "material_id": material_id,
                "chunk_id": chunk_id,
                "element_id": element_id,
                "element_label": element_label,
                "event_type": event_type,
                "user_message": user_message,
                "generated_explanation": generated_explanation,
            },
        )
        session.commit()
    except Exception as exc:
        session.rollback()
        logger.warning("Failed to record student stumble event: %s", exc)
    finally:
        session.close()


# ---------------------------------------------------------------------------
# PDF processing helpers
# ---------------------------------------------------------------------------


def extract_pdf_text(pdf_bytes: bytes) -> str:
    """PDFからテキストを抽出する。PyMuPDFを使用。"""
    import fitz  # PyMuPDF

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    text_parts = []
    for page in doc:
        text_parts.append(page.get_text())
    doc.close()
    return "\n".join(text_parts).replace("\x00", "")


def extract_pdf_pages(pdf_bytes: bytes) -> list[dict]:
    """PDFからページ単位のテキストを抽出する。"""
    import fitz  # PyMuPDF

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages = []
    for i, page in enumerate(doc, start=1):
        pages.append({"page": i, "text": page.get_text().replace("\x00", "")})
    doc.close()
    return pages


def _safe_dsl_value(value: str) -> str:
    cleaned = str(value or "").strip()
    cleaned = cleaned.replace("(", "[").replace(")", "]").replace(":", "-")
    return cleaned[:80] or "Concept"


def _node_var(value: str, index: int) -> str:
    base = "".join(ch.lower() for ch in str(value or "") if ch.isalnum())
    if not base:
        base = f"v{index}"
    if base[0].isdigit():
        base = "v" + base
    return base[:16]


def _synthesize_smiles_dsl(edges: list[dict]) -> str:
    node_vars: dict[str, str] = {}

    def _var(node: str) -> str:
        if node not in node_vars:
            node_vars[node] = _node_var(node, len(node_vars))
        return node_vars[node]

    parts: list[str] = []
    for edge in edges[:24]:
        source = str(edge.get("source") or "").strip()
        target = str(edge.get("target") or "").strip()
        if not source or not target:
            continue
        predicate = str(edge.get("core_predicate") or edge.get("relation") or "RELATED_TO").upper()
        domain_verb = str(edge.get("domain_verb") or predicate.lower()).replace(":", "-")
        polarity = str(edge.get("polarity") or "+")
        is_core = bool(edge.get("is_core", True))
        arrow = f"==[{predicate}:{domain_verb}:{polarity}]=>" if is_core else f"-[{predicate}:{domain_verb}:{polarity}]->"
        parts.append(
            f"({_var(source)}:Resource:{_safe_dsl_value(source)}) "
            f"{arrow} "
            f"({_var(target)}:Resource:{_safe_dsl_value(target)})"
        )
    return " ".join(parts)


def _paper_structure_to_knowledge_graph(structure: PaperStructure) -> dict:
    data = structure.model_dump(mode="json")
    abstract = data.get("abstract_structure") or {}
    edges = abstract.get("edges") if isinstance(abstract.get("edges"), list) else []
    smiles_dsl = str(abstract.get("smiles_dsl") or "").strip()
    if not smiles_dsl and edges:
        smiles_dsl = _synthesize_smiles_dsl(edges)
        abstract["smiles_dsl"] = smiles_dsl

    variables = abstract.get("variables") if isinstance(abstract.get("variables"), list) else []
    concepts = [
        {
            "id": _node_var(str(v), i),
            "name": str(v),
            "description": "",
            "type": "concept",
        }
        for i, v in enumerate(variables)
    ]
    relationships = [
        {
            "source": str(edge.get("source") or ""),
            "target": str(edge.get("target") or ""),
            "relation": str(edge.get("core_predicate") or "RELATED_TO"),
            "description": str(edge.get("domain_verb") or ""),
        }
        for edge in edges
        if edge.get("source") and edge.get("target")
    ]
    return {
        "title": data.get("title") or structure.title,
        "domain": data.get("domain") or "",
        "concepts": concepts,
        "relationships": relationships,
        "abstract_structure": abstract,
        "key_equations": data.get("key_equations") or [],
        "chapters": [],
    }


def build_knowledge_graph(text: str, title: str) -> dict:
    """抽出したテキストからDSLベースのナレッジグラフを構築する。"""
    structured_prompt = f"""以下の教材テキストから PaperStructure を抽出してください。

教材タイトル: {title}

教材テキスト:
{text[:12000]}

要件:
- abstract_structure.variables に主要概念・物理量・理論枠組みを入れる
- abstract_structure.edges に概念間の CONTAINS / REQUIRES / DEFINES / MEASURES / CAUSES / CORRELATES / EQUIVALENT 関係を入れる
- abstract_structure.smiles_dsl は必ず非空にする
- DSL のノードは必ず (varID:OntologyType:value) 形式にする
- CONTAINS 関係がある場合は edges に必ず含める
- 物理論文では MathematicalObject, PhysicalPhenomenon, TheoreticalFramework, Theorem, Symmetry, Particle を優先する
"""

    try:
        structure = generate_text_with_structured_output(
            messages=[{"role": "user", "content": structured_prompt}],
            response_format=PaperStructure,
        )
        graph = _paper_structure_to_knowledge_graph(structure)
        return graph  # smiles_dsl がなくても返す。呼び出し元で合成・プレースホルダーを処理する
    except Exception as exc:
        logger.warning("Structured knowledge graph extraction failed: %s", exc, exc_info=True)

    prompt = f"""以下の教材テキストから知識グラフを構築してください。

**教材タイトル:** {title}

**教材テキスト (抜粋):**
{text[:8000]}

以下のJSON形式で出力してください:
{{
  "title": "教材タイトル",
  "domain": "分野名",
  "concepts": [
    {{
      "id": "concept_1",
      "name": "概念名",
      "description": "概念の説明",
      "type": "definition|theorem|method|example"
    }}
  ],
  "relationships": [
    {{
      "source": "concept_1",
      "target": "concept_2",
      "relation": "REQUIRES|CONTAINS|CAUSES|DEFINES|EXTENDS|APPLIES_TO",
      "description": "関係の説明"
    }}
  ],
  "abstract_structure": {{
    "variables": ["concept_1", "concept_2"],
    "edges": [
      {{
        "source": "concept_1",
        "target": "concept_2",
        "core_predicate": "CONTAINS|REQUIRES|CAUSES|DEFINES|EXTENDS|APPLIES_TO",
        "domain_verb": "contains",
        "polarity": "+",
        "is_core": true
      }}
    ],
    "smiles_dsl": "(concept_1:Resource:概念名) ==[CONTAINS:contains:+]=> (concept_2:Resource:下位概念名)"
  }},
  "chapters": [
    {{
      "title": "章タイトル",
      "concepts": ["concept_1", "concept_2"],
      "topics": [
        {{
          "id": "topic_1",
          "title": "トピックタイトル",
          "concepts": ["concept_1"]
        }}
      ]
    }}
  ]
}}"""

    try:
        raw = generate_text(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()
        if raw.startswith("json"):
            raw = raw[4:].strip()
        return json.loads(raw)
    except Exception as exc:
        logger.warning("Knowledge graph extraction failed: %s", exc)
        return {"title": title, "concepts": [], "relationships": [], "chapters": []}


# ---------------------------------------------------------------------------
# Embedding 制約
# ---------------------------------------------------------------------------
# Vertex AI の text-embedding 系モデルは
#   - 1 入力あたり最大 ~2048 トークン
#   - 1 リクエストあたり最大 ~20000 トークン (バッチ合計)
# の制約を持つ。日本語や数式を含む論文ではトークン密度が高く、
# 文字数だけで判定するとこの上限を超えやすい。
# 安全側に倒した推定として「2 文字 = 1 トークン」を仮定し、
# チャンク自体を超過させない/バッチ合計が上限を超えないように制御する。
EMBEDDING_MAX_INPUT_TOKENS = 2000   # 1 入力あたりの安全上限 (Vertex AI 2048 に対して余裕)
EMBEDDING_MAX_BATCH_TOKENS = 18000  # 1 リクエスト合計の安全上限 (Vertex AI 20000 に対して余裕)
EMBEDDING_BATCH_MAX_ITEMS = 50      # 1 リクエストあたりの最大アイテム数


def _estimate_tokens(text: str) -> int:
    """テキストのトークン数を保守的に見積もる。

    日本語・数式を含む可能性があるため、1 トークン = 約 2 文字と仮定する
    (英語では実際にはもっと少ないが、過大評価で安全側に倒す)。
    """
    if not text:
        return 0
    return max(1, (len(text) + 1) // 2)


# 段落 → 改行 → 文末 → 空白 の優先順で分割を試みる。
_SPLIT_SEPARATORS: tuple[str, ...] = (
    "\n\n",   # 段落
    "\n",     # 改行
    "。",     # 日本語句点
    ". ",     # 英文末
    "！",
    "？",
    "; ",
    "、",
    ", ",
    " ",      # 空白 (最後のフォールバック)
)


def _split_with_separator(text: str, separator: str) -> list[str]:
    """指定セパレータで分割するが、セパレータを各セグメント末尾に残す。"""
    if not separator or separator not in text:
        return [text]
    parts: list[str] = []
    remaining = text
    while True:
        idx = remaining.find(separator)
        if idx == -1:
            if remaining:
                parts.append(remaining)
            break
        end = idx + len(separator)
        parts.append(remaining[:end])
        remaining = remaining[end:]
    return parts


def _split_oversize_segment(segment: str, max_chars: int) -> list[str]:
    """max_chars を超える単一セグメントを、自然な境界で再分割する。"""
    if len(segment) <= max_chars:
        return [segment]
    for sep in _SPLIT_SEPARATORS:
        if sep in segment:
            sub_parts = _split_with_separator(segment, sep)
            if len(sub_parts) > 1:
                # 分割結果を再帰的に max_chars 以下に揃える。
                result: list[str] = []
                for sub in sub_parts:
                    if len(sub) <= max_chars:
                        result.append(sub)
                    else:
                        result.extend(_split_oversize_segment(sub, max_chars))
                return result
    # 全セパレータで分割できなかった場合は文字単位で強制分割。
    return [segment[i:i + max_chars] for i in range(0, len(segment), max_chars)]


def chunk_text(
    text: str,
    chunk_size: int = 1000,
    overlap: int = 100,
    max_chars: int | None = None,
) -> list[str]:
    """テキストを段落・文・空白などの自然な境界で分割する。

    Parameters
    ----------
    text : str
        対象テキスト。
    chunk_size : int
        ターゲットとなるチャンク文字数 (これを超えたら次のチャンクを開始)。
    overlap : int
        隣接チャンク間のオーバーラップ文字数。
    max_chars : int | None
        ハード上限。1 チャンクが Embedding API の 1 入力上限を超えないようにする。
        既定では ``EMBEDDING_MAX_INPUT_TOKENS`` 相当の文字数 (2 文字/トークン換算)。
    """
    if max_chars is None:
        max_chars = EMBEDDING_MAX_INPUT_TOKENS * 2  # 1 トークン ≒ 2 文字 (保守的)
    # max_chars と chunk_size の関係を保つ (chunk_size が max_chars を超えないように)。
    chunk_size = min(chunk_size, max_chars)

    if not text:
        return []

    # 1) まず段落で大きく分け、長すぎるものは更に細かく分割する。
    raw_segments = _split_with_separator(text, "\n\n")
    segments: list[str] = []
    for seg in raw_segments:
        if len(seg) > max_chars:
            segments.extend(_split_oversize_segment(seg, max_chars))
        else:
            segments.append(seg)

    # 2) chunk_size に届くまでセグメントを連結してチャンクを作る。
    chunks: list[str] = []
    current = ""
    for seg in segments:
        if not seg:
            continue
        if not current:
            current = seg
            continue
        if len(current) + len(seg) <= chunk_size:
            current += seg
        else:
            stripped = current.strip()
            if stripped:
                chunks.append(stripped)
            # オーバーラップ: 直前チャンクの末尾を引き継いで文脈を維持する。
            if overlap > 0 and len(current) > overlap:
                tail = current[-overlap:]
                # オーバーラップ + 次セグメントが max_chars を超えないようにする。
                if len(tail) + len(seg) <= max_chars:
                    current = tail + seg
                else:
                    current = seg
            else:
                current = seg
    if current.strip():
        chunks.append(current.strip())

    return chunks


def chunk_pdf_pages(
    pages: list[dict],
    chunk_size: int = 1000,
    overlap: int = 100,
    max_chars: int | None = None,
) -> list[dict]:
    """ページ単位テキストをチャンク化し、ページ範囲を保持する。

    Embedding API の入力トークン上限を超えないよう、段落・文・改行などの
    自然な境界でチャンクを区切る。
    """
    if max_chars is None:
        max_chars = EMBEDDING_MAX_INPUT_TOKENS * 2

    full = ""
    spans: list[dict] = []
    for page in pages:
        page_text = str(page.get("text") or "")
        start = len(full)
        full += page_text + "\n"
        spans.append({"page": page.get("page"), "start": start, "end": len(full)})

    if not full.strip():
        return []

    text_chunks = chunk_text(full, chunk_size=chunk_size, overlap=overlap, max_chars=max_chars)

    # 各チャンクに対応するページ範囲を、テキスト内出現位置から逆引きする。
    chunks: list[dict] = []
    cursor = 0
    for chunk in text_chunks:
        idx = full.find(chunk, cursor)
        if idx == -1:
            # オーバーラップにより strip されている可能性があるため、先頭一致でフォールバック。
            head = chunk[:64]
            idx = full.find(head, cursor) if head else -1
        if idx == -1:
            chunk_start = cursor
            chunk_end = cursor + len(chunk)
        else:
            chunk_start = idx
            chunk_end = idx + len(chunk)
            cursor = max(cursor, chunk_end - overlap)

        touched = [
            span["page"]
            for span in spans
            if span["page"] and span["start"] < chunk_end and span["end"] > chunk_start
        ]
        chunks.append({
            "text": chunk,
            "page_start": min(touched) if touched else None,
            "page_end": max(touched) if touched else None,
        })
    return chunks


def _build_embedding_batches(
    texts: list[str],
    *,
    max_batch_tokens: int = EMBEDDING_MAX_BATCH_TOKENS,
    max_input_tokens: int = EMBEDDING_MAX_INPUT_TOKENS,
    max_items: int = EMBEDDING_BATCH_MAX_ITEMS,
) -> list[list[int]]:
    """インデックスのバッチ列を、トークン推定に基づいて構築する。

    各バッチは ``max_batch_tokens`` を超えず、
    各入力は ``max_input_tokens`` を超えないように切り詰める前提で
    呼び出し側がチャンク分割済みであることを期待する
    (チャンクが超過する場合でも、単独で 1 バッチに分けることでリクエスト自体は通す)。
    """
    batches: list[list[int]] = []
    current: list[int] = []
    current_tokens = 0
    for i, text in enumerate(texts):
        est = _estimate_tokens(text)
        # 単独でバッチ上限を超える場合でも、1 アイテムだけのバッチとして送る。
        if est >= max_batch_tokens:
            if current:
                batches.append(current)
                current = []
                current_tokens = 0
            batches.append([i])
            continue
        if current and (
            current_tokens + est > max_batch_tokens
            or len(current) >= max_items
        ):
            batches.append(current)
            current = []
            current_tokens = 0
        current.append(i)
        current_tokens += est
    if current:
        batches.append(current)
    return batches


def _extract_chunk_structure_metadata(knowledge_graph: object) -> tuple[str | None, object | None, object | None]:
    """knowledge_graph から chunks に保存する構造メタデータを取り出す。"""
    graph = _json_obj(knowledge_graph)
    abstract = graph.get("abstract_structure") if isinstance(graph.get("abstract_structure"), dict) else {}

    smiles_dsl = str(abstract.get("smiles_dsl") or graph.get("smiles_dsl") or "").strip() or None

    variables: object | None = abstract.get("variables") or None
    if variables is None:
        concepts = graph.get("concepts")
        if isinstance(concepts, list) and concepts:
            variables = [
                {
                    "id": c.get("id") or c.get("name"),
                    "name": c.get("name") or c.get("id"),
                    "type": c.get("type"),
                    "description": c.get("description"),
                }
                for c in concepts
                if isinstance(c, dict)
            ]

    edge_candidates = abstract.get("edges")
    if not isinstance(edge_candidates, list):
        edge_candidates = graph.get("relationships") if isinstance(graph.get("relationships"), list) else []

    if not smiles_dsl and edge_candidates:
        smiles_dsl = _synthesize_smiles_dsl(edge_candidates) or None

    ancestors = _build_ancestors_from_edges(edge_candidates)

    return smiles_dsl, variables, ancestors or None


def _edge_predicate(edge: dict) -> str:
    return str(
        edge.get("core_predicate")
        or edge.get("relation")
        or edge.get("predicate")
        or ""
    ).upper()


def _build_ancestors_from_edges(edges: object) -> dict[str, list[str]]:
    """CONTAINS エッジから child -> ancestors のマップを構築する。"""
    if not isinstance(edges, list):
        return {}

    parent_of: dict[str, set[str]] = {}
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        if _edge_predicate(edge) != "CONTAINS":
            continue
        source = str(edge.get("source") or "").strip()
        target = str(edge.get("target") or "").strip()
        if source and target:
            parent_of.setdefault(target, set()).add(source)

    def _get_ancestors(node: str, visited: set[str] | None = None) -> list[str]:
        visited = visited or set()
        if node in visited:
            return []
        visited.add(node)
        result: list[str] = []
        for parent in sorted(parent_of.get(node, set())):
            result.append(parent)
            result.extend(_get_ancestors(parent, visited))
        return result

    return {
        child: ancestors
        for child in parent_of
        if (ancestors := _get_ancestors(child))
    }


def embed_chunks(
    material_id: str,
    doc_id: str,
    chunks: list[str] | list[dict],
    knowledge_graph: object | None = None,
) -> int:
    """テキストチャンクをembeddingしてPostgreSQLに保存する。

    Returns:
        実際にDBに保存できたチャンク数

    Raises:
        Exception: embedding生成またはDB保存に失敗した場合（呼び出し元で要ハンドリング）
    """
    embedded_count = 0
    records = [
        c if isinstance(c, dict) else {"text": c, "page_start": None, "page_end": None}
        for c in chunks
    ]

    # Embedding API の 1 入力上限を超えそうなチャンクは、ここで再分割する。
    # チャンク化の段階で対策はしているが、将来の入力経路に備えた最終ガード。
    safe_records: list[dict] = []
    for record in records:
        text_value = str(record.get("text") or "")
        if _estimate_tokens(text_value) <= EMBEDDING_MAX_INPUT_TOKENS:
            safe_records.append(record)
            continue
        sub_texts = chunk_text(
            text_value,
            chunk_size=EMBEDDING_MAX_INPUT_TOKENS * 2,
            overlap=0,
            max_chars=EMBEDDING_MAX_INPUT_TOKENS * 2,
        )
        for sub in sub_texts:
            safe_records.append({
                "text": sub,
                "page_start": record.get("page_start"),
                "page_end": record.get("page_end"),
            })
    records = safe_records

    smiles_dsl, variables, ancestors = _extract_chunk_structure_metadata(knowledge_graph)
    total = len(records)
    all_texts = [str(item.get("text") or "") for item in records]
    batches = _build_embedding_batches(all_texts)
    try:
        progress = 0
        for batch_indices in batches:
            batch = [records[k] for k in batch_indices]
            batch_texts = [all_texts[k] for k in batch_indices]
            batch_tokens = sum(_estimate_tokens(t) for t in batch_texts)
            logger.info(
                "Embedding batch items=%d est_tokens=%d (progress %d/%d) for material %s",
                len(batch), batch_tokens, progress, total, material_id,
            )
            embeddings = generate_embeddings(batch_texts)
            session = _pg_session()
            try:
                for j, embedding in enumerate(embeddings):
                    record = batch[j]
                    chunk_id = uuid.uuid4()
                    session.execute(
                        sa_text("""
                            INSERT INTO chunks (
                                id, document_id, chunk_index, text, embedding,
                                material_id, page_start, page_end,
                                smiles_dsl, variables, ancestors
                            )
                            VALUES (
                                :id, CAST(:doc_id AS uuid), :idx, :text, :embedding,
                                :material_id, :page_start, :page_end,
                                :smiles_dsl, CAST(:variables AS jsonb), CAST(:ancestors AS jsonb)
                            )
                        """),
                        {
                            "id": chunk_id,
                            "doc_id": doc_id,
                            "idx": batch_indices[j],
                            "text": record.get("text") or "",
                            "embedding": str(embedding),
                            "material_id": material_id,
                            "page_start": record.get("page_start"),
                            "page_end": record.get("page_end"),
                            "smiles_dsl": smiles_dsl,
                            "variables": json.dumps(variables, ensure_ascii=False) if variables is not None else None,
                            "ancestors": json.dumps(ancestors, ensure_ascii=False) if ancestors is not None else None,
                        },
                    )
                session.commit()
                embedded_count += len(batch)
                progress += len(batch)
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

        logger.info(
            "Embedded %d / %d chunks for material %s",
            embedded_count, total, material_id,
        )
        return embedded_count
    except Exception as exc:
        logger.exception(
            "Embedding failed for material %s after saving %d / %d chunks: %s",
            material_id, embedded_count, total, exc,
        )
        raise  # 呼び出し元の process_material_background に伝搬させる


def process_material_background(
    material_id: str,
    doc_id: str,
    filename: str,
    pdf_bytes: bytes,
    task_id: str | None = None,
    cartridge_id: str | None = None,
    source_kind: str = "pdf",
) -> None:
    """バックグラウンドで新Agent Pipelineを実行する (issue #226)。

    Stages:
        save_pdf → document_structure → source_chunking → source_embedding
        → paper_skeleton → rhetorical_role → claim_qualification
        → equation_semantics → thesis_reconstruction → dsl_linking
        → dsl_embedding → component_assembly
        → persist_claims_components_graph → completed

    各ステージ完了時に background_tasks.result_data["stage"] を更新する。
    旧 build_knowledge_graph / chunk_pdf_pages ベースの導線は廃止。
    """
    from core.document_pipeline import PipelineStageError, run_document_pipeline

    with _material_lock:
        _material_status[material_id] = {
            "status": "processing",
            "filename": filename,
        }

    if task_id:
        update_background_task(task_id, "processing", result_data={"stage": "started"})

    # 元ソースを MinIO に保存（pipeline は一時ファイルに書き出すが正本は MinIO）
    try:
        if source_kind == "tex_archive":
            suffix = ".tgz" if filename.lower().endswith(".tgz") else ".tar.gz"
            _get_storage().upload_bytes(
                "raw-papers",
                f"uploads/{material_id}{suffix}",
                pdf_bytes,
                content_type="application/gzip",
            )
        else:
            _get_storage().upload_pdf("raw-papers", f"uploads/{material_id}.pdf", pdf_bytes)
        logger.info("Source saved to MinIO for material=%s kind=%s", material_id, source_kind)
    except Exception as _storage_exc:
        logger.warning(
            "Failed to save source to MinIO for material=%s: %s", material_id, _storage_exc,
        )

    def _on_stage(stage: str, info: dict) -> None:
        if task_id:
            payload = {"stage": stage}
            payload.update(info or {})
            update_background_task(task_id, "processing", result_data=payload)

    try:
        result = run_document_pipeline(
            pdf_bytes=pdf_bytes,
            document_id=doc_id,
            material_id=material_id,
            filename=filename,
            source_kind=source_kind,
            cartridge_id=cartridge_id,
            progress_callback=_on_stage,
        )

        # documents テーブルの最終ステータスを更新
        session = _pg_session()
        try:
            session.execute(
                sa_text("""
                    UPDATE documents
                    SET status = 'completed',
                        chunk_count = :chunk_count,
                        updated_at = now()
                    WHERE id = CAST(:doc_id AS uuid)
                """),
                {"doc_id": doc_id, "chunk_count": result.chunk_count},
            )
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

        with _material_lock:
            _material_status[material_id]["status"] = "completed"

        if task_id:
            update_background_task(task_id, "completed", result_data={
                "material_id": material_id,
                "doc_id": doc_id,
                "chunk_count": result.chunk_count,
                "claim_count": result.claim_count,
                "component_count": result.component_count,
                "dsl_node_count": result.dsl_node_count,
                "dsl_edge_count": result.dsl_edge_count,
                "stage": "completed",
            })

        logger.info(
            "Material processing completed: material=%s doc=%s filename=%s "
            "chunks=%d claims=%d components=%d",
            material_id, doc_id, filename,
            result.chunk_count, result.claim_count, result.component_count,
        )

    except Exception as exc:
        stage = getattr(exc, "stage", "unknown") if isinstance(exc, PipelineStageError) else "unknown"
        logger.exception(
            "Material processing FAILED at stage=%s: material=%s doc=%s filename=%s: %s",
            stage, material_id, doc_id, filename, exc,
        )
        with _material_lock:
            _material_status[material_id]["status"] = "failed"

        error_msg = f"[{stage}] {exc}" if stage != "unknown" else str(exc)
        if task_id:
            update_background_task(
                task_id,
                "failed",
                error_message=error_msg,
                result_data={"stage": stage},
            )

        # documents.status を 'failed' に更新する（ここでの失敗も明示的にログする）
        try:
            session = _pg_session()
            try:
                session.execute(
                    sa_text(
                        "UPDATE documents SET status = 'failed', updated_at = now() "
                        "WHERE id = CAST(:doc_id AS uuid)"
                    ),
                    {"doc_id": doc_id},
                )
                session.commit()
            except Exception:
                session.rollback()
            finally:
                session.close()
        except Exception as db_exc:
            # documents.status 更新まで失敗した場合はDBが不整合になるため critical で記録
            logger.critical(
                "CRITICAL: documents.status を 'failed' に更新できませんでした。"
                "DBが不整合状態の可能性があります。"
                " doc_id=%s material_id=%s error=%s",
                doc_id, material_id, db_exc,
            )


def reanalyze_course_structure_background(
    course_id: str,
    course_data: dict,
    task_id: str,
) -> None:
    """既存チャンクを維持したまま、コース教材の構造メタデータだけ再解析する。"""
    sources = course_data.get("sources", []) if isinstance(course_data, dict) else []
    material_ids = [
        str(s.get("material_id")).strip()
        for s in sources
        if isinstance(s, dict) and s.get("material_id")
    ]
    material_ids = list(dict.fromkeys(material_ids))

    total = len(material_ids)
    result_data = {
        "course_id": course_id,
        "total_materials": total,
        "processed_materials": 0,
        "updated_chunks": 0,
        "errors": 0,
        "progress": 0,
        "stage": "started",
    }
    update_background_task(task_id, "processing", result_data=result_data)

    if not material_ids:
        result_data["stage"] = "completed"
        update_background_task(task_id, "completed", result_data=result_data)
        return

    processed = 0
    updated_chunks = 0
    errors: list[str] = []

    for material_id in material_ids:
        try:
            session = _pg_session()
            try:
                row = session.execute(
                    sa_text("""
                        SELECT d.id, COALESCE(d.title, d.filename, d.source_path, '') AS title,
                               string_agg(c.text, E'\n\n' ORDER BY c.chunk_index) AS full_text,
                               COUNT(c.id) AS chunk_count
                        FROM documents d
                        LEFT JOIN chunks c ON c.document_id = d.id
                        WHERE d.source_path = :material_id
                        GROUP BY d.id, d.title, d.filename, d.source_path
                        LIMIT 1
                    """),
                    {"material_id": material_id},
                ).fetchone()
            finally:
                session.close()

            if not row:
                raise RuntimeError(f"material not found: {material_id}")

            doc_id = str(row[0])
            title = row[1] or material_id
            full_text = row[2] or ""
            chunk_count = int(row[3] or 0)
            if not full_text.strip() or chunk_count == 0:
                raise RuntimeError(f"no chunks to analyze: {material_id}")

            result_data.update({
                "stage": "building_graph",
                "current_material_id": material_id,
            })
            update_background_task(task_id, "processing", result_data=result_data)

            knowledge_graph = build_knowledge_graph(full_text, title)
            smiles_dsl, variables, ancestors = _extract_chunk_structure_metadata(knowledge_graph)
            if not smiles_dsl:
                # LLM が DSL を生成できなかった場合は題名から最小限のプレースホルダーを生成する
                smiles_dsl = f"(doc:Resource:{_safe_dsl_value(title)})"
                logger.warning(
                    "smiles_dsl not generated for material %s, using placeholder", material_id
                )

            session = _pg_session()
            try:
                session.execute(
                    sa_text("""
                        UPDATE documents
                        SET knowledge_graph = CAST(:kg AS jsonb),
                            text_length = :text_length,
                            updated_at = now()
                        WHERE id = CAST(:doc_id AS uuid)
                    """),
                    {
                        "doc_id": doc_id,
                        "kg": json.dumps(knowledge_graph, ensure_ascii=False),
                        "text_length": len(full_text),
                    },
                )
                update_result = session.execute(
                    sa_text("""
                        UPDATE chunks
                        SET smiles_dsl = :smiles_dsl,
                            variables = CAST(:variables AS jsonb),
                            ancestors = CAST(:ancestors AS jsonb)
                        WHERE document_id = CAST(:doc_id AS uuid)
                    """),
                    {
                        "doc_id": doc_id,
                        "smiles_dsl": smiles_dsl,
                        "variables": json.dumps(variables, ensure_ascii=False) if variables is not None else None,
                        "ancestors": json.dumps(ancestors, ensure_ascii=False) if ancestors is not None else None,
                    },
                )
                session.commit()
                updated_chunks += int(update_result.rowcount or 0) if smiles_dsl else 0
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

            processed += 1
        except Exception as exc:
            errors.append(f"{material_id}: {exc}")
            logger.warning("Structure reanalysis failed for material %s: %s", material_id, exc, exc_info=True)
        finally:
            done = processed + len(errors)
            result_data.update({
                "processed_materials": processed,
                "updated_chunks": updated_chunks,
                "errors": len(errors),
                "progress": int((done / total) * 100) if total else 100,
            })
            update_background_task(task_id, "processing", result_data=result_data)

    result_data.update({
        "stage": "completed",
        "processed_materials": processed,
        "updated_chunks": updated_chunks,
        "errors": len(errors),
        "progress": 100,
    })
    status = "completed" if not errors else "failed"
    update_background_task(
        task_id,
        status,
        result_data=result_data,
        error_message="; ".join(errors[:10]) if errors else None,
    )


# ---------------------------------------------------------------------------
# Course Builder session helpers
# ---------------------------------------------------------------------------


def save_cb_session(
    user_id: str,
    session_id: str,
    history: list[dict],
    course_draft: dict | None,
) -> None:
    """コース構築セッションの履歴と draft を PostgreSQL に保存する。"""
    try:
        session = _pg_session()
        try:
            session.execute(
                sa_text("""
                    UPDATE course_builder_sessions
                    SET history = CAST(:history AS jsonb),
                        course_draft = CAST(:course_draft AS jsonb),
                        updated_at = now()
                    WHERE id = :session_id AND user_id = CAST(:user_id AS uuid)
                """),
                {
                    "session_id": session_id,
                    "user_id": user_id,
                    "history": json.dumps(history, ensure_ascii=False),
                    "course_draft": (
                        json.dumps(course_draft, ensure_ascii=False)
                        if course_draft is not None else None
                    ),
                },
            )
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
    except Exception:
        logger.exception("Failed to save course builder session %s", session_id)


# ---------------------------------------------------------------------------
# Missing Link Suggestion（分野横断パターン検索クエリ生成）
# ---------------------------------------------------------------------------


def generate_missing_link_suggestions(
    pattern_name: str,
    pattern_description: str,
    structural_rules: list[str],
    variables_template: list[str],
    existing_fields: list[str] | None = None,
) -> dict:
    """パターンメタデータを受け取り、構造的空白を検知して分野横断の検索クエリを生成する。

    Returns a dict matching the MissingLinkSuggestion schema (without pattern_id).
    """
    rules_text = "\n".join(f"  - {r}" for r in structural_rules) if structural_rules else "  (none)"
    vars_text = ", ".join(variables_template) if variables_template else "(none)"
    existing_text = ", ".join(existing_fields) if existing_fields else "none known"

    prompt = f"""You are a cross-domain research advisor for the Episteme Graph system.

Given the following abstraction pattern, suggest academic fields where this structural pattern
likely occurs but is NOT yet represented in our pattern library.

## Pattern Information
- **Name**: {pattern_name}
- **Description**: {pattern_description}
- **Abstract Variables**: {vars_text}
- **Structural Rules**:
{rules_text}
- **Fields already covered**: {existing_text}

## Your Task
1. Identify 3-5 academic fields/domains where this same structural pattern likely manifests,
   but which are NOT in the "already covered" list.
2. For each field, explain WHY this pattern would appear there (concrete reasoning, not generic).
3. For each field, provide 2-4 arXiv search keywords that combine the pattern's structural
   concepts with field-specific terminology. Keywords should be specific enough to find relevant
   papers, mixing both generic structural terms and specialized domain terms.

## Output Format (strict JSON)
Return ONLY a JSON object with this structure:
{{
  "suggestions": [
    {{
      "field": "<academic field name>",
      "reasoning": "<1-2 sentences explaining why this pattern appears in this field>",
      "keywords": ["<keyword1>", "<keyword2>", "<keyword3>"]
    }}
  ]
}}

Important:
- Do NOT include fields already covered.
- Keywords must be suitable for arXiv search (English, technical terms).
- Balance generic structural terms with field-specific jargon to mitigate hallucination."""

    raw = generate_text(
        messages=[{"role": "user", "content": prompt}],
    )

    # Strip markdown code fences if present
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        cleaned = "\n".join(lines)

    return json.loads(cleaned)
