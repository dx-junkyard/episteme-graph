"""通知 fan-out（決定論的・非LLM）。イベント種別 → 宛先 + user_notifications 書き込み。

宛先は教材所有者 / コース所有者 / 共有 editor に限定する（S5: 権限が所有者・共有 editor
以外に広がらない）。v1 の配信対象は schema.EVENT_TO_NOTIFICATION_KIND の6種のみ
（段階登録。通知過多は通知が無いのと同じ）。
"""
from __future__ import annotations

import json
import logging

from sqlalchemy import text as sa_text

from core.postgres import get_session

from . import schema

logger = logging.getLogger(__name__)


def _material_recipients(session, document_id: str) -> list[str]:
    rows = session.execute(
        sa_text("""
            SELECT DISTINCT uid FROM (
                SELECT uploaded_by::text AS uid FROM documents WHERE id = CAST(:did AS uuid)
                UNION
                SELECT gm.user_id::text AS uid
                FROM document_group_permissions p
                JOIN group_members gm ON gm.group_id = p.group_id
                WHERE p.document_id = CAST(:did AS uuid) AND p.permission = 'editor'
            ) t WHERE uid IS NOT NULL
        """),
        {"did": document_id},
    ).fetchall()
    return [str(r[0]) for r in rows]


def _course_recipients(session, course_id: str) -> list[str]:
    rows = session.execute(
        sa_text("""
            SELECT DISTINCT uid FROM (
                SELECT user_id::text AS uid FROM learning_courses WHERE id = :cid
                UNION
                SELECT gm.user_id::text AS uid
                FROM course_group_permissions p
                JOIN group_members gm ON gm.group_id = p.group_id
                WHERE p.course_id = :cid AND p.permission = 'editor'
            ) t WHERE uid IS NOT NULL
        """),
        {"cid": course_id},
    ).fetchall()
    return [str(r[0]) for r in rows]


def recipients_for(entity_type: str, entity_id: str) -> list[str]:
    """宛先ユーザー id を返す（所有者 + 共有 editor のみ, S5 fail-closed）。"""
    session = get_session()
    try:
        if entity_type == schema.ENTITY_TYPE_MATERIAL:
            return _material_recipients(session, entity_id)
        if entity_type == schema.ENTITY_TYPE_COURSE:
            return _course_recipients(session, entity_id)
        return []
    finally:
        session.close()


def fan_out_event(
    event_id: str,
    event_kind: str,
    entity_type: str,
    entity_id: str,
    payload: dict | None = None,
) -> int:
    """1件の status_events を通知に fan-out する。v1 の6種以外は no-op。戻り値は配信件数。"""
    notif_kind = schema.EVENT_TO_NOTIFICATION_KIND.get(event_kind)
    if not notif_kind:
        return 0
    ids = recipients_for(entity_type, entity_id)
    if not ids:
        return 0

    session = get_session()
    try:
        payload_json = json.dumps(payload or {}, ensure_ascii=False)
        for uid in ids:
            session.execute(
                sa_text("""
                    INSERT INTO user_notifications
                        (recipient_id, kind, entity_type, entity_id, event_id, payload)
                    VALUES (CAST(:uid AS uuid), :kind, :entity_type, :entity_id,
                            CAST(:event_id AS uuid), CAST(:payload AS jsonb))
                """),
                {
                    "uid": uid, "kind": notif_kind, "entity_type": entity_type,
                    "entity_id": entity_id, "event_id": event_id, "payload": payload_json,
                },
            )
        session.commit()
        return len(ids)
    except Exception:
        session.rollback()
        logger.exception("status notification fan-out failed: %s %s %s", event_kind, entity_type, entity_id)
        return 0
    finally:
        session.close()
