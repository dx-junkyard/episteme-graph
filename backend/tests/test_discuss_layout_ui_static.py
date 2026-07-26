"""discuss モード（「論文と話す」）の「対話ファーストレイアウト」改修
（2026-07-26, docs/features/discussion_mode_design.md §9.7）に対する静的ガードレール。

test_discuss_ui_static.py / test_learning_layout_static.py と同じ流儀で、ソース文字列の
部分一致・関数本体/CSS宣言ブロックの抽出検査のみを行う（実サーバ・実DOM不使用）。

この改修が守るべき不変条項:

1. discuss 中は `.app`（学習画面の grid ルート）へ `discuss-on` クラスを、会話が
   1件以上始まったら追加で `discuss-chat-active` クラスを付与する。トグルは
   `applyDiscussLayout()` の1関数に集約し、モード切替・会話描画・トピック遷移の
   各経路（`renderChat` / `selectTopic`）から必ず呼ばれる（呼び忘れによる layout
   不整合を防ぐ）。
2. 右パネル（`.rp`）は discuss 中は非表示、左サイドバー（`.sb`）はモード切替ピル
   （`.discuss-mode-switch`）だけを残した細いレールに折りたたむ。中央カラムは
   最大幅を持つセンタリング列になる。
3. 開幕カード（`#material-body` に描画される静的な開幕画面）は、会話が始まる前は
   通常どおり空間を占有するが、会話が始まると `#material-region` が
   「論文の要点」の細いヘッダーストリップへ自動的に折りたたまれ、
   `#discuss-opening-toggle`（開く/たたむ、`aria-expanded` 公開）で
   `discuss-expanded` クラスをトグルして再展開できる。
4. 分割ハンドルのドラッグ（`.split-handle`）と機能2の自動圧縮/復元
   （`collapseMaterialForChat` / `restoreMaterialRegion`）は discuss 中は無効化する
   （`if (isDiscussMode()) return;` の先頭ガード）。
5. 基底レイアウト（1画面に収める設計）の CSS 宣言は変更しない。
   `.material-region` の基底ルール（`flex: 0 1 auto` / `max-height: 42%`）は
   test_learning_layout_static.py が別途固定しており、本ファイルはその上に discuss
   専用ルールを追加していないことの多重防御のみ行う。
6. 新規コピー（要点ストリップ周り）にも DM5（「寄り道」語彙の追放）が及ぶ。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP_JS = ROOT / "frontend" / "public" / "js" / "app.js"
INDEX_HTML = ROOT / "frontend" / "public" / "index.html"
STYLES_CSS = ROOT / "frontend" / "public" / "css" / "styles.css"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _function_block(src: str, start_marker: str) -> str:
    """`start_marker`（例: "function foo() {" のような完全な関数シグネチャ、または
    "function foo(" のようなシグネチャの先頭部分）の出現位置から、最初の "{" を起点に
    素朴な波括弧カウントで対応する "}" までを抽出する。対象関数の実装は文字列リテラル中に
    `{`/`}` を含まない前提（test_discuss_ui_static.py の `_extract_function_body` と
    同じ実装idiom）。"""
    start = src.index(start_marker)
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
    raise AssertionError("unterminated function body for: " + start_marker)


def _rule(css: str, selector: str) -> str:
    """`selector {` から対応する `}` までの宣言ブロックを返す
    （test_learning_layout_static.py と同じ実装idiom）。"""
    idx = css.index(selector + " {")
    end = css.index("}", idx)
    return css[idx:end]


def _all_indices(haystack: str, needle: str):
    idx = haystack.find(needle)
    while idx != -1:
        yield idx
        idx = haystack.find(needle, idx + 1)


class TestDiscussLayoutClassToggling:
    """discuss モードの3クラス設計の中心となる `applyDiscussLayout()`。"""

    def test_apply_discuss_layout_function_exists(self):
        js = _read(APP_JS)
        assert "function applyDiscussLayout(" in js

    def test_apply_discuss_layout_toggles_both_classes(self):
        js = _read(APP_JS)
        block = _function_block(js, "function applyDiscussLayout(")
        assert 'classList.toggle("discuss-on"' in block
        assert 'classList.toggle("discuss-chat-active"' in block

    def test_apply_discuss_layout_called_from_render_chat(self):
        js = _read(APP_JS)
        block = _function_block(js, "function renderChat() {")
        assert "applyDiscussLayout();" in block

    def test_apply_discuss_layout_called_from_select_topic(self):
        js = _read(APP_JS)
        block = _function_block(js, "async function selectTopic(topicId, opts) {")
        assert "applyDiscussLayout();" in block

    def test_no_hardcoded_flex_0_0_auto_on_material_region(self):
        """機能2由来の回帰ガード（test_learning_layout_static.py と同一趣旨）:
        discuss 対応の追加実装でも .material-region の flex-shrink を inline で
        0 にしないこと（縦の低い画面で下段=composer が押し出される）。"""
        js = _read(APP_JS)
        assert 'region.style.flex = "0 0 auto"' not in js


class TestDiscussDisablesInlineResizeMachinery:
    """discuss 中は分割ハンドルのドラッグ・自動圧縮/復元（機能2）を無効化する
    （discuss は開幕カード/要点ストリップという別レイアウトのため、px 指定の
    高さ操作と衝突する）。"""

    def test_collapse_material_for_chat_guards_discuss_mode(self):
        js = _read(APP_JS)
        block = _function_block(js, "function collapseMaterialForChat() {")
        assert "if (isDiscussMode()) return;" in block

    def test_restore_material_region_guards_discuss_mode(self):
        js = _read(APP_JS)
        block = _function_block(js, "function restoreMaterialRegion() {")
        assert "if (isDiscussMode()) return;" in block


class TestOpeningStripToggle:
    """開幕カードの「論文の要点」ストリップ化（開く/たたむ）。"""

    def test_index_html_has_opening_toggle_button(self):
        html = _read(INDEX_HTML)
        assert 'id="discuss-opening-toggle"' in html

    def test_toggle_function_exists_and_toggles_expanded_class(self):
        js = _read(APP_JS)
        assert "function toggleDiscussOpening(" in js
        block = _function_block(js, "function toggleDiscussOpening(")
        assert 'classList.toggle("discuss-expanded")' in block

    def test_aria_expanded_is_set_dynamically(self):
        js = _read(APP_JS)
        assert "aria-expanded" in js

    def test_update_material_header_shows_opening_strip_title(self):
        js = _read(APP_JS)
        block = _function_block(js, "function updateMaterialHeader() {")
        assert "論文の要点" in block
        # 既存不変条項（discuss 中は提示形式トグル自体を隠す）は維持されていること。
        assert "toggle.hidden = isDiscussMode()" in block

    def test_toggle_labels_open_and_collapse(self):
        js = _read(APP_JS)
        assert '"たたむ"' in js
        assert '"開く"' in js


class TestDiscussLayoutCss:
    """discuss 対話ファーストレイアウトの CSS 側の骨格。"""

    def test_app_discuss_on_changes_grid_columns(self):
        css = _read(STYLES_CSS)
        rule = _rule(css, ".app.discuss-on")
        assert "grid-template-columns" in rule

    def test_right_panel_hidden_in_discuss(self):
        css = _read(STYLES_CSS)
        rule = _rule(css, ".app.discuss-on .rp")
        assert "display: none;" in rule

    def test_sidebar_collapses_to_rail_except_mode_switch(self):
        css = _read(STYLES_CSS)
        rule = _rule(css, ".app.discuss-on .sb > *")
        assert "display: none;" in rule
        # モード切替ピルはレール化後も見えていること（等重表示 DM5 の継続）。
        assert ".app.discuss-on .sb > .discuss-mode-switch" in css

    def test_split_handle_hidden_in_discuss(self):
        css = _read(STYLES_CSS)
        rule = _rule(css, ".app.discuss-on .split-handle")
        assert "display: none;" in rule

    def test_material_region_full_height_before_chat_starts(self):
        css = _read(STYLES_CSS)
        rule = _rule(css, ".app.discuss-on:not(.discuss-chat-active) .material-region")
        assert "max-height: none;" in rule

    def test_material_body_hidden_when_collapsed_after_chat_starts(self):
        css = _read(STYLES_CSS)
        rule = _rule(
            css,
            ".app.discuss-on.discuss-chat-active .material-region:not(.discuss-expanded) .material-body",
        )
        assert "display: none;" in rule

    def test_material_region_expands_on_toggle(self):
        css = _read(STYLES_CSS)
        rule = _rule(
            css,
            ".app.discuss-on.discuss-chat-active .material-region.discuss-expanded",
        )
        assert "max-height" in rule

    def test_opening_toggle_button_styled(self):
        css = _read(STYLES_CSS)
        assert ".discuss-opening-toggle {" in css

    def test_center_column_is_capped_width(self):
        """中央カラム（.ca を含む会話周り）は discuss 中に最大幅を持つセンタリング列
        になる。単独ルールでもグループセレクタの一部でも良いため、`.ca` を含む
        セレクタリストの直後の宣言ブロックを素朴に取り出して検査する。"""
        css = _read(STYLES_CSS)
        marker = ".app.discuss-on .ca"
        assert marker in css
        idx = css.index(marker)
        brace_open = css.index("{", idx)
        brace_close = css.index("}", brace_open)
        block = css[brace_open:brace_close]
        assert "max-width" in block

    def test_base_material_region_rule_untouched(self):
        """基底レイアウト（1画面に収める設計, test_learning_layout_static.py）を
        discuss 対応で壊していないことの多重防御。最初に出現する `.material-region {`
        ルール（discuss 専用の複合セレクタより手前にある基底ルール）を検査する。"""
        css = _read(STYLES_CSS)
        idx = css.index(".material-region {")
        end = css.index("}", idx)
        rule = css[idx:end]
        assert "flex: 0 1 auto;" in rule
        assert "max-height: 42%;" in rule


class TestNoBannedVocabInNewCopy:
    """DM5 継承: 新規コピー（要点ストリップ周り）にも「寄り道」を混入させない。"""

    MARKERS = ("discuss-opening-toggle", "applyDiscussLayout", "論文の要点")

    def test_no_detour_word_near_new_markers_in_js(self):
        js = _read(APP_JS)
        for marker in self.MARKERS:
            for idx in _all_indices(js, marker):
                line_start = js.rfind("\n", 0, idx) + 1
                nl = js.find("\n", idx)
                line_end = nl if nl != -1 else len(js)
                line = js[line_start:line_end]
                assert "寄り道" not in line, f"'{marker}' 近傍の行に「寄り道」: {line!r}"

    def test_index_html_new_marker_lines_have_no_detour_word(self):
        # index.html には「『寄り道』という語を discuss で使わない」ことを説明する
        # 既存のメタコメント（discuss-bar 上の開発メモ）が正当に存在するため、
        # ファイル全体ではなく新設マーカー行にスコープする
        # （既存マーカーの網羅は test_discuss_ui_static.py::TestNoDetourVocabInDiscussUi）。
        html = _read(INDEX_HTML)
        for idx in _all_indices(html, "discuss-opening-toggle"):
            line_start = html.rfind("\n", 0, idx) + 1
            nl = html.find("\n", idx)
            line_end = nl if nl != -1 else len(html)
            line = html[line_start:line_end]
            assert "寄り道" not in line, f"新設マーカー行に「寄り道」: {line!r}"
