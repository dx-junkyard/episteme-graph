"""認証なし公開ユーザー登録エンドポイントの撤去を検証するガードレールテスト。

背景: `POST /api/auth/register` は認証不要で誰でも learner ロールのアカウントを
作成できてしまう脆弱性があったため撤去した（`backend/api/routes/auth.py`）。
アカウント作成は `POST /api/admin/users/student`（TEACHER 以上）/
`POST /api/admin/users/teacher`（SYSTEM_ADMIN のみ）に一本化されている
（`backend/api/routes/admin.py`）。

TestClient のセットアップは `test_course_builder_infra.py` と同型
（`from api.main import app` を直接 import し、lifespan は起動しない前提）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure api/ is importable (他の backend/tests/*.py と同じ流儀)
_backend_dir = str(Path(__file__).resolve().parents[1])
_api_dir = str(Path(__file__).resolve().parents[1] / "api")
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)
if _api_dir not in sys.path:
    sys.path.insert(0, _api_dir)

_HAS_FASTAPI = True
try:
    from fastapi.testclient import TestClient
except (ImportError, RuntimeError):
    _HAS_FASTAPI = False

pytestmark = pytest.mark.skipif(
    not _HAS_FASTAPI, reason="FastAPI not installed (run in Docker for full API tests)"
)


@pytest.fixture
def client():
    from api.main import app
    return TestClient(app)


class TestPublicRegisterRemoved:
    """POST /api/auth/register が撤去され、認証なし登録ができないこと。"""

    def test_register_route_gone(self, client):
        resp = client.post(
            "/api/auth/register",
            json={"username": "attacker", "email": "attacker@example.com", "password": "hunter2"},
        )
        # ルート自体が存在しないため 404。405 (Method Not Allowed) も許容する
        # （別 verb で偶然パスが残っている実装の変化を過度に固定しないため）。
        assert resp.status_code in (404, 405)

    def test_register_route_gone_get(self, client):
        # verb 違いでも登録が生き残っていないことを一応確認する。
        resp = client.get("/api/auth/register")
        assert resp.status_code in (404, 405)

    def test_me_route_still_exists_and_requires_auth(self, client):
        # /me は撤去対象ではない — ルーティングが消えておらず、認証必須のままである
        # ことのみ確認する（DB アクセスを伴わないため実 DB 接続不要）。
        resp = client.get("/api/auth/me")
        assert resp.status_code in (401, 403)


class TestAdminUserCreationRequiresAuth:
    """アカウント作成は管理エンドポイント経由のみで、認証必須であること。"""

    def test_create_student_without_token_rejected(self, client):
        resp = client.post(
            "/api/admin/users/student",
            json={"username": "newstudent", "email": "newstudent@example.com", "password": "hunter2"},
        )
        # HTTPBearer は資格情報なしだと既定で 403 を返すが、実装がどちらでも
        # 「未認証で拒否される」という不変条件のみを検証する。
        assert resp.status_code in (401, 403)

    def test_create_teacher_without_token_rejected(self, client):
        resp = client.post(
            "/api/admin/users/teacher",
            json={"username": "newteacher", "email": "newteacher@example.com", "password": "hunter2"},
        )
        assert resp.status_code in (401, 403)

    def test_create_student_with_student_token_rejected(self):
        """学生ロールのトークンでは TEACHER 権限が要求される /users/student にも入れない。"""
        from api.main import app
        from dependencies import _create_token, ROLE_STUDENT

        client = TestClient(app)
        token = _create_token(
            "11111111-1111-1111-1111-111111111111", "student1", "student1@test.com", ROLE_STUDENT
        )
        resp = client.post(
            "/api/admin/users/student",
            json={"username": "newstudent2", "email": "newstudent2@example.com", "password": "hunter2"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403
