"""live 行の同期（knowledge_objects_design.md §5.2 / §5.3・KO3）。

再解析は **DELETE しない**。stable_key が一致する live 行は同じ UUID のまま内容列を
更新し、人間の確定列（``preserved_columns`` / 「触った行」の追加保護列）は触らない。
一致しない旧 live 行は ``superseded_at`` / ``superseded_by_run_id`` を刻んで残し、
一致しない新オブジェクトは INSERT する。

純 SQL ヘルパ。呼び出し側のセッションで動き、**commit しない**（呼び出し側の
トランザクションに同乗する）。FastAPI / LLM は import しない。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text as sa_text

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    """:func:`sync_live_rows` の戻り値。

    Attributes:
        id_map: ``{agent_id: DB UUID}``。更新・新規の両方を含む。
        remaps: ``[(old_agent_id, new_agent_id, stable_key)]``。stable_key が一致した
            live 行で agent 側 ID だけが変わった組（KO8 の再係留の材料）。
        stats: ``{"updated", "inserted", "superseded"}`` の件数。
    """

    id_map: dict[str, str] = field(default_factory=dict)
    remaps: list[tuple[str, str, str]] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=lambda: {"updated": 0, "inserted": 0, "superseded": 0})


def _strip_nuls(value: Any) -> Any:
    """PostgreSQL の text / jsonb が受け付けない NUL を落とす（persistence と同じ扱い）。"""
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, list):
        return [_strip_nuls(v) for v in value]
    if isinstance(value, dict):
        return {str(k).replace("\x00", ""): _strip_nuls(v) for k, v in value.items()}
    return value


def _json_dumps(value: Any) -> str:
    return json.dumps(_strip_nuls(value), ensure_ascii=False)


def _clean(value: Any) -> str:
    return str(value or "").strip()


class _Binder:
    """列 → プレースホルダ SQL / パラメータの対応を作る。

    - 値が dict / list なら ``CAST(:p AS jsonb)``（呼び出し側で json 文字列化しない）
    - ``column_casts`` に型が宣言されていれば ``CAST(:p AS <type>)``（uuid 等）
    - それ以外は素の ``:p``（列型からの推論に任せる）
    """

    def __init__(self, column_casts: Mapping[str, str] | None = None):
        self._casts = dict(column_casts or {})
        self.params: dict[str, Any] = {}
        self._counter = 0

    def bind(self, column: str, value: Any) -> str:
        self._counter += 1
        name = f"v{self._counter}"
        if isinstance(value, (dict, list)):
            self.params[name] = _json_dumps(value)
            return f"CAST(:{name} AS jsonb)"
        cast = self._casts.get(column)
        self.params[name] = _strip_nuls(value)
        if cast:
            return f"CAST(:{name} AS {cast})"
        return f":{name}"


def sync_live_rows(
    session,
    *,
    table: str,
    document_id: str,
    run_id: str | None,
    incoming: Sequence[Mapping[str, Any]],
    content_columns: Sequence[str],
    agent_id_column: str,
    preserved_columns: Sequence[str] = (),
    human_touched: Callable[[Mapping[str, Any]], bool] | None = None,
    protected_when_touched: Sequence[str] = (),
    column_casts: Mapping[str, str] | None = None,
    touch_columns: Sequence[str] = (),
) -> SyncResult:
    """1 document 分の live 行を incoming に同期する。

    Args:
        table: 基表名（``core/knowledge_objects/schema.py`` の ``TABLE_*``）。
        incoming: ``[{"stable_key", "agent_id", "values": {列: 値}}]``。``values`` は
            ``content_columns`` ∪ ``preserved_columns`` の **初期値**（新規 INSERT 用）。
            既存行の ``preserved_columns`` は上書きしない。
        content_columns: 一致時に上書きしてよい内容列。
        preserved_columns: 人間の確定列（一致時は触らない・§5.3）。
        human_touched: live 行（SELECT した mapping）を受け取り「人間が触った行」なら
            True を返す述語。True の行では ``protected_when_touched`` も上書きしない。
        protected_when_touched: 触った行でのみ追加で保護する内容列（components の
            ``name`` / ``summary``）。
        column_casts: 列 → SQL 型（``{"chunk_id": "uuid"}``）。dict / list 値は宣言不要
            （自動で jsonb になる）。
        touch_columns: ``human_touched`` の判定に必要で ``preserved_columns`` に
            含まれない列（SELECT に足すだけ）。

    Returns:
        :class:`SyncResult`。
    """
    content_columns = tuple(dict.fromkeys(content_columns))
    preserved_columns = tuple(dict.fromkeys(preserved_columns))
    protected_when_touched = tuple(dict.fromkeys(protected_when_touched))
    select_extra = tuple(dict.fromkeys(tuple(preserved_columns) + tuple(touch_columns)))

    select_columns = ["id::text AS id", "stable_key", f"{agent_id_column} AS agent_id"]
    select_columns += [c for c in select_extra if c not in ("id", "stable_key", agent_id_column)]
    live_rows = [
        dict(row)
        for row in session.execute(
            sa_text(
                f"""
                SELECT {", ".join(select_columns)}
                FROM {table}
                WHERE document_id = :document_id
                  AND superseded_at IS NULL
                """
            ),
            {"document_id": document_id},
        ).mappings().all()
    ]

    by_key: dict[str, dict] = {}
    for row in live_rows:
        key = _clean(row.get("stable_key"))
        if key and key not in by_key:
            by_key[key] = row

    result = SyncResult()
    matched_ids: set[str] = set()

    for item in incoming:
        stable_key = _clean(item.get("stable_key"))
        agent_id = _clean(item.get("agent_id"))
        values = dict(item.get("values") or {})
        live = by_key.get(stable_key) if stable_key else None

        if live is not None and _clean(live.get("id")) not in matched_ids:
            row_id = _clean(live.get("id"))
            matched_ids.add(row_id)
            protected = set(preserved_columns)
            if human_touched is not None:
                try:
                    touched = bool(human_touched(live))
                except Exception:  # pragma: no cover - 述語は純粋な想定だが落とさない
                    logger.warning("human_touched predicate failed for %s id=%s", table, row_id, exc_info=True)
                    touched = True
                if touched:
                    protected |= set(protected_when_touched)
            binder = _Binder(column_casts)
            assignments = [
                f"{column} = {binder.bind(column, values[column])}"
                for column in content_columns
                if column in values and column not in protected
            ]
            assignments.append(f"{agent_id_column} = {binder.bind(agent_id_column, agent_id)}")
            if run_id:
                assignments.append(f"produced_by_run_id = {binder.bind('produced_by_run_id', run_id)}")
            assignments.append("updated_at = now()")
            params = dict(binder.params)
            params["row_id"] = row_id
            session.execute(
                sa_text(
                    f"""
                    UPDATE {table}
                    SET {", ".join(assignments)}
                    WHERE id = CAST(:row_id AS uuid)
                    """
                ),
                params,
            )
            result.stats["updated"] += 1
            old_agent_id = _clean(live.get("agent_id"))
            if agent_id and old_agent_id and old_agent_id != agent_id:
                result.remaps.append((old_agent_id, agent_id, stable_key))
            if agent_id:
                result.id_map[agent_id] = row_id
            continue

        binder = _Binder(column_casts)
        columns: list[str] = ["document_id", "stable_key", agent_id_column]
        expressions: list[str] = [
            binder.bind("document_id", document_id),
            binder.bind("stable_key", stable_key),
            binder.bind(agent_id_column, agent_id),
        ]
        for column in tuple(content_columns) + tuple(preserved_columns):
            if column in values and column not in columns:
                columns.append(column)
                expressions.append(binder.bind(column, values[column]))
        if run_id:
            columns.append("produced_by_run_id")
            expressions.append(binder.bind("produced_by_run_id", run_id))
        row = session.execute(
            sa_text(
                f"""
                INSERT INTO {table} ({", ".join(columns)})
                VALUES ({", ".join(expressions)})
                RETURNING id
                """
            ),
            binder.params,
        ).fetchone()
        new_id = str(row[0]) if row else ""
        result.stats["inserted"] += 1
        if agent_id and new_id:
            result.id_map[agent_id] = new_id

    stale_ids = [
        _clean(row.get("id"))
        for row in live_rows
        if _clean(row.get("id")) and _clean(row.get("id")) not in matched_ids
    ]
    if stale_ids:
        params: dict[str, Any] = {"run_id": run_id}
        placeholders = []
        for index, row_id in enumerate(stale_ids):
            name = f"sup_{index}"
            params[name] = row_id
            placeholders.append(f"CAST(:{name} AS uuid)")
        session.execute(
            sa_text(
                f"""
                UPDATE {table}
                SET superseded_at = now(),
                    superseded_by_run_id = CAST(:run_id AS uuid),
                    updated_at = now()
                WHERE id IN ({", ".join(placeholders)})
                """
            ),
            params,
        )
        result.stats["superseded"] = len(stale_ids)

    return result
