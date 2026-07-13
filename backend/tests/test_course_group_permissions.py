"""Issue #125: コースのグループ単位での共有・権限設定のテスト。

対象:
  - `backend/db/010_course_group_permissions.sql`
    （2026-07 アーキテクチャ整理 Tier 3-14 で migration 044 に統合済み。本ファイルは
    no-op スタブになっている。実テーブルの正本は `backend/db/044_object_group_permissions.sql`）
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
MIG_044_SQL = BACKEND_DIR / "db" / "044_object_group_permissions.sql"
MIG_035_SQL = BACKEND_DIR / "db" / "035_document_group_permissions.sql"


# ---------------------------------------------------------------------------
# Migration (DDL)
# ---------------------------------------------------------------------------

_SQL_LINE_COMMENT_RE = re.compile(r"--[^\n]*")


def _executable_sql(sql: str) -> str:
    """行コメント（--...）を取り除いた、実行される可能性のある本文だけを返す。"""
    return _SQL_LINE_COMMENT_RE.sub("", sql)


class TestMigration010Stub:
    """010 は 2026-07 統合（Tier 3-14）で no-op スタブになっている。

    実テーブルの CREATE は行わない（044 が毎起動 INSERT...SELECT→DROP TABLE する
    往復を避けるため）。過去に適用済みだった意味を消すのではなく、現在の正本状態
    （テーブルは存在しない・044 に統合済み）に追従させたスタブであることを確認する。
    """

    @pytest.fixture(autouse=True)
    def _load(self):
        assert MIG_SQL.exists(), "010_course_group_permissions.sql が存在しません"
        self.sql = MIG_SQL.read_text(encoding="utf-8")

    def test_no_longer_creates_the_table(self):
        assert not re.search(r"\bCREATE\s+TABLE\b", _executable_sql(self.sql), re.IGNORECASE)

    def test_references_044_as_source_of_truth(self):
        assert "044" in self.sql


class TestMigration035Stub:
    """035（document_group_permissions）も 010 と同様に no-op スタブになっている。"""

    @pytest.fixture(autouse=True)
    def _load(self):
        assert MIG_035_SQL.exists(), "035_document_group_permissions.sql が存在しません"
        self.sql = MIG_035_SQL.read_text(encoding="utf-8")

    def test_no_longer_creates_the_table(self):
        assert not re.search(r"\bCREATE\s+TABLE\b", _executable_sql(self.sql), re.IGNORECASE)

    def test_references_044_as_source_of_truth(self):
        assert "044" in self.sql


class TestMigration044:
    """object_group_permissions（course_group_permissions + document_group_permissions
    の統合先）の静的スキーマ検査。"""

    @pytest.fixture(autouse=True)
    def _load(self):
        assert MIG_044_SQL.exists(), "044_object_group_permissions.sql が存在しません"
        self.sql = MIG_044_SQL.read_text(encoding="utf-8")

    def test_table_created(self):
        assert re.search(
            r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+object_group_permissions",
            self.sql,
            re.IGNORECASE,
        )

    def test_object_type_check_constraint(self):
        assert re.search(
            r"object_type\s+TEXT\s+NOT\s+NULL\s+CHECK\s*\(\s*object_type\s+IN\s*\(\s*'course'\s*,\s*'document'\s*\)\s*\)",
            self.sql,
        )

    def test_object_id_is_text_without_fk(self):
        assert re.search(r"object_id\s+TEXT\s+NOT\s+NULL", self.sql)
        # object_id 自体への REFERENCES が無いこと（ポリモーフィックなので FK を張れない）
        object_id_line = next(
            line for line in self.sql.splitlines() if re.search(r"^\s*object_id\s+TEXT", line)
        )
        assert "REFERENCES" not in object_id_line

    def test_group_foreign_key(self):
        assert "REFERENCES groups(id) ON DELETE CASCADE" in self.sql

    def test_permission_check_constraint(self):
        assert re.search(
            r"CHECK\s*\(\s*permission\s+IN\s*\(\s*'viewer'\s*,\s*'editor'\s*\)\s*\)",
            self.sql,
        )

    def test_primary_key_composite(self):
        assert "PRIMARY KEY (object_type, object_id, group_id)" in self.sql

    def test_indexes_present(self):
        assert "idx_ogp_object" in self.sql
        assert "idx_ogp_group" in self.sql
        assert "idx_ogp_group_permission" in self.sql

    def test_migrates_data_from_course_group_permissions(self):
        """旧 course_group_permissions からの移行 DO ブロックが存在すること。"""
        assert "course_group_permissions" in self.sql
        assert re.search(r"INSERT\s+INTO\s+object_group_permissions", self.sql, re.IGNORECASE)
        assert re.search(r"'course'\s*,\s*course_id", self.sql)
        assert re.search(r"DROP\s+TABLE\s+course_group_permissions", self.sql, re.IGNORECASE)

    def test_migrates_data_from_document_group_permissions(self):
        """旧 document_group_permissions からの移行 DO ブロックが存在すること。
        document_id は ::text で正規化してから移すこと。
        """
        assert "document_group_permissions" in self.sql
        assert re.search(r"'document'\s*,\s*document_id::text", self.sql)
        assert re.search(r"DROP\s+TABLE\s+document_group_permissions", self.sql, re.IGNORECASE)

    def test_data_migration_guarded_by_information_schema_existence_check(self):
        """旧テーブルが既に無い環境での再実行が no-op になるよう、
        information_schema.tables で存在確認してから移行・DROP すること。"""
        assert self.sql.count("information_schema.tables") >= 2

    def test_on_conflict_do_nothing_for_idempotent_reapply(self):
        assert "ON CONFLICT (object_type, object_id, group_id) DO NOTHING" in self.sql


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
        """editor 判定は object_group_permissions（object_type='course'）× group_members
        JOIN で行う。"""
        assert "object_group_permissions" in self.src
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
        assert "object_group_permissions" in self.src
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
        """list_courses が object_group_permissions（object_type='course'）経由の
        受講可能コースを返す。"""
        assert "object_group_permissions" in self.src

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


# ---------------------------------------------------------------------------
# 統合後ガードレール（アーキテクチャ整理 Tier 3-14）
# ---------------------------------------------------------------------------
# course_group_permissions と document_group_permissions は migration 044 で
# object_group_permissions に統合された。旧テーブル名が backend/ の実装コードに
# 残っていないこと、および FK CASCADE が失われた分の後始末（明示 DELETE）が
# 各削除経路に入っていることを構造的に保証する。


_OLD_TABLE_NAME_RE = re.compile(r"\b(course_group_permissions|document_group_permissions)\b")


def _backend_python_files_outside_tests():
    """backend/**/*.py のうち tests/ 配下・.venv・__pycache__ を除いたものを返す。

    テスト自身（本ファイルや db/*.sql を検査するだけのテスト群）は「旧テーブル名を
    知っている」ことが目的なので対象外にする。migration SQL（backend/db/*.sql）は
    そもそも *.py ではないため自然に対象外。
    """
    for path in sorted(BACKEND_DIR.rglob("*.py")):
        rel_parts = path.relative_to(BACKEND_DIR).parts
        if rel_parts[0] in ("tests", ".venv", "__pycache__"):
            continue
        if any(part in (".venv", "__pycache__") for part in rel_parts):
            continue
        yield path


class TestOldGroupPermissionTableNamesRemoved:
    """旧テーブル名（course_group_permissions / document_group_permissions）が
    backend/ の実装コード（migration SQL・テスト自身を除く）に残っていないこと。

    単語境界付き正規表現（``\\b...\\b``）で検査するため、`get_course_group_permissions`
    のような**関数名**（本タスクでシグネチャ変更禁止のため意図的に残す識別子）は
    誤検出しない。実際に SQL 文字列やコメントで table 名を独立トークンとして
    書いている箇所だけを違反として拾う。
    """

    def test_no_old_table_name_outside_migrations_and_tests(self):
        violations: list[str] = []
        for path in _backend_python_files_outside_tests():
            text = path.read_text(encoding="utf-8")
            for m in _OLD_TABLE_NAME_RE.finditer(text):
                line_no = text.count("\n", 0, m.start()) + 1
                violations.append(f"{path.relative_to(PROJECT_ROOT)}:{line_no}: {m.group(0)}")
        assert violations == [], (
            f"{len(violations)} 件の旧テーブル名参照が見つかりました:\n" + "\n".join(violations)
        )


class TestExplicitDeleteReplacesLostCascade:
    """object_group_permissions は course_id/document_id への FK を持たない
    ポリモーフィックテーブルのため、旧テーブルが持っていた ON DELETE CASCADE の
    代替として、各削除経路が明示的に DELETE FROM object_group_permissions を
    行うことを保証する（孤児行防止。統合の最重要注意点）。
    """

    def test_delete_material_cleans_up_course_and_document_permissions(self):
        src = ADMIN_PY.read_text(encoding="utf-8")
        body = src.split("def delete_material")[1].split("\n@router")[0]
        assert body.count("DELETE FROM object_group_permissions") >= 2
        assert "object_type = 'course'" in body
        assert "object_type = 'document'" in body

    def test_delete_course_endpoint_cleans_up_permissions(self):
        src = ADMIN_PY.read_text(encoding="utf-8")
        body = src.split("def delete_course(")[1].split("\n@router")[0]
        assert "DELETE FROM object_group_permissions" in body
        assert "object_type = 'course'" in body

    def test_learning_delete_course_data_cleans_up_permissions(self):
        src = SERVICES_PY.read_text(encoding="utf-8")
        body = src.split("def delete_course_data")[1].split("\ndef ")[0]
        assert "DELETE FROM object_group_permissions" in body
        assert "object_type = 'course'" in body

    def test_versioning_purge_course_cleans_up_permissions(self):
        deletion_py = BACKEND_DIR / "core" / "versioning" / "deletion.py"
        src = deletion_py.read_text(encoding="utf-8")
        body = src.split("def _purge_course")[1].split("\ndef ")[0]
        assert "DELETE FROM object_group_permissions" in body
        assert "object_type = 'course'" in body

    def test_versioning_purge_document_cleans_up_permissions(self):
        deletion_py = BACKEND_DIR / "core" / "versioning" / "deletion.py"
        src = deletion_py.read_text(encoding="utf-8")
        body = src.split("def _purge_document")[1].split("\ndef ")[0]
        # _purge_document は自分自身の document 権限だけでなく、巻き添えで
        # 削除する所有者のコース（course loop）の権限も後始末する。
        assert body.count("DELETE FROM object_group_permissions") >= 2
        assert "object_type = 'course'" in body
        assert "object_type = 'document'" in body
