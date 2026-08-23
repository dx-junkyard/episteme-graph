"""理解サイクル Phase 1（Understanding Cycle, docs/features/understanding_cycle_design.md §5）
のフロントエンド実装に対する静的ガードレール。

対象（フロントのみ・バックエンド API は別エージェント実装。契約は §5.6 を正とする）:
  - frontend/public/js/discuss.js — OPEN（動機記録・持ち越し再訪）/ ELICIT・DIFF（予想の
    並置）/ LEAVE（持ち越す問いの選択）
  - frontend/public/js/app.js — ANCHOR（軽量4ボタン）/ 精読モードトグル
  - frontend/public/index.html — 常設ストリップ・精読モードトグルのマークアップ
  - frontend/public/css/styles.css — cycle-* / quick-anchor-* クラス

すべて静的解析（部分文字列検索・波括弧カウントによる関数本体抽出）のみ。実サーバ・実DOM・
ブラウザは使わない（test_discuss_phase2_ui_static.py と同じ流儀）。

検証観点:
1. discuss.js: renderCycleOpeningSection が buildOpeningHtml 内で renderCourseFocusSection
   より前に呼ばれる。
2. discuss.js: 契約フレーズ（「いまならどう考えますか」「なぜ今開きましたか」
   「次に持ち越すなら」）が存在する。
3. discuss.js: intention POST 成功パスに openingCache 無効化（invalidateOpeningCache）が
   複数箇所で呼ばれている。
4. discuss.js: 並置 DIFF に正誤・採点語彙が無い（UC2）。
5. discuss.js: 着地の Promise.all に landing-candidates 取得が入っている。
6. app.js / index.html: 4ボタンのラベル4つ・/cycle/anchor パス・
   data-ui-anchor="material.quick-anchor"・localStorage キー eg_precision_reading: の存在。
7. app.js: 既存の「ここについて質問」導線が残存している（回帰確認）。
8. styles.css: cycle-* / quick-anchor-* クラスの存在。
9. discuss.js + app.js: 理解サイクルのメトリクス6語彙がどこかで送信されている。
10. index.html: discuss.js の script タグが ?v= 無しのまま（既存契約の再確認）。
11. discuss.js 全域で絶対制約の禁止語彙（%・fixture・mock・dummy・setInterval・寄り道・
    疑え・confidence・スコア・達成率・踏破・ランキング）が一切含まれない。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP_JS = ROOT / "frontend" / "public" / "js" / "app.js"
DISCUSS_JS = ROOT / "frontend" / "public" / "js" / "discuss.js"
INDEX_HTML = ROOT / "frontend" / "public" / "index.html"
STYLES_CSS = ROOT / "frontend" / "public" / "css" / "styles.css"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _extract_function_body(src: str, signature: str) -> str:
    """`signature`（例: "function foo(...) {"）から対応する閉じ `}` までを、
    素朴な波括弧カウントで抽出する（test_discuss_phase2_ui_static.py と同じ流儀）。"""
    start = src.index(signature)
    brace_start = src.index("{", start)
    depth = 0
    i = brace_start
    while i < len(src):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
        i += 1
    raise AssertionError("unterminated function body for: " + signature)


class TestOpenSectionWiring:
    """OPEN（初回動機・持ち越し再訪, §5.1/§5.2）。"""

    def test_render_cycle_opening_section_called_before_course_focus(self):
        js = _read(DISCUSS_JS)
        block = _extract_function_body(js, "function buildOpeningHtml(data) {")
        idx_cycle = block.index("renderCycleOpeningSection(data)")
        idx_focus = block.index("renderCourseFocusSection(focus)")
        assert idx_cycle < idx_focus

    def test_opening_section_returns_empty_without_intention_field(self):
        js = _read(DISCUSS_JS)
        block = _extract_function_body(js, "function renderCycleOpeningSection(data) {")
        assert 'if (!intention) return "";' in block

    def test_carryover_and_motive_are_mutually_exclusive(self):
        """両方同時には出さない・二問目は出さない（§5.1）。"""
        js = _read(DISCUSS_JS)
        block = _extract_function_body(js, "function renderCycleOpeningSection(data) {")
        assert "if (intention.carryover) return renderCycleCarryoverBlock(intention.carryover);" in block
        assert 'if (intention.has_motive === false) return renderCycleMotiveBlock();' in block

    def test_precision_mode_link_available_even_when_off(self):
        """精読モード off でも「予想してから開く」に小さなリンクから入れる（§5.3）。"""
        js = _read(DISCUSS_JS)
        block = _extract_function_body(js, "function renderCycleMotiveBlock() {")
        assert "cycle-predict-link" in block
        assert "data-cycle-predict-open" in block


class TestContractPhrases:
    """バックエンド契約・設計書と対になる文言。"""

    def test_revisit_phrase_present(self):
        assert "いまならどう考えますか" in _read(DISCUSS_JS)

    def test_initial_motive_phrase_present(self):
        assert "なぜ今開きましたか" in _read(DISCUSS_JS)

    def test_leave_phrase_present(self):
        assert "次に持ち越すなら" in _read(DISCUSS_JS)


class TestOpeningCacheInvalidatedOnSave:
    """intention の保存に成功したら openingCache を無効化し、次回取得で最新状態から
    組み立て直させる（絶対制約 #5）。"""

    def test_invalidate_helper_defined(self):
        js = _read(DISCUSS_JS)
        block = _extract_function_body(js, "function invalidateOpeningCache() {")
        assert 'openingCache = { courseId: "", data: null };' in block

    def test_invalidate_called_from_multiple_save_paths(self):
        js = _read(DISCUSS_JS)
        # 定義1箇所 + 呼び出し複数箇所（動機保存・予想保存・再訪保存・持ち越し保存・reset）。
        assert js.count("invalidateOpeningCache();") >= 4

    def test_motive_save_path_invalidates_cache(self):
        js = _read(DISCUSS_JS)
        block = _extract_function_body(js, "function bindOpeningEvents(containerEl) {")
        motive_idx = block.index("data-cycle-motive-save")
        invalidate_idx = block.index("invalidateOpeningCache();", motive_idx)
        done_idx = block.index("記録しました。", motive_idx)
        assert motive_idx < invalidate_idx < done_idx


