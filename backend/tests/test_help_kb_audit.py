"""content-hash 監査記帳（設計書 §2-3、温存・Phase 3 任意実装）のテスト。

対象: ``backend/core/help_kb/audit.py``。フェイクセッションで
``theory_review_events`` への書き込みを検証する（DB 不要）。
"""

from __future__ import annotations

import json

import pytest

from core import schema as core_schema
from core.help_kb import audit as kb_audit


# ---------------------------------------------------------------------------
# フェイク DB（core/atlas 系テストと同型の router ベース FakeSession）
# ---------------------------------------------------------------------------


class FakeResult:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class FakeSession:
    """``theory_review_events`` への SELECT(最新hash) / INSERT のみを解する簡易フェイク。"""

    def __init__(self, previous_hash: str | None = None, raise_on_insert: bool = False):
        self.previous_hash = previous_hash
        self.calls: list[tuple[str, dict]] = []
        self.committed = False
        self.rolled_back = False
        self.closed = False
        self._raise_on_insert = raise_on_insert
        self.inserted_metadata: dict | None = None

    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        params = params or {}
        self.calls.append((sql, params))
        if sql.startswith("SELECT metadata->>'content_hash'"):
            if self.previous_hash is None:
                return FakeResult(None)
            return FakeResult((self.previous_hash,))
        if sql.startswith("INSERT INTO theory_review_events"):
            if self._raise_on_insert:
                raise RuntimeError("boom: insert failed")
            self.inserted_metadata = json.loads(params["metadata"])
            self.previous_hash = self.inserted_metadata["content_hash"]
            return FakeResult(None)
        raise AssertionError(f"unexpected SQL in FakeSession: {sql}")

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class RaisingSelectSession(FakeSession):
    """SELECT 自体が例外を投げる（テーブル未整備・DB 障害）想定のフェイク。"""

    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        if sql.startswith("SELECT metadata->>'content_hash'"):
            raise RuntimeError("relation \"theory_review_events\" does not exist")
        return super().execute(stmt, params)


def _section(audience, file, anchor, title, body):
    return {"audience": audience, "file": file, "anchor": anchor, "title": title, "body": body}


def _patch_manual(monkeypatch, sections, excluded=None):
    monkeypatch.setattr(
        kb_audit._manual, "_sections_for_audience", lambda audience: list(sections)
    )
    monkeypatch.setattr(kb_audit._manual, "excluded_sections", lambda: list(excluded or []))


# ---------------------------------------------------------------------------
# manual_snapshot() の決定性
# ---------------------------------------------------------------------------


class TestManualSnapshot:
    def test_returns_three_keys_only(self, monkeypatch):
        _patch_manual(monkeypatch, [_section("student", "01.md", "a", "T", "B")])
        snap = kb_audit.manual_snapshot()
        assert set(snap.keys()) == {"content_hash", "files", "excluded_sections"}

    def test_deterministic_for_same_input(self, monkeypatch):
        sections = [
            _section("student", "01.md", "a", "Title A", "Body A"),
            _section("teacher", "02.md", "b", "Title B", "Body B"),
        ]
        _patch_manual(monkeypatch, sections)
        snap1 = kb_audit.manual_snapshot()
        snap2 = kb_audit.manual_snapshot()
        assert snap1 == snap2
        assert snap1["content_hash"] == snap2["content_hash"]

    def test_hash_invariant_to_section_order(self, monkeypatch):
        a = _section("student", "01.md", "a", "Title A", "Body A")
        b = _section("teacher", "02.md", "b", "Title B", "Body B")

        _patch_manual(monkeypatch, [a, b])
        snap_ab = kb_audit.manual_snapshot()

        _patch_manual(monkeypatch, [b, a])
        snap_ba = kb_audit.manual_snapshot()

        assert snap_ab["content_hash"] == snap_ba["content_hash"]
        assert snap_ab["files"] == snap_ba["files"]

    def test_hash_changes_when_body_changes(self, monkeypatch):
        _patch_manual(monkeypatch, [_section("student", "01.md", "a", "T", "Body v1")])
        snap1 = kb_audit.manual_snapshot()

        _patch_manual(monkeypatch, [_section("student", "01.md", "a", "T", "Body v2")])
        snap2 = kb_audit.manual_snapshot()

        assert snap1["content_hash"] != snap2["content_hash"]

    def test_files_are_audience_qualified_and_deduped(self, monkeypatch):
        sections = [
            _section("student", "01.md", "a", "T1", "B1"),
            _section("student", "01.md", "b", "T2", "B2"),
            _section("system_admin", "04.md", "c", "T3", "B3"),
        ]
        _patch_manual(monkeypatch, sections)
        snap = kb_audit.manual_snapshot()
        assert snap["files"] == ["student/01.md", "system_admin/04.md"]

    def test_excluded_sections_reflected(self, monkeypatch):
        _patch_manual(
            monkeypatch,
            [_section("student", "01.md", "a", "T", "B")],
            excluded=[{"audience": "teacher", "file": "03.md", "anchor": "x", "title": "TODO節"}],
        )
        snap = kb_audit.manual_snapshot()
        assert snap["excluded_sections"] == ["teacher/03.md#x"]


# ---------------------------------------------------------------------------
# record_snapshot_if_changed()
# ---------------------------------------------------------------------------


