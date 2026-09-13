"""記号の「直前の定義」の UI 契約（概念レジストリ P3-5 / §7）。

``frontend/public/{index.html,js/app.js,css/styles.css}`` を静的に検査して次を固定する:

1. ポップオーバーの器（``#symbol-lookup-popover``）が index.html にあり、既定で hidden。
2. app.js が学習者 API を1本だけ呼び、``symbol`` / ``equation_id`` / ``chunk_id`` を渡す。
3. **自動表示しない**（タップのときだけ。ポーリング・setInterval を作らない）。
4. **数値を描かない**（confidence / score / 件数のような語を描画コードに置かない）。
5. アンカー3点セット（担体 / ``core/help_kb/ui_anchors.py`` / マニュアル節）。
6. 既存の教材導線（テキスト選択・ホバーツールチップ・数式カードの文脈ボタン）を
   壊さない（``stopPropagation`` / ``preventDefault`` をクリック経路に入れない）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

INDEX_HTML = ROOT / "frontend" / "public" / "index.html"
APP_JS = ROOT / "frontend" / "public" / "js" / "app.js"
STYLES_CSS = ROOT / "frontend" / "public" / "css" / "styles.css"
MANUAL = ROOT / "docs" / "manual" / "student" / "02-student.md"

ANCHOR_ID = "material.symbol-lookup"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _function_block(name: str) -> str:
    """``app.js`` の関数1つ分の本文（次の同インデント ``function`` まで）。"""
    src = _read(APP_JS)
    pattern = re.compile(r"^([ \t]*)(?:async +)?function %s\(" % re.escape(name), re.MULTILINE)
    match = pattern.search(src)
    assert match, f"{name} が app.js に見つからない"
    indent = match.group(1)
    rest = src[match.end():]
    end = re.search(r"^%s\}" % re.escape(indent), rest, re.MULTILINE)
    return rest[: end.start()] if end else rest


class TestPopoverCarrier:
    def test_index_html_has_the_popover_element(self):
        html = _read(INDEX_HTML)
        assert 'id="symbol-lookup-popover"' in html

    def test_popover_is_hidden_by_default(self):
        """自動では出さない（KR5 / IH9 と同じ「本人の明示操作」原則）。"""
        html = _read(INDEX_HTML)
        block = html[html.index('id="symbol-lookup-popover"'):]
        block = block[: block.index(">") + 1]
        assert "hidden" in block

    def test_css_keeps_the_hidden_attribute_effective(self):
        """``.symbol-lookup-popover`` の display 指定が [hidden] を上書きしない。"""
        css = _read(STYLES_CSS)
        assert "#symbol-lookup-popover[hidden] { display: none !important; }" in css


class TestApiCall:
    def test_calls_the_learner_symbol_lookup_endpoint(self):
        js = _function_block("openSymbolLookup")
        assert "/symbols/lookup?" in js
        assert "/learning/courses/" in js

    def test_sends_symbol_and_position(self):
        js = _function_block("openSymbolLookup")
        assert "symbol: symbolText" in js
        assert '"equation_id"' in js or "'equation_id'" in js or 'set("equation_id"' in js
        assert 'set("chunk_id"' in js

    def test_position_keys_are_omitted_when_unknown(self):
        """位置が取れないときはキー自体を送らない（0 / 空文字を既定にしない）。"""
        js = _function_block("openSymbolLookup")
        assert 'if (eqId) params.set("equation_id", eqId);' in js
        assert 'if (chunkId) params.set("chunk_id", chunkId);' in js

    def test_no_write_method(self):
        js = _function_block("openSymbolLookup")
        assert "method:" not in js  # GET のみ（apiFetch の既定）

    def test_equation_card_carries_the_position_attribute(self):
        """数式カードに ``data-equation-id``（タップ位置の担体）が付いている。"""
        js = _read(APP_JS)
        assert 'data-equation-id="' in js


class TestNoAutoDisplayAndNoNumbers:
    def test_no_polling(self):
        js = _function_block("initSymbolLookup")
        assert "setInterval" not in js
        assert "setTimeout" not in js

    def test_opens_only_on_click(self):
        js = _function_block("initSymbolLookup")
        assert 'addEventListener("click"' in js
        # ホバーでは開かない（既存のホバーツールチップと役割を分ける）。
        assert 'addEventListener("mouseover"' not in js

    def test_closes_on_escape_and_outside_click(self):
        js = _function_block("initSymbolLookup")
        assert '"Escape"' in js
        assert 'addEventListener("mousedown"' in js

    def test_renderer_shows_no_numbers(self):
        """confidence / score / 件数のような数値表示をしない（KR6）。"""
        js = _function_block("renderSymbolLookupPopover")
        for forbidden in ("confidence", "score", "件", "%"):
            assert forbidden not in js

    def test_renderer_uses_server_facts_verbatim(self):
        """事実文はサーバが正本（フロントに文言を焼き込まない）。"""
        js = _function_block("renderSymbolLookupPopover")
        assert "data.facts" in js or "(data && data.facts)" in js
        # 定義の逐語はそのまま描く（要約・言い換えをしない）。
        assert "definition.text" in js


class TestDoesNotBreakExistingAffordances:
    def test_click_handler_does_not_swallow_events(self):
        """``stopPropagation`` / ``preventDefault`` を入れない（既存導線を優先させる）。"""
        js = _function_block("initSymbolLookup")
        assert "stopPropagation" not in js
        assert "preventDefault" not in js

    def test_only_katex_tokens_inside_the_material_region_are_targeted(self):
        js = _function_block("initSymbolLookup")
        assert '"#material-body"' in js
        assert '".katex"' in js

    def test_symbol_token_gate_is_narrow(self):
        """1〜3文字のラテン / ギリシャ文字だけを記号として扱う（誤爆を避ける）。"""
        js = _read(APP_JS)
        assert "SYMBOL_TOKEN_RE" in js
        assert "{1,3}" in js


class TestAnchorTriplet:
    def test_frontend_carrier(self):
        html = _read(INDEX_HTML)
        assert 'data-ui-anchor="%s"' % ANCHOR_ID in html

    def test_registry_entry(self):
        from core.help_kb import ui_anchors

        assert ANCHOR_ID in ui_anchors.KNOWN_UI_ANCHOR_IDS
        assert ui_anchors.UI_ANCHORS[ANCHOR_ID] == "student/02-student.md#symbol-lookup"

    def test_manual_section_exists(self):
        text = _read(MANUAL)
        assert "{#symbol-lookup}" in text
        assert "](#symbol-lookup)" in text  # 目次からのリンク

    def test_manual_section_states_the_closed_world(self):
        """「この論文には」と書く（分野レベルの不在を語らない = KR8）。"""
        text = _read(MANUAL)
        section = text[text.index("{#symbol-lookup}"):]
        section = section[: section.index("\n---")]
        assert "この論文" in section
        for forbidden in ("分野の中で定義されていない、という意味ではありません",):
            assert forbidden in section

    def test_manual_section_has_no_numbers_promise(self):
        text = _read(MANUAL)
        section = text[text.index("{#symbol-lookup}"):]
        section = section[: section.index("\n---")]
        assert "記録され" in section  # 記録されないことを明記している
