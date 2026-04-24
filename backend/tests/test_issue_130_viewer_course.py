"""Issue #130: 共有コース関連の権限まわりの不具合修正。

対象:
  - `backend/api/services.py`
    - `user_owns_course` を新設（オーナー判定のみ）
    - `user_can_edit_course` は owner または editor
    - `user_can_view_course` は owner / editor / viewer
  - `backend/api/routes/admin.py`
    - `list_course_group_permissions` → `user_can_view_course`
      （viewer にも共有設定を読み取らせて、UI の「未設定」誤表示を解消）
    - `upsert_course_group_permission` → `user_owns_course`
      （editor が共有設定を書き換えられてしまう不具合の修正）
    - `delete_course_group_permission` → `user_owns_course`
  - `frontend/public/js/admin.js`
    - role === 'viewer' に対応するバッジ表示
    - 非オーナーには「共有設定」ボタンを出さない
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = PROJECT_ROOT / "backend"
SERVICES_PY = BACKEND_DIR / "api" / "services.py"
ADMIN_PY = BACKEND_DIR / "api" / "routes" / "admin.py"
ADMIN_JS = PROJECT_ROOT / "frontend" / "public" / "js" / "admin.js"


# ---------------------------------------------------------------------------
# Fix 1: services.py — 認可ヘルパを3段階に整理
# ---------------------------------------------------------------------------


class TestServicesOwnershipHelpers:
    @pytest.fixture(autouse=True)
    def _load(self):
        self.src = SERVICES_PY.read_text(encoding="utf-8")

    def _body(self, name: str) -> str:
        m = re.search(
            rf"def\s+{re.escape(name)}\b.*?(?=\n(?:def |class )\b)",
            self.src,
            re.DOTALL,
        )
        assert m, f"{name} の関数本体が抽出できない"
        return m.group(0)

    def test_defines_user_owns_course(self):
        assert re.search(
            r"def\s+user_owns_course\s*\(\s*user_id\s*:\s*str\s*,\s*course_id\s*:\s*str\s*\)",
            self.src,
        )

    def test_user_owns_course_checks_only_owner(self):
        body = self._body("user_owns_course")
        # learning_courses.user_id との一致だけをチェックしていること
        assert "learning_courses" in body
        assert "user_id = CAST(:uid AS uuid)" in body
        # group_members / course_group_permissions は使わない
        assert "course_group_permissions" not in body
        assert "group_members" not in body

    def test_user_can_edit_course_delegates_to_owns(self):
        body = self._body("user_can_edit_course")
        assert "user_owns_course" in body
        # editor 権限の確認は残っていること
        assert "'editor'" in body

    def test_user_can_edit_course_does_not_accept_viewer(self):
        body = self._body("user_can_edit_course")
        # editor 判定で viewer を混ぜない（IN ('editor','viewer') にしないこと）
        assert not re.search(r"IN\s*\(\s*'editor'\s*,\s*'viewer'\s*\)", body)

    def test_user_can_view_course_accepts_viewer(self):
        body = self._body("user_can_view_course")
        assert "'viewer'" in body
        # editor/所有者も当然含む（user_can_edit_course 経由）
        assert "user_can_edit_course" in body


# ---------------------------------------------------------------------------
# Fix 2: admin.py — 共有設定エンドポイントの認可の三段分け
# ---------------------------------------------------------------------------


class TestAdminCourseGroupPermissionsAuthz:
    @pytest.fixture(autouse=True)
    def _load(self):
        self.src = ADMIN_PY.read_text(encoding="utf-8")

    def _body(self, name: str) -> str:
        m = re.search(
            rf"def\s+{re.escape(name)}\b.*?(?=\n(?:def |class |@router\.)\b|\Z)",
            self.src,
            re.DOTALL,
        )
        assert m, f"{name} の関数本体が抽出できない"
        return m.group(0)

    def test_imports_new_helpers(self):
        assert "user_owns_course" in self.src
        assert "user_can_view_course" in self.src

    def test_list_uses_can_view(self):
        body = self._body("list_course_group_permissions")
        assert "user_can_view_course" in body
        # owner/editor 限定だった古いガードは使わない
        assert "user_can_edit_course" not in body

    def test_upsert_uses_owns(self):
        body = self._body("upsert_course_group_permission")
        assert "user_owns_course" in body
        assert "user_can_edit_course" not in body

    def test_delete_uses_owns(self):
        body = self._body("delete_course_group_permission")
        assert "user_owns_course" in body
        assert "user_can_edit_course" not in body


# ---------------------------------------------------------------------------
# Fix 3: admin.js — viewer バッジと、非オーナーに「共有設定」ボタンを出さない
# ---------------------------------------------------------------------------


class TestAdminJsRoleAwareUI:
    @pytest.fixture(autouse=True)
    def _load(self):
        self.src = ADMIN_JS.read_text(encoding="utf-8")

    def _render_body(self) -> str:
        m = re.search(
            r"function renderCourseManagementTable\b.*?\n  \}\n",
            self.src,
            re.DOTALL,
        )
        assert m, "renderCourseManagementTable の本体が抽出できない"
        return m.group(0)

    def test_viewer_role_branch_exists(self):
        body = self._render_body()
        assert re.search(r"c\.role\s*===\s*['\"]viewer['\"]", body), (
            "viewer 用のロールバッジ分岐が無い"
        )

    def test_manage_button_gated_by_owner(self):
        body = self._render_body()
        # 「共有設定」ボタンが owner 分岐の中に現れる（非 owner では出さない）形になっていること
        assert re.search(r"c\.role\s*===\s*['\"]owner['\"]", body)
        assert "共有設定" in body
        # 非オーナー向けに読み取り専用のヒントが表示されること
        assert "所有者のみ変更可" in body
