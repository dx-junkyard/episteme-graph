"""アカウントライフサイクル管理（AL層）— 認証側（Phase 0 + 1）のテスト。

正本: ``docs/features/account_lifecycle_management_design.md`` §3.2（auth_events）/
§4.1（ログインの判定順序）/ §4.2（トークン照合と TTL キャッシュ）/ §12（ガードレール）。

対象:
- ``backend/core/auth_events.py`` — event 語彙・IP 解決・payload サニタイズ・best-effort 記録
- ``backend/core/account_status.py`` — TTL キャッシュ付き状態照合・last_seen スロットル
- ``backend/api/dependencies.py`` — ``gen`` クレーム + 照合（非 active / 世代不一致 /
  行不在 → 401、DB 例外のみ fail-open）
- ``backend/api/routes/auth.py`` — 資格情報を status より先に検証する順序（列挙攻撃対策）
- ``backend/db/068_account_lifecycle.sql`` — DDL の構造（冪等イディオム・語彙一致）

実 DB には接続しない（fake session / monkeypatch）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import jwt
import pytest

# 他の backend/tests/*.py と同じ流儀で api/ を import 可能にする
_backend_dir = str(Path(__file__).resolve().parents[1])
_api_dir = str(Path(__file__).resolve().parents[1] / "api")
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)
if _api_dir not in sys.path:
    sys.path.insert(0, _api_dir)

from core import account_status, auth_events  # noqa: E402
from core.schema import AUDIT_ENTITY_TYPES, AUDIT_ENTITY_USER_ACCOUNT  # noqa: E402
from tests.guardrail_helpers import assert_source_forbids  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION_SQL = (_REPO_ROOT / "backend" / "db" / "068_account_lifecycle.sql").read_text(
    encoding="utf-8"
)
_AUTH_EVENTS_SRC = (_REPO_ROOT / "backend" / "core" / "auth_events.py").read_text(encoding="utf-8")
_ACCOUNT_STATUS_SRC = (_REPO_ROOT / "backend" / "core" / "account_status.py").read_text(
    encoding="utf-8"
)
_AUTH_ROUTE_SRC = (_REPO_ROOT / "backend" / "api" / "routes" / "auth.py").read_text(
    encoding="utf-8"
)
_DEPENDENCIES_SRC = (_REPO_ROOT / "backend" / "api" / "dependencies.py").read_text(
    encoding="utf-8"
)


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeSession:
    """execute の (sql, params) を記録する duck-typed fake session。"""

    def __init__(self, rows=None, fail_on: str | None = None):
        # rows: execute 呼び出し順に返す行（None も 1件として数える）
        self._rows = list(rows) if rows is not None else []
        self._idx = 0
        self._fail_on = fail_on
        self.executed: list[tuple[str, dict]] = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self.executed.append((sql, dict(params or {})))
        if self._fail_on is not None and self._fail_on in sql:
            raise RuntimeError("boom: forced db failure")
        row = None
        if self._idx < len(self._rows):
            row = self._rows[self._idx]
        self._idx += 1
        return _FakeResult(row)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


# conftest.py の autouse フィクスチャ `_default_account_status` は、認証状態を主題に
# しないテストのために get_account_state / touch_last_seen を差し替える。本ファイルは
# その照合自身を検証するため、import 時に捉えた**実装本体**へ戻す（conftest の
# フィクスチャより後に走るモジュール内 autouse フィクスチャで上書きする）。
_REAL_GET_ACCOUNT_STATE = account_status.get_account_state
_REAL_TOUCH_LAST_SEEN = account_status.touch_last_seen


@pytest.fixture(autouse=True)
def _real_account_status(monkeypatch):
    monkeypatch.setattr(account_status, "get_account_state", _REAL_GET_ACCOUNT_STATE)
    monkeypatch.setattr(account_status, "touch_last_seen", _REAL_TOUCH_LAST_SEEN)
    account_status.invalidate_all()
    yield
    account_status.invalidate_all()


# ---------------------------------------------------------------------------
# core/auth_events.py — 語彙
# ---------------------------------------------------------------------------


class TestAuthEventVocabulary:
    def test_vocabulary_matches_design_table(self):
        assert set(auth_events.AUTH_EVENTS) == {
            "login_success",
            "login_failed",
            "login_rejected_suspended",
            "token_rejected_suspended",
            "token_rejected_stale",
            "password_reset",
        }

    def test_constants_are_in_the_tuple(self):
        for value in (
            auth_events.AUTH_EVENT_LOGIN_SUCCESS,
            auth_events.AUTH_EVENT_LOGIN_FAILED,
            auth_events.AUTH_EVENT_LOGIN_REJECTED_SUSPENDED,
            auth_events.AUTH_EVENT_TOKEN_REJECTED_SUSPENDED,
            auth_events.AUTH_EVENT_TOKEN_REJECTED_STALE,
            auth_events.AUTH_EVENT_PASSWORD_RESET,
        ):
            assert value in auth_events.AUTH_EVENTS

    def test_unknown_event_raises_value_error(self):
        with pytest.raises(ValueError):
            auth_events.record_auth_event(_FakeSession(), event="not_a_real_event")


# ---------------------------------------------------------------------------
# core/auth_events.py — IP / UA 解決
# ---------------------------------------------------------------------------


class TestClientIpResolution:
    def test_prefers_x_real_ip(self):
        headers = {"X-Real-IP": "203.0.113.9", "X-Forwarded-For": "1.2.3.4, 203.0.113.9"}
        assert auth_events.client_ip_from_headers(headers) == "203.0.113.9"

    def test_falls_back_to_last_forwarded_for_element(self):
        # 先頭（1.2.3.4）はクライアントが偽装できるため使わない（設計書 §3.2）
        headers = {"X-Forwarded-For": "1.2.3.4, 10.0.0.1, 203.0.113.9"}
        assert auth_events.client_ip_from_headers(headers) == "203.0.113.9"

    def test_single_forwarded_for_element(self):
        assert auth_events.client_ip_from_headers({"X-Forwarded-For": "203.0.113.9"}) == "203.0.113.9"

    def test_lowercase_keys_supported(self):
        assert auth_events.client_ip_from_headers({"x-real-ip": "198.51.100.7"}) == "198.51.100.7"

    def test_none_and_empty(self):
        assert auth_events.client_ip_from_headers(None) is None
        assert auth_events.client_ip_from_headers({}) is None
        assert auth_events.client_ip_from_headers({"X-Real-IP": "   "}) is None

    def test_user_agent(self):
        assert auth_events.user_agent_from_headers({"user-agent": "curl/8"}) == "curl/8"
        assert auth_events.user_agent_from_headers({}) is None
        assert auth_events.user_agent_from_headers(None) is None

    def test_long_values_are_truncated(self):
        long_ua = "x" * 5000
        assert len(auth_events.user_agent_from_headers({"user-agent": long_ua})) <= 500


# ---------------------------------------------------------------------------
# core/auth_events.py — payload サニタイズ（AL4）
# ---------------------------------------------------------------------------


class TestPayloadSanitize:
    def test_drops_credential_keys(self):
        out = auth_events.sanitize_payload(
            {
                "status": "suspended",
                "password": "hunter2",
                "new_password": "hunter2",
                "password_hash": "$2b$12$abc",
                "PASSWORD": "hunter2",
                "access_token": "ey...",
                "secret": "s",
                "credentials": "c",
            }
        )
        assert out == {"status": "suspended"}

    def test_nested_values_are_stringified(self):
        out = auth_events.sanitize_payload({"nested": {"a": 1}})
        assert isinstance(out["nested"], str)

    def test_scalars_preserved(self):
        out = auth_events.sanitize_payload({"n": 3, "f": 1.5, "b": True, "none": None})
        assert out == {"n": 3, "f": 1.5, "b": True, "none": None}

    def test_empty(self):
        assert auth_events.sanitize_payload(None) == {}
        assert auth_events.sanitize_payload({}) == {}


# ---------------------------------------------------------------------------
# core/auth_events.py — 記録（best-effort）
# ---------------------------------------------------------------------------


class TestRecordAuthEvent:
    def test_shared_session_insert_without_commit(self):
        session = _FakeSession()
        ok = auth_events.record_auth_event(
            session,
            event=auth_events.AUTH_EVENT_LOGIN_SUCCESS,
            user_id="11111111-1111-1111-1111-111111111111",
            username_attempted="taro",
            ip_address="203.0.113.9",
            user_agent="curl/8",
        )
        assert ok is True
        sql, params = session.executed[0]
        assert "INSERT INTO auth_events" in sql
        assert params["event"] == "login_success"
        assert params["username_attempted"] == "taro"
        # 共有セッションは呼び出し側が commit する
        assert session.commits == 0

    def test_shared_session_failure_does_not_raise(self):
        session = _FakeSession(fail_on="INSERT INTO auth_events")
        ok = auth_events.record_auth_event(
            session, event=auth_events.AUTH_EVENT_LOGIN_FAILED, username_attempted="taro"
        )
        assert ok is False

    def test_own_session_commits_and_closes(self, monkeypatch):
        session = _FakeSession()
        monkeypatch.setattr("core.postgres.get_session", lambda: session)
        ok = auth_events.record_auth_event(event=auth_events.AUTH_EVENT_LOGIN_FAILED)
        assert ok is True
        assert session.commits == 1
        assert session.closed is True

    def test_own_session_failure_rolls_back_and_returns_false(self, monkeypatch):
        session = _FakeSession(fail_on="INSERT INTO auth_events")
        monkeypatch.setattr("core.postgres.get_session", lambda: session)
        ok = auth_events.record_auth_event(event=auth_events.AUTH_EVENT_LOGIN_FAILED)
        assert ok is False
        assert session.rollbacks == 1
        assert session.closed is True

    def test_db_unavailable_does_not_raise(self, monkeypatch):
        def _boom():
            raise RuntimeError("no db")

        monkeypatch.setattr("core.postgres.get_session", _boom)
        assert auth_events.record_auth_event(event=auth_events.AUTH_EVENT_LOGIN_FAILED) is False

    def test_payload_credentials_never_reach_sql_params(self):
        session = _FakeSession()
        auth_events.record_auth_event(
            session,
            event=auth_events.AUTH_EVENT_PASSWORD_RESET,
            user_id="11111111-1111-1111-1111-111111111111",
            payload={"target": "u1", "new_password": "hunter2"},
        )
        _sql, params = session.executed[0]
        assert "hunter2" not in params["payload"]
        assert "target" in params["payload"]


# ---------------------------------------------------------------------------
# core/account_status.py
# ---------------------------------------------------------------------------


class TestAccountStatus:
    def test_status_vocabulary_matches_migration_check(self):
        assert account_status.ACCOUNT_STATUSES == (
            "active",
            "suspended",
            "pending_deletion",
            "deleted",
        )
        for value in account_status.ACCOUNT_STATUSES:
            assert f"'{value}'" in _MIGRATION_SQL

    def test_fetches_and_caches(self, monkeypatch):
        calls = []

        def _factory():
            calls.append(1)
            return _FakeSession(rows=[("active", 3)])

        monkeypatch.setattr("core.postgres.get_session", _factory)
        state = account_status.get_account_state("u-1")
        assert state is not None
        assert state.status == "active"
        assert state.token_generation == 3
        assert state.is_active is True

        # 2回目はキャッシュから（DB を引かない）
        again = account_status.get_account_state("u-1")
        assert again == state
        assert len(calls) == 1

    def test_missing_row_returns_none_and_is_cached(self, monkeypatch):
        calls = []

        def _factory():
            calls.append(1)
            return _FakeSession(rows=[None])

        monkeypatch.setattr("core.postgres.get_session", _factory)
        assert account_status.get_account_state("u-missing") is None
        assert account_status.get_account_state("u-missing") is None
        assert len(calls) == 1

    def test_db_error_raises_unavailable_and_is_not_cached(self, monkeypatch):
        calls = []

        def _factory():
            calls.append(1)
            return _FakeSession(fail_on="FROM users")

        monkeypatch.setattr("core.postgres.get_session", _factory)
        with pytest.raises(account_status.AccountStatusUnavailable):
            account_status.get_account_state("u-2")
        with pytest.raises(account_status.AccountStatusUnavailable):
            account_status.get_account_state("u-2")
        assert len(calls) == 2

    def test_invalidate_forces_refetch(self, monkeypatch):
        rows = [("active", 0), ("suspended", 1)]
        calls = []

        def _factory():
            idx = len(calls)
            calls.append(1)
            return _FakeSession(rows=[rows[idx]])

        monkeypatch.setattr("core.postgres.get_session", _factory)
        assert account_status.get_account_state("u-3").status == "active"
        account_status.invalidate("u-3")
        assert account_status.get_account_state("u-3").status == "suspended"
        assert len(calls) == 2

    def test_empty_user_id_returns_none_without_db(self, monkeypatch):
        def _boom():
            raise AssertionError("must not touch the DB for an empty user_id")

        monkeypatch.setattr("core.postgres.get_session", _boom)
        assert account_status.get_account_state("") is None

    def test_ttl_default_is_thirty_seconds(self):
        assert account_status.CACHE_TTL_SECONDS == 30.0

    def test_last_seen_throttle_is_five_minutes(self):
        assert account_status.LAST_SEEN_THROTTLE_SECONDS == 300.0


class TestTouchLastSeen:
    def test_updates_then_throttles(self, monkeypatch):
        sessions = []

        def _factory():
            session = _FakeSession()
            sessions.append(session)
            return session

        monkeypatch.setattr("core.postgres.get_session", _factory)
        assert account_status.touch_last_seen("u-4") is True
        assert account_status.touch_last_seen("u-4") is False
        assert len(sessions) == 1
        sql, params = sessions[0].executed[0]
        assert "UPDATE users SET last_seen_at" in sql
        assert params["user_id"] == "u-4"
        assert sessions[0].commits == 1
        assert sessions[0].closed is True

    def test_failure_is_swallowed(self, monkeypatch):
        monkeypatch.setattr(
            "core.postgres.get_session", lambda: _FakeSession(fail_on="UPDATE users")
        )
        assert account_status.touch_last_seen("u-5") is False

    def test_empty_user_id_is_noop(self, monkeypatch):
        monkeypatch.setattr(
            "core.postgres.get_session",
            lambda: (_ for _ in ()).throw(AssertionError("must not touch DB")),
        )
        assert account_status.touch_last_seen("") is False


# ---------------------------------------------------------------------------
# api/dependencies.py — トークン照合（§4.2）
# ---------------------------------------------------------------------------

_USER_ID = "11111111-1111-1111-1111-111111111111"


def _token(gen: int = 0):
    from api.dependencies import ROLE_TEACHER, _create_token

    return _create_token(_USER_ID, "sensei", "sensei@example.com", ROLE_TEACHER, gen=gen)


class _Credentials:
    def __init__(self, token: str):
        self.credentials = token


class _FakeRequest:
    def __init__(self, headers=None):
        self.headers = headers or {}


class TestCreateTokenGenClaim:
    def test_gen_claim_present(self):
        payload = jwt.decode(_token(gen=7), "test-secret-key", algorithms=["HS256"])
        assert payload["gen"] == 7

    def test_gen_defaults_to_zero(self):
        payload = jwt.decode(_token(), "test-secret-key", algorithms=["HS256"])
        assert payload["gen"] == 0


class TestGetCurrentUserVerification:
    @staticmethod
    def _call(monkeypatch, *, state=None, error=False, gen=0, recorded=None):
        from api import dependencies as deps

        if error:
            def _state(_user_id):
                raise account_status.AccountStatusUnavailable("no db")
        else:
            def _state(_user_id):
                return state

        monkeypatch.setattr(deps._account_status, "get_account_state", _state)
        monkeypatch.setattr(deps._account_status, "touch_last_seen", lambda uid: recorded is not None and recorded.append(("touch", uid)) is None)

        def _record(**kwargs):
            if recorded is not None:
                recorded.append(("event", kwargs.get("event")))
            return True

        monkeypatch.setattr(deps._auth_events, "record_auth_event", _record)
        return deps._get_current_user(_FakeRequest(), _Credentials(_token(gen=gen)))

    def test_active_matching_generation_passes(self, monkeypatch):
        recorded: list = []
        state = account_status.AccountState(user_id=_USER_ID, status="active", token_generation=2)
        user = self._call(monkeypatch, state=state, gen=2, recorded=recorded)
        assert user["id"] == _USER_ID
        assert user["role"] == "TEACHER"
        assert ("touch", _USER_ID) in recorded
        assert not [item for item in recorded if item[0] == "event"]

    def test_suspended_rejected_401(self, monkeypatch):
        from fastapi import HTTPException

        recorded: list = []
        state = account_status.AccountState(user_id=_USER_ID, status="suspended", token_generation=0)
        with pytest.raises(HTTPException) as exc:
            self._call(monkeypatch, state=state, gen=0, recorded=recorded)
        assert exc.value.status_code == 401
        assert ("event", "token_rejected_suspended") in recorded

    @pytest.mark.parametrize("status", ["pending_deletion", "deleted"])
    def test_non_active_statuses_rejected(self, monkeypatch, status):
        from fastapi import HTTPException

        state = account_status.AccountState(user_id=_USER_ID, status=status, token_generation=0)
        with pytest.raises(HTTPException) as exc:
            self._call(monkeypatch, state=state, gen=0)
        assert exc.value.status_code == 401

    def test_generation_mismatch_rejected_401(self, monkeypatch):
        from fastapi import HTTPException

        recorded: list = []
        state = account_status.AccountState(user_id=_USER_ID, status="active", token_generation=5)
        with pytest.raises(HTTPException) as exc:
            self._call(monkeypatch, state=state, gen=4, recorded=recorded)
        assert exc.value.status_code == 401
        assert ("event", "token_rejected_stale") in recorded

    def test_legacy_token_without_gen_matches_generation_zero(self, monkeypatch):
        """`gen` クレームの無い旧トークンは gen=0 とみなす（列の初期値と一致 = 後方互換）。"""
        from api import dependencies as deps

        state = account_status.AccountState(user_id=_USER_ID, status="active", token_generation=0)
        monkeypatch.setattr(deps._account_status, "get_account_state", lambda _uid: state)
        monkeypatch.setattr(deps._account_status, "touch_last_seen", lambda _uid: False)

        legacy = jwt.encode(
            {
                "sub": _USER_ID,
                "username": "sensei",
                "email": "sensei@example.com",
                "role": "TEACHER",
            },
            "test-secret-key",
            algorithm="HS256",
        )
        user = deps._get_current_user(_FakeRequest(), _Credentials(legacy))
        assert user["id"] == _USER_ID

    def test_missing_row_rejected_401(self, monkeypatch):
        from fastapi import HTTPException

        recorded: list = []
        with pytest.raises(HTTPException) as exc:
            self._call(monkeypatch, state=None, gen=0, recorded=recorded)
        assert exc.value.status_code == 401
        assert ("event", "token_rejected_stale") in recorded

    def test_db_error_fails_open(self, monkeypatch):
        recorded: list = []
        user = self._call(monkeypatch, error=True, gen=9, recorded=recorded)
        # DB 例外のときだけ payload だけで通す（設計書 §4.2-3）
        assert user["id"] == _USER_ID
        assert not [item for item in recorded if item[0] == "event"]

    def test_invalid_signature_still_401_before_any_db_access(self, monkeypatch):
        from fastapi import HTTPException

        from api import dependencies as deps

        def _boom(_uid):
            raise AssertionError("must not check account status for an invalid token")

        monkeypatch.setattr(deps._account_status, "get_account_state", _boom)
        with pytest.raises(HTTPException) as exc:
            deps._get_current_user(_FakeRequest(), _Credentials("not-a-jwt"))
        assert exc.value.status_code == 401


class TestGetCurrentUserViaFastApi:
    """FastAPI の依存解決経路で照合が効くこと（``Request`` の注入も含む）。"""

    @pytest.fixture
    def client(self):
        fastapi_testclient = pytest.importorskip("fastapi.testclient")
        from api.main import app

        return fastapi_testclient.TestClient(app)

    def test_suspended_token_is_401_and_records_client_ip(self, client, monkeypatch):
        from api import dependencies as deps

        state = account_status.AccountState(
            user_id=_USER_ID, status="suspended", token_generation=0
        )
        monkeypatch.setattr(deps._account_status, "get_account_state", lambda _uid: state)

        recorded: list = []

        def _record(**kwargs):
            recorded.append(kwargs)
            return True

        monkeypatch.setattr(deps._auth_events, "record_auth_event", _record)

        resp = client.get(
            "/api/auth/me",
            headers={
                "Authorization": f"Bearer {_token()}",
                "X-Real-IP": "203.0.113.9",
                "User-Agent": "curl/8",
            },
        )
        assert resp.status_code == 401
        assert recorded[0]["event"] == "token_rejected_suspended"
        assert recorded[0]["user_id"] == _USER_ID
        assert recorded[0]["ip_address"] == "203.0.113.9"
        assert recorded[0]["user_agent"] == "curl/8"

    def test_active_token_passes_through_dependency(self, client, monkeypatch):
        from api import dependencies as deps

        state = account_status.AccountState(user_id=_USER_ID, status="active", token_generation=0)
        monkeypatch.setattr(deps._account_status, "get_account_state", lambda _uid: state)
        monkeypatch.setattr(deps._account_status, "touch_last_seen", lambda _uid: False)

        resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {_token()}"})
        assert resp.status_code == 200
        assert resp.json()["id"] == _USER_ID


# ---------------------------------------------------------------------------
# api/routes/auth.py — ログインの判定順序（§4.1・§12-12）
# ---------------------------------------------------------------------------


class _LoginBody:
    def __init__(self, username="taro", password="hunter2"):
        self.username = username
        self.password = password


def _login_with(monkeypatch, row, *, verify=True, headers=None):
    """auth_login を fake session / fake verifier で呼び、記録されたイベントを返す。"""
    import routes.auth as auth_route

    read_session = _FakeSession(rows=[row])
    write_session = _FakeSession()
    sessions = [read_session, write_session]

    def _factory():
        return sessions.pop(0) if sessions else _FakeSession()

    verify_calls: list = []

    def _verify(plain, hashed):
        verify_calls.append((plain, hashed))
        return verify

    events: list = []

    def _record(session=None, **kwargs):
        events.append(kwargs.get("event"))
        return True

    monkeypatch.setattr(auth_route, "_pg_session", _factory)
    monkeypatch.setattr(auth_route, "_verify_password", _verify)
    monkeypatch.setattr(auth_route._auth_events, "record_auth_event", _record)

    result = None
    error = None
    try:
        result = auth_route.auth_login(_LoginBody(), _FakeRequest(headers))
    except Exception as exc:  # noqa: BLE001 — HTTPException を検証対象として返す
        error = exc

    return {
        "result": result,
        "error": error,
        "events": events,
        "verify_calls": verify_calls,
        "read_session": read_session,
        "write_session": write_session,
    }


def _row(status="active", *, password_hash="$2b$12$fakehash", gen=0, role="instructor"):
    return (_USER_ID, "taro@example.com", password_hash, role, status, gen)


class TestLoginOrdering:
    def test_wrong_password_on_suspended_account_is_401_login_failed(self, monkeypatch):
        """status 判定より資格情報検証が先（列挙リークの防止 — §4.1-2 の注記）。"""
        out = _login_with(monkeypatch, _row("suspended"), verify=False)
        assert out["error"].status_code == 401
        assert out["events"] == ["login_failed"]
        assert "停止" not in str(out["error"].detail)

    def test_unknown_user_is_401_login_failed(self, monkeypatch):
        out = _login_with(monkeypatch, None)
        assert out["error"].status_code == 401
        assert out["events"] == ["login_failed"]
        # 存在しないユーザーではハッシュ検証を呼ばない
        assert out["verify_calls"] == []

    def test_null_password_hash_does_not_call_verify(self, monkeypatch):
        """password_hash IS NULL の行は _verify_password を呼ばない（500 潜在バグの是正）。"""
        out = _login_with(monkeypatch, _row("active", password_hash=None))
        assert out["error"].status_code == 401
        assert out["verify_calls"] == []
        assert out["events"] == ["login_failed"]

    def test_suspended_with_correct_password_is_403(self, monkeypatch):
        out = _login_with(monkeypatch, _row("suspended"))
        assert out["error"].status_code == 403
        assert out["error"].detail == "このアカウントは停止されています。管理者に連絡してください。"
        assert out["events"] == ["login_rejected_suspended"]

    def test_pending_deletion_with_correct_password_is_403(self, monkeypatch):
        out = _login_with(monkeypatch, _row("pending_deletion"))
        assert out["error"].status_code == 403
        assert out["events"] == ["login_rejected_suspended"]

    def test_deleted_is_401_and_does_not_reveal_existence(self, monkeypatch):
        out = _login_with(monkeypatch, _row("deleted"))
        assert out["error"].status_code == 401
        assert out["error"].detail == "Invalid credentials"
        assert out["events"] == ["login_failed"]


class TestLoginSuccess:
    def test_success_updates_last_login_and_records_event(self, monkeypatch):
        out = _login_with(monkeypatch, _row("active", gen=4))
        assert out["error"] is None
        assert out["events"] == ["login_success"]
        write_sql = [sql for sql, _ in out["write_session"].executed]
        assert any("UPDATE users SET last_login_at" in sql for sql in write_sql)
        assert out["write_session"].commits == 1
        assert out["write_session"].closed is True

    def test_token_carries_current_generation_and_role(self, monkeypatch):
        out = _login_with(monkeypatch, _row("active", gen=4))
        payload = jwt.decode(out["result"].access_token, "test-secret-key", algorithms=["HS256"])
        assert payload["gen"] == 4
        assert payload["sub"] == _USER_ID
        assert payload["role"] == "TEACHER"

    def test_login_query_selects_status_and_generation(self, monkeypatch):
        out = _login_with(monkeypatch, _row("active"))
        sql, _params = out["read_session"].executed[0]
        assert "status" in sql
        assert "token_generation" in sql

    def test_ip_and_user_agent_are_resolved_from_headers(self, monkeypatch):
        import routes.auth as auth_route

        captured: list = []

        def _record(session=None, **kwargs):
            captured.append(kwargs)
            return True

        monkeypatch.setattr(auth_route._auth_events, "record_auth_event", _record)
        monkeypatch.setattr(auth_route, "_verify_password", lambda *_a: True)
        monkeypatch.setattr(auth_route, "_pg_session", lambda: _FakeSession(rows=[_row("active")]))

        auth_route.auth_login(
            _LoginBody(),
            _FakeRequest({"X-Forwarded-For": "1.2.3.4, 203.0.113.9", "user-agent": "curl/8"}),
        )
        assert captured[0]["ip_address"] == "203.0.113.9"
        assert captured[0]["user_agent"] == "curl/8"

    def test_recording_failure_does_not_block_login(self, monkeypatch):
        import routes.auth as auth_route

        monkeypatch.setattr(auth_route, "_verify_password", lambda *_a: True)
        monkeypatch.setattr(
            auth_route,
            "_pg_session",
            lambda: _FakeSession(rows=[_row("active")], fail_on="UPDATE users"),
        )
        token = auth_route.auth_login(_LoginBody(), _FakeRequest())
        assert token.access_token


# ---------------------------------------------------------------------------
# 監査カタログ（§12-8）
# ---------------------------------------------------------------------------


class TestAuditCatalog:
    def test_user_account_entity_registered(self):
        assert AUDIT_ENTITY_USER_ACCOUNT == "user_account"
        assert AUDIT_ENTITY_USER_ACCOUNT in AUDIT_ENTITY_TYPES

    def test_catalog_has_no_duplicates(self):
        assert len(AUDIT_ENTITY_TYPES) == len(set(AUDIT_ENTITY_TYPES))


# ---------------------------------------------------------------------------
# ソースレベルのガードレール（AL4 / AL5 / core は FastAPI 非 import）
# ---------------------------------------------------------------------------


class TestSourceGuardrails:
    def test_core_modules_do_not_import_fastapi(self):
        for src, name in ((_AUTH_EVENTS_SRC, "auth_events.py"), (_ACCOUNT_STATUS_SRC, "account_status.py")):
            assert_source_forbids(src, ["from fastapi", "import fastapi"], context=name)

    def test_auth_events_is_append_only(self):
        assert_source_forbids(
            _AUTH_EVENTS_SRC,
            ["DELETE FROM auth_events", "UPDATE auth_events", "TRUNCATE"],
            context="auth_events.py (AL5: append-only)",
        )

    def test_no_delete_from_users_in_this_layer(self):
        for src, name in (
            (_AUTH_EVENTS_SRC, "auth_events.py"),
            (_ACCOUNT_STATUS_SRC, "account_status.py"),
            (_AUTH_ROUTE_SRC, "routes/auth.py"),
            (_DEPENDENCIES_SRC, "dependencies.py"),
        ):
            assert_source_forbids(src, ["DELETE FROM users"], context=f"{name} (AL1)")

    def test_login_verifies_credentials_before_status(self):
        """§12-12: 資格情報の検証が status 判定より前にあること（ソース順序で固定）。"""
        credentials_idx = _AUTH_ROUTE_SRC.index("credentials_ok =")
        status_idx = _AUTH_ROUTE_SRC.index("_LOGIN_BLOCKED_STATUSES:")
        assert credentials_idx < status_idx

    def test_login_guards_null_hash_before_verify(self):
        assert "bool(stored_hash) and _verify_password" in _AUTH_ROUTE_SRC

    def test_auth_route_does_not_log_or_record_credentials(self):
        """AL4: logger 呼び出し・auth_events payload に credential を渡さない。

        ``body.password`` 自体はハッシュ照合で必要なので禁止しない。禁止するのは
        「ログ行・記録引数に credential が乗ること」で、行単位で検査する。
        """
        offending = [
            line.strip()
            for line in _AUTH_ROUTE_SRC.splitlines()
            if ("logger." in line or "payload=" in line)
            and ("password" in line.lower() or "hash" in line.lower())
        ]
        assert offending == [], f"credential leaked into log/record: {offending}"

    def test_dependencies_fail_open_only_on_db_error(self):
        """fail-open は AccountStatusUnavailable の except 節のみ（行不在は 401）。"""
        assert _DEPENDENCIES_SRC.count("AccountStatusUnavailable") == 1
        unavailable_idx = _DEPENDENCIES_SRC.index("AccountStatusUnavailable")
        missing_idx = _DEPENDENCIES_SRC.index("if state is None:")
        assert unavailable_idx < missing_idx


# ---------------------------------------------------------------------------
# migration 068 の DDL 構造（§3.1 / §3.2）
# ---------------------------------------------------------------------------


class TestMigrationStructure:
    @pytest.mark.parametrize(
        "column",
        [
            "status",
            "status_changed_at",
            "status_changed_by",
            "status_reason",
            "token_generation",
            "password_updated_at",
            "last_login_at",
            "last_seen_at",
            "purge_after",
        ],
    )
    def test_users_columns_added_idempotently(self, column):
        assert f"ADD COLUMN IF NOT EXISTS {column} " in _MIGRATION_SQL

    def test_status_check_is_guarded_by_do_block(self):
        assert "pg_constraint" in _MIGRATION_SQL
        assert "users_status_check" in _MIGRATION_SQL
        # ADD CONSTRAINT は DO $$ ブロックの内側にあること
        do_idx = _MIGRATION_SQL.index("DO $$")
        add_idx = _MIGRATION_SQL.index("ADD CONSTRAINT users_status_check")
        assert do_idx < add_idx

    def test_token_generation_defaults_to_zero(self):
        assert "token_generation INTEGER NOT NULL DEFAULT 0" in _MIGRATION_SQL

    def test_auth_events_table_and_indexes(self):
        assert "CREATE TABLE IF NOT EXISTS auth_events" in _MIGRATION_SQL
        assert "CREATE INDEX IF NOT EXISTS idx_auth_events_user" in _MIGRATION_SQL
        assert "CREATE INDEX IF NOT EXISTS idx_auth_events_event" in _MIGRATION_SQL

    def test_auth_events_has_no_foreign_key(self):
        """FK は意図的に張らない（AL5 / AL8。テレメトリは墓標化後も残る）。"""
        table_start = _MIGRATION_SQL.index("CREATE TABLE IF NOT EXISTS auth_events")
        table_sql = _MIGRATION_SQL[table_start:]
        assert "REFERENCES users" not in table_sql

    def test_migration_has_no_password_columns(self):
        assert_source_forbids(
            _MIGRATION_SQL, ["password_hash"], context="068 (AL4: credential を増やさない)"
        )
