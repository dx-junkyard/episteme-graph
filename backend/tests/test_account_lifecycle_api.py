"""アカウントライフサイクル管理 — 管理 API（``api/routes/admin.py`` の User Management 節）。

正本: ``docs/features/account_lifecycle_management_design.md`` §5（API 設計）/ §6（停止の
セマンティクス）/ §7（利用実績照会）/ §8（削除）。

``tests/test_landscape_api.py`` の流儀（実 app + ``TestClient`` + ``_pg_session`` の
フェイク差し替え）を踏襲する。DB には接続しない。

検証観点:
  1. 一覧の TEACHER fail-closed（role パラメータを無視して learner 固定 = AL7）
  2. **対象ロールを DB から読んで**権限判定する（TEACHER が教員を停止できない）
  3. AL10 ロックアウト防止（自分自身 / Administrator は 422）
  4. 状態遷移の前提（active→suspended / suspended→active / suspended→pending_deletion）
  5. AL3 停止・再開・リセットのあとに account_status キャッシュを破棄する
  6. AL4 平文・ハッシュを監査 metadata・レスポンスに載せない
  7. パスワードリセットは SYSTEM_ADMIN のみ・token_generation++・auth_events 記録
  8. 個票は SYSTEM_ADMIN のみ・LLM 集計は fail-soft
  9. 移管の前提（active な教員・管理者のみ）と件数レスポンス
 10. 404 統一（不在・不正な UUID とも）/ email 重複の 409
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

try:
    import fastapi  # noqa: F401
    _HAS_FASTAPI = True
except ImportError:  # pragma: no cover
    _HAS_FASTAPI = False

pytestmark = pytest.mark.skipif(not _HAS_FASTAPI, reason="FastAPI not installed")

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _path in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, str(_path))

_ADMIN = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_TEACHER = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
_STUDENT = "cccccccc-cccc-cccc-cccc-cccccccccccc"
_OTHER_TEACHER = "dddddddd-dddd-dddd-dddd-dddddddddddd"
_BOOTSTRAP = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
_MISSING = "ffffffff-ffff-ffff-ffff-ffffffffffff"


# ---------------------------------------------------------------------------
# フェイク行 / フェイクセッション
# ---------------------------------------------------------------------------


def _user(uid, name, *, email=None, role="learner", status="active",
          reason="", purge_after=None):
    """`_USER_COLUMNS_SQL` の列順に一致するタプル。"""
    return (
        uid, name, email if email is not None else f"{name}@example.com", role, status,
        "2026-08-01T00:00:00+00:00",  # created_at
        "2026-08-20T10:00:00+00:00",  # last_login_at
        "2026-08-20T10:30:00+00:00",  # last_seen_at
        reason, purge_after,
    )


_DEFAULT_USERS = {
    _STUDENT: _user(_STUDENT, "gakusei", role="learner"),
    _TEACHER: _user(_TEACHER, "kyoin", role="instructor"),
    _OTHER_TEACHER: _user(_OTHER_TEACHER, "kyoin2", role="instructor"),
    _ADMIN: _user(_ADMIN, "kanri", role="admin"),
    _BOOTSTRAP: _user(_BOOTSTRAP, "Administrator", role="admin"),
}


class _Result:
    def __init__(self, rows=(), rowcount: int = 0):
        self._rows = list(rows)
        self.rowcount = rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeSession:
    def __init__(self, users=None, *, auth_events=(), name_taken=False, email_taken=False,
                 auth_events_raise=False):
        self.users = dict(users if users is not None else _DEFAULT_USERS)
        self.auth_events = list(auth_events)
        self.name_taken = name_taken
        self.email_taken = email_taken
        self.auth_events_raise = auth_events_raise
        self.calls: list[tuple[str, dict]] = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = 0

    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        params = dict(params or {})
        self.calls.append((sql, params))

        if sql.startswith("SELECT id FROM users WHERE display_name"):
            return _Result([("x",)] if self.name_taken else [])
        if sql.startswith("SELECT id FROM users WHERE email"):
            return _Result([("x",)] if self.email_taken else [])
        if sql.startswith("SELECT COUNT(*) FROM users"):
            return _Result([(len(self._filtered(params, sql)),)])
        if "FROM users WHERE id = CAST(:uid AS uuid)" in sql:
            row = self.users.get(str(params.get("uid")))
            return _Result([row] if row else [])
        if sql.startswith("SELECT id::text, display_name"):
            return _Result(self._filtered(params, sql))
        if sql.startswith("UPDATE users SET status"):
            return _Result(rowcount=1)
        if sql.startswith("UPDATE users SET password_hash"):
            return _Result([("2026-08-23T12:00:00+00:00",)], rowcount=1)
        if "FROM auth_events" in sql:
            if self.auth_events_raise:
                raise RuntimeError("bad cursor")
            return _Result(self.auth_events)
        if sql.startswith("INSERT INTO auth_events"):
            return _Result(rowcount=1)
        if sql.startswith("INSERT INTO users"):
            return _Result(rowcount=1)
        return _Result()

    def _filtered(self, params, sql):
        rows = list(self.users.values())
        if "role = :role" in sql:
            rows = [r for r in rows if r[3] == params.get("role")]
        if "status = :status" in sql:
            rows = [r for r in rows if r[4] == params.get("status")]
        return rows

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed += 1

    # --- 検査用 ---
    def sqls(self):
        return [sql for sql, _p in self.calls]

    def params_for(self, prefix):
        for sql, params in self.calls:
            if sql.startswith(prefix):
                return params
        return None


@pytest.fixture
def env(monkeypatch):
    """TestClient + フェイクセッション + 監査 / キャッシュ / 認証イベントの記録。"""
    from fastapi.testclient import TestClient
    from api.main import app
    from dependencies import ROLE_STUDENT, ROLE_SYSTEM_ADMIN, ROLE_TEACHER, _create_token
    import routes.admin as routes

    state: dict = {
        "session": FakeSession(),
        "audits": [],
        "invalidated": [],
        "auth_events": [],
        "transfers": [],
    }
    monkeypatch.setattr(routes, "_pg_session", lambda: state["session"])
    monkeypatch.setattr(
        routes, "record_review_event",
        lambda *args: state["audits"].append(args),
    )
    monkeypatch.setattr(
        routes.account_status, "invalidate",
        lambda uid: state["invalidated"].append(str(uid)),
    )
    monkeypatch.setattr(routes, "_hash_password", lambda pw: f"hashed::{len(pw)}")

    def _record_auth_event(session=None, **kwargs):
        state["auth_events"].append(kwargs)
        return True

    monkeypatch.setattr(routes.auth_events_module, "record_auth_event", _record_auth_event)

    def _transfer(session, src, dst):
        state["transfers"].append((src, dst))
        return {"documents": 3, "courses": 2, "groups": 1}

    monkeypatch.setattr(routes.account_lifecycle, "transfer_ownership", _transfer)

    state["client"] = TestClient(app)
    state["routes"] = routes
    state["monkeypatch"] = monkeypatch
    state["tokens"] = {
        "admin": _create_token(_ADMIN, "kanri", "kanri@x", ROLE_SYSTEM_ADMIN),
        "teacher": _create_token(_TEACHER, "kyoin", "kyoin@x", ROLE_TEACHER),
        "student": _create_token(_STUDENT, "gakusei", "g@x", ROLE_STUDENT),
    }
    return state


def _auth(env, who):
    return {"Authorization": "Bearer " + env["tokens"][who]}


# ---------------------------------------------------------------------------
# 1. 一覧（GET /api/admin/users）
# ---------------------------------------------------------------------------


class TestListUsers:
    def test_student_is_forbidden(self, env):
        res = env["client"].get("/api/admin/users", headers=_auth(env, "student"))
        assert res.status_code == 403

    def test_teacher_is_forced_to_learner_even_when_role_is_passed(self, env):
        """AL7 fail-closed: TEACHER の role パラメータは採用しない。"""
        res = env["client"].get(
            "/api/admin/users?role=admin", headers=_auth(env, "teacher"),
        )
        assert res.status_code == 200
        params = env["session"].params_for("SELECT id::text, display_name")
        assert params["role"] == "learner"
        roles = {u["role"] for u in res.json()["users"]}
        assert roles == {"STUDENT"}

    def test_system_admin_sees_all_roles_by_default(self, env):
        res = env["client"].get("/api/admin/users", headers=_auth(env, "admin"))
        assert res.status_code == 200
        body = res.json()
        assert body["total"] == len(_DEFAULT_USERS)
        assert {"STUDENT", "TEACHER", "SYSTEM_ADMIN"} == {u["role"] for u in body["users"]}

    def test_system_admin_can_filter_by_role(self, env):
        res = env["client"].get(
            "/api/admin/users?role=instructor", headers=_auth(env, "admin"),
        )
        assert res.status_code == 200
        assert {u["role"] for u in res.json()["users"]} == {"TEACHER"}

    def test_invalid_role_is_422(self, env):
        res = env["client"].get("/api/admin/users?role=hacker", headers=_auth(env, "admin"))
        assert res.status_code == 422

    def test_invalid_status_is_422(self, env):
        res = env["client"].get("/api/admin/users?status=zombie", headers=_auth(env, "admin"))
        assert res.status_code == 422

    def test_status_filter_is_passed_through(self, env):
        res = env["client"].get(
            "/api/admin/users?status=suspended", headers=_auth(env, "admin"),
        )
        assert res.status_code == 200
        assert env["session"].params_for("SELECT id::text, display_name")["status"] == "suspended"

    def test_keyword_search_uses_ilike_on_name_and_email(self, env):
        env["client"].get("/api/admin/users?q=yama", headers=_auth(env, "admin"))
        sql = next(s for s in env["session"].sqls() if s.startswith("SELECT id::text, display_name"))
        assert "display_name ILIKE :kw" in sql and "email ILIKE :kw" in sql
        assert env["session"].params_for("SELECT id::text, display_name")["kw"] == "%yama%"

    def test_limit_is_clamped_to_max(self, env):
        env["client"].get("/api/admin/users?limit=9999", headers=_auth(env, "admin"))
        assert env["session"].params_for("SELECT id::text, display_name")["limit"] == 200

    def test_limit_zero_falls_back_to_default_and_offset_has_a_floor(self, env):
        """limit=0 は「未指定」として既定 50 に落ち、offset は 0 未満にならない。"""
        env["client"].get("/api/admin/users?limit=0&offset=-5", headers=_auth(env, "admin"))
        params = env["session"].params_for("SELECT id::text, display_name")
        assert params["limit"] == 50 and params["offset"] == 0

    def test_negative_limit_has_a_floor(self, env):
        env["client"].get("/api/admin/users?limit=-3", headers=_auth(env, "admin"))
        assert env["session"].params_for("SELECT id::text, display_name")["limit"] == 1

    def test_row_shape_is_the_frontend_contract(self, env):
        res = env["client"].get("/api/admin/users?role=learner", headers=_auth(env, "admin"))
        row = res.json()["users"][0]
        assert set(row) == {
            "id", "username", "email", "role", "status",
            "created_at", "last_login_at", "last_seen_at",
        }
        # username は display_name、role はアプリ語彙（DB 語彙を外に出さない）
        assert row["username"] == "gakusei"
        assert row["role"] == "STUDENT"

    def test_list_does_not_leak_status_reason(self, env):
        """停止理由は一覧に載せない（個票 activity のみ）。"""
        res = env["client"].get("/api/admin/users", headers=_auth(env, "admin"))
        assert all("status_reason" not in row for row in res.json()["users"])


# ---------------------------------------------------------------------------
# 2. 停止（suspend）
# ---------------------------------------------------------------------------


class TestSuspend:
    def _post(self, env, uid, who="admin", reason="不正利用の疑い"):
        return env["client"].post(
            f"/api/admin/users/{uid}/suspend",
            json={"reason": reason}, headers=_auth(env, who),
        )

    def test_student_is_forbidden(self, env):
        assert self._post(env, _STUDENT, who="student").status_code == 403

    def test_empty_reason_is_422(self, env):
        res = self._post(env, _STUDENT, reason="   ")
        assert res.status_code == 422
        assert "理由" in res.json()["detail"]

    def test_teacher_can_suspend_a_student(self, env):
        res = self._post(env, _STUDENT, who="teacher")
        assert res.status_code == 200
        assert res.json()["status"] == "suspended"

    def test_teacher_cannot_suspend_an_instructor(self, env):
        """対象ロールは DB から読む（リクエストで偽れない fail-closed）。"""
        res = self._post(env, _OTHER_TEACHER, who="teacher")
        assert res.status_code == 403
        assert "users" not in [s.split()[1] for s in env["session"].sqls() if s.startswith("UPDATE")]

    def test_system_admin_can_suspend_an_instructor(self, env):
        assert self._post(env, _OTHER_TEACHER, who="admin").status_code == 200

    def test_self_suspension_is_422(self, env):
        res = self._post(env, _ADMIN, who="admin")
        assert res.status_code == 422
        assert "自分自身" in res.json()["detail"]

    def test_bootstrap_administrator_is_422(self, env):
        res = self._post(env, _BOOTSTRAP, who="admin")
        assert res.status_code == 422
        assert "Administrator" in res.json()["detail"]

    def test_already_suspended_is_422(self, env):
        env["session"].users[_STUDENT] = _user(_STUDENT, "gakusei", status="suspended")
        assert self._post(env, _STUDENT).status_code == 422

    def test_pending_deletion_cannot_be_suspended_again(self, env):
        env["session"].users[_STUDENT] = _user(_STUDENT, "gakusei", status="pending_deletion")
        assert self._post(env, _STUDENT).status_code == 422

    def test_only_status_columns_are_updated(self, env):
        """AL2: 所有権・可視性・グループ・受講状態を触らない。"""
        self._post(env, _STUDENT)
        update_sql = next(s for s in env["session"].sqls() if s.startswith("UPDATE users"))
        for forbidden in ("uploaded_by", "learning_courses", "object_group_permissions",
                          "group_members", "learning_states", "visibility"):
            assert forbidden not in update_sql
        assert "status = :status" in update_sql

    def test_invalidates_status_cache(self, env):
        self._post(env, _STUDENT)
        assert env["invalidated"] == [_STUDENT]

    def test_audit_uses_catalog_entity_and_records_reason(self, env):
        from core.schema import AUDIT_ENTITY_USER_ACCOUNT

        self._post(env, _STUDENT, reason="共有アカウントの使用")
        entity_type, entity_id, old, new, actor, metadata = env["audits"][0]
        assert entity_type == AUDIT_ENTITY_USER_ACCOUNT
        assert entity_id == _STUDENT
        assert (old, new) == ("active", "suspended")
        assert actor == _ADMIN
        assert metadata["action"] == "suspend"
        assert metadata["reason"] == "共有アカウントの使用"
        assert metadata["target_role"] == "learner"


# ---------------------------------------------------------------------------
# 3. 再開（restore）
# ---------------------------------------------------------------------------


class TestRestore:
    def _post(self, env, uid, who="admin"):
        return env["client"].post(
            f"/api/admin/users/{uid}/restore", headers=_auth(env, who),
        )

    def test_suspended_to_active(self, env):
        env["session"].users[_STUDENT] = _user(
            _STUDENT, "gakusei", status="suspended", reason="要確認",
        )
        res = self._post(env, _STUDENT, who="teacher")
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "active"
        assert body["status_reason"] == ""

    def test_active_account_is_422(self, env):
        assert self._post(env, _STUDENT).status_code == 422

    def test_pending_deletion_is_not_restorable(self, env):
        """削除予約は「再開」で黙って解除しない（取消 API 専用）。"""
        env["session"].users[_STUDENT] = _user(_STUDENT, "gakusei", status="pending_deletion")
        res = self._post(env, _STUDENT)
        assert res.status_code == 422
        assert "削除予約の取消" in res.json()["detail"]

    def test_teacher_cannot_restore_an_instructor(self, env):
        env["session"].users[_OTHER_TEACHER] = _user(
            _OTHER_TEACHER, "kyoin2", role="instructor", status="suspended",
        )
        assert self._post(env, _OTHER_TEACHER, who="teacher").status_code == 403

    def test_previous_reason_is_kept_in_audit(self, env):
        env["session"].users[_STUDENT] = _user(
            _STUDENT, "gakusei", status="suspended", reason="不正利用の疑い",
        )
        self._post(env, _STUDENT)
        _t, _i, old, new, _a, metadata = env["audits"][0]
        assert (old, new) == ("suspended", "active")
        assert metadata["previous_reason"] == "不正利用の疑い"

    def test_invalidates_status_cache(self, env):
        env["session"].users[_STUDENT] = _user(_STUDENT, "gakusei", status="suspended")
        self._post(env, _STUDENT)
        assert env["invalidated"] == [_STUDENT]


# ---------------------------------------------------------------------------
# 4. パスワードリセット
# ---------------------------------------------------------------------------


class TestPasswordReset:
    def _post(self, env, uid, who="admin", password="new-password-1"):
        return env["client"].post(
            f"/api/admin/users/{uid}/password-reset",
            json={"new_password": password}, headers=_auth(env, who),
        )

    def test_teacher_is_forbidden_even_for_a_student(self, env):
        """§14-1 裁定: 対象が学生でも SYSTEM_ADMIN のみ。"""
        assert self._post(env, _STUDENT, who="teacher").status_code == 403

    def test_short_password_is_422(self, env):
        res = self._post(env, _STUDENT, password="short")
        assert res.status_code == 422
        assert "8文字以上" in res.json()["detail"]

    def test_success_bumps_token_generation(self, env):
        res = self._post(env, _STUDENT)
        assert res.status_code == 200
        sql = next(s for s in env["session"].sqls() if s.startswith("UPDATE users SET password_hash"))
        assert "token_generation = token_generation + 1" in sql
        assert "password_updated_at = now()" in sql

    def test_records_password_reset_auth_event_without_credentials(self, env):
        self._post(env, _STUDENT)
        from core.auth_events import AUTH_EVENT_PASSWORD_RESET

        assert len(env["auth_events"]) == 1
        event = env["auth_events"][0]
        assert event["event"] == AUTH_EVENT_PASSWORD_RESET
        assert event["user_id"] == _STUDENT
        assert event["payload"] == {"actor_id": _ADMIN}
        dumped = repr(event)
        assert "new-password-1" not in dumped and "hashed::" not in dumped

    def test_audit_carries_no_credentials(self, env):
        self._post(env, _STUDENT)
        _t, _i, _o, _n, _a, metadata = env["audits"][0]
        assert metadata["action"] == "password_reset"
        dumped = repr(metadata)
        assert "new-password-1" not in dumped and "hashed::" not in dumped
        assert not any("password" in key for key in metadata if key != "action")

    def test_response_never_returns_the_secret(self, env):
        body = self._post(env, _STUDENT).json()
        assert set(body) == {"id", "username", "password_updated_at", "self_reset"}
        assert "new-password-1" not in repr(body)

    def test_self_reset_is_flagged(self, env):
        assert self._post(env, _ADMIN).json()["self_reset"] is True

    def test_other_reset_is_not_flagged(self, env):
        assert self._post(env, _STUDENT).json()["self_reset"] is False

    def test_bootstrap_administrator_can_be_reset(self, env):
        """AL10 が禁じるのは停止・削除・降格。リセットはロックアウトを作らない。"""
        assert self._post(env, _BOOTSTRAP).status_code == 200

    def test_invalidates_status_cache(self, env):
        self._post(env, _STUDENT)
        assert env["invalidated"] == [_STUDENT]


# ---------------------------------------------------------------------------
# 5. 個票（activity）
# ---------------------------------------------------------------------------


class TestActivity:
    def _get(self, env, uid, who="admin", query=""):
        return env["client"].get(
            f"/api/admin/users/{uid}/activity{query}", headers=_auth(env, who),
        )

    def _patch_usage(self, env, payload=None, raise_error=False):
        def _collect(session, **kwargs):
            if raise_error:
                raise RuntimeError("no such column")
            env.setdefault("usage_calls", []).append(kwargs)
            return payload or {"rows": []}

        env["monkeypatch"].setattr(
            env["routes"].llm_usage_metrics, "collect_metrics", _collect,
        )

    def test_teacher_is_forbidden(self, env):
        """AL7: 学生の個票も TEACHER には開示しない。"""
        self._patch_usage(env)
        assert self._get(env, _STUDENT, who="teacher").status_code == 403

    def test_returns_auth_events_newest_first(self, env):
        self._patch_usage(env)
        env["session"].auth_events = [
            ("login_success", "2026-08-20T10:00:00+00:00", "10.0.0.1", "Mozilla"),
            ("login_failed", "2026-08-19T09:00:00+00:00", None, None),
        ]
        body = self._get(env, _STUDENT).json()
        assert [e["event"] for e in body["auth_events"]] == ["login_success", "login_failed"]
        assert body["auth_events"][0]["ip_address"] == "10.0.0.1"
        sql = next(s for s in env["session"].sqls() if "FROM auth_events" in s)
        assert "ORDER BY created_at DESC" in sql

    def test_auth_events_do_not_expose_attempted_usernames(self, env):
        self._patch_usage(env)
        env["session"].auth_events = [("login_failed", "2026-08-19T09:00:00+00:00", None, None)]
        body = self._get(env, _STUDENT).json()
        assert set(body["auth_events"][0]) == {"event", "created_at", "ip_address", "user_agent"}

    def test_before_cursor_is_applied(self, env):
        self._patch_usage(env)
        self._get(env, _STUDENT, query="?before=2026-08-01T00:00:00Z")
        sql = next(s for s in env["session"].sqls() if "FROM auth_events" in s)
        assert "created_at < CAST(:before AS timestamptz)" in sql

    def test_limit_is_clamped_to_100(self, env):
        self._patch_usage(env)
        self._get(env, _STUDENT, query="?limit=5000")
        params = next(p for s, p in env["session"].calls if "FROM auth_events" in s)
        assert params["limit"] == 100

    def test_llm_usage_separates_reported_and_estimated(self, env):
        self._patch_usage(env, payload={"rows": [
            {"key": {"feature": "learning:chat"},
             "reported": {"prompt_tokens": 10, "completion_tokens": 5,
                          "total_tokens": 15, "calls": 2},
             "estimated": {"prompt_tokens": 0, "completion_tokens": 0,
                           "total_tokens": 0, "calls": 0}},
            {"key": {"feature": "pipeline:document_structure"},
             "reported": {"prompt_tokens": 0, "completion_tokens": 0,
                          "total_tokens": 0, "calls": 0},
             "estimated": {"prompt_tokens": 100, "completion_tokens": 50,
                           "total_tokens": 150, "calls": 3}},
        ]})
        usage = self._get(env, _STUDENT).json()["llm_usage"]
        assert usage["available"] is True
        assert usage["window_days"] == 30
        assert usage["reported"]["total_tokens"] == 15
        assert usage["estimated"]["total_tokens"] == 150
        # U1: 合算した単一の数値を作らない
        assert "total" not in usage and "combined" not in usage
        # 上位 feature は総量降順（内部の並べ替えキーのみ合算）
        assert [f["feature"] for f in usage["top_features"]] == [
            "pipeline:document_structure", "learning:chat",
        ]

    def test_llm_usage_is_scoped_to_the_target_user(self, env):
        self._patch_usage(env)
        self._get(env, _STUDENT)
        call = env["usage_calls"][0]
        assert call["user_id"] == _STUDENT
        assert call["group_by"] == ["feature"]

    def test_llm_usage_failure_degrades_softly(self, env):
        self._patch_usage(env, raise_error=True)
        body = self._get(env, _STUDENT).json()
        assert body["llm_usage"] == {"available": False}
        assert body["user"]["id"] == _STUDENT  # 個票そのものは返る

    def test_auth_events_failure_does_not_break_the_view(self, env):
        self._patch_usage(env)
        env["session"].auth_events_raise = True
        body = self._get(env, _STUDENT).json()
        assert body["auth_events"] == []
        assert body["user"]["username"] == "gakusei"

    def test_user_header_includes_operational_columns(self, env):
        self._patch_usage(env)
        env["session"].users[_STUDENT] = _user(
            _STUDENT, "gakusei", status="pending_deletion", reason="退学",
            purge_after="2026-09-06T00:00:00+00:00",
        )
        user = self._get(env, _STUDENT).json()["user"]
        assert user["status"] == "pending_deletion"
        assert user["status_reason"] == "退学"
        assert user["purge_after"] == "2026-09-06T00:00:00+00:00"


# ---------------------------------------------------------------------------
# 6. 削除予約 / 取消
# ---------------------------------------------------------------------------


class TestDeletionScheduling:
    def _post(self, env, uid, who="admin", body=None):
        return env["client"].post(
            f"/api/admin/users/{uid}/deletion", json=body or {}, headers=_auth(env, who),
        )

    def _delete(self, env, uid, who="admin"):
        return env["client"].delete(
            f"/api/admin/users/{uid}/deletion", headers=_auth(env, who),
        )

    def _suspended(self, env, uid=_STUDENT, **kwargs):
        env["session"].users[uid] = _user(uid, "gakusei", status="suspended", **kwargs)

    def test_teacher_is_forbidden(self, env):
        assert self._post(env, _STUDENT, who="teacher").status_code == 403

    def test_active_account_cannot_be_scheduled(self, env):
        res = self._post(env, _STUDENT)
        assert res.status_code == 422
        assert "停止中" in res.json()["detail"]

    def test_suspended_account_gets_purge_after(self, env):
        self._suspended(env)
        res = self._post(env, _STUDENT)
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "pending_deletion"
        assert body["purge_after"]

    def test_default_grace_days_comes_from_versioning_layer(self, env):
        from core.versioning.schema import DEFAULT_GRACE_DAYS

        self._suspended(env)
        self._post(env, _STUDENT)
        _t, _i, _o, _n, _a, metadata = env["audits"][0]
        assert metadata["grace_days"] == DEFAULT_GRACE_DAYS == 14

    def test_explicit_grace_days_is_honored(self, env):
        self._suspended(env)
        self._post(env, _STUDENT, body={"grace_days": 30})
        _t, _i, _o, _n, _a, metadata = env["audits"][0]
        assert metadata["grace_days"] == 30

    @pytest.mark.parametrize("days", [0, -1, 400])
    def test_out_of_range_grace_days_is_422(self, env, days):
        self._suspended(env)
        assert self._post(env, _STUDENT, body={"grace_days": days}).status_code == 422

    def test_self_deletion_is_422(self, env):
        env["session"].users[_ADMIN] = _user(_ADMIN, "kanri", role="admin", status="suspended")
        assert self._post(env, _ADMIN).status_code == 422

    def test_bootstrap_deletion_is_422(self, env):
        env["session"].users[_BOOTSTRAP] = _user(
            _BOOTSTRAP, "Administrator", role="admin", status="suspended",
        )
        assert self._post(env, _BOOTSTRAP).status_code == 422

    def test_cancel_returns_to_suspended(self, env):
        env["session"].users[_STUDENT] = _user(
            _STUDENT, "gakusei", status="pending_deletion",
            purge_after="2026-09-06T00:00:00+00:00",
        )
        res = self._delete(env, _STUDENT)
        assert res.status_code == 200
        assert res.json()["status"] == "suspended"
        assert res.json()["purge_after"] is None

    def test_cancel_on_non_pending_is_422(self, env):
        self._suspended(env)
        assert self._delete(env, _STUDENT).status_code == 422

    def test_cancel_is_forbidden_for_teacher(self, env):
        assert self._delete(env, _STUDENT, who="teacher").status_code == 403

    def test_schedule_does_not_touch_owned_objects(self, env):
        self._suspended(env)
        self._post(env, _STUDENT)
        for sql in env["session"].sqls():
            assert "documents" not in sql
            assert "learning_courses" not in sql


# ---------------------------------------------------------------------------
# 7. 移管
# ---------------------------------------------------------------------------


class TestTransferOwnership:
    def _post(self, env, uid, to_user_id, who="admin"):
        return env["client"].post(
            f"/api/admin/users/{uid}/transfer-ownership",
            json={"to_user_id": to_user_id}, headers=_auth(env, who),
        )

    def test_teacher_is_forbidden(self, env):
        assert self._post(env, _TEACHER, _OTHER_TEACHER, who="teacher").status_code == 403

    def test_success_returns_counts(self, env):
        res = self._post(env, _TEACHER, _OTHER_TEACHER)
        assert res.status_code == 200
        body = res.json()
        assert body["transferred"] == {"documents": 3, "courses": 2, "groups": 1}
        assert body["to_user_id"] == _OTHER_TEACHER
        assert env["transfers"] == [(_TEACHER, _OTHER_TEACHER)]

    def test_destination_must_not_be_a_learner(self, env):
        res = self._post(env, _TEACHER, _STUDENT)
        assert res.status_code == 422
        assert "教員または管理者" in res.json()["detail"]

    def test_destination_must_be_active(self, env):
        env["session"].users[_OTHER_TEACHER] = _user(
            _OTHER_TEACHER, "kyoin2", role="instructor", status="suspended",
        )
        res = self._post(env, _TEACHER, _OTHER_TEACHER)
        assert res.status_code == 422
        assert "利用中" in res.json()["detail"]

    def test_destination_cannot_be_the_source(self, env):
        assert self._post(env, _TEACHER, _TEACHER).status_code == 422

    def test_missing_destination_is_404(self, env):
        assert self._post(env, _TEACHER, _MISSING).status_code == 404

    def test_audit_records_counts_and_destination(self, env):
        self._post(env, _TEACHER, _OTHER_TEACHER)
        _t, entity_id, _o, _n, _a, metadata = env["audits"][0]
        assert entity_id == _TEACHER
        assert metadata["action"] == "transfer_ownership"
        assert metadata["to_user_id"] == _OTHER_TEACHER
        assert metadata["transferred"] == {"documents": 3, "courses": 2, "groups": 1}

    def test_admin_destination_is_allowed(self, env):
        assert self._post(env, _TEACHER, _ADMIN).status_code == 200


# ---------------------------------------------------------------------------
# 8. 404 統一 / 作成 API の 409
# ---------------------------------------------------------------------------


class TestErrorContract:
    @pytest.mark.parametrize("path,method,body", [
        ("/suspend", "post", {"reason": "x"}),
        ("/restore", "post", None),
        ("/password-reset", "post", {"new_password": "abcdefgh"}),
        ("/deletion", "post", {}),
        ("/deletion", "delete", None),
    ])
    def test_missing_user_is_404(self, env, path, method, body):
        client = env["client"]
        url = f"/api/admin/users/{_MISSING}{path}"
        call = getattr(client, method)
        res = call(url, json=body, headers=_auth(env, "admin")) if body is not None \
            else call(url, headers=_auth(env, "admin"))
        assert res.status_code == 404

    def test_malformed_uuid_is_404_not_500(self, env):
        res = env["client"].post(
            "/api/admin/users/not-a-uuid/suspend",
            json={"reason": "x"}, headers=_auth(env, "admin"),
        )
        assert res.status_code == 404

    def test_duplicate_email_on_student_creation_is_409(self, env):
        env["session"].email_taken = True
        res = env["client"].post(
            "/api/admin/users/student",
            json={"username": "new", "email": "dup@example.com", "password": "pw12345678"},
            headers=_auth(env, "teacher"),
        )
        assert res.status_code == 409
        assert "Email" in res.json()["detail"]

    def test_duplicate_email_on_teacher_creation_is_409(self, env):
        env["session"].email_taken = True
        res = env["client"].post(
            "/api/admin/users/teacher",
            json={"username": "new", "email": "dup@example.com", "password": "pw12345678"},
            headers=_auth(env, "admin"),
        )
        assert res.status_code == 409

    def test_duplicate_username_still_409(self, env):
        env["session"].name_taken = True
        res = env["client"].post(
            "/api/admin/users/student",
            json={"username": "gakusei", "email": "x@example.com", "password": "pw12345678"},
            headers=_auth(env, "teacher"),
        )
        assert res.status_code == 409
        assert "Username" in res.json()["detail"]
