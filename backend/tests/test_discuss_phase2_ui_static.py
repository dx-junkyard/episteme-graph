"""discuss モード（「論文と話す」, docs/features/discussion_mode_design.md §6.3 Phase 2）の
フロントエンド実装（frontend/public/js/discuss.js, app.js, index.html, styles.css）に対する
静的ガードレール。test_discuss_ui_static.py（Phase 1）と同じ流儀で、ソース文字列の
部分一致・関数本体の抽出検査のみを行う（実サーバ・実DOM不使用）。

検証観点:
1. discuss.js: 開幕画面3区画（この論文が賭けているもの／理論のバックボーン／最初の一手）
   の見出し文言が存在する。
2. discuss.js: 開幕画面は available:false / 非 ok 応答 / documents 空のとき教材区画を
   上書きしない（fail-closed。Phase 1 のプレースホルダのまま）。
3. discuss.js: 着地画面（consolidation）の3区画見出し・スキップボタン・15分無活動
   タイマー定数が存在する。
4. discuss.js に数値スコア・件数・網羅率を学習者に見せる表示が無い。
5. app.js: 分岐チップ（深掘り／横展開）は discuss モード限定でのみ描画される。
6. app.js: sendMessage 成功パスから notifyActivity() を呼ぶ配線がある。
7. app.js: selectTopic の discuss→通常トピック遷移直後に着地画面トリガー②が発火する。
8. index.html: discuss.js の script タグと着地モーダルのルート要素が存在する。
9. discuss.js 全体に「寄り道」「疑え」という語が一切含まれない。
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
    素朴な波括弧カウントで抽出する（test_discuss_ui_static.py と同じ流儀）。"""
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


class TestOpeningScreenSections:
    """設計 §3.3: 開幕画面3区画。すべて非LLM・A層成果の読み出しのみ。"""

    def test_three_section_headings_present(self):
        js = _read(DISCUSS_JS)
        assert "この論文が賭けているもの" in js
        assert "理論のバックボーン" in js
        assert "最初の一手" in js

    def test_fragile_point_subsection_present(self):
        js = _read(DISCUSS_JS)
        assert "最も脆い一手" in js

    def test_free_input_note_present(self):
        js = _read(DISCUSS_JS)
        assert "自由に入力してもかまいません。" in js

    def test_backbone_node_click_starts_conversation(self):
        """ノードクリック = そこから対話開始（sendPrompt 経由・事実ベースの定型文）。"""
        js = _read(DISCUSS_JS)
        block = _extract_function_body(js, "function renderBackboneSection(doc) {")
        assert "data-discuss-ask" in block
        assert "位置づけと根拠を教えてください" in block

    def test_review_status_gets_subtle_visual_difference_not_alarm_color(self):
        """review_status/source_backing_status が source_backed でないノードは
        点線枠などの控えめな差にとどめ、警告色・強調色にしない（設計 §3.3）。"""
        js = _read(DISCUSS_JS)
        block = _extract_function_body(js, "function renderBackboneSection(doc) {")
        assert "source_backed" in block
        assert "discuss-backbone-node--review" in block
        css = _read(STYLES_CSS)
        review_rule = css.split(".discuss-backbone-node--review {")[1].split("}")[0]
        assert "dashed" in review_rule
        for alarm_color in ("red", "#f00", "warning"):
            assert alarm_color not in review_rule.lower()


