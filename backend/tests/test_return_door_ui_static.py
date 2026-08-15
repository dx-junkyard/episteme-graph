"""帰還の扉（Phase 2 — 帰還の三段 v1）フロントエンドの静的ガードレール。

設計正本: docs/features/return_door_design.md（RD1〜RD5）。
test_personal_map_home_ui_static.py / test_my_records_ui_static.py と同じ方式
（Path 読み + 素の assert + re / 波括弧カウントによる関数本体抽出）で、フロントエンド
ソースの静的検証により受け入れ条件を固定する（実ブラウザ・実APIには依存しない）。

受け入れ条件との対応:
1. discuss.js: 「未来の自分への書き置き」「あなたの言葉」の逐語存在 / トレイが既定畳み
   （details に open 属性なし）/ 書き置きを促す確認ダイアログの不在（RD3）/
   トレイは words[].text の逐語のみを textContent で描画（RD1: AI応答を混ぜない）
2. app.js: #return-door の描画は textContent のみ（innerHTML 禁止, RD1）/
   経過日数語彙（「ぶりです」「日ぶり」「久しぶり」）の不在（RD2）/
   新規関数に setInterval なし（ポーリング禁止）/ 取得はコース読込につき1回 /
   ×はメモリ内フラグのみ（localStorage 非永続）
3. app.js: 欄外の印は map_excluded を除外 / 上限定数 MARGIN_MARKS_MAX = 12 /
   トグル状態は eg_margin_marks:<courseId>（精読モードと同型の許容例外・既定 ON）
4. UI アンカー4点セット（material.return-door: KNOWN 登録・UI_ANCHORS 値・
   マニュアル節 {#return-door} 実在・frontend の data-ui-anchor 値が KNOWN ⊆）
5. 新規 UI 文言に禁止語彙（踏破/達成率/ランキング/獲得/成長しました/おすすめ/スコア）なし
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from core.help_kb.ui_anchors import KNOWN_UI_ANCHOR_IDS, UI_ANCHORS  # noqa: E402

APP_JS = ROOT / "frontend" / "public" / "js" / "app.js"
DISCUSS_JS = ROOT / "frontend" / "public" / "js" / "discuss.js"
INDEX_HTML = ROOT / "frontend" / "public" / "index.html"
STYLES_CSS = ROOT / "frontend" / "public" / "css" / "styles.css"
STUDENT_MANUAL = ROOT / "docs" / "manual" / "student" / "02-student.md"

ANCHOR_ID = "material.return-door"

# 数値・進捗・ゲーミフィケーションを匂わせる禁止語彙
# （test_personal_map_home_ui_static.py の FORBIDDEN_WORDS と同一集合）。
FORBIDDEN_WORDS = ("踏破", "達成率", "ランキング", "獲得", "成長しました", "おすすめ", "スコア")

# RD2: 経過日数の語彙（「14日ぶりですね」等）を UI に出さない。
ELAPSED_DAYS_WORDS = ("ぶりです", "日ぶり", "久しぶり")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _extract_function_body(src: str, signature: str) -> str:
    """`signature` から対応する閉じ `}` までを波括弧カウントで抽出する
    （test_understanding_cycle_ui_static.py と同じ流儀）。"""
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


def _discuss_new_bodies() -> str:
    js = _read(DISCUSS_JS)
    return "\n".join(
        _extract_function_body(js, sig)
        for sig in (
            "function leaveNoteSectionHtml() {",
            "async function loadTodaysWordsOnce() {",
            "async function saveLeaveNote() {",
        )
    )


def _app_new_bodies() -> str:
    js = _read(APP_JS)
    return "\n".join(
        _extract_function_body(js, sig)
        for sig in (
            "function hideReturnDoor() {",
            "function renderReturnDoor(data) {",
            "async function loadReturnDoor() {",
            "function extractMarginMarks(traces) {",
            "function renderMarginMarks() {",
            "async function loadMarginMarks() {",
            "function initMarginMarks() {",
            "function showMarginMarkTip(dot, text) {",
            "function hideMarginMarkTip() {",
        )
    )


class TestLeaveNoteInDiscussJs:
    """書き置きの記入欄（LEAVE, 設計書 §2.1）と「今日のあなたの言葉」トレイ（§2.2）。"""

    def test_leave_note_heading_verbatim(self):
        assert "未来の自分への書き置き" in _read(DISCUSS_JS)

    def test_your_words_label_verbatim(self):
        """RD1: トレイの見出しに「あなたの言葉」ラベルを必ず表示する。"""
        block = _extract_function_body(_read(DISCUSS_JS), "function leaveNoteSectionHtml() {")
        assert "今日のあなたの言葉" in block
        assert "あなたの言葉（今日の発話の逐語）です。" in block

    def test_tray_collapsed_by_default(self):
        """トレイは既定畳み（details に open 属性を付けない）。"""
        block = _extract_function_body(_read(DISCUSS_JS), "function leaveNoteSectionHtml() {")
        assert '<details class="discuss-todays-words" id="discuss-todays-words">' in block
        assert "<details open" not in block

    def test_no_confirmation_dialog(self):
        """RD3: 書き置きを促す確認ダイアログ・リマインドを出さない。"""
        js = _read(DISCUSS_JS)
        for phrase in ("書き置きを書きますか", "書き置きを残しますか", "window.confirm"):
            assert phrase not in js, f"確認ダイアログ相当の記述: {phrase!r}"

    def test_save_uses_existing_cycle_intention_with_leave_note_role(self):
        block = _extract_function_body(_read(DISCUSS_JS), "async function saveLeaveNote() {")
        assert 'role: "leave_note"' in block
        assert "postCycleIntention(" in block

    def test_save_failure_keeps_input(self):
        """失敗時は本人が書いた文章を消さない（saveReflection と同型）。"""
        block = _extract_function_body(_read(DISCUSS_JS), "async function saveLeaveNote() {")
        assert "保存できませんでした。入力はそのまま残しています。" in block

    def test_tray_fetches_todays_words_once_on_open(self):
        block = _extract_function_body(_read(DISCUSS_JS), "async function loadTodaysWordsOnce() {")
        assert "/cycle/todays-words" in block
        assert "dataset.loaded" in block, "開いたときに1回だけフェッチするガードが無い"

    def test_tray_renders_only_word_text_via_textcontent(self):
        """RD1: words[].text 以外を描画しない・textContent のみ（AI 応答を混ぜない）。"""
        block = _extract_function_body(_read(DISCUSS_JS), "async function loadTodaysWordsOnce() {")
        assert "btn.textContent = text" in block
        assert "innerHTML" not in block
        assert "assistant" not in block

    def test_tray_click_quotes_into_leave_note_textarea(self):
        block = _extract_function_body(_read(DISCUSS_JS), "async function loadTodaysWordsOnce() {")
        assert "discuss-leave-note-input" in block

    def test_section_appended_at_end_of_landing_body(self):
        js = _read(DISCUSS_JS)
        block = _extract_function_body(
            js, "function buildLandingBodyHtml(tensionItems, anchorItems, reconItem) {"
        )
        idx_continue = block.index("このトピックで続きを学ぶ")
        idx_leave_note = block.index("leaveNoteSectionHtml()")
        assert idx_continue < idx_leave_note, "書き置き欄は着地画面の末尾に置く"

    def test_landing_events_wired(self):
        block = _extract_function_body(
            _read(DISCUSS_JS), "function bindLandingContentEvents(root, reconItem) {"
        )
        assert "discuss-leave-note-save" in block
        assert "discuss-todays-words" in block


class TestReturnDoorInlayAppJs:
    """扉（再入口インレイ #return-door, 設計書 §2.1）。"""

    def test_render_uses_textcontent_only(self):
        """RD1: 本文（本人の言葉）は textContent でのみ描画する。"""
        block = _extract_function_body(_read(APP_JS), "function renderReturnDoor(data) {")
        assert "textContent" in block
        assert "innerHTML" not in block
        assert "insertAdjacentHTML" not in block

    def test_fixed_presentation_order(self):
        """固定順: 書き置き → 持ち越しの問い → 最後に確定した引っかかり。
        （ラベル文字列リテラルの出現順で検証する — コメント内の言及に影響されないよう
        引用符付きで探す。）"""
        block = _extract_function_body(_read(APP_JS), "function renderReturnDoor(data) {")
        i1 = block.index('"あなたの書き置き"')
        i2 = block.index('"持ち越しの問い"')
        i3 = block.index('"最後に確定した引っかかり"')
        assert i1 < i2 < i3

    def test_no_elapsed_days_vocabulary_anywhere_in_app_js(self):
        """RD2: 「14日ぶり」等の経過日数表示を作らない。"""
        js = _read(APP_JS)
        hits = [w for w in ELAPSED_DAYS_WORDS if w in js]
        assert not hits, f"経過日数語彙が見つかりました: {hits}"

    def test_no_setinterval_in_new_functions(self):
        """新規追加分にポーリング（setInterval）なし。"""
        assert "setInterval" not in _app_new_bodies()

    def test_fetch_once_per_course_single_call_site(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "async function loadReturnDoor() {")
        assert "/cycle/return-door" in block
        # フェッチ地点は loadReturnDoor の1箇所のみ（ポーリング・多重取得の禁止）。
        assert js.count("/cycle/return-door") == 1

    def test_loaded_from_course_load_path(self):
        block = _extract_function_body(_read(APP_JS), "async function loadAndRenderCourse() {")
        assert "loadReturnDoor();" in block

    def test_empty_and_failure_render_nothing(self):
        """empty:true・取得失敗はインレイ自体を描画しない（fail-closed。空の枠を出さない）。"""
        block = _extract_function_body(_read(APP_JS), "async function loadReturnDoor() {")
        assert "data.empty" in block
        assert "catch" in block

    def test_dismiss_is_memory_only(self):
        """×はメモリ内フラグのみ（localStorage へ永続化しない）。"""
        js = _read(APP_JS)
        assert "returnDoorDismissed" in js
        for sig in (
            "function hideReturnDoor() {",
            "function renderReturnDoor(data) {",
            "async function loadReturnDoor() {",
        ):
            assert "localStorage" not in _extract_function_body(js, sig)

    def test_index_html_inlay_markup(self):
        """#return-door は既定 hidden で、data-ui-anchor が同一タグにある。"""
        html = _read(INDEX_HTML)
        m = re.search(r'<div[^>]*\bid="return-door"[^>]*>', html)
        assert m, "index.html に #return-door がありません"
        tag = m.group(0)
        assert f'data-ui-anchor="{ANCHOR_ID}"' in tag
        assert "hidden" in tag

    def test_css_inlay_does_not_shrink_layout(self):
        """flex: 0 0 auto の薄い1区画（上段 .material-region の圧縮規約を壊さない）。"""
        css = _read(STYLES_CSS)
        idx = css.index(".return-door {")
        rule = css[idx:css.index("}", idx)]
        assert "flex: 0 0 auto;" in rule


