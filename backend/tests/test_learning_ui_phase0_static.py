"""学習画面 UI 再編 Phase 0（`docs/features/learning_ui_inspect_hover_design.md` §13）の
フロントエンド静的ガードレール。

Phase 0 のスコープは純フロント + テスト更新のみ（「？」のトップバー移設・lecture-toggle
移設は Phase 1 のため対象外）:

1. §3.3 composer 整理: 送信のみがプライマリ（青塗り）。「質問」→「送信」改称。
   `#chat-input` の placeholder 変更。`#voice-mode-btn` のラベル改善（ID 維持）。
2. §3.4 モードバー静音化: on-path では `#mode-bar` を表示しない。教材フッタの
   「本筋：〜」表示（旧 `#material-here`）を削除。
3. §3.5 重複導線の削除は `test_discuss_ui_static.py::TestFreeLinkRemovedAndModeBar` が担う
   （discuss 固有の検査と同居させたほうが自然なため、ここでは重複させない）。
4. §3.6 右パネル降格: 「質疑応答履歴を削除」を `<details>` 配下へ。
   `#chat-clear-btn` の ID・機能・確認ダイアログは不変。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP_JS = ROOT / "frontend" / "public" / "js" / "app.js"
INDEX_HTML = ROOT / "frontend" / "public" / "index.html"
STYLES_CSS = ROOT / "frontend" / "public" / "css" / "styles.css"
DISCUSS_JS = ROOT / "frontend" / "public" / "js" / "discuss.js"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _extract_function_body(src: str, signature: str) -> str:
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


class TestSendIsOnlyPrimary:
    """§3.3: 青塗り（プライマリ）は #send-btn のみ。"""

    def test_send_btn_labeled_and_primary(self):
        html = _read(INDEX_HTML)
        assert 'id="send-btn"' in html
        assert ">送信<" in html
        assert 'class="primary"' in html

    def test_help_and_voice_buttons_are_not_labeled_question(self):
        html = _read(INDEX_HTML)
        # 旧ラベル「質問」がボタン文言として残っていないこと（send-btn 以外）。
        assert ">質問<" not in html

    def test_css_primary_rule_scoped_to_send_btn(self):
        css = _read(STYLES_CSS)
        assert "#send-btn" in css
        assert ".ia button.primary" in css
        # .ia button 自体の既定はセカンダリ（背景透明）であること。
        idx = css.index(".ia button {")
        block = css[idx:css.index("}", idx) + 1]
        assert "background: transparent;" in block

    def test_help_usage_btn_css_is_secondary(self):
        css = _read(STYLES_CSS)
        idx = css.index(".help-usage-btn {")
        block = css[idx:css.index("}", idx) + 1]
        assert "background: transparent;" in block
        assert "border: 1px solid" in block

    def test_voice_mode_btn_css_is_secondary(self):
        css = _read(STYLES_CSS)
        idx = css.index(".voice-mode-btn {")
        block = css[idx:css.index("}", idx) + 1]
        assert "background: transparent;" in block
        assert "border: 1px solid" in block