class TestOpeningScreenFailClosed:
    """開幕画面は API 失敗・available:false・documents 空のとき教材区画を
    上書きしない（Phase 1 プレースホルダのまま fail-closed に縮退する）。"""

    def test_render_opening_checks_ok_before_replacing(self):
        js = _read(DISCUSS_JS)
        block = _extract_function_body(js, "async function renderOpening(containerEl, courseId) {")
        assert "if (!res.ok) return;" in block

    def test_render_opening_checks_available_flag(self):
        js = _read(DISCUSS_JS)
        block = _extract_function_body(js, "async function renderOpening(containerEl, courseId) {")
        assert "if (!data || !data.available) return;" in block

    def test_render_opening_checks_empty_documents(self):
        js = _read(DISCUSS_JS)
        block = _extract_function_body(js, "function buildOpeningHtml(data) {")
        assert "if (!docs.length) return" in block
        opening_block = _extract_function_body(js, "async function renderOpening(containerEl, courseId) {")
        assert "if (!html) return;" in opening_block

    def test_render_opening_never_renders_fixture_or_fake_data(self):
        js = _read(DISCUSS_JS)
        forbidden = ("ATLAS_FIXTURE", "fixture", "mock", "dummy")
        for word in forbidden:
            assert word not in js, f"discuss.js にフィクスチャ/偽データ由来の語 {word!r} が含まれている"

    def test_app_js_calls_render_opening_only_after_placeholder(self):
        """app.js 側: プレースホルダを描画してから renderOpening を呼ぶ順序
        （取得できなければプレースホルダのまま残る）。"""
        js = _read(APP_JS)
        block = _extract_function_body(js, "function renderMaterialRegion() {")
        idx_placeholder = block.index("renderDiscussPlaceholder()")
        idx_opening = block.index("window.Discuss.renderOpening(body, state.courseId)")
        assert idx_placeholder < idx_opening


class TestLandingScreen:
    """設計 §3.5: consolidation。討議終了時の軽量モーダル。スキップ可・既定オン。"""

    def test_three_section_headings_present(self):
        js = _read(DISCUSS_JS)
        assert "今日話した内容を地図に置く" in js
        assert "理解の確認" in js
        assert "このトピックで続きを学ぶ" in js

    def test_empty_candidates_fact_sentence(self):
        js = _read(DISCUSS_JS)
        assert "今回の対話からの候補はありません" in js
        assert "わたしの地図" in js

    def test_recon_probe_section_hidden_when_no_item(self):
        """再構成プローブは「あれば」1問。無ければ区画ごと非表示。"""
        js = _read(DISCUSS_JS)
        block = _extract_function_body(
            js, "function buildLandingBodyHtml(tensionItems, anchorItems, reconItem) {"
        )
        assert "if (reconItem) {" in block

    def test_skip_button_present_and_always_available(self):
        js = _read(DISCUSS_JS)
        assert "discuss-landing-skip-btn" in js
        assert "スキップ" in js
        assert "スキップしても、ここまでの記録は残ります" in js

    def test_confirm_dismiss_actions_for_tension_and_anchor_cards(self):
        js = _read(DISCUSS_JS)
        assert "data-discuss-tension-confirm" in js
        assert "data-discuss-tension-dismiss" in js
        assert "data-discuss-anchor-confirm" in js
        assert "data-discuss-anchor-dismiss" in js

    def test_maybe_show_landing_suppresses_when_no_turns_or_recently_shown(self):
        js = _read(DISCUSS_JS)
        block = _extract_function_body(js, "async function maybeShowLanding(courseId, reason) {")
        assert "if (turnCount <= 0) return;" in block
        assert "SUPPRESS_MS" in block

    def test_inactivity_timeout_constant_is_fifteen_minutes(self):
        js = _read(DISCUSS_JS)
        assert "var INACTIVITY_MS = 15 * 60 * 1000;" in js

    def test_notify_activity_arms_timer_without_polling(self):
        js = _read(DISCUSS_JS)
        block = _extract_function_body(js, "function notifyActivity() {")
        assert "armInactivityTimer" in block
        # ポーリング禁止（setInterval は使わない。setTimeout の張り直しのみ）
        assert "setInterval" not in js

    def test_continue_learning_button_is_informational_not_automatic(self):
        """「このトピックで続きを学ぶ」は情報的ボタンで自動遷移しない。"""
        js = _read(DISCUSS_JS)
        block = _extract_function_body(js, "function bindLandingContentEvents(root, reconItem) {")
        assert "discuss-landing-continue-btn" in block
        assert "discussReturnToSequential" in block


class TestNoNumericScoresOrHypeInDiscussJs:
    """DM6: 数値・件数・網羅率を学習者に見せない。煽り文句・ゲーミフィケーションもしない。"""

    FORBIDDEN_SUBSTRINGS = ("confidence", "スコア", "達成率", "踏破", "ランキング", "%")

    def test_no_forbidden_numeric_or_hype_tokens(self):
        js = _read(DISCUSS_JS)
        for token in self.FORBIDDEN_SUBSTRINGS:
            assert token not in js, f"discuss.js に禁止トークン {token!r} が含まれている"


