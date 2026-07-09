"""共有物のバージョン管理 API（V層, migration 037）。

admin ルータ配下（実パス /api/admin/shared/...）。既存の revisions.py と同型で mount する。
発行・削除予約は所有者のみ。閲覧・取り込みは共有先（viewer）。document は _resolve_document で
documents.id::text に正規化してから版キーに使う。
"""
from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel

import services
from dependencies import _require_teacher

from core.versioning import deletion, notifications, releases, resolver, subscriptions
from core.versioning import schema as vschema

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Versioning"])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class PublishRequest(BaseModel):
    note: str = ""


class DeletionScheduleRequest(BaseModel):
    grace_days: int | None = None
    purge_after: str | None = None   # ISO8601（優先）。無ければ grace_days（既定14日）
    reason: str = ""


class AdoptRequest(BaseModel):
    expected_pinned_release_id: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _uid(current_user: dict) -> str | None:
    return current_user.get("id") or current_user.get("user_id")


def _canonical(object_type: str, object_id: str) -> str:
    """object_type を検証し、document は documents.id::text に正規化して返す。"""
    if not vschema.is_valid_object_type(object_type):
        raise HTTPException(status_code=400, detail="invalid object_type")
    if object_type == vschema.OBJECT_TYPE_DOCUMENT:
        doc = services._resolve_document(object_id)
        if not doc:
            raise HTTPException(status_code=404, detail="document not found")
        return doc["id"]
    return object_id


def _require_owner(object_type: str, object_id: str, current_user: dict) -> str:
    """所有者のみ許可。非所有者は 404（既存 user_owns_* の慣習）。canonical id を返す。"""
    canonical = _canonical(object_type, object_id)
    uid = _uid(current_user)
    if object_type == vschema.OBJECT_TYPE_DOCUMENT:
        owns = services.user_owns_document(uid, canonical)
    else:
        owns = services.user_owns_course(uid, canonical)
    if not owns:
        raise HTTPException(status_code=404, detail="not found")
    return canonical


def _require_viewer(object_type: str, object_id: str, current_user: dict) -> str:
    """閲覧権限（所有者/editor/viewer）を要求。canonical id を返す。"""
    canonical = _canonical(object_type, object_id)
    uid = _uid(current_user)
    if object_type == vschema.OBJECT_TYPE_DOCUMENT:
        can = services.user_can_view_document(uid, canonical)
    else:
        can = services.user_can_view_course(uid, canonical)
    if not can:
        raise HTTPException(status_code=404, detail="not found")
    return canonical


def _map_versioning_error(exc: Exception) -> HTTPException:
    if isinstance(exc, vschema.PurgedError):
        return HTTPException(status_code=410, detail=str(exc))
    if isinstance(exc, vschema.PendingDeletionError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, vschema.AdoptConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, vschema.VersioningError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=500, detail="versioning error")


# ---------------------------------------------------------------------------
# Releases（発行・一覧・取得）
# ---------------------------------------------------------------------------

@router.post("/shared/{object_type}/{object_id}/releases", status_code=201)
def publish_release(
    object_type: str,
    object_id: str,
    body: PublishRequest = Body(default=PublishRequest()),
    current_user: dict = Depends(_require_teacher),
):
    """新しい共有版を発行する（所有者のみ）。共有先へ version_published 通知を配信する。"""
    canonical = _require_owner(object_type, object_id, current_user)
    uid = _uid(current_user)
    try:
        rel = releases.publish_release(
            object_type=object_type, object_id=canonical, note=body.note, published_by=uid,
        )
    except vschema.VersioningError as exc:
        raise _map_versioning_error(exc)
    notified = notifications.fan_out(
        object_type, canonical, vschema.NOTIF_VERSION_PUBLISHED,
        release_id=rel["id"],
        payload={"version_no": rel["version_no"], "note": body.note},
        exclude_user=uid,
    )
    services.record_review_event(
        vschema.AUDIT_RELEASE, canonical, "", str(rel["version_no"]), uid,
        {"object_type": object_type, "release_id": rel["id"], "note": body.note},
    )
    return {**rel, "notified": notified}


@router.get("/shared/{object_type}/{object_id}/releases")
def list_releases(
    object_type: str,
    object_id: str,
    current_user: dict = Depends(_require_teacher),
):
    """共有版の一覧（新しい順）。"""
    canonical = _require_viewer(object_type, object_id, current_user)
    return releases.list_releases(object_type, canonical)


@router.get("/shared/releases/{release_id}")
def get_release(
    release_id: str,
    current_user: dict = Depends(_require_teacher),
):
    """単一 Release（スナップショット含む）。閲覧権限を確認する。"""
    rel = releases.get_release(release_id)
    if not rel:
        raise HTTPException(status_code=404, detail="release not found")
    _require_viewer(rel["object_type"], rel["object_id"], current_user)
    return rel


