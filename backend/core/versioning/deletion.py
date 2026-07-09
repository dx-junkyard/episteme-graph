"""削除予約・取消・物理 purge。

- schedule_deletion: 猶予付き削除予約（lifecycle='pending_deletion'）。期限は所有者指定（既定14日）。
- cancel_deletion: 予約取消。
- purge_object: 全ユーザーからの物理削除（冪等）。既存 delete_material/delete_course 相当に加え、
  現状消し残す document_analysis_runs / document スコープ theory_* の orphan gap も解消する。
  実削除は worker から呼ばれる（手動削除エンドポイントもこの経路に合流させる）。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text as sa_text

from core.postgres import get_session

from . import schema

logger = logging.getLogger(__name__)


def default_purge_after(days: int = schema.DEFAULT_GRACE_DAYS) -> datetime:
    """今から days 日後（UTC）を返す。"""
    return datetime.now(timezone.utc) + timedelta(days=max(0, int(days)))


def schedule_deletion(
    *,
    object_type: str,
    object_id: str,
    purge_after: datetime,
    scheduled_by: str | None,
    reason: str = "",
) -> dict:
    """削除を予約する（猶予表示のため lifecycle='pending_deletion'）。

    raises: VersioningError — purge_after が過去 / purged 済み。
    """
    if not schema.is_valid_object_type(object_type):
        raise schema.VersioningError(f"invalid object_type: {object_type}")
    now = datetime.now(timezone.utc)
    pa = purge_after if purge_after.tzinfo else purge_after.replace(tzinfo=timezone.utc)
    if pa <= now:
        raise schema.VersioningError("purge_after must be in the future")

    session = get_session()
    try:
        session.execute(
            sa_text("""
                INSERT INTO shared_version_state (object_type, object_id, lifecycle)
                VALUES (:ot, :oid, 'active')
                ON CONFLICT (object_type, object_id) DO NOTHING
            """),
            {"ot": object_type, "oid": object_id},
        )
        row = session.execute(
            sa_text("""
                SELECT lifecycle FROM shared_version_state
                WHERE object_type = :ot AND object_id = :oid FOR UPDATE
            """),
            {"ot": object_type, "oid": object_id},
        ).fetchone()
        if row and row[0] == schema.LIFECYCLE_PURGED:
            raise schema.PurgedError("object has been purged")
        session.execute(
            sa_text("""
                UPDATE shared_version_state
                SET lifecycle = 'pending_deletion',
                    delete_scheduled_at = now(),
                    delete_purge_after = :pa,
                    delete_scheduled_by = CAST(:by AS uuid),
                    delete_reason = :reason,
                    updated_at = now()
                WHERE object_type = :ot AND object_id = :oid
            """),
            {"pa": pa, "by": scheduled_by or None, "reason": reason or "",
             "ot": object_type, "oid": object_id},
        )
        session.commit()
    except schema.VersioningError:
        session.rollback()
        raise
    except Exception:
        session.rollback()
        logger.exception("schedule_deletion failed: %s %s", object_type, object_id)
        raise
    finally:
        session.close()
    from . import releases
    return releases.get_state(object_type, object_id) or {}


def cancel_deletion(*, object_type: str, object_id: str, cancelled_by: str | None) -> dict:
    """削除予約を取り消す（active へ戻す）。"""
    session = get_session()
    try:
        session.execute(
            sa_text("""
                UPDATE shared_version_state
                SET lifecycle = 'active',
                    delete_scheduled_at = NULL,
                    delete_purge_after = NULL,
                    delete_scheduled_by = NULL,
                    delete_reason = '',
                    updated_at = now()
                WHERE object_type = :ot AND object_id = :oid
                  AND lifecycle = 'pending_deletion'
            """),
            {"ot": object_type, "oid": object_id},
        )
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("cancel_deletion failed: %s %s", object_type, object_id)
        raise
    finally:
        session.close()
    from . import releases
    return releases.get_state(object_type, object_id) or {}


# ---------------------------------------------------------------------------
# 物理削除（冪等）
# ---------------------------------------------------------------------------

def _purge_course(session, course_id: str) -> None:
    session.execute(sa_text("DELETE FROM learning_chat_history WHERE course_id = :cid"), {"cid": course_id})
    # CASCADE で learning_states / course_group_permissions / course スコープ theory_* も削除される
    session.execute(sa_text("DELETE FROM learning_courses WHERE id = :cid"), {"cid": course_id})


def _purge_document(session, document_id: str) -> None:
    doc = session.execute(
        sa_text("SELECT source_path, uploaded_by::text FROM documents WHERE id = CAST(:id AS uuid)"),
        {"id": document_id},
    ).fetchone()
    source_path = (doc[0] if doc else "") or ""
    owner = doc[1] if doc else None
    id_forms = {"a": document_id, "b": source_path or document_id}

    # 1) この教材を source に含む所有者のコースを削除（delete_material と同スコープ）
    if owner and source_path:
        needle = json.dumps([{"material_id": source_path}])
        course_ids = session.execute(
            sa_text("""
                SELECT id FROM learning_courses
                WHERE user_id = CAST(:owner AS uuid)
                  AND data->'sources' @> CAST(:needle AS jsonb)
            """),
            {"owner": owner, "needle": needle},
        ).fetchall()
        for (cid,) in course_ids:
            session.execute(sa_text("DELETE FROM learning_chat_history WHERE course_id = :cid"), {"cid": cid})
            session.execute(sa_text("DELETE FROM learning_courses WHERE id = :cid"), {"cid": cid})

    # 2) document スコープの成果物（orphan gap 解消。document_id は UUID / material_id 両形）
    #    D層（migration 029-033）の challenges / epistemic_ledger は claim / component の id を
    #    target_id で参照するため、theory_* を消す前に対象 id を集める（FK が無く CASCADE されない）。
    target_rows = session.execute(
        sa_text("""
            SELECT id::text FROM theory_claims WHERE document_id IN (:a, :b)
            UNION
            SELECT id::text FROM theory_components WHERE document_id IN (:a, :b)
        """),
        id_forms,
    ).fetchall()
    target_ids = [r[0] for r in target_rows]

    for tbl in ("theory_claims", "theory_component_links", "theory_components", "theory_component_graphs"):
        session.execute(sa_text(f"DELETE FROM {tbl} WHERE document_id IN (:a, :b)"), id_forms)

    # D層 polymorphic 行（FK-less TEXT。削除済み document / claim / component を指す孤児を掃除する）。
    # verification_proposals は challenges に ON DELETE CASCADE なので challenges 削除で自動的に消える。
    session.execute(sa_text("DELETE FROM epistemic_ledger WHERE document_id IN (:a, :b)"), id_forms)
    session.execute(sa_text("DELETE FROM counterfactual_sessions WHERE document_id IN (:a, :b)"), id_forms)
    if target_ids:
        session.execute(sa_text("DELETE FROM challenges WHERE target_id = ANY(:ids)"), {"ids": target_ids})
        session.execute(sa_text("DELETE FROM epistemic_ledger WHERE target_id = ANY(:ids)"), {"ids": target_ids})

    session.execute(
        sa_text("DELETE FROM document_group_permissions WHERE document_id = CAST(:a AS uuid)"),
        {"a": document_id},
    )
    # 3) チャンク → ドキュメント → 解析 Run（documents.active_analysis_run_id FK があるため runs は最後）
    session.execute(sa_text("DELETE FROM chunks WHERE document_id = CAST(:a AS uuid)"), {"a": document_id})
    session.execute(sa_text("DELETE FROM documents WHERE id = CAST(:a AS uuid)"), {"a": document_id})
    session.execute(sa_text("DELETE FROM document_analysis_runs WHERE document_id IN (:a, :b)"), id_forms)


def _cleanup_version_tables(session, object_type: str, object_id: str) -> None:
    """版テーブルを FK 順（notifications/subscriptions → versions）に明示削除し、
    state を 'purged' 墓標として残す（同一 id の再発行で古いピンが復活しないように）。"""
    session.execute(
        sa_text("""
            INSERT INTO shared_version_state (object_type, object_id, lifecycle, active_release_id, updated_at)
            VALUES (:ot, :oid, 'purged', NULL, now())
            ON CONFLICT (object_type, object_id)
            DO UPDATE SET lifecycle = 'purged', active_release_id = NULL, updated_at = now()
        """),
        {"ot": object_type, "oid": object_id},
    )
    session.execute(
        sa_text("DELETE FROM share_notifications WHERE object_type = :ot AND object_id = :oid"),
        {"ot": object_type, "oid": object_id},
    )
    session.execute(
        sa_text("DELETE FROM shared_version_subscriptions WHERE object_type = :ot AND object_id = :oid"),
        {"ot": object_type, "oid": object_id},
    )
    session.execute(
        sa_text("DELETE FROM shared_versions WHERE object_type = :ot AND object_id = :oid"),
        {"ot": object_type, "oid": object_id},
    )


def purge_object(*, object_type: str, object_id: str) -> dict:
    """全ユーザーから物理削除する（冪等）。purged 済みなら no-op。

    版テーブル（notifications/subscriptions/versions）は FK 順に明示削除し、
    state は 'purged' 墓標として残す（同一 id の再発行で古いピンが復活しないように）。
    """
    session = get_session()
    try:
        state = session.execute(
            sa_text("""
                SELECT lifecycle FROM shared_version_state
                WHERE object_type = :ot AND object_id = :oid FOR UPDATE
            """),
            {"ot": object_type, "oid": object_id},
        ).fetchone()
        if state and state[0] == schema.LIFECYCLE_PURGED:
            session.rollback()
            return {"purged": False, "already_purged": True}

        if object_type == schema.OBJECT_TYPE_COURSE:
            _purge_course(session, object_id)
        else:
            _purge_document(session, object_id)

        _cleanup_version_tables(session, object_type, object_id)
        session.commit()
        return {"purged": True}
    except Exception:
        session.rollback()
        logger.exception("purge_object failed: %s %s", object_type, object_id)
        raise
    finally:
        session.close()


def teardown_versioning(
    object_type: str,
    object_id: str,
    *,
    extra_recipients: list[str] | None = None,
    actor: str | None = None,
) -> dict:
    """既存の即時削除エンドポイント（delete_material / delete_course）用の V層あと片付け。

    物理行は呼び出し側で既に削除済みの前提で、版テーブルを掃除して state を 'purged' 墓標にし、
    購読者（+ 事前収集した宛先）へ 'deleted' 通知を配信する（best-effort・冪等）。
    共有版を一度も使っていない（state 行が無い）オブジェクトでは何もしない。
    これにより手動削除でも幽霊ピン・陳腐化通知・active のままの state を残さない。
    """
    from . import audit, notifications, subscriptions

    recipients = list(extra_recipients or [])
    try:
        recipients += subscriptions.subscriber_ids(object_type, object_id)
    except Exception:  # noqa: BLE001 — 宛先収集の失敗は片付けを止めない
        logger.debug("teardown: subscriber collection skipped for %s %s", object_type, object_id, exc_info=True)

    session = get_session()
    try:
        state = session.execute(
            sa_text("""
                SELECT lifecycle FROM shared_version_state
                WHERE object_type = :ot AND object_id = :oid FOR UPDATE
            """),
            {"ot": object_type, "oid": object_id},
        ).fetchone()
        if not state:
            session.rollback()
            return {"purged": False, "no_versioning": True}
        if state[0] == schema.LIFECYCLE_PURGED:
            session.rollback()
            return {"purged": False, "already_purged": True}
        _cleanup_version_tables(session, object_type, object_id)
        session.commit()
    except Exception:  # noqa: BLE001 — 片付けの失敗は削除本体を無効化しない
        session.rollback()
        logger.exception("teardown_versioning failed: %s %s", object_type, object_id)
        return {"purged": False, "error": True}
    finally:
        session.close()

    if recipients:
        notifications.notify_users(
            recipients, object_type, object_id, schema.NOTIF_DELETED,
            payload={"reason": "deleted_by_owner"},
        )
    audit.record_event(
        schema.AUDIT_DELETION, object_id, schema.LIFECYCLE_ACTIVE, schema.LIFECYCLE_PURGED,
        actor, {"object_type": object_type, "trigger": "manual_delete"},
    )
    return {"purged": True}
