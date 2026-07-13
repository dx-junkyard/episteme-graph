"""library_entries / library_entry_versions (migration 042) のインメモリフェイク。

core/library/store.py が発行する SQL 面だけを模倣する。`atlas_skeletons_fake.py`
（`AtlasSkeletonTableFake`）の流儀を踏襲する。store.py の各公開関数は自前で
セッションを開閉する（`from core.postgres import get_session`）ため、テストは
`monkeypatch.setattr(store, "get_session", make_session_factory(fake))` の形で
差し替える。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

_JSON_FIELDS = ("aliases", "body", "exemplar_images", "source_component_ids", "source_document_ids")

_ENTRY_FIELDS = (
    "domain_key",
    "entry_type",
    "name",
    "aliases",
    "summary",
    "body",
    "exemplar_images",
    "source_component_ids",
    "source_document_ids",
)

_ENTRY_COLUMN_ORDER = (
    "id",
    "domain_key",
    "entry_type",
    "name",
    "aliases",
    "summary",
    "body",
    "exemplar_images",
    "source_component_ids",
    "source_document_ids",
    "status",
    "revision",
    "latest_version_no",
    "created_by",
    "updated_by",
    "created_at",
    "updated_at",
)


class FakeResult:
    def __init__(self, rows=None, rowcount=0):
        self._rows = rows or []
        self.rowcount = rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class LibraryEntryTableFake:
    """library_entries / library_entry_versions のインメモリ実装。

    セッションとテーブルを兼ねる (commit/rollback/close は no-op)。
    """

    def __init__(self):
        self.entries: dict[str, dict] = {}
        self.versions: list[dict] = []
        self._seq = 0
        self.calls: list[tuple[str, dict]] = []

    # -- session インターフェース --
    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        params = dict(params or {})
        self.calls.append((sql, params))
        return self._dispatch(sql, params)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass

    # -- ヘルパ --
    def _next_id(self) -> str:
        self._seq += 1
        return f"{self._seq:08d}-0000-0000-0000-000000000000"

    def _row_tuple(self, entry: dict) -> tuple:
        return tuple(entry[c] for c in _ENTRY_COLUMN_ORDER)

    # -- SQL ディスパッチ --
    def _dispatch(self, sql: str, p: dict) -> FakeResult:  # noqa: C901
        if sql.startswith("INSERT INTO library_entries"):
            return self._insert_entry(p)
        if sql.startswith("INSERT INTO library_entry_versions"):
            return self._insert_version(p)
        if sql.startswith("SELECT 1 FROM library_entries"):
            return self._exists_by_domain_name_type(p)
        if sql.startswith("SELECT revision FROM library_entries"):
            return self._select_revision(p)
        if sql.startswith("SELECT id::text, entry_id::text, version_no"):
            return self._select_versions(sql, p)
        if sql.startswith("SELECT id::text, domain_key"):
            return self._select_entries(sql, p)
        if sql.startswith("UPDATE library_entries"):
            if "revision = :expected_revision" in sql:
                return self._update_entry_optimistic(p)
            if "SET status" in sql:
                return self._set_status(p)
            if "SET latest_version_no" in sql:
                return self._update_latest_version_no(p)
            raise AssertionError(f"unhandled UPDATE library_entries SQL: {sql}")
        if sql.startswith("SELECT domain_key,") and "count(*)" in sql:
            return self._domain_summary()
        raise AssertionError(f"unhandled library SQL: {sql}")

    def _insert_entry(self, p: dict) -> FakeResult:
        entry_id = self._next_id()
        now = datetime.now(timezone.utc)
        entry = {
            "id": entry_id,
            "domain_key": p["domain_key"],
            "entry_type": p["entry_type"],
            "name": p["name"],
            "aliases": _as_list(p.get("aliases")),
            "summary": p.get("summary") or "",
            "body": _as_dict(p.get("body")),
            "exemplar_images": _as_list(p.get("exemplar_images")),
            "source_component_ids": _as_list(p.get("source_component_ids")),
            "source_document_ids": _as_list(p.get("source_document_ids")),
            "status": "active",
            "revision": 1,
            "latest_version_no": 0,
            "created_by": p.get("created_by"),
            "updated_by": p.get("created_by"),
            "created_at": now,
            "updated_at": now,
        }
        self.entries[entry_id] = entry
        return FakeResult([self._row_tuple(entry)], rowcount=1)

    def _select_entries(self, sql: str, p: dict) -> FakeResult:
        if "WHERE id = CAST(:id AS uuid) LIMIT 1" in sql:
            entry = self.entries.get(str(p.get("id") or ""))
            return FakeResult([self._row_tuple(entry)] if entry else [])

        rows = list(self.entries.values())
        if "status = 'active'" in sql:
            rows = [r for r in rows if r["status"] == "active"]
        if "domain_key = :domain_key" in sql:
            rows = [r for r in rows if r["domain_key"] == p.get("domain_key")]
        if "entry_type = :entry_type" in sql:
            rows = [r for r in rows if r["entry_type"] == p.get("entry_type")]
        if "name ILIKE :q" in sql:
            needle = str(p.get("q") or "").strip("%").lower()
            rows = [
                r
                for r in rows
                if needle in r["name"].lower()
                or needle in str(r["aliases"]).lower()
                or needle in r["summary"].lower()
            ]
        rows.sort(key=lambda r: (r["domain_key"], r["entry_type"], r["name"]))
        return FakeResult([self._row_tuple(r) for r in rows])

    def _update_entry_optimistic(self, p: dict) -> FakeResult:
        entry_id = str(p.get("id") or "")
        expected_revision = int(p.get("expected_revision") or 0)
        entry = self.entries.get(entry_id)
        if entry is None or entry["revision"] != expected_revision:
            return FakeResult(rowcount=0)
        for key, value in p.items():
            if key in ("id", "expected_revision", "updated_by"):
                continue
            if key in _JSON_FIELDS:
                entry[key] = _as_json_value(value)
            elif key in _ENTRY_FIELDS:
                entry[key] = value
        entry["revision"] += 1
        entry["updated_by"] = p.get("updated_by")
        entry["updated_at"] = datetime.now(timezone.utc)
        return FakeResult([self._row_tuple(entry)], rowcount=1)

    def _select_revision(self, p: dict) -> FakeResult:
        entry = self.entries.get(str(p.get("id") or ""))
        if entry is None:
            return FakeResult()
        return FakeResult([(entry["revision"],)])

    def _set_status(self, p: dict) -> FakeResult:
        entry_id = str(p.get("id") or "")
        entry = self.entries.get(entry_id)
        if entry is None:
            return FakeResult()
        entry["status"] = p.get("status")
        entry["updated_by"] = p.get("updated_by")
        entry["updated_at"] = datetime.now(timezone.utc)
        return FakeResult([self._row_tuple(entry)], rowcount=1)

    def _insert_version(self, p: dict) -> FakeResult:
        version_id = self._next_id()
        now = datetime.now(timezone.utc)
        version = {
            "id": version_id,
            "entry_id": str(p.get("entry_id") or ""),
            "version_no": int(p["version_no"]),
            "content": _as_json_value(p.get("content")),
            "note": p.get("note") or "",
            "published_by": p.get("published_by"),
            "created_at": now,
        }
        self.versions.append(version)
        return FakeResult([(version_id, version["version_no"], now)], rowcount=1)

    def _update_latest_version_no(self, p: dict) -> FakeResult:
        entry_id = str(p.get("id") or "")
        entry = self.entries.get(entry_id)
        if entry is None:
            return FakeResult(rowcount=0)
        entry["latest_version_no"] = int(p["version_no"])
        entry["updated_at"] = datetime.now(timezone.utc)
        return FakeResult(rowcount=1)

    def _exists_by_domain_name_type(self, p: dict) -> FakeResult:
        hit = any(
            e["domain_key"] == p.get("domain_key")
            and e["name"] == p.get("name")
            and e["entry_type"] == p.get("entry_type")
            for e in self.entries.values()
        )
        return FakeResult([(1,)] if hit else [])

    def _select_versions(self, sql: str, p: dict) -> FakeResult:
        entry_id = str(p.get("entry_id") or "")
        rows = [v for v in self.versions if v["entry_id"] == entry_id]
        if "AND version_no = :version_no" in sql:
            version_no = int(p.get("version_no") or 0)
            rows = [v for v in rows if v["version_no"] == version_no]
        rows.sort(key=lambda v: -v["version_no"])
        return FakeResult(
            [
                (
                    v["id"],
                    v["entry_id"],
                    v["version_no"],
                    v["content"],
                    v["note"],
                    v["published_by"],
                    v["created_at"],
                )
                for v in rows
            ]
        )

    def _domain_summary(self) -> FakeResult:
        totals: dict[str, dict[str, int]] = {}
        for entry in self.entries.values():
            bucket = totals.setdefault(
                entry["domain_key"], {"entry_count": 0, "frozen_count": 0, "retired_count": 0}
            )
            if entry["status"] == "active":
                bucket["entry_count"] += 1
                if entry["latest_version_no"] > 0:
                    bucket["frozen_count"] += 1
            elif entry["status"] == "retired":
                bucket["retired_count"] += 1
        return FakeResult(
            [
                (domain_key, b["entry_count"], b["frozen_count"], b["retired_count"])
                for domain_key, b in sorted(totals.items())
            ]
        )


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return _as_json_value(value) if isinstance(value, str) else list(value)


def _as_dict(value: Any) -> dict:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    return _as_json_value(value) if isinstance(value, str) else dict(value)


def _as_json_value(value: Any) -> Any:
    if isinstance(value, str):
        import json

        return json.loads(value)
    return value


def make_session_factory(fake: LibraryEntryTableFake) -> Callable[[], Any]:
    """core.library.store.get_session の差し替え用 (常に同じフェイクを返す)。"""

    def factory():
        return fake

    return factory
