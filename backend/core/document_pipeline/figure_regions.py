"""図領域の幾何推定と画像の情報量判定（figure_image_extraction の下請け・非LLM・決定論）。

設計: docs/features/image_pipeline_knowledge_library_design.md §4-2 / §17（図領域推定の改訂）。

旧実装の問題（2026-09-24 実測: fujimoto_d.pdf）:

- 領域の横幅を **caption の横幅** にしていたため、caption より広い図の両端が切れた
  （Figure 1.2）。
- 領域の上端を **reading order で直前のブロック** の下端にしていたため、order と
  幾何がずれる PDF（GROBID 経路で caption が節末に並ぶ等）ではページ上端へ縮退し、
  本文ごと切り出していた（Figure 1.1）。
- caption 直上の埋め込み画像を無条件に図とみなしていたため、ベクター図に部品として
  置かれた小さな画像（波線の sprite）が図として採用された（Figure 2.1。SMask を落として
  黒い帯になっていた）。

本モジュールの方針:

1. **障害物（本文・見出し・番号付き式・他の caption・柱）を幾何と組版から判定**し、
   reading order を使わない（``analyze_page``）。
2. caption の上（空なら下）の **障害物に挟まれた帯** を種にして、ページの低解像度
   インク画像の **連結成分** を集める（``locate_figure_region``）。成分は帯の外へ
   はみ出してもよい — これが「切り抜いた境界の向こう側に連続する要素があるか」の
   検査そのもので、連続していれば境界を越えて取り込む。障害物やページ端に阻まれて
   連続が切れた辺は ``truncated_edges`` として正直に返す。
3. 画像の情報量を **エッジ密度・インク比・階調エントロピー** で見積もる
   （``assess_information``）。一様な塗りつぶしや空白は ``low_information``。

FastAPI / DB / LLM を import しない（幾何計算 + PyMuPDF。caption の判定だけ classifier の正本を使う）。
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from episteme_graph.agents.document_structure.classifier import looks_like_caption_label

try:
    import fitz  # PyMuPDF
except ImportError:  # pragma: no cover - PyMuPDF is a hard runtime dependency
    fitz = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# 定数（値を変えるときは設計書 §17 に実測を足す）
# ---------------------------------------------------------------------------

# 連結成分を取るためのインク画像の倍率（1.0 = 72dpi）。0.75 で 1px ≈ 1.33pt。
INK_SCALE = 0.75
# 背景とみなす明るさの下限（0-255、これ未満をインクとする）。
INK_THRESHOLD = 235
# 連結とみなす隙間（px, INK_SCALE 上）。横 4px ≈ 5pt / 縦 3px ≈ 4pt。
LINK_GAP_X = 4
LINK_GAP_Y = 3
# 切り出し矩形に付ける余白 (pt)。
REGION_PAD = 4.0
# 帯として扱う最小の高さ (pt)。
MIN_BAND_HEIGHT = 6.0
# 柱（running header / footer）を探すページ上下の割合。
HEADER_ZONE_RATIO = 0.12
FOOTER_ZONE_RATIO = 0.90

# 情報量判定: サムネイルの長辺 (px)。
INFO_THUMB_MAX_SIDE = 160
# 情報量判定の閾値。エントロピーは記録するだけで判定に使わない（線画は白地が大半で
# エントロピーが低く、情報の多い図まで落としてしまうため — 2026-09-24 実測）。
INFO_MIN_INK_RATIO = 0.003
INFO_MIN_EDGE_RATIO = 0.004
# これより小さい（長辺 pt）画像は図として提示しない（行頭記号・アイコンの類）。
MIN_FIGURE_SIDE_PT = 36.0

_CAPTION_START_RE = re.compile(
    r"^\s*(?:Figure|Fig\.?|FIG\.?|Table|TABLE|Tab\.?|図|表)\s*(?:[A-Z]\.?)?\d", re.IGNORECASE
)
_EQ_NUMBER_RE = re.compile(r"\(\s*[A-Z]?\d+(?:\.\d+)*[a-z]?\s*\)\s*$")
_PAGE_NUMBER_RE = re.compile(r"^(?:\d{1,4}|[ivxlcdm]{1,6}|-\s*\d{1,4}\s*-)$", re.IGNORECASE)


def looks_like_figure_caption(text: str) -> bool:
    """"Figure 1.2: ..." は caption、"Figure 1.2 shows ..." は本文。

    判定の正本は classifier の ``looks_like_caption_label``（ラベル直後に小文字の語が続けば
    本文中の図表参照）。GROBID 経路や旧 artifact の caption にも同じ規則を効かせるため、
    抽出側でもここを通す。ラベルで始まらないものは判定しない（True = 従来どおり caption）。
    """
    raw = (text or "").strip()
    if not _CAPTION_START_RE.match(raw):
        return True
    return looks_like_caption_label(raw)


_LABEL_RE = re.compile(
    # 枝番は小文字1文字だけ（"2a"）。直後に小文字が続くなら語の頭（"5.19shows"）なので取らない。
    r"^\s*(?:Figure|Fig\.?|FIG\.?|図)\s*((?:[A-Z]\s*\.?\s*)?\d+(?:\s*[.\-]\s*\d+)*(?:(?-i:[a-z])(?![a-z]))?)(?![\d])",
    re.IGNORECASE,
)


def caption_label_raw(text: str) -> str | None:
    """図ラベルの表記をそのまま返す（空白だけ除く。"Figure 5 . 22" → "5.22"、"Fig. A1." → "A1"）。

    ラベルの直後に語が空白なしで続く文字層（"Figure1.Theflowchart..."）でもラベルだけを取る。
    ラベルで始まらなければ None。
    """
    match = _LABEL_RE.match(text or "")
    if not match:
        return None
    raw = re.sub(r"\s+", "", match.group(1))
    return raw or None


def caption_label(text: str) -> str | None:
    """図ラベルを正規化して返す（"Figure 5.22:" / "Figure 5 . 22" → "5_22"、"Fig. E.1" → "e_1"）。

    ラベルで始まらなければ None。構造化パーサが崩した空白（"5 . 4"）も吸収する。
    """
    raw = caption_label_raw(text)
    if not raw:
        return None
    key = re.sub(r"[^0-9A-Za-z]+", "_", raw).strip("_").lower()
    return key or None


def anchor_caption(
    stats: "DocumentLayoutStats",
    *,
    text: str,
    page: int | None,
    bbox: list[float] | tuple[float, ...] | None,
) -> "CaptionAnchor | None":
    """構造化の caption を、PDF の文字層にある実際の caption に結び付ける。

    構造化パーサ（特に GROBID 経路）の caption は、頁が既定値のまま・別の頁の行に突合・
    本文段落を caption と誤認、が起こる（2026-09-25 実測）。位置の正本は PDF の文字層とし、
    - ラベルが取れれば、同じラベルで始まる文字塊を文書全体から探す（複数なら申告頁に近いもの）。
    - ラベルが取れなければ、申告位置に重なる文字塊がラベルで始まるときだけ採る。
    どちらでも見つからなければ None（図の領域を決める根拠が無い）。
    """
    label = caption_label(text)
    if label:
        candidates = stats.caption_anchors.get(label, [])
        if candidates:
            def score(anchor: CaptionAnchor) -> tuple:
                same_page_overlap = (
                    anchor.page == page and bbox is not None
                    and _box_overlap(anchor.bbox, bbox) > 0
                )
                return (0 if same_page_overlap else 1, abs(anchor.page - (page or anchor.page)))
            return min(candidates, key=score)
        return None
    if not page or not bbox:
        return None
    best: CaptionAnchor | None = None
    best_overlap = 0.0
    for anchors in stats.caption_anchors.values():
        for anchor in anchors:
            if anchor.page != page:
                continue
            overlap = _box_overlap(anchor.bbox, bbox)
            if overlap > best_overlap:
                best, best_overlap = anchor, overlap
    return best


def _box_overlap(a: tuple[float, ...] | list[float], b: tuple[float, ...] | list[float]) -> float:
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    return (ix1 - ix0) * (iy1 - iy0)


# ---------------------------------------------------------------------------
# データ型
# ---------------------------------------------------------------------------


@dataclass
class TextLine:
    bbox: tuple[float, float, float, float]
    text: str
    size: float
    bold: bool
    font: str = ""


@dataclass
class TextBlock:
    bbox: tuple[float, float, float, float]
    lines: list[TextLine]

    @property
    def text(self) -> str:
        return " ".join(line.text for line in self.lines).strip()

    @property
    def chars(self) -> int:
        return sum(len(line.text.strip()) for line in self.lines)

    @property
    def median_size(self) -> float:
        sizes = sorted(line.size for line in self.lines if line.text.strip())
        return sizes[len(sizes) // 2] if sizes else 0.0

    @property
    def main_font(self) -> str:
        fonts = Counter()
        for line in self.lines:
            fonts[line.font] += len(line.text.strip())
        return fonts.most_common(1)[0][0] if fonts else ""

    @property
    def all_bold(self) -> bool:
        lines = [line for line in self.lines if line.text.strip()]
        return bool(lines) and all(line.bold for line in lines)


@dataclass
class DocumentLayoutStats:
    """文書全体の組版統計（本文サイズ・左余白・柱の文言）。"""

    body_size: float = 10.0
    left_margin: float | None = None
    column_width: float | None = None
    # 本文行の左端として頻出する x（2 段組なら各段の左端）。見出しの判定に使う。
    left_margins: list[float] = field(default_factory=list)
    # 本文の書体（本文サイズの長い行で最も多い書体名）。短い本文行の判定に使う。
    body_font: str = ""
    running_keys: set[str] = field(default_factory=set)
    # PDF の文字層にある図 caption（ラベル → 出現箇所）。caption の位置の正本（§18）。
    caption_anchors: dict[str, list["CaptionAnchor"]] = field(default_factory=dict)


@dataclass
class CaptionAnchor:
    """PDF の文字層で「Figure 5.22: ...」のように図ラベルで始まる文字塊。"""

    page: int
    bbox: tuple[float, float, float, float]
    text: str
    label: str


@dataclass
class Obstacle:
    bbox: tuple[float, float, float, float]
    kind: str  # body / heading / equation / caption / header / footer


@dataclass
class PageLayout:
    page_rect: tuple[float, float, float, float]
    obstacles: list[Obstacle]
    header_bottom: float
    footer_top: float
    columns: list[tuple[float, float]]  # 2段組のときだけ2要素（左, 右）
    images: list["ImagePlacement"]
    # obstacles の index -> 元の文字塊（柱・caption 由来は None）
    obstacle_blocks: dict[int, TextBlock | None] = field(default_factory=dict)
    ink: "_InkMask | None" = None


@dataclass
class RegionResult:
    bbox: list[float]
    confidence: float
    side: str  # above / below
    truncated_edges: list[str]
    source: str = "ink_components"
    # 図の中の文章と判明して障害物から外した文字塊の数（両側に図が続いていたもの）。
    demoted_obstacles: int = 0


@dataclass
class InformationAssessment:
    low_information: bool
    reason: str
    ink_ratio: float
    edge_ratio: float
    entropy_bits: float


# ---------------------------------------------------------------------------
# テキストの取り出し
# ---------------------------------------------------------------------------


# 画像データを dict に埋め込まない（TEXT_PRESERVE_IMAGES を外す。付けると画像の多い
# 文書で文字の取り出しが桁違いに遅くなる）。
_TEXT_FLAGS = (
    (fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES) if fitz is not None else 0
)


def _page_text_blocks(page: Any) -> list[TextBlock]:
    try:
        raw = page.get_text("dict", flags=_TEXT_FLAGS)
    except Exception:
        return []
    blocks: list[TextBlock] = []
    for b in raw.get("blocks", []):
        if b.get("type") != 0:
            continue
        lines: list[TextLine] = []
        for line in b.get("lines", []):
            spans = [s for s in line.get("spans", []) if (s.get("text") or "").strip()]
            if not spans:
                continue
            text = "".join(s.get("text", "") for s in spans)
            size = max(float(s.get("size") or 0.0) for s in spans)
            bold = all(int(s.get("flags") or 0) & 16 for s in spans)
            fonts = Counter()
            for sp in spans:
                fonts[sp.get("font") or ""] += len(sp.get("text") or "")
            font = fonts.most_common(1)[0][0] if fonts else ""
            lines.append(TextLine(tuple(line.get("bbox")), text, size, bold, font))
        if lines:
            blocks.append(TextBlock(tuple(b.get("bbox")), lines))
    return blocks


def _running_key(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\d+", "#", (text or "").strip().lower()))


def collect_document_stats(doc: Any, page_numbers: list[int] | None = None) -> DocumentLayoutStats:
    """本文サイズ・左余白・柱の文言を文書全体から集める（決定論）。

    柱の文言は「ページ上端/下端の帯にある1行で、数字を伏せた文言が 3 ページ以上かつ全体の
    1/4 以上に現れる」もの。2 ページ程度の一致は、頁の上端に置かれた図のタイトルや凡例が
    たまたま同じだっただけのことがある（2026-09-25 実測: 2 段組論文で図の上部が柱として
    切り落とされた）。見出しの帯を罫線で区切る版面は ``analyze_page`` が罫線で判定する。
    """
    size_chars: Counter = Counter()
    left_counter: Counter = Counter()
    right_values: list[float] = []
    running: Counter = Counter()
    pages = range(len(doc)) if page_numbers is None else [p - 1 for p in page_numbers]

    per_page_lines: list[list[TextLine]] = []
    stats_anchors: dict[str, list[CaptionAnchor]] = {}
    for idx in pages:
        if idx < 0 or idx >= len(doc):
            continue
        page = doc[idx]
        height = page.rect.height
        blocks = _page_text_blocks(page)
        for block in blocks:
            text = block.text
            label = caption_label(text)
            if label and looks_like_figure_caption(text):
                stats_anchors.setdefault(label, []).append(
                    CaptionAnchor(idx + 1, tuple(block.bbox), text, label)
                )
        lines = [line for block in blocks for line in block.lines]
        per_page_lines.append(lines)
        seen_keys: set[str] = set()
        for line in lines:
            n = len(line.text.strip())
            size_chars[round(line.size * 2) / 2] += n
            if line.bbox[1] <= height * HEADER_ZONE_RATIO or line.bbox[3] >= height * FOOTER_ZONE_RATIO:
                key = _running_key(line.text)
                if key and key not in seen_keys:
                    seen_keys.add(key)
                    running[key] += 1

    stats = DocumentLayoutStats()
    if size_chars:
        stats.body_size = float(size_chars.most_common(1)[0][0])
    font_counter: Counter = Counter()
    for lines in per_page_lines:
        for line in lines:
            if abs(line.size - stats.body_size) <= 0.6 and len(line.text.strip()) >= 40:
                left_counter[round(line.bbox[0])] += 1
                right_values.append(line.bbox[2])
                font_counter[line.font] += len(line.text.strip())
    if font_counter:
        stats.body_font = font_counter.most_common(1)[0][0]
    if left_counter:
        stats.left_margin = float(left_counter.most_common(1)[0][0])
        top_count = left_counter.most_common(1)[0][1]
        stats.left_margins = sorted(
            float(x) for x, c in left_counter.items() if c >= top_count * 0.3
        )
    if right_values and stats.left_margin is not None:
        right_values.sort()
        right = right_values[int(len(right_values) * 0.9)]
        stats.column_width = max(1.0, right - stats.left_margin)
    n_pages = max(1, len(per_page_lines))
    min_repeat = max(3, math.ceil(n_pages * 0.25))
    # 数字だけの行（ページ番号・軸の目盛り）は文言の繰り返しとして数えない。ページ番号は
    # analyze_page が「そのページのいちばん上/下の文字」であることを確かめて柱にする。
    stats.running_keys = {
        key for key, count in running.items()
        if count >= min_repeat and re.search(r"[^\W\d_#]", key)
    }
    stats.caption_anchors = stats_anchors
    return stats


# ---------------------------------------------------------------------------
# ページの障害物
# ---------------------------------------------------------------------------


def _is_visible_enclosure(drawing: dict, page_area: float) -> bool:
    rect = drawing.get("rect")
    if rect is None or rect.width < 20 or rect.height < 12:
        return False
    if rect.width * rect.height > page_area * 0.6:
        return False
    fill = drawing.get("fill")
    color = drawing.get("color")
    if color is not None:
        return True
    return fill is not None and tuple(fill)[:3] != (1.0, 1.0, 1.0)


def _contains(outer: tuple[float, ...], inner: tuple[float, ...], margin: float = 0.5) -> bool:
    return (
        outer[0] - margin <= inner[0] and outer[1] - margin <= inner[1]
        and outer[2] + margin >= inner[2] and outer[3] + margin >= inner[3]
    )


def _classify_block(block: TextBlock, stats: DocumentLayoutStats, column_width: float) -> str | None:
    """本文・見出し・番号付き式・caption なら種別を返す。図の中のラベルは None。"""
    text = block.text
    if _CAPTION_START_RE.match(text) and looks_like_figure_caption(text):
        return "caption"
    width = block.bbox[2] - block.bbox[0]
    size = block.median_size
    n_lines = len(block.lines)
    body_like_size = stats.body_size * 0.9 <= size <= stats.body_size * 1.15
    if n_lines >= 2 and body_like_size and width >= column_width * 0.45 and block.chars >= 60:
        return "body"
    if n_lines == 1 and body_like_size and block.chars >= 40 and width >= column_width * 0.5:
        return "body"
    margins = stats.left_margins or ([stats.left_margin] if stats.left_margin is not None else [])
    # 短い本文行（ディスプレイ数式の後の "where ..." / "and are plotted in Fig. 5." など）:
    # 本文の書体・本文サイズで、段の左端から始まる行。図の中の文字は図の書体か、段の左端に
    # 揃わないことが多い（揃ってしまった場合は図の連続性の検査で障害物から外れる）。
    if (
        stats.body_font and block.main_font == stats.body_font and body_like_size
        and n_lines <= 3 and block.chars >= 12
        and any(abs(block.bbox[0] - m) <= 3.0 for m in margins)
    ):
        return "body"
    if (
        block.all_bold and n_lines <= 3 and size >= stats.body_size * 0.95 and block.chars <= 160
        and any(abs(block.bbox[0] - m) <= 6.0 for m in margins)
    ):
        return "heading"
    last = block.lines[-1].text.strip()
    if body_like_size and _EQ_NUMBER_RE.search(last) and width >= column_width * 0.3:
        return "equation"
    return None


def analyze_page(
    page: Any,
    stats: DocumentLayoutStats,
    *,
    caption_bboxes: list[list[float]] | None = None,
) -> PageLayout:
    """ページの障害物（図に含めてはいけない領域）と段組・画像配置を求める。"""
    rect = page.rect
    page_rect = (rect.x0, rect.y0, rect.x1, rect.y1)
    page_area = max(rect.width * rect.height, 1.0)
    column_width = stats.column_width or rect.width * 0.7

    try:
        drawings = page.get_drawings()
    except Exception:
        drawings = []
    enclosures = [tuple(d["rect"]) for d in drawings if _is_visible_enclosure(d, page_area)]

    blocks = _page_text_blocks(page)
    obstacles: list[Obstacle] = []
    obstacle_blocks: dict[int, TextBlock | None] = {}
    header_bottom = rect.y0
    footer_top = rect.y1
    header_lines: list[tuple[float, float, float, float]] = []
    footer_lines: list[tuple[float, float, float, float]] = []

    text_top = min((b.bbox[1] for b in blocks), default=rect.y1)
    text_bottom = max((b.bbox[3] for b in blocks), default=rect.y0)
    for block in blocks:
        # ページ番号はページのいちばん上か下の文字のときだけ柱（軸の目盛りの数字と区別する）。
        extreme = block.bbox[1] <= text_top + 1.0 or block.bbox[3] >= text_bottom - 1.0
        running_lines = [
            line for line in block.lines
            if _running_key(line.text) in stats.running_keys
            or (extreme and _PAGE_NUMBER_RE.match(line.text.strip()))
        ]
        if running_lines and len(running_lines) == len(block.lines):
            if block.bbox[3] <= rect.y0 + rect.height * HEADER_ZONE_RATIO:
                header_lines.append(block.bbox)
                continue
            if block.bbox[1] >= rect.y0 + rect.height * FOOTER_ZONE_RATIO:
                footer_lines.append(block.bbox)
                continue
        kind = _classify_block(block, stats, column_width)
        if kind is None:
            continue
        if kind != "caption" and any(_contains(enc, block.bbox, margin=1.0) for enc in enclosures):
            # 枠線で囲まれた文章は図の中の説明文（本文ではない）。
            continue
        obstacle_blocks[len(obstacles)] = block
        obstacles.append(Obstacle(tuple(block.bbox), kind))

    _extend_caption_continuations(blocks, obstacles, obstacle_blocks)

    if header_lines:
        header_bottom = max(b[3] for b in header_lines)
    if footer_lines:
        footer_top = min(b[1] for b in footer_lines)
    # 柱の罫線: ページ上端/下端の帯にある細い横線で、すぐ上（下端なら下）に文字があるもの。
    # 柱の文言が文書内で繰り返されなくても（学位論文の節ごとの見出し）罫線で区切られていれば柱。
    top_zone = rect.y0 + rect.height * HEADER_ZONE_RATIO
    bottom_zone = rect.y0 + rect.height * FOOTER_ZONE_RATIO
    for d in drawings:
        r = d.get("rect")
        if r is None or r.height > 2.0 or r.width < rect.width * 0.4:
            continue
        if r.y1 <= top_zone and any(
            r.y0 - 14.0 <= b.bbox[3] <= r.y0 + 2.0 and b.bbox[0] < r.x1 and b.bbox[2] > r.x0
            for b in blocks
        ):
            header_bottom = max(header_bottom, r.y1)
        if r.y0 >= bottom_zone and any(
            r.y1 - 2.0 <= b.bbox[1] <= r.y1 + 14.0 and b.bbox[0] < r.x1 and b.bbox[2] > r.x0
            for b in blocks
        ):
            footer_top = min(footer_top, r.y0)
    if header_bottom > rect.y0:
        obstacles.append(Obstacle((rect.x0, rect.y0, rect.x1, header_bottom + 0.5), "header"))
    if footer_top < rect.y1:
        obstacles.append(Obstacle((rect.x0, footer_top - 0.5, rect.x1, rect.y1), "footer"))

    for cb in caption_bboxes or []:
        if cb and len(cb) == 4:
            obstacles.append(Obstacle(tuple(float(v) for v in cb), "caption"))

    columns = _detect_columns(obstacles, rect)

    images = image_placements(page)

    return PageLayout(
        page_rect, obstacles, header_bottom, footer_top, columns, images,
        obstacle_blocks=obstacle_blocks,
    )


@dataclass
class ImagePlacement:
    """ページ上の画像1配置（同じ画像を複数箇所に置けば配置ごとに1つ）。"""

    index: int
    bbox: tuple[float, float, float, float]
    width: int
    height: int
    xref: int = 0  # 寸法から一意に引けたときだけ（引けなければ 0）
    size: int = 0


def _extend_caption_continuations(
    blocks: list[TextBlock],
    obstacles: list[Obstacle],
    obstacle_blocks: dict[int, TextBlock | None],
) -> None:
    """caption の続きの文字塊（すぐ下・同じ左端・同じ字の大きさ）を caption の障害物に含める。

    文字層では長い caption が複数の塊に分かれることがあり、続きの塊（"slice. The red, blue ..."）が
    障害物にならないと、その下の図の領域に caption の尾が入る（2026-09-25 実測: 2 段組論文）。
    """
    claimed = {id(b) for b in obstacle_blocks.values() if b is not None}
    for idx in [i for i, o in enumerate(obstacles) if o.kind == "caption"]:
        cap_block = obstacle_blocks.get(idx)
        if cap_block is None:
            continue
        box = list(obstacles[idx].bbox)
        size = cap_block.median_size
        grew = True
        while grew:
            grew = False
            for block in blocks:
                if id(block) in claimed:
                    continue
                b = block.bbox
                if (
                    0 <= b[1] - box[3] <= 4.0
                    and abs(b[0] - box[0]) <= 3.0
                    and abs(block.median_size - size) <= 0.5
                    and not _CAPTION_START_RE.match(block.text)
                ):
                    box = [min(box[0], b[0]), box[1], max(box[2], b[2]), b[3]]
                    claimed.add(id(block))
                    grew = True
        obstacles[idx] = Obstacle(tuple(box), "caption")


def image_placements(page: Any) -> list[ImagePlacement]:
    """ページ上の画像配置の一覧（ページ内に切り詰めた矩形）。

    ``page.get_image_rects`` / ``get_image_info(xrefs=True)`` は xref を引くために画像を
    ハッシュし直し、大きな画像の多い文書で桁違いに遅い（2026-09-24 実測: 190 頁で 17 秒）。
    ``get_image_info()`` の1回で配置を取り、xref は ``get_images`` の寸法が一意に一致する
    ときだけ付ける。
    """
    rect = page.rect
    result: list[ImagePlacement] = []
    try:
        infos = page.get_image_info()
    except Exception:
        return result
    by_dims: dict[tuple[int, int], list[int]] = {}
    try:
        for img in page.get_images(full=True):
            by_dims.setdefault((int(img[2]), int(img[3])), []).append(int(img[0]))
    except Exception:
        pass
    for i, info in enumerate(infos):
        bbox = info.get("bbox")
        if not bbox:
            continue
        clipped = fitz.Rect(bbox) & rect
        if clipped.is_empty or clipped.width <= 0 or clipped.height <= 0:
            continue
        w, h = int(info.get("width") or 0), int(info.get("height") or 0)
        xrefs = by_dims.get((w, h), [])
        result.append(ImagePlacement(
            index=i,
            bbox=(clipped.x0, clipped.y0, clipped.x1, clipped.y1),
            width=w,
            height=h,
            xref=xrefs[0] if len(set(xrefs)) == 1 else 0,
            size=int(info.get("size") or 0),
        ))
    return result


def _detect_columns(obstacles: list[Obstacle], rect: Any) -> list[tuple[float, float]]:
    mid = (rect.x0 + rect.x1) / 2
    body = [o.bbox for o in obstacles if o.kind == "body"]
    left = [b for b in body if b[2] <= mid + 4]
    right = [b for b in body if b[0] >= mid - 4]
    if left and right:
        return [
            (min(b[0] for b in left), max(b[2] for b in left)),
            (min(b[0] for b in right), max(b[2] for b in right)),
        ]
    return []


# ---------------------------------------------------------------------------
# インク画像と連結成分（run-length union-find）
# ---------------------------------------------------------------------------

_INK_TABLE = bytes(1 if v < INK_THRESHOLD else 0 for v in range(256))
_INK_RUN_RE = re.compile(b"\x01+")


class _InkMask:
    """ページの低解像度グレースケールからインクの横ランを持つ。"""

    def __init__(self, page: Any, scale: float = INK_SCALE, renderer: Any = None):
        self.scale = scale
        pix = (renderer or page).get_pixmap(
            matrix=fitz.Matrix(scale, scale), colorspace=fitz.csGRAY, alpha=False,
        )
        self.width = pix.width
        self.height = pix.height
        self.x0 = page.rect.x0
        self.y0 = page.rect.y0
        samples = pix.samples
        stride = pix.stride
        self.rows: list[list[tuple[int, int]]] = []
        for y in range(self.height):
            row = samples[y * stride: y * stride + self.width].translate(_INK_TABLE)
            self.rows.append([(m.start(), m.end() - 1) for m in _INK_RUN_RE.finditer(row)])

    def to_px(self, bbox: tuple[float, ...]) -> tuple[int, int, int, int]:
        s = self.scale
        return (
            max(0, int(math.floor((bbox[0] - self.x0) * s))),
            max(0, int(math.floor((bbox[1] - self.y0) * s))),
            min(self.width - 1, int(math.ceil((bbox[2] - self.x0) * s))),
            min(self.height - 1, int(math.ceil((bbox[3] - self.y0) * s))),
        )

    def to_pt(self, box: tuple[int, int, int, int]) -> list[float]:
        s = self.scale
        return [
            self.x0 + box[0] / s,
            self.y0 + box[1] / s,
            self.x0 + (box[2] + 1) / s,
            self.y0 + (box[3] + 1) / s,
        ]


@dataclass
class _Component:
    box: list[int]  # x0, y0, x1, y1 (px, inclusive)
    # 接している障害物: index -> 障害物がこの成分のどちら側にあるか（top/bottom/left/right）
    touches: dict[int, set[str]] = field(default_factory=dict)
    touches_page: set[str] = field(default_factory=set)
    pixels: int = 0

    @property
    def width(self) -> int:
        return self.box[2] - self.box[0] + 1

    @property
    def height(self) -> int:
        return self.box[3] - self.box[1] + 1


def _subtract_intervals(
    run: tuple[int, int],
    blocked: list[tuple[int, int, int]],
) -> list[tuple[int, int, int | None, int | None]]:
    """run から blocked 区間 (x0, x1, 障害物 index) を除いた断片を返す。

    断片ごとに左端/右端で接している障害物の index（無ければ None）を付ける。
    """
    pieces: list[tuple[int, int, int | None, int | None]] = [(run[0], run[1], None, None)]
    for b0, b1, oi in blocked:
        nxt: list[tuple[int, int, int | None, int | None]] = []
        for a0, a1, lt, rt in pieces:
            if b1 < a0 or b0 > a1:
                nxt.append((a0, a1, lt, rt))
                continue
            if a0 < b0:
                nxt.append((a0, b0 - 1, lt, oi))
            if a1 > b1:
                nxt.append((b1 + 1, a1, oi, rt))
        pieces = nxt
    return pieces


def _connected_components(
    mask: _InkMask,
    obstacles_px: list[tuple[int, int, int, int]],
) -> list[_Component]:
    """障害物を除いたインクの連結成分（隙間 LINK_GAP_X/Y までを連結とみなす）。

    障害物の矩形の中のインクは消してから数えるので、障害物を貫いて続く線は障害物の縁で
    切れる。その縁に接している成分には ``touches`` に障害物と向きを記録する
    （= 境界の向こうへ連続していた証拠）。
    """
    parent: list[int] = []

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    run_info: list[tuple[int, int, int]] = []  # (y, x0, x1)
    run_touch: list[list[tuple[int, str]]] = []
    recent: list[list[int]] = []  # 直近 LINK_GAP_Y+1 行の run index
    indexed = list(enumerate(obstacles_px))
    for y, runs in enumerate(mask.rows):
        blocked = sorted((o[0], o[2], i) for i, o in indexed if o[1] <= y <= o[3])
        above = [(i, o) for i, o in indexed if o[3] == y - 1]
        below = [(i, o) for i, o in indexed if o[1] == y + 1]
        # 横の隙間を詰めて断片を束ねる（障害物で切れた端の情報は保持する）。
        pieces: list[tuple[int, int, list[tuple[int, str]]]] = []
        for run in runs:
            parts = _subtract_intervals(run, blocked) if blocked else [(run[0], run[1], None, None)]
            for x0, x1, lt, rt in parts:
                touch: list[tuple[int, str]] = []
                if lt is not None:
                    touch.append((lt, "left"))
                if rt is not None:
                    touch.append((rt, "right"))
                if pieces and x0 - pieces[-1][1] - 1 <= LINK_GAP_X:
                    p0, _, pt = pieces[-1]
                    pieces[-1] = (p0, x1, pt + touch)
                else:
                    pieces.append((x0, x1, touch))
        current: list[int] = []
        for x0, x1, touch in pieces:
            idx = len(run_info)
            parent.append(idx)
            touch = list(touch)
            for i, o in above:
                if o[0] <= x1 and o[2] >= x0:
                    touch.append((i, "top"))
            for i, o in below:
                if o[0] <= x1 and o[2] >= x0:
                    touch.append((i, "bottom"))
            run_info.append((y, x0, x1))
            run_touch.append(touch)
            for prev_row in recent:
                for j in prev_row:
                    _, px0, px1 = run_info[j]
                    if px0 - LINK_GAP_X <= x1 and px1 + LINK_GAP_X >= x0:
                        union(idx, j)
            current.append(idx)
        recent.append(current)
        if len(recent) > LINK_GAP_Y + 1:
            recent.pop(0)

    comps: dict[int, _Component] = {}
    for idx, (y, x0, x1) in enumerate(run_info):
        root = find(idx)
        comp = comps.get(root)
        if comp is None:
            comp = _Component(box=[x0, y, x1, y])
            comps[root] = comp
        else:
            comp.box[0] = min(comp.box[0], x0)
            comp.box[1] = min(comp.box[1], y)
            comp.box[2] = max(comp.box[2], x1)
            comp.box[3] = max(comp.box[3], y)
        comp.pixels += x1 - x0 + 1
        for oi, side in run_touch[idx]:
            comp.touches.setdefault(oi, set()).add(side)
        if x0 <= 0:
            comp.touches_page.add("left")
        if x1 >= mask.width - 1:
            comp.touches_page.add("right")
        if y <= 0:
            comp.touches_page.add("top")
        if y >= mask.height - 1:
            comp.touches_page.add("bottom")
    return list(comps.values())


# ---------------------------------------------------------------------------
# 図領域の推定
# ---------------------------------------------------------------------------

# 図の中の文章を本文と誤認したとき、境界の向こうに図が続いていれば障害物から外す。
# 外してよいのは短い文字塊だけ（長い段落は図を貫いていても本文として残す）。
_DEMOTABLE_KINDS = frozenset({"body", "equation", "heading"})
_DEMOTABLE_MAX_LINES = 6
_DEMOTABLE_MAX_CHARS = 320
# 選んだ成分のうち、障害物に接する細い切れ端（表の罫・下線の残り）を捨てる閾値。
_STRAY_MAX_THICKNESS_PX = 4
_STRAY_MAX_PIXEL_SHARE = 0.10
_OPPOSITE = {"top": "bottom", "bottom": "top", "left": "right", "right": "left"}


def _overlap_x(a: tuple[float, ...], x0: float, x1: float) -> bool:
    return a[0] < x1 and a[2] > x0


def _caption_window(layout: PageLayout, cap: tuple[float, ...]) -> tuple[float, float]:
    """caption が属する段の横範囲（1段組はページ幅 — 図は本文幅より広くてよい）。"""
    px0, _, px1, _ = layout.page_rect
    if len(layout.columns) == 2:
        (l0, l1), (r0, r1) = layout.columns
        pad = 6.0
        if cap[2] <= l1 + pad:
            return (max(px0, l0 - pad * 3), min(px1, (l1 + r0) / 2))
        if cap[0] >= r0 - pad:
            return (max(px0, (l1 + r0) / 2), min(px1, r1 + pad * 3))
    return (px0, px1)


def _band(
    obstacles: list[Obstacle],
    page_rect: tuple[float, float, float, float],
    cap: tuple[float, ...],
    window: tuple[float, float],
    side: str,
) -> tuple[float, float] | None:
    wx0, wx1 = window
    others = [o for o in obstacles if not _contains(o.bbox, cap, 1.0) and not _contains(cap, o.bbox, 1.0)]
    if side == "above":
        limits = [o.bbox[3] for o in others if o.bbox[3] <= cap[1] + 1.0 and _overlap_x(o.bbox, wx0, wx1)]
        top = max(limits) if limits else page_rect[1]
        bottom = cap[1]
    else:
        limits = [o.bbox[1] for o in others if o.bbox[1] >= cap[3] - 1.0 and _overlap_x(o.bbox, wx0, wx1)]
        top = cap[3]
        bottom = min(limits) if limits else page_rect[3]
    if bottom - top < MIN_BAND_HEIGHT:
        return None
    return (top, bottom)


def _select_in_band(
    components: list[_Component],
    band_px: tuple[int, int, int, int],
    cap_px: tuple[int, int, int, int],
    body_px: list[tuple[int, int, int, int]],
) -> list[_Component]:
    selected: list[_Component] = []
    for comp in components:
        b = comp.box
        if b[2] < band_px[0] or b[0] > band_px[2] or b[3] < band_px[1] or b[1] > band_px[3]:
            continue
        if _box_contains(b, cap_px) and any(_box_contains(b, bp) for bp in body_px):
            # ページ枠（caption も本文も囲む罫）は図ではない。
            continue
        selected.append(comp)
    return selected


def _drop_strays(selected: list[_Component], cap_index: int) -> list[_Component]:
    """障害物に接する細い切れ端（表の下罫・下線の残り）を捨てる。"""
    if len(selected) <= 1:
        return selected
    total = sum(c.pixels for c in selected) or 1
    kept = [
        c for c in selected
        if not (
            any(oi != cap_index for oi in c.touches)
            and min(c.width, c.height) <= _STRAY_MAX_THICKNESS_PX
            and c.pixels / total <= _STRAY_MAX_PIXEL_SHARE
        )
    ]
    return kept or selected


def _demotable_blockers(
    selected: list[_Component],
    components: list[_Component],
    obstacles: list[Obstacle],
    blocks: dict[int, TextBlock | None],
    cap_index: int,
) -> set[int]:
    """選んだ成分と、その反対側の別の成分の両方が接している短い文字塊の障害物。

    「図 → 障害物 → 図」と連続しているなら、その障害物は図の中の文章（ラベル・説明）で
    あって本文ではない。境界の向こう側に連続する要素があるかの検査を障害物に適用する。
    """
    selected_ids = {id(c) for c in selected}
    result: set[int] = set()
    for comp in selected:
        for oi, sides in comp.touches.items():
            if oi == cap_index or oi >= len(obstacles) or obstacles[oi].kind not in _DEMOTABLE_KINDS:
                continue
            block = blocks.get(oi)
            if block is not None and (
                len(block.lines) > _DEMOTABLE_MAX_LINES or block.chars > _DEMOTABLE_MAX_CHARS
            ):
                continue
            wanted = {_OPPOSITE[s] for s in sides}
            for other in components:
                if id(other) in selected_ids:
                    continue
                if wanted & other.touches.get(oi, set()):
                    result.add(oi)
                    break
    return result


def locate_figure_region(
    page: Any,
    layout: PageLayout,
    caption_bbox: list[float] | tuple[float, ...],
    *,
    renderer: Any = None,
) -> RegionResult | None:
    """caption に対応する図領域を、障害物に挟まれた帯のインク連結成分から求める。

    帯（caption の上。空なら下）に掛かる連結成分をすべて集め、その外接矩形を図とする。
    成分は帯の外へはみ出してよい（境界の向こうへ連続する要素は取り込む）。図の中の
    文章を本文と誤認していた場合は、その文字塊の両側に図が続いていることを確かめて
    障害物から外し、やり直す。連続が障害物・ページ端で切れたままの辺は
    ``truncated_edges`` に入れ、confidence を下げる。成分が無ければ None
    （空白を図として提示しない）。``renderer`` に ``page.get_displaylist()`` を渡すと
    ページの描画（大きな画像の復号）を使い回せる。
    """
    if fitz is None or not caption_bbox or len(caption_bbox) != 4:
        return None
    cap = tuple(float(v) for v in caption_bbox)
    if layout.ink is None:
        layout.ink = _InkMask(page, renderer=renderer)
    mask = layout.ink
    window = _caption_window(layout, cap)
    obstacles = list(layout.obstacles)
    blocks = dict(layout.obstacle_blocks)

    for side in ("above", "below"):
        demoted: set[int] = set()
        for _ in range(4):
            active = [o for i, o in enumerate(obstacles) if i not in demoted]
            active_idx = [i for i in range(len(obstacles)) if i not in demoted]
            band = _band(active, layout.page_rect, cap, window, side)
            if band is None:
                break
            obstacles_px = [mask.to_px(o.bbox) for o in active]
            cap_px = mask.to_px(cap)
            obstacles_px.append(cap_px)
            cap_index = len(obstacles_px) - 1
            components = _connected_components(mask, obstacles_px)
            body_px = [mask.to_px(o.bbox) for o in active if o.kind == "body"]
            band_px = mask.to_px((window[0], band[0], window[1], band[1]))
            selected = _select_in_band(components, band_px, cap_px, body_px)
            if not selected:
                break
            local_blocks = {k: blocks.get(orig) for k, orig in enumerate(active_idx)}
            blockers = _demotable_blockers(selected, components, active, local_blocks, cap_index)
            if blockers:
                demoted |= {active_idx[k] for k in blockers}
                continue
            selected = _drop_strays(selected, cap_index)
            return _region_from_selection(
                mask, layout, cap, cap_px, cap_index, side, selected, active, demoted
            )
    return None


def _region_from_selection(
    mask: _InkMask,
    layout: PageLayout,
    cap: tuple[float, ...],
    cap_px: tuple[int, int, int, int],
    cap_index: int,
    side: str,
    selected: list[_Component],
    active: list[Obstacle],
    demoted: set[int],
) -> RegionResult:
    box = (
        min(c.box[0] for c in selected), min(c.box[1] for c in selected),
        max(c.box[2] for c in selected), max(c.box[3] for c in selected),
    )
    bbox_pt = mask.to_pt(box)
    px0, py0, px1, py1 = layout.page_rect
    bbox_pt = [
        max(px0, bbox_pt[0] - REGION_PAD), max(py0, bbox_pt[1] - REGION_PAD),
        min(px1, bbox_pt[2] + REGION_PAD), min(py1, bbox_pt[3] + REGION_PAD),
    ]
    # 余白が caption に食い込まないようにする。
    if side == "above":
        bbox_pt[3] = min(bbox_pt[3], max(bbox_pt[1] + 1.0, cap[1] - 0.5))
    else:
        bbox_pt[1] = max(bbox_pt[1], min(bbox_pt[3] - 1.0, cap[3] + 0.5))
    truncated: set[str] = set()
    for comp in selected:
        for oi, sides in comp.touches.items():
            if oi == cap_index:
                continue  # 図と caption が近いのは正常
            truncated |= sides
        truncated |= comp.touches_page
    confidence = 0.9 if side == "above" else 0.75
    if truncated:
        confidence -= 0.3
    return RegionResult(
        bbox=[round(v, 2) for v in bbox_pt],
        confidence=round(max(0.05, confidence), 2),
        side=side,
        truncated_edges=sorted(truncated),
        demoted_obstacles=len(demoted),
    )


def _box_contains(outer: list[int] | tuple[int, ...], inner: tuple[int, ...]) -> bool:
    return outer[0] <= inner[0] and outer[1] <= inner[1] and outer[2] >= inner[2] and outer[3] >= inner[3]


def _overlap_area(a: tuple[float, ...] | list[float], b: tuple[float, ...] | list[float]) -> float:
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    return (ix1 - ix0) * (iy1 - iy0)


def rect_inside_ratio(inner: tuple[float, ...] | list[float], outer: tuple[float, ...] | list[float]) -> float:
    """inner の面積のうち outer に含まれる割合。"""
    area = max((inner[2] - inner[0]) * (inner[3] - inner[1]), 1e-6)
    return _overlap_area(inner, outer) / area


def single_image_covering(layout: PageLayout, bbox: list[float], min_cover: float = 0.9) -> ImagePlacement | None:
    """図領域の min_cover 以上を1つの画像配置が覆うならその配置。

    そのときだけ図を「埋め込み画像そのもの」とみなす。部品として置かれた小さな画像
    （ベクター図中の sprite 等）は図の代わりにしない。
    """
    region_area = max((bbox[2] - bbox[0]) * (bbox[3] - bbox[1]), 1e-6)
    best: ImagePlacement | None = None
    best_cover = 0.0
    for placement in layout.images:
        cover = _overlap_area(placement.bbox, bbox) / region_area
        if cover > best_cover:
            best_cover = cover
            best = placement
    if best is not None and best_cover >= min_cover:
        return best
    return None


def visible_content_bbox(renderer: Any, bbox: list[float] | tuple[float, ...], scale: float = 0.5) -> list[float] | None:
    """bbox の中で実際にインクが描かれている範囲（ページ座標）。何も描かれていなければ None。

    画像の配置矩形はクリップパスで切り抜かれて一部しか見えないことがある（埋め込まれた
    別 PDF の図など）。見えている範囲で判定しないと、図の断片を別の図として拾ってしまう。
    """
    if fitz is None or not bbox:
        return None
    try:
        rect = fitz.Rect(*bbox)
        if rect.is_empty:
            return None
        pix = renderer.get_pixmap(
            matrix=fitz.Matrix(scale, scale), clip=rect, colorspace=fitz.csGRAY, alpha=False,
        )
    except Exception:
        return None
    w, h, stride, samples = pix.width, pix.height, pix.stride, pix.samples
    xs0, ys0, xs1, ys1 = w, h, -1, -1
    for y in range(h):
        row = samples[y * stride: y * stride + w].translate(_INK_TABLE)
        first = row.find(b"\x01")
        if first < 0:
            continue
        last = row.rfind(b"\x01")
        xs0, xs1 = min(xs0, first), max(xs1, last)
        ys0 = min(ys0, y)
        ys1 = y
    if xs1 < 0:
        return None
    sx = rect.width / max(w, 1)
    sy = rect.height / max(h, 1)
    return [
        rect.x0 + xs0 * sx, rect.y0 + ys0 * sy,
        min(rect.x1, rect.x0 + (xs1 + 1) * sx), min(rect.y1, rect.y0 + (ys1 + 1) * sy),
    ]


# ---------------------------------------------------------------------------
# 情報量の見積もり
# ---------------------------------------------------------------------------


def assess_information(pix: Any) -> InformationAssessment:
    """画像の「図としての情報量」を簡易に見積もる（決定論・非LLM）。

    長辺 ``INFO_THUMB_MAX_SIDE`` px のグレースケールに縮めて、
    インク比（背景と異なる画素の割合）・エッジ密度（隣接画素の大きな段差の割合）・
    16 階調のエントロピーを測る。インク比かエッジ密度が閾値未満なら
    ``low_information``（空白・一様な塗りつぶし・帯）。
    """
    try:
        gray = pix if (pix.n == 1 and not pix.alpha) else fitz.Pixmap(fitz.csGRAY, pix)
        if gray.alpha:
            gray = fitz.Pixmap(gray, 0)
        longest = max(gray.width, gray.height)
        factor = 0
        while longest >> (factor + 1) >= INFO_THUMB_MAX_SIDE:
            factor += 1
        if factor:
            gray = fitz.Pixmap(gray)
            gray.shrink(factor)
        w, h, stride = gray.width, gray.height, gray.stride
        samples = gray.samples
    except Exception:
        return InformationAssessment(False, "unmeasured", 0.0, 0.0, 0.0)
    if w < 2 or h < 2:
        return InformationAssessment(True, "too_small", 0.0, 0.0, 0.0)

    rows = [samples[y * stride: y * stride + w] for y in range(h)]
    hist = [0] * 16
    for row in rows:
        for v in row:
            hist[v >> 4] += 1
    total = w * h
    background_bin = max(range(16), key=lambda i: hist[i])
    ink = total - hist[background_bin] - (hist[background_bin - 1] if background_bin > 0 else 0) - (
        hist[background_bin + 1] if background_bin < 15 else 0
    )
    ink_ratio = max(0, ink) / total
    entropy = -sum((c / total) * math.log2(c / total) for c in hist if c)
    edges = 0
    pairs = 0
    for y in range(h):
        row = rows[y]
        below = rows[y + 1] if y + 1 < h else None
        for x in range(w):
            v = row[x]
            if x + 1 < w:
                pairs += 1
                if abs(v - row[x + 1]) > 40:
                    edges += 1
            if below is not None:
                pairs += 1
                if abs(v - below[x]) > 40:
                    edges += 1
    edge_ratio = edges / max(pairs, 1)

    reason = ""
    if ink_ratio < INFO_MIN_INK_RATIO:
        reason = "blank"
    elif edge_ratio < INFO_MIN_EDGE_RATIO:
        reason = "flat"
    return InformationAssessment(
        low_information=bool(reason),
        reason=reason or "informative",
        ink_ratio=round(ink_ratio, 4),
        edge_ratio=round(edge_ratio, 4),
        entropy_bits=round(entropy, 3),
    )
