"""読み取りの版解決。

- resolve_course_data: ピン viewer / 未ピン viewer・学習者 に、有効な Release の data を返す。
  Release が無い（未発行）コースは None を返し、呼び出し側は live（working copy）にフォールバックする。
  所有者・editor は HEAD を読むので呼び出し側でこの関数を経由しない（分類は Phase 3 の配線側）。
- resolve_document_run_id: document のピン run（成果物の凍結ブラウズ用の入口）。
- view_badges: UI バッジ（更新あり / 削除予定）用のオブジェクト状態。
"""
from __future__ import annotations

import logging

from . import releases, schema, subscriptions

logger = logging.getLogger(__name__)


def resolve_course_data(course_id: str, viewer_id: str) -> dict | None:
    """viewer にとって有効なコース data（Release スナップショット）を返す。

    Release 未発行なら None（呼び出し側が live にフォールバック）。
    """
    release_id = subscriptions.resolve_effective_release_id(
        schema.OBJECT_TYPE_COURSE, course_id, viewer_id
    )
    if not release_id:
        return None
    rel = releases.get_release(release_id)
    if not rel:
        return None
    snapshot = rel.get("snapshot") or {}
    data = snapshot.get("data")
    return data if isinstance(data, dict) else None


def resolve_document_run_id(document_id: str, viewer_id: str) -> str | None:
    """viewer にとって有効な document の analysis_run_id（Release がピンする run）。"""
    release_id = subscriptions.resolve_effective_release_id(
        schema.OBJECT_TYPE_DOCUMENT, document_id, viewer_id
    )
    if not release_id:
        return None
    rel = releases.get_release(release_id)
    if not rel:
        return None
    return (rel.get("snapshot") or {}).get("analysis_run_id") or None


def view_badges(object_type: str, object_id: str, viewer_id: str) -> dict:
    """UI バッジ用の状態を返す。

    - update_available: 有効ピンが最新版より前（＝取り込み可能）
    - lifecycle / delete_purge_after: 削除予定バナー用
    - pinned_version_no / latest_version_no
    """
    state = releases.get_state(object_type, object_id)
    if not state:
        return {"has_versioning": False}

    effective = subscriptions.resolve_effective_release_id(object_type, object_id, viewer_id)
    active = state.get("active_release_id")
    sub = subscriptions.get_subscription(object_type, object_id, viewer_id)
    pinned_release_id = sub.get("pinned_release_id") if sub else None

    pinned_version_no = None
    if pinned_release_id:
        rel = releases.get_release(pinned_release_id)
        pinned_version_no = rel.get("version_no") if rel else None

    return {
        "has_versioning": True,
        "lifecycle": state.get("lifecycle"),
        "delete_purge_after": state.get("delete_purge_after"),
        "delete_reason": state.get("delete_reason", ""),
        "latest_version_no": state.get("latest_version_no", 0),
        "active_release_id": active,
        "pinned_release_id": pinned_release_id,
        "pinned_version_no": pinned_version_no,
        "effective_release_id": effective,
        # ピンがあり、それが active より前なら取り込み可能
        "update_available": bool(pinned_release_id and active and pinned_release_id != active),
    }
