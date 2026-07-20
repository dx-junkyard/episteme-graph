"""ビジョン×UXギャップ調査 G2-B / G2-C の管理画面フロント静的ガードレール。

対象:
  - G2-B: 知識ネットワークビジョン Phase B の教員向け唯一の窓口
    GET /api/admin/courses/{course_id}/bridge-insights が「関心集約」タブに接続され、
    サーバの k-匿名集約（人数レンジのみ）をそのまま事実文で提示すること。
    candidate を数えない・個人特定情報を出さない・ランキング/スコア語彙を作らないこと。
  - G2-C: C層 sharing-dashboard（GET /api/admin/courses/{course_id}/sharing-dashboard）が
    コース管理タブの行に接続され、段階ラベル（endorsement_label）を使い、
    独自の数値スコアを捏造しないこと。

すべて静的解析（正規表現・部分文字列検索）。外部 API / 実 DOM は使わない。
"""

from __future__ import annotations

import re
from pathlib import Path

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend" / "public"
ADMIN_JS = FRONTEND_DIR / "js" / "admin.js"
ADMIN_HTML = FRONTEND_DIR / "admin.html"

# G3/P3/P7 系の禁止語彙（学習者に対してだけでなく、ここでは k-匿名教員向け集約に
# ランキング・煽り・スコア表現を持ち込まないためのガードレール語彙）。
FORBIDDEN_RANKING_WORDS = ["ランキング", "1位", "第1位", "トップ3", "順位"]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _extract_function(src: str, name: str) -> str:
    marker = "function " + name
    assert marker in src, f"{name} が見つかりません"
    after = src.split(marker, 1)[1]
    return after.split("\n  function ")[0]


# ---------------------------------------------------------------------------
# G2-B: 橋の候補（学習者の重ね合わせ）
# ---------------------------------------------------------------------------


class TestBridgeInsightsHtml:
    def setup_method(self):
        self.html = _read(ADMIN_HTML)

    def test_section_has_stable_id(self):
        assert 'id="bridge-insights-section"' in self.html

    def test_section_lives_inside_interest_dashboard_tab(self):
        panel = self.html.split('id="tab-interest-dashboard"')[1].split('<!-- System Stats tab')[0]
        assert 'id="bridge-insights-section"' in panel
        assert 'id="bridge-insights-body"' in panel


class TestBridgeInsightsJs:
    def setup_method(self):
        self.js = _read(ADMIN_JS)
        self.body = _extract_function(self.js, "loadBridgeInsights")

    def test_calls_bridge_insights_endpoint(self):
        assert '"/bridge-insights"' in self.body
        assert "/admin/courses/" in self.body

    def test_empty_state_matches_required_copy(self):
        assert "まだ表示できる集約はありません（3名以上の確定痕跡が必要です）" in self.body

    def test_uses_learner_count_range_not_raw_count(self):
        """人数はレンジ表示のみ（core/privacy.py 由来の文字列）を使い、生の人数を集計しない。"""
        assert "learner_count_range" in self.body
        # 生カウントを再計算するような算術（bridges.length を人数として使う等）をしていないこと。
        assert "bridges.length +" not in self.body

    def test_no_ranking_or_score_vocabulary(self):
        for word in FORBIDDEN_RANKING_WORDS:
            assert word not in self.body, f"禁止語彙 {word!r} が橋の候補セクションに含まれています"

    def test_does_not_expose_individual_user_ids(self):
        assert "user_id" not in self.body

    def test_triggered_from_interest_dashboard_load_regardless_of_early_return(self):
        """関心集約本体のコース未選択/データなしの早期 return に巻き込まれず、
        常に loadBridgeInsights が呼ばれること（別セクションとして独立に描画するため）。"""
        dash_body = _extract_function(self.js, "loadInterestDashboard")
        assert "loadBridgeInsights(courseId);" in dash_body
        # 早期 return より前（コース未選択 return の前）で呼ばれていること。
        call_idx = dash_body.index("loadBridgeInsights(courseId);")
        early_return_idx = dash_body.index("コースを選択してください")
        assert call_idx < early_return_idx


class TestBridgeInsightsAssistantAnchor:
    def setup_method(self):
        self.js = _read(ADMIN_JS)
        self.hooks_body = self.js.split("function registerAssistantHooks")[1]

    def test_bridge_insights_section_anchor_registered(self):
        assert "bridge_insights_section:" in self.hooks_body
        m = re.search(r'AA\.registerUiAnchors\("interest-dashboard",\s*\{(.*?)\}\);', self.hooks_body, re.S)
        assert m, "interest-dashboard の registerUiAnchors 呼び出しが見つかりません"
        assert "bridge-insights-section" in m.group(1)


# ---------------------------------------------------------------------------
# G2-C: C層 共有ダッシュボード
# ---------------------------------------------------------------------------


class TestSharingDashboardButton:
    def setup_method(self):
        self.js = _read(ADMIN_JS)

    def test_button_class_present(self):
        assert 'cm-sharing-dashboard-btn' in self.js

    def test_button_rendered_for_owner_and_non_owner_rows(self):
        """自分がアクセスできるコース行（owner/editor/viewer）すべてに導線があること。"""
        body = _extract_function(self.js, "renderCourseManagementTable")
        assert body.count("sharingDashboardBtn") >= 3  # 変数定義 + owner branch + non-owner branch

    def test_click_handler_wired(self):
        assert 'querySelectorAll(".cm-sharing-dashboard-btn")' in self.js
        assert "openSharingDashboardModal(" in self.js


class TestSharingDashboardModal:
    def setup_method(self):
        self.js = _read(ADMIN_JS)
        self.open_body = _extract_function(self.js, "openSharingDashboardModal")
        self.render_body = _extract_function(self.js, "renderSharingDashboardModal")

    def test_calls_sharing_dashboard_endpoint(self):
        assert '"/sharing-dashboard"' in self.open_body
        assert "/admin/courses/" in self.open_body

    def test_uses_server_provided_endorsement_label_not_fabricated_score(self):
        """承認は API が返す段階ラベル（endorsement_label）をそのまま使い、
        独自の平均・重み付けスコアを計算しないこと。"""
        assert "endorsement_label" in self.render_body
        assert "toFixed" not in self.render_body
        assert "* 0." not in self.render_body  # 重み付け計算のような算術がないこと

    def test_totals_come_directly_from_api(self):
        assert "totals.explanation_count" in self.render_body
        assert "totals.shared_count" in self.render_body
        assert "totals.endorsed_count" in self.render_body
        assert "totals.citation_total" in self.render_body

    def test_empty_state_present(self):
        assert "まだ説明バージョンがありません" in self.render_body


class TestSharingDashboardAssistantAnchor:
    def setup_method(self):
        self.js = _read(ADMIN_JS)
        self.hooks_body = self.js.split("function registerAssistantHooks")[1]

    def test_sharing_dashboard_button_anchor_registered(self):
        assert "sharing_dashboard_button:" in self.hooks_body
        m = re.search(r"sharing_dashboard_button:\s*function\s*\(id\)\s*\{(.*?)\n\s*\}", self.hooks_body, re.S)
        assert m, "sharing_dashboard_button アンカーが見つかりません"
        assert "cm-sharing-dashboard-btn" in m.group(1)
