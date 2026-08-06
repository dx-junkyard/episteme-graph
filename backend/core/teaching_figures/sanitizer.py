"""SVG サニタイザ — 生成図の**唯一の保存入口**（設計書 §4.1・FG3）。

本機能は「外部（LLM / 教員クライアント）が作ったマークアップを同一オリジンで配信する」
リポジトリ初のケースなので、方針を明示しておく:

1. **パーサは lxml 固定**（``resolve_entities=False`` / ``load_dtd=False`` /
   ``no_network=True`` / ``huge_tree=False``）。stdlib ``xml.etree`` への
   フォールバックは**書かない** — エンティティ攻撃に脆弱なため（設計書 §11-8 の裁定）。
   lxml が無い環境は import 時に気づかせる。
2. **パース前に ``<!DOCTYPE`` / ``<!ENTITY`` を含む入力を無条件拒否**
   （billion laughs / XXE の入口を二重に塞ぐ）。XML 宣言以外の処理命令
   （``<?xml-stylesheet ...?>`` 等）も拒否する（外部スタイルシートの経路）。
3. **要素・属性は許可リスト方式**。``<script>`` / ``<foreignObject>`` / ``<image>`` /
   ``on*`` 属性 / 外部 ``href`` は**除去ではなく拒否**（:class:`SvgRejected`）。
   除去して黙って保存すると「教員がプレビューで見た図」と配信物がズレるため、
   拒否して修正リトライの契機にする（設計書 §4.1）。
4. ``viewBox`` 必須。ルート ``<svg>`` に ``width`` / ``height`` が無ければ viewBox から
   正規化付与する（``<img>`` の intrinsic size 欠落で 300px 既定レンダになるのを防ぐ）。
5. 出力は**検証済みノードだけで組み直した新しいツリー**を直列化する
   （コメントは落ちる・名前空間は SVG に正規化される）。

FastAPI は import しない（core/ 共通ルール）。DB にも触らない純粋関数。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from lxml import etree

# ---------------------------------------------------------------------------
# 名前空間
# ---------------------------------------------------------------------------

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
XML_NS = "http://www.w3.org/XML/1998/namespace"

_ALLOWED_NAMESPACES = frozenset({SVG_NS, XLINK_NS, XML_NS})

# ---------------------------------------------------------------------------
# 許可リスト（設計書 §4.1）
# ---------------------------------------------------------------------------

# `<style>` 要素は v1 では不許可（style **属性**で足りる）。`image` / `foreignObject` /
# `script` / `animate*` / `set` / `filter` 系は列挙しない = 拒否される。
ALLOWED_ELEMENTS = frozenset(
    {
        "svg",
        "g",
        "path",
        "rect",
        "circle",
        "ellipse",
        "line",
        "polyline",
        "polygon",
        "text",
        "tspan",
        "defs",
        "marker",
        "title",
        "desc",
        "linearGradient",
        "radialGradient",
        "stop",
        "clipPath",
        "use",
    }
)

# どの要素にも許される表示・幾何の共通属性。
_COMMON_ATTRS = frozenset(
    {
        "id",
        "class",
        "transform",
        "fill",
        "fill-opacity",
        "fill-rule",
        "stroke",
        "stroke-width",
        "stroke-opacity",
        "stroke-linecap",
        "stroke-linejoin",
        "stroke-dasharray",
        "stroke-dashoffset",
        "stroke-miterlimit",
        "opacity",
        "color",
        "display",
        "visibility",
        "clip-path",
        "clip-rule",
        "paint-order",
        "vector-effect",
        "shape-rendering",
        "marker-start",
        "marker-mid",
        "marker-end",
        # style 属性は url( / expression( / @import を含む値を拒否した上で許可する
        # （_check_style_value）。
        "style",
    }
)

# 文字表現（text / tspan）の属性。
_TEXT_ATTRS = frozenset(
    {
        "x",
        "y",
        "dx",
        "dy",
        "text-anchor",
        "dominant-baseline",
        "alignment-baseline",
        "baseline-shift",
        "font-family",
        "font-size",
        "font-style",
        "font-weight",
        "font-variant",
        "letter-spacing",
        "word-spacing",
        "text-decoration",
        "textLength",
        "lengthAdjust",
        "writing-mode",
        "rotate",
        f"{{{XML_NS}}}space",
    }
)

# 要素固有の追加属性（共通属性との和が許可集合になる）。
_ELEMENT_ATTRS: dict[str, frozenset[str]] = {
    "svg": frozenset(
        {
            "viewBox",
            "width",
            "height",
            "x",
            "y",
            "preserveAspectRatio",
            "version",
            "baseProfile",
            "overflow",
            # ルートにテキスト既定を置く SVG が多いので text 属性も許す。
        }
    )
    | _TEXT_ATTRS,
    "g": _TEXT_ATTRS,
    "path": frozenset({"d", "pathLength"}),
    "rect": frozenset({"x", "y", "width", "height", "rx", "ry"}),
    "circle": frozenset({"cx", "cy", "r"}),
    "ellipse": frozenset({"cx", "cy", "rx", "ry"}),
    "line": frozenset({"x1", "y1", "x2", "y2"}),
    "polyline": frozenset({"points"}),
    "polygon": frozenset({"points"}),
    "text": _TEXT_ATTRS,
    "tspan": _TEXT_ATTRS,
    "defs": frozenset(),
    "marker": frozenset(
        {
            "markerWidth",
            "markerHeight",
            "refX",
            "refY",
            "orient",
            "markerUnits",
            "viewBox",
            "overflow",
            "preserveAspectRatio",
        }
    ),
    "title": frozenset(),
    "desc": frozenset(),
    "linearGradient": frozenset(
        {
            "x1",
            "y1",
            "x2",
            "y2",
            "gradientUnits",
            "gradientTransform",
            "spreadMethod",
            "href",
            f"{{{XLINK_NS}}}href",
        }
    ),
    "radialGradient": frozenset(
        {
            "cx",
            "cy",
            "r",
            "fx",
            "fy",
            "fr",
            "gradientUnits",
            "gradientTransform",
            "spreadMethod",
            "href",
            f"{{{XLINK_NS}}}href",
        }
    ),
    "stop": frozenset({"offset", "stop-color", "stop-opacity"}),
    "clipPath": frozenset({"clipPathUnits"}),
    "use": frozenset(
        {"href", f"{{{XLINK_NS}}}href", "x", "y", "width", "height"}
    ),
}

# 参照属性（値は `#id` 形式の内部参照のみ許可。外部 URL は拒否）。
_REFERENCE_ATTRS = frozenset({"href", f"{{{XLINK_NS}}}href"})

# style 属性値の禁止トークン（外部リソース読み込み・レガシー式評価の経路）。
_FORBIDDEN_STYLE_TOKENS = ("url(", "expression(", "@import", "javascript:")

# パース前に無条件拒否する入力（大文字小文字を無視して検査する）。
_FORBIDDEN_RAW_TOKENS = ("<!doctype", "<!entity")

# XML 宣言（`<?xml version="1.0"?>`）以外の処理命令。`<?xml-stylesheet ...?>` は
# `xml` の直後が `-` なので下記にマッチして拒否される。
_PROCESSING_INSTRUCTION_RE = re.compile(r"<\?(?!xml\s)", re.IGNORECASE)

_VIEWBOX_RE = re.compile(r"[\s,]+")

# 属性値中の `url(...)` の参照先（`fill="url(#g)"` 等の paint 参照）。
_URL_FUNC_RE = re.compile(r"url\(\s*['\"]?([^)'\"]*)", re.IGNORECASE)


class SvgRejected(Exception):
    """SVG がサニタイズを通らなかった（保存しない・422 にマッピングする）。

    ``reason`` は教員にそのまま見せられる日本語の事実文。LLM への修復
    フィードバックにも同じ文を使う（:mod:`core.teaching_figures.generator`）。
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class SanitizedSvg:
    """サニタイズ結果。

    - ``svg``: 保存・配信する SVG 文字列（検証済みノードのみ・SVG 名前空間付き）
    - ``sha256``: ``svg`` の決定論 sha256（配信スナップショットの同一性）
    - ``source_sha256``: **提出原文**の sha256（監査 metadata 用。保存 API は教員
      クライアント由来の svg_source を受けるため「AI 生成物である」ことは検証できない
      — FG5 の最終担保が教員レビューであるという事実をここに残す。設計書 §4.1）
    """

    svg: str
    sha256: str
    source_sha256: str


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _local_name(tag: object) -> str | None:
    """要素タグからローカル名を取り出す（コメント・PI・非 SVG 名前空間は None）。"""
    if not isinstance(tag, str):
        # etree.Comment / etree.ProcessingInstruction は callable な tag を持つ。
        return None
    if tag.startswith("{"):
        namespace, _, local = tag[1:].partition("}")
        if namespace != SVG_NS:
            return None
        return local
    return tag


