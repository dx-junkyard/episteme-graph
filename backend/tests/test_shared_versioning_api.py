"""V層 API の結合テスト（TestClient + monkeypatch）。

認証・RBAC・所有者ガード・エラーマッピングは DB アクセス前 / core 関数を monkeypatch して
実 DB なしで検証する（test_reconstruction_api.py と同型）。core.versioning の import は
collection エラーを避けるため必ずテスト内で行う。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from fastapi.testclient import TestClient  # noqa: F401
    _HAS_FASTAPI = True
except Exception:  # pragma: no cover
    _HAS_FASTAPI = False

pytestmark = pytest.mark.skipif(not _HAS_FASTAPI, reason="FastAPI 未導入")


@pytest.fixture
def client_and_tokens():
    from fastapi.testclient import TestClient
    from api.main import app
    from dependencies import _create_token, ROLE_STUDENT, ROLE_TEACHER

    client = TestClient(app)
    student = _create_token("11111111-1111-1111-1111-111111111111", "stu", "stu@x", ROLE_STUDENT)
    teacher = _create_token("22222222-2222-2222-2222-222222222222", "tea", "tea@x", ROLE_TEACHER)
    return client, student, teacher


def _auth(tok):
    return {"Authorization": "Bearer " + tok}


class TestReleaseRBAC:
    def test_publish_requires_auth(self, client_and_tokens):
        client, _s, _t = client_and_tokens
        r = client.post("/api/admin/shared/course/c1/releases", json={"note": "x"})
        assert r.status_code in (401, 403)

    def test_publish_forbidden_for_student(self, client_and_tokens):
        client, student, _t = client_and_tokens
        r = client.post("/api/admin/shared/course/c1/releases", json={"note": "x"}, headers=_auth(student))
        assert r.status_code == 403

    def test_publish_owner_only_404_for_non_owner(self, client_and_tokens, monkeypatch):
        client, _s, teacher = client_and_tokens
        monkeypatch.setattr("services.user_owns_course", lambda uid, cid: False)
        r = client.post("/api/admin/shared/course/c1/releases", json={"note": "x"}, headers=_auth(teacher))
        assert r.status_code == 404

    def test_publish_ok_for_owner(self, client_and_tokens, monkeypatch):
        client, _s, teacher = client_and_tokens
        monkeypatch.setattr("services.user_owns_course", lambda uid, cid: True)
        monkeypatch.setattr("services.record_review_event", lambda *a, **k: None)
        monkeypatch.setattr(
            "core.versioning.releases.publish_release",
            lambda **kw: {"id": "rel-1", "version_no": 1, "object_type": "course",
                          "object_id": "c1", "note": kw.get("note", ""), "snapshot": {}},
        )
        monkeypatch.setattr("core.versioning.notifications.fan_out", lambda *a, **k: 2)
        r = client.post("/api/admin/shared/course/c1/releases", json={"note": "v1"}, headers=_auth(teacher))
        assert r.status_code == 201
        body = r.json()
        assert body["version_no"] == 1
        assert body["notified"] == 2

    def test_publish_pending_deletion_returns_409(self, client_and_tokens, monkeypatch):
        client, _s, teacher = client_and_tokens
        from core.versioning import schema as vschema
        monkeypatch.setattr("services.user_owns_course", lambda uid, cid: True)

        def _boom(**kw):
            raise vschema.PendingDeletionError("scheduled")
        monkeypatch.setattr("core.versioning.releases.publish_release", _boom)
        r = client.post("/api/admin/shared/course/c1/releases", json={"note": "x"}, headers=_auth(teacher))
        assert r.status_code == 409


class TestAdoptErrorMapping:
    def test_adopt_conflict_returns_409(self, client_and_tokens, monkeypatch):
        client, _s, teacher = client_and_tokens
        from core.versioning import schema as vschema
        monkeypatch.setattr("services.user_can_view_course", lambda uid, cid: True)

        def _conflict(*a, **k):
            raise vschema.AdoptConflictError("stale")
        monkeypatch.setattr("core.versioning.subscriptions.adopt_latest", _conflict)
        r = client.post("/api/admin/shared/course/c1/subscription/adopt",
                        json={"expected_pinned_release_id": "old"}, headers=_auth(teacher))
        assert r.status_code == 409

    def test_adopt_purged_returns_410(self, client_and_tokens, monkeypatch):
        client, _s, teacher = client_and_tokens
        from core.versioning import schema as vschema
        monkeypatch.setattr("services.user_can_view_course", lambda uid, cid: True)

        def _purged(*a, **k):
            raise vschema.PurgedError("gone")
        monkeypatch.setattr("core.versioning.subscriptions.adopt_latest", _purged)
        r = client.post("/api/admin/shared/course/c1/subscription/adopt",
                        json={}, headers=_auth(teacher))
        assert r.status_code == 410


class TestDeletionScheduling:
    def test_deletion_invalid_iso_returns_422(self, client_and_tokens, monkeypatch):
        client, _s, teacher = client_and_tokens
        monkeypatch.setattr("services.user_owns_course", lambda uid, cid: True)
        r = client.post("/api/admin/shared/course/c1/deletion",
                        json={"purge_after": "not-a-date"}, headers=_auth(teacher))
        assert r.status_code == 422

    def test_deletion_past_date_returns_422(self, client_and_tokens, monkeypatch):
        client, _s, teacher = client_and_tokens
        from core.versioning import schema as vschema
        monkeypatch.setattr("services.user_owns_course", lambda uid, cid: True)

        def _boom(**kw):
            raise vschema.VersioningError("purge_after must be in the future")
        monkeypatch.setattr("core.versioning.deletion.schedule_deletion", _boom)
        r = client.post("/api/admin/shared/course/c1/deletion",
                        json={"grace_days": 7}, headers=_auth(teacher))
        assert r.status_code == 422

    def test_deletion_ok_notifies(self, client_and_tokens, monkeypatch):
        client, _s, teacher = client_and_tokens
        monkeypatch.setattr("services.user_owns_course", lambda uid, cid: True)
        monkeypatch.setattr("services.record_review_event", lambda *a, **k: None)
        monkeypatch.setattr(
            "core.versioning.deletion.schedule_deletion",
            lambda **kw: {"lifecycle": "pending_deletion", "delete_purge_after": "2099-01-01T00:00:00+00:00"},
        )
        calls = {"n": 0}
        monkeypatch.setattr("core.versioning.notifications.fan_out",
                            lambda *a, **k: calls.__setitem__("n", calls["n"] + 1) or 3)
        r = client.post("/api/admin/shared/course/c1/deletion",
                        json={"grace_days": 7, "reason": "outdated"}, headers=_auth(teacher))
        assert r.status_code == 200
        assert r.json()["lifecycle"] == "pending_deletion"
        assert calls["n"] == 1

    def test_cancel_deletion_owner_only(self, client_and_tokens, monkeypatch):
        client, _s, teacher = client_and_tokens
        monkeypatch.setattr("services.user_owns_course", lambda uid, cid: False)
        r = client.delete("/api/admin/shared/course/c1/deletion", headers=_auth(teacher))
        assert r.status_code == 404


class TestNotificationsInbox:
    def test_inbox_requires_auth(self, client_and_tokens):
        client, _s, _t = client_and_tokens
        r = client.get("/api/admin/shared/notifications")
        assert r.status_code in (401, 403)

    def test_inbox_ok_for_teacher(self, client_and_tokens, monkeypatch):
        client, _s, teacher = client_and_tokens
        monkeypatch.setattr("core.versioning.notifications.unread_count", lambda uid: 0)
        monkeypatch.setattr("core.versioning.notifications.list_inbox", lambda uid, **k: [])
        r = client.get("/api/admin/shared/notifications", headers=_auth(teacher))
        assert r.status_code == 200
        assert r.json()["unread_count"] == 0
        assert r.json()["notifications"] == []


class TestLearnerVersionNotice:
    """受講者向けの削除猶予バナー用エンドポイント（/api/learning/.../version-notice）。"""

    def test_requires_auth(self, client_and_tokens):
        client, _s, _t = client_and_tokens
        r = client.get("/api/learning/courses/c1/version-notice")
        assert r.status_code in (401, 403)

    def test_404_when_course_not_accessible(self, client_and_tokens, monkeypatch):
        client, student, _t = client_and_tokens
        monkeypatch.setattr("routes.learning.get_course_data", lambda uid, cid: None)
        r = client.get("/api/learning/courses/c1/version-notice", headers=_auth(student))
        assert r.status_code == 404

    def test_returns_pending_deletion_for_accessible_course(self, client_and_tokens, monkeypatch):
        client, student, _t = client_and_tokens
        monkeypatch.setattr("routes.learning.get_course_data", lambda uid, cid: {"id": cid, "topics": []})
        monkeypatch.setattr(
            "core.versioning.releases.get_state",
            lambda ot, oid: {"lifecycle": "pending_deletion",
                             "delete_purge_after": "2099-01-01T00:00:00+00:00", "delete_reason": "古い"},
        )
        r = client.get("/api/learning/courses/c1/version-notice", headers=_auth(student))
        assert r.status_code == 200
        body = r.json()
        assert body["lifecycle"] == "pending_deletion"
        assert body["delete_purge_after"].startswith("2099")

    def test_fail_open_when_state_errors(self, client_and_tokens, monkeypatch):
        client, student, _t = client_and_tokens
        monkeypatch.setattr("routes.learning.get_course_data", lambda uid, cid: {"id": cid, "topics": []})

        def _boom(ot, oid):
            raise RuntimeError("db down")
        monkeypatch.setattr("core.versioning.releases.get_state", _boom)
        r = client.get("/api/learning/courses/c1/version-notice", headers=_auth(student))
        assert r.status_code == 200
        assert r.json()["lifecycle"] == "active"

    def test_source_document_deletion_surfaced_via_notice(self, client_and_tokens, monkeypatch):
        # Issue 2a: コース自体は active でも、元教材の削除予約を course_deletion_notice が返せば
        #           バナー用に pending_deletion を返す（ルート配線の検証）。
        client, student, _t = client_and_tokens
        monkeypatch.setattr("routes.learning.get_course_data", lambda uid, cid: {"id": cid, "topics": []})
        monkeypatch.setattr(
            "routes.learning.course_deletion_notice",
            lambda cid: {"lifecycle": "pending_deletion",
                         "delete_purge_after": "2099-02-02T00:00:00+00:00",
                         "delete_reason": "この教材が削除予定のため"},
        )
        r = client.get("/api/learning/courses/c1/version-notice", headers=_auth(student))
        assert r.status_code == 200
        body = r.json()
        assert body["lifecycle"] == "pending_deletion"
        assert "教材" in body["delete_reason"]
