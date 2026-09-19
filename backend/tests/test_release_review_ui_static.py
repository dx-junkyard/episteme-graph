"""リリース前の確認（Release Review）— フロント契約の静的検査。

正本: ``docs/features/release_review_flow_design.md``（不変条項 RR1〜RR7）。
``tests/test_landscape_admin_ui_static.py`` と同じ流儀（ソース文字列の構造検査）。

検証観点:
  1. モジュール形（ES5 IIFE + ``window.AdminReleaseReview`` の init/open/close）
  2. 3ステップ構成と「次へ」= 承認の意味を画面に明示していること（RR2）
  3. API パス（course-scoped GET / accept / visibility）と、ステップ1の既存 UI 委譲
     （atlas-binding の保存ペイロードを再実装しない）
  4. RR4: 各行の [却下] [再検討] が出ていること・個別遷移は既存 PATCH を使うこと
  5. RR6: weight / confidence を読まないこと
  6. RR7: ポーリングしない / 失敗しても公開へ進めること（公開の前提条件を作らない）
  7. 3点セット: data-ui-anchor と admin.js / admin.html の結線
"""

from __future__ import annotations

import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
JS = ROOT / "frontend" / "public" / "js" / "admin-release-review.js"
ADMIN_JS = ROOT / "frontend" / "public" / "js" / "admin.js"
ADMIN_HTML = ROOT / "frontend" / "public" / "admin.html"

SRC = JS.read_text(encoding="utf-8")
ADMIN_SRC = ADMIN_JS.read_text(encoding="utf-8")
HTML_SRC = ADMIN_HTML.read_text(encoding="utf-8")


def _code_only(src: str) -> str:
    """コメント（/** */ ブロックと行頭 //）を落としたソース。

    「読まないこと」を検査する項目は、設計意図を書いた注釈まで禁止語に引っかけない
    ようにコード部分だけを見る。
    """
    without_blocks = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    lines = [
        line for line in without_blocks.splitlines()
        if not line.lstrip().startswith("//")
    ]
    return "\n".join(lines)


CODE = _code_only(SRC)


class TestModuleShape:
    def test_file_exists_and_is_an_iife(self):
        assert JS.exists()
        assert CODE.lstrip().startswith("(function ()")
        assert SRC.rstrip().endswith("})();")
        assert '"use strict"' in SRC

    def test_exposes_init_open_close(self):
        assert "window.AdminReleaseReview = {" in SRC
        for name in ("init:", "open: open", "close: close"):
            assert name in SRC

    def test_is_es5_only(self):
        """admin.js 系と同じ ES5 縛り（const/let/アロー/テンプレートリテラルを使わない）。"""
        assert not re.search(r"\b(const|let)\s+\w+\s*=", SRC)
        assert "=>" not in SRC
        assert "`" not in SRC

    def test_dependencies_are_injected_not_globals(self):
        """DI で受ける（apiFetch / escHtml を直接グローバル参照しない）。"""
        assert "function api(path, options)" in SRC
        assert "deps.apiFetch(path, options)" in SRC
        assert "deps.escHtml" in SRC


class TestSteps:
    def test_three_steps_in_order(self):
        assert re.search(r'key:\s*"map"', SRC)
        assert re.search(r'key:\s*"landscape"', SRC)
        assert re.search(r'key:\s*"publish"', SRC)
        assert SRC.index('key: "map"') < SRC.index('key: "landscape"') < SRC.index('key: "publish"')

    def test_next_labels_are_declared(self):
        assert "この対応で次へ" in SRC
        assert "この配置で次へ" in SRC

    def test_next_meaning_is_stated_on_screen(self):
        """RR2: 「次へ」= 確認したものとして記録することを黙って行わない。"""
        assert "NOTICE_NEXT_MEANING" in SRC
        assert "確認したものとして記録" in SRC

    def test_default_is_that_shown_content_ships(self):
        """RR1: 修正しなければ表示されているものが届く、と画面に書く。"""
        assert "修正しなければ" in SRC

    def test_skip_is_always_available(self):
        """RR1/RR7: 記録せずに進める「あとで」を各ステップに出す。"""
        assert SRC.count('"あとで"') >= 2


class TestApiContract:
    def test_reads_course_scoped_placements(self):
        assert '"/admin/landscape/courses/" +' in SRC
        assert '"/placements"' in SRC

    def test_accept_posts_to_the_course_scoped_endpoint(self):
        assert '"/placements/accept"' in SRC
        accept = SRC[SRC.index("function acceptPlacements") : SRC.index("function renderPublishStep")]
        assert 'method: "POST"' in accept

    def test_publish_uses_the_existing_visibility_endpoint(self):
        publish = SRC[SRC.index("function publish(btn)") :]
        assert '"/admin/courses/" + encodeURIComponent(state.courseId) + "/visibility"' in publish
        assert 'method: "PUT"' in publish
        assert '"public"' in publish

    def test_step1_delegates_to_the_existing_atlas_binding_editor(self):
        """分割・保存ペイロードを再実装しない（設計書 §3.2）。"""
        assert "deps.atlasBindingPropose(" in SRC
        assert "saveLabel:" in SRC
        assert "saveAnchor:" in SRC
        # 自前で atlas-binding を PUT していないこと。
        assert "/atlas-binding" not in SRC

    def test_row_transitions_use_the_existing_patch_endpoint(self):
        assert '"/admin/landscape/placements/" + encodeURIComponent(placementId)' in SRC
        assert 'method: "PATCH"' in SRC

    def test_no_delete_request(self):
        assert 'method: "DELETE"' not in SRC


