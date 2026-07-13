"""消費者ピン。消費側は Release にピンし、同意（adopt）でピンを active へ進める。

- ensure_pin: 初回引用時などに現在の active でピンを作る（既存ピンは上書きしない）。
- adopt_latest: ピンを active_release_id へ前進（楽観ロック。stale expected は 409）。
- resolve_effective_release_id: ピン有→pinned / 無→active（未ピン viewer・学習者は active 追従）。
"""
from __future__ import annotations

import logging

from sqlalchemy import text as sa_text

from core.postgres import get_session

from . import audit, schema

logger = logging.getLogger(__name__)


def _active_release_id(session, object_type: str, object_id: str) -> tuple[str | None, str]:
    """(active_release_id, lifecycle) を返す。state 行が無ければ (None, 'active')。"""
    row = session.execute(
        sa_text("""
            SELECT active_release_id::text, lifecycle
            FROM shared_version_state
            WHERE object_type = :ot AND object_id = :oid
            LIMIT 1
        """),
        {"ot": object_type, "oid": object_id},
    ).fetchone()
    if not row:
        return None, schema.LIFECYCLE_ACTIVE
    return row[0], row[1]


def get_subscription(object_type: str, object_id: str, subscriber_id: str) -> dict | None:
    session = get_session()
    try:
        row = session.execute(
            sa_text("""
                SELECT id::text, pinned_release_id::text, status, created_at, updated_at
                FROM shared_version_subscriptions
                WHERE object_type = :ot AND object_id = :oid
                  AND subscriber_id = CAST(:uid AS uuid)
                LIMIT 1
            """),
            {"ot": object_type, "oid": object_id, "uid": subscriber_id},
        ).fetchone()
    finally:
        session.close()
    if not row:
        return None
    return {
        "id": row[0],
        "object_type": object_type,
        "object_id": object_id,
        "pinned_release_id": row[1],
        "status": row[2],
        "created_at": row[3].isoformat() if row[3] else "",
        "updated_at": row[4].isoformat() if row[4] else "",
    }


def list_subscriptions_for_user(subscriber_id: str) -> list[dict]:
    """本人の active なピン一覧 + 各オブジェクトの最新版/更新有無を返す。"""
    session = get_session()
    try:
        rows = session.execute(
            sa_text("""
                SELECT s.object_type, s.object_id, s.pinned_release_id::text,
                       st.active_release_id::text, st.latest_version_no, st.lifecycle,
                       st.delete_purge_after,
                       pv.version_no AS pinned_version_no
                FROM shared_version_subscriptions s
                LEFT JOIN shared_version_state st
                       ON st.object_type = s.object_type AND st.object_id = s.object_id
                LEFT JOIN shared_versions pv
                       ON pv.id = s.pinned_release_id
                WHERE s.subscriber_id = CAST(:uid AS uuid) AND s.status = 'active'
                ORDER BY s.updated_at DESC
            """),
            {"uid": subscriber_id},
        ).fetchall()
    finally:
        session.close()
    out = []
    for r in rows:
        pinned = r[2]
        active = r[3]
        out.append({
            "object_type": r[0],
            "object_id": r[1],
            "pinned_release_id": pinned,
            "active_release_id": active,
            "latest_version_no": int(r[4] or 0),
            "pinned_version_no": int(r[7]) if r[7] is not None else None,
            "lifecycle": r[5] or schema.LIFECYCLE_ACTIVE,
            "delete_purge_after": r[6].isoformat() if r[6] else None,
            "update_available": bool(active and pinned and active != pinned),
        })
    return out


def ensure_pin(object_type: str, object_id: str, subscriber_id: str, release_id: str | None = None) -> dict:
    """消費者ピンを用意する（無ければ作る。既存ピンは上書きしない）。

    release_id 未指定なら現在の active_release_id にピンする。初回引用時の auto-pin に使う。
    """
    session = get_session()
    try:
        target = release_id
        if target is None:
            target, _ = _active_release_id(session, object_type, object_id)
        session.execute(
            sa_text("""
                INSERT INTO shared_version_subscriptions
                    (object_type, object_id, subscriber_id, pinned_release_id, status)
                VALUES (:ot, :oid, CAST(:uid AS uuid), CAST(:rid AS uuid), 'active')
                ON CONFLICT (object_type, object_id, subscriber_id) DO NOTHING
            """),
            {"ot": object_type, "oid": object_id, "uid": subscriber_id, "rid": target},
        )
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("ensure_pin failed: %s %s %s", object_type, object_id, subscriber_id)
        raise
    finally:
        session.close()
    audit.record_event(
        schema.AUDIT_SUBSCRIPTION, object_id, "", str(release_id or ""),
        subscriber_id, {"object_type": object_type, "action": "auto_pin"},
    )
    return get_subscription(object_type, object_id, subscriber_id) or {}


