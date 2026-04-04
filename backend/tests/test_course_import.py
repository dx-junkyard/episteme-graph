"""Issue #50: 登録済みコースの再編集（インポート）機能のテスト。

admin.html / admin.js のUI要素と、バックエンドAPIエンドポイントの検証。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Frontend DOM / JS tests (静的HTML/JS解析)
# ---------------------------------------------------------------------------

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend" / "public"
ADMIN_HTML = FRONTEND_DIR / "admin.html"
ADMIN_JS = FRONTEND_DIR / "js" / "admin.js"


@pytest.fixture(autouse=True)
def _no_backend_settings():
    """フロントエンドテストではバックエンド設定のオーバーライドは不要。"""
    yield


class TestAdminJSCourseImport:
    """admin.js にコースインポート機能のロジックが存在することを検証する。"""

    @pytest.fixture(autouse=True)
    def _load_js(self):
        self.js = ADMIN_JS.read_text(encoding="utf-8")

    def test_import_course_btn_rendered(self):
        """renderSessionBar 内に「既存コースを読込」ボタンが生成されること。"""
        assert 'id="import-course-btn"' in self.js

    def test_open_import_course_modal_exists(self):
        """openImportCourseModal 関数が定義されていること。"""
        assert "function openImportCourseModal" in self.js

    def test_import_course_function_exists(self):
        """importCourse 関数が定義されていること。"""
        assert "function importCourse" in self.js

    def test_import_modal_id(self):
        """モーダルが import-course-modal ID で作成されること。"""
        assert "import-course-modal" in self.js

    def test_api_endpoint_admin_courses(self):
        """コース一覧取得に /admin/courses エンドポイントを使用すること。"""
        assert '"/admin/courses"' in self.js

    def test_api_endpoint_draft_format(self):
        """コース変換に /draft-format エンドポイントを使用すること。"""
        assert "/draft-format" in self.js

    def test_import_creates_new_session(self):
        """インポート時に新しいセッションを作成すること。"""
        # importCourse 内で /admin/course-builder/sessions への POST が行われること
        assert "再編集:" in self.js

    def test_import_sets_course_draft(self):
        """インポート時に state.courseDraft が設定されること。"""
        assert "state.courseDraft = draft" in self.js

    def test_import_sets_initial_message(self):
        """インポート時にAIからの初期メッセージが配置されること。"""
        assert "データを読み込みました" in self.js

    def test_import_saves_session_with_draft(self):
        """インポート後にセッションが draft 付きで保存されること。"""
        assert "course_draft: draft" in self.js

    def test_state_has_imported_from_course_id(self):
        """state に importedFromCourseId プロパティが存在すること。"""
        assert "importedFromCourseId" in self.js

    def test_context_sharing_for_imported_draft(self):
        """インポートされた draft のコンテキストがAIに送られること。"""
        assert "現在のコース構成（JSON）" in self.js

    def test_import_button_click_handler(self):
        """インポートボタンにクリックハンドラが設定されること。"""
        assert "openImportCourseModal()" in self.js


class TestAdminHTMLCourseBuilder:
    """admin.html のコースビルダー関連DOM要素を検証する。"""

    @pytest.fixture(autouse=True)
    def _load_html(self):
        self.html = ADMIN_HTML.read_text(encoding="utf-8")

    def test_session_bar_exists(self):
        """セッションバーコンテナが存在すること。"""
        assert 'id="cb-session-bar"' in self.html

    def test_course_builder_tab_exists(self):
        """コース構築タブボタンが存在すること。"""
        assert 'data-tab="course-builder"' in self.html


# ---------------------------------------------------------------------------
# Backend API endpoint tests
# ---------------------------------------------------------------------------

_HAS_FASTAPI = True
try:
    from fastapi.testclient import TestClient
except ImportError:
    _HAS_FASTAPI = False

_skip_no_fastapi = pytest.mark.skipif(
    not _HAS_FASTAPI,
    reason="FastAPI not installed (run in Docker for full API tests)",
)


@pytest.fixture
def client():
    """FastAPI TestClient を生成する。"""
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
    """テスト用の教員JWTトークンを生成する。"""
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


@_skip_no_fastapi
class TestListTeacherCourses:
    """GET /api/admin/courses エンドポイントのテスト。"""

    @patch("routes.admin._pg_session")
    def test_list_courses_empty(self, mock_session, client, auth_headers):
        """コースが0件の場合、空リストが返ること。"""
        mock_pg = MagicMock()
        mock_pg.execute.return_value.fetchall.return_value = []
        mock_session.return_value = mock_pg

        resp = client.get("/api/admin/courses", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    @patch("routes.admin._pg_session")
    def test_list_courses_with_data(self, mock_session, client, auth_headers):
        """コースがある場合、正しい形式で返ること。"""
        from datetime import datetime

        now = datetime(2026, 3, 30, 12, 0, 0)
        mock_pg = MagicMock()
        mock_pg.execute.return_value.fetchall.return_value = [
            ("c-001", "量子力学入門", True, True, now, now),
        ]
        mock_session.return_value = mock_pg

        resp = client.get("/api/admin/courses", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == "c-001"
        assert data[0]["title"] == "量子力学入門"
        assert data[0]["is_template"] is True
        assert data[0]["is_published"] is True

    def test_list_courses_requires_auth(self, client):
        """認証なしで401が返ること。"""
        resp = client.get("/api/admin/courses")
        assert resp.status_code in (401, 403)


@_skip_no_fastapi
class TestGetCourseAsDraft:
    """GET /api/admin/courses/{id}/draft-format エンドポイントのテスト。"""

    @patch("routes.admin._pg_session")
    def test_convert_course_to_draft(self, mock_session, client, auth_headers):
        """コースデータが draft 形式に変換されること。"""
        course_data = {
            "id": "c-001",
            "title": "量子力学入門",
            "chapters": [
                {"title": "第1章: 波動関数", "status": "locked", "progress_pct": 0},
                {"title": "第2章: シュレーディンガー方程式", "status": "locked", "progress_pct": 0},
            ],
            "topics": [
                {
                    "id": "t0",
                    "title": "波動関数の基礎",
                    "chapter_index": 0,
                    "status": "in_progress",
                    "prerequisites": [],
                    "misconceptions": [],
                },
                {
                    "id": "t1",
                    "title": "確率解釈",
                    "chapter_index": 0,
                    "status": "locked",
                    "prerequisites": [{"name": "波動関数の基礎", "status": "not_started"}],
                    "misconceptions": [],
                },
                {
                    "id": "t2",
                    "title": "定常状態",
                    "chapter_index": 1,
                    "status": "locked",
                    "prerequisites": [{"name": "確率解釈", "status": "not_started"}],
                    "misconceptions": [],
                },
            ],
            "concepts": [
                {"name": "波動関数", "status": "future", "children": ["確率振幅"], "expanded": False},
            ],
            "sources": [
                {"title": "Griffiths QM", "subtitle": "第3版", "license": "", "used_section": "", "material_id": "m-001"},
            ],
        }

        mock_pg = MagicMock()
        mock_pg.execute.return_value.fetchone.return_value = (
            course_data,
            "量子力学入門",
        )
        mock_session.return_value = mock_pg

        resp = client.get("/api/admin/courses/c-001/draft-format", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()

        assert data["course_id"] == "c-001"
        assert data["course_title"] == "量子力学入門"

        draft = data["course_draft"]
        assert draft["title"] == "量子力学入門"
        assert len(draft["chapters"]) == 2
        assert draft["chapters"][0]["title"] == "第1章: 波動関数"
        # Topics grouped correctly
        assert len(draft["chapters"][0]["topics"]) == 2
        assert len(draft["chapters"][1]["topics"]) == 1
        # Prerequisites converted to string list
        assert draft["chapters"][0]["topics"][1]["prerequisites"] == ["波動関数の基礎"]
        assert draft["chapters"][1]["topics"][0]["prerequisites"] == ["確率解釈"]
        # Concepts
        assert len(draft["concepts"]) == 1
        assert draft["concepts"][0]["name"] == "波動関数"
        assert draft["concepts"][0]["children"] == ["確率振幅"]
        # Sources
        assert len(draft["sources"]) == 1
        assert draft["sources"][0]["title"] == "Griffiths QM"

    @patch("routes.admin._pg_session")
    def test_course_not_found(self, mock_session, client, auth_headers):
        """存在しないコースIDで404が返ること。"""
        mock_pg = MagicMock()
        mock_pg.execute.return_value.fetchone.return_value = None
        mock_session.return_value = mock_pg

        resp = client.get("/api/admin/courses/nonexistent/draft-format", headers=auth_headers)
        assert resp.status_code == 404

    def test_draft_format_requires_auth(self, client):
        """認証なしで401が返ること。"""
        resp = client.get("/api/admin/courses/c-001/draft-format")
        assert resp.status_code in (401, 403)