class TestReviewAffordances:
    def test_row_actions_offer_reject_and_review_required(self):
        """RR4: 一括の「次へ」とは独立に個別の修正手段を出す。"""
        assert 'ROW_ACTIONS' in SRC
        assert '["rejected", "却下"]' in SRC
        assert '["review_required", "再検討"]' in SRC

    def test_rejected_rows_lose_their_action_buttons(self):
        assert 'placement.status !== "rejected"' in SRC

    def test_status_labels_prefer_the_server_label(self):
        assert "placement.status_label" in SRC
        assert "STATUS_FALLBACK_LABELS" in SRC

    def test_row_change_reloads_from_the_server(self):
        """ローカルで状態を書き換えない（サーバ状態を正本にする）。"""
        assert "function reloadLandscape()" in SRC
        change = SRC[SRC.index("function updateRowStatus") : SRC.index("function reloadLandscape")]
        assert "reloadLandscape()" in change

    def test_unplaced_domains_are_shown_as_facts(self):
        assert "の地図に配置できませんでした" in SRC

    def test_evidence_is_available_per_row(self):
        """確定文脈（decision_context_design.md §5）: 何を見て確認したかを再構成できる
        ように、判断の材料（逐語引用）を各行に畳んで置く。引用が無い行もその事実を書く。"""
        assert 'data-ui-anchor="release-review.evidence"' in SRC
        assert "根拠を見る" in SRC
        assert "この配置には論文からの引用が残っていません。" in SRC

    def test_reopen_path_is_stated_next_to_the_meaning_of_next(self):
        """RR2 の解釈補足: 確定の後に再審の経路が見えている（release_review §6.1）。"""
        assert "NOTICE_REOPEN" in SRC
        assert "個別に再検討・却下へ戻せます。" in SRC


class TestNoNumbersAndNoPolling:
    def test_no_raw_weight_or_confidence_is_read(self):
        """RR6 / LS5: 生数値のキーに触らない（CSS の font-weight・注釈は対象外）。"""
        for forbidden in (
            ".weight",
            '"weight"',
            "weight_label",   # 段階ラベルすら並べない（このウィザードでは不要）
            "confidence",
        ):
            assert forbidden not in CODE

    def test_no_polling(self):
        assert "setInterval" not in SRC

    def test_counts_are_facts_not_scores(self):
        """件数（未確認 N件）は出してよいが、スコア・達成率・百分率にしない。"""
        assert "未確認 " in CODE
        for forbidden in ("達成", "スコア", "* 100", "toFixed("):
            assert forbidden not in CODE


class TestFailOpen:
    def test_landscape_load_failure_still_advances(self):
        """RR7: 読めなくても公開まで進める。"""
        step = SRC[SRC.index("function renderLandscapeStep") : SRC.index("function statusLabel")]
        assert ".catch(function (err)" in step
        assert "nextBtn.disabled = false" in step

    def test_accept_failure_states_the_consequence_and_does_not_block(self):
        accept = SRC[SRC.index("function acceptPlacements") : SRC.index("function renderPublishStep")]
        assert "AIによる推定（未確認）" in accept
        # 失敗時にモーダルを閉じたり公開ボタンを消したりしない。
        assert "close()" not in accept

    def test_publish_step_has_no_precondition_gate(self):
        """公開の前提条件を新設しない（既存の公開操作と同じ1操作）。"""
        publish_step = SRC[SRC.index("function renderPublishStep") : SRC.index("function publish(btn)")]
        assert "disabled = true" not in publish_step
        assert "公開する" in publish_step

    def test_closing_the_wizard_is_never_blocked(self):
        assert "if (event.target === overlay) close();" in SRC


class TestAnchorsAndWiring:
    def test_modal_and_button_anchors_are_present(self):
        assert 'overlay.setAttribute("data-ui-anchor", "release-review.modal")' in SRC
        assert '"release-review.next"' in SRC
        assert '"release-review.publish"' in SRC

    def test_admin_js_renders_the_course_row_button(self):
        assert 'class="cm-release-review-btn admin-action-btn"' in ADMIN_SRC
        assert 'data-ui-anchor="course-management.release-review-btn"' in ADMIN_SRC
        assert '.cm-release-review-btn' in ADMIN_SRC
        assert "window.AdminReleaseReview.open(" in ADMIN_SRC

    def test_admin_js_initializes_with_di(self):
        assert "window.AdminReleaseReview.init({" in ADMIN_SRC
        block = ADMIN_SRC[ADMIN_SRC.index("window.AdminReleaseReview.init({") :][:700]
        for key in ("apiFetch:", "escHtml:", "atlasBindingPropose:", "refreshNextSteps:"):
            assert key in block

    def test_course_builder_opens_the_wizard_with_fallback(self):
        """登録直後に自動で開き、未ロード時は従来のインラインパネルへ縮退する（RR7）。"""
        assert "autoOpened: true" in ADMIN_SRC
        idx = ADMIN_SRC.index("autoOpened: true")
        tail = ADMIN_SRC[idx : idx + 900]
        assert "cb-atlas-binding-area" in tail  # else 側の縮退経路

    def test_atlas_binding_editor_supports_the_relabeled_save_button(self):
        assert 'options.saveAnchor || "atlas.binding-save"' in ADMIN_SRC
        assert 'options.saveLabel || "この対応で反映"' in ADMIN_SRC

    def test_script_is_loaded_before_admin_js(self):
        assert "/js/admin-release-review.js" in HTML_SRC
        assert HTML_SRC.index("/js/admin-release-review.js") < HTML_SRC.index("/js/admin.js")