class TestRecordSnapshotIfChanged:
    def test_first_call_records(self, monkeypatch):
        _patch_manual(monkeypatch, [_section("student", "01.md", "a", "T", "B")])
        session = FakeSession(previous_hash=None)

        recorded = kb_audit.record_snapshot_if_changed(session=session)

        assert recorded is True
        assert session.committed is True
        assert session.inserted_metadata is not None

    def test_unchanged_hash_is_not_recorded_again(self, monkeypatch):
        _patch_manual(monkeypatch, [_section("student", "01.md", "a", "T", "B")])
        snap = kb_audit.manual_snapshot()
        session = FakeSession(previous_hash=snap["content_hash"])

        recorded = kb_audit.record_snapshot_if_changed(session=session)

        assert recorded is False
        insert_calls = [c for c in session.calls if c[0].startswith("INSERT")]
        assert insert_calls == []

    def test_changed_content_is_recorded(self, monkeypatch):
        _patch_manual(monkeypatch, [_section("student", "01.md", "a", "T", "Body v1")])
        old_snap = kb_audit.manual_snapshot()
        session = FakeSession(previous_hash=old_snap["content_hash"])

        _patch_manual(monkeypatch, [_section("student", "01.md", "a", "T", "Body v2")])
        recorded = kb_audit.record_snapshot_if_changed(session=session)

        assert recorded is True
        assert session.inserted_metadata["content_hash"] != old_snap["content_hash"]

    def test_repeated_calls_do_not_accumulate_rows(self, monkeypatch):
        """再起動を模した複数回呼び出しでも、内容不変なら行が増殖しない（冪等性）。"""
        _patch_manual(monkeypatch, [_section("student", "01.md", "a", "T", "B")])
        session = FakeSession(previous_hash=None)

        first = kb_audit.record_snapshot_if_changed(session=session)
        second = kb_audit.record_snapshot_if_changed(session=session)
        third = kb_audit.record_snapshot_if_changed(session=session)

        assert (first, second, third) == (True, False, False)
        insert_calls = [c for c in session.calls if c[0].startswith("INSERT")]
        assert len(insert_calls) == 1

    def test_changed_by_is_always_null(self, monkeypatch):
        """コンテナ内には git 帰属が無いため changed_by は常に NULL（偽装記帳しない）。"""
        _patch_manual(monkeypatch, [_section("student", "01.md", "a", "T", "B")])
        session = FakeSession(previous_hash=None)

        kb_audit.record_snapshot_if_changed(session=session)

        insert_sql, insert_params = next(
            c for c in session.calls if c[0].startswith("INSERT")
        )
        assert "NULL" in insert_sql
        assert "changed_by" not in insert_params  # bound only via literal NULL, no param

    def test_entity_type_uses_catalog_constant(self, monkeypatch):
        _patch_manual(monkeypatch, [_section("student", "01.md", "a", "T", "B")])
        session = FakeSession(previous_hash=None)

        kb_audit.record_snapshot_if_changed(session=session)

        _sql, params = next(c for c in session.calls if c[0].startswith("INSERT"))
        assert params["entity_type"] == core_schema.AUDIT_ENTITY_MANUAL
        assert core_schema.AUDIT_ENTITY_MANUAL in core_schema.AUDIT_ENTITY_TYPES

    def test_metadata_has_exactly_three_keys(self, monkeypatch):
        _patch_manual(monkeypatch, [_section("student", "01.md", "a", "T", "B")])
        session = FakeSession(previous_hash=None)

        kb_audit.record_snapshot_if_changed(session=session)

        assert set(session.inserted_metadata.keys()) == {
            "content_hash",
            "files",
            "excluded_sections",
        }

    def test_fail_open_on_db_error_during_select(self, monkeypatch):
        _patch_manual(monkeypatch, [_section("student", "01.md", "a", "T", "B")])
        session = RaisingSelectSession(previous_hash=None)

        recorded = kb_audit.record_snapshot_if_changed(session=session)

        assert recorded is False

    def test_fail_open_on_db_error_during_insert(self, monkeypatch):
        _patch_manual(monkeypatch, [_section("student", "01.md", "a", "T", "B")])
        session = FakeSession(previous_hash=None, raise_on_insert=True)

        recorded = kb_audit.record_snapshot_if_changed(session=session)

        assert recorded is False
        assert session.rolled_back is True

    def test_fail_open_never_raises(self, monkeypatch):
        _patch_manual(monkeypatch, [_section("student", "01.md", "a", "T", "B")])
        session = RaisingSelectSession(previous_hash=None)
        try:
            kb_audit.record_snapshot_if_changed(session=session)
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"record_snapshot_if_changed must fail-open, raised: {exc!r}")


# ---------------------------------------------------------------------------
# ソース検査: 生リテラル "manual" を INSERT 近傍で使わない
# ---------------------------------------------------------------------------


class TestSourceUsesCatalogConstantNotRawLiteral:
    def test_no_raw_manual_literal_passed_as_entity_type(self):
        src = kb_audit.__file__
        with open(src, encoding="utf-8") as f:
            text = f.read()
        # entity_type の値として渡している箇所は AUDIT_ENTITY_MANUAL のみであるべき。
        # dict リテラル `"entity_type": AUDIT_ENTITY_MANUAL,` の形を維持していることを検査する。
        assert '"entity_type": AUDIT_ENTITY_MANUAL' in text
        assert '"entity_type": "manual"' not in text
        assert "'entity_type': 'manual'" not in text

    def test_module_imports_catalog_constant(self):
        assert "from core.schema import AUDIT_ENTITY_MANUAL" in open(
            kb_audit.__file__, encoding="utf-8"
        ).read()
