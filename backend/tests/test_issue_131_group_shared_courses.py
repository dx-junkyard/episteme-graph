"""Issue #131: グループで共有されたコースが学習画面のプルダウン
（受講者側 UI）に表示されない不具合の修正確認。

対象:
  - `backend/api/routes/learning.py` の `list_courses`
    (object_group_permissions〔object_type='course'〕経由で共有されたコースも返すこと)
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LEARNING_PY = PROJECT_ROOT / "backend" / "api" / "routes" / "learning.py"


class TestListCoursesSharedViaPermissions:
    @pytest.fixture(autouse=True)
    def _load(self):
        self.src = LEARNING_PY.read_text(encoding="utf-8")

    def _list_courses_body(self) -> str:
        m = re.search(
            r"def list_courses\b.*?(?=\n(?:def |class |@)\b|\Z)",
            self.src,
            re.DOTALL,
        )
        assert m, "list_courses 関数本体が抽出できない"
        return m.group(0)

    def test_joins_object_group_permissions(self):
        body = self._list_courses_body()
        assert "object_group_permissions" in body, (
            "list_courses が object_group_permissions と JOIN していない"
        )
        assert "object_type = 'course'" in body

    def test_filters_viewer_or_editor(self):
        body = self._list_courses_body()
        assert re.search(
            r"cgp\.permission\s+IN\s*\(\s*'viewer'\s*,\s*'editor'\s*\)",
            body,
        ), "cgp.permission の絞り込みに viewer/editor が両方含まれていない"

    def test_uses_user_groups(self):
        body = self._list_courses_body()
        assert "user_groups" in body and "get_user_group_ids" in self.src, (
            "ユーザーの所属グループを利用してフィルタしていない"
        )

    def test_excludes_already_enrolled_templates(self):
        """既に受講済みのテンプレートは再度出さない。

        Issue #133 でクローン方式から learning_states 方式へ移行したため、
        既受講かどうかは learning_states のレコード存在で判定する。
        """
        body = self._list_courses_body()
        assert re.search(
            r"NOT\s+EXISTS\s*\(.*?FROM\s+learning_states\s+ls.*?ls\.course_id\s*=\s*lc\.id",
            body,
            re.DOTALL,
        ), "既受講コースを除外する learning_states の NOT EXISTS 句が見当たらない"

    def test_deduplicates_by_course_id(self):
        """visibility='public' のテンプレートが object_group_permissions
        （object_type='course'）に登録されていると、public_records と group_records の
        両方にヒットし同じコースが重複して返るため、ID で重複排除すること。"""
        body = self._list_courses_body()
        assert "seen_ids" in body, (
            "list_courses に course_id による重複排除処理が無い"
        )

    def test_group_branch_marks_is_enrollable(self):
        """group_records から来たコースは is_enrollable=True で返すこと。"""
        body = self._list_courses_body()
        # group_records ブロックで is_enrollable=True が立っていること
        m = re.search(
            r"for r in group_records",
            body,
        )
        assert m, "group_records をループする comprehension が見つからない"
        # group_records を生成する comprehension の前段（ブロック内）に
        # is_enrollable=True が含まれている
        tail = body[max(0, m.start() - 500) : m.start()]
        assert "is_enrollable=True" in tail, (
            "group_records から生成される LearningCourseOut の "
            "is_enrollable が True になっていない"
        )
