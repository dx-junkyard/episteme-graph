"""通知インボックス（user_notifications）の読み取り・既読・却下。

core/versioning/notifications.py と同型（migration 045 でテーブル自体も統合済み。
両モジュールとも同じ user_notifications を読み書きするため、本モジュールは
source='status' で必ずスコープする（付け忘れると V層（source='shared'）由来の行が
status 側の一覧・未読数・既読/却下にも二重に見えてしまう。統合の最重要注意点）。
行削除しない（S4）。
"""
from __future__ import annotations

import json
import logging

from sqlalchemy import text as sa_text

from core.postgres import get_session

logger = logging.getLogger(__name__)


def list_inbox(recipient_id: str, *, unread_only: bool = False, limit: int = 100) -> list[dict]:
    session = get_session()
    try:
        clause = "AND n.read_at IS NULL" if unread_only else ""
        rows = session.execute(
            sa_text(f"""
                SELECT n.id::text, n.kind, n.entity_type, n.entity_id, n.payload,
                       n.created_at, n.read_at, n.dismissed_at
                FROM user_notifications n
                WHERE n.recipient_id = CAST(:uid AS uuid)
                  AND n.source = 'status'
                  AND n.dismissed_at IS NULL
                  {clause}
                ORDER BY n.created_at DESC
                LIMIT :limit
            """),
            {"uid": recipient_id, "limit": limit},
        ).fetchall()
    finally:
        session.close()
    return [
        {
            "id": r[0],
            "source": "status",
            "kind": r[1],
            "entity_type": r[2],
            "entity_id": r[3],
            "payload": r[4] if isinstance(r[4], dict) else json.loads(r[4] or "{}"),
            "created_at": r[5].isoformat() if r[5] else "",
            "read_at": r[6].isoformat() if r[6] else None,
            "dismissed_at": r[7].isoformat() if r[7] else None,
        }
        for r in rows
    ]


def unread_count(recipient_id: str) -> int:
    session = get_session()
    try:
        return int(session.execute(
            sa_text("""
                SELECT count(*) FROM user_notifications
                WHERE recipient_id = CAST(:uid AS uuid) AND source = 'status'
                  AND read_at IS NULL AND dismissed_at IS NULL
            """),
            {"uid": recipient_id},
        ).scalar() or 0)
    finally:
        session.close()


def mark_read(notification_id: str, recipient_id: str) -> bool:
    session = get_session()
    try:
        result = session.execute(
            sa_text("""
                UPDATE user_notifications
                SET read_at = now()
                WHERE id = CAST(:nid AS uuid) AND recipient_id = CAST(:uid AS uuid)
                  AND source = 'status' AND read_at IS NULL
            """),
            {"nid": notification_id, "uid": recipient_id},
        )
        session.commit()
        return int(result.rowcount or 0) > 0
    except Exception:
        session.rollback()
        logger.exception("status inbox mark_read failed: %s", notification_id)
        return False
    finally:
        session.close()


def mark_all_read(recipient_id: str) -> int:
    session = get_session()
    try:
        result = session.execute(
            sa_text("""
                UPDATE user_notifications
                SET read_at = now()
                WHERE recipient_id = CAST(:uid AS uuid) AND source = 'status' AND read_at IS NULL
            """),
            {"uid": recipient_id},
        )
        session.commit()
        return int(result.rowcount or 0)
    except Exception:
        session.rollback()
        logger.exception("status inbox mark_all_read failed: %s", recipient_id)
        return 0
    finally:
        session.close()


def dismiss(notification_id: str, recipient_id: str) -> bool:
    """却下する（行削除しない・S4。既読と独立に管理する）。source='status' のみ対象。"""
    session = get_session()
    try:
        result = session.execute(
            sa_text("""
                UPDATE user_notifications
                SET dismissed_at = now()
                WHERE id = CAST(:nid AS uuid) AND recipient_id = CAST(:uid AS uuid)
                  AND source = 'status' AND dismissed_at IS NULL
            """),
            {"nid": notification_id, "uid": recipient_id},
        )
        session.commit()
        return int(result.rowcount or 0) > 0
    except Exception:
        session.rollback()
        logger.exception("status inbox dismiss failed: %s", notification_id)
        return False
    finally:
        session.close()