class TestDiffHasNoGradingVocabulary:
    """並置 DIFF は判定・採点をしない（UC2）。"""

    FORBIDDEN = ("正解", "不正解", "点数", "一致度")

    def test_diff_function_has_no_grading_words(self):
        js = _read(DISCUSS_JS)
        block = _extract_function_body(js, "function cycleDiffHtml(docs, predictionText) {")
        for word in self.FORBIDDEN:
            assert word not in block

    def test_no_grading_words_anywhere_in_discuss_js(self):
        js = _read(DISCUSS_JS)
        for word in self.FORBIDDEN:
            assert word not in js, f"discuss.js に採点語彙 {word!r} が含まれている"


class TestLandingCandidatesWiring:
    """LEAVE（§5.5）: 着地の Promise.all に持ち越し候補取得が入っている。"""

    def test_fetch_landing_candidates_defined(self):
        js = _read(DISCUSS_JS)
        block = _extract_function_body(js, "async function fetchLandingCandidates(courseId) {")
        assert "/cycle/landing-candidates" in block

    def test_maybe_show_landing_fetches_candidates(self):
        js = _read(DISCUSS_JS)
        block = _extract_function_body(js, "async function maybeShowLanding(courseId, reason) {")
        assert "fetchLandingCandidates(courseIdForFetch)" in block
        assert "_cycleLandingCandidates = results[3];" in block

    def test_leave_section_rendered_after_map_placement_section_and_before_recon(self):
        js = _read(DISCUSS_JS)
        block = _extract_function_body(
            js, "function buildLandingBodyHtml(tensionItems, anchorItems, reconItem) {"
        )
        idx_map_section = block.index("今日話した内容を地図に置く")
        idx_leave = block.index("cycleLeaveSectionHtml(_cycleLandingCandidates)")
        idx_recon = block.index("if (reconItem) {")
        assert idx_map_section < idx_leave < idx_recon

    def test_leave_selection_does_nothing_if_untouched(self):
        """何も選ばず閉じても何も起きない: 選択・自由入力の配線は明示クリックにのみ反応する
        （買い切りの自動送信・自動保存が無いことの構造確認）。"""
        js = _read(DISCUSS_JS)
        block = _extract_function_body(js, "function bindLandingContentEvents(root, reconItem) {")
        assert "data-cycle-leave-pick" in block
        assert "cycle-leave-free-link" in block


