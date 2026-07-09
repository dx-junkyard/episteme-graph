"""通知インボックス。発行・削除予約・取消・削除を共有先へ配信する。

宛先＝当該オブジェクトに viewer|editor を持つグループメンバー（所有者は exclude）。
join 形は services._has_document_group_permission / user_can_view_course と同型。
"""
from __future__ import annotations

import json
import logging

from sqlalchemy import text as sa_text

from core.postgres import get_session

from . import schema

logger = logging.getLogger(__name__)


def _recipient_ids(session, object_type: str, object_id: str) -> list[str]:
    """当該オブジェクトの共有先（通知を届けるべきユーザー id）を返す。fan_out / recipients_for の単一の真実源。

    - course: course_group_permissions の viewer|editor グループメンバー。
    - document: document_group_permissions（migration 035）の viewer|editor に加え、
      レガシー単一グループ共有（documents.visibility='group' + group_id）のメンバーも含める。
      これらは services.user_can_view_document で閲覧・pin/adopt が許可されるため、
      通知の宛先も揃える（更新・削除予約・削除の取りこぼしを防ぐ）。
    """
    if object_type == schema.OBJECT_TYPE_COURSE:
        rows = session.execute(
            sa_text("""
                SELECT DISTINCT gm.user_id::text
                FROM course_group_permissions p
                JOIN group_members gm ON gm.group_id = p.group_id
                WHERE p.course_id = :oid AND p.permission IN ('viewer','editor')
            """),
            {"oid": object_id},
        ).fetchall()
    else:
        rows = session.execute(
            sa_text("""
                SELECT DISTINCT uid FROM (
                    SELECT gm.user_id::text AS uid
                    FROM document_group_permissions p
                    JOIN group_members gm ON gm.group_id = p.group_id
                    WHERE p.document_id = CAST(:oid AS uuid) AND p.permission IN ('viewer','editor')
                    UNION
                    SELECT gm.user_id::text AS uid
                    FROM documents d
                    JOIN group_members gm ON gm.group_id = d.group_id
                    WHERE d.id = CAST(:oid AS uuid)
                      AND d.visibility = 'group' AND d.group_id IS NOT NULL
                ) t
            """),
            {"oid": object_id},
        ).fetchall()
    return [str(r[0]) for r in rows]


def fan_out(
    object_type: str,
    object_id: str,
    kind: str,
    *,
    release_id: str | None = None,
    payload: dict | None = None,
    exclude_user: str | None = None,
) -> int:
    """共有先（viewer|editor グループメンバー − 所有者）へ通知を配信する。戻り値は件数。"""
    if kind not in schema.NOTIFICATION_KINDS:
        raise schema.VersioningError(f"invalid notification kind: {kind}")
    session = get_session()
    try:
        ids = _recipient_ids(session, object_type, object_id)
    finally:
        session.close()
    if exclude_user:
        ids = [i for i in ids if i != str(exclude_user)]
    return notify_users(ids, object_type, object_id, kind, release_id=release_id, payload=payload)


def recipients_for(object_type: str, object_id: str) -> list[str]:
    """当該オブジェクトの共有先ユーザー id を返す（purge 前の通知宛先収集用）。"""
    session = get_session()
    try:
        return _recipient_ids(session, object_type, object_id)
    finally:
        session.close()


def notify_users(
    recipient_ids: list[str],
    object_type: str,
    object_id: str,
    kind: str,
    *,
    release_id: str | None = None,
    payload: dict | None = None,
) -> int:
    """明示的な宛先リストへ通知を配信する（purge 後に権限が消えても届けられる）。"""
    ids = [rid for rid in dict.fromkeys(recipient_ids) if rid]
    if not ids or kind not in schema.NOTIFICATION_KINDS:
        return 0
    session = get_session()
    try:
        payload_json = json.dumps(payload or {}, ensure_ascii=False)
        for rid in ids:
            session.execute(
                sa_text("""
                    INSERT INTO share_notifications
                        (recipient_id, object_type, object_id, kind, release_id, payload)
                    VALUES (CAST(:uid AS uuid), :ot, :oid, :kind,
                            CAST(:rid AS uuid), CAST(:payload AS jsonb))
                """),
                {"uid": rid, "ot": object_type, "oid": object_id, "kind": kind,
                 "rid": release_id, "payload": payload_json},
            )
        session.commit()
        return len(ids)
    except Exception:
        session.rollback()
        logger.exception("notify_users failed: %s %s %s", object_type, object_id, kind)
        return 0
    finally:
        session.close()


def list_inbox(recipient_id: str, *, unread_only: bool = False, limit: int = 100) -> list[dict]:
    """本人の通知一覧（新しい順）。release_id があれば version_no を添える。"""
    session = get_session()
    try:
        clause = "AND n.read_at IS NULL" if unread_only else ""
        rows = session.execute(
            sa_text(f"""
                SELECT n.id::text, n.object_type, n.object_id, n.kind, n.release_id::text,
                       n.payload, n.created_at, n.read_at, n.acted_at, v.version_no
                FROM share_notifications n
                LEFT JOIN shared_versions v ON v.id = n.release_id
                WHERE n.recipient_id = CAST(:uid AS uuid) {clause}
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
            "object_type": r[1],
            "object_id": r[2],
            "kind": r[3],
            "release_id": r[4],
            "payload": r[5] if isinstance(r[5], dict) else json.loads(r[5] or "{}"),
            "created_at": r[6].isoformat() if r[6] else "",
            "read_at": r[7].isoformat() if r[7] else None,
            "acted_at": r[8].isoformat() if r[8] else None,
            "version_no": int(r[9]) if r[9] is not None else None,
        }
        for r in rows
    ]


def unread_count(recipient_id: str) -> int:
    session = get_session()
    try:
        return int(session.execute(
            sa_text("""
                SELECT count(*) FROM share_notifications
                WHERE recipient_id = CAST(:uid AS uuid) AND read_at IS NULL
            """),
            {"uid": recipient_id},
        ).scalar() or 0)
    finally:
        session.close()


def mark_read(notification_id: str, recipient_id: str) -> bool:
    """通知を既読にする（本人のみ）。戻り値は更新できたか。"""
    session = get_session()
    try:
        result = session.execute(
            sa_text("""
                UPDATE share_notifications
                SET read_at = now()
                WHERE id = CAST(:nid AS uuid)
                  AND recipient_id = CAST(:uid AS uuid)
                  AND read_at IS NULL
            """),
            {"nid": notification_id, "uid": recipient_id},
        )
        session.commit()
        return int(result.rowcount or 0) > 0
    except Exception:
        session.rollback()
        logger.exception("mark_read failed: %s", notification_id)
        return False
    finally:
        session.close()


def mark_all_read(recipient_id: str) -> int:
    session = get_session()
    try:
        result = session.execute(
            sa_text("""
                UPDATE share_notifications
                SET read_at = now()
                WHERE recipient_id = CAST(:uid AS uuid) AND read_at IS NULL
            """),
            {"uid": recipient_id},
        )
        session.commit()
        return int(result.rowcount or 0)
    except Exception:
        session.rollback()
        logger.exception("mark_all_read failed: %s", recipient_id)
        return 0
    finally:
        session.close()
