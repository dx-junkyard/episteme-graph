"""分野の地図 — 修正報告フロー (Issue D) の API 結合テスト。

DB は FakeSession (core.postgres.get_session を monkeypatch) で代替し、
受け入れ条件を検証する:

1. パネルから報告を送信すると、帰属・対象・レベル・骨格バージョンが揃った
   レコードが保存され、教員レビューキューに現れる
2. 匿名での送信経路が存在しない (API 側でも要認証・要帰属)
3. 採用 → 次版凍結の流れで changelog.credits に報告者が残る
4. 報告者に採用/見送りが通知される (mine?unacked / ack)
5. 骨格バージョン不一致 (旧版への報告) がレビュー画面で識別できる
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

_HAS_FASTAPI = True
try:
    from fastapi.testclient import TestClient  # noqa: F401
except ImportError:
    _HAS_FASTAPI = False

_skip_no_fastapi = pytest.mark.skipif(
    not _HAS_FASTAPI, reason="FastAPI not installed (run inside Docker for full API tests)"
)

STUDENT_ID = "22222222-2222-2222-2222-222222222222"
TEACHER_ID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def client():
    if not _HAS_FASTAPI:
        pytest.skip("FastAPI not installed")
    backend_dir = str(Path(__file__).resolve().parents[2])
    api_dir = str(Path(__file__).resolve().parents[2] / "api")
    src_dir = str(Path(__file__).resolve().parents[3] / "src")
    for p in (backend_dir, api_dir, src_dir):
        if p not in sys.path:
            sys.path.insert(0, p)
    from fastapi.testclient import TestClient
    from api.main import app

    return TestClient(app)


def _headers(role: str, sub: str):
    import jwt

    payload = {"sub": sub, "role": role, "username": "u1", "email": "u1@test.com"}
    token = jwt.encode(payload, "test-secret-key", algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def student_headers():
    return _headers("STUDENT", STUDENT_ID)


@pytest.fixture
def teacher_headers():
    return _headers("TEACHER", TEACHER_ID)


@pytest.fixture
def sandbox_cartridges(tmp_path, monkeypatch):
    from core import cartridges as cartridges_module

    src = cartridges_module._cartridges_root() / "particle_physics"
    dst = tmp_path / "particle_physics"
    shutil.copytree(src, dst)
    monkeypatch.setattr(cartridges_module, "_cartridges_root", lambda: tmp_path)
    cartridges_module.clear_cache()
    yield tmp_path
    cartridges_module.clear_cache()


# ---------------------------------------------------------------------------
# FakeSession (core.postgres.get_session の差し替え)
# ---------------------------------------------------------------------------


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

    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        self.calls.append((sql, params or {}))
        return self.router(sql, params or {})

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def close(self):
        pass


@pytest.fixture
def fake_db(monkeypatch):
    """core.postgres.get_session を FakeSession ファクトリに差し替える。

    ルーティング関数は fake_db.route で後から差し替えられる。
    生成された全セッションは fake_db.sessions に残る (SQL の検証用)。
    atlas_skeletons (migration 027: 骨格の DB 管理) はステートフルな
    インメモリフェイクへ委譲する (draft 保存 → 凍結の流れを成立させるため)。
    """
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
    from tests.fixtures.atlas_skeletons_fake import AtlasSkeletonTableFake

    class Factory:
        def __init__(self):
            self.sessions = []
            self.route = lambda sql, params: FakeResult()
            self.skeletons = AtlasSkeletonTableFake()

        def _dispatch(self, sql, params):
            if "atlas_skeletons" in sql:
                return self.skeletons.execute(sql, params)
            return self.route(sql, params)

        def __call__(self):
            session = FakeSession(lambda sql, p: self._dispatch(sql, p))
            self.sessions.append(session)
            return session

        def all_calls(self):
            return [call for s in self.sessions for call in s.calls]

    factory = Factory()
    import core.postgres as postgres_module

    monkeypatch.setattr(postgres_module, "get_session", factory)
    return factory


def _report_row(**overrides):
    """core.atlas_reports._SELECT_REPORTS の列順に合わせた行タプル。"""
    base = {
        "id": "rep-1",
        "cartridge_id": "particle_physics",
        "skeleton_version": "2025.9",
        "node_id": "dual",
        "region_id": "",
        "level": 1,
        "node_label": "双対性の仮定",
        "report_text": "この配置は実際と違う",
        "reporter_id": STUDENT_ID,
        "reporter_name": "hanako",
        "status": "pending",
        "resolution_note": "",
        "resolved_by": None,
        "resolved_at": None,
        "merged_into": None,
        "incorporated_at": None,
        "incorporated_by": None,
        "incorporation_note": "",
        "applied_version": "",
        "notified_at": None,
        "created_at": "2026-07-01T00:00:00",
    }
    base.update(overrides)
    from core.atlas_reports import _REPORT_COLUMNS

    return tuple(base[c] for c in _REPORT_COLUMNS)


# ---------------------------------------------------------------------------
# D-1: 送信 (帰属・バリデーション)
# ---------------------------------------------------------------------------


@_skip_no_fastapi
class TestCreateReport:
    def test_anonymous_submission_is_rejected(self, client, fake_db):
        """匿名での送信経路が存在しない (受け入れ条件2)。"""
        resp = client.post(
            "/api/atlas/report",
            json={"text": "x", "node_id": "dual", "level": 1, "skeleton_version": "2026.1"},
        )
        assert resp.status_code in (401, 403)
        assert fake_db.all_calls() == []

    def test_empty_text_is_rejected(self, client, student_headers, fake_db):
        """空文字送信のガード (「(内容未記入)」は本実装では送信不可)。"""
        resp = client.post(
            "/api/atlas/report",
            headers=student_headers,
            json={"text": "   ", "node_id": "dual", "level": 1, "skeleton_version": "2026.1"},
        )
        assert resp.status_code == 422
        assert not any(
            "INSERT INTO atlas_correction_reports" in sql for sql, _ in fake_db.all_calls()
        )

    def test_missing_skeleton_version_is_rejected(self, client, student_headers, fake_db):
        resp = client.post(
            "/api/atlas/report",
            headers=student_headers,
            json={"text": "ここが違う", "node_id": "dual", "level": 1},
        )
        assert resp.status_code == 422

    def test_valid_report_is_stored_with_attribution(self, client, student_headers, fake_db):
        """帰属・対象・レベル・骨格バージョンが揃って保存される (受け入れ条件1)。"""

        def route(sql, params):
            if "INSERT INTO atlas_correction_reports" in sql:
                return FakeResult(rows=[("rep-1",)])
            return FakeResult()

        fake_db.route = route
        resp = client.post(
            "/api/atlas/report",
            headers=student_headers,
            json={
                "text": "この配置は実際と違う",
                "node_id": "dual",
                "level": 1,
                "skeleton_version": "2026.1",
                "cartridge_id": "particle_physics",
                "node_label": "双対性の仮定",
            },
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["report_id"] == "rep-1"

        insert = next(
            (sql, p) for sql, p in fake_db.all_calls()
            if "INSERT INTO atlas_correction_reports" in sql
        )
        assert insert[1]["reporter_id"] == STUDENT_ID  # 帰属 = JWT の本人 (匿名不可)
        assert insert[1]["skeleton_version"] == "2026.1"
        assert insert[1]["node_id"] == "dual"
        assert insert[1]["level"] == 1
        # 監査: theory_review_events に entity_type='atlas_report' で記録
        audit = next(
            (sql, p) for sql, p in fake_db.all_calls()
            if "INSERT INTO theory_review_events" in sql
        )
        assert audit[1]["entity_type"] == "atlas_report"
        assert audit[1]["new_status"] == "pending"


# ---------------------------------------------------------------------------
# D-2: レビューキュー (教員のみ・旧版識別)
# ---------------------------------------------------------------------------


@_skip_no_fastapi
class TestReviewQueue:
    def test_queue_requires_teacher(self, client, student_headers, sandbox_cartridges, fake_db):
        resp = client.get(
            "/api/admin/cartridges/particle_physics/atlas/reports", headers=student_headers
        )
        assert resp.status_code == 403

    def test_queue_lists_reports_with_version_mismatch(
        self, client, teacher_headers, sandbox_cartridges, fake_db
    ):
        """報告本文 + 対象 + 骨格バージョン + 報告者が現れ、旧版が識別できる
        (受け入れ条件1・5)。同梱凍結版は 2026.1、報告は 2025.9。"""

        def route(sql, params):
            if sql.startswith("SELECT r.id::text"):
                return FakeResult(rows=[_report_row()])
            return FakeResult()

        fake_db.route = route
        resp = client.get(
            "/api/admin/cartridges/particle_physics/atlas/reports?status=pending",
            headers=teacher_headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["current_version"] == "2026.1"
        report = data["reports"][0]
        assert report["report_text"] == "この配置は実際と違う"
        assert report["reporter_name"] == "hanako"
        assert report["skeleton_version"] == "2025.9"
        assert report["version_mismatch"] is True
        assert report["target"] == "dual"

    def test_unknown_status_filter_is_rejected(
        self, client, teacher_headers, sandbox_cartridges, fake_db
    ):
        resp = client.get(
            "/api/admin/cartridges/particle_physics/atlas/reports?status=bogus",
            headers=teacher_headers,
        )
        assert resp.status_code == 422

    def test_decline_requires_note(self, client, teacher_headers, sandbox_cartridges, fake_db):
        def route(sql, params):
            if sql.startswith("SELECT status"):
                return FakeResult(rows=[("pending",)])
            return FakeResult(rowcount=1)

        fake_db.route = route
        resp = client.post(
            "/api/admin/cartridges/particle_physics/atlas/reports/rep-1/resolve",
            headers=teacher_headers,
            json={"action": "decline", "note": ""},
        )
        assert resp.status_code == 422

    def test_accept_transitions_and_audits(
        self, client, teacher_headers, sandbox_cartridges, fake_db
    ):
        def route(sql, params):
            if sql.startswith("SELECT status"):
                return FakeResult(rows=[("pending",)])
            return FakeResult(rowcount=1)

        fake_db.route = route
        resp = client.post(
            "/api/admin/cartridges/particle_physics/atlas/reports/rep-1/resolve",
            headers=teacher_headers,
            json={"action": "accept", "note": "次版で直す"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() == {
            "report_id": "rep-1",
            "old_status": "pending",
            "new_status": "accepted",
        }
        audit = next(
            (sql, p) for sql, p in fake_db.all_calls()
            if "INSERT INTO theory_review_events" in sql
        )
        assert audit[1]["entity_type"] == "atlas_report"
        assert audit[1]["old_status"] == "pending"
        assert audit[1]["new_status"] == "accepted"
        assert audit[1]["changed_by"] == TEACHER_ID

    def test_incorporate_marks_accepted_report_and_audits(
        self, client, teacher_headers, sandbox_cartridges, fake_db
    ):
        def route(sql, params):
            if sql.startswith("SELECT status, incorporated_at"):
                return FakeResult(rows=[("accepted", None, "")])
            return FakeResult(rowcount=1)

        fake_db.route = route
        resp = client.post(
            "/api/admin/cartridges/particle_physics/atlas/reports/rep-1/incorporate",
            headers=teacher_headers,
            json={"note": "領域を移動した"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["new_status"] == "incorporated"
        update = next(
            (sql, p) for sql, p in fake_db.all_calls()
            if "SET incorporated_at = now()" in sql
        )
        assert update[1]["note"] == "領域を移動した"
        audit = next(
            (sql, p) for sql, p in fake_db.all_calls()
            if "INSERT INTO theory_review_events" in sql
        )
        assert audit[1]["new_status"] == "incorporated"


# ---------------------------------------------------------------------------
# D-3: 採用の changelog 帰属 (凍結) と報告者への通知
# ---------------------------------------------------------------------------


def _sample_draft_dict(cartridge_id: str = "particle_physics") -> dict:
    return {
        "atlas_skeleton": {
            "version": "",
            "cartridge": cartridge_id,
            "status": "draft",
            "generated_by": "model:test batch:api",
            "reviewed_by": [],
            "regions": [
                {
                    "id": "region_a",
                    "label": "領域A",
                    "layout": {"x": 0.1, "y": 0.1, "w": 0.4, "h": 0.4},
                    "concepts": [{"id": "concept_a", "label": "概念A"}],
                }
            ],
            "edges": [],
            "concept_bindings": [],
        }
    }


@_skip_no_fastapi
class TestFreezeCredits:
    def test_accepted_reports_credit_into_changelog(
        self, client, teacher_headers, sandbox_cartridges, fake_db
    ):
        """採用 → 次版凍結の流れで changelog.credits に報告者が残る (受け入れ条件3)。"""

        def route(sql, params):
            if sql.startswith("SELECT r.id::text") and "applied_version = ''" in sql:
                return FakeResult(
                    rows=[
                        _report_row(id="rep-1", status="accepted", reporter_name="hanako"),
                        _report_row(id="rep-2", status="accepted", reporter_name="taro"),
                    ]
                )
            return FakeResult(rowcount=2)

        fake_db.route = route
        resp = client.put(
            "/api/admin/cartridges/particle_physics/atlas/skeleton/draft",
            headers=teacher_headers,
            json={"skeleton": _sample_draft_dict()},
        )
        assert resp.status_code == 200, resp.text

        resp = client.post(
            "/api/admin/cartridges/particle_physics/atlas/skeleton/freeze",
            headers=teacher_headers,
            json={"version": "2027.1", "note": "報告反映", "credits": ["外部指摘"]},
        )
        assert resp.status_code == 200, resp.text
        frozen = resp.json()["frozen"]["skeleton"]
        entry = next(e for e in frozen["changelog"] if e["version"] == "2027.1")
        # 明示 credits + 採用済み報告の帰属が重複なく合流する
        assert entry["credits"] == ["外部指摘", "hanako", "taro"]

        # 採用済み報告への applied_version の刻印 (自動クローズ) が実行される
        stamp = next(
            (sql, p) for sql, p in fake_db.all_calls()
            if "SET applied_version = :version" in sql
        )
        assert stamp[1]["version"] == "2027.1"

    def test_freeze_without_reports_keeps_explicit_credits(
        self, client, teacher_headers, sandbox_cartridges, fake_db
    ):
        resp = client.put(
            "/api/admin/cartridges/particle_physics/atlas/skeleton/draft",
            headers=teacher_headers,
            json={"skeleton": _sample_draft_dict()},
        )
        assert resp.status_code == 200
        resp = client.post(
            "/api/admin/cartridges/particle_physics/atlas/skeleton/freeze",
            headers=teacher_headers,
            json={"version": "2027.2", "note": "改版", "credits": ["外部指摘"]},
        )
        assert resp.status_code == 200, resp.text
        frozen = resp.json()["frozen"]["skeleton"]
        entry = next(e for e in frozen["changelog"] if e["version"] == "2027.2")
        assert entry["credits"] == ["外部指摘"]


@_skip_no_fastapi
class TestReporterNotification:
    def test_mine_returns_own_unacked_results(self, client, student_headers, fake_db):
        """報告者に採用/見送りの結果 (見送り理由を含む) が届く (受け入れ条件4)。"""

        def route(sql, params):
            if sql.startswith("SELECT r.id::text"):
                assert params["user_id"] == STUDENT_ID  # 本人のみ
                assert "notified_at IS NULL" in sql
                return FakeResult(
                    rows=[
                        _report_row(
                            id="rep-1",
                            status="declined",
                            resolution_note="現行版で正しい配置です",
                            resolved_by=TEACHER_ID,
                            resolved_at="2026-07-02T00:00:00",
                        )
                    ]
                )
            return FakeResult()

        fake_db.route = route
        resp = client.get("/api/atlas/reports/mine?unacked=true", headers=student_headers)
        assert resp.status_code == 200, resp.text
        report = resp.json()["reports"][0]
        assert report["status"] == "declined"
        assert report["resolution_note"] == "現行版で正しい配置です"

    def test_ack_marks_notification_read(self, client, student_headers, fake_db):
        fake_db.route = lambda sql, params: FakeResult(rowcount=1)
        resp = client.post("/api/atlas/reports/rep-1/ack", headers=student_headers)
        assert resp.status_code == 200
        assert resp.json()["acked"] is True

    def test_ack_of_foreign_or_unresolved_report_is_404(self, client, student_headers, fake_db):
        fake_db.route = lambda sql, params: FakeResult(rowcount=0)
        resp = client.post("/api/atlas/reports/rep-9/ack", headers=student_headers)
        assert resp.status_code == 404

    def test_mine_requires_auth(self, client, fake_db):
        resp = client.get("/api/atlas/reports/mine")
        assert resp.status_code in (401, 403)
