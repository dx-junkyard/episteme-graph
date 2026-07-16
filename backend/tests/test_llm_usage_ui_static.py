"""ビジョン×UXギャップ調査 G2-U（U層 LLM使用量のフロント未接続）の静的ガードレール。

対象:
  - SYSTEM_ADMIN 向け「LLM使用量」タブが admin.html / admin.js / admin-llm-usage.js に
    実在し、GET /api/admin/llm-usage/metrics を reported/estimated 分離で表示すること（U1）。
  - dropped_events を隠さない（0件でも表示する。U8）。
  - 価格表未設定時に費用を捏造しない（cost_usd が null のとき "-" 表示。U7）。
  - TEACHER 向け教材見積りポップオーバーが GET /api/admin/llm-usage/estimate/documents/{id}
    を呼び、レンジのみ・金額なしで表示すること（U5）。
  - Admin Copilot 用の UI アンカー（llm_usage_metrics / material_estimate_button）が
    registerAssistantHooks に登録されていること。

すべて静的解析（正規表現・部分文字列検索）。外部 API / 実 DOM は使わない。
"""

from __future__ import annotations

import re
from pathlib import Path

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend" / "public"
ADMIN_JS = FRONTEND_DIR / "js" / "admin.js"
ADMIN_HTML = FRONTEND_DIR / "admin.html"
LLM_USAGE_JS = FRONTEND_DIR / "js" / "admin-llm-usage.js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _extract_function(src: str, name: str) -> str:
    marker = "function " + name
    assert marker in src, f"{name} が見つかりません"
    after = src.split(marker, 1)[1]
    # 次の "function " 定義（同じインデント階層）の手前までを本体とみなす。
    return after.split("\n  function ")[0]


# ---------------------------------------------------------------------------
# admin.html: タブ・パネル・script タグの実在
# ---------------------------------------------------------------------------


class TestLlmUsageHtml:
    def setup_method(self):
        self.html = _read(ADMIN_HTML)

    def test_tab_button_exists_hidden_by_default(self):
        assert 'data-tab="llm-usage"' in self.html
        assert 'id="tab-btn-llm-usage"' in self.html
        m = re.search(r'<button[^>]*id="tab-btn-llm-usage"[^>]*>', self.html)
        assert m, "tab-btn-llm-usage ボタンが見つかりません"
        assert 'style="display:none"' in m.group(0)

    def test_panel_container_exists(self):
        assert 'id="tab-llm-usage"' in self.html

    def test_script_tag_registered_before_admin_js(self):
        assert "/js/admin-llm-usage.js" in self.html
        # admin.js より前に読み込まれること（admin.js の initApp から DI 注入で呼ばれるため）。
        idx_llm = self.html.index("/js/admin-llm-usage.js")
        idx_admin = self.html.index("/js/admin.js")
        assert idx_llm < idx_admin


# ---------------------------------------------------------------------------
# admin.js: 役割別可視性・材料行の見積りボタン・DI 注入
# ---------------------------------------------------------------------------


class TestLlmUsageAdminJsWiring:
    def setup_method(self):
        self.js = _read(ADMIN_JS)

    def test_tab_shown_for_system_admin_only(self):
        body = _extract_function(self.js, "setupRoleBasedUI")
        assert "tab-btn-llm-usage" in body

    def test_material_row_has_estimate_button(self):
        assert 'class="admin-estimate-btn"' in self.js
        assert "解析コスト見積り" in self.js

    def test_estimate_button_no_cost_wording_near_definition(self):
        """見積りボタンの title は金額ではなくトークン量の目安であることを明記する。"""
        m = re.search(r'var estimateBtn = [^;]*;', self.js, re.S)
        assert m, "estimateBtn の定義が見つかりません"
        assert "金額は表示されません" in m.group(0)

    def test_estimate_button_click_delegates_to_module(self):
        assert "window.AdminLlmUsage.openEstimatePopover" in self.js

    def test_init_called_with_di(self):
        m = re.search(r"window\.AdminLlmUsage\.init\(\{(.*?)\}\)", self.js, re.S)
        assert m, "AdminLlmUsage.init 呼び出しが見つかりません"
        body = m.group(1)
        assert "apiFetch" in body
        assert "escHtml" in body
        assert "onTabActivate" in body
        assert "state" in body


# ---------------------------------------------------------------------------
# admin.js: Admin Copilot の UI アンカー登録
# ---------------------------------------------------------------------------


class TestLlmUsageAssistantAnchors:
    def setup_method(self):
        self.js = _read(ADMIN_JS)
        self.hooks_body = self.js.split("function registerAssistantHooks")[1]

    def test_llm_usage_metrics_anchor_registered(self):
        assert "llm_usage_metrics:" in self.hooks_body
        assert 'AA.registerUiAnchors("llm-usage"' in self.hooks_body

    def test_material_estimate_button_anchor_registered(self):
        assert "material_estimate_button:" in self.hooks_body
        m = re.search(r"material_estimate_button:\s*function\s*\(id\)\s*\{(.*?)\n\s*\}", self.hooks_body, re.S)
        assert m, "material_estimate_button アンカーが見つかりません"
        assert "admin-estimate-btn" in m.group(1)


# ---------------------------------------------------------------------------
# admin-llm-usage.js: reported/estimated 分離・dropped_events・価格捏造禁止・レンジのみ
# ---------------------------------------------------------------------------


class TestLlmUsageModule:
    def setup_method(self):
        assert LLM_USAGE_JS.exists(), "admin-llm-usage.js が存在しません"
        self.js = _read(LLM_USAGE_JS)

    def test_exposes_window_api(self):
        assert "window.AdminLlmUsage = {" in self.js
        assert "init: init" in self.js
        assert "openEstimatePopover: openEstimatePopover" in self.js

    def test_calls_metrics_endpoint(self):
        assert '"/admin/llm-usage/metrics?group_by="' in self.js

    def test_calls_estimate_endpoint(self):
        assert '"/admin/llm-usage/estimate/documents/"' in self.js

    def test_metrics_render_separates_reported_and_estimated(self):
        """U1: reported/estimated を分離表示すること（合算した単一数値を作らない）。"""
        body = _extract_function(self.js, "renderMetrics")
        assert "実測 (reported)" in body
        assert "推計 (estimated)" in body
        assert "row.reported" in body
        assert "row.estimated" in body

    def test_dropped_events_always_rendered(self):
        """U8: dropped_events は 0 件でも隠さず表示する。"""
        body = _extract_function(self.js, "renderMetrics")
        assert "dropped_events" in body
        # 0件のときに non-rendering へ分岐する if がないこと（常に textContent をセットする）。
        assert "if (dropped > 0)" not in body or "droppedNote.textContent" in body

    def test_cost_not_fabricated_when_null(self):
        """U7: price_table 未設定などで cost_usd が null のときに金額を捏造しない。"""
        body = _extract_function(self.js, "renderMetrics")
        assert 'cost === null' in body
        assert '"-"' in body

    def test_price_table_state_shown_honestly(self):
        body = _extract_function(self.js, "renderMetrics")
        assert "price_table_loaded" in body
        assert "未設定" in body

    def test_estimate_popover_has_no_cost_wording(self):
        """U5: 事前見積りはレンジのみ・金額は一切表示しない。"""
        body = _extract_function(self.js, "renderEstimatePopover")
        assert "$" not in body
        assert "cost_usd" not in body
        assert "円" not in body
        assert "金額は表示しません" in body

    def test_estimate_popover_uses_tokens_range_only(self):
        body = _extract_function(self.js, "renderEstimatePopover")
        assert "tokens_range" in body
        assert "total_tokens_range" in body
