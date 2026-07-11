"""統合通知インボックス API（migration 038）。

admin ルータ配下（実パス /api/admin/notifications...）。user_notifications（本層）と
share_notifications（V層, migration 037）を読み取りで併合して返す。V層のテーブル・
ロジックは変更しない（読み取りのみ）。既読/却下対象がどちらのテーブルの行かは
呼び出し側から分からないため、id が一致する方を探して処理する。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from dependencies import _require_teacher

from core.status import inbox as status_inbox
from core.versioning import notifications as shared_notifications

router = APIRouter(prefix="/notifications", tags=["Notifications"])


def _uid(current_user: dict) -> str:
    return current_user.get("id") or current_user.get("user_id")


def _to_common(n: dict) -> dict:
    """share_notifications の行を統合レスポンス形式へ合わせる（source を明示）。"""
    if "source" in n:
        return n
    return {
        "id": n["id"],
        "source": "shared",
        "kind": n["kind"],
        "entity_type": n.get("object_type", ""),
        "entity_id": n.get("object_id", ""),
        "payload": n.get("payload", {}),
        "created_at": n.get("created_at", ""),
        "read_at": n.get("read_at"),
        "dismissed_at": None,
    }


@router.get("")
def list_notifications(
    unread_only: bool = Query(default=False),
    current_user: dict = Depends(_require_teacher),
):
    uid = _uid(current_user)
    status_items = status_inbox.list_inbox(uid, unread_only=unread_only)
    shared_items = [_to_common(n) for n in shared_notifications.list_inbox(uid, unread_only=unread_only)]
    merged = sorted(status_items + shared_items, key=lambda n: n["created_at"], reverse=True)
    unread_count = status_inbox.unread_count(uid) + shared_notifications.unread_count(uid)
    return {"unread_count": unread_count, "notifications": merged}


@router.post("/{notification_id}/read")
def read_notification(notification_id: str, current_user: dict = Depends(_require_teacher)):
    uid = _uid(current_user)
    if status_inbox.mark_read(notification_id, uid):
        return {"read": True}
    if shared_notifications.mark_read(notification_id, uid):
        return {"read": True}
    raise HTTPException(status_code=404, detail="notification not found")


@router.post("/read-all")
def read_all_notifications(current_user: dict = Depends(_require_teacher)):
    uid = _uid(current_user)
    n = status_inbox.mark_all_read(uid) + shared_notifications.mark_all_read(uid)
    return {"read": n}


@router.post("/{notification_id}/dismiss")
def dismiss_notification(notification_id: str, current_user: dict = Depends(_require_teacher)):
    """却下（S4: 行削除しない）。v1 は本層（user_notifications）通知のみ対象。"""
    uid = _uid(current_user)
    if status_inbox.dismiss(notification_id, uid):
        return {"dismissed": True}
    raise HTTPException(status_code=404, detail="notification not found")