class TestMarginMarks:
    """欄外の印（設計書 §2.3）。"""

    def test_map_excluded_rows_are_excluded(self):
        block = _extract_function_body(_read(APP_JS), "function extractMarginMarks(traces) {")
        assert "map_excluded" in block

    def test_max_marks_constant_is_twelve(self):
        js = _read(APP_JS)
        assert "const MARGIN_MARKS_MAX = 12" in js
        block = _extract_function_body(js, "function extractMarginMarks(traces) {")
        assert "MARGIN_MARKS_MAX" in block

    def test_no_count_display(self):
        """RD5: 数を表示しない（「◯件」等の件数表示を作らない）。"""
        block = _extract_function_body(_read(APP_JS), "function renderMarginMarks() {")
        assert "件" not in block

    def test_tooltip_rendered_via_textcontent(self):
        block = _extract_function_body(_read(APP_JS), "function showMarginMarkTip(dot, text) {")
        assert "textContent" in block
        assert "innerHTML" not in block

    def test_toggle_uses_precision_reading_pattern(self):
        """localStorage eg_margin_marks:<courseId>（精読モードと同型の許容例外）。"""
        js = _read(APP_JS)
        assert '"eg_margin_marks:"' in js
        assert 'id="margin-marks-toggle"' in _read(INDEX_HTML)

    def test_default_is_on(self):
        """既定は表示 ON（キー未設定なら ON。明示的な "0" のときだけ OFF）。"""
        block = _extract_function_body(_read(APP_JS), "function isMarginMarksOn(courseId) {")
        assert '!== "0"' in block

    def test_confirmed_tension_statuses_whitelist(self):
        """確定 tension の status 語彙（正本: core/tension/schema.py）で絞り込む。"""
        js = _read(APP_JS)
        for status in ("open", "articulated", "connected", "abstracted"):
            assert f'"{status}"' in js
        assert "MARGIN_TENSION_STATUSES" in js


