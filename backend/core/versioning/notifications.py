"""通知インボックス。発行・削除予約・取消・削除を共有先へ配信する。

宛先＝当該オブジェクトに viewer|editor を持つグループメンバー（所有者は exclude）。
JOIN の形（*_group_permissions を group_members と JOIN する部分）は
core.notification_recipients に集約している（core/status/notification_rules.py と
同型の実装が独立に存在していたのを統合。Tier2 提案10）。「所有者を含めない」
「viewer も対象にする」「レガシー単一グループ共有も含める」という組み立て自体は
このファイル固有（V層の共有可視性の要件）。

【2026-07 アーキテクチャ整理 Tier 3-15】通知の実体は user_notifications（migration 038,
状態管理・通知基盤）に統合済み（migration 045）。本モジュールは source='shared' で
スコープした行のみを読み書きする（status 層の行と混ざらない）。旧・専用通知テーブルの
object_type/object_id は統合テーブルの entity_type/entity_id 列に入っており、この
モジュールの公開関数のシグネチャ・戻り値の形（object_type/object_id/release_id/acted_at
等のキー名）は SQL 側のエイリアスで従来どおり保つ（routes/versioning.py の生形式 API
互換のため）。
"""
from __future__ import annotations

import json
import logging

from sqlalchemy import text as sa_text

from core import notification_recipients as _recipients
from core.postgres import get_session

from . import schema

logger = logging.getLogger(__name__)


def _recipient_ids(session, object_type: str, object_id: str) -> list[str]:
    """当該オブジェクトの共有先（通知を届けるべきユーザー id）を返す。fan_out / recipients_for の単一の真実源。

    - course: object_group_permissions（migration 044、統合前は専用テーブル=migration 010）の
      viewer|editor グループメンバー。
    - document: object_group_permissions（統合前は専用テーブル=migration 035）の
      viewer|editor に加え、
      レガシー単一グループ共有（documents.visibility='group' + group_id）のメンバーも含める。
      これらは services.user_can_view_document で閲覧・pin/adopt が許可されるため、
      通知の宛先も揃える（更新・削除予約・削除の取りこぼしを防ぐ）。
    """
    if object_type == schema.OBJECT_TYPE_COURSE:
        return _recipients.course_group_member_ids(session, object_id, ("viewer", "editor"))
    ids = set(_recipients.document_group_member_ids(session, object_id, ("viewer", "editor")))
    ids.update(_recipients.document_legacy_group_visibility_member_ids(session, object_id))
    return list(ids)


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
                    INSERT INTO user_notifications
                        (recipient_id, entity_type, entity_id, kind, release_id, payload, source)
                    VALUES (CAST(:uid AS uuid), :ot, :oid, :kind,
                            CAST(:rid AS uuid), CAST(:payload AS jsonb), 'shared')
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
    """本人の通知一覧（新しい順）。release_id があれば version_no を添える。

    source='shared' でスコープする（status 層の行を混ぜない。二重表示防止）。
    """
    session = get_session()
    try:
        clause = "AND n.read_at IS NULL" if unread_only else ""
        rows = session.execute(
            sa_text(f"""
                SELECT n.id::text, n.entity_type, n.entity_id, n.kind, n.release_id::text,
                       n.payload, n.created_at, n.read_at, n.acted_at, v.version_no
                FROM user_notifications n
                LEFT JOIN shared_versions v ON v.id = n.release_id
                WHERE n.recipient_id = CAST(:uid AS uuid) AND n.source = 'shared' {clause}
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
    """source='shared' でスコープする（status 層の未読数と混ざらない）。"""
    session = get_session()
    try:
        return int(session.execute(
            sa_text("""
                SELECT count(*) FROM user_notifications
                WHERE recipient_id = CAST(:uid AS uuid) AND source = 'shared' AND read_at IS NULL
            """),
            {"uid": recipient_id},
        ).scalar() or 0)
    finally:
        session.close()


def mark_read(notification_id: str, recipient_id: str) -> bool:
    """通知を既読にする（本人のみ）。戻り値は更新できたか。source='shared' でスコープする。"""
    session = get_session()
    try:
        result = session.execute(
            sa_text("""
                UPDATE user_notifications
                SET read_at = now()
                WHERE id = CAST(:nid AS uuid)
                  AND recipient_id = CAST(:uid AS uuid)
                  AND source = 'shared'
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
    """source='shared' でスコープする（status 層の行を巻き込んで既読にしない）。"""
    session = get_session()
    try:
        result = session.execute(
            sa_text("""
                UPDATE user_notifications
                SET read_at = now()
                WHERE recipient_id = CAST(:uid AS uuid) AND source = 'shared' AND read_at IS NULL
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
