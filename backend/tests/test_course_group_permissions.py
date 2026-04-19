"""Issue #125: コースのグループ単位での共有・権限設定のテスト。

対象:
  - `backend/db/010_course_group_permissions.sql` (マイグレーション)
  - `backend/api/routes/admin.py` の GET/POST/DELETE /courses/{id}/groups
  - `backend/api/routes/learning.py` の list_courses (viewer 権限コース統合)
  - `backend/api/services.py` の user_can_edit_course / user_can_view_course
  - フロントエンドの「コース管理」タブと共有設定モーダル
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIR = PROJECT_ROOT / "frontend" / "public"
ADMIN_HTML = FRONTEND_DIR / "admin.html"
ADMIN_JS = FRONTEND_DIR / "js" / "admin.js"
BACKEND_DIR = PROJECT_ROOT / "backend"
ADMIN_PY = BACKEND_DIR / "api" / "routes" / "admin.py"
LEARNING_PY = BACKEND_DIR / "api" / "routes" / "learning.py"
SERVICES_PY = BACKEND_DIR / "api" / "services.py"
SCHEMAS_PY = BACKEND_DIR / "api" / "schemas.py"
MIG_SQL = BACKEND_DIR / "db" / "010_course_group_permissions.sql"
MAIN_PY = BACKEND_DIR / "api" / "main.py"


# ---------------------------------------------------------------------------
# Migration (DDL)
# ---------------------------------------------------------------------------


class TestMigration010:
    @pytest.fixture(autouse=True)
    def _load(self):
        assert MIG_SQL.exists(), "010_course_group_permissions.sql が存在しません"
        self.sql = MIG_SQL.read_text(encoding="utf-8")

    def test_table_created(self):
        assert re.search(
            r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+course_group_permissions",
            self.sql,
            re.IGNORECASE,
        )

    def test_course_foreign_key(self):
        assert "REFERENCES learning_courses(id)" in self.sql

    def test_group_foreign_key(self):
        assert "REFERENCES groups(id)" in self.sql

    def test_permission_check_constraint(self):
        assert "'viewer'" in self.sql and "'editor'" in self.sql

    def test_primary_key_composite(self):
        assert "PRIMARY KEY (course_id, group_id)" in self.sql

    def test_indexes_present(self):
        assert "idx_cgp_course" in self.sql
        assert "idx_cgp_group" in self.sql

    def test_applied_in_main_migrations(self):
        """main.py の _run_migrations() にこのマイグレーションが含まれること。"""
        assert MAIN_PY.exists()
        content = MAIN_PY.read_text(encoding="utf-8")
        assert "course_group_permissions" in content


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class TestSchemas:
    @pytest.fixture(autouse=True)
    def _load(self):
        self.src = SCHEMAS_PY.read_text(encoding="utf-8")

    def test_permission_out_model(self):
        assert "class CourseGroupPermissionOut" in self.src

    def test_permission_upsert_model(self):
        assert "class CourseGroupPermissionUpsertRequest" in self.src


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------


class TestServices:
    @pytest.fixture(autouse=True)
    def _load(self):
        self.src = SERVICES_PY.read_text(encoding="utf-8")

    def test_get_permissions_helper(self):
        assert "def get_course_group_permissions" in self.src

    def test_user_can_edit_course_helper(self):
        assert "def user_can_edit_course" in self.src

    def test_user_can_view_course_helper(self):
        assert "def user_can_view_course" in self.src

    def test_editor_check_uses_group_members(self):
        """editor 判定は course_group_permissions × group_members JOIN で行う。"""
        assert "course_group_permissions" in self.src
        assert "group_members" in self.src
        assert "'editor'" in self.src


# ---------------------------------------------------------------------------
# Admin routes
# ---------------------------------------------------------------------------


class TestAdminRoutes:
    @pytest.fixture(autouse=True)
    def _load(self):
        self.src = ADMIN_PY.read_text(encoding="utf-8")

    def test_publish_endpoint_removed(self):
        """旧 PUT /courses/{id}/publish は削除されていること。"""
        assert '"/courses/{course_id}/publish"' not in self.src

    def test_get_permissions_endpoint(self):
        assert re.search(
            r'@router\.get\(\s*\n?\s*["\']/courses/\{course_id\}/groups["\']',
            self.src,
        )

    def test_upsert_endpoint(self):
        assert re.search(
            r'@router\.post\(\s*\n?\s*["\']/courses/\{course_id\}/groups["\']',
            self.src,
        )

    def test_delete_endpoint(self):
        assert '@router.delete("/courses/{course_id}/groups/{group_id}"' in self.src

    def test_teacher_list_includes_editor_role(self):
        """list_teacher_courses が editor 権限グループ経由のコースも返すこと。"""
        assert "course_group_permissions" in self.src
        assert "'editor'" in self.src
        assert "role" in self.src

    def test_draft_access_uses_editor_check(self):
        assert "user_can_edit_course" in self.src


# ---------------------------------------------------------------------------
# Learning routes (student-facing)
# ---------------------------------------------------------------------------


class TestLearningRoutes:
    @pytest.fixture(autouse=True)
    def _load(self):
        self.src = LEARNING_PY.read_text(encoding="utf-8")

    def test_list_courses_joins_permissions(self):
        """list_courses が course_group_permissions 経由の受講可能コースを返す。"""
        assert "course_group_permissions" in self.src

    def test_list_courses_filters_viewer_or_editor(self):
        assert "'viewer'" in self.src and "'editor'" in self.src

    def test_enroll_uses_view_helper(self):
        assert "user_can_view_course" in self.src


# ---------------------------------------------------------------------------
# Frontend HTML
# ---------------------------------------------------------------------------


class TestAdminHtml:
    @pytest.fixture(autouse=True)
    def _load(self):
        self.html = ADMIN_HTML.read_text(encoding="utf-8")

    def test_course_management_tab_button(self):
        assert 'data-tab="course-management"' in self.html
        assert "コース管理" in self.html

    def test_course_management_tab_panel(self):
        assert 'id="tab-course-management"' in self.html

    def test_course_management_table(self):
        assert 'id="cm-table"' in self.html
        assert 'id="cm-tbody"' in self.html

    def test_refresh_button(self):
        assert 'id="cm-refresh"' in self.html


# ---------------------------------------------------------------------------
# Frontend JS
# ---------------------------------------------------------------------------


class TestAdminJs:
    @pytest.fixture(autouse=True)
    def _load(self):
        self.js = ADMIN_JS.read_text(encoding="utf-8")

    def test_publish_button_removed(self):
        assert "学生に公開する" not in self.js
        assert 'id="cb-publish-btn"' not in self.js

    def test_publish_function_removed(self):
        assert "function publishCourse" not in self.js
        assert "/admin/courses/" + '" + courseId + "/publish' not in self.js

    def test_init_course_management_registered(self):
        assert "function initCourseManagement" in self.js
        assert "initCourseManagement()" in self.js

    def test_load_course_management_function(self):
        assert "function loadCourseManagement" in self.js

    def test_open_permission_modal_function(self):
        assert "function openPermissionModal" in self.js

    def test_add_permission_calls_post(self):
        assert "function addPermissionMapping" in self.js
        # POST /admin/courses/<id>/groups を呼ぶ
        assert re.search(r"/admin/courses/\"\s*\+\s*encodeURIComponent\(courseId\)\s*\+\s*\"/groups\"", self.js)

    def test_remove_permission_calls_delete(self):
        assert "function removePermissionMapping" in self.js
        assert 'method: "DELETE"' in self.js

    def test_viewer_editor_options_in_modal(self):
        """権限選択 UI に viewer / editor の両方が提示されること。"""
        assert 'value="viewer"' in self.js
        assert 'value="editor"' in self.js

    def test_publish_badge_removed(self):
        """[公開中] バッジが一覧から取り除かれていること。"""
        assert "[公開中]" not in self.js
