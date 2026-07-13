"""Issue #129: 共有コース(viewer)が教員UIに表示されない・原稿スタジオで読み込みエラーになる不具合の修正。

対象:
  - `backend/api/services.py` — 共有コース用データ取得ヘルパ
    (`get_editable_course_data`, `get_viewable_course_data`)
  - `backend/api/routes/admin.py` の `list_teacher_courses`
    (viewer 権限グループ経由のコースも一覧に含める)
  - `backend/api/routes/lecture_studio.py`
    (原稿スタジオが owner 限定の `get_course_data` を使っていた問題を修正)
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = PROJECT_ROOT / "backend"
SERVICES_PY = BACKEND_DIR / "api" / "services.py"
ADMIN_PY = BACKEND_DIR / "api" / "routes" / "admin.py"
# Tier 3-17a: get_course_scripts / batch_generate_scripts / batch_generate_audio は
# lecture_studio パッケージの scripts.py に移設された。
LECTURE_STUDIO_PY = BACKEND_DIR / "api" / "routes" / "lecture_studio" / "scripts.py"


# ---------------------------------------------------------------------------
# Fix 1: services.py — 共有コース用データ取得ヘルパの追加
# ---------------------------------------------------------------------------


class TestServicesSharedCourseHelpers:
    @pytest.fixture(autouse=True)
    def _load(self):
        self.src = SERVICES_PY.read_text(encoding="utf-8")

    def test_defines_get_editable_course_data(self):
        assert re.search(
            r"def\s+get_editable_course_data\s*\(\s*user_id\s*:\s*str\s*,\s*course_id\s*:\s*str\s*\)",
            self.src,
        )

    def test_defines_get_viewable_course_data(self):
        assert re.search(
            r"def\s+get_viewable_course_data\s*\(\s*user_id\s*:\s*str\s*,\s*course_id\s*:\s*str\s*\)",
            self.src,
        )

    def test_editable_uses_user_can_edit_course(self):
        m = re.search(
            r"def\s+get_editable_course_data\b.*?(?=\n(?:def |class )\b)",
            self.src,
            re.DOTALL,
        )
        assert m, "get_editable_course_data 本体が抽出できない"
        body = m.group(0)
        assert "user_can_edit_course" in body

    def test_viewable_uses_user_can_view_course(self):
        m = re.search(
            r"def\s+get_viewable_course_data\b.*?(?=\n(?:def |class )\b)",
            self.src,
            re.DOTALL,
        )
        assert m, "get_viewable_course_data 本体が抽出できない"
        body = m.group(0)
        assert "user_can_view_course" in body


# ---------------------------------------------------------------------------
# Fix 2: admin.py list_teacher_courses — viewer を含める
# ---------------------------------------------------------------------------


class TestAdminListTeacherCoursesViewer:
    @pytest.fixture(autouse=True)
    def _load(self):
        self.src = ADMIN_PY.read_text(encoding="utf-8")

    def _body(self) -> str:
        m = re.search(
            r"def list_teacher_courses\b.*?(?=\n(?:def |class )\b)",
            self.src,
            re.DOTALL,
        )
        assert m, "list_teacher_courses 関数本体が抽出できない"
        return m.group(0)

    def test_includes_viewer_in_where(self):
        body = self._body()
        # cgp.permission IN ('editor', 'viewer') の形を検出
        assert re.search(
            r"cgp\.permission\s+IN\s*\(\s*'editor'\s*,\s*'viewer'\s*\)",
            body,
        ), "list_teacher_courses の WHERE に viewer が含まれていない"

    def test_role_uses_cgp_permission(self):
        body = self._body()
        # 修正: LEFT JOINの増殖を防ぐ新クエリにおいて、グループ権限（editor/viewer）に基づく
        # roleの動的出し分けが正しく実装されていることを確認する
        assert "cgp.permission = 'editor'" in body, (
            "editor権限を判定するロジックが含まれていません"
        )
        assert "ELSE 'viewer'" in body, (
            "roleのフォールバック（viewer）が正しく設定されていません"
        )

    def test_role_comment_mentions_viewer(self):
        body = self._body()
        # Python 側のマッピングで role の取り得る値として viewer が明示されていること
        assert "viewer" in body


# ---------------------------------------------------------------------------
# Fix 3: lecture_studio.py — 共有コース対応
# ---------------------------------------------------------------------------


class TestLectureStudioSharedAccess:
    @pytest.fixture(autouse=True)
    def _load(self):
        self.src = LECTURE_STUDIO_PY.read_text(encoding="utf-8")

    def test_imports_editable_and_viewable(self):
        assert "get_editable_course_data" in self.src
        assert "get_viewable_course_data" in self.src

    def _func_body(self, name: str) -> str:
        m = re.search(
            rf"def {re.escape(name)}\b.*?(?=\n(?:def |class |@)\b|\Z)",
            self.src,
            re.DOTALL,
        )
        assert m, f"{name} 関数本体が抽出できない"
        return m.group(0)

    def test_get_course_scripts_uses_viewable(self):
        """GET /lecture-scripts は viewer でも読めること。"""
        body = self._func_body("get_course_scripts")
        assert "get_viewable_course_data" in body
        # owner-only の get_course_data を直接呼んでいないこと
        assert not re.search(r"\bget_course_data\s*\(", body)

    def test_batch_generate_scripts_uses_editable(self):
        """POST /lecture-scripts/generate は editor 以上のみ。"""
        body = self._func_body("batch_generate_scripts")
        assert "get_editable_course_data" in body
        assert not re.search(r"\bget_course_data\s*\(", body)

    def test_batch_generate_audio_uses_editable(self):
        """POST /lecture-audio/generate は editor 以上のみ。"""
        body = self._func_body("batch_generate_audio")
        assert "get_editable_course_data" in body
        assert not re.search(r"\bget_course_data\s*\(", body)