class TestBranchChipsDiscussOnly:
    """設計 §3.4: 分岐チップ（深掘り／横展開）は discuss モードの応答直後にのみ出す。"""

    def test_branch_chips_defined_in_discuss_js(self):
        js = _read(DISCUSS_JS)
        assert "🔎 深掘り" in js
        assert "🧭 横展開" in js
        assert "discuss-branch-chips" in js

    def test_app_js_gates_branch_chips_behind_discuss_mode(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function renderAiContent(text, msg)")
        idx = block.index("window.Discuss.renderBranchChips()")
        # 直前の行が isDiscussMode() ガードであること
        preceding = block[:idx]
        guard_idx = preceding.rindex("if (isDiscussMode()")
        assert idx - guard_idx < 120

    def test_branch_chips_use_generic_suggest_btn_wiring(self):
        """discuss.js は自前のチャット区画バインディングを増やさず、既存の
        .suggest-btn 汎用配線（app.js の renderChat 内）に相乗りする。"""
        js = _read(DISCUSS_JS)
        block = _extract_function_body(js, "function renderBranchChips() {")
        assert "suggest-btn" in block
        assert "data-suggest=" in block


class TestActivityAndTriggerWiring:
    """討議終了トリガー3つの配線: ① 明示終了 ② discuss→通常トピック切替 ③ 無活動タイムアウト。"""

    def test_trigger_one_explicit_end_button_wired(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function initDiscussUI() {")
        assert "discuss-end-btn" in block
        assert "maybeShowLanding(state.courseId, \"explicit\")" in block

    def test_trigger_two_topic_switch_hook_in_select_topic(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "async function selectTopic(topicId, opts) {")
        assert "_discussLeaving" in block
        assert 'maybeShowLanding(_discussLeavingCourseId, "topic_switch")' in block
        # 遷移自体はブロックしない: フックはトピック切替が完了した後（関数末尾）に呼ぶ
        idx_set = block.index("state.currentTopicId = topicId;")
        idx_hook = block.index('maybeShowLanding(_discussLeavingCourseId, "topic_switch")')
        assert idx_set < idx_hook

    def test_trigger_three_notify_activity_on_send_success(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "async function sendMessage(text, actionPayload) {")
        assert "window.Discuss.notifyActivity()" in block
        idx_ok = block.index("if (res.ok) {")
        idx_notify = block.index("window.Discuss.notifyActivity()")
        assert idx_ok < idx_notify

    def test_end_button_present_in_html(self):
        html = _read(INDEX_HTML)
        assert 'id="discuss-end-btn"' in html
        assert "議論を終える" in html


class TestIndexHtmlWiring:
    def test_discuss_js_script_tag_present(self):
        html = _read(INDEX_HTML)
        assert '<script src="/js/discuss.js"></script>' in html
        # reconstruction.js より後に読み込む（既存の並び順を踏襲）
        idx_recon = html.index('<script src="/js/reconstruction.js"></script>')
        idx_discuss = html.index('<script src="/js/discuss.js"></script>')
        assert idx_recon < idx_discuss

    def test_landing_modal_root_present_and_hidden_by_default(self):
        html = _read(INDEX_HTML)
        assert 'id="discuss-landing-region"' in html
        block = html[html.index('id="discuss-landing-region"') - 80: html.index('id="discuss-landing-region"') + 120]
        assert "hidden" in block


class TestStylesCssPhase2:
    def test_phase2_css_classes_defined(self):
        css = _read(STYLES_CSS)
        for selector in (
            ".discuss-opening",
            ".discuss-section",
            ".discuss-chip",
            ".discuss-backbone-list",
            ".discuss-backbone-node",
            ".discuss-branch-chips",
            ".discuss-end-btn",
            ".discuss-landing-overlay",
            ".discuss-landing-panel",
            ".discuss-landing-card",
            ".discuss-landing-skip-btn",
        ):
            assert selector in css, f"missing CSS selector: {selector}"


class TestNoDetourOrShameVocabInDiscussJs:
    """discuss.js 全体に「寄り道」「疑え」という語が一切含まれない。"""

    def test_no_detour_word(self):
        js = _read(DISCUSS_JS)
        assert "寄り道" not in js

    def test_no_doubt_imperative_word(self):
        js = _read(DISCUSS_JS)
        assert "疑え" not in js
