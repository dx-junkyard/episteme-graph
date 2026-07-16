"""R層 UI 配線の静的ガードレール（vision_ux_gap_survey_2026-07.md G4-R）。

問題: 教員向け review キュー（`GET /api/admin/reconstruction/items/review-queue`、
疑わしさランク順）は API 実装済みだが、原稿スタジオの到達経路が
「コース選択→トピック選択→右ペイン切替→claim カード展開」の5段階の奥にあり、
到達前に件数バッジも無かった（admin-lecture-studio.js:1605-1770 付近）。

本ファイルは、コース/コンポーネントビュー付近に追加した直行ボタン
（`lsReconReviewBadgeHtml`）とフラットな一覧モーダル（`lsOpenReconReviewModal`）の
配線を frontend/public/js/admin-lecture-studio.js のソース静的検証で固定する
（test_deliberation_ui_static.py と同型のアプローチ）。既存の5段階の経路
（`lsToggleReviewQueueForClaim` / `lsPatchReconItem`）は変更していないことも検証する。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LECTURE_STUDIO_JS = ROOT / "frontend" / "public" / "js" / "admin-lecture-studio.js"


def _read() -> str:
    return LECTURE_STUDIO_JS.read_text(encoding="utf-8")


def _function_block(src: str, name: str) -> str:
    m = re.search(r"function " + name + r"\([\s\S]+?\n  \}\n", src)
    assert m, f"function {name} が見つかりません"
    return m.group(0)


class TestReconReviewBadgeWiring:
    """コース/コンポーネントビュー付近の直行ボタン。"""

    def test_file_exists(self):
        assert LECTURE_STUDIO_JS.exists()

    def test_badge_html_function_present(self):
        src = _read()
        assert "function lsReconReviewBadgeHtml" in src

    def test_badge_wired_into_course_and_components_headers(self):
        """コースビュー（lsRenderCourseStructure）とコンポーネントビュー
        （lsRenderComponentsTab）の両方のヘッダにバッジボタンを差し込む。"""
        src = _read()
        course_block = _function_block(src, "lsRenderCourseStructure")
        components_block = _function_block(src, "lsRenderComponentsTab")
        assert "lsReconReviewBadgeHtml()" in course_block
        assert "lsReconReviewBadgeHtml()" in components_block

    def test_badge_click_opens_flat_review_queue_modal(self):
        src = _read()
        block = _function_block(src, "lsBindReconReviewButtons")
        assert "data-ls-recon-review" in block
        assert "lsOpenReconReviewModal()" in block

    def test_badge_count_derived_from_review_queue_endpoint(self):
        """既存 API（review-queue）を再利用し、専用の集計エンドポイントを新設していない。"""
        src = _read()
        block = _function_block(src, "lsRefreshReconReviewBadge")
        assert "/admin/reconstruction/items/review-queue" in block

    def test_badge_count_excludes_low_signal_items(self):
        """情報不足（k未満）の item は教員の作業件数に数えない。"""
        src = _read()
        block = _function_block(src, "lsRefreshReconReviewBadge")
        assert '"情報不足"' in block
        assert 'it.status === "auto"' in block

    def test_badge_reset_on_course_change(self):
        """コース切替・未選択でバッジの件数をクリアする（前コースの件数が漏れ出さない）。"""
        src = _read()
        assert src.count("lsState.reconReviewCount = null;") >= 2


class TestReconReviewModalWiring:
    """フラットな一覧モーダル。既存 confirm/retire PATCH API・既存関数を再利用する。"""

    def test_modal_open_function_present(self):
        src = _read()
        assert "function lsOpenReconReviewModal" in src

    def test_modal_reuses_settings_modal_shell(self):
        """新規モーダル専用の DOM/CSS を増やしすぎず、既存の .ls-settings-modal を再利用する。"""
        src = _read()
        block = _function_block(src, "lsOpenReconReviewModal")
        assert "ls-settings-modal" in block
        assert "ls-settings-dialog" in block

    def test_modal_list_loads_review_queue(self):
        src = _read()
        block = _function_block(src, "lsLoadReconReviewModalList")
        assert "/admin/reconstruction/items/review-queue" in block

    def test_modal_actions_reuse_existing_patch_status_helper(self):
        """既存 PATCH API・既存関数を再利用する（confirm/retire ロジックを複製しない）。"""
        src = _read()
        modal_patch_block = _function_block(src, "lsPatchReconItemInModal")
        assert "lsPatchReconItemStatus(" in modal_patch_block

        inline_patch_block = _function_block(src, "lsPatchReconItem")
        assert "lsPatchReconItemStatus(" in inline_patch_block

        shared_block = _function_block(src, "lsPatchReconItemStatus")
        assert "/admin/reconstruction/items/" in shared_block
        assert 'method: "PATCH"' in shared_block

    def test_modal_offers_confirm_and_retire_only(self):
        """confirm/retired のみ（削除 API は無い・retire のみが P4 の一貫した挙動）。"""
        src = _read()
        block = _function_block(src, "lsRenderReconReviewModalList")
        assert 'data-recon-item-status="retired"' in block
        assert 'data-recon-item-status="confirmed"' in block
        assert "DELETE" not in block

    def test_no_new_delete_endpoint_introduced(self):
        """新設した review キュー関連の関数群は DELETE を使わない（既存の他機能
        の DELETE 利用――例: C層 endorsement 取り消し――とは無関係。ファイル全体を
        見ると無関係な DELETE 呼び出しが既にあるため、新設関数の範囲だけを検査する）。"""
        src = _read()
        for name in (
            "lsReconReviewBadgeHtml", "lsUpdateReconReviewBadgeText", "lsBindReconReviewButtons",
            "lsRefreshReconReviewBadge", "lsOpenReconReviewModal", "lsLoadReconReviewModalList",
            "lsRenderReconReviewModalList", "lsPatchReconItemInModal", "lsPatchReconItemStatus",
        ):
            block = _function_block(src, name)
            assert '"DELETE"' not in block and "'DELETE'" not in block, name

    def test_existing_five_step_path_untouched(self):
        """既存の5段階経路（claim別インライン確認パネル）は残す。"""
        src = _read()
        assert "function lsToggleReviewQueueForClaim" in src
        assert "function lsRenderStumbleSummary" in src


class TestNoForbiddenVocabularyOrPolling:
    """R層不変条項: スコア/順位の煽り語彙を出さない・ポーリングしない（プル型）。"""

    BANNED = ("踏破", "達成率", "ランキング", "ノーベル賞", "疑え")

    def test_no_banned_words(self):
        src = _read()
        for word in self.BANNED:
            assert word not in src

    def test_badge_refresh_is_event_driven_not_polled(self):
        """再取得はコース選択・item 変更後のみ（setInterval 等の定期実行をしない）。"""
        src = _read()
        block = _function_block(src, "lsRefreshReconReviewBadge")
        assert "setInterval" not in block
