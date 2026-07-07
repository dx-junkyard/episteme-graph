"""assistant_actions テーブル I/O（migration 034）。

戻す（Undo/Revert）の永続台帳。P3: apply 前に before スナップショットを保持し、
取り消しは行削除ではなく status 遷移（applied → reverted）で行う。
"""

from __future__ import annotations

import datetime
import json
import uuid
from typing import Optional

from sqlalchemy import text as sa_text

from core.postgres import get_session as _pg_session


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def new_action_id() -> str:
    return "a_" + uuid.uuid4().hex[:20]


def _row_to_dict(row) -> Optional[dict]:
    if row is None:
        return None
    m = row._mapping
    out = dict(m)
    for k in ("args", "before_snapshot", "after_snapshot", "revert_spec"):
        v = out.get(k)
        if isinstance(v, str):
            try:
                out[k] = json.loads(v)
            except Exception:
                pass
    for k in ("reverted_at", "created_at"):
        if out.get(k) is not None:
            out[k] = out[k].isoformat()
    return out


def create_action(
    *,
    user_id: str,
    capability_id: str,
    screen: str,
    target_type: str,
    target_id: Optional[str],
    args: dict,
    before_snapshot: Optional[dict],
    after_snapshot: Optional[dict],
    reversible: bool,
    revert_spec: Optional[dict],
    session_id: Optional[str] = None,
    status: str = "applied",
) -> dict:
    action_id = new_action_id()
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("""
                INSERT INTO assistant_actions
                    (id, user_id, session_id, capability_id, screen, target_type, target_id,
                     args, before_snapshot, after_snapshot, reversible, revert_spec, status)
                VALUES
                    (:id, CAST(:user_id AS uuid), :session_id, :capability_id, :screen,
                     :target_type, :target_id, CAST(:args AS jsonb),
                     CAST(:before AS jsonb), CAST(:after AS jsonb), :reversible,
                     CAST(:revert_spec AS jsonb), :status)
                RETURNING id, user_id::text AS user_id, session_id, capability_id, screen,
                          target_type, target_id, args, before_snapshot, after_snapshot,
                          reversible, revert_spec, status, reverted_at, created_at
            """),
            {
                "id": action_id,
                "user_id": user_id,
                "session_id": session_id,
                "capability_id": capability_id,
                "screen": screen,
                "target_type": target_type,
                "target_id": target_id,
                "args": json.dumps(args or {}, ensure_ascii=False),
                "before": json.dumps(before_snapshot) if before_snapshot is not None else None,
                "after": json.dumps(after_snapshot) if after_snapshot is not None else None,
                "reversible": bool(reversible),
                "revert_spec": json.dumps(revert_spec) if revert_spec is not None else None,
                "status": status,
            },
        ).fetchone()
        session.commit()
        return _row_to_dict(row)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_action(action_id: str) -> Optional[dict]:
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("""
                SELECT id, user_id::text AS user_id, session_id, capability_id, screen,
                       target_type, target_id, args, before_snapshot, after_snapshot,
                       reversible, revert_spec, status, reverted_at, created_at
                FROM assistant_actions WHERE id = :id
            """),
            {"id": action_id},
        ).fetchone()
        return _row_to_dict(row)
    finally:
        session.close()


def mark_reverted(action_id: str) -> bool:
    session = _pg_session()
    try:
        res = session.execute(
            sa_text("""
                UPDATE assistant_actions
                SET status = 'reverted', reverted_at = :ts
                WHERE id = :id AND status = 'applied'
                RETURNING id
            """),
            {"id": action_id, "ts": _now()},
        ).fetchone()
        session.commit()
        return res is not None
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def list_actions(user_id: str, limit: int = 20) -> list[dict]:
    session = _pg_session()
    try:
        rows = session.execute(
            sa_text("""
                SELECT id, user_id::text AS user_id, session_id, capability_id, screen,
                       target_type, target_id, args, before_snapshot, after_snapshot,
                       reversible, revert_spec, status, reverted_at, created_at
                FROM assistant_actions
                WHERE user_id = CAST(:user_id AS uuid)
                ORDER BY created_at DESC
                LIMIT :limit
            """),
            {"user_id": user_id, "limit": max(1, min(limit, 100))},
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        session.close()
