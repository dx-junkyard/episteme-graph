"""コース削除・教材削除APIとUIのテスト。

名前入力確認付き削除、教材削除時のカスケード削除を検証する。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Frontend DOM / JS tests (静的HTML/JS解析)
# ---------------------------------------------------------------------------

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend" / "public"
ADMIN_HTML = FRONTEND_DIR / "admin.html"
ADMIN_JS = FRONTEND_DIR / "js" / "admin.js"


class TestAdminHTMLDeleteColumn:
    """admin.html に操作カラムが存在することを検証する。"""

    @pytest.fixture(autouse=True)
    def _load_html(self):
        self.html = ADMIN_HTML.read_text(encoding="utf-8")

    def test_operation_column_header(self):
        """教材テーブルに「操作」ヘッダーが存在すること。"""
        assert "<th>操作</th>" in self.html

    def test_materials_table_has_5_columns(self):
        """教材テーブルのヘッダーが5列であること。"""
        # colspanの値を確認（空状態のメッセージ）
        assert 'colspan="5"' in self.html


class TestAdminJSDeleteMaterial:
    """admin.js に教材削除機能のロジックが存在することを検証する。"""

    @pytest.fixture(autouse=True)
    def _load_js(self):
        self.js = ADMIN_JS.read_text(encoding="utf-8")

    def test_delete_button_rendered_in_materials(self):
        """renderMaterials 内に削除ボタンが生成されること。"""
        assert "admin-delete-btn" in self.js

    def test_delete_button_has_material_id(self):
        """削除ボタンに data-material-id 属性が含まれること。"""
        assert "data-material-id" in self.js

    def test_delete_button_has_material_title(self):
        """削除ボタンに data-material-title 属性が含まれること。"""
        assert "data-material-title" in self.js

    def test_delete_btn_click_calls_modal(self):
        """削除ボタンのクリックで openDeleteConfirmModal が呼ばれること。"""
        assert 'openDeleteConfirmModal("material"' in self.js

    def test_materials_empty_state_colspan_5(self):
        """教材0件時のcolspanが5に更新されていること。"""
        assert 'colspan="5"' in self.js


class TestAdminJSDeleteCourse:
    """admin.js にコース削除機能のロジックが存在することを検証する。"""

    @pytest.fixture(autouse=True)
    def _load_js(self):
        self.js = ADMIN_JS.read_text(encoding="utf-8")

    def test_course_delete_btn_exists(self):
        """コース一覧にcourse-delete-btn クラスが存在すること。"""
        assert "course-delete-btn" in self.js

    def test_course_delete_btn_has_course_id(self):
        """コース削除ボタンに data-course-id 属性が含まれること。"""
        assert 'data-course-id' in self.js

    def test_course_delete_btn_has_course_title(self):
        """コース削除ボタンに data-course-title 属性が含まれること。"""
        assert 'data-course-title' in self.js

    def test_course_delete_calls_modal(self):
        """コース削除ボタンのクリックで openDeleteConfirmModal("course") が呼ばれること。"""
        assert 'openDeleteConfirmModal("course"' in self.js

    def test_course_delete_stops_propagation(self):
        """コース削除ボタンのクリックで e.stopPropagation() が呼ばれること。"""
        assert "e.stopPropagation()" in self.js


class TestAdminJSDeleteConfirmModal:
    """admin.js の openDeleteConfirmModal 関数を検証する。"""

    @pytest.fixture(autouse=True)
    def _load_js(self):
        self.js = ADMIN_JS.read_text(encoding="utf-8")

    def test_function_defined(self):
        """openDeleteConfirmModal 関数が定義されていること。"""
        assert "function openDeleteConfirmModal" in self.js

    def test_modal_element_id(self):
        """モーダルが delete-confirm-modal ID で作成されること。"""
        assert "delete-confirm-modal" in self.js

    def test_confirm_input_exists(self):
        """名前確認用入力フィールドが存在すること。"""
        assert "delete-confirm-input" in self.js

    def test_exec_button_exists(self):
        """実行ボタンが存在すること。"""
        assert "delete-exec-btn" in self.js

    def test_cancel_button_exists(self):
        """キャンセルボタンが存在すること。"""
        assert "delete-cancel-btn" in self.js

    def test_exec_button_initially_disabled(self):
        """削除ボタンが初期状態で無効化されていること。"""
        assert 'id="delete-exec-btn"' in self.js
        assert "disabled" in self.js

    def test_input_validation_enables_button(self):
        """入力がname と一致したときにボタンが有効化されるロジックがあること。"""
        assert "input.value !== name" in self.js

    def test_delete_api_endpoint_material(self):
        """教材削除時に /admin/materials/ エンドポイントが使用されること。"""
        assert '"/admin/materials/"' in self.js

    def test_delete_api_endpoint_course(self):
        """コース削除時に /admin/courses/ エンドポイントが使用されること。"""
        assert '"/admin/courses/"' in self.js

    def test_confirm_name_sent_in_body(self):
        """confirm_name がリクエストボディに含まれること。"""
        assert "confirm_name:" in self.js

    def test_delete_method_used(self):
        """DELETE メソッドが使用されること。"""
        assert '"DELETE"' in self.js

    def test_material_warning_text(self):
        """教材削除時のカスケード警告テキストが表示されること。"""
        assert "紐づくコースも同時に削除" in self.js

    def test_course_warning_text(self):
        """コース削除時の学習履歴削除警告テキストが表示されること。"""
        assert "学習履歴も削除されます" in self.js

    def test_success_reloads_materials(self):
        """教材削除成功後にloadMaterialsが呼ばれること。"""
        assert "loadMaterials()" in self.js

    def test_cascade_deletion_count_shown(self):
        """カスケード削除されたコース数が表示されること。"""
        assert "deleted_courses" in self.js

    def test_error_displayed_on_failure(self):
        """削除失敗時にエラーメッセージが表示されること。"""
        assert "delete-confirm-error" in self.js


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
class TestDeleteMaterial:
    """DELETE /api/admin/materials/{material_id} エンドポイントのテスト。"""

    @patch("routes.admin._pg_session")
    def test_delete_material_success(self, mock_session, client, auth_headers):
        """教材削除が正常に行われること。"""
        mock_pg = MagicMock()
        # fetchone for document lookup
        mock_pg.execute.return_value.fetchone.return_value = (
            "doc-uuid-1",     # id
            "テスト教材",      # title
            "test.pdf",       # filename
            "mat-001",        # source_path
        )
        # fetchall for courses (no linked courses)
        mock_pg.execute.return_value.fetchall.return_value = []
        mock_session.return_value = mock_pg

        resp = client.request(
            "DELETE",
            "/api/admin/materials/mat-001",
            json={"confirm_name": "テスト教材"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["material_id"] == "mat-001"
        assert data["deleted"] is True
        assert data["deleted_courses"] == []
        mock_pg.commit.assert_called_once()

    @patch("routes.admin._pg_session")
    def test_delete_material_name_mismatch(self, mock_session, client, auth_headers):
        """確認名が一致しない場合に400が返ること。"""
        mock_pg = MagicMock()
        mock_pg.execute.return_value.fetchone.return_value = (
            "doc-uuid-1",
            "テスト教材",
            "test.pdf",
            "mat-001",
        )
        mock_session.return_value = mock_pg

        resp = client.request(
            "DELETE",
            "/api/admin/materials/mat-001",
            json={"confirm_name": "間違った名前"},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "一致しません" in resp.json()["detail"]

    @patch("routes.admin._pg_session")
    def test_delete_material_not_found(self, mock_session, client, auth_headers):
        """存在しない教材で404が返ること。"""
        mock_pg = MagicMock()
        mock_pg.execute.return_value.fetchone.return_value = None
        mock_session.return_value = mock_pg

        resp = client.request(
            "DELETE",
            "/api/admin/materials/nonexistent",
            json={"confirm_name": "何か"},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    @patch("routes.admin._pg_session")
    def test_delete_material_cascade_courses(self, mock_session, client, auth_headers):
        """教材削除時に紐づくコースもカスケード削除されること。"""
        mock_pg = MagicMock()

        # We need different return values for sequential execute calls:
        # 1st call: document lookup (fetchone)
        # 2nd call: course list (fetchall)
        # 3rd call: course data lookup (fetchone) for each course
        # 4th+: DELETE statements

        call_count = {"n": 0}
        original_execute = mock_pg.execute

        def side_effect_execute(*args, **kwargs):
            call_count["n"] += 1
            result = MagicMock()
            n = call_count["n"]
            if n == 1:
                # Document lookup
                result.fetchone.return_value = (
                    "doc-uuid-1", "テスト教材", "test.pdf", "mat-001"
                )
            elif n == 2:
                # Course list
                result.fetchall.return_value = [("course-1",), ("course-2",)]
            elif n == 3:
                # Course-1 data with linked material
                result.fetchone.return_value = (
                    json.dumps({
                        "sources": [{"material_id": "mat-001", "title": "テスト教材"}]
                    }),
                )
            elif n == 4:
                # Delete chat history for course-1
                result.rowcount = 0
            elif n == 5:
                # Delete course-1
                result.rowcount = 1
            elif n == 6:
                # Course-2 data with different material
                result.fetchone.return_value = (
                    json.dumps({
                        "sources": [{"material_id": "mat-999", "title": "別の教材"}]
                    }),
                )
            elif n == 7:
                # Delete chunks
                result.rowcount = 5
            elif n == 8:
                # Delete document
                result.rowcount = 1
            return result

        mock_pg.execute.side_effect = side_effect_execute
        mock_session.return_value = mock_pg

        resp = client.request(
            "DELETE",
            "/api/admin/materials/mat-001",
            json={"confirm_name": "テスト教材"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["deleted"] is True
        # course-1 is linked, course-2 is not
        assert "course-1" in data["deleted_courses"]
        assert "course-2" not in data["deleted_courses"]
        mock_pg.commit.assert_called_once()

    def test_delete_material_requires_auth(self, client):
        """認証なしで401/403が返ること。"""
        resp = client.request(
            "DELETE",
            "/api/admin/materials/mat-001",
            json={"confirm_name": "テスト"},
        )
        assert resp.status_code in (401, 403)


@_skip_no_fastapi
class TestDeleteCourse:
    """DELETE /api/admin/courses/{course_id} エンドポイントのテスト。"""

    @patch("routes.admin._pg_session")
    def test_delete_course_success(self, mock_session, client, auth_headers):
        """コース削除が正常に行われること。"""
        mock_pg = MagicMock()
        mock_pg.execute.return_value.fetchone.return_value = (
            "course-001",
            "量子力学入門",
        )
        mock_session.return_value = mock_pg

        resp = client.request(
            "DELETE",
            "/api/admin/courses/course-001",
            json={"confirm_name": "量子力学入門"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["course_id"] == "course-001"
        assert data["deleted"] is True
        mock_pg.commit.assert_called_once()

    @patch("routes.admin._pg_session")
    def test_delete_course_name_mismatch(self, mock_session, client, auth_headers):
        """確認名が一致しない場合に400が返ること。"""
        mock_pg = MagicMock()
        mock_pg.execute.return_value.fetchone.return_value = (
            "course-001",
            "量子力学入門",
        )
        mock_session.return_value = mock_pg

        resp = client.request(
            "DELETE",
            "/api/admin/courses/course-001",
            json={"confirm_name": "間違ったコース名"},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "一致しません" in resp.json()["detail"]

    @patch("routes.admin._pg_session")
    def test_delete_course_not_found(self, mock_session, client, auth_headers):
        """存在しないコースで404が返ること。"""
        mock_pg = MagicMock()
        mock_pg.execute.return_value.fetchone.return_value = None
        mock_session.return_value = mock_pg

        resp = client.request(
            "DELETE",
            "/api/admin/courses/nonexistent",
            json={"confirm_name": "何か"},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    @patch("routes.admin._pg_session")
    def test_delete_course_deletes_chat_history(self, mock_session, client, auth_headers):
        """コース削除時にチャット履歴も削除されること。"""
        mock_pg = MagicMock()

        call_count = {"n": 0}

        def side_effect_execute(*args, **kwargs):
            call_count["n"] += 1
            result = MagicMock()
            if call_count["n"] == 1:
                # Course lookup
                result.fetchone.return_value = ("course-001", "量子力学入門")
            return result

        mock_pg.execute.side_effect = side_effect_execute
        mock_session.return_value = mock_pg

        resp = client.request(
            "DELETE",
            "/api/admin/courses/course-001",
            json={"confirm_name": "量子力学入門"},
            headers=auth_headers,
        )
        assert resp.status_code == 200

        # Verify that DELETE FROM learning_chat_history was called
        executed_queries = [
            str(c[0][0]) for c in mock_pg.execute.call_args_list
        ]
        chat_history_deleted = any(
            "learning_chat_history" in q for q in executed_queries
        )
        assert chat_history_deleted, "learning_chat_history の DELETE が呼ばれていません"

    def test_delete_course_requires_auth(self, client):
        """認証なしで401/403が返ること。"""
        resp = client.request(
            "DELETE",
            "/api/admin/courses/course-001",
            json={"confirm_name": "テスト"},
        )
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


class TestDeleteConfirmRequestSchema:
    """DeleteConfirmRequest スキーマのテスト。"""

    def test_schema_import(self):
        """DeleteConfirmRequest がインポートできること。"""
        import sys

        backend_dir = str(Path(__file__).resolve().parents[1])
        api_dir = str(Path(__file__).resolve().parents[1] / "api")
        if backend_dir not in sys.path:
            sys.path.insert(0, backend_dir)
        if api_dir not in sys.path:
            sys.path.insert(0, api_dir)

        from schemas import DeleteConfirmRequest

        req = DeleteConfirmRequest(confirm_name="テスト教材")
        assert req.confirm_name == "テスト教材"

    def test_schema_requires_confirm_name(self):
        """confirm_name が必須フィールドであること。"""
        import sys

        backend_dir = str(Path(__file__).resolve().parents[1])
        api_dir = str(Path(__file__).resolve().parents[1] / "api")
        if backend_dir not in sys.path:
            sys.path.insert(0, backend_dir)
        if api_dir not in sys.path:
            sys.path.insert(0, api_dir)

        from pydantic import ValidationError
        from schemas import DeleteConfirmRequest

        with pytest.raises(ValidationError):
            DeleteConfirmRequest()
