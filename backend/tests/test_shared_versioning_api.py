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


class TestVersionStatePermissionFlags:
    """N10残: version-state に is_owner/can_publish/can_schedule_deletion/can_edit/role を
    追加し、コース版管理を非所有者にも読み取り専用で開放できるようにする下地。
    「見せて、できない操作は理由付きで無効化」に対応するため、閲覧権があれば
    version-state 自体は 200 を返し、権限フラグだけで発行・削除予約セクションの
    活性化をフロントに判定させる契約。
    """

    def test_course_owner_gets_full_flags(self, client_and_tokens, monkeypatch):
        client, _s, teacher = client_and_tokens
        monkeypatch.setattr("services.user_can_view_course", lambda uid, cid: True)
        monkeypatch.setattr("services.user_owns_course", lambda uid, cid: True)
        monkeypatch.setattr("services.user_can_edit_course", lambda uid, cid: True)
        monkeypatch.setattr(
            "core.versioning.resolver.view_badges",
            lambda ot, oid, uid: {"has_versioning": True, "lifecycle": "active"},
        )
        r = client.get("/api/admin/shared/course/c1/version-state", headers=_auth(teacher))
        assert r.status_code == 200
        body = r.json()
        assert body["is_owner"] is True
        assert body["can_publish"] is True
        assert body["can_schedule_deletion"] is True
        assert body["can_edit"] is True
        assert body["role"] == "owner"
        # 既存のバッジフィールドは維持される
        assert body["has_versioning"] is True

    def test_course_group_editor_can_edit_but_not_publish(self, client_and_tokens, monkeypatch):
        client, _s, teacher = client_and_tokens
        monkeypatch.setattr("services.user_can_view_course", lambda uid, cid: True)
        monkeypatch.setattr("services.user_owns_course", lambda uid, cid: False)
        monkeypatch.setattr("services.user_can_edit_course", lambda uid, cid: True)
        monkeypatch.setattr(
            "core.versioning.resolver.view_badges",
            lambda ot, oid, uid: {"has_versioning": True},
        )
        r = client.get("/api/admin/shared/course/c1/version-state", headers=_auth(teacher))
        assert r.status_code == 200
        body = r.json()
        assert body["is_owner"] is False
        assert body["can_publish"] is False
        assert body["can_schedule_deletion"] is False
        assert body["can_edit"] is True
        assert body["role"] == "editor"

    def test_course_group_viewer_is_read_only(self, client_and_tokens, monkeypatch):
        client, _s, teacher = client_and_tokens
        monkeypatch.setattr("services.user_can_view_course", lambda uid, cid: True)
        monkeypatch.setattr("services.user_owns_course", lambda uid, cid: False)
        monkeypatch.setattr("services.user_can_edit_course", lambda uid, cid: False)
        monkeypatch.setattr(
            "core.versioning.resolver.view_badges",
            lambda ot, oid, uid: {"has_versioning": True},
        )
        r = client.get("/api/admin/shared/course/c1/version-state", headers=_auth(teacher))
        assert r.status_code == 200
        body = r.json()
        assert body["is_owner"] is False
        assert body["can_publish"] is False
        assert body["can_edit"] is False
        assert body["role"] == "viewer"

    def test_course_non_viewer_gets_404(self, client_and_tokens, monkeypatch):
        client, _s, teacher = client_and_tokens
        monkeypatch.setattr("services.user_can_view_course", lambda uid, cid: False)
        r = client.get("/api/admin/shared/course/c1/version-state", headers=_auth(teacher))
        assert r.status_code == 404

    def test_document_owner_gets_full_flags(self, client_and_tokens, monkeypatch):
        client, _s, teacher = client_and_tokens
        from services import DocumentAccess

        monkeypatch.setattr(
            "services.resolve_document_access",
            lambda uid, ref: DocumentAccess(
                document_id="doc-1", is_owner=True, can_view=True, can_edit=True,
            ),
        )
        monkeypatch.setattr(
            "core.versioning.resolver.view_badges",
            lambda ot, oid, uid: {"has_versioning": True},
        )
        r = client.get("/api/admin/shared/document/doc-1/version-state", headers=_auth(teacher))
        assert r.status_code == 200
        body = r.json()
        assert body["is_owner"] is True
        assert body["can_publish"] is True
        assert body["role"] == "owner"

    def test_document_group_editor_not_owner(self, client_and_tokens, monkeypatch):
        client, _s, teacher = client_and_tokens
        from services import DocumentAccess

        monkeypatch.setattr(
            "services.resolve_document_access",
            lambda uid, ref: DocumentAccess(
                document_id="doc-1", is_owner=False, can_view=True, can_edit=True,
            ),
        )
        monkeypatch.setattr(
            "core.versioning.resolver.view_badges",
            lambda ot, oid, uid: {"has_versioning": True},
        )
        r = client.get("/api/admin/shared/document/doc-1/version-state", headers=_auth(teacher))
        assert r.status_code == 200
        body = r.json()
        assert body["is_owner"] is False
        assert body["can_edit"] is True
        assert body["role"] == "editor"

    def test_document_group_viewer_read_only(self, client_and_tokens, monkeypatch):
        client, _s, teacher = client_and_tokens
        from services import DocumentAccess

        monkeypatch.setattr(
            "services.resolve_document_access",
            lambda uid, ref: DocumentAccess(
                document_id="doc-1", is_owner=False, can_view=True, can_edit=False,
            ),
        )
        monkeypatch.setattr(
            "core.versioning.resolver.view_badges",
            lambda ot, oid, uid: {"has_versioning": True},
        )
        r = client.get("/api/admin/shared/document/doc-1/version-state", headers=_auth(teacher))
        assert r.status_code == 200
        body = r.json()
        assert body["is_owner"] is False
        assert body["can_publish"] is False
        assert body["can_edit"] is False
        assert body["role"] == "viewer"

    def test_permission_flags_fail_closed_on_error(self, client_and_tokens, monkeypatch):
        """権限判定が例外を投げても version-state 全体を落とさず、フラグは最も制限的な
        側（is_owner=False 等）に fail-closed する（figure_presentation の
        viewer_is_owner と同型の防御）。"""
        client, _s, teacher = client_and_tokens
        monkeypatch.setattr("services.user_can_view_course", lambda uid, cid: True)

        def _boom(uid, cid):
            raise RuntimeError("db down")
        monkeypatch.setattr("services.user_owns_course", _boom)
        monkeypatch.setattr("services.user_can_edit_course", _boom)
        monkeypatch.setattr(
            "core.versioning.resolver.view_badges",
            lambda ot, oid, uid: {"has_versioning": True},
        )
        r = client.get("/api/admin/shared/course/c1/version-state", headers=_auth(teacher))
        assert r.status_code == 200
        body = r.json()
        assert body["is_owner"] is False
        assert body["can_publish"] is False
        assert body["can_edit"] is False
        assert body["role"] == "viewer"


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
