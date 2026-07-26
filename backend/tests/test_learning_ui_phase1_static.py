"""学習画面 UI 再編 Phase 1（`docs/features/learning_ui_inspect_hover_design.md` §13）の
フロントエンド静的ガードレール。

Phase 1 のスコープ:

1. §3.2 教材区画ヘッダの新設: `#material-region` 先頭に「教材 · トピック名」+
   提示形式セグメント [📖 読む|🎙 講義] を持つ。`lecture-toggle`/`lecture-toggle-hint`
   はここへ移設済み（ID は維持）。discuss モード中はセグメントを隠す。
2. §4 冒頭: `#help-usage-btn` をトップバーへ移設（ID・typed action 配線・外側クリック
   許可リスト記載は不変。インスペクト・モード化自体は Phase 2 でありここでは行わない）。
3. §15（案①・裁定済み）: `.mn.lecture-on` の非表示対象は `#material-region` のみに
   縮小し、下段（`#chat-area`・composer・`#split-handle`・`#mode-bar`）は講義中も
   生かす。第2 composer（`lecture-chat-popup`）は HTML/CSS/JS とも廃止済み。
   講義中の質問は通常 composer に一本化され、一時停止中はモードバーに
   `lecture-paused` ブランチで再開導線を出す。全画面ボタン（既定 OFF・講義中のみ表示）
   を教材ヘッダに追加する。
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


class TestMaterialHeaderAndFormatSegment:
    """§3.2: 教材区画ヘッダ（②提示形式）の新設。"""

    def test_material_header_exists_inside_material_region(self):
        html = _read(INDEX_HTML)
        region_idx = html.index('<div class="material-region" id="material-region">')
        body_idx = html.index('<div class="material-body" id="material-body">', region_idx)
        header_block = html[region_idx:body_idx]
        assert 'class="material-header" id="material-header"' in header_block
        assert 'id="material-header-title"' in header_block

    def test_format_segment_has_read_and_lecture_options(self):
        html = _read(INDEX_HTML)
        idx = html.index('id="material-format-toggle"')
        window = html[idx:idx + 800]
        assert 'id="material-format-read"' in window
        assert 'id="lecture-toggle"' in window  # ID は維持（§3.2）
        assert "📖 読む" in window
        assert "🎙 講義" in window

    def test_lecture_toggle_hint_moved_into_format_toggle(self):
        """lecture-toggle-hint は composer の lecture-toggle-wrap ではなく、
        教材ヘッダの material-format-toggle 内に移設されている。"""
        html = _read(INDEX_HTML)
        idx = html.index('id="material-format-toggle"')
        end = html.index("</div>", idx)
        window = html[idx:end]
        assert 'id="lecture-toggle-hint"' in window
        assert "lecture-toggle-wrap" not in html

    def test_composer_no_longer_contains_lecture_toggle(self):
        html = _read(INDEX_HTML)
        ia_start = html.index('<div class="ia">')
        ia_end = html.index("</div>", ia_start)
        composer_block = html[ia_start:ia_end]
        assert "lecture-toggle" not in composer_block

    def test_update_material_header_function_exists_and_used_by_render_material_region(self):
        js = _read(APP_JS)
        assert "function updateMaterialHeader() {" in js
        block = _extract_function_body(js, "function renderMaterialRegion() {")
        assert "updateMaterialHeader();" in block

    def test_update_material_header_sets_title_from_current_topic(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function updateMaterialHeader() {")
        assert "material-header-title" in block
        assert "getCurrentTopic()" in block
        assert "教材" in block

    def test_update_material_header_hides_segment_in_discuss_mode(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function updateMaterialHeader() {")
        assert "material-format-toggle" in block
        assert "isDiscussMode()" in block
        assert ".hidden = isDiscussMode()" in block


class TestHelpUsageBtnInTopbar:
    """§4 冒頭: 「？」のトップバー移設（typed action のまま。インスペクト化は Phase 2）。"""

    def test_help_usage_btn_in_topbar(self):
        html = _read(INDEX_HTML)
        topbar_start = html.index('<div class="topbar-r">')
        topbar_end = html.index("</div>", html.index("id=\"logout-btn\"", topbar_start))
        topbar_block = html[topbar_start:topbar_end]
        assert 'id="help-usage-btn"' in topbar_block

    def test_help_usage_btn_not_in_composer(self):
        html = _read(INDEX_HTML)
        ia_start = html.index('<div class="ia">')
        voice_panel_start = html.index('<div class="voice-panel"', ia_start)
        composer_block = html[ia_start:voice_panel_start]
        assert "help-usage-btn" not in composer_block

    def test_help_usage_btn_id_and_label_unchanged(self):
        html = _read(INDEX_HTML)
        assert 'id="help-usage-btn"' in html
        assert "❓ 使い方" in html

    def test_help_usage_btn_still_typed_action_wired(self):
        """typed action（support_action="usage_help"）の配線自体は不変。

        Phase 1 時点ではボタン押下が直接この payload を送っていたが、Phase 2
        （docs/features/learning_ui_inspect_hover_design.md §4、
        test_learning_ui_phase2_static.py で検証）でボタン押下は使い方インスペクト・
        モードの ON/OFF トグルに置き換わり、typed action の送信は
        `sendUsageHelpMessage()`（composer/音声の発話経路から呼ばれる）へ移設された。
        ここでは「配線そのものがコードベースから消えていない」ことのみを確認する。"""
        js = _read(APP_JS)
        assert 'function sendUsageHelpMessage(typedText) {' in js
        start = js.index("function sendUsageHelpMessage(typedText) {")
        end = js.index("\n  }", start)
        block = js[start:end]
        assert 'support_action: "usage_help"' in block

    def test_help_usage_btn_still_in_outside_click_allowlist(self):
        """機能2の自動畳み（collapseMaterialForChat）の外側クリック判定許可リストに
        引き続き含まれる（トップバーへ移設後も送信直後の誤復元を防ぐため必須）。"""
        js = _read(APP_JS)
        idx = js.index("if (!_matCollapse.active) return;")
        window = js[idx:idx + 400]
        assert "#help-usage-btn" in window
        assert "#lecture-chat-popup" not in window


class TestLectureSurgeryPlanA:
    """§15（案①・裁定済み）: 上段のみスライド化。下段（会話・composer）は講義中も生かす。"""

    def test_lecture_on_hides_only_material_region(self):
        css = _read(STYLES_CSS)
        idx = css.index(".mn.lecture-on #material-region {")
        # 次の独立したルールブロック群（全画面セクション）が始まる直前までを走査する。
        end = css.index("全画面表示", idx)
        window = css[idx:end]
        for selector in (
            "#chat-area", "#chat-input", "#send-btn", "#voice-mode-btn",
            "#split-handle", "#mode-bar",
        ):
            assert selector not in window, f"{selector} が .mn.lecture-on で非表示のままです"

    def test_lecture_on_still_shows_slide_stage(self):
        css = _read(STYLES_CSS)
        assert ".mn.lecture-on .lecture-slide-stage {" in css
        idx = css.index(".mn.lecture-on .lecture-slide-stage {")
        block = css[idx:css.index("}", idx) + 1]
        assert "display: flex !important;" in block

    def test_lecture_chat_popup_absent_from_html(self):
        html = _read(INDEX_HTML)
        assert "lecture-chat-popup" not in html
        assert "lecture-chat-input" not in html
        assert "lecture-chat-mic" not in html
        assert "lecture-chat-resume" not in html
        assert "lecture-chat-send" not in html
        assert "lecture-chat-close" not in html
        assert "lecture-chat-messages" not in html

    def test_lecture_chat_popup_absent_from_css(self):
        css = _read(STYLES_CSS)
        assert ".lecture-chat-popup" not in css
        assert ".lecture-chat-header" not in css
        assert ".lecture-chat-messages" not in css
        assert ".lecture-chat-input-area" not in css
        assert ".lecture-mic-btn" not in css
        assert ".lecture-chat-resume-btn" not in css

    def test_lecture_chat_popup_absent_from_js(self):
        js = _read(APP_JS)
        for fn in (
            "function openInterruptChat(",
            "function closeInterruptChat(",
            "function renderInterruptMessages(",
            "function sendInterruptMessage(",
            "function toggleVoiceInput(",
        ):
            assert fn not in js

    def test_mic_pulse_keyframe_retained_for_voice_mode_btn(self):
        """lecture-chat-popup 廃止後も、voice-mode-btn.active が使う @keyframes
        mic-pulse は残す（共有アニメーションのため削除しない）。"""
        css = _read(STYLES_CSS)
        assert "@keyframes mic-pulse {" in css
        idx = css.index(".voice-mode-btn.active {")
        block = css[idx:css.index("}", idx) + 1]
        assert "mic-pulse" in block


class TestLecturePausedModeBar:
    """§15: 講義を質問のため一時停止中はモードバーに再開導線を出す。"""

    def test_render_mode_bar_has_lecture_paused_branch(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function renderModeBar() {")
        assert "lectureState.pausedForQuestion" in block
        assert "mode-bar lecture-paused" in block
        assert "講義を一時停止中" in block
        assert "resumeLecture" in block

    def test_lecture_paused_branch_precedes_discuss_branch(self):
        """他の状態表示より優先して出す（§15 の意図）。"""
        js = _read(APP_JS)
        block = _extract_function_body(js, "function renderModeBar() {")
        paused_idx = block.index("lectureState.pausedForQuestion")
        discuss_idx = block.index("isDiscussMode()")
        assert paused_idx < discuss_idx

    def test_css_defines_lecture_paused_style(self):
        css = _read(STYLES_CSS)
        assert ".mode-bar.lecture-paused" in css

    def test_resume_lecture_clears_paused_flag(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function resumeLecture() {")
        assert "lectureState.pausedForQuestion = false;" in block
        assert "startPlayback()" in block

    def test_send_message_pauses_active_lecture_playback(self):
        """composer から送信すると講義再生中なら自動一時停止する（旧 openInterruptChat
        の一時停止ロジックの流用）。"""
        js = _read(APP_JS)
        block = _extract_function_body(js, "async function sendMessage(text, actionPayload) {")
        assert "pauseLectureForQuestion();" in block
        assert "lectureState.active && lectureState.playing" in block

    def test_lecture_question_button_wired_to_focus_chat_input(self):
        js = _read(APP_JS)
        assert "focusChatForLectureQuestion" in js
        block = _extract_function_body(js, "function focusChatForLectureQuestion() {")
        assert "pauseLectureForQuestion();" in block
        assert 'getElementById("chat-input")' in block


class TestDiscussHidesFormatSegment:
    """discuss モード中（教材区画がプレースホルダ）はセグメントを非表示にする（§3.2）。"""

    def test_discuss_mode_hides_material_format_toggle(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function updateMaterialHeader() {")
        assert 'toggle.hidden = isDiscussMode()' in block


class TestFullscreenButton:
    """§3.2/§15: 教材ヘッダの全画面ボタン（既定 OFF・講義中のみ表示）。"""

    def test_fullscreen_button_present_and_hidden_by_default(self):
        html = _read(INDEX_HTML)
        idx = html.index('id="lecture-fullscreen-btn"')
        tag_start = html.rindex("<button", 0, idx)
        tag_end = html.index(">", idx)
        tag = html[tag_start:tag_end + 1]
        assert "hidden" in tag
        assert "全画面" in html[idx:idx + 60]

    def test_toggle_lecture_fullscreen_function_exists(self):
        js = _read(APP_JS)
        assert "function toggleLectureFullscreen() {" in js
        block = _extract_function_body(js, "function toggleLectureFullscreen() {")
        assert "lecture-fullscreen" in block

    def test_fullscreen_button_shown_only_while_lecture_active(self):
        """sync 関数が lectureState.active に応じて hidden を切り替える。"""
        js = _read(APP_JS)
        block = _extract_function_body(js, "function syncMaterialFormatToggle() {")
        assert "lecture-fullscreen-btn" in block
        assert "fsBtn.hidden = !lectureState.active;" in block

    def test_escape_key_clears_lecture_fullscreen(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function initLectureMode() {")
        assert '"Escape"' in block
        assert "lecture-fullscreen" in block

    def test_css_defines_fullscreen_takeover_rules(self):
        css = _read(STYLES_CSS)
        assert ".mn.lecture-on.lecture-fullscreen" in css


class TestVoiceModeBtnDisabledDuringLecture:
    """§3 の音声3分離: 講義中はハンズフリー音声（voice-mode-btn）を無効化する
    （非表示ではなく disabled — §15 で常時可視になったため）。"""

    def test_update_voice_mode_btn_for_lecture_exists(self):
        js = _read(APP_JS)
        assert "function updateVoiceModeBtnForLecture() {" in js
        block = _extract_function_body(js, "function updateVoiceModeBtnForLecture() {")
        assert "voiceBtn.disabled = true;" in block
        assert "voiceBtn.disabled = false;" in block

    def test_toggle_lecture_mode_and_deactivate_call_voice_gate(self):
        js = _read(APP_JS)
        toggle_block = _extract_function_body(js, "async function toggleLectureMode() {")
        assert "updateVoiceModeBtnForLecture();" in toggle_block
        deactivate_block = _extract_function_body(js, "function deactivateLecture() {")
        assert "updateVoiceModeBtnForLecture();" in deactivate_block