@router.get("/shared/{object_type}/{object_id}/version-state")
def get_version_state(
    object_type: str,
    object_id: str,
    current_user: dict = Depends(_require_teacher),
):
    """オブジェクトの版状態 + 呼び出し元のバッジ（更新あり/削除予定）。"""
    canonical = _require_viewer(object_type, object_id, current_user)
    return resolver.view_badges(object_type, canonical, _uid(current_user))


# ---------------------------------------------------------------------------
# Deletion（猶予予約・取消）
# ---------------------------------------------------------------------------

@router.post("/shared/{object_type}/{object_id}/deletion")
def schedule_deletion(
    object_type: str,
    object_id: str,
    body: DeletionScheduleRequest = Body(default=DeletionScheduleRequest()),
    current_user: dict = Depends(_require_teacher),
):
    """削除を予約する（所有者のみ）。期限後にスイーパが全ユーザーから物理削除する。"""
    canonical = _require_owner(object_type, object_id, current_user)
    uid = _uid(current_user)
    if body.purge_after:
        try:
            purge_after = datetime.fromisoformat(body.purge_after.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=422, detail="invalid purge_after (ISO8601 required)")
    else:
        days = body.grace_days if body.grace_days is not None else vschema.DEFAULT_GRACE_DAYS
        purge_after = deletion.default_purge_after(days)
    try:
        state = deletion.schedule_deletion(
            object_type=object_type, object_id=canonical,
            purge_after=purge_after, scheduled_by=uid, reason=body.reason,
        )
    except vschema.VersioningError as exc:
        raise _map_versioning_error(exc)
    notifications.fan_out(
        object_type, canonical, vschema.NOTIF_DELETION_SCHEDULED,
        payload={"purge_after": state.get("delete_purge_after"), "reason": body.reason},
        exclude_user=uid,
    )
    services.record_review_event(
        vschema.AUDIT_DELETION, canonical, "active", "scheduled", uid,
        {"object_type": object_type, "purge_after": state.get("delete_purge_after"), "reason": body.reason},
    )
    return state


@router.delete("/shared/{object_type}/{object_id}/deletion")
def cancel_deletion(
    object_type: str,
    object_id: str,
    current_user: dict = Depends(_require_teacher),
):
    """削除予約を取り消す（所有者のみ）。"""
    canonical = _require_owner(object_type, object_id, current_user)
    uid = _uid(current_user)
    state = deletion.cancel_deletion(object_type=object_type, object_id=canonical, cancelled_by=uid)
    notifications.fan_out(
        object_type, canonical, vschema.NOTIF_DELETION_CANCELLED, exclude_user=uid,
    )
    services.record_review_event(
        vschema.AUDIT_DELETION, canonical, "pending_deletion", "cancelled", uid,
        {"object_type": object_type},
    )
    return state


# ---------------------------------------------------------------------------
# Subscriptions（ピン取り込み）
# ---------------------------------------------------------------------------

@router.post("/shared/{object_type}/{object_id}/subscription/adopt")
def adopt_release(
    object_type: str,
    object_id: str,
    body: AdoptRequest = Body(default=AdoptRequest()),
    current_user: dict = Depends(_require_teacher),
):
    """共有版の更新を取り込む（ピンを active へ前進）。stale expected は 409。"""
    canonical = _require_viewer(object_type, object_id, current_user)
    uid = _uid(current_user)
    try:
        return subscriptions.adopt_latest(
            object_type, canonical, uid,
            expected_pinned_release_id=body.expected_pinned_release_id,
        )
    except vschema.VersioningError as exc:
        raise _map_versioning_error(exc)


@router.get("/shared/subscription/me")
def my_subscriptions(current_user: dict = Depends(_require_teacher)):
    """本人のピン一覧（更新有無つき）。"""
    return subscriptions.list_subscriptions_for_user(_uid(current_user))


# ---------------------------------------------------------------------------
# Notifications（インボックス）
# ---------------------------------------------------------------------------

@router.get("/shared/notifications")
def list_notifications(
    unread_only: bool = Query(default=False),
    current_user: dict = Depends(_require_teacher),
):
    uid = _uid(current_user)
    return {
        "unread_count": notifications.unread_count(uid),
        "notifications": notifications.list_inbox(uid, unread_only=unread_only),
    }


@router.post("/shared/notifications/{notification_id}/read")
def read_notification(
    notification_id: str,
    current_user: dict = Depends(_require_teacher),
):
    ok = notifications.mark_read(notification_id, _uid(current_user))
    if not ok:
        raise HTTPException(status_code=404, detail="notification not found")
    return {"read": True}


@router.post("/shared/notifications/read-all")
def read_all_notifications(current_user: dict = Depends(_require_teacher)):
    n = notifications.mark_all_read(_uid(current_user))
    return {"read": n}