def _attr_display_name(name: str) -> str:
    """エラーメッセージ用の属性名（名前空間付きは ``xlink:href`` 形式に戻す）。"""
    if name.startswith("{"):
        namespace, _, local = name[1:].partition("}")
        if namespace == XLINK_NS:
            return f"xlink:{local}"
        if namespace == XML_NS:
            return f"xml:{local}"
        return local
    return name


def _check_style_value(element_name: str, value: str) -> None:
    lowered = value.lower()
    for token in _FORBIDDEN_STYLE_TOKENS:
        if token in lowered:
            raise SvgRejected(
                f"<{element_name}> の style 属性に使用できない値（{token}）が含まれています。"
                "外部リソースの読み込みを含む style は保存できません。"
            )


def _check_paint_url_value(element_name: str, attr_name: str, value: str) -> None:
    """``fill="url(#g)"`` のような paint 参照が**内部参照のみ**であることを検査する。

    ``<img>`` コンテキストでは外部参照は元々読み込まれないが、それに依存せず入口で塞ぐ
    （設計書 FG3 の「サーバ側サニタイザを唯一の入口とする」）。
    """
    lowered = value.lower()
    if "javascript:" in lowered:
        raise SvgRejected(
            f"<{element_name}> の {attr_name} に javascript: が含まれています。"
            "スクリプトを含む図は保存できません。"
        )
    for target in _URL_FUNC_RE.findall(value):
        if not target.strip().startswith("#"):
            raise SvgRejected(
                f"<{element_name}> の {attr_name} が外部リソース（url({target[:40]})）を"
                "参照しています。図の中の要素を指す url(#id) 形式のみ使用できます。"
            )


