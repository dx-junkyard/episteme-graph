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
                           1 - (c.embedding::halfvec({dim}) <=> CAST(:query_vector AS halfvec({dim}))) AS score
                    FROM chunks c
                    LEFT JOIN documents d ON c.document_id = d.id
                    WHERE c.embedding IS NOT NULL
                    ORDER BY c.embedding::halfvec({dim}) <=> CAST(:query_vector AS halfvec({dim}))
                    LIMIT :limit
                """),
                {"query_vector": str(query_vector), "limit": top_k},
            ).fetchall()
            return [
                {
                    "id": str(row[0]),
                    "text": row[1],
                    "source_title": row[2] or row[3] or "不明な教材",
                    "source_file": row[3],
                    "score": float(row[4]),
                }
                for row in rows
                if row[1]
            ]
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


def _derive_graph_mentions(text: str, knowledge_graph: object, formulas: list[dict]) -> list[dict]:
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

    mentions = _derive_graph_mentions(text, knowledge_graph, formulas)
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
                           c.chapter, c.section, c.material_id, d.knowledge_graph
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
                        session, chunk_id, material_id, text, row[8], formulas,
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
) -> str | None:
    """コースデータの prerequisites フィールドを基に前提知識を確認し、未習得なら逆質問を返す。"""
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
        return (
            f"「{topic_title}」を理解するには、まず以下の前提知識を押さえる必要があります：\n\n"
            f"**{prereq_list}**\n\n"
            f"この前提知識を理解していますか？\n"
            f"理解している場合は、その前提で「{topic_title}」の説明に進みます。"
            f"理解できていない場合は、まず「{first_prereq}」から説明します。\n\n"
            f"[ACTION_BUTTON: はい、理解しています]\n"
            f"[ACTION_BUTTON: いいえ、理解できていないので{first_prereq}について教えてください]"
        )
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
) -> None:
    """チャット履歴を PostgreSQL に永続化する。"""
    updated_history = history + [
        {"role": "user", "content": user_message},
        {"role": "assistant", "content": assistant_answer},
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
                       d.knowledge_graph, d.uploaded_by
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
        graph_description = ""
        resolved_label = element_label or element_id
        target_formula = None

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


def chunk_text(text: str, chunk_size: int = 1000, overlap: int = 100) -> list[str]:
    """テキストをチャンクに分割する。"""
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]
        if chunk.strip():
            chunks.append(chunk.strip())
        start = end - overlap
    return chunks


def chunk_pdf_pages(pages: list[dict], chunk_size: int = 1000, overlap: int = 100) -> list[dict]:
    """ページ単位テキストをチャンク化し、ページ範囲を保持する。"""
    full = ""
    spans: list[dict] = []
    for page in pages:
        page_text = str(page.get("text") or "")
        start = len(full)
        full += page_text + "\n"
        spans.append({"page": page.get("page"), "start": start, "end": len(full)})

    chunks: list[dict] = []
    start = 0
    while start < len(full):
        end = start + chunk_size
        chunk = full[start:end].strip()
        if chunk:
            touched = [
                span["page"]
                for span in spans
                if span["page"] and span["start"] < end and span["end"] > start
            ]
            chunks.append({
                "text": chunk,
                "page_start": min(touched) if touched else None,
                "page_end": max(touched) if touched else None,
            })
        start = end - overlap
    return chunks


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
    batch_size = 50
    records = [
        c if isinstance(c, dict) else {"text": c, "page_start": None, "page_end": None}
        for c in chunks
    ]
    smiles_dsl, variables, ancestors = _extract_chunk_structure_metadata(knowledge_graph)
    total = len(records)
    try:
        for i in range(0, total, batch_size):
            batch = records[i:i + batch_size]
            batch_texts = [str(item.get("text") or "") for item in batch]
            logger.info(
                "Embedding batch %d-%d / %d for material %s",
                i + 1, i + len(batch), total, material_id,
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
                            "idx": i + j,
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
) -> None:
    """バックグラウンドでPDF処理パイプラインを実行する。

    各ステージで background_tasks.result_data["stage"] を更新するため、
    失敗時にどのフェーズで止まったか GET /api/admin/tasks/{task_id} で確認できる。

    Stages: extracting → chunking → embedding → building_graph → finalizing
    """

    def _update_stage(stage: str) -> None:
        """現在の処理ステージを background_tasks に記録する。"""
        if task_id:
            update_background_task(task_id, "processing", result_data={"stage": stage})

    with _material_lock:
        _material_status[material_id] = {
            "status": "processing",
            "filename": filename,
        }

    if task_id:
        update_background_task(task_id, "processing", result_data={"stage": "started"})

    # PDF を MinIO に保存（upload_material が古いバージョンで未保存だった場合の補完）
    try:
        _get_storage().upload_pdf("raw-papers", f"uploads/{material_id}.pdf", pdf_bytes)
        logger.info("PDF saved to MinIO for material=%s", material_id)
    except Exception as _storage_exc:
        logger.warning("Failed to save PDF to MinIO for material=%s: %s", material_id, _storage_exc)

    embedded_count = 0

    try:
        # ── Stage 1: テキスト抽出 ──────────────────────────────────────────
        _update_stage("extracting")
        pages = extract_pdf_pages(pdf_bytes)
        extracted_text = "\n".join(str(p.get("text") or "") for p in pages).replace("\x00", "")
        logger.info(
            "Stage[extracting] completed: material=%s doc=%s chars=%d",
            material_id, doc_id, len(extracted_text),
        )

        # ── Stage 2: チャンク分割 ──────────────────────────────────────────
        _update_stage("chunking")
        chunks = chunk_pdf_pages(pages, chunk_size=1000, overlap=100)
        logger.info(
            "Stage[chunking] completed: material=%s doc=%s chunks=%d",
            material_id, doc_id, len(chunks),
        )

        if not chunks:
            logger.warning(
                "Stage[chunking] produced 0 chunks for material=%s doc=%s filename=%s. "
                "PDFが空か、テキスト抽出に失敗している可能性があります。",
                material_id, doc_id, filename,
            )

        # ── Stage 3: ナレッジグラフ構築 ───────────────────────────────────
        _update_stage("building_graph")
        title = os.path.splitext(filename)[0]
        knowledge_graph = build_knowledge_graph(extracted_text, title)
        logger.info(
            "Stage[building_graph] completed: material=%s concepts=%d",
            material_id, len(knowledge_graph.get("concepts", [])),
        )

        # ── Stage 4: Embedding & DB保存 ───────────────────────────────────
        if chunks:
            _update_stage("embedding")
            try:
                embedded_count = embed_chunks(material_id, doc_id, chunks, knowledge_graph)
            except Exception as embed_exc:
                # embed_chunks が失敗するとチャンクが1件も登録されない致命的エラー
                raise RuntimeError(
                    f"チャンクのEmbedding/DB保存に失敗しました "
                    f"(material={material_id}, doc={doc_id}, "
                    f"text_chunks={len(chunks)}): {embed_exc}"
                ) from embed_exc
            logger.info(
                "Stage[embedding] completed: material=%s doc=%s embedded=%d",
                material_id, doc_id, embedded_count,
            )

        # ── Stage 5: documents テーブル更新 ───────────────────────────────
        _update_stage("finalizing")
        session = _pg_session()
        try:
            session.execute(
                sa_text("""
                    UPDATE documents
                    SET status = 'completed',
                        knowledge_graph = CAST(:kg AS jsonb),
                        text_length = :text_length,
                        chunk_count = :chunk_count,
                        updated_at = now()
                    WHERE id = CAST(:doc_id AS uuid)
                """),
                {
                    "doc_id": doc_id,
                    "kg": json.dumps(knowledge_graph, ensure_ascii=False),
                    "text_length": len(extracted_text),
                    "chunk_count": embedded_count,  # 実際にDBに保存されたチャンク数
                },
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
                "text_length": len(extracted_text),
                "chunk_count": embedded_count,
                "stage": "completed",
            })

        logger.info(
            "Material processing completed: material=%s doc=%s filename=%s embedded_chunks=%d",
            material_id, doc_id, filename, embedded_count,
        )

    except Exception as exc:
        logger.exception(
            "Material processing FAILED: material=%s doc=%s filename=%s: %s",
            material_id, doc_id, filename, exc,
        )
        with _material_lock:
            _material_status[material_id]["status"] = "failed"

        error_msg = str(exc)
        if task_id:
            update_background_task(task_id, "failed", error_message=error_msg)

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
