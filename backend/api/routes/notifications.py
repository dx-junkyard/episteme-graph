"""統合通知インボックス API（migration 038 + 045）。

admin ルータ配下（実パス /api/admin/notifications...）。通知の実体は
user_notifications 1テーブルに統合済み（migration 045、source='status'|'shared' で
由来を区別）。read/mark 系のみを扱う（INSERT/DELETE は行わない。書き込みは
core/status/notification_rules.py・core/versioning/notifications.py 側の責務）。

- list_notifications / unread_count: source を問わず単一クエリで両層をまとめて返す
  （旧実装の「2テーブルをアプリ層でマージ」は不要になった）。
- read_notification / read_all_notifications: read 系は source 不問の単一 UPDATE
  （旧実装の「どちらのテーブルか分からないので両方試す」二重試行と同じ最終効果）。
- dismiss_notification: 現行どおり status 層（source='status'）のみ対象。V層通知への
  dismiss 拡張はやらない（挙動保存。core/status/inbox.dismiss が source='status' で
  スコープしているため、shared 由来の id を渡しても 404 になる）。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text as sa_text

from dependencies import _require_teacher

from core.postgres import get_session
from core.status import inbox as status_inbox

router = APIRouter(prefix="/notifications", tags=["Notifications"])


def _uid(current_user: dict) -> str:
    return current_user.get("id") or current_user.get("user_id")


@router.get("")
def list_notifications(
    unread_only: bool = Query(default=False),
    current_user: dict = Depends(_require_teacher),
):
    uid = _uid(current_user)
    session = get_session()
    try:
        clause = "AND read_at IS NULL" if unread_only else ""
        rows = session.execute(
            sa_text(f"""
                SELECT id::text, source, kind, entity_type, entity_id, payload,
                       created_at, read_at, dismissed_at
                FROM user_notifications
                WHERE recipient_id = CAST(:uid AS uuid) AND dismissed_at IS NULL {clause}
                ORDER BY created_at DESC
                LIMIT 100
            """),
            {"uid": uid},
        ).fetchall()
        unread = int(session.execute(
            sa_text("""
                SELECT count(*) FROM user_notifications
                WHERE recipient_id = CAST(:uid AS uuid) AND read_at IS NULL AND dismissed_at IS NULL
            """),
            {"uid": uid},
        ).scalar() or 0)
    finally:
        session.close()
    notifications = [
        {
            "id": r[0],
            "source": r[1],
            "kind": r[2],
            "entity_type": r[3],
            "entity_id": r[4],
            "payload": r[5] if isinstance(r[5], dict) else json.loads(r[5] or "{}"),
            "created_at": r[6].isoformat() if r[6] else "",
            "read_at": r[7].isoformat() if r[7] else None,
            "dismissed_at": r[8].isoformat() if r[8] else None,
        }
        for r in rows
    ]
    return {"unread_count": unread, "notifications": notifications}


@router.post("/{notification_id}/read")
def read_notification(notification_id: str, current_user: dict = Depends(_require_teacher)):
    """既読にする（source 不問の単一 UPDATE。status/shared どちらの行にも効く）。"""
    uid = _uid(current_user)
    session = get_session()
    try:
        result = session.execute(
            sa_text("""
                UPDATE user_notifications
                SET read_at = now()
                WHERE id = CAST(:nid AS uuid) AND recipient_id = CAST(:uid AS uuid) AND read_at IS NULL
            """),
            {"nid": notification_id, "uid": uid},
        )
        ok = int(result.rowcount or 0) > 0
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    if not ok:
        raise HTTPException(status_code=404, detail="notification not found")
    return {"read": True}


@router.post("/read-all")
def read_all_notifications(current_user: dict = Depends(_require_teacher)):
    """全件既読（source 不問の単一 UPDATE）。"""
    uid = _uid(current_user)
    session = get_session()
    try:
        result = session.execute(
            sa_text("""
                UPDATE user_notifications
                SET read_at = now()
                WHERE recipient_id = CAST(:uid AS uuid) AND read_at IS NULL
            """),
            {"uid": uid},
        )
        n = int(result.rowcount or 0)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    return {"read": n}


@router.post("/{notification_id}/dismiss")
def dismiss_notification(notification_id: str, current_user: dict = Depends(_require_teacher)):
    """却下（S4: 行削除しない）。v1 は本層（source='status'）通知のみ対象。"""
    uid = _uid(current_user)
    if status_inbox.dismiss(notification_id, uid):
        return {"dismissed": True}
    raise HTTPException(status_code=404, detail="notification not found")