def _check_reference_value(element_name: str, attr_name: str, value: str) -> None:
    if not value.startswith("#"):
        raise SvgRejected(
            f"<{element_name}> の {attr_name} が外部参照（{value[:40]}）になっています。"
            "図の中の要素を指す #id 形式の参照のみ使用できます。"
        )


def _allowed_attrs_for(element_name: str) -> frozenset[str]:
    return _COMMON_ATTRS | _ELEMENT_ATTRS.get(element_name, frozenset())


def _validate_namespaces(element: etree._Element) -> None:
    for prefix, namespace in (element.nsmap or {}).items():
        if namespace not in _ALLOWED_NAMESPACES:
            label = prefix or "既定"
            raise SvgRejected(
                f"使用できない名前空間が宣言されています（{label}: {namespace}）。"
                "SVG（と #id 参照用の xlink）以外の名前空間は保存できません。"
            )


def _clean_attributes(element: etree._Element, element_name: str) -> dict[str, str]:
    allowed = _allowed_attrs_for(element_name)
    cleaned: dict[str, str] = {}
    for raw_name, raw_value in element.attrib.items():
        name = str(raw_name)
        value = str(raw_value)
        display = _attr_display_name(name)

        # on* イベントハンドラは名前空間の有無を問わず拒否（script 実行の経路）。
        if display.lower().startswith("on"):
            raise SvgRejected(
                f"<{element_name}> にイベントハンドラ属性（{display}）が含まれています。"
                "スクリプトを含む図は保存できません。"
            )
        if name not in allowed:
            raise SvgRejected(
                f"<{element_name}> で使用できない属性（{display}）が含まれています。"
                "図形・文字の表示属性のみ使用できます。"
            )
        if name in _REFERENCE_ATTRS:
            _check_reference_value(element_name, display, value)
        if name == "style":
            _check_style_value(element_name, value)
        else:
            # style 属性は url( を一律拒否（上記）。それ以外の属性は paint 参照として
            # url(#id) の内部参照のみ許す。
            _check_paint_url_value(element_name, display, value)
        cleaned[name] = value
    return cleaned


def _build_clean(element: etree._Element, *, is_root: bool = False) -> etree._Element:
    """検証しつつ、SVG 名前空間に正規化した新しいノードを組み立てる（再帰）。

    コメントは出力に含めない（実行可能な内容を持たないため拒否まではしないが、
    保存物には残さない）。処理命令・エンティティ参照は拒否する
    （処理命令は :func:`sanitize_svg` の入力前検査でも塞いでいる）。
    """
    name = _local_name(element.tag)
    if name is None:
        raise SvgRejected(
            "SVG 以外の名前空間の要素、または解釈できない特殊ノードが含まれています。"
        )
    if name not in ALLOWED_ELEMENTS:
        raise SvgRejected(
            f"使用できない要素（<{name}>）が含まれています。"
            "図形・文字・グラデーション等の描画要素のみ使用できます。"
        )
    _validate_namespaces(element)

    # ルートに既定名前空間（xmlns="http://www.w3.org/2000/svg"）を宣言し、子孫は
    # それを継承させる（`ns0:` 接頭辞付きの直列化を避け、`<img>` からそのまま読める形にする）。
    clean = (
        etree.Element(f"{{{SVG_NS}}}{name}", nsmap={None: SVG_NS})
        if is_root
        else etree.Element(f"{{{SVG_NS}}}{name}")
    )
    for attr_name, attr_value in _clean_attributes(element, name).items():
        clean.set(attr_name, attr_value)
    if element.text:
        clean.text = element.text

    last_child: etree._Element | None = None
    for child in element:
        if child.tag is etree.Comment:
            # コメントは保存物に残さない。前後のテキストは失わない（P4 の精神）。
            if child.tail:
                if last_child is not None:
                    last_child.tail = (last_child.tail or "") + child.tail
                else:
                    clean.text = (clean.text or "") + child.tail
            continue
        if not isinstance(child.tag, str):
            raise SvgRejected(
                "SVG に処理命令・エンティティ参照が含まれています。"
                "外部リソースを参照しうる記述を含む図は保存できません。"
            )
        clean_child = _build_clean(child)
        if child.tail:
            clean_child.tail = child.tail
        clean.append(clean_child)
        last_child = clean_child
    return clean


