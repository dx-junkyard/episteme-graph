"""discuss モード（「論文と話す」, docs/features/discussion_mode_design.md §6.2 Phase 1）の
フロントエンド実装（frontend/public/js/app.js, index.html, styles.css）に対する
静的ガードレール。test_help_usage_ui_static.py / test_content_grounding.py と同じ流儀で、
ソース文字列の部分一致・関数本体の抽出検査のみを行う（実サーバ・実DOM不使用）。

検証観点:
1. `_discussion` 予約疑似トピックで送信するとき、on_path/explore の既定判定より
   優先して `intent_mode: "discuss"` と `discuss_scope` を付与する配線がある
   （バックエンド契約: POST .../topics/_discussion/chat）。
2. サイドバー二枚看板（「順番に学ぶ」/「この論文と議論する」）が同格（同じCSSクラス）
   で存在し、ナビゲーションに配線されている。
3. スコープ2段切替の2ラベルが存在し、値がバックエンド契約
   （course_sources / all_visible）と一致する。
4. 「もっと自由に話す」常設リンクはサイドバー二枚看板と完全重複のため削除済み
   （学習UI再編 docs/features/learning_ui_inspect_hover_design.md §3.5）。discuss への
   入口は二枚看板に一本化されており、free-link の痕跡が HTML/JS/CSS に残っていない。
5. discuss 関連 UI 文言に「寄り道」という語が一切含まれない（DM5）。
6. discuss モードでは `fetchTopicMaterial` を呼ばない（予約トピックは実在しない）。
7. モードバーは通常状態（on-path）では表示されない（学習UI再編 §3.4 静音化）。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP_JS = ROOT / "frontend" / "public" / "js" / "app.js"
INDEX_HTML = ROOT / "frontend" / "public" / "index.html"
STYLES_CSS = ROOT / "frontend" / "public" / "css" / "styles.css"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _extract_function_body(src: str, signature: str) -> str:
    """`signature`（例: "function foo(...) {"）から対応する閉じ `}` までを、
    素朴な波括弧カウントで抽出する。対象関数の実装は文字列リテラル中に
    `{`/`}` を含まない前提（本ファイルが検査する discuss 関連関数はすべて該当）。"""
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


def _all_indices(haystack: str, needle: str):
    idx = haystack.find(needle)
    while idx != -1:
        yield idx
        idx = haystack.find(needle, idx + 1)


class TestDiscussSendWiring:
    """バックエンド契約: POST /learning/courses/{courseId}/topics/_discussion/chat
    に `intent_mode: "discuss"` と `discuss_scope` を付与して送る。"""

    def test_discuss_topic_id_constant(self):
        js = _read(APP_JS)
        assert 'var DISCUSS_TOPIC_ID = "_discussion";' in js

    def test_send_with_injects_discuss_intent_and_scope(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function sendWith(mode) {")
        assert "isDiscussMode()" in block
        assert 'payload.intent_mode = "discuss";' in block
        assert 'payload.discuss_scope = state.discussScope || "course_sources";' in block
        # 既存の on_path/explore 判定（mode 引数）よりあとに上書きすることで優先する
        idx_initial = block.index("var payload = { intent_mode: mode };")
        idx_override = block.index('payload.intent_mode = "discuss";')
        assert idx_initial < idx_override

    def test_replace_message_id_still_works_in_discuss(self):
        """機能3（書き直し）の _replace_message_id 配線は discuss でも生きること。"""
        js = _read(APP_JS)
        block = _extract_function_body(js, "function sendWith(mode) {")
        assert "payload._replace_message_id = replaceId;" in block

    def test_send_current_keeps_existing_on_path_explore_judgement(self):
        """既存の Enter/送信ボタン経路 (sendCurrent) 自体は変更しない
        （discuss への優先上書きは sendWith 内部で行う）。"""
        js = _read(APP_JS)
        assert 'function sendCurrent() { sendWith(Session.inDetour() ? "explore" : "on_path"); }' in js

    def test_select_topic_skips_material_fetch_for_discuss(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "async function selectTopic(topicId, opts) {")
        assert "DISCUSS_TOPIC_ID" in block
        branch_start = block.index("if (topicId === DISCUSS_TOPIC_ID) {")
        branch_else_start = block.index("} else {", branch_start)
        discuss_branch = block[branch_start:branch_else_start]
        assert "fetchTopicMaterial" not in discuss_branch
        assert "loadChatHistory" in discuss_branch

    def test_enter_discuss_mode_reuses_select_topic(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function enterDiscussMode() {")
        assert "selectTopic(DISCUSS_TOPIC_ID)" in block


class TestTwoBannerEntry:
    """設計 §3.2: 同じ視覚的重みの二枚看板。既定選択なし。"""

    def test_two_buttons_present_with_expected_labels(self):
        js = _read(APP_JS)
        assert "📘 順番に学ぶ" in js
        assert "🗣 この論文と議論する" in js

    def test_buttons_share_same_css_class_equal_weight(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function renderSidebar() {")
        assert block.count('class="discuss-mode-btn') >= 2
        # どちらかを primary 色で強調しない（設計 §3.2「既定選択なし」）
        assert "primary" not in block

    def test_buttons_wired_to_navigation(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function renderSidebar() {")
        assert "goToSequentialLearning" in block
        assert "enterDiscussMode()" in block

    def test_sequential_button_is_noop_when_already_sequential(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function goToSequentialLearning() {")
        assert "isDiscussMode()" in block


class TestScopeToggle:
    """設計 §3.4 スコープ2段切替。値はバックエンド契約
    (course_sources 既定 / all_visible) と一致させる。"""

    def test_two_scope_labels_present_in_html(self):
        html = _read(INDEX_HTML)
        assert "このコースのソース論文" in html
        assert "閲覧できる周辺資料まで" in html

    def test_scope_values_match_backend_contract(self):
        html = _read(INDEX_HTML)
        assert 'data-discuss-scope="course_sources"' in html
        assert 'data-discuss-scope="all_visible"' in html

    def test_scope_selection_state_default_and_persistence(self):
        js = _read(APP_JS)
        assert 'discussScope: "course_sources", // "course_sources" | "all_visible"' in js
        block = _extract_function_body(js, "function initDiscussUI() {")
        assert 'state.discussScope = this.getAttribute("data-discuss-scope") || "course_sources";' in block

    def test_scope_toggle_shows_current_selection(self):
        """選択状態そのものが出所の正直さの UI になる（DM1）: active クラスで常時視認できる。"""
        js = _read(APP_JS)
        block = _extract_function_body(js, "function renderDiscussBar() {")
        assert 'classList.toggle("active"' in block


class TestFreeLinkRemovedAndModeBar:
    """重複導線の削除（設計 §3.5）: 「もっと自由に話す」はサイドバー二枚看板と
    完全重複していたため削除済み。discuss への入口は二枚看板に一本化されている
    （TestTwoBannerEntry が唯一の入口であることを検査する）。"""

    def test_free_link_absent_from_html(self):
        html = _read(INDEX_HTML)
        assert 'id="discuss-free-link-btn"' not in html
        assert 'id="discuss-free-link-row"' not in html
        assert "もっと自由に話す" not in html

    def test_free_link_absent_from_js(self):
        js = _read(APP_JS)
        assert "discuss-free-link-btn" not in js
        assert "discuss-free-link-row" not in js
        assert "freeLinkBtn" not in js
        assert "freeLinkRow" not in js
        assert "showFreeLink" not in js

    def test_free_link_absent_from_css(self):
        css = _read(STYLES_CSS)
        assert "discuss-free-link" not in css

    def test_render_discuss_bar_no_longer_touches_free_link(self):
        """renderDiscussBar はスコープトグルの active 表示のみを担う。"""
        js = _read(APP_JS)
        block = _extract_function_body(js, "function renderDiscussBar() {")
        assert "freeLinkRow" not in block
        assert 'classList.toggle("active"' in block

    def test_mode_bar_discuss_branch_is_neutral_and_terse(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function renderModeBar() {")
        idx = block.index("if (isDiscussMode()) {")
        end = block.index("return;", idx) + len("return;")
        discuss_branch = block[idx:end]
        assert "論文と議論中" in discuss_branch
        assert "寄り道" not in discuss_branch
        # 復帰督促ボタン（本筋へ戻る）は discuss では出さない
        assert "mb-return" not in discuss_branch

    def test_mode_bar_on_path_is_hidden(self):
        """§3.4 モードバー静音化: 通常状態（on-path）ではバー自体を表示しない
        （discuss/detour のときだけ「特殊状態」として出す）。"""
        js = _read(APP_JS)
        block = _extract_function_body(js, "function renderModeBar() {")
        idx = block.rindex("} else {")
        else_branch = block[idx:]
        assert "bar.hidden = true;" in else_branch
        # 旧「本筋に沿って学習中」の常設表示文言は残っていない
        assert "本筋に沿って学習中" not in js


class TestDiscussPlaceholderMaterialRegion:
    def test_placeholder_wired_when_no_material_chunks(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function renderMaterialRegion() {")
        assert "isDiscussMode()" in block
        assert "renderDiscussPlaceholder()" in block

    def test_placeholder_copy_has_no_hype_or_numbers(self):
        """DM6: 煽り文句・数値・「自由に何でも」的な誇大表現を禁止。"""
        js = _read(APP_JS)
        block = _extract_function_body(js, "function renderDiscussPlaceholder() {")
        for forbidden in ("何でも", "自由自在", "%", "件"):
            assert forbidden not in block


class TestNoDetourVocabInDiscussUi:
    """DM5: 「寄り道」という語を discuss 関連 UI 文言から追放する
    （既存 explore の内部語彙・UI 自体は変更しない）。"""

    DISCUSS_JS_FUNCTIONS = [
        "function renderDiscussPlaceholder() {",
        "function _renderDiscussInitialGreeting() {",
        "function renderDiscussBar() {",
        "function initDiscussUI() {",
        "function enterDiscussMode() {",
        "function goToSequentialLearning() {",
    ]

    DISCUSS_MARKERS = [
        "discuss-mode-switch",
        "discuss-mode-btn",
        "discuss-bar",
        "discuss-scope",
        "discuss-placeholder",
    ]

    def test_discuss_only_functions_do_not_mention_detour(self):
        js = _read(APP_JS)
        for fn in self.DISCUSS_JS_FUNCTIONS:
            block = _extract_function_body(js, fn)
            assert "寄り道" not in block, f"{fn} に「寄り道」が含まれている"

    def test_no_detour_word_on_lines_touching_discuss_markers_in_js(self):
        js = _read(APP_JS)
        for marker in self.DISCUSS_MARKERS:
            for idx in _all_indices(js, marker):
                line_start = js.rfind("\n", 0, idx) + 1
                nl = js.find("\n", idx)
                line_end = nl if nl != -1 else len(js)
                line = js[line_start:line_end]
                assert "寄り道" not in line, f"'{marker}' 近傍の行に「寄り道」: {line!r}"

    def test_no_detour_word_on_lines_touching_discuss_markers_in_html(self):
        html = _read(INDEX_HTML)
        for marker in self.DISCUSS_MARKERS:
            for idx in _all_indices(html, marker):
                line_start = html.rfind("\n", 0, idx) + 1
                nl = html.find("\n", idx)
                line_end = nl if nl != -1 else len(html)
                line = html[line_start:line_end]
                assert "寄り道" not in line, f"'{marker}' 近傍の行に「寄り道」: {line!r}"

    def test_no_detour_word_on_lines_touching_discuss_markers_in_css(self):
        css = _read(STYLES_CSS)
        for marker in self.DISCUSS_MARKERS:
            for idx in _all_indices(css, marker):
                line_start = css.rfind("\n", 0, idx) + 1
                nl = css.find("\n", idx)
                line_end = nl if nl != -1 else len(css)
                line = css[line_start:line_end]
                assert "寄り道" not in line, f"'{marker}' 近傍の行に「寄り道」: {line!r}"


class TestDiscussCssPresent:
    def test_discuss_css_classes_defined(self):
        css = _read(STYLES_CSS)
        for selector in (
            ".discuss-mode-switch",
            ".discuss-mode-btn",
            ".discuss-mode-btn.active",
            ".mode-bar.discuss",
            ".discuss-bar",
            ".discuss-scope-toggle",
            ".discuss-scope-opt",
            ".discuss-placeholder",
        ):
            assert selector in css, f"missing CSS selector: {selector}"


class TestExistingUiUnchanged:
    """explore（寄り道）・casual 音声 UI の既存動作を変えないことの回帰確認。"""

    def test_detour_mode_bar_branch_still_present(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function renderModeBar() {")
        assert "🔍 寄り道中" in block
        assert "本筋へ戻る" in block

    def test_casual_voice_mode_untouched_marker_present(self):
        js = _read(APP_JS)
        assert "toggleVoiceMode" in js
        assert 'document.getElementById("voice-mode-btn")' in js