def adopt_latest(
    object_type: str,
    object_id: str,
    subscriber_id: str,
    expected_pinned_release_id: str | None = None,
) -> dict:
    """ピンを active_release_id へ前進させる（同意して取り込む）。

    既存ピンがある場合は expected_pinned_release_id が現在値と一致しなければ 409（AdoptConflictError）。
    purge 済みは 410（PurgedError）。
    """
    session = get_session()
    try:
        target, lifecycle = _active_release_id(session, object_type, object_id)
        if lifecycle == schema.LIFECYCLE_PURGED:
            raise schema.PurgedError("object has been purged")
        if not target:
            raise schema.VersioningError("no active release to adopt")

        existing = session.execute(
            sa_text("""
                SELECT pinned_release_id::text FROM shared_version_subscriptions
                WHERE object_type = :ot AND object_id = :oid AND subscriber_id = CAST(:uid AS uuid)
                FOR UPDATE
            """),
            {"ot": object_type, "oid": object_id, "uid": subscriber_id},
        ).fetchone()

        if existing is None:
            # 未ピン（active 追従中）から明示ピンへ。expected は None のはず。
            if expected_pinned_release_id:
                raise schema.AdoptConflictError("no existing pin but expected a release")
            session.execute(
                sa_text("""
                    INSERT INTO shared_version_subscriptions
                        (object_type, object_id, subscriber_id, pinned_release_id, status)
                    VALUES (:ot, :oid, CAST(:uid AS uuid), CAST(:rid AS uuid), 'active')
                """),
                {"ot": object_type, "oid": object_id, "uid": subscriber_id, "rid": target},
            )
            old_pin = ""
        else:
            old_pin = existing[0] or ""
            result = session.execute(
                sa_text("""
                    UPDATE shared_version_subscriptions
                    SET pinned_release_id = CAST(:rid AS uuid),
                        status = 'active',
                        updated_at = now()
                    WHERE object_type = :ot AND object_id = :oid
                      AND subscriber_id = CAST(:uid AS uuid)
                      AND pinned_release_id IS NOT DISTINCT FROM CAST(:expected AS uuid)
                """),
                {
                    "rid": target, "ot": object_type, "oid": object_id,
                    "uid": subscriber_id, "expected": expected_pinned_release_id,
                },
            )
            if result.rowcount != 1:
                raise schema.AdoptConflictError(
                    "pinned_release_id changed since read; refresh and retry"
                )

        # 関連する未対応通知に acted_at を刻む（migration 045 で user_notifications に統合。
        # source='shared' でスコープし status 層の行を巻き込まない）
        session.execute(
            sa_text("""
                UPDATE user_notifications
                SET acted_at = now()
                WHERE recipient_id = CAST(:uid AS uuid)
                  AND source = 'shared'
                  AND entity_type = :ot AND entity_id = :oid
                  AND kind = 'version_published' AND acted_at IS NULL
            """),
            {"uid": subscriber_id, "ot": object_type, "oid": object_id},
        )
        session.commit()
    except schema.VersioningError:
        session.rollback()
        raise
    except Exception:
        session.rollback()
        logger.exception("adopt_latest failed: %s %s %s", object_type, object_id, subscriber_id)
        raise
    finally:
        session.close()

    audit.record_event(
        schema.AUDIT_SUBSCRIPTION, object_id, old_pin, target,
        subscriber_id, {"object_type": object_type, "action": "adopt"},
    )
    return get_subscription(object_type, object_id, subscriber_id) or {}


def subscriber_ids(object_type: str, object_id: str) -> list[str]:
    """当該オブジェクトに active なピンを持つ購読者の user id を返す（purge 前の通知宛先収集用）。"""
    session = get_session()
    try:
        rows = session.execute(
            sa_text("""
                SELECT subscriber_id::text FROM shared_version_subscriptions
                WHERE object_type = :ot AND object_id = :oid AND status = 'active'
            """),
            {"ot": object_type, "oid": object_id},
        ).fetchall()
        return [str(r[0]) for r in rows]
    finally:
        session.close()


def resolve_effective_release_id(object_type: str, object_id: str, viewer_id: str) -> str | None:
    """viewer にとって有効な Release id を返す（ピン有→pinned / 無→active）。"""
    session = get_session()
    try:
        pin = session.execute(
            sa_text("""
                SELECT pinned_release_id::text
                FROM shared_version_subscriptions
                WHERE object_type = :ot AND object_id = :oid
                  AND subscriber_id = CAST(:uid AS uuid) AND status = 'active'
                LIMIT 1
            """),
            {"ot": object_type, "oid": object_id, "uid": viewer_id},
        ).fetchone()
        if pin and pin[0]:
            return pin[0]
        active, _ = _active_release_id(session, object_type, object_id)
        return active
    finally:
        session.close()
