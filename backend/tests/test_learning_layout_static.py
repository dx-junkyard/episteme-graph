"""学習画面レイアウトの静的ガードレール（1画面に収める / ページはスクロールさせない）。

学習画面は `.app`（grid, height:100vh）で1画面に収める設計で、スクロールは
サイドバー・教材本文・会話・右パネルの**内側だけ**で起こる。ここが崩れると
「画面全体（トップバー含む）が一体でスクロールしてしまう」事故になる。原因は
2系統あり、両方を構造的に塞いだことを検査する:

1. ページ（document）自体がスクロール可能になる — `<html class="learn-page">` +
   `overflow: clip` で塞ぐ。`hidden` ではなく `clip` を使うのは、hidden だと
   `scrollIntoView()` / `input.focus()` によるプログラム的スクロールが依然
   可能で、トップバーが画面外へ送られてしまうため。admin 画面はページ
   スクロールが正なので `learn-page` を付けない（scope 分離）。
2. 主カラム `.mn` が内部スクロールする — こちらも `overflow: clip` にし、
   さらに下段（モードバー・discuss バー・composer）を `flex: 0 0 auto`、
   上段（教材区画）を `flex: 0 1 auto` + `min-height: 0` にして、縦が
   足りないときは**上段が縮む**（下段が押し出されない）ようにする。

併せて、教材区画の本文が `flex-basis: 0` だと教材区画の基準高さに本文が
算入されず `max-height: 42%` まで伸びない（ヘッダ＋フッタ分に潰れる）ため、
`flex: 1 1 auto` + `min-height: 0` であることも固定する。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INDEX_HTML = ROOT / "frontend" / "public" / "index.html"
ADMIN_HTML = ROOT / "frontend" / "public" / "admin.html"
STYLES_CSS = ROOT / "frontend" / "public" / "css" / "styles.css"
APP_JS = ROOT / "frontend" / "public" / "js" / "app.js"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _rule(css: str, selector: str) -> str:
    """`selector {` から対応する `}` までの宣言ブロックを返す。"""
    idx = css.index(selector + " {")
    end = css.index("}", idx)
    return css[idx:end]


class TestPageItselfNeverScrolls:
    """(1) document がスクロールしない。"""

    def test_index_html_marks_learn_page(self):
        html = _read(INDEX_HTML)
        m = re.search(r"<html[^>]*>", html)
        assert m is not None
        assert "learn-page" in m.group(0), "学習画面の <html> に learn-page クラスが無い"

    def test_admin_html_is_not_marked(self):
        # admin 画面（.admin-app, min-height:100vh）はページスクロールが正。
        m = re.search(r"<html[^>]*>", _read(ADMIN_HTML))
        assert m is not None
        assert "learn-page" not in m.group(0)

    def test_learn_page_uses_overflow_clip(self):
        css = _read(STYLES_CSS)
        assert "html.learn-page,\nhtml.learn-page > body {" in css
        block = css[css.index("html.learn-page,"):]
        block = block[:block.index("}")]
        assert "overflow: clip;" in block, "hidden ではなく clip で塞ぐこと（下記 docstring 参照）"

    def test_app_grid_is_viewport_height(self):
        rule = _rule(_read(STYLES_CSS), ".app")
        assert "height: 100vh;" in rule


class TestMainColumnNeverScrollsAsAWhole:
    """(2) 主カラムが列ごと動かない。"""

    def test_mn_uses_overflow_clip(self):
        rule = _rule(_read(STYLES_CSS), ".mn")
        assert "overflow: clip;" in rule, ".mn が hidden だと scrollIntoView で列ごとスクロールする"
        assert "min-height: 0;" in rule

    def test_bottom_rows_do_not_shrink(self):
        css = _read(STYLES_CSS)
        for selector in (".ia", ".mode-bar", ".discuss-bar", ".lecture-player"):
            assert "flex: 0 0 auto;" in _rule(css, selector), f"{selector} が縮む設定になっている"

    def test_material_region_shrinks_instead(self):
        rule = _rule(_read(STYLES_CSS), ".material-region")
        assert "flex: 0 1 auto;" in rule
        assert "min-height: 0;" in rule
        assert "max-height: 42%;" in rule

    def test_chat_area_keeps_a_floor(self):
        rule = _rule(_read(STYLES_CSS), ".ca")
        assert "min-height: 120px;" in rule
        # 極端に低いウィンドウでは composer 確保を優先して floor を下げる。
        assert re.search(r"@media \(max-height: \d+px\) \{\s*\.ca \{\s*min-height: 60px;", _read(STYLES_CSS))

    def test_app_js_keeps_material_region_shrinkable(self):
        js = _read(APP_JS)
        # 分割ハンドル・自動圧縮が inline で flex-shrink を 0 にすると、px 指定の
        # 高さがそのまま下段を押し出す（ウィンドウを低くしたときに composer が消える）。
        assert 'region.style.flex = "0 0 auto"' not in js
        assert js.count('region.style.flex = "0 1 auto"') >= 2


class TestPanesScrollInternally:
    """スクロールは各ペインの内側で起こる（機能としての縦スクロールは残す）。"""

    def test_sidebar_and_right_panel_scroll(self):
        css = _read(STYLES_CSS)
        assert "overflow-y: auto;" in _rule(css, ".sb")
        assert "overflow-y: auto;" in _rule(css, ".rp")

    def test_material_body_and_chat_scroll(self):
        css = _read(STYLES_CSS)
        body_rule = _rule(css, ".material-region .material-body")
        assert "overflow-y: auto;" in body_rule
        # basis 0 にすると教材区画が 42% まで伸びない（潰れる）。
        assert "flex: 1 1 auto;" in body_rule
        assert "min-height: 0;" in body_rule
        assert "overflow-y: auto;" in _rule(css, ".ca")
