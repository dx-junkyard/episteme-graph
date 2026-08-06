"""教材図スタジオ（Teaching Figure Studio）のフロントエンド静的ガードレール。

正本: docs/features/teaching_figure_studio_design.md
  §6.1 フロント（都度生成モーダル・状態はブラウザ内のみ）
  §5.3 右ペイン第3トグル「図の提案」
  §7.3 原稿スタジオプレビューの2段フォールバック
  §10 UI static: ES5 準拠 / innerHTML への SVG 直挿入が無いこと（blob+img 経由のみ）/
      モデルチップ scene / 右ペイン3値トグルの後方互換

バックエンド（`/api/admin/courses/{id}/figure-studio/turn` ほか）は並行実装中のため、
ここでは JS/HTML/CSS 側の静的契約のみを検証する
（`test_admin_help_inspect_ui_static.py` / `test_figure_course_flow_ui_static.py` と同じ流儀）。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ADMIN_HTML = ROOT / "frontend" / "public" / "admin.html"
FIGURE_STUDIO_JS = ROOT / "frontend" / "public" / "js" / "admin-figure-studio.js"
ADMIN_LS_JS = ROOT / "frontend" / "public" / "js" / "admin-lecture-studio.js"
STYLES_CSS = ROOT / "frontend" / "public" / "css" / "styles.css"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


class TestModuleWiring:
    def test_admin_html_loads_figure_studio_script(self):
        src = _read(ADMIN_HTML)
        assert "js/admin-figure-studio.js" in src

    def test_public_api_surface(self):
        src = _read(FIGURE_STUDIO_JS)
        assert "window.FigureStudio = {" in src
        start = src.index("window.FigureStudio = {")
        block = src[start : src.index("\n  };", start)]
        for method in ("init:", "open:", "close:", "isOpen:"):
            assert method in block, f"{method} が公開 API に無い"

    def test_di_injection_with_window_fallback(self):
        """deliberation.js と同型の DI（apiFetch / apiFetchRaw / escHtml）。"""
        src = _read(FIGURE_STUDIO_JS)
        assert "var deps = { apiFetch: null, apiFetchRaw: null, escHtml: null };" in src
        assert "deps.apiFetch || window.apiFetch" in src
        assert "deps.apiFetchRaw || window.apiFetchRaw" in src

    def test_lecture_studio_injects_dependencies(self):
        """呼び出し元は原稿スタジオのみ。init はそこから注入する（起動点を増やさない）。"""
        src = _read(ADMIN_LS_JS)
        assert "window.FigureStudio.init({" in src

    def test_no_polling(self):
        assert "setInterval" not in _read(FIGURE_STUDIO_JS)


class TestSvgRenderedOnlyViaBlobImage:
    """FG3: SVG は blob URL → <img> でしか描画しない（innerHTML 直挿入の禁止）。"""

    def test_preview_uses_blob_and_object_url(self):
        src = _read(FIGURE_STUDIO_JS)
        start = src.index("function _setPreviewSvg(svg) {")
        block = src[start : src.index("\n  }", start)]
        assert 'new Blob([String(svg || "")], { type: "image/svg+xml" })' in block
        assert "URL.createObjectURL(blob)" in block
        assert "img.src = url;" in block
        assert "innerHTML" not in block

    def test_no_innerhtml_assignment_carries_svg_source(self):
        src = _read(FIGURE_STUDIO_JS)
        for m in re.finditer(r"innerHTML\s*=\s*([^;]*);", src, re.S):
            assert "svg" not in m.group(1).lower(), (
                "innerHTML への SVG 直挿入が疑われる: " + m.group(1)[:120]
            )

    def test_object_urls_revoked_on_close(self):
        src = _read(FIGURE_STUDIO_JS)
        assert "URL.revokeObjectURL(url)" in src
        start = src.index("function _closeModal() {")
        block = src[start : src.index("\n  }", start)]
        assert "_revokeObjectUrls();" in block
        assert "studio = _emptyState();" in block, "閉じたら状態をリセットすること（§6.1）"


class TestModalContract:
    def test_modal_created_on_demand_at_z_index_9500(self):
        src = _read(FIGURE_STUDIO_JS)
        assert 'overlay = document.createElement("div")' in src
        assert "z-index:9500" in src
        assert "document.body.appendChild(overlay);" in src

    def test_background_click_and_close_button_close_modal(self):
        """背景クリック / × は「未保存なら確認」を通す（_requestClose）。"""
        src = _read(FIGURE_STUDIO_JS)
        assert 'overlay.addEventListener("click", function (e) { if (e.target === overlay) _requestClose(); });' in src
        assert 'document.getElementById("figure-studio-close")' in src
        assert "closeBtn.addEventListener(\"click\", _requestClose)" in src

    def test_unsaved_close_asks_for_confirmation_with_a_fact_statement(self):
        """未保存の図を捨てる前に事実文で確認する（保存済み・図なしは確認しない）。"""
        src = _read(FIGURE_STUDIO_JS)
        assert "作成中の図は保存されていません。閉じますか？" in src
        start = src.index("function _requestClose() {")
        block = src[start : src.index("\n  }", start)]
        assert "studio.currentSvg && !studio.adoptedFigureId && !studio.savedDraftId" in block
        assert "window.AdminDangerConfirm" in block
        assert "window.confirm(CLOSE_CONFIRM_MESSAGE)" in block

    def test_escape_key_closes_through_the_same_confirmation(self):
        src = _read(FIGURE_STUDIO_JS)
        start = src.index("function _onModalKeyDown(e) {")
        block = src[start : src.index("\n  }", start)]
        assert 'e.key === "Escape" || e.keyCode === 27' in block
        assert "_requestClose();" in block
        assert 'document.addEventListener("keydown", _onModalKeyDown);' in src
        assert 'document.removeEventListener("keydown", _onModalKeyDown);' in src

    def test_enter_send_has_ime_guard(self):
        src = _read(FIGURE_STUDIO_JS)
        assert '!e.shiftKey && !e.isComposing && e.keyCode !== 229' in src

    def test_model_chip_uses_figure_studio_scene(self):
        src = _read(FIGURE_STUDIO_JS)
        assert "window.AdminLlmModels && window.AdminLlmModels.createModelChip" in src
        assert 'sceneKey: "figure_studio"' in src

    def test_api_paths_match_contract(self):
        src = _read(FIGURE_STUDIO_JS)
        assert '"/figure-studio/turn"' in src
        assert '"/teaching-figures"' in src
        assert '"/teaching-figures/" + encodeURIComponent(item.id) + "/image"' in src

    def test_adopt_shows_double_gate_fact_statement(self):
        """FG2 の二重ゲート（採用 → トピック保存）と音声の作り直しを事実文で出す。"""
        src = _read(FIGURE_STUDIO_JS)
        assert "保存するとこのトピックの生成済み音声は作り直しになります。" in src
        assert "トピックを保存するまで学習者には表示されません。" in src

    def test_error_text_uses_server_detail_without_numbers(self):
        """429 / degraded はサーバの detail をそのまま出す（上限値を UI に書かない）。"""
        src = _read(FIGURE_STUDIO_JS)
        start = src.index("function _errorText(err, fallback) {")
        block = src[start : src.index("\n  }", start)]
        assert "err.detail" in block
        assert not re.search(r"\d{2,}", block), "エラー文に数値を書かない（FG8）"


class TestTeachingFigureLifecycleUi:
    """生成図のライフサイクル UI（§7.1b / §7.4 / §8 PATCH）。

    レビュー指摘（M2/M3）の修正: ①PATCH を呼ぶ UI が存在せず retire / draft→adopted /
    caption 修正が到達不能だった ②既存図タブの挿入が §7.1b のトピック登録を経ず、
    draft/retired の図を挿入できた（学習者に生 UUID が露出し、AI 書き換えで embed が消える）。
    """

    def test_patch_helper_targets_the_teaching_figure_endpoint(self):
        src = _read(FIGURE_STUDIO_JS)
        start = src.index("function _patchTeachingFigure(item, body) {")
        block = src[start : src.index("\n  }", start)]
        assert 'method: "PATCH"' in block
        assert "_teachingFigurePath(item.id)" in block

    def test_teaching_figure_insert_registers_the_topic_via_the_server(self):
        """生成図の挿入は register_topic_id 付き PATCH を必ず通す（§7.1b）。"""
        src = _read(FIGURE_STUDIO_JS)
        start = src.index("function _insertExisting(item) {")
        block = src[start : src.index("\n  }", start)]
        assert "register_topic_id: studio.topicId" in block
        assert "_patchTeachingFigure(item, body)" in block
        # draft は採用も同じ操作で行う
        assert 'if (item.status !== "adopted") body.status = "adopted";' in block

    def test_retired_figure_is_not_insertable(self):
        src = _read(FIGURE_STUDIO_JS)
        start = src.index("function _insertExisting(item) {")
        block = src[start : src.index("\n  }", start)]
        assert 'if (item.status === "retired") {' in block
        assert "RETIRED_INSERT_NOTICE" in block
        # カード側でも回収済みには挿入ボタンを描かない
        card = src[src.index("function _existingCardHtml(item, idx) {") :]
        card = card[: card.index("\n  }")]
        assert "if (!retired) {" in card

    def test_missing_topic_blocks_the_registration_instead_of_inserting_blindly(self):
        src = _read(FIGURE_STUDIO_JS)
        start = src.index("function _insertExisting(item) {")
        block = src[start : src.index("\n  }", start)]
        assert "if (!studio.topicId) {" in block

    def test_extracted_figures_keep_insert_only_behaviour(self):
        """抽出図（document_figures）は挿入のみ（設計 §6.2-3。状態も登録も持たない）。"""
        src = _read(FIGURE_STUDIO_JS)
        start = src.index("function _insertExisting(item) {")
        block = src[start : src.index("\n  }", start)]
        assert "if (!item.teaching) {" in block
        head = block[: block.index("if (item.status === \"retired\")")]
        assert "_patchTeachingFigure" not in head

    def test_retire_confirms_with_a_reference_count_fact_statement(self):
        src = _read(FIGURE_STUDIO_JS)
        start = src.index("function _retireMessage(count) {")
        block = src[start : src.index("\n  }", start)]
        assert "この図を参照中のトピックが " in block
        assert "配信対象ではありません" in block
        retire = src.index("function _retireFigure(item) {")
        retire_block = src[retire : src.index("\n  }", retire)]
        assert "_confirmThen(" in retire_block
        assert '_countTopicReferences(item.id)' in retire_block
        assert '{ status: "retired" }' in retire_block

    def test_reference_count_is_never_guessed(self):
        """件数を数えられない場合は件数を書かない（-1 で分岐）。"""
        src = _read(FIGURE_STUDIO_JS)
        start = src.index("function _countTopicReferences(figureId) {")
        block = src[start : src.index("\n  }", start)]
        assert "return -1;" in block

    def test_restore_transitions_back_to_adopted(self):
        src = _read(FIGURE_STUDIO_JS)
        start = src.index("function _restoreFigure(item) {")
        block = src[start : src.index("\n  }", start)]
        assert '{ status: "adopted" }' in block

    def test_caption_edit_displays_the_saved_value(self):
        """FG5: サーバが「（模式図）」を付けるため、送った文字列で上書きしない。"""
        src = _read(FIGURE_STUDIO_JS)
        start = src.index("function _saveCaption(item, idx) {")
        block = src[start : src.index("\n  }", start)]
        assert "{ caption: value }" in block
        assert "data && data.figure ? data.figure.caption : value" in block

    def test_snapshot_failure_is_shown_as_a_fact_statement(self):
        src = _read(FIGURE_STUDIO_JS)
        assert "配信用の画像の更新に失敗したため、再保存をおすすめします" in src
        for fn in ("_insertExisting", "_retireFigure", "_restoreFigure", "_saveCaption", "_saveFigure"):
            start = src.index("function " + fn + "(")
            block = src[start : src.index("\n  }", start)]
            assert "image_snapshot_failed" in block, fn

    def test_card_actions_are_serialized_to_avoid_double_writes(self):
        src = _read(FIGURE_STUDIO_JS)
        start = src.index("function _runCardAction(item, busyMessage, run) {")
        block = src[start : src.index("\n  }", start)]
        assert "if (!studio || studio.cardBusy) return;" in block
        assert "studio.cardBusy = true;" in block
        assert "studio.cardBusy = false;" in block

    def test_lifecycle_buttons_carry_ui_anchors(self):
        src = _read(FIGURE_STUDIO_JS)
        for anchor in (
            "lecture-studio.figure-studio-insert",
            "lecture-studio.figure-studio-caption",
            "lecture-studio.figure-studio-retire",
            "lecture-studio.figure-studio-restore",
        ):
            assert 'data-ui-anchor="' + anchor + '"' in src


class TestSaveButtonsSingleUse:
    """m9-1: 採用後もボタンが活性のままで、二度押しで行・登録・embed が重複していた。"""

    def test_saved_state_disables_both_save_buttons(self):
        src = _read(FIGURE_STUDIO_JS)
        start = src.index("function _updateActionButtons() {")
        block = src[start : src.index("\n  }", start)]
        assert "studio.adoptedFigureId || studio.savedDraftId" in block
        assert "adoptBtn.disabled = !hasSvg || busy || saved;" in block
        assert "draftBtn.disabled = !hasSvg || busy || saved;" in block

    def test_draft_save_records_the_saved_id(self):
        src = _read(FIGURE_STUDIO_JS)
        start = src.index("function _saveFigure(adopt) {")
        block = src[start : src.index("\n  }", start)]
        assert "studio.savedDraftId = figureId;" in block
        assert "studio.adoptedFigureId = figureId;" in block

    def test_a_new_figure_clears_the_saved_state(self):
        """図を作り直したら別の図として保存できる（保存済みフラグを落とす）。"""
        src = _read(FIGURE_STUDIO_JS)
        start = src.index("function _sendTurn(presetInstruction) {")
        block = src[start : src.index("\n  }", start)]
        assert "studio.adoptedFigureId = null;" in block
        assert "studio.savedDraftId = null;" in block


class TestPreviewObjectUrlLifecycle:
    """m9-5: プレビュー差し替え時に旧 ObjectURL を revoke していなかった。"""

    def test_replacing_the_preview_revokes_the_previous_url(self):
        src = _read(FIGURE_STUDIO_JS)
        start = src.index("function _setPreviewSvg(svg) {")
        block = src[start : src.index("\n  }", start)]
        assert "if (studio.previewUrl) {" in block
        assert "URL.revokeObjectURL(stale)" in block
        assert "studio.previewUrl = url;" in block


class TestFigureStudioEs5:
    """開発ルール5: admin 系 JS は ES5 で書く。"""

    def test_no_arrow_functions(self):
        assert "=>" not in _read(FIGURE_STUDIO_JS)

    def test_no_const_or_let(self):
        src = _read(FIGURE_STUDIO_JS)
        assert re.search(r"(^|[^\w.$])const\s+\w", src) is None
        assert re.search(r"(^|[^\w.$])let\s+\w", src) is None

    def test_no_template_literals_or_class(self):
        src = _read(FIGURE_STUDIO_JS)
        assert "`" not in src
        assert re.search(r"(^|[^\w.$])class\s+\w", src) is None

    def test_no_promise_finally(self):
        """deliberation.js と同型: 後処理は .then チェーンの末尾で行う。"""
        assert ".finally(" not in _read(FIGURE_STUDIO_JS)


class TestRightPaneThreeWayToggle:
    """§5.3: 右ペイントグルの3値化。既存2値（根拠リンク / つまづき）の挙動は不変。"""

    def test_html_has_three_toggle_buttons(self):
        src = _read(ADMIN_HTML)
        start = src.index('id="ls-stumble-tabs"')
        block = src[start : src.index("</div>", start)]
        for mode in ("evidence", "stumble", "figures"):
            assert 'data-ls-rightmode="' + mode + '"' in block
        assert 'data-ui-anchor="lecture-studio.stumble-tab-figures"' in block

    def test_evidence_remains_the_default_mode(self):
        src = _read(ADMIN_LS_JS)
        assert 'rightPaneMode: "evidence",' in src
        assert 'lsState.rightPaneMode = btn.getAttribute("data-ls-rightmode") || "evidence";' in src

    def test_render_has_figures_branch_before_stumble(self):
        src = _read(ADMIN_LS_JS)
        start = src.index("function lsRenderRightPaneForTopic(topic) {")
        block = src[start : src.index("\n  }", src.index("lsBindCourseEvidenceCards(topic, preview);", start))]
        assert 'if (lsState.rightPaneMode === "figures") {' in block
        assert 'lsRenderFigureSuggestionsPane(topic, preview);' in block
        # 既存分岐が残っていること（後方互換）
        assert 'lsLoadStumbleSummary(topic, preview);' in block
        assert 'preview.innerHTML = lsCourseEvidenceHtml(topic);' in block

    def test_suggestions_pane_carries_ui_anchor(self):
        src = _read(ADMIN_LS_JS)
        assert 'data-ui-anchor="lecture-studio.figure-suggestions"' in src
        assert 'data-ui-anchor="lecture-studio.figure-suggestions-generate"' in src

    def test_suggestion_cards_show_stage_labels_only(self):
        """FG8: confidence は API の段階ラベル（confidence_label）だけを描画する。"""
        src = _read(ADMIN_LS_JS)
        start = src.index("function lsFigureSuggestionCardHtml(suggestion) {")
        block = src[start : src.index("\n  }", start)]
        assert "suggestion.confidence_label" in block
        assert re.search(r"suggestion\.confidence\b(?!_label)", block) is None

    def test_regenerate_confirms_with_fact_statement(self):
        src = _read(ADMIN_LS_JS)
        assert "提案を作り直すと、まだ判断していない候補は差し替え済みになります。" in src
        assert "window.AdminDangerConfirm" in src


class TestInsertFigureEntryPoint:
    """§6.2-1: ツールバーの [🖼 図を挿入]（読み上げ原稿では無効化）。"""

    def test_generic_insert_helper_extracted(self):
        src = _read(ADMIN_LS_JS)
        assert "function lsInsertTextAtCursor(el, text) {" in src
        start = src.index("function lsInsertSlideMarkerIntoTextarea(el) {")
        block = src[start : src.index("\n  }", start)]
        assert 'lsInsertTextAtCursor(el, "\\n===\\n");' in block

    def test_insert_helper_dispatches_input_event(self):
        src = _read(ADMIN_LS_JS)
        start = src.index("function lsInsertTextAtCursor(el, text) {")
        block = src[start : src.index("\n  }", start)]
        assert "el.selectionStart = el.selectionEnd = newPos;" in block
        assert "el.focus();" in block
        assert 'el.dispatchEvent(new Event("input"));' in block

    def test_button_present_with_anchor(self):
        src = _read(ADMIN_LS_JS)
        assert 'id="ls-course-insert-figure-btn"' in src
        assert 'data-ui-anchor="lecture-studio.insert-figure"' in src

    def test_button_disabled_on_spoken_script_with_reason(self):
        src = _read(ADMIN_LS_JS)
        start = src.index("function updateInsertFigureBtnState() {")
        block = src[start : src.index("\n    }", start)]
        assert 'courseSlideLastFocus === "ls-course-spoken-script"' in block
        assert "insertFigureBtn.disabled = onSpoken;" in block
        assert "読み上げ原稿には図を挿入できません" in block

    def test_open_passes_contract_options(self):
        src = _read(ADMIN_LS_JS)
        assert "window.FigureStudio.open({" in src
        start = src.index("window.FigureStudio.open({")
        block = src[start : src.index("});", start)]
        for key in ("courseId:", "topicId:", "insertTarget:", "figuresIndex:", "documentIds:", "onInsert:"):
            assert key in block, f"open() の契約キー {key} が渡されていない"

    def test_insert_does_not_save_topic(self):
        """図の挿入は本文編集まで。トピック保存は既存の [原稿を保存] のみ（FG2）。"""
        src = _read(ADMIN_LS_JS)
        start = src.index("function lsApplyFigureInsert(")
        block = src[start : src.index("\n  }", start)]
        for method in ('"PUT"', '"DELETE"', '"PATCH"'):
            assert method not in block


class TestFigureSuggestionsDegraded:
    """m9/M4: 生成失敗（degraded）を「候補ゼロ」と混同させない。"""

    def test_generate_keeps_degraded_and_note_in_the_pane_cache(self):
        src = _read(ADMIN_LS_JS)
        start = src.index("function lsGenerateFigureSuggestions(topic, container, btn) {")
        block = src[start : src.index("\n  }", start)]
        assert "degraded: degraded," in block
        assert 'note: (data && data.note) || ""' in block

    def test_pane_shows_the_server_fact_statement_instead_of_an_empty_list(self):
        src = _read(ADMIN_LS_JS)
        start = src.index("function lsFigureSuggestionsHtml(data) {")
        block = src[start : src.index("\n  }", start)]
        assert "ls-figure-suggestions-degraded" in block
        assert "data.note || LS_FIGURE_SUGGEST_DEGRADED_FALLBACK" in block
        # 「まだありません」は degraded でないときだけ出す（区別する）
        assert "if (!(data && data.degraded)) {" in block

    def test_degraded_fallback_text_has_no_numbers(self):
        src = _read(ADMIN_LS_JS)
        start = src.index("var LS_FIGURE_SUGGEST_DEGRADED_FALLBACK =")
        block = src[start : src.index(";", start)]
        assert not re.search(r"\d", block), "事実文に数値を書かない（FG8）"


class TestFigureSuggestionDecisionConflicts:
    """m9-3 / m9-4: accept の 409 を握り潰さない / accepted に [却下] を描かない。"""

    def test_accept_handles_conflict_like_dismiss_does(self):
        src = _read(ADMIN_LS_JS)
        start = src.index("function lsOpenFigureStudioForSuggestion(topic, suggestionId) {")
        block = src[start : src.index("\n  }", start)]
        assert "res.status === 409" in block
        assert "lsShowActionStatus(detail" in block
        assert "lsRenderRightPaneForTopic(topic)" in block

    def test_dismiss_button_is_candidate_only(self):
        src = _read(ADMIN_LS_JS)
        start = src.index("function lsFigureSuggestionCardHtml(suggestion) {")
        block = src[start : src.index("\n  }", start)]
        assert 'var dismissable = status === "candidate";' in block
        assert 'var buildable = status === "candidate" || status === "accepted";' in block
        assert "data-figure-suggestion-dismiss" in block


class TestDeferredTextareaFocus:
    """m9-2: モーダル表示中は背後の textarea にフォーカスを移さない。"""

    def test_insert_defers_focus_while_the_modal_is_open(self):
        src = _read(ADMIN_LS_JS)
        start = src.index("function lsInsertTextAtCursor(el, text) {")
        block = src[start : src.index("\n  }", start)]
        assert "if (lsModalBlocksTextareaFocus()) {" in block
        assert "lsDeferredTextareaFocus = { el: el, pos: newPos };" in block
        assert "el.focus();" in block

    def test_focus_is_restored_when_the_modal_closes(self):
        src = _read(ADMIN_LS_JS)
        assert "function lsRestoreDeferredTextareaFocus() {" in src
        assert src.count("onClose: lsRestoreDeferredTextareaFocus") >= 2

    def test_studio_invokes_on_close_after_removing_the_modal(self):
        src = _read(FIGURE_STUDIO_JS)
        start = src.index("function _closeModal() {")
        block = src[start : src.index("\n  }", start)]
        assert "if (modal) modal.remove();" in block
        assert 'if (typeof onClose === "function") onClose();' in block
        assert block.index("modal.remove();") < block.index("onClose();")


class TestRetireReferenceCounting:
    """§7.4: 参照中トピック数はコース構造から数え、スタジオへ関数として渡す。"""

    def test_counter_is_passed_to_the_studio(self):
        src = _read(ADMIN_LS_JS)
        assert src.count("countTopicReferences: lsCountTopicsReferencingFigure") >= 2

    def test_counter_uses_the_three_reference_paths(self):
        src = _read(ADMIN_LS_JS)
        start = src.index("function lsTopicReferencesFigure(topic, figureId) {")
        block = src[start : src.index("\n  }", start)]
        assert "topic.linked_figure_ids" in block
        assert "topic.evidence_links" in block
        assert 'text.indexOf("![[figure:" + figureId + "]]")' in block


class TestCourseSlideIndicatorAutoPaginate:
    """§7.5: コーストピックのスライド枚数は受講側と同じ自動ページ分割で数える。"""

    def test_fetch_helper_sends_the_auto_paginate_option(self):
        src = _read(ADMIN_LS_JS)
        start = src.index("function lsFetchSplitSlides(displayText, spokenText, formulas, autoPaginate) {")
        block = src[start : src.index("\n  }", start)]
        assert "auto_paginate: !!autoPaginate," in block
        assert "autoPaginated: !!data.auto_paginated," in block
        assert "spokenDegraded: !!data.spoken_degraded," in block

    def test_course_topic_indicator_requests_auto_pagination(self):
        src = _read(ADMIN_LS_JS)
        assert "lsFetchSplitSlides(dText, sText, [], true)" in src

    def test_chunk_indicator_keeps_the_previous_behaviour(self):
        """チャンク編集側の呼び出しは 3 引数のまま（auto_paginate=false 相当）。"""
        src = _read(ADMIN_LS_JS)
        assert "lsFetchSplitSlides(displayText, spokenText, chunk.formulas || [])" in src

    def test_indicator_states_the_timer_advance_fact(self):
        src = _read(ADMIN_LS_JS)
        start = src.index("function lsRenderSlideIndicatorEl(el, split) {")
        block = src[start : src.index("\n  }", start)]
        assert "split.spokenDegraded" in block
        assert "タイマー送りになります" in block
        assert "=== で明示分割すると解消できます" in block
        # スライド分割そのものはクライアントで再実装しない（サーバの結果だけを描く）
        assert "split(" not in block


class TestStudioPreviewFallback:
    """§7.3: ![[figure:id]] プレビューの2段フォールバック（figures_index）。"""

    def test_figures_index_stored_from_course_structure(self):
        src = _read(ADMIN_LS_JS)
        assert "figuresIndex: {}," in src
        assert "lsState.figuresIndex = (data && data.figures_index) || {};" in src

    def test_caches_cleared_on_course_switch(self):
        src = _read(ADMIN_LS_JS)
        assert src.count("lsState.figuresIndex = {};") >= 2
        assert src.count("lsState.figureSuggestionsByTopic = {};") >= 2

    def test_figure_branch_falls_back_to_index(self):
        src = _read(ADMIN_LS_JS)
        start = src.index('if (embed.kind === "figure") {')
        end = src.index('if (embed.kind === "source" && (embedId === "summary"', start)
        block = src[start:end]
        # 1段目: evidence_links 由来 item（document_id 付き）
        assert "figureItem.document_id" in block
        # 2段目: figures_index
        assert "lsFigureIndexFetch(" in block
        assert "data-figure-fetch-path" in block

    def test_index_fetch_builds_admin_paths(self):
        src = _read(ADMIN_LS_JS)
        start = src.index("function lsFigureIndexFetch(figureId) {")
        block = src[start : src.index("\n  }", start)]
        assert "/teaching-figures/" in block
        assert "/admin/documents/" in block
        assert 'path.indexOf("/api/") === 0' in block, "image_url の /api プレフィックスを剥がすこと"


class TestStyles:
    def test_figure_studio_classes_defined(self):
        src = _read(STYLES_CSS)
        for selector in (
            ".figure-studio-dialog {",
            ".figure-studio-preview-frame {",
            ".figure-studio-preview-img {",
            ".figure-studio-card {",
            ".figure-studio-footer {",
            ".ls-figure-suggestion-card {",
            ".ls-figure-suggestions-toolbar {",
        ):
            assert selector in src, f"{selector} が styles.css に見つかりません"

    def test_disabled_toolbar_button_has_visible_state(self):
        src = _read(STYLES_CSS)
        assert ".ls-mini-tab:disabled {" in src
