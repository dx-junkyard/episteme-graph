"""通知 fan-out（決定論的・非LLM）。イベント種別 → 宛先 + user_notifications 書き込み。

宛先は教材所有者 / コース所有者 / 共有 editor に限定する（S5: 権限が所有者・共有 editor
以外に広がらない。viewer には出さない）。v1 の配信対象は schema.EVENT_TO_NOTIFICATION_KIND
の6種のみ（段階登録。通知過多は通知が無いのと同じ）。

宛先解決の JOIN 形（*_group_permissions を group_members と JOIN する部分）は
core.notification_recipients に集約している（core/versioning/notifications.py と
同型の実装が独立に存在していたのを統合。Tier2 提案10）。「owner を含める」
「editor のみを対象にする」という組み立て自体はこのファイル固有（S5 の要件）。
"""
from __future__ import annotations

import json
import logging

from sqlalchemy import text as sa_text

from core import notification_recipients as _recipients
from core.postgres import get_session

from . import schema

logger = logging.getLogger(__name__)


def _material_recipients(session, document_id: str) -> list[str]:
    ids: set[str] = set()
    owner = _recipients.document_owner_id(session, document_id)
    if owner:
        ids.add(owner)
    ids.update(_recipients.document_group_member_ids(session, document_id, ("editor",)))
    return list(ids)


def _course_recipients(session, course_id: str) -> list[str]:
    ids: set[str] = set()
    owner = _recipients.course_owner_id(session, course_id)
    if owner:
        ids.add(owner)
    ids.update(_recipients.course_group_member_ids(session, course_id, ("editor",)))
    return list(ids)


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
