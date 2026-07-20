"""ビジョン×UXギャップ調査 G1-1/G5-2: コース公開・開示範囲の意味論固定。

対象:
  - `PUT /api/admin/courses/{course_id}/visibility`（backend/api/routes/admin.py）
    の is_published 更新意味論（旧実装は public 化のときだけ true を立て、
    group/private へ戻しても is_published が true のまま残っていた）
  - `GET /api/admin/courses`（list_teacher_courses）が visibility/group_id を返すこと
    （コース管理タブで公開状態を確認できるようにするための追加フィールド）
  - `PUT /courses/{course_id}/publish` は引き続き存在しないこと（意図的撤去の再固定。
    test_course_group_permissions.py::test_publish_endpoint_removed と重複しても
    このモジュール単体でも意味論変更の趣旨を明示するために再掲する）

外部 API / 実 DB は呼び出さない。DB アクセスは MagicMock で分離する。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
ADMIN_PY = BACKEND_DIR / "api" / "routes" / "admin.py"

_HAS_FASTAPI = True
try:
    from fastapi.testclient import TestClient  # noqa: F401
except ImportError:
    _HAS_FASTAPI = False

_skip_no_fastapi = pytest.mark.skipif(
    not _HAS_FASTAPI,
    reason="FastAPI not installed (run in Docker for full API tests)",
)


@pytest.fixture
def client():
    if not _HAS_FASTAPI:
        pytest.skip("FastAPI not installed")
    import sys

    backend_dir = str(Path(__file__).resolve().parents[1])
    api_dir = str(Path(__file__).resolve().parents[1] / "api")
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    if api_dir not in sys.path:
        sys.path.insert(0, api_dir)

    from api.main import app

    return TestClient(app)


@pytest.fixture
def teacher_token():
    import jwt

    payload = {
        "sub": "test-teacher-id",
        "role": "TEACHER",
        "username": "teacher1",
        "email": "teacher1@test.com",
    }
    return jwt.encode(payload, "test-secret-key", algorithm="HS256")


@pytest.fixture
def auth_headers(teacher_token):
    return {"Authorization": f"Bearer {teacher_token}"}


# ---------------------------------------------------------------------------
# 静的解析: is_published が visibility に対して sticky でなくなっていること
# ---------------------------------------------------------------------------


class TestUpdateCourseVisibilitySource:
    @pytest.fixture(autouse=True)
    def _load(self):
        self.src = ADMIN_PY.read_text(encoding="utf-8")
        self.body = self.src.split("def update_course_visibility")[1].split("\n@router")[0]

    def test_is_published_derived_from_visibility(self):
        """is_published は現在の visibility='public' を常に正確に反映する
        （group/private へ戻したときも re-evaluate される）。"""
        assert "is_published = (:visibility = 'public')" in self.body

    def test_is_published_no_longer_sticky(self):
        """旧実装の『public 化したとき以外は既存値のまま』という CASE 文が
        残っていないこと（退行防止）。"""
        assert "is_published = CASE WHEN :visibility = 'public' THEN true ELSE is_published END" not in self.body

    def test_is_template_intentionally_kept_sticky(self):
        """is_template は「テンプレートとして作られたことがあるか」の意図を保つため、
        従来どおり public 化時のみ true を立てる（意図的にリセットしない）。"""
        assert "is_template = CASE WHEN :visibility = 'public' THEN true ELSE is_template END" in self.body

    def test_publish_endpoint_still_removed(self):
        """撤去済みの旧 publish エンドポイントを復活させていないこと。"""
        assert '"/courses/{course_id}/publish"' not in self.src


class TestListTeacherCoursesSource:
    @pytest.fixture(autouse=True)
    def _load(self):
        self.src = ADMIN_PY.read_text(encoding="utf-8")
        self.body = self.src.split("def list_teacher_courses")[1].split("\n@router")[0]

    def test_selects_visibility_and_group_id(self):
        """コース管理タブが公開状態・開示範囲を描画できるよう、一覧 API が
        visibility/group_id を SELECT すること（G5-2）。"""
        assert "COALESCE(lc.visibility, 'private') AS visibility" in self.body
        assert "lc.group_id" in self.body

    def test_response_includes_visibility_fields(self):
        assert '"visibility":' in self.body
        assert '"group_id":' in self.body


# ---------------------------------------------------------------------------
# 挙動テスト（DB はモック）
# ---------------------------------------------------------------------------


@_skip_no_fastapi
class TestUpdateCourseVisibilityBehavior:
    @patch("routes.admin.user_can_access_group", return_value=True)
    @patch("routes.admin._pg_session")
    def test_unpublish_sets_is_published_false(self, mock_session, _mock_group, client, auth_headers):
        """public だったコースを private に戻すと is_published=false で更新される
        （旧実装は is_published が true のまま残る退行があった）。"""
        mock_pg = MagicMock()
        mock_pg.execute.return_value.fetchone.return_value = ("c-001",)
        mock_session.return_value = mock_pg

        resp = client.put(
            "/api/admin/courses/c-001/visibility",
            headers=auth_headers,
            json={"visibility": "private"},
        )
        assert resp.status_code == 200
        assert resp.json()["visibility"] == "private"

        call_args = mock_pg.execute.call_args
        query_text = str(call_args[0][0])
        params = call_args[0][1]
        assert "is_published = (:visibility = 'public')" in query_text
        assert params["visibility"] == "private"

    @patch("routes.admin.user_can_access_group", return_value=True)
    @patch("routes.admin._pg_session")
    def test_publish_sets_is_published_true(self, mock_session, _mock_group, client, auth_headers):
        mock_pg = MagicMock()
        mock_pg.execute.return_value.fetchone.return_value = ("c-001",)
        mock_session.return_value = mock_pg

        resp = client.put(
            "/api/admin/courses/c-001/visibility",
            headers=auth_headers,
            json={"visibility": "public"},
        )
        assert resp.status_code == 200
        params = mock_pg.execute.call_args[0][1]
        assert params["visibility"] == "public"

    @patch("routes.admin._pg_session")
    def test_group_visibility_requires_group_id(self, mock_session, client, auth_headers):
        resp = client.put(
            "/api/admin/courses/c-001/visibility",
            headers=auth_headers,
            json={"visibility": "group"},
        )
        assert resp.status_code == 400

    @patch("routes.admin._pg_session")
    def test_course_not_found_returns_404(self, mock_session, client, auth_headers):
        mock_pg = MagicMock()
        mock_pg.execute.return_value.fetchone.return_value = None
        mock_session.return_value = mock_pg

        resp = client.put(
            "/api/admin/courses/does-not-exist/visibility",
            headers=auth_headers,
            json={"visibility": "private"},
        )
        assert resp.status_code == 404

    def test_requires_teacher_role(self, client):
        resp = client.put("/api/admin/courses/c-001/visibility", json={"visibility": "private"})
        assert resp.status_code in (401, 403)
