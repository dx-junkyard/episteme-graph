"""theory_review_events への監査追記（core 内で完結・FastAPI 非依存）。

api/services.py の `record_review_event` と同じ表・列を使う。core が api を import
しないよう（project rule 2）、独立した best-effort ヘルパーとして持つ。
"""
from __future__ import annotations

import json
import logging

from sqlalchemy import text as sa_text

from core.postgres import get_session

logger = logging.getLogger(__name__)


def record_event(
    entity_type: str,
    entity_id: str,
    old_status: str,
    new_status: str,
    user_id: str | None,
    metadata: dict | None = None,
) -> None:
    """監査行を1件追記する（best-effort。失敗しても呼び出し元を止めない）。"""
    session = get_session()
    try:
        session.execute(
            sa_text("""
                INSERT INTO theory_review_events
                    (entity_type, entity_id, old_status, new_status, changed_by, metadata)
                VALUES
                    (:entity_type, :entity_id, :old_status, :new_status,
                     CAST(:changed_by AS uuid), CAST(:metadata AS jsonb))
            """),
            {
                "entity_type": entity_type,
                "entity_id": entity_id,
                "old_status": old_status or "",
                "new_status": new_status or "",
                "changed_by": user_id or None,
                "metadata": json.dumps(metadata or {}, ensure_ascii=False),
            },
        )
        session.commit()
    except Exception:
        session.rollback()
        logger.warning("failed to record versioning audit event: %s %s", entity_type, entity_id, exc_info=True)
    finally:
        session.close()
