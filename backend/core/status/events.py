"""status_events の読み取り専用アクセサ（エージェント向けイベント購読の読み取り口）。

v1 の消費者は通知 fan-out（notification_rules.py）のみだが、将来のエージェント
自動支援・KPI 集計のための読み取り口としてここに切り出す。書き込み API は作らない
（append-only の書き込みは watcher.py の `_emit` に一元化する）。

FastAPI / LLM クライアントを import しない（S3。core のテスタビリティ確保）。
"""
from __future__ import annotations

from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session

_MIN_LIMIT = 1
_MAX_LIMIT = 500
_DEFAULT_LIMIT = 100


def _clamp_limit(limit: int) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        value = _DEFAULT_LIMIT
    return max(_MIN_LIMIT, min(_MAX_LIMIT, value))


def _iso(value) -> str:
    if value is None:
        return ""
    try:
        return value.isoformat()
    except AttributeError:
        return str(value)


def _row_to_dict(row: dict) -> dict:
    return {
        "id": row["id"],
        "event_kind": row["event_kind"],
        "entity_type": row["entity_type"],
        "entity_id": row["entity_id"],
        "from_state": row["from_state"],
        "to_state": row["to_state"],
        "source_table": row["source_table"],
        "source_id": row["source_id"],
        "payload": row["payload"] if isinstance(row["payload"], dict) else {},
        "occurred_at": _iso(row["occurred_at"]),
        "created_at": _iso(row["created_at"]),
    }


def list_events(
    session: Session,
    *,
    entity_type: str | None = None,
    entity_id: str | None = None,
    entity_ids: list[str] | None = None,
    event_kind: str | None = None,
    since: str | None = None,
    limit: int = _DEFAULT_LIMIT,
) -> list[dict]:
    """status_events を新しい順に返す（フィルタは指定されたものだけ WHERE に効かせる）。

    `since` は occurred_at と比較する ISO8601 文字列（下限、含む）。`entity_ids` は
    所有物スコープの絞り込み用（LIMIT の前に SQL で効かせ、他ユーザーのイベントで
    limit 窓が埋まって自分のイベントが溢れるのを防ぐ）。書き込みは行わない。
    """
    clauses = []
    params: dict = {}

    if entity_type:
        clauses.append("entity_type = :entity_type")
        params["entity_type"] = entity_type
    if entity_id:
        clauses.append("entity_id = :entity_id")
        params["entity_id"] = entity_id
    if entity_ids is not None:
        clauses.append("entity_id = ANY(:entity_ids)")
        params["entity_ids"] = list(entity_ids)
    if event_kind:
        clauses.append("event_kind = :event_kind")
        params["event_kind"] = event_kind
    if since:
        clauses.append("occurred_at >= CAST(:since AS timestamptz)")
        params["since"] = since

    where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params["limit"] = _clamp_limit(limit)

    rows = session.execute(
        sa_text(f"""
            SELECT id::text, event_kind, entity_type, entity_id, from_state, to_state,
                   source_table, source_id, payload, occurred_at, created_at
            FROM status_events
            {where_sql}
            ORDER BY occurred_at DESC
            LIMIT :limit
        """),
        params,
    ).mappings().fetchall()
    return [_row_to_dict(dict(r)) for r in rows]