class TestQuickAnchorButtons:
    """ANCHOR（軽量4ボタン, §5.4）。"""

    LABELS = ("気になる", "まだ分からない", "あとで戻る", "何かとつながりそう")

    def test_four_labels_present_in_app_js(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function initSelectionAnchor() {")
        for label in self.LABELS:
            assert label in js
        # QUICK_ANCHOR_OPTIONS の定義そのものにも4つ揃っていること
        assert "curious" in js and "not_yet" in js and "return_later" in js and "connects" in js

    def test_four_labels_also_present_in_strip_markup(self):
        html = _read(INDEX_HTML)
        for label in self.LABELS:
            assert label in html

    def test_cycle_anchor_endpoint_path(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "async function postCycleAnchor(payload) {")
        assert "/cycle/anchor" in block
        assert 'method: "POST"' in block

    def test_quick_anchor_ui_anchor_registered_on_strip_container(self):
        html = _read(INDEX_HTML)
        assert 'data-ui-anchor="material.quick-anchor"' in html
        idx = html.index('data-ui-anchor="material.quick-anchor"')
        line_start = html.rfind("\n", 0, idx) + 1
        line_end = html.find("\n", idx)
        line = html[line_start:line_end]
        assert 'id="quick-anchor-strip"' in line
        assert "hidden" in line

    def test_strip_visibility_follows_course_and_topic_selection(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function updateQuickAnchorStrip() {")
        assert "state.course && state.currentTopicId" in block

    def test_strip_wired_once_from_init_app(self):
        js = _read(APP_JS)
        init_block = _extract_function_body(js, "async function initApp() {")
        assert "initQuickAnchorStrip();" in init_block

    def test_existing_ask_about_this_flow_is_unchanged(self):
        """既存の「ここについて質問」導線（テキスト選択→質問）は不変（回帰確認）。"""
        js = _read(APP_JS)
        assert '"ここについて質問"' in js
        block = _extract_function_body(js, "function initSelectionAnchor() {")
        assert 'btn.textContent = "ここについて質問";' in block


class TestPrecisionReadingToggle:
    """精読モード（§4.3/§5.3）。既定 off・コース単位の localStorage。"""

    def test_storage_key_prefix_present(self):
        js = _read(APP_JS)
        assert 'return "eg_precision_reading:"' in js

    def test_toggle_default_off_in_html(self):
        html = _read(INDEX_HTML)
        assert 'id="cycle-precision-toggle"' in html
        idx = html.index('id="cycle-precision-toggle"')
        tag_end = html.index(">", idx)
        assert 'aria-pressed="false"' in html[idx:tag_end]

    def test_discuss_js_reads_precision_mode_only_via_di(self):
        """discuss.js は localStorage を直接触らず DI 経由でだけ読む。"""
        js = _read(DISCUSS_JS)
        block = _extract_function_body(js, "function isPrecisionReadingOn() {")
        assert "isPrecisionReadingFn" in block
        assert "localStorage" not in block

    def test_app_js_injects_precision_reading_into_discuss_init(self):
        js = _read(APP_JS)
        assert "window.Discuss.init(" in js
        idx = js.index("window.Discuss.init(")
        snippet = js[idx:idx + 400]
        assert "isPrecisionReading" in snippet


class TestMetricVocabularyCoverage:
    """§10: cycle_* イベント6語彙がどこかから送信されていること。"""

    CYCLE_EVENTS = (
        "cycle_motive_saved",
        "cycle_prediction_saved",
        "cycle_diff_viewed",
        "cycle_carryover_saved",
        "cycle_revisit_answered",
        "cycle_anchor_quick",
    )

    def test_exactly_six_cycle_events(self):
        assert len(self.CYCLE_EVENTS) == 6

    def test_all_six_events_sent_somewhere(self):
        combined = _read(APP_JS) + "\n" + _read(DISCUSS_JS)
        for event in self.CYCLE_EVENTS:
            assert '"' + event + '"' in combined, f"イベント {event!r} の送信箇所が見つからない"

    def test_metrics_sent_with_empty_payload(self):
        """絶対制約 #7: payload は常に空（sendDiscussMetric(event, {})）。"""
        discuss_js = _read(DISCUSS_JS)
        app_js = _read(APP_JS)
        for event in self.CYCLE_EVENTS:
            call = 'sendDiscussMetric("' + event + '", {})'
            assert call in discuss_js or call in app_js, f"{event} が空payloadで送信されていない"


class TestStylesCssClassesDefined:
    def test_cycle_and_quick_anchor_classes_present(self):
        css = _read(STYLES_CSS)
        for selector in (
            ".cycle-opening-block",
            ".cycle-textarea",
            ".cycle-diff",
            ".cycle-diff-col",
            ".cycle-leave-option",
            ".cycle-precision-toggle",
            ".quick-anchor-strip",
            ".quick-anchor-btn",
            "#quick-anchor-popover",
            ".quick-anchor-toast",
        ):
            assert selector in css, f"missing CSS selector: {selector}"

    def test_material_foot_no_longer_force_hidden_in_discuss_mode(self):
        """常設ストリップを material-foot に置いたため、discuss モードでも
        material-foot 自体は隠さない（次へボタンは JS 側で個別に隠す）。"""
        css = _read(STYLES_CSS)
        assert ".app.discuss-on .material-foot {\n  display: none;\n}" not in css


class TestIndexHtmlScriptTagUnchanged:
    def test_discuss_js_script_tag_has_no_version_query(self):
        html = _read(INDEX_HTML)
        assert '<script src="/js/discuss.js"></script>' in html


class TestDiscussJsForbiddenVocabulary:
    """絶対制約: discuss.js には以下の語彙を一切書けない。"""

    FORBIDDEN = (
        "%", "fixture", "mock", "dummy", "setInterval",
        "寄り道", "疑え", "confidence", "スコア", "達成率", "踏破", "ランキング",
    )

    def test_forbidden_tokens_absent(self):
        js = _read(DISCUSS_JS)
        for token in self.FORBIDDEN:
            assert token not in js, f"discuss.js に禁止トークン {token!r} が含まれている"
