"""アカウントライフサイクル管理 — 削除層（Phase 3）の core 実装のテスト。

正本: ``docs/features/account_lifecycle_management_design.md`` §8（削除のセマンティクス）。
対象は ``backend/core/account_lifecycle.py`` と、スイーパへの相乗り
（``backend/core/versioning/worker.py``）。

DB には接続せず、``core.postgres.get_session`` を「発行された SQL を記録し、指定した行を
返すだけ」のフェイクへ差し替える（``core/`` 配下のテストの流儀）。検証観点:

  1. :func:`due_users` が pending_deletion + 期限到来のみを対象にする
  2. **AL9**: 所有物が残っていれば users 行を**一切変更しない**（通知だけを積む）
  3. **AL1**: ``DELETE FROM users`` を発行せず、墓標化（UPDATE）で終わる
  4. 冪等: すでに ``status='deleted'`` / 行不在は no-op
  5. §8.4 の「DELETE する」表の全エントリが実際に DELETE される
  6. AL3: 墓標化のあとに ``account_status.invalidate`` が呼ばれる
  7. §8.2 移管: documents / learning_courses（user_id + owner_id）/ groups +
     移管先の group admin 保証
  8. スイーパ: 1件の失敗が全体を止めない / V層の sweep 結果を捨てない
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _path in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from core import account_lifecycle  # noqa: E402

_UID = "11111111-1111-1111-1111-111111111111"
_ADMIN_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_ADMIN_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
_DEST = "22222222-2222-2222-2222-222222222222"
_GROUP = "33333333-3333-3333-3333-333333333333"


# ---------------------------------------------------------------------------
# フェイクセッション
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, rows=(), rowcount: int = 0):
        self._rows = list(rows)
        self.rowcount = rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeSession:
    """発行 SQL を記録し、あらかじめ与えた行を返すだけのフェイク。"""

    def __init__(self, *, user_row=None, leftovers=None, admin_ids=(),
                 recent_notice=False, group_ids=(), rowcounts=None):
        self.user_row = user_row
        self.leftovers = dict(leftovers or {})
        self.admin_ids = list(admin_ids)
        self.recent_notice = recent_notice
        self.group_ids = list(group_ids)
        self.rowcounts = dict(rowcounts or {})
        self.calls: list[tuple[str, dict]] = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = 0

    # --- 記録 ------------------------------------------------------------
    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        self.calls.append((sql, dict(params or {})))

        if sql.startswith("SELECT status, display_name FROM users"):
            return _Result([self.user_row] if self.user_row else [])
        if sql.startswith("SELECT id::text, status_changed_by::text FROM users"):
            return _Result([(_UID, _ADMIN_A)])
        if sql.startswith("SELECT COUNT(*) FROM "):
            table = sql.split("FROM ")[1].split(" ")[0]
            return _Result([(self.leftovers.get(table, 0),)])
        if "FROM user_notifications" in sql:
            return _Result([(1,)] if self.recent_notice else [])
        if "FROM users WHERE role = 'admin'" in sql:
            return _Result([(uid,) for uid in self.admin_ids])
        if sql.startswith("SELECT id::text FROM groups"):
            return _Result([(gid,) for gid in self.group_ids])
        if sql.startswith("DELETE FROM "):
            table = sql.split("DELETE FROM ")[1].split(" ")[0]
            return _Result(rowcount=self.rowcounts.get(table, 0))
        if sql.startswith("UPDATE "):
            table = sql.split("UPDATE ")[1].split(" ")[0]
            return _Result(rowcount=self.rowcounts.get(table, 0))
        return _Result()

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed += 1

    # --- 検査用 ----------------------------------------------------------
    def sqls(self) -> list[str]:
        return [sql for sql, _params in self.calls]

    def deleted_tables(self) -> list[str]:
        return [
            sql.split("DELETE FROM ")[1].split(" ")[0]
            for sql in self.sqls() if sql.startswith("DELETE FROM ")
        ]

    def updated_tables(self) -> list[str]:
        return [
            sql.split("UPDATE ")[1].split(" ")[0]
            for sql in self.sqls() if sql.startswith("UPDATE ")
        ]

    def inserted_tables(self) -> list[str]:
        return [
            sql.split("INSERT INTO ")[1].split(" ")[0]
            for sql in self.sqls() if sql.startswith("INSERT INTO ")
        ]


@pytest.fixture
def env(monkeypatch):
    """``core.postgres.get_session`` を差し替え、監査・キャッシュ破棄を記録する。"""
    import core.postgres

    state: dict = {"sessions": [], "audits": [], "invalidated": []}

    def _factory():
        session = FakeSession(**state.get("session_kwargs", {}))
        state["sessions"].append(session)
        return session

    monkeypatch.setattr(core.postgres, "get_session", _factory)
    monkeypatch.setattr(
        account_lifecycle, "_record_audit",
        lambda *args, **kwargs: state["audits"].append((args, kwargs)),
    )
    monkeypatch.setattr(
        account_lifecycle.account_status, "invalidate",
        lambda uid: state["invalidated"].append(str(uid)),
    )
    return state


def _configure(env, **kwargs) -> None:
    env["session_kwargs"] = kwargs


def _first(env) -> FakeSession:
    return env["sessions"][0]


# ---------------------------------------------------------------------------
# 1. 表そのものの契約（AL1 / §8.4）
# ---------------------------------------------------------------------------


class TestPurgeTables:
    def test_users_is_never_a_purge_target(self):
        """AL1: users 行は物理削除しない（import 時 assert の二重化）。"""
        assert all(t.table != "users" for t in account_lifecycle.PURGE_TABLES)

    def test_every_entry_declares_a_reason(self):
        for target in account_lifecycle.PURGE_TABLES:
            assert target.reason, f"{target.table}.{target.column} に根拠コメントが無い"
        for note in account_lifecycle.RETAIN_TABLES:
            assert note.reason, f"{note.table} に残す根拠が無い"

    def test_delete_sql_binds_the_user_id(self):
        sql = account_lifecycle._delete_sql(
            account_lifecycle.PurgeTarget("learning_states", "user_id")
        )
        assert sql == "DELETE FROM learning_states WHERE user_id = CAST(:uid AS uuid)"

    def test_delete_sql_appends_extra_condition(self):
        sql = account_lifecycle._delete_sql(
            account_lifecycle.PurgeTarget("llm_model_policies", "user_id", where="scope = 'user'")
        )
        assert sql.endswith("WHERE user_id = CAST(:uid AS uuid) AND scope = 'user'")

    def test_scope_user_only_for_llm_model_policies(self):
        """M層のシステム既定（scope='system'）を巻き添えにしない。"""
        entries = [t for t in account_lifecycle.PURGE_TABLES if t.table == "llm_model_policies"]
        assert entries and all("scope = 'user'" in t.where for t in entries)

    def test_tombstone_values_are_deterministic(self):
        first = account_lifecycle.tombstone_values(_UID)
        assert first == account_lifecycle.tombstone_values(_UID)
        assert first["email"].startswith("deleted+") and first["email"].endswith("invalid.local")
        assert first["display_name"] == "deleted-11111111"


# ---------------------------------------------------------------------------
# 2. due_users
# ---------------------------------------------------------------------------


class TestDueUsers:
    def test_selects_only_pending_deletion_past_due(self):
        session = FakeSession()
        rows = account_lifecycle.due_users(session)
        sql = session.sqls()[0]
        assert "status = 'pending_deletion'" in sql
        assert "purge_after <= now()" in sql
        assert "purge_after IS NOT NULL" in sql
        assert rows == [account_lifecycle.DueUser(user_id=_UID, scheduled_by=_ADMIN_A)]

    def test_reads_only(self):
        session = FakeSession()
        account_lifecycle.due_users(session)
        assert session.commits == 0
        assert all(sql.startswith("SELECT") for sql in session.sqls())


# ---------------------------------------------------------------------------
# 3. purge_user — 冪等・no-op
# ---------------------------------------------------------------------------


class TestPurgeIdempotency:
    def test_missing_user_is_noop(self, env):
        _configure(env, user_row=None)
        assert account_lifecycle.purge_user(_UID) is False
        assert _first(env).updated_tables() == []
        assert env["audits"] == []

    def test_already_deleted_is_noop(self, env):
        _configure(env, user_row=("deleted", "deleted-11111111"))
        assert account_lifecycle.purge_user(_UID) is False
        assert _first(env).deleted_tables() == []
        assert _first(env).updated_tables() == []

    def test_empty_user_id_is_noop_without_session(self, env):
        assert account_lifecycle.purge_user("") is False
        assert env["sessions"] == []


# ---------------------------------------------------------------------------
# 4. purge_user — AL9 前提チェック
# ---------------------------------------------------------------------------


class TestPurgePreconditions:
    def test_leftovers_abort_without_touching_users(self, env):
        _configure(env, user_row=("pending_deletion", "old-teacher"),
                   leftovers={"documents": 3}, admin_ids=[_ADMIN_A, _ADMIN_B])
        assert account_lifecycle.purge_user(_UID) is False
        session = _first(env)
        # users 行を一切変更していない（AL9）
        assert "users" not in session.updated_tables()
        # 個人データも消していない
        assert session.deleted_tables() == []

    def test_leftovers_notify_all_active_system_admins(self, env):
        _configure(env, user_row=("pending_deletion", "old-teacher"),
                   leftovers={"learning_courses": 2}, admin_ids=[_ADMIN_A, _ADMIN_B])
        account_lifecycle.purge_user(_UID)
        session = _first(env)
        inserts = [
            (sql, params) for sql, params in session.calls
            if sql.startswith("INSERT INTO user_notifications")
        ]
        assert len(inserts) == 2
        recipients = {params["uid"] for _sql, params in inserts}
        assert recipients == {_ADMIN_A, _ADMIN_B}
        for sql, params in inserts:
            assert "'status'" in sql  # migration 045 の CHECK に収まる source
            assert params["kind"] == account_lifecycle.NOTIF_ACCOUNT_PURGE_BLOCKED
            assert "移管または削除してから再実行されます" in params["payload"]
            assert "コース" in params["payload"]

    def test_leftover_notice_is_deduped_within_window(self, env):
        _configure(env, user_row=("pending_deletion", "old-teacher"),
                   leftovers={"groups": 1}, admin_ids=[_ADMIN_A], recent_notice=True)
        account_lifecycle.purge_user(_UID)
        session = _first(env)
        assert session.inserted_tables() == []
        # 通知を見送った周期は監査記帳もしない（同じ事実を毎周期積まない）
        assert env["audits"] == []

    def test_leftover_notice_records_audit_when_sent(self, env):
        _configure(env, user_row=("pending_deletion", "old-teacher"),
                   leftovers={"documents": 1}, admin_ids=[_ADMIN_A])
        account_lifecycle.purge_user(_UID)
        assert len(env["audits"]) == 1
        args, _kwargs = env["audits"][0]
        # old_status == new_status（状態は変えていない）
        assert args[1] == args[2] == "pending_deletion"
        assert args[4]["action"] == account_lifecycle.AUDIT_ACTION_PURGE_BLOCKED
        assert args[4]["leftovers"] == {"documents": 1}

    def test_ownership_guards_cover_the_three_owner_columns(self):
        guards = {(table, column) for table, column, _label in account_lifecycle.OWNERSHIP_GUARDS}
        assert guards == {
            ("documents", "uploaded_by"),
            ("learning_courses", "user_id"),
            ("groups", "created_by"),
        }


# ---------------------------------------------------------------------------
# 5. purge_user — 墓標化
# ---------------------------------------------------------------------------


class TestPurgeTombstone:
    def _run(self, env, **kwargs):
        _configure(env, user_row=("pending_deletion", "old-teacher"), leftovers={}, **kwargs)
        return account_lifecycle.purge_user(_UID, scheduled_by=_ADMIN_A)

    def test_returns_true_and_updates_users_row(self, env):
        assert self._run(env) is True
        assert _first(env).updated_tables() == ["users"]

    def test_never_deletes_the_users_row(self, env):
        self._run(env)
        assert "users" not in _first(env).deleted_tables()
        assert not any("DELETE FROM users" in sql for sql in _first(env).sqls())

    def test_deletes_every_declared_purge_target(self, env):
        self._run(env)
        deleted = _first(env).deleted_tables()
        for target in account_lifecycle.PURGE_TABLES:
            assert target.table in deleted, f"{target.table} が purge されていない"

    def test_tombstone_anonymizes_and_bumps_generation(self, env):
        self._run(env)
        update_sql, params = next(
            (sql, p) for sql, p in _first(env).calls if sql.startswith("UPDATE users")
        )
        assert "token_generation = token_generation + 1" in update_sql
        assert "status = 'deleted'" in update_sql
        assert "purge_after = NULL" in update_sql
        assert params["email"] == f"deleted+{_UID}@invalid.local"
        assert params["display_name"] == "deleted-11111111"
        assert params["password_hash"] == account_lifecycle.TOMBSTONE_PASSWORD_HASH
        # 検証不能な非 NULL センチネル（§8.4: NULL にしない）
        assert params["password_hash"] is not None

    def test_invalidates_status_cache_after_commit(self, env):
        self._run(env)
        assert env["invalidated"] == [_UID]
        assert _first(env).commits == 1

    def test_records_audit_with_transition_and_counts(self, env):
        self._run(env, rowcounts={"learning_states": 4})
        args, _kwargs = env["audits"][-1]
        assert args[0] == _UID
        assert args[1] == "pending_deletion"
        assert args[2] == "deleted"
        assert args[3] == _ADMIN_A
        assert args[4]["action"] == account_lifecycle.AUDIT_ACTION_PURGE
        assert args[4]["purged_rows"] == {"learning_states.user_id": 4}

    def test_session_is_closed(self, env):
        self._run(env)
        assert _first(env).closed == 1


# ---------------------------------------------------------------------------
# 6. transfer_ownership（§8.2）
# ---------------------------------------------------------------------------


class TestTransferOwnership:
    def test_updates_documents_courses_and_groups(self):
        session = FakeSession(group_ids=[_GROUP],
                              rowcounts={"documents": 5, "learning_courses": 2})
        result = account_lifecycle.transfer_ownership(session, _UID, _DEST)
        assert result == {"documents": 5, "courses": 2, "groups": 1}

    def test_updates_both_course_owner_columns(self):
        """learning_courses.owner_id は認可未使用の実列。黙った不整合を残さない。"""
        session = FakeSession(group_ids=[])
        account_lifecycle.transfer_ownership(session, _UID, _DEST)
        course_sql = next(sql for sql in session.sqls() if sql.startswith("UPDATE learning_courses"))
        assert "user_id = CAST(:dst AS uuid)" in course_sql
        assert "owner_id = CAST(:dst AS uuid)" in course_sql

    def test_guarantees_destination_is_group_admin(self):
        session = FakeSession(group_ids=[_GROUP])
        account_lifecycle.transfer_ownership(session, _UID, _DEST)
        insert_sql, params = next(
            (sql, p) for sql, p in session.calls if sql.startswith("INSERT INTO group_members")
        )
        assert "ON CONFLICT (group_id, user_id) DO UPDATE SET role = 'admin'" in insert_sql
        assert params == {"gid": _GROUP, "dst": _DEST}

    def test_skips_group_update_when_no_groups_owned(self):
        session = FakeSession(group_ids=[])
        account_lifecycle.transfer_ownership(session, _UID, _DEST)
        assert not any(sql.startswith("UPDATE groups") for sql in session.sqls())
        assert session.inserted_tables() == []

    def test_does_not_commit_or_close_caller_session(self):
        session = FakeSession(group_ids=[_GROUP])
        account_lifecycle.transfer_ownership(session, _UID, _DEST)
        assert session.commits == 0
        assert session.closed == 0

    def test_does_not_touch_published_by(self):
        """不変 Release の帰属（shared_versions.published_by）は付け替えない。"""
        session = FakeSession(group_ids=[])
        account_lifecycle.transfer_ownership(session, _UID, _DEST)
        assert not any("shared_versions" in sql for sql in session.sqls())


# ---------------------------------------------------------------------------
# 7. スイーパへの相乗り
# ---------------------------------------------------------------------------


class TestSweeperIntegration:
    def test_sweep_once_counts_user_purges(self, monkeypatch):
        from core.versioning import worker

        monkeypatch.setattr(worker, "_due_objects", lambda: [])
        monkeypatch.setattr(
            worker.account_lifecycle, "due_users",
            lambda _session: [account_lifecycle.DueUser(_UID, _ADMIN_A)],
        )
        monkeypatch.setattr(worker, "get_session", lambda: FakeSession())
        purged: list[str] = []

        def _purge(uid, *, scheduled_by=None):
            purged.append(uid)
            return True

        monkeypatch.setattr(worker.account_lifecycle, "purge_user", _purge)
        assert worker.sweep_once() == 1
        assert purged == [_UID]

    def test_one_user_failure_does_not_stop_the_pass(self, monkeypatch):
        from core.versioning import worker

        monkeypatch.setattr(worker, "_due_objects", lambda: [])
        monkeypatch.setattr(worker, "get_session", lambda: FakeSession())
        monkeypatch.setattr(
            worker.account_lifecycle, "due_users",
            lambda _session: [
                account_lifecycle.DueUser("u1", None),
                account_lifecycle.DueUser("u2", None),
            ],
        )

        def _purge(uid, *, scheduled_by=None):
            if uid == "u1":
                raise RuntimeError("boom")
            return True

        monkeypatch.setattr(worker.account_lifecycle, "purge_user", _purge)
        assert worker.sweep_once() == 1

    def test_due_users_failure_degrades_to_empty(self, monkeypatch):
        from core.versioning import worker

        monkeypatch.setattr(worker, "_due_objects", lambda: [])
        monkeypatch.setattr(worker, "get_session", lambda: FakeSession())

        def _boom(_session):
            raise RuntimeError("no table")

        monkeypatch.setattr(worker.account_lifecycle, "due_users", _boom)
        assert worker.sweep_once() == 0  # V層 sweep を止めない

    def test_user_pass_failure_keeps_object_purge_count(self, monkeypatch):
        from core.versioning import worker

        monkeypatch.setattr(worker, "_due_objects", lambda: [("course", "c1", None)])
        monkeypatch.setattr(worker, "_purge_one", lambda *a, **k: True)

        def _boom():
            raise RuntimeError("boom")

        monkeypatch.setattr(worker, "_sweep_users", _boom)
        assert worker.sweep_once() == 1

    def test_object_type_vocabulary_is_untouched(self):
        """V層の意味論を汚さない: object_type に 'user' を足していない。"""
        from core.versioning import schema as v_schema

        assert v_schema.OBJECT_TYPES == ("course", "document")
        assert "user" not in v_schema.OBJECT_TYPES