class TestLearnerHelpAnchor:
    """UIアンカー4点セット（正本 = core/help_kb/ui_anchors.py。
    test_my_records_ui_static.py の TestLearnerHelpAnchor 型）。
    """

    def test_anchor_registered_in_known_ids(self):
        assert ANCHOR_ID in KNOWN_UI_ANCHOR_IDS

    def test_anchor_mapped_to_student_manual_section(self):
        assert UI_ANCHORS.get(ANCHOR_ID) == "student/02-student.md#return-door"

    def test_manual_section_exists_with_explicit_anchor(self):
        md = _read(STUDENT_MANUAL)
        assert "{#return-door}" in md
        assert "未来の自分への書き置き" in md
        assert "欄外の印" in md
        # RD2/RD3 の約束をマニュアル側にも明記する。
        assert "書かなければ何も出ません" in md
        assert "通知やリマインドが届くことはありません" in md

    def test_frontend_anchor_values_are_all_known(self):
        used: set[str] = set()
        for path in (INDEX_HTML, APP_JS, DISCUSS_JS):
            used |= set(re.findall(r'data-ui-anchor="([a-zA-Z0-9_.\-]+)"', _read(path)))
        assert ANCHOR_ID in used
        assert not (used - set(KNOWN_UI_ANCHOR_IDS)), (
            f"KNOWN_UI_ANCHOR_IDS に無いアンカーID: {used - set(KNOWN_UI_ANCHOR_IDS)}"
        )


class TestForbiddenVocabulary:
    """新規 UI 文言に数値・進捗・ゲーミフィケーション語彙を出さない。"""

    def test_new_discuss_functions_clean(self):
        body = _discuss_new_bodies()
        hits = [w for w in FORBIDDEN_WORDS if w in body]
        assert not hits, f"禁止語彙が見つかりました: {hits}"

    def test_new_app_functions_clean(self):
        body = _app_new_bodies()
        hits = [w for w in FORBIDDEN_WORDS if w in body]
        assert not hits, f"禁止語彙が見つかりました: {hits}"

    def test_manual_section_clean(self):
        md = _read(STUDENT_MANUAL)
        section = md[md.index("{#return-door}"):]
        hits = [w for w in FORBIDDEN_WORDS if w in section]
        assert not hits, f"禁止語彙が見つかりました: {hits}"
