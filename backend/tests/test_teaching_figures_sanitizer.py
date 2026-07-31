"""SVG サニタイザ（core/teaching_figures/sanitizer.py）のテスト。

正本: docs/features/teaching_figure_studio_design.md §4.1 / FG3 / §11-8 の裁定。

観点:
  1. 危険な構造は**除去ではなく拒否**（script / on* / foreignObject / image /
     外部 href / DOCTYPE / ENTITY / 処理命令 / 許可外の名前空間・属性）
  2. viewBox 必須、width/height の正規化付与
  3. style 属性の url( / expression( / @import 拒否、paint 属性の外部 url( 拒否
  4. サイズ上限（入力・出力の両方）
  5. 正常系（日本語テキスト・path・marker・グラデーション・内部 use 参照）と
     ハッシュの決定性
  6. lxml 安全パーサ固定（stdlib xml.etree へのフォールバックが存在しない）

DB にも LLM にも触らない純粋な単体テスト。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_backend_dir = str(Path(__file__).resolve().parents[1])
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from core.teaching_figures.sanitizer import (  # noqa: E402
    SanitizedSvg,
    SvgRejected,
    sanitize_svg,
)
from tests.guardrail_helpers import assert_source_does_not_import  # noqa: E402

_MAX = 200_000

_SVG_OPEN = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100"'


def _svg(inner: str, *, root_attrs: str = "") -> str:
    return f"{_SVG_OPEN}{(' ' + root_attrs) if root_attrs else ''}>{inner}</svg>"


def _reject_reason(source: str, *, max_bytes: int = _MAX) -> str:
    with pytest.raises(SvgRejected) as exc:
        sanitize_svg(source, max_bytes=max_bytes)
    assert exc.value.reason, "SvgRejected.reason は教員に見せる事実文なので空にしない"
    return exc.value.reason


# ===========================================================================
# 1. 危険な構造の拒否（除去ではなく拒否）
# ===========================================================================


class TestDangerousConstructsAreRejected:
    def test_script_element(self):
        reason = _reject_reason(_svg('<script>alert(1)</script>'))
        assert "script" in reason

    def test_script_element_nested_in_group(self):
        reason = _reject_reason(_svg('<g><rect/><script>alert(1)</script></g>'))
        assert "script" in reason

    def test_event_handler_attribute(self):
        reason = _reject_reason(_svg('<rect onclick="steal()"/>'))
        assert "onclick" in reason

    def test_event_handler_attribute_uppercase(self):
        reason = _reject_reason(_svg('<rect ONLOAD="steal()"/>'))
        assert "ONLOAD" in reason or "onload" in reason.lower()

    def test_foreign_object(self):
        reason = _reject_reason(_svg("<foreignObject><rect/></foreignObject>"))
        assert "foreignObject" in reason

    def test_image_element(self):
        reason = _reject_reason(_svg('<image href="#x" width="10" height="10"/>'))
        assert "image" in reason

    def test_style_element_is_not_allowed(self):
        # v1 は <style> 要素を許可しない（style 属性で足りる。§4.1）
        reason = _reject_reason(_svg("<style>rect{fill:red}</style>"))
        assert "style" in reason

    def test_external_href_on_use(self):
        source = (
            '<svg xmlns="http://www.w3.org/2000/svg" '
            'xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 10 10">'
            '<use xlink:href="https://evil.example/x.svg#a"/></svg>'
        )
        reason = _reject_reason(source)
        assert "xlink:href" in reason

    def test_external_href_plain_attribute(self):
        reason = _reject_reason(_svg('<use href="https://evil.example/x.svg#a"/>'))
        assert "href" in reason

    def test_internal_href_is_allowed(self):
        result = sanitize_svg(
            _svg('<defs><rect id="a" width="2" height="2"/></defs><use href="#a"/>'),
            max_bytes=_MAX,
        )
        assert 'href="#a"' in result.svg

    def test_doctype_declaration(self):
        reason = _reject_reason(
            '<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "x.dtd">' + _svg("<rect/>")
        )
        assert "DOCTYPE" in reason

    def test_doctype_declaration_lowercase(self):
        reason = _reject_reason("<!doctype svg>" + _svg("<rect/>"))
        assert "DOCTYPE" in reason

    def test_entity_declaration(self):
        # billion laughs の入口。パース前に無条件で拒否する。
        source = (
            '<!DOCTYPE svg [<!ENTITY lol "haha">]>' + _svg("<desc>&lol;</desc>")
        )
        reason = _reject_reason(source)
        assert "DOCTYPE" in reason or "ENTITY" in reason

    def test_entity_declaration_without_doctype_token(self):
        reason = _reject_reason('<!ENTITY x "y">' + _svg("<rect/>"))
        assert "ENTITY" in reason

    def test_processing_instruction_is_rejected(self):
        reason = _reject_reason('<?xml-stylesheet href="evil.css"?>' + _svg("<rect/>"))
        assert "処理命令" in reason

    def test_xml_declaration_is_allowed(self):
        result = sanitize_svg('<?xml version="1.0" encoding="UTF-8"?>' + _svg("<rect/>"), max_bytes=_MAX)
        assert result.svg.startswith("<svg")

    def test_foreign_namespace_element(self):
        source = (
            '<svg xmlns="http://www.w3.org/2000/svg" '
            'xmlns:h="http://www.w3.org/1999/xhtml" viewBox="0 0 10 10">'
            "<h:b>x</h:b></svg>"
        )
        reason = _reject_reason(source)
        assert "名前空間" in reason

    def test_unknown_attribute_is_rejected(self):
        reason = _reject_reason(_svg('<rect data-payload="1"/>'))
        assert "data-payload" in reason

    def test_animation_element_is_rejected(self):
        reason = _reject_reason(_svg('<rect><animate attributeName="x" to="10"/></rect>'))
        assert "animate" in reason

    def test_empty_input(self):
        assert _reject_reason("   ")

    def test_non_svg_root(self):
        reason = _reject_reason('<html xmlns="http://www.w3.org/1999/xhtml"><body/></html>')
        assert "<svg>" in reason

    def test_malformed_xml(self):
        reason = _reject_reason(_SVG_OPEN + "><rect>")
        assert "解析できませんでした" in reason


# ===========================================================================
# 2. viewBox 必須 / width・height 正規化
# ===========================================================================


class TestViewBoxAndSizeNormalization:
    def test_missing_viewbox_is_rejected(self):
        reason = _reject_reason('<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>')
        assert "viewBox" in reason

    def test_empty_viewbox_is_rejected(self):
        reason = _reject_reason('<svg xmlns="http://www.w3.org/2000/svg" viewBox=" "><rect/></svg>')
        assert "viewBox" in reason

    def test_viewbox_with_wrong_arity_is_rejected(self):
        reason = _reject_reason('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10"><rect/></svg>')
        assert "viewBox" in reason

    def test_viewbox_with_non_numeric_values_is_rejected(self):
        reason = _reject_reason(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ten 10"><rect/></svg>'
        )
        assert "viewBox" in reason

    def test_viewbox_with_zero_extent_is_rejected(self):
        reason = _reject_reason(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 0 10"><rect/></svg>'
        )
        assert "viewBox" in reason

    def test_width_height_are_derived_from_viewbox(self):
        result = sanitize_svg(_svg("<rect/>"), max_bytes=_MAX)
        assert 'width="200"' in result.svg
        assert 'height="100"' in result.svg

    def test_existing_width_height_are_preserved(self):
        result = sanitize_svg(_svg("<rect/>", root_attrs='width="50" height="25"'), max_bytes=_MAX)
        assert 'width="50"' in result.svg
        assert 'height="25"' in result.svg

    def test_only_missing_dimension_is_added(self):
        result = sanitize_svg(_svg("<rect/>", root_attrs='width="50"'), max_bytes=_MAX)
        assert 'width="50"' in result.svg
        assert 'height="100"' in result.svg

    def test_comma_separated_viewbox_is_accepted(self):
        result = sanitize_svg(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0,0,30,15"><rect/></svg>',
            max_bytes=_MAX,
        )
        assert 'width="30"' in result.svg
        assert 'height="15"' in result.svg

    def test_fractional_viewbox_extent(self):
        result = sanitize_svg(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 12.5 6.25"><rect/></svg>',
            max_bytes=_MAX,
        )
        assert 'width="12.5"' in result.svg
        assert 'height="6.25"' in result.svg


# ===========================================================================
# 3. style 属性・paint 参照
# ===========================================================================


class TestStyleAndPaintReferences:
    def test_style_attribute_is_allowed(self):
        result = sanitize_svg(_svg('<rect style="fill:#333;stroke-width:2"/>'), max_bytes=_MAX)
        assert 'style="fill:#333;stroke-width:2"' in result.svg

    def test_style_with_url_is_rejected(self):
        reason = _reject_reason(_svg('<rect style="fill:url(https://evil.example/x)"/>'))
        assert "url(" in reason

    def test_style_with_internal_url_is_also_rejected(self):
        # style 属性内の url( は内部参照でも一律拒否（属性側で fill="url(#g)" と書ける）。
        assert _reject_reason(_svg('<rect style="fill:url(#g)"/>'))

    def test_style_with_expression_is_rejected(self):
        reason = _reject_reason(_svg('<rect style="width:expression(alert(1))"/>'))
        assert "expression(" in reason

    def test_style_with_import_is_rejected(self):
        reason = _reject_reason(_svg('<rect style="@import \'evil.css\'"/>'))
        assert "@import" in reason

    def test_style_with_javascript_scheme_is_rejected(self):
        assert _reject_reason(_svg('<rect style="background:javascript:alert(1)"/>'))

    def test_internal_paint_url_is_allowed(self):
        source = _svg(
            '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="0">'
            '<stop offset="0" stop-color="#fff"/></linearGradient></defs>'
            '<rect fill="url(#g)" width="10" height="10"/>'
        )
        result = sanitize_svg(source, max_bytes=_MAX)
        assert 'fill="url(#g)"' in result.svg

    def test_external_paint_url_is_rejected(self):
        reason = _reject_reason(_svg('<rect fill="url(https://evil.example/x.svg#g)"/>'))
        assert "url(" in reason


# ===========================================================================
# 4. サイズ上限
# ===========================================================================


class TestSizeLimit:
    def test_oversized_input_is_rejected(self):
        source = _svg("<desc>" + "あ" * 500 + "</desc>")
        reason = _reject_reason(source, max_bytes=100)
        assert "上限" in reason

    def test_within_limit_is_accepted(self):
        source = _svg("<rect width='4' height='4'/>")
        result = sanitize_svg(source, max_bytes=len(source.encode("utf-8")) + 200)
        assert result.svg

    def test_limit_counts_utf8_bytes_not_characters(self):
        # 日本語は 1 文字 3 バイト。文字数で数えていたらこれは通ってしまう。
        source = _svg("<desc>" + "あ" * 60 + "</desc>")
        assert len(source) < 300
        assert _reject_reason(source, max_bytes=200)


# ===========================================================================
# 5. 正常系と決定性
# ===========================================================================


class TestHappyPath:
    _RICH = _svg(
        '<title>熱平衡に達する過程</title>'
        '<desc>3段階のプロセス図</desc>'
        '<defs>'
        '<marker id="arrow" markerWidth="6" markerHeight="6" refX="3" refY="3" orient="auto">'
        '<path d="M0 0 L6 3 L0 6 z" fill="#333"/></marker>'
        '<clipPath id="c"><rect x="0" y="0" width="10" height="10"/></clipPath>'
        '</defs>'
        '<g transform="translate(4,4)" clip-path="url(#c)">'
        '<rect x="0" y="0" width="40" height="20" rx="3" fill="#eef" stroke="#334"/>'
        '<line x1="40" y1="10" x2="70" y2="10" stroke="#334" marker-end="url(#arrow)"/>'
        '<circle cx="90" cy="10" r="6" fill="none" stroke="#334" stroke-dasharray="2 2"/>'
        '<polyline points="0,0 5,5 10,0" fill="none" stroke="#334"/>'
        '<polygon points="0,0 5,5 10,0" fill="#fff"/>'
        '<ellipse cx="120" cy="10" rx="8" ry="5" fill="#fff"/>'
        '<text x="10" y="14" font-family="sans-serif" font-size="10" text-anchor="middle">'
        '境界条件の適用<tspan dx="2" font-weight="bold">（第2段階）</tspan></text>'
        '</g>'
    )

    def test_rich_svg_passes(self):
        result = sanitize_svg(self._RICH, max_bytes=_MAX)
        assert isinstance(result, SanitizedSvg)
        assert result.svg.startswith("<svg")
        assert 'xmlns="http://www.w3.org/2000/svg"' in result.svg

    def test_japanese_text_is_preserved(self):
        result = sanitize_svg(self._RICH, max_bytes=_MAX)
        assert "境界条件の適用" in result.svg
        assert "（第2段階）" in result.svg
        assert "熱平衡に達する過程" in result.svg

    def test_ns0_prefix_is_not_emitted(self):
        # 既定名前空間で直列化する（`<img>` からそのまま読める形にする）。
        result = sanitize_svg(self._RICH, max_bytes=_MAX)
        assert "ns0:" not in result.svg

    def test_comments_are_dropped_but_content_is_kept(self):
        result = sanitize_svg(
            _svg("<!-- 作図メモ --><desc>説明</desc>"), max_bytes=_MAX
        )
        assert "作図メモ" not in result.svg
        assert "説明" in result.svg

    def test_hash_is_deterministic(self):
        first = sanitize_svg(self._RICH, max_bytes=_MAX)
        second = sanitize_svg(self._RICH, max_bytes=_MAX)
        assert first.sha256 == second.sha256
        assert len(first.sha256) == 64

    def test_source_hash_tracks_submitted_original(self):
        # サニタイズで width/height が付くので、正規化後と原文のハッシュは別物になる。
        result = sanitize_svg(_svg("<rect/>"), max_bytes=_MAX)
        assert result.source_sha256 != result.sha256
        assert len(result.source_sha256) == 64

    def test_output_is_idempotent(self):
        once = sanitize_svg(self._RICH, max_bytes=_MAX)
        twice = sanitize_svg(once.svg, max_bytes=_MAX)
        assert twice.svg == once.svg
        assert twice.sha256 == once.sha256

    def test_svg_without_namespace_gains_one(self):
        result = sanitize_svg('<svg viewBox="0 0 10 10"><rect/></svg>', max_bytes=_MAX)
        assert 'xmlns="http://www.w3.org/2000/svg"' in result.svg

    def test_xml_space_attribute_is_allowed(self):
        source = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
            '<text xml:space="preserve">a  b</text></svg>'
        )
        assert "a  b" in sanitize_svg(source, max_bytes=_MAX).svg


# ===========================================================================
# 6. lxml 固定（設計書 §11-8 の裁定）
# ===========================================================================


class TestParserIsLxmlOnly:
    @staticmethod
    def _source() -> str:
        path = Path(_backend_dir) / "core" / "teaching_figures" / "sanitizer.py"
        return path.read_text(encoding="utf-8")

    def test_no_stdlib_elementtree_fallback(self):
        # 「stdlib へフォールバックする実装が無い」ことを import 文で検査する
        # （docstring では禁止の理由として xml.etree に言及しているので、素の
        #  部分文字列検査ではなく import 面を見る）。
        assert_source_does_not_import(
            self._source(),
            ["xml.etree", "xml.dom", "xml.sax"],
            context="core/teaching_figures/sanitizer.py",
        )

    def test_lxml_is_the_only_parser_used(self):
        source = self._source()
        assert "from lxml import etree" in source
        assert "ElementTree(" not in source
        assert "minidom" not in source

    def test_safe_parser_flags_are_set(self):
        source = self._source()
        for flag in (
            "resolve_entities=False",
            "load_dtd=False",
            "no_network=True",
            "huge_tree=False",
        ):
            assert flag in source, f"lxml XMLParser の安全フラグ {flag} が失われている"
