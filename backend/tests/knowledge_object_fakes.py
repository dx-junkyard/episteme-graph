"""知識オブジェクト同期（knowledge_objects_design.md §5.2）のテスト用 fake セッション。

``core.knowledge_objects.sync`` は列名を SQL に組み立て、値を ``:v1`` … の連番
プレースホルダで渡す。テスト側が「どの列に何を書いたか」で契約を検査できるように、
この fake は INSERT / UPDATE 文を解析して ``{列: 値}`` に戻す。DB も LLM も使わない。
"""

from __future__ import annotations

import json
import re
from typing import Any

_PLACEHOLDER = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)")


def _split_top_level(text: str) -> list[str]:
    """括弧の深さ 0 のカンマで分割する（``CAST(:v1 AS jsonb)`` を壊さない）。"""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for char in text:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    if current:
        parts.append("".join(current))
    return [p.strip() for p in parts if p.strip()]


def _balanced_group(text: str, start: int) -> str:
    """``text[start]`` の ``(`` から対応する ``)`` までの中身を返す。"""
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                return text[start + 1:index]
    return ""


def _value_for(expression: str, params: dict) -> Any:
    names = _PLACEHOLDER.findall(expression)
    if not names:
        return expression.strip()
    return params.get(names[0])


class _FakeResult:
    def __init__(self, rows: list[dict] | None = None, row: tuple | None = None, rowcount: int = 0):
        self._rows = rows or []
        self._row = row
        self.rowcount = rowcount

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)

    def fetchone(self):
        if self._row is not None:
            return self._row
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeKnowledgeSession:
    """live 行を返し、INSERT / UPDATE を列単位で捕捉する fake セッション。

    Attributes:
        inserts: ``[(table, {列: 値})]``
        updates: ``[(table, where_params, {列: 値})]``
        superseded: supersede UPDATE で指定された行 id のリスト
        sql: 実行された SQL 文（順序）
    """

    def __init__(self, live_rows: list[dict] | None = None, id_prefix: str = "new-uuid"):
        self.live_rows = [dict(r) for r in (live_rows or [])]
        self.id_prefix = id_prefix
        self.sql: list[str] = []
        self.inserts: list[tuple[str, dict]] = []
        self.updates: list[tuple[str, dict, dict]] = []
        self.superseded: list[str] = []
        self.committed = False
        self.rolled_back = False
        self._counter = 0
        self.scalar_counts: list[int] = []

    # -- helpers ----------------------------------------------------------
    def inserted_into(self, table: str) -> list[dict]:
        return [values for name, values in self.inserts if name == table]

    def updated_in(self, table: str) -> list[dict]:
        return [values for name, _where, values in self.updates if name == table]

    def json_of(self, values: dict, column: str) -> Any:
        raw = values.get(column)
        if isinstance(raw, str):
            return json.loads(raw)
        return raw

    # -- session API ------------------------------------------------------
    def execute(self, statement, params=None):
        sql = str(statement)
        params = dict(params or {})
        self.sql.append(sql)
        stripped = sql.strip()

        if stripped.upper().startswith("SELECT"):
            if "COUNT(*)" in sql.upper():
                value = self.scalar_counts.pop(0) if self.scalar_counts else 0
                return _FakeResult(row=(value,))
            if "superseded_at IS NULL" in sql:
                return _FakeResult(rows=self.live_rows)
            return _FakeResult(rows=[])

        if "INSERT INTO" in sql:
            table = re.search(r"INSERT INTO\s+([A-Za-z_][A-Za-z0-9_]*)", sql).group(1)
            columns_match = re.search(r"INSERT INTO\s+[A-Za-z_][A-Za-z0-9_]*\s*\(([^)]*)\)", sql, re.S)
            values_start = sql.find("(", sql.upper().find("VALUES"))
            values: dict[str, Any] = {}
            if columns_match and values_start > 0:
                columns = [c.strip() for c in columns_match.group(1).split(",") if c.strip()]
                expressions = _split_top_level(_balanced_group(sql, values_start))
                for column, expression in zip(columns, expressions):
                    values[column] = _value_for(expression, params)
            else:  # pragma: no cover - 解析できない形は生パラメータで残す
                values = params
            self.inserts.append((table, values))
            self._counter += 1
            return _FakeResult(row=(f"{self.id_prefix}-{self._counter}",), rowcount=1)

        if stripped.upper().startswith("UPDATE"):
            table = re.search(r"UPDATE\s+([A-Za-z_][A-Za-z0-9_]*)", sql).group(1)
            set_match = re.search(r"SET\s+(.*?)\s+WHERE", sql, re.S)
            values = {}
            if set_match:
                for assignment in _split_top_level(set_match.group(1)):
                    if "=" not in assignment:
                        continue
                    column, expression = assignment.split("=", 1)
                    values[column.strip()] = _value_for(expression, params)
            if "superseded_at" in values:
                self.superseded.extend(
                    value for key, value in params.items() if key.startswith("sup_")
                )
            self.updates.append((table, params, values))
            return _FakeResult(rowcount=1)

        if stripped.upper().startswith("DELETE"):
            return _FakeResult(rowcount=0)
        return _FakeResult()

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        pass
