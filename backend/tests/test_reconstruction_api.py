"""R層 API の結合テスト（TestClient + monkeypatch）。

認証・RBAC・入力検証は DB アクセス前に評価されるため、実 DB なしで検証できる。
happy-path は core の集計関数・受講判定を monkeypatch して 200 まで通す。
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


class TestAdminRBAC:
    def test_review_queue_requires_auth(self, client_and_tokens):
        client, _s, _t = client_and_tokens
        r = client.get("/api/admin/reconstruction/items/review-queue")
        assert r.status_code in (401, 403)

    def test_review_queue_forbidden_for_student(self, client_and_tokens):
        client, student, _t = client_and_tokens
        r = client.get("/api/admin/reconstruction/items/review-queue", headers=_auth(student))
        assert r.status_code == 403

    def test_review_queue_ok_for_teacher(self, client_and_tokens, monkeypatch):
        client, _s, teacher = client_and_tokens
        import routes.reconstruction as route_mod
        monkeypatch.setattr(route_mod, "get_review_queue", lambda doc=None: {"items": [], "k_anonymity": 3})
        r = client.get("/api/admin/reconstruction/items/review-queue", headers=_auth(teacher))
        assert r.status_code == 200
        assert r.json()["k_anonymity"] == 3


class TestLearnerGating:
    def test_next_requires_auth(self, client_and_tokens):
        client, _s, _t = client_and_tokens
        r = client.get("/api/learning/courses/c1/topics/t1/reconstruction/next")
        assert r.status_code in (401, 403)

    def test_next_404_when_course_not_accessible(self, client_and_tokens, monkeypatch):
        client, student, _t = client_and_tokens
        import routes.reconstruction as route_mod
        monkeypatch.setattr(route_mod, "get_accessible_course_data", lambda uid, cid: None)
        r = client.get("/api/learning/courses/c1/topics/t1/reconstruction/next", headers=_auth(student))
        assert r.status_code == 404

    def test_self_check_rejects_invalid_result(self, client_and_tokens):
        client, student, _t = client_and_tokens
        r = client.post(
            "/api/learning/reconstruction/r1/self-check",
            headers=_auth(student), json={"result": "not-a-valid-value"},
        )
        assert r.status_code == 422

    def test_submit_404_when_course_not_accessible(self, client_and_tokens, monkeypatch):
        client, student, _t = client_and_tokens
        import routes.reconstruction as route_mod
        monkeypatch.setattr(route_mod, "get_accessible_course_data", lambda uid, cid: None)
        r = client.post(
            "/api/learning/reconstruction/i1/submit",
            headers=_auth(student), json={"course_id": "c1", "response": {"option_id": "a"}},
        )
        assert r.status_code == 404

    def test_submit_rejects_empty_response(self, client_and_tokens, monkeypatch):
        """空応答でリビール（伏せフィールドの開示）を引き出せない（CAPTURE → REVEAL）。"""
        client, student, _t = client_and_tokens
        import routes.reconstruction as route_mod
        monkeypatch.setattr(route_mod, "get_accessible_course_data", lambda uid, cid: {"sources": []})
        for resp in ({}, {"text": ""}, {"option_id": "  "}):
            r = client.post(
                "/api/learning/reconstruction/i1/submit",
                headers=_auth(student), json={"course_id": "c1", "response": resp},
            )
            assert r.status_code == 422

    def test_revise_rejects_malformed_revision_of(self, client_and_tokens, monkeypatch):
        client, student, _t = client_and_tokens
        import routes.reconstruction as route_mod
        monkeypatch.setattr(route_mod, "get_accessible_course_data", lambda uid, cid: {"sources": []})
        r = client.post(
            "/api/learning/reconstruction/i1/revise",
            headers=_auth(student),
            json={"course_id": "c1", "response": {"text": "again"}, "revision_of": "not-a-uuid"},
        )
        assert r.status_code == 422


class TestStumbleRBAC:
    def test_stumble_summary_forbidden_for_student(self, client_and_tokens):
        client, student, _t = client_and_tokens
        r = client.get("/api/admin/documents/doc1/claims/stumble-summary", headers=_auth(student))
        assert r.status_code == 403
