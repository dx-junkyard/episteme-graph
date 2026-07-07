"""機能2: コース作成UX — 教材選択の情報提示・検索/絞り込み/並び替え・プレビュー。

対象:
  - `backend/api/schemas.py` の `MaterialOut`（サマリ用フィールド追加）
  - `backend/api/routes/admin.py` の `list_materials`（?include=summary 集約）
  - `frontend/public/admin.html` の教材選択ツールバー
  - `frontend/public/js/admin.js` の教材カード描画・検索/絞り込み/並び替え
  - `frontend/public/css/styles.css` の教材カード関連スタイル

すべて静的解析で検証し、外部 API / DB は呼び出さない。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = PROJECT_ROOT / "backend"
FRONTEND_DIR = PROJECT_ROOT / "frontend" / "public"

ADMIN_PY = BACKEND_DIR / "api" / "routes" / "admin.py"
SCHEMAS_PY = BACKEND_DIR / "api" / "schemas.py"
ADMIN_HTML = FRONTEND_DIR / "admin.html"
ADMIN_JS = FRONTEND_DIR / "js" / "admin.js"
STYLES_CSS = FRONTEND_DIR / "css" / "styles.css"


# ---------------------------------------------------------------------------
# schemas.py — MaterialOut のサマリ用フィールド
# ---------------------------------------------------------------------------


class TestMaterialOutSchema:
    @pytest.fixture(autouse=True)
    def _load(self):
        self.src = SCHEMAS_PY.read_text(encoding="utf-8")

    def _material_out_block(self) -> str:
        m = re.search(r"class MaterialOut\(BaseModel\):(.+?)\nclass ", self.src, re.DOTALL)
        assert m, "MaterialOut クラス本体が抽出できない"
        return m.group(1)

    @pytest.mark.parametrize(
        "field",
        [
            "authors",
            "year",
            "doc_type",
            "analyzed_at",
            "component_count",
            "top_components",
            "top_concepts",
            "section_outline",
            "has_course",
        ],
    )
    def test_material_out_has_summary_field(self, field):
        block = self._material_out_block()
        assert re.search(rf"\n\s*{field}\s*:", block), f"MaterialOut に {field} が無い"

    def test_new_list_fields_have_defaults(self):
        """list 型の新フィールドは default_factory を持ち、必須化して既存呼び出しを壊さないこと。"""
        block = self._material_out_block()
        for field in ("authors", "top_components", "top_concepts", "section_outline"):
            assert re.search(rf"{field}\s*:\s*list\[str\]\s*=\s*Field\(default_factory=list\)", block), (
                f"{field} が default_factory=list になっていない"
            )


# ---------------------------------------------------------------------------
# admin.py — list_materials の ?include=summary 集約
# ---------------------------------------------------------------------------


class TestListMaterialsSummary:
    @pytest.fixture(autouse=True)
    def _load(self):
        self.src = ADMIN_PY.read_text(encoding="utf-8")

    def _body(self) -> str:
        m = re.search(r"def list_materials\b.*?return materials", self.src, re.DOTALL)
        assert m, "list_materials 関数本体が抽出できない"
        return m.group(0)

    def test_accepts_include_param(self):
        m = re.search(r"def list_materials\(\s*(.*?)\)\s*->", self.src, re.DOTALL)
        assert m and "include" in m.group(1), "list_materials が include パラメータを受け取らない"

    def test_want_summary_gate(self):
        body = self._body()
        assert re.search(r"want_summary\s*=\s*bool\(", body), "want_summary ゲートが無い"
        assert '"summary"' in body

    def test_selects_metadata_columns(self):
        """authors / year / doc_type を documents から取得している。"""
        body = self._body()
        assert "d.authors" in body
        assert "d.year" in body
        assert "d.doc_type" in body

    def test_runs_query_selects_completed_at(self):
        """解析完了時刻（直近の生成の並び替え用）を取得している。"""
        body = self._body()
        assert "completed_at" in body

    def test_summary_queries_theory_components(self):
        """サマリ集約で theory_components から主要コンポーネント名を取得する。"""
        body = self._body()
        assert "theory_components" in body
        assert "components_by_mid" in body

    def test_summary_queries_courses_for_has_course(self):
        """コース未作成判定のため learning_courses を参照する。"""
        body = self._body()
        assert "learning_courses" in body
        assert "materials_with_course" in body
        # 自分のコースに限定していること
        assert "lc.user_id = CAST(:user_id AS uuid)" in body

    def test_material_out_populates_summary_fields(self):
        body = self._body()
        for field in ("has_course=", "top_components=", "section_outline=", "analyzed_at=", "component_count="):
            assert field in body, f"MaterialOut 構築に {field} が無い"

    def test_summary_only_reads_existing_tables(self):
        """A層非改変: サマリ集約は読み取り専用（INSERT/UPDATE/DELETE を含まない）。"""
        body = self._body()
        assert not re.search(r"\b(INSERT|UPDATE|DELETE)\b", body), "list_materials が書き込みを含む"


# ---------------------------------------------------------------------------
# admin.html — 教材選択ツールバー
# ---------------------------------------------------------------------------


class TestAdminHtmlToolbar:
    @pytest.fixture(autouse=True)
    def _load(self):
        self.src = ADMIN_HTML.read_text(encoding="utf-8")

    def test_has_search_input(self):
        assert 'id="cb-material-search"' in self.src

    def test_has_sort_select(self):
        assert 'id="cb-material-sort"' in self.src
        assert 'value="analyzed"' in self.src  # 直近の生成順

    def test_has_filter_chips(self):
        assert 'id="cb-material-filters"' in self.src
        for f in ("all", "no_course", "recent", "completed"):
            assert f'data-filter="{f}"' in self.src, f"フィルタチップ {f} が無い"

    def test_has_count_element(self):
        assert 'id="cb-material-count"' in self.src


# ---------------------------------------------------------------------------
# admin.js — 教材カード描画・検索/絞り込み/並び替え
# ---------------------------------------------------------------------------


class TestAdminJsMaterialCards:
    @pytest.fixture(autouse=True)
    def _load(self):
        self.src = ADMIN_JS.read_text(encoding="utf-8")

    def test_fetches_with_include_summary(self):
        assert '/admin/materials?include=summary' in self.src

    def test_state_has_filter_sort_search(self):
        for key in ("materialFilter", "materialSort", "materialSearch"):
            assert key in self.src, f"state に {key} が無い"

    @pytest.mark.parametrize(
        "fn",
        [
            "function renderMaterialCards",
            "function cbFilteredMaterials",
            "function initMaterialToolbar",
            "function toggleMaterialSelection",
            "function toggleMaterialDetail",
            "function cbStatusBadge",
        ],
    )
    def test_defines_functions(self, fn):
        assert fn in self.src, f"{fn} が定義されていない"

    def test_toolbar_initialized(self):
        assert "initMaterialToolbar()" in self.src

    def test_no_longer_filters_to_completed_only(self):
        """全ステータスをカードで表示するため、completed だけに絞り込む旧ロジックが無いこと。"""
        m = re.search(r"function loadMaterialsForSelection\(\).*?\n  \}", self.src, re.DOTALL)
        assert m, "loadMaterialsForSelection が抽出できない"
        body = m.group(0)
        assert 'm.status === "completed"' not in body, "旧 completed フィルタが残っている"

    def test_selection_gated_to_completed(self):
        """選択可否は解析完了に限定する。"""
        assert 'm.status === "completed"' in self.src  # cbMaterialSelectable 内

    def test_no_course_and_recent_filters(self):
        assert '"no_course"' in self.src
        assert '"recent"' in self.src
        assert "has_course" in self.src
        assert "analyzed_at" in self.src


# ---------------------------------------------------------------------------
# styles.css — 教材カード関連スタイル
# ---------------------------------------------------------------------------


class TestStylesCss:
    @pytest.fixture(autouse=True)
    def _load(self):
        self.src = STYLES_CSS.read_text(encoding="utf-8")

    @pytest.mark.parametrize(
        "selector",
        [".cb-mat-card", ".cb-mat-chip", ".cb-material-toolbar", ".cb-mat-badge", ".cb-mat-detail"],
    )
    def test_defines_selector(self, selector):
        assert selector in self.src, f"{selector} のスタイルが無い"

    def test_status_badge_variants(self):
        for cls in (".cb-mat-badge.st-ok", ".cb-mat-badge.st-proc", ".cb-mat-badge.st-fail", ".cb-mat-badge.nocourse"):
            assert cls in self.src, f"{cls} が無い"
