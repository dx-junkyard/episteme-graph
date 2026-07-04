"""分野の地図 — 見晴らしの導線 (Issue F) の API テスト。

- GET /api/learning/atlas/cues/state: 初回自動表示フラグ
  (atlas_cue_events の first_login/opened 行の存在。永続 = 再ログイン・別端末でも同じ)
- POST /api/learning/atlas/cues/events: 導線イベントの内部計測
  (cue / event の語彙検証、帰属は JWT から)
- DB 不通時のフェイルセーフ: state は seen=True (自動表示を繰り返さない側に倒す)

DB は FakeSession (core.postgres.get_session を monkeypatch) で代替する。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HAS_FASTAPI = True
try:
    from fastapi.testclient import TestClient  # noqa: F401
except ImportError:
    _HAS_FASTAPI = False

pytestmark = pytest.mark.skipif(
    not _HAS_FASTAPI, reason="FastAPI not installed (run inside Docker for full API tests)"
)

STUDENT_ID = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def client():
    backend_dir = str(Path(__file__).resolve().parents[2])
    api_dir = str(Path(__file__).resolve().parents[2] / "api")
    src_dir = str(Path(__file__).resolve().parents[3] / "src")
    for p in (backend_dir, api_dir, src_dir):
        if p not in sys.path:
            sys.path.insert(0, p)
    from fastapi.testclient import TestClient
    from api.main import app

    return TestClient(app)


def _headers(role: str = "STUDENT", sub: str = STUDENT_ID):
    import jwt

    payload = {"sub": sub, "role": role, "username": "u1", "email": "u1@test.com"}
    token = jwt.encode(payload, "test-secret-key", algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


class FakeResult:
    def __init__(self, rows=None, rowcount=0):
        self._rows = rows or []
        self.rowcount = rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeSession:
    def __init__(self, router):
        self.calls = []
        self.router = router
        self.committed = False
        self.rolled_back = False

    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        self.calls.append((sql, params or {}))
        return self.router(sql, params or {})

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        pass


@pytest.fixture
def fake_db(monkeypatch):
    class Factory:
        def __init__(self):
            self.sessions = []
            self.route = lambda sql, params: FakeResult()

        def __call__(self):
            session = FakeSession(lambda sql, p: self.route(sql, p))
            self.sessions.append(session)
            return session

        def all_calls(self):
            return [call for s in self.sessions for call in s.calls]

    factory = Factory()
    import core.postgres as postgres_module

    monkeypatch.setattr(postgres_module, "get_session", factory)
    return factory


# ---------------------------------------------------------------------------
# GET /api/learning/atlas/cues/state — 初回自動表示フラグ
# ---------------------------------------------------------------------------


def test_cue_state_unseen(client, fake_db):
    """フラグ行が無ければ first_login_seen=False (初回自動表示が許可される)。"""
    fake_db.route = lambda sql, params: FakeResult(rows=[])
    res = client.get("/api/learning/atlas/cues/state", headers=_headers())
    assert res.status_code == 200
    assert res.json() == {"first_login_seen": False}
    # 検索条件が first_login/opened の行存在であること
    sql, params = fake_db.all_calls()[0]
    assert "atlas_cue_events" in sql
    assert "first_login" in sql and "opened" in sql
    assert params["uid"] == STUDENT_ID


def test_cue_state_seen_after_opened(client, fake_db):
    """opened 行が存在すれば first_login_seen=True (受け入れ条件4: 一度きり)。"""
    fake_db.route = lambda sql, params: FakeResult(rows=[(1,)])
    res = client.get("/api/learning/atlas/cues/state", headers=_headers())
    assert res.status_code == 200
    assert res.json() == {"first_login_seen": True}


def test_cue_state_fail_closed_without_db(client, monkeypatch):
    """DB 不通時は seen=True — 確認できないときに自動表示を繰り返さない。"""
    import core.postgres as postgres_module

    def _raise():
        raise RuntimeError("no db")

    monkeypatch.setattr(postgres_module, "get_session", _raise)
    res = client.get("/api/learning/atlas/cues/state", headers=_headers())
    assert res.status_code == 200
    assert res.json() == {"first_login_seen": True}


def test_cue_state_requires_auth(client, fake_db):
    res = client.get("/api/learning/atlas/cues/state")
    assert res.status_code in (401, 403)


# ---------------------------------------------------------------------------
# POST /api/learning/atlas/cues/events — 内部計測
# ---------------------------------------------------------------------------


def test_record_cue_event(client, fake_db):
    res = client.post(
        "/api/learning/atlas/cues/events",
        headers=_headers(),
        json={"cue": "topic_complete", "event": "shown", "payload": {"focus_label": "OPE"}},
    )
    assert res.status_code == 201
    assert res.json() == {"ok": True}
    sql, params = fake_db.all_calls()[0]
    assert "INSERT INTO atlas_cue_events" in sql
    assert params["cue"] == "topic_complete"
    assert params["event"] == "shown"
    assert params["uid"] == STUDENT_ID
    assert fake_db.sessions[0].committed


def test_record_first_login_opened_sets_flag_roundtrip(client, fake_db):
    """first_login/opened を記録 → 以後 state が seen=True を返す (フラグの実体)。"""
    inserted = []

    def route(sql, params):
        if "INSERT INTO atlas_cue_events" in sql:
            inserted.append(params)
            return FakeResult()
        if "SELECT 1 FROM atlas_cue_events" in sql:
            hit = [
                p for p in inserted
                if p["cue"] == "first_login" and p["event"] == "opened" and p["uid"] == params["uid"]
            ]
            return FakeResult(rows=[(1,)] if hit else [])
        return FakeResult()

    fake_db.route = route
    res = client.post(
        "/api/learning/atlas/cues/events",
        headers=_headers(),
        json={"cue": "first_login", "event": "opened"},
    )
    assert res.status_code == 201
    res = client.get("/api/learning/atlas/cues/state", headers=_headers())
    assert res.json() == {"first_login_seen": True}


def test_record_cue_event_rejects_unknown_vocabulary(client, fake_db):
    """cue / event は固定語彙のみ (計測の混入・自由文の書き込みを防ぐ)。"""
    res = client.post(
        "/api/learning/atlas/cues/events",
        headers=_headers(),
        json={"cue": "banner_hack", "event": "shown"},
    )
    assert res.status_code == 422
    res = client.post(
        "/api/learning/atlas/cues/events",
        headers=_headers(),
        json={"cue": "minimap", "event": "exploded"},
    )
    assert res.status_code == 422
    assert fake_db.all_calls() == []


def test_record_cue_event_oversized_payload_is_dropped(client, fake_db):
    """巨大 payload は空 JSON に落として記録する (テーブル汚染防止)。"""
    res = client.post(
        "/api/learning/atlas/cues/events",
        headers=_headers(),
        json={"cue": "minimap", "event": "dwell", "payload": {"x": "a" * 5000}},
    )
    assert res.status_code == 201
    _, params = fake_db.all_calls()[0]
    assert params["payload"] == "{}"


def test_record_cue_event_requires_auth(client, fake_db):
    res = client.post(
        "/api/learning/atlas/cues/events",
        json={"cue": "minimap", "event": "shown"},
    )
    assert res.status_code in (401, 403)
    assert fake_db.all_calls() == []
