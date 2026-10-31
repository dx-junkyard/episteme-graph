"""検証シミュレーション層（SB層）— 実験機部品の目録（L層 凍結版の読み出し）。

設計: ``docs/features/simulation_bridge_design.md`` §5.2（部品は凍結版
``library_entry_versions.content`` から読む。draft を読まない = L層の規律）。

- 読むのは ``library_entries`` と ``library_entry_versions`` の 2 表だけ（書かない）。
- 対象は ``entry_type ∈ schema.PART_TYPES`` かつ ``status='active'`` の entry の**最新の凍結版**。
  ``review_status='candidate'`` の entry は凍結できないため、自然に目録から外れる。
- FastAPI / LLM を import しない。セッションは呼び出し側が管理する（commit しない）。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text as sa_text

from core.simulation_bridge import schema

logger = logging.getLogger(__name__)

__all__ = ["catalog_by_id", "load_parts_catalog", "part_row"]

_CATALOG_SQL = """
    WITH latest AS (
        SELECT v.entry_id, v.version_no, v.content,
               row_number() OVER (PARTITION BY v.entry_id ORDER BY v.version_no DESC) AS rn
          FROM library_entry_versions v
    )
    SELECT e.id::text, l.version_no, e.entry_type, l.content
      FROM latest l
      JOIN library_entries e ON e.id = l.entry_id
     WHERE l.rn = 1
       AND e.status = 'active'
       AND e.entry_type = ANY(:entry_types)
     ORDER BY e.entry_type, e.name, e.id
"""


def _loads(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def part_row(entry_id: Any, version_no: Any, entry_type: Any, content: Any) -> dict:
    """1 行を目録の形 ``{entry_id, version_no, entry_type, entry_type_label, name, summary, body}`` にする。"""
    data = _loads(content)
    etype = str(entry_type or data.get("entry_type") or "")
    body = data.get("body") if isinstance(data.get("body"), dict) else {}
    try:
        vno = int(version_no) if version_no is not None else None
    except (TypeError, ValueError):
        vno = None
    return {
        "entry_id": str(entry_id or ""),
        "version_no": vno,
        "entry_type": etype,
        "entry_type_label": schema.PART_TYPE_LABELS.get(etype, etype),
        "name": str(data.get("name") or ""),
        "summary": str(data.get("summary") or ""),
        "body": body,
    }


def load_parts_catalog(session: Any) -> list[dict]:
    """凍結版の部品目録（3 entry_type・active・各 entry の最新版）。"""
    rows = session.execute(
        sa_text(_CATALOG_SQL),
        {"entry_types": list(schema.PART_TYPES)},
    ).fetchall()
    out: list[dict] = []
    for row in rows or []:
        values = list(row)
        if len(values) < 4:
            continue
        item = part_row(values[0], values[1], values[2], values[3])
        if item["entry_id"] and item["entry_type"] in schema.PART_TYPES:
            out.append(item)
    return out


def catalog_by_id(rows: list[dict]) -> dict[str, dict]:
    """目録の一覧を ``entry_id -> 部品`` に引ける形にする（instrument / revision が読む形）。"""
    return {str(row.get("entry_id")): dict(row) for row in rows or [] if row.get("entry_id")}
