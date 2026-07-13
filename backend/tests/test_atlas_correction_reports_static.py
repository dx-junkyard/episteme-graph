"""分野の地図 — 修正報告フロー (Issue D) のフロントエンド/SQL 静的検証。

既存テスト (test_atlas_detail_panel_transitions.py) と同様、
ソースの静的検証で UI 実装の受け入れ条件を確認する。

受け入れ条件との対応:
- D-1: インラインフォーム (トグル・placeholder・帰属明示・空文字ガード・トースト)
- 匿名での送信経路が存在しない (フォームに匿名オプションを置かない)
- 送信後はパネルへ復帰しオーバーレイは閉じない
- D-2: レビュー画面に本文・対象・版・報告者・旧版識別・件数・処理アクション
- D-3: changelog credits の表示、報告者への結果通知
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

REPORT_JS = ROOT / "frontend" / "public" / "js" / "atlas-report.js"
PANEL_JS = ROOT / "frontend" / "public" / "js" / "atlas-panel.js"
ADMIN_JS = ROOT / "frontend" / "public" / "js" / "admin.js"
ADMIN_HTML = ROOT / "frontend" / "public" / "admin.html"
INDEX_HTML = ROOT / "frontend" / "public" / "index.html"
FIXTURE_JS = ROOT / "frontend" / "public" / "js" / "atlas-fixture.js"
MIGRATION_SQL = ROOT / "backend" / "db" / "023_atlas_correction_reports.sql"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# D-1: 報告フォームと送信 (atlas-report.js)
# ---------------------------------------------------------------------------


class TestReportForm:
    def test_form_is_wired_to_reportrequest_event(self):
        src = _read(REPORT_JS)
        assert 'addEventListener("atlas:reportrequest"' in src

    def test_form_has_specified_placeholder(self):
        assert "この配置・状態のどこが実際と違うか" in _read(REPORT_JS)

    def test_submit_button_states_attribution(self):
        """送信ボタンと近傍に帰属つきであることを明示する。"""
        src = _read(REPORT_JS)
        assert "帰属つきで送信 ↗" in src
        assert "匿名にはできません" in src

    def test_no_anonymous_option(self):
        """匿名オプションを置かない (受け入れ条件2 の UI 側)。"""
        src = _read(REPORT_JS)
        assert "匿名で送信" not in src
        assert "anonymous" not in src.lower()

    def test_empty_text_guard(self):
        """空文字送信のガード (「(内容未記入)」は本実装では送信不可)。"""
        src = _read(REPORT_JS)
        assert "内容が未記入です" in src
        assert "textarea.value.trim()" in src

    def test_success_shows_toast_and_stays_in_overlay(self):
        """送信後トースト + フォームを閉じてパネルへ復帰 (オーバーレイは閉じない)。"""
        src = _read(REPORT_JS)
        assert "修正報告を記録しました" in src
        # ↗ アクションと違い、報告はオーバーレイを閉じない
        assert "AtlasOverlay.close" not in src

    def test_auto_attached_meta_uses_selection_event(self):
        """対象 / level / skeleton_version を選択イベント (issue B) から自動添付。"""
        src = _read(REPORT_JS)
        for field in ("node_id", "region_id", "level", "skeleton_version"):
            assert field in src
        assert "detail.skeleton_version" in src

    def test_report_endpoint_is_called(self):
        src = _read(REPORT_JS)
        assert '"/atlas/report"' in src
        assert 'fetch("/api"' in src

    def test_panel_dispatches_reportrequest(self):
        """issue C の接続点イベントが維持されている。"""
        assert "atlas:reportrequest" in _read(PANEL_JS)

    def test_script_is_included_after_panel(self):
        html = _read(INDEX_HTML)
        assert "/js/atlas-report.js" in html
        assert html.index("/js/atlas-panel.js") < html.index("/js/atlas-report.js")

    def test_fixture_carries_cartridge_id(self):
        """報告の cartridge_id 自動添付のため、地図データは cartridge を持つ。"""
        assert '"cartridge"' in _read(FIXTURE_JS)


# ---------------------------------------------------------------------------
# D-2: レビューキュー (admin.js / admin.html)
# ---------------------------------------------------------------------------


class TestReviewQueueUI:
    def test_admin_has_reports_section(self):
        html = _read(ADMIN_HTML)
        assert 'id="atlas-reports-section"' in html
        assert 'id="atlas-reports-list"' in html
        assert 'id="atlas-report-status-filter"' in html

    def test_review_display_shows_required_fields(self):
        """報告本文 + 対象ノード/領域 + 骨格バージョン + 報告者。"""
        src = _read(ADMIN_JS)
        assert "r.report_text" in src
        assert "r.node_id" in src and "r.region_id" in src
        assert "r.skeleton_version" in src
        assert "r.reporter_name" in src

    def test_version_mismatch_is_visible(self):
        """旧版への報告がレビュー画面で識別できる (受け入れ条件5)。"""
        src = _read(ADMIN_JS)
        assert "version_mismatch" in src
        assert "旧版" in src

    def test_resolve_actions_exist(self):
        """採用 / 見送り (理由つき) / 重複統合。"""
        src = _read(ADMIN_JS)
        for action in ("accept", "decline", "merge"):
            assert f"data-report-action='{action}'" in src
        assert "見送りには理由が必要です" in src

    def test_report_accumulation_and_revision_hint(self):
        """同一ノードへの報告蓄積の件数と改版トリガヒント (issue A の閾値と接続)。"""
        src = _read(ADMIN_JS)
        assert "open_count_for_target" in src
        assert "revision_hint_targets" in src
        assert "revision_trigger_threshold" in src

    def test_changelog_credits_are_displayed(self):
        """採用の帰属が骨格の changelog 表示から確認できる (受け入れ条件3)。"""
        src = _read(ADMIN_JS)
        assert "entry.credits" in src
        assert "修正報告の帰属" in src


# ---------------------------------------------------------------------------
# D-3: 結果通知 (学習者側)
# ---------------------------------------------------------------------------


class TestReporterNotificationUI:
    def test_unacked_results_are_fetched_and_acked(self):
        src = _read(REPORT_JS)
        assert "/atlas/reports/mine?unacked=true" in src
        assert "/ack" in src

    def test_decline_reason_is_shown_to_reporter(self):
        src = _read(REPORT_JS)
        assert "見送りになりました" in src
        assert "resolution_note" in src

    def test_accepted_shows_applied_version(self):
        src = _read(REPORT_JS)
        assert "採用され" in src
        assert "applied_version" in src


# ---------------------------------------------------------------------------
# スキーマ (migration 023) — 匿名不可・版固定・昇格互換
# ---------------------------------------------------------------------------


class TestMigrationSchema:
    def test_reference_sql_defines_expected_columns(self):
        """正本は backend/db/023_atlas_correction_reports.sql（main.py のインライン DDL ではない）。"""
        sql = _read(MIGRATION_SQL)
        for fragment in (
            "CREATE TABLE IF NOT EXISTS atlas_correction_reports",
            "reporter_id      UUID NOT NULL REFERENCES users(id)",
            "skeleton_version TEXT NOT NULL",
            "CHECK (node_id <> '' OR region_id <> '')",
            "kind             TEXT NOT NULL DEFAULT 'map_correction'",
        ):
            assert fragment in sql, f"023 SQL に {fragment!r} がない"

    def test_status_vocabulary_supports_review_flow(self):
        sql = _read(MIGRATION_SQL)
        assert "'pending', 'accepted', 'declined', 'merged'" in sql