class TestComposerLabelsAndPlaceholder:
    def test_chat_input_placeholder_updated(self):
        html = _read(INDEX_HTML)
        assert 'placeholder="質問や考えを入力…（Enterで送信）"' in html

    def test_set_chat_enabled_placeholder_matches(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function setChatEnabled(enabled) {")
        assert "質問や考えを入力…（Enterで送信）" in block

    def test_voice_mode_btn_relabeled_with_id_preserved(self):
        html = _read(INDEX_HTML)
        assert 'id="voice-mode-btn"' in html
        assert "🎤 音声で話す" in html
        assert 'aria-label="音声で話す"' in html
        # title（ハンズフリー会話の説明）は維持される。
        assert 'title="気軽に話せる先生とハンズフリーで会話"' in html

    def test_help_usage_btn_moved_to_topbar_in_phase1(self):
        """help-usage-btn の移設は Phase 1（§4）で実施済み: ID・文言は不変のまま
        composer から退去し、トップバーへ移った（置き場所の検査は
        test_learning_ui_phase1_static.py 側が担う）。"""
        html = _read(INDEX_HTML)
        assert 'id="help-usage-btn"' in html
        assert "❓ 使い方" in html
        ia_start = html.index('<div class="ia">')
        voice_panel_start = html.index('<div class="voice-panel"', ia_start)
        composer_block = html[ia_start:voice_panel_start]
        assert "help-usage-btn" not in composer_block


class TestMaterialHereRemoved:
    """§3.4: 教材フッタの「本筋：〜」常設表示（旧 #material-here）を削除する。"""

    def test_material_here_absent_from_html(self):
        html = _read(INDEX_HTML)
        assert "material-here" not in html

    def test_material_here_dom_lookup_removed_from_js(self):
        """DOM 要素は削除済み。app.js 側のコメントで旧要素名（historical note）に
        触れることは許容するが、実際の getElementById 参照・書き込みは残らないこと。"""
        js = _read(APP_JS)
        assert 'document.getElementById("material-here")' not in js
        assert "here.textContent" not in js

    def test_material_here_absent_from_css(self):
        css = _read(STYLES_CSS)
        assert "material-here" not in css

    def test_next_topic_btn_still_present(self):
        """「確認して次へ」ボタン自体は不変。"""
        html = _read(INDEX_HTML)
        assert 'id="next-topic-btn"' in html
        assert "確認して次へ" in html

    def test_discuss_js_uses_data_attribute_instead_of_material_here(self):
        """discuss.js の stillInDiscussContext() は #material-here 廃止に伴い、
        #material-body の data-discuss-active 属性を見る不可視の合図に切り替える
        （表示テキストへ回帰しない）。"""
        js = _read(DISCUSS_JS)
        block = _extract_function_body(js, "function stillInDiscussContext() {")
        assert "material-here" not in block
        assert "data-discuss-active" in block or "dataset.discussActive" in block

    def test_app_js_sets_discuss_active_dataset(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function renderMaterialRegion() {")
        assert "dataset.discussActive" in block


class TestModeBarOnPathHidden:
    """§3.4: 通常状態（on-path）ではモードバー自体を出さない。"""

    def test_on_path_branch_hides_bar(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function renderModeBar() {")
        else_idx = block.rindex("} else {")
        else_branch = block[else_idx:]
        assert "bar.hidden = true;" in else_branch

    def test_no_static_on_path_label_text(self):
        js = _read(APP_JS)
        assert "本筋に沿って学習中" not in js

    def test_discuss_and_detour_branches_still_show_bar(self):
        """discuss/detour では引き続き表示する（回帰確認）。Phase 1（レクチャー外科手術
        案①・§15）で lecture-paused ブランチが追加されたため、表示するブランチは
        discuss/detour/lecture-paused の3つになった。"""
        js = _read(APP_JS)
        block = _extract_function_body(js, "function renderModeBar() {")
        assert block.count("bar.hidden = false;") == 3


class TestRightPanelCollapse:
    """§3.6: 「質疑応答履歴を削除」を折りたたみ配下へ降格。機能・確認ダイアログは不変。"""

    def test_danger_zone_wrapped_in_details(self):
        html = _read(INDEX_HTML)
        rp_idx = html.index('<div class="rp">')
        window = html[rp_idx:rp_idx + 2000]
        assert "<details" in window
        assert "<summary" in window
        assert "このトピックの操作" in window
        # <details> の中に既存の .rp-danger-zone / #chat-clear-btn がそのまま残っていること。
        details_idx = window.index("<details")
        assert "rp-danger-zone" in window[details_idx:]
        assert "chat-clear-btn" in window[details_idx:]

    def test_chat_clear_btn_id_and_confirm_unchanged(self):
        html = _read(INDEX_HTML)
        assert 'id="chat-clear-btn"' in html
        assert "質疑応答履歴を削除" in html
        js = _read(APP_JS)
        assert "async function clearChatHistory() {" in js
        block = _extract_function_body(js, "async function clearChatHistory() {")
        assert "confirm(" in block

    def test_rp_collapse_css_defined(self):
        css = _read(STYLES_CSS)
        assert ".rp-collapse" in css
        assert ".rp-danger-zone" in css  # 既存クラスは維持（テスト test_course_selector_ui.py が pin）