def _parse_viewbox(value: str) -> tuple[float, float, float, float]:
    parts = [p for p in _VIEWBOX_RE.split(value.strip()) if p]
    if len(parts) != 4:
        raise SvgRejected(
            "ルートの <svg> の viewBox が「最小X 最小Y 幅 高さ」の4つの数値になっていません。"
        )
    try:
        numbers = tuple(float(p) for p in parts)
    except ValueError:
        raise SvgRejected(
            "ルートの <svg> の viewBox に数値以外の値が含まれています。"
        ) from None
    if numbers[2] <= 0 or numbers[3] <= 0:
        raise SvgRejected("ルートの <svg> の viewBox の幅・高さが 0 以下です。")
    return numbers  # type: ignore[return-value]


def _format_length(value: float) -> str:
    """viewBox 由来の width/height 文字列（整数なら小数点を付けない）。"""
    if float(value).is_integer():
        return str(int(value))
    return f"{value:g}"


def sanitize_svg(source: str, *, max_bytes: int) -> SanitizedSvg:
    """SVG 文字列を検証し、保存・配信できる形へ正規化して返す。

    Raises:
        SvgRejected: 危険な構造・許可外の要素/属性・viewBox 欠落・サイズ超過。
            ``.reason`` に教員へそのまま出せる日本語の事実文を持つ。
    """
    raw = source if isinstance(source, str) else str(source or "")
    source_sha256 = _sha256(raw)

    if not raw.strip():
        raise SvgRejected("SVG が空です。")

    limit = max(1, int(max_bytes))
    if len(raw.encode("utf-8")) > limit:
        raise SvgRejected(
            f"SVG のサイズが上限（{limit} バイト）を超えています。図を簡略にしてください。"
        )

    lowered = raw.lower()
    for token in _FORBIDDEN_RAW_TOKENS:
        if token in lowered:
            raise SvgRejected(
                f"SVG に使用できない宣言（{token[2:].upper()}）が含まれています。"
                "DOCTYPE / ENTITY 宣言を含む図は保存できません。"
            )
    if _PROCESSING_INSTRUCTION_RE.search(raw):
        raise SvgRejected(
            "SVG に処理命令（<? ... ?>）が含まれています。"
            "外部スタイルシート等を参照する図は保存できません。"
        )

    parser = etree.XMLParser(
        resolve_entities=False,
        load_dtd=False,
        no_network=True,
        huge_tree=False,
    )
    try:
        root = etree.fromstring(raw.encode("utf-8"), parser=parser)
    except etree.XMLSyntaxError as exc:
        raise SvgRejected(f"SVG として解析できませんでした（{exc.msg}）。") from None

    if _local_name(root.tag) != "svg":
        raise SvgRejected("ルート要素が <svg> ではありません。")

    viewbox = root.get("viewBox")
    if not viewbox or not str(viewbox).strip():
        raise SvgRejected(
            "ルートの <svg> に viewBox がありません。viewBox は表示サイズの基準として必須です。"
        )
    _, _, vb_width, vb_height = _parse_viewbox(str(viewbox))

    clean_root = _build_clean(root, is_root=True)

    # `<img>` の intrinsic size 欠落で 300px 既定レンダになるのを防ぐ（設計書 §4.1）。
    if not clean_root.get("width"):
        clean_root.set("width", _format_length(vb_width))
    if not clean_root.get("height"):
        clean_root.set("height", _format_length(vb_height))

    svg = etree.tostring(clean_root, encoding="unicode")
    if len(svg.encode("utf-8")) > limit:
        raise SvgRejected(
            f"SVG のサイズが上限（{limit} バイト）を超えています。図を簡略にしてください。"
        )

    return SanitizedSvg(svg=svg, sha256=_sha256(svg), source_sha256=source_sha256)


__all__ = [
    "ALLOWED_ELEMENTS",
    "SVG_NS",
    "SanitizedSvg",
    "SvgRejected",
    "XLINK_NS",
    "sanitize_svg",
]
