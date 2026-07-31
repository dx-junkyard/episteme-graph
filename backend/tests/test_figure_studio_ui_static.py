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
        src = _read(FIGURE_STUDIO_JS)
        assert 'overlay.addEventListener("click", function (e) { if (e.target === overlay) _closeModal(); });' in src
        assert 'document.getElementById("figure-studio-close")' in src

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
