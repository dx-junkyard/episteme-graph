"""削除猶予の定期スイーパ。

期限（delete_purge_after）が到来した pending_deletion オブジェクトを物理 purge し、
共有先へ 'deleted' 通知を配信する。冪等（purge 済みは no-op）・1件失敗は skip。

同期パスには入れない。起動時に daemon スレッドで回す（VERSION_SWEEPER_ENABLED で制御）。
"""
from __future__ import annotations

import logging
import os
import threading
import time

from sqlalchemy import text as sa_text

from core.postgres import get_session

from . import audit, deletion, notifications, schema, subscriptions

logger = logging.getLogger(__name__)

_started = False
_lock = threading.Lock()


def _due_objects() -> list[tuple[str, str, str | None]]:
    """期限到来済みの (object_type, object_id, scheduled_by) を返す。"""
    session = get_session()
    try:
        rows = session.execute(
            sa_text("""
                SELECT object_type, object_id, delete_scheduled_by::text
                FROM shared_version_state
                WHERE lifecycle = 'pending_deletion'
                  AND delete_purge_after IS NOT NULL
                  AND delete_purge_after <= now()
                ORDER BY delete_purge_after
            """),
        ).fetchall()
        return [(r[0], r[1], r[2]) for r in rows]
    finally:
        session.close()


def _purge_one(object_type: str, object_id: str, scheduled_by: str | None) -> bool:
    """1オブジェクトを purge し 'deleted' を配信する。purge した場合 True。"""
    # 権限が消える前に宛先を収集する（purge 後は group 権限が消えて fan_out が空になる）
    recipients = notifications.recipients_for(object_type, object_id)
    recipients += subscriptions.subscriber_ids(object_type, object_id)

    result = deletion.purge_object(object_type=object_type, object_id=object_id)
    if not result.get("purged"):
        return False

    notifications.notify_users(
        recipients, object_type, object_id, schema.NOTIF_DELETED,
        payload={"reason": "grace_period_expired"},
    )
    audit.record_event(
        schema.AUDIT_DELETION, object_id, schema.LIFECYCLE_PENDING_DELETION, schema.LIFECYCLE_PURGED,
        scheduled_by, {"object_type": object_type, "trigger": "sweeper"},
    )
    logger.info("purged %s %s (grace period expired), notified %d recipients",
                object_type, object_id, len(set(recipients)))
    return True


def sweep_once() -> int:
    """期限到来済みを一巡 purge する。戻り値は purge した件数。"""
    purged = 0
    for object_type, object_id, scheduled_by in _due_objects():
        try:
            if _purge_one(object_type, object_id, scheduled_by):
                purged += 1
        except Exception:  # noqa: BLE001 — 1件の失敗が全体を止めないように
            logger.exception("sweep: purge failed for %s %s", object_type, object_id)
    return purged


def run_forever(interval_seconds: int = 3600) -> None:
    while True:
        try:
            sweep_once()
        except Exception:  # noqa: BLE001
            logger.exception("versioning sweeper iteration failed")
        time.sleep(max(60, interval_seconds))


def start_background_sweeper() -> None:
    """起動時に1度だけ daemon スイーパを開始する（VERSION_SWEEPER_ENABLED で無効化可）。"""
    global _started
    if os.getenv("VERSION_SWEEPER_ENABLED", "1") not in ("1", "true", "True", "yes"):
        logger.info("versioning sweeper disabled by VERSION_SWEEPER_ENABLED")
        return
    with _lock:
        if _started:
            return
        interval = int(os.getenv("VERSION_SWEEP_INTERVAL_SECONDS", "3600") or "3600")
        thread = threading.Thread(
            target=run_forever, kwargs={"interval_seconds": interval},
            name="versioning-sweeper", daemon=True,
        )
        thread.start()
        _started = True
        logger.info("versioning sweeper started (interval=%ds)", interval)
