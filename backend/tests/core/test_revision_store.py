"""共通基盤 core/revision_store.py（Tier 3-20）のユニットテスト。

- ``RevisionConflictError`` の current_revision 属性
- ``update_with_revision_lock``: 成功 / 衝突（current_revision 付き）/
  not_found_exc 指定時の未存在ケース / revision_default によるコーナーケース差分
- ``idempotent_seed_import``: 存在チェックでのスキップ / 作成成功 /
  catch_create_errors=True で1件失敗を握って続行 / False で伝播（atlas 相当）
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core import revision_store  # noqa: E402


class _FakeResult:
    def __init__(self, rows=None, rowcount=0):
        self._rows = rows or []
        self.rowcount = rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _ScriptedSession:
    """execute() 呼び出し順に応じて事前定義した Result を返す最小セッション。"""

    def __init__(self, results: list[_FakeResult]):
        self._results = list(results)
        self.calls: list[tuple[str, dict]] = []

    def execute(self, stmt, params=None):
        self.calls.append((str(stmt), dict(params or {})))
        return self._results.pop(0)


class MyConflictError(revision_store.RevisionConflictError):
    pass


class MyNotFoundError(Exception):
    pass


class TestRevisionConflictError:
    def test_stores_current_revision(self):
        exc = revision_store.RevisionConflictError("stale", current_revision=5)
        assert str(exc) == "stale"
        assert exc.current_revision == 5

    def test_defaults_current_revision_to_none(self):
        exc = revision_store.RevisionConflictError("stale")
        assert exc.current_revision is None


class TestUpdateWithRevisionLock:
    def test_success_returns_result(self):
        session = _ScriptedSession([_FakeResult(rows=[("row",)], rowcount=1)])
        result = revision_store.update_with_revision_lock(
            session,
            update_sql="UPDATE t SET x = :x WHERE id = :id AND revision = :rev",
            update_params={"x": 1, "id": "a", "rev": 1},
            select_sql="SELECT revision FROM t WHERE id = :id",
            select_params={"id": "a"},
            conflict_exc=MyConflictError,
            conflict_message="conflict",
        )
        assert result.fetchone() == ("row",)
        assert len(session.calls) == 1  # select は呼ばれない

    def test_conflict_raises_with_current_revision(self):
        session = _ScriptedSession(
            [
                _FakeResult(rowcount=0),  # update が0件ヒット
                _FakeResult(rows=[(7,)]),  # 再 SELECT で現在の revision
            ]
        )
        with pytest.raises(MyConflictError) as exc:
            revision_store.update_with_revision_lock(
                session,
                update_sql="UPDATE t SET x = :x WHERE id = :id AND revision = :rev",
                update_params={"x": 1, "id": "a", "rev": 1},
                select_sql="SELECT revision FROM t WHERE id = :id",
                select_params={"id": "a"},
                conflict_exc=MyConflictError,
                conflict_message="conflict",
            )
        assert exc.value.current_revision == 7
        assert str(exc.value) == "conflict"

    def test_not_found_when_select_returns_nothing_and_not_found_exc_given(self):
        session = _ScriptedSession([_FakeResult(rowcount=0), _FakeResult(rows=[])])
        with pytest.raises(MyNotFoundError):
            revision_store.update_with_revision_lock(
                session,
                update_sql="UPDATE t SET x = :x WHERE id = :id AND revision = :rev",
                update_params={"x": 1, "id": "missing", "rev": 1},
                select_sql="SELECT revision FROM t WHERE id = :id",
                select_params={"id": "missing"},
                conflict_exc=MyConflictError,
                conflict_message="conflict",
                not_found_exc=MyNotFoundError,
                not_found_message="not found: missing",
            )

    def test_conflict_with_current_revision_none_when_no_not_found_exc(self):
        """atlas_store の挙動: 対象行が消えていても not_found_exc を指定しない場合は
        current_revision=None の conflict_exc になる (未存在と衝突を区別しない)。"""
        session = _ScriptedSession([_FakeResult(rowcount=0), _FakeResult(rows=[])])
        with pytest.raises(MyConflictError) as exc:
            revision_store.update_with_revision_lock(
                session,
                update_sql="UPDATE t SET x = :x WHERE id = :id AND revision = :rev",
                update_params={"x": 1, "id": "a", "rev": 1},
                select_sql="SELECT revision FROM t WHERE id = :id",
                select_params={"id": "a"},
                conflict_exc=MyConflictError,
                conflict_message="conflict",
            )
        assert exc.value.current_revision is None

    def test_revision_default_applies_fallback_for_falsy_value(self):
        """library 側の `int(existing[0] or 1)` コーナーケースを revision_default で再現する。"""
        session = _ScriptedSession([_FakeResult(rowcount=0), _FakeResult(rows=[(None,)])])
        with pytest.raises(MyConflictError) as exc:
            revision_store.update_with_revision_lock(
                session,
                update_sql="UPDATE t SET x = :x WHERE id = :id AND revision = :rev",
                update_params={"x": 1, "id": "a", "rev": 1},
                select_sql="SELECT revision FROM t WHERE id = :id",
                select_params={"id": "a"},
                conflict_exc=MyConflictError,
                conflict_message="conflict",
                revision_default=1,
            )
        assert exc.value.current_revision == 1

    def test_without_revision_default_raises_on_none_value(self):
        """atlas_store の挙動: revision_default 未指定では None 値をそのまま int() に渡す
        (現行コードのコーナーケースをそのまま踏襲し、フォールバックしない)。"""
        session = _ScriptedSession([_FakeResult(rowcount=0), _FakeResult(rows=[(None,)])])
        with pytest.raises(TypeError):
            revision_store.update_with_revision_lock(
                session,
                update_sql="UPDATE t SET x = :x WHERE id = :id AND revision = :rev",
                update_params={"x": 1, "id": "a", "rev": 1},
                select_sql="SELECT revision FROM t WHERE id = :id",
                select_params={"id": "a"},
                conflict_exc=MyConflictError,
                conflict_message="conflict",
            )


class TestIdempotentSeedImport:
    def test_creates_new_candidates_and_counts_imported(self):
        created = []
        result = revision_store.idempotent_seed_import(
            ["a", "b"],
            exists_fn=lambda c: False,
            create_fn=created.append,
        )
        assert result == {"imported": 2, "skipped": 0, "errors": []}
        assert created == ["a", "b"]

    def test_existing_candidates_are_skipped(self):
        result = revision_store.idempotent_seed_import(
            ["a", "b"],
            exists_fn=lambda c: c == "a",
            create_fn=lambda c: None,
        )
        assert result == {"imported": 1, "skipped": 1, "errors": []}

    def test_catch_create_errors_true_holds_failure_and_continues(self):
        created = []
        errors_seen = []

        def _create(c):
            if c == "bad":
                raise RuntimeError("boom")
            created.append(c)

        result = revision_store.idempotent_seed_import(
            ["good", "bad", "good2"],
            exists_fn=lambda c: False,
            create_fn=_create,
            on_create_error=lambda c, exc: errors_seen.append((c, str(exc))),
        )
        assert result["imported"] == 2
        assert result["skipped"] == 1
        assert result["errors"] == ["boom"]
        assert created == ["good", "good2"]
        assert errors_seen == [("bad", "boom")]

    def test_catch_create_errors_false_propagates(self):
        """atlas 相当: 同梱データは信頼できる前提で fail-fast する。"""

        def _create(c):
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            revision_store.idempotent_seed_import(
                ["a"],
                exists_fn=lambda c: False,
                create_fn=_create,
                catch_create_errors=False,
            )

    def test_on_created_hook_invoked_only_for_successful_creates(self):
        notified = []
        revision_store.idempotent_seed_import(
            ["a", "b"],
            exists_fn=lambda c: c == "b",
            create_fn=lambda c: None,
            on_created=notified.append,
        )
        assert notified == ["a"]

    def test_exists_fn_exception_propagates_uncaught(self):
        """存在チェック自体の失敗は握らない（DB 接続断など致命的な失敗はそのまま伝播）。"""

        def _exists(_c):
            raise RuntimeError("db down")

        with pytest.raises(RuntimeError):
            revision_store.idempotent_seed_import(
                ["a"], exists_fn=_exists, create_fn=lambda c: None
            )
