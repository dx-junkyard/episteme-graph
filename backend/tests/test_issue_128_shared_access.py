"""Issue #128: 招待されたグループメンバーが共有コース・教材を閲覧できない不具合の修正。

対象:
  - `backend/api/routes/learning.py` の `list_courses` (group_records の is_template/is_published を保持)
  - `backend/api/routes/admin.py` の `list_materials`/`get_material`
    (editor/viewer 権限グループ経由の教材アクセス許可)
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = PROJECT_ROOT / "backend"
LEARNING_PY = BACKEND_DIR / "api" / "routes" / "learning.py"
ADMIN_PY = BACKEND_DIR / "api" / "routes" / "admin.py"


# ---------------------------------------------------------------------------
# Fix 1: learning.py list_courses — group_records のフラグ
# ---------------------------------------------------------------------------


class TestLearningListCoursesGroupRecords:
    @pytest.fixture(autouse=True)
    def _load(self):
        self.src = LEARNING_PY.read_text(encoding="utf-8")

    def _group_records_block(self) -> str:
        """`group_records = session.execute(...)` ブロックを抽出する。"""
        m = re.search(
            r"group_records\s*=\s*session\.execute\(\s*sa_text\(f?\"\"\"(.+?)\"\"\"\)",
            self.src,
            re.DOTALL,
        )
        assert m, "group_records のクエリブロックが見つからない"
        return m.group(1)

    def test_group_records_selects_is_template(self):
        """group_records クエリに is_template が含まれていること。"""
        block = self._group_records_block()
        assert re.search(r"COALESCE\(\s*lc\.is_template\s*,\s*false\s*\)", block, re.IGNORECASE)

    def test_group_records_selects_is_published(self):
        """group_records クエリに is_published が含まれていること。"""
        block = self._group_records_block()
        assert re.search(r"COALESCE\(\s*lc\.is_published\s*,\s*false\s*\)", block, re.IGNORECASE)

    def test_group_records_mapping_preserves_flags(self):
        """group_records を LearningCourseOut にマップする処理で、
        is_template/is_published が取得値（bool(r[...])）になっていること。
        （ハードコードの False が残っていないこと）
        """
        # "is_template=False" のハードコードが残っていないこと（group_records マップ部分）
        # LearningCourseOut(... is_template=False, is_published=False, is_enrollable=True ...) の形を検出
        bad_pattern = re.compile(
            r"LearningCourseOut\([^)]*is_template\s*=\s*False[^)]*is_published\s*=\s*False[^)]*is_enrollable\s*=\s*True",
            re.DOTALL,
        )
        assert not bad_pattern.search(self.src), (
            "group_records の map で is_template/is_published がハードコードのまま残っている"
        )

    def test_group_records_mapping_uses_bool_casts(self):
        """group_records をマップする処理で bool(r[2]) / bool(r[3]) が使われ、
        visibility デフォルトが 'group' であること（public 用と区別）。
        """
        # "for r in group_records" のコンテキストの直前にある LearningCourseOut(...) を抽出
        m = re.search(
            r"(LearningCourseOut\([^()]*(?:\([^()]*\)[^()]*)*\))\s*for\s+r\s+in\s+group_records",
            self.src,
            re.DOTALL,
        )
        assert m, "group_records の map 部が見つからない"
        block = m.group(1)
        assert "bool(r[2])" in block, "is_template が bool(r[2]) になっていない"
        assert "bool(r[3])" in block, "is_published が bool(r[3]) になっていない"
        assert '"group"' in block, "group_records 用の visibility デフォルトが 'group' でない"


# ---------------------------------------------------------------------------
# Fix 2: admin.py list_materials / get_material — 共有グループ経由アクセス
# ---------------------------------------------------------------------------


class TestAdminMaterialsSharedAccess:
    @pytest.fixture(autouse=True)
    def _load(self):
        self.src = ADMIN_PY.read_text(encoding="utf-8")

    def test_imports_get_user_group_ids(self):
        assert "get_user_group_ids" in self.src

    def test_list_materials_includes_public_visibility(self):
        """list_materials のクエリが visibility='public' 許可条件を含むこと。"""
        m = re.search(
            r"def list_materials\b.*?return materials",
            self.src,
            re.DOTALL,
        )
        assert m, "list_materials 関数本体が抽出できない"
        body = m.group(0)
        assert "visibility = 'public'" in body

    def test_list_materials_includes_group_visibility(self):
        """list_materials のクエリが visibility='group' と user_groups 参照を含む。"""
        m = re.search(
            r"def list_materials\b.*?return materials",
            self.src,
            re.DOTALL,
        )
        body = m.group(0)
        assert "visibility = 'group'" in body
        assert "user_groups" in body or "get_user_group_ids" in body

    def test_list_materials_joins_course_permissions(self):
        """editor/viewer 権限で紐づくコースの sources.material_id を許可条件に含む。"""
        m = re.search(
            r"def list_materials\b.*?return materials",
            self.src,
            re.DOTALL,
        )
        body = m.group(0)
        assert "object_group_permissions" in body
        assert "object_type = 'course'" in body
        assert "jsonb_array_elements" in body
        assert "material_id" in body

    def test_list_materials_removes_hardcoded_uploaded_by_only(self):
        """uploaded_by 単独の WHERE 句が残っていないこと（OR 条件になっている）。"""
        m = re.search(
            r"def list_materials\b.*?return materials",
            self.src,
            re.DOTALL,
        )
        body = m.group(0)
        # "WHERE uploaded_by = CAST(:user_id AS uuid) AND filename IS NOT NULL" のような
        # 旧パターンが残っていないこと
        bad = re.search(
            r"WHERE\s+uploaded_by\s*=\s*CAST\(:user_id AS uuid\)\s+AND\s+filename IS NOT NULL\s*\n\s*ORDER BY",
            body,
        )
        assert not bad, "list_materials が uploaded_by 単独の WHERE のまま"

    def test_get_material_includes_shared_access(self):
        """get_material にも共有グループ経由のアクセス許可が入っていること。"""
        m = re.search(
            r"def get_material\b.*?return MaterialOut",
            self.src,
            re.DOTALL,
        )
        assert m, "get_material 関数本体が抽出できない"
        body = m.group(0)
        assert "visibility = 'public'" in body
        assert "object_group_permissions" in body

    def test_get_material_removes_hardcoded_uploaded_by_only(self):
        """get_material の WHERE に uploaded_by 単独が残っていないこと。"""
        m = re.search(
            r"def get_material\b.*?return MaterialOut",
            self.src,
            re.DOTALL,
        )
        body = m.group(0)
        bad = re.search(
            r"WHERE\s+uploaded_by\s*=\s*CAST\(:user_id AS uuid\)\s+AND\s+source_path\s*=",
            body,
        )
        assert not bad, "get_material が uploaded_by 単独の WHERE のまま"
