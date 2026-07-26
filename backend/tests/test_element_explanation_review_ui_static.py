"""説明レビューキュー（Element Explanation Review Queue）の静的ガードレール。

背景: 教材管理 → 検出要素（deliberation.js の要素インベントリ、`window.Deliberation.openInventory`）
から要素ごとに「深く検討」を開いて説明候補（element_explanations, migration 056）を
1件ずつ承認する既存 UX は、候補数が多い教材では負担が大きい。本ファイルは document
単位でレビューキューを一覧し、要素ごとにグループ化して一括承認/却下できる UI
（`_openExplanationReviewModal` 他）の受け入れ条件を、test_deliberation_ui_static.py
と同型のソース静的検証で固定する。

candidate-only 原則（承認するまで学習者に表示されない）は維持する。1件ずつの既存 UX
（`_explanationCardHtml` / `_decideExplanation`、overview 内）は変更せず併存させる。

バックエンド API 契約（別エージェントが並行実装）:
  GET  /api/admin/documents/{document_id}/element-explanations?status=candidate
  POST /api/admin/documents/{document_id}/element-explanations/bulk-review
       body: {"action": "approve"|"dismiss", "explanation_ids": [...]}（最大200件）
       → {"updated": [...], "skipped": [{"id","status","reason"}]}
  POST /api/admin/element-explanations/{id}/approve|dismiss（既存・単件）

受け入れ条件:
1. bulk-review への fetch は POST であり、action は "approve"/"dismiss" の2値のみを送る
2. 一括実行前の確認文言に「学習者に表示されます」を含む（承認の破壊的影響を正直に示す）
3. 禁止語彙（踏破・達成率・ランキング・スコア）が本機能の追加分に無い
4. setInterval/setTimeout によるポーリングが無い（取得はモーダルを開いた時のみ）
5. キューの動的描画（カード・グループ見出し・メッセージ）は escHtml を経由する
6. skipped（部分成功）は「スキップ」を含む事実文で正直に表示する
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

DELIBERATION_JS = ROOT / "frontend" / "public" / "js" / "deliberation.js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _function_block(src: str, name: str) -> str:
    """モジュールレベル関数（2スペースインデント）本体をソースから切り出す。

    test_deliberation_ui_static.py の同名ヘルパーと同型。引数リストが空でない
    関数も受けられるよう `[^)]*` で受ける。
    """
    m = re.search(r"function " + name + r"\([^)]*\)\s*\{[\s\S]+?\n  \}\n", src)
    assert m, f"function {name} が見つかりません"
    return m.group(0)


def _review_queue_region(src: str) -> str:
    """本機能で新規追加した範囲（state 宣言 〜 キュー取得関数末尾）を切り出す。

    要素インベントリのモーダル DOM 生成部（既存機能）との境界は
    「// ── 要素インベントリ: モーダル DOM / 描画 ───」コメントとする。
    """
    start = src.index("var explanationReviewState = {")
    end = src.index("// ── 要素インベントリ: モーダル DOM / 描画")
    assert start < end
    return src[start:end]


class TestExplanationReviewQueueContract:
    """①⑥: bulk-review API 呼び出しの契約（POST・action の2値・skipped の正直表示）。"""

    def test_bulk_review_endpoint_uses_shared_base_path_helper(self):
        src = _read(DELIBERATION_JS)
        block = _function_block(src, "_explanationReviewBasePath")
        assert '"/admin/documents/"' in block
        assert '"/element-explanations"' in block

    def test_list_candidates_uses_get_with_status_candidate(self):
        src = _read(DELIBERATION_JS)
        block = _function_block(src, "_loadExplanationReviewQueue")
        assert "_explanationReviewBasePath(documentId)" in block
        assert '"?status=candidate"' in block
        # GET は method 指定なし（apiFetch の既定）。POST を明示していないこと。
        assert 'method: "POST"' not in block

    def test_bulk_review_posts_to_bulk_review_suffix(self):
        src = _read(DELIBERATION_JS)
        block = _function_block(src, "_bulkReviewExplanations")
        assert '_explanationReviewBasePath(documentId) + "/bulk-review"' in block
        assert 'method: "POST"' in block

    def test_bulk_review_action_is_scoped_to_two_values(self):
        """action は "approve"/"dismiss" のリテラル呼び出しのみで、他の値を送らない。"""
        src = _read(DELIBERATION_JS)
        assert '_bulkReviewExplanations("approve")' in src
        assert '_bulkReviewExplanations("dismiss")' in src
        block = _function_block(src, "_bulkReviewExplanations")
        assert "action: action" in block
        assert 'action === "approve"' in block

    def test_bulk_review_body_sends_action_and_explanation_ids(self):
        src = _read(DELIBERATION_JS)
        block = _function_block(src, "_bulkReviewExplanations")
        assert "explanation_ids: ids" in block

    def test_single_card_decide_reuses_existing_element_explanations_endpoint(self):
        """キュー内の単件承認/却下も既存の element-explanations 承認 API を使う
        （C層・E2 の唯一の承認台帳を分裂させない）。"""
        src = _read(DELIBERATION_JS)
        block = _function_block(src, "_decideExplanationReviewCard")
        assert '"/admin/element-explanations/"' in block
        assert 'method: "POST"' in block

    def test_skipped_results_are_shown_as_a_factual_note(self):
        """⑥ 部分成功: skipped は削除・黙殺せず「スキップ」を含む事実文で表示する。"""
        src = _read(DELIBERATION_JS)
        assert "スキップ" in src
        block = _function_block(src, "_bulkReviewExplanations")
        assert "data.skipped" in block
        assert "skipped.length" in block

    def test_updated_rows_are_removed_from_queue_not_the_whole_response(self):
        """updated のみをキューから除去し、skipped は残す（正直な部分適用）。"""
        src = _read(DELIBERATION_JS)
        block = _function_block(src, "_bulkReviewExplanations")
        assert "data.updated" in block
        assert "_removeExplanationsFromReviewQueue(" in block

    def test_client_side_bulk_limit_matches_server_contract(self):
        """②番目の壁: 200件超はクライアント側でも弾く（先頭200件だけ送る、ではなく
        メッセージを出して送信しない）。"""
        src = _read(DELIBERATION_JS)
        assert "EXPLANATION_REVIEW_MAX_BULK" in src
        assert re.search(r"EXPLANATION_REVIEW_MAX_BULK\s*=\s*200", src)
        block = _function_block(src, "_bulkReviewExplanations")
        assert "ids.length > EXPLANATION_REVIEW_MAX_BULK" in block
        assert "一度に承認できるのは200件までです" in block
        # 制限超過時は先頭を切り詰めて送るのではなく、そこで return して送信しない。
        limit_idx = block.index("ids.length > EXPLANATION_REVIEW_MAX_BULK")
        return_idx = block.index("return", limit_idx)
        message_idx = block.index("一度に承認できるのは200件までです")
        assert limit_idx < message_idx < return_idx


class TestExplanationReviewConfirmationCopy:
    """②: 一括実行前の確認文言（学習者への表示影響を事実として明示する）。"""

    def test_approve_confirmation_mentions_learner_visibility(self):
        src = _read(DELIBERATION_JS)
        block = _function_block(src, "_bulkReviewExplanations")
        assert "学習者に表示されます" in block

    def test_dismiss_confirmation_mentions_candidate_is_kept(self):
        """P4: 却下しても候補は削除されず保持されることを明示する。"""
        src = _read(DELIBERATION_JS)
        block = _function_block(src, "_bulkReviewExplanations")
        assert "削除されず保持されます" in block

    def test_confirmation_uses_shared_danger_confirm_modal_with_window_confirm_fallback(self):
        """versioning.js の _doScheduleDeletion と同型: admin.js の共通2段確認モーダル
        （window.AdminDangerConfirm）があればそれを使い、無ければ window.confirm に
        フォールバックする。"""
        src = _read(DELIBERATION_JS)
        block = _function_block(src, "_bulkReviewExplanations")
        assert "window.AdminDangerConfirm" in block
        assert "window.AdminDangerConfirm.open" in block
        assert "window.confirm(confirmMessage)" in block

    def test_confirmation_gates_the_actual_bulk_request(self):
        """確認をキャンセルしたら fetch を送らない（_doBulkReview はコールバック内でのみ実行）。"""
        src = _read(DELIBERATION_JS)
        block = _function_block(src, "_bulkReviewExplanations")
        assert "function _doBulkReview" in block
        # window.confirm 分岐は確認が false の場合に return し、_doBulkReview を呼ばない。
        assert "if (!window.confirm(confirmMessage)) return;" in block


class TestExplanationReviewNoPollingAndForbiddenVocabulary:
    """③④: 新規追加分に禁止語彙・ポーリングが無いこと。"""

    def test_no_setinterval_or_settimeout_introduced(self):
        src = _read(DELIBERATION_JS)
        region = _review_queue_region(src)
        assert "setInterval" not in region
        assert "setTimeout" not in region

    def test_no_forbidden_vocabulary_introduced(self):
        src = _read(DELIBERATION_JS)
        region = _review_queue_region(src)
        for word in ("踏破", "達成率", "ランキング", "スコア"):
            assert word not in region, f"禁止語彙 {word!r} が説明レビューキュー追加分に含まれています"

    def test_fetch_only_happens_on_open_and_after_queue_actions(self):
        """取得（GET）は openInventory 経由の初回ロードのみで、キュー内操作
        （承認/却下・一括承認/却下）は差分をローカル状態から取り除くだけで
        全件再取得はしない（ポーリング禁止・無駄な fetch を増やさない）。"""
        src = _read(DELIBERATION_JS)
        load_fn = "function _loadExplanationReviewQueue"
        assert src.count(load_fn) == 1
        decide_block = _function_block(src, "_decideExplanationReviewCard")
        assert "_loadExplanationReviewQueue(" not in decide_block
        bulk_block = _function_block(src, "_bulkReviewExplanations")
        assert "_loadExplanationReviewQueue(" not in bulk_block


class TestExplanationReviewEscHtmlUsage:
    """⑤: キューの動的描画（カード本文・グループ見出し・メッセージ）は escHtml 経由。"""

    def test_card_rendering_uses_esc_html_repeatedly(self):
        src = _read(DELIBERATION_JS)
        block = _function_block(src, "_explanationReviewCardHtml")
        assert block.count("escHtml(") >= 3
        assert "escHtml(exp.body" in block

    def test_group_title_uses_esc_html(self):
        src = _read(DELIBERATION_JS)
        block = _function_block(src, "_explanationReviewGroupHtml")
        assert "escHtml(group.label)" in block

    def test_bulk_message_rendered_through_esc_html(self):
        src = _read(DELIBERATION_JS)
        block = _function_block(src, "_setExplanationReviewMessage")
        assert "escHtml(text)" in block

    def test_element_label_lookup_falls_back_without_bypassing_esc_html_at_render(self):
        """要素ラベルが引けない場合の代替表示（種別ラベル + element_id 先頭8文字）も、
        最終的に _explanationReviewGroupHtml で escHtml を通ってから描画される。"""
        src = _read(DELIBERATION_JS)
        block = _function_block(src, "_inventoryElementLabel")
        assert "substring(0, 8)" in block


class TestExplanationReviewEntryPointAndModal:
    """入口ボタン・モーダル DOM・グループ化・件数表示の受け入れ条件。"""

    def test_entry_button_container_present_in_inventory_modal(self):
        src = _read(DELIBERATION_JS)
        assert 'id="deliberation-explanation-review-entry"' in src

    def test_entry_button_shows_candidate_count_in_label(self):
        src = _read(DELIBERATION_JS)
        block = _function_block(src, "_renderExplanationReviewEntry")
        assert "説明レビュー (" in block
        assert "count" in block

    def test_entry_button_disabled_when_zero_candidates(self):
        src = _read(DELIBERATION_JS)
        block = _function_block(src, "_renderExplanationReviewEntry")
        assert 'count === 0 ? " disabled"' in block

    def test_entry_hidden_on_fetch_failure_fail_closed(self):
        """一覧取得に失敗したら items を null にし、ボタンごと出さない（fail-closed）。"""
        src = _read(DELIBERATION_JS)
        block = _function_block(src, "_loadExplanationReviewQueue")
        assert "explanationReviewState.items = null;" in block
        entry_block = _function_block(src, "_renderExplanationReviewEntry")
        assert "if (!items)" in entry_block

    def test_modal_grouped_by_element(self):
        src = _read(DELIBERATION_JS)
        assert "function _groupExplanationsByElement" in src
        block = _function_block(src, "_renderExplanationReviewList")
        assert "_groupExplanationsByElement(items)" in block

    def test_select_all_and_deselect_all_wired(self):
        src = _read(DELIBERATION_JS)
        assert 'id="deliberation-explanation-review-select-all"' in src
        assert 'id="deliberation-explanation-review-deselect-all"' in src

    def test_bulk_buttons_track_selected_count_and_disable_at_zero(self):
        src = _read(DELIBERATION_JS)
        block = _function_block(src, "_updateExplanationReviewToolbar")
        assert "count === 0" in block
        assert "選択した" in block

    def test_checkbox_state_persists_across_rerender(self):
        """チェック状態は explanationReviewState.selected に保持し、
        _renderExplanationReviewList の再描画後も維持される。"""
        src = _read(DELIBERATION_JS)
        card_block = _function_block(src, "_explanationReviewCardHtml")
        assert "explanationReviewState.selected[exp.id]" in card_block

    def test_closing_inventory_modal_also_closes_review_modal_and_resets_state(self):
        src = _read(DELIBERATION_JS)
        block = _function_block(src, "_closeInventoryModal")
        assert "_closeExplanationReviewModal()" in block
        assert "_resetExplanationReviewState()" in block

    def test_opening_inventory_resets_review_state_and_loads_queue_once(self):
        src = _read(DELIBERATION_JS)
        start = src.index("function openInventory")
        end = src.index("window.Deliberation = {")
        block = src[start:end]
        assert "_resetExplanationReviewState()" in block
        assert "_loadExplanationReviewQueue(documentId)" in block
        # openInventory 内で1回だけ呼ぶ（複数回・ループ内呼び出しにしない）。
        assert block.count("_loadExplanationReviewQueue(") == 1

    def test_modal_does_not_open_when_queue_is_empty(self):
        src = _read(DELIBERATION_JS)
        block = _function_block(src, "_openExplanationReviewModal")
        assert "if (!explanationReviewState.items || !explanationReviewState.items.length) return;" in block

    def test_existing_single_card_explanation_ux_is_untouched(self):
        """既存の overview 内カード（_explanationCardHtml/_decideExplanation/
        _bindExplanationActions）は変更せず併存させる（本機能は別の関数群として
        追加されている）。"""
        src = _read(DELIBERATION_JS)
        for name in ("_explanationCardHtml", "_decideExplanation", "_bindExplanationActions"):
            assert f"function {name}" in src
        # 既存関数はキュー専用の data 属性を持ち込んでいない。
        existing_block = _function_block(src, "_explanationCardHtml")
        assert "data-explanation-review-action" not in existing_block
        assert "data-explanation-review-checkbox" not in existing_block


class TestExplanationReviewEs5RegressionGuard:
    """開発ルール5: ES5 準拠を本追加分でも維持する。"""

    def test_no_arrow_functions(self):
        src = _read(DELIBERATION_JS)
        region = _review_queue_region(src)
        assert "=>" not in region

    def test_no_const_or_let(self):
        src = _read(DELIBERATION_JS)
        region = _review_queue_region(src)
        assert re.search(r"\bconst\s+\w", region) is None
        assert re.search(r"\blet\s+\w", region) is None

    def test_no_template_literals(self):
        src = _read(DELIBERATION_JS)
        region = _review_queue_region(src)
        assert "`" not in region
