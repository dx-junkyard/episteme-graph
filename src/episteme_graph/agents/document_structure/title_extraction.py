"""論文題名の決定論的な抽出（非LLM・A層・provisional）。

題名は次の順で1つだけ採る（IK-0420）:

1. **TEI ヘッダ**（``source="tei"``）— GROBID の ``teiHeader`` 内の
   ``titleStmt/title`` を ``type="main"`` → ``level="a"`` → その他の順に、続けて
   ``sourceDesc//analytic/title`` を見る。組版スタンプ（``Draft version ...`` 等）に
   見える候補は採らない（GROBID のヘッダモデルは AASTeX / MNRAS のスタンプを題名と
   取り違えることがある）。
2. **1頁目の最大フォント**（``source="font_size"``）— PyMuPDF のレイアウトブロックから、
   1頁目の上半分で最も大きい文字の連なりを読み順につなぐ。文字の大きさが下がった
   ところで止める。組版ヘッダ・arXiv スタンプ・日付・e-mail・URL に見えるブロックは
   候補にしない。本文の文字より十分大きくなければ題名とみなさない。推定なので
   ``needs_review=True``。
3. どちらも取れなければ ``None``（``source="none"``）。推測で題名を作らない。

題名は常に1行の文字列に正規化する（空白の畳み込み・行末ハイフンは残して改行だけを
詰める）。``metadata.title_extraction`` の形は ``author_extraction`` と揃える
（``source`` / ``confidence`` / ``needs_review`` / ``review_reasons`` /
``candidate_sources``）。confidence は後段が参照する provenance で、画面には出さない。
"""
from __future__ import annotations

import re
from typing import Any, Iterable

TITLE_SOURCES = ("tei", "font_size", "none")

# 題名として採る長さの上限（これより長いものは段落の取り違えとみなす）。
TITLE_MAX_CHARS = 200

# 1頁目の「上の方」とみなす範囲（頁の高さに対する割合）。
_TITLE_TOP_FRACTION = 0.5
# 題名ブロックの文字の大きさが本文（1頁目の加重中央値）の何倍以上あれば題名とみなすか。
_TITLE_MIN_FONT_RATIO = 1.15
# 「同じ大きさ」とみなす許容幅（最大の大きさに対する割合）。
_TITLE_SIZE_TOLERANCE = 0.06

_SOURCE_CONFIDENCE = {"tei": 0.95, "font_size": 0.6, "none": 0.0}

_MONTHS = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|"
    r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)

# 組版ヘッダ・スタンプ・連絡先など、題名でないと判断できるもの。
_NON_TITLE_RE = re.compile(
    r"(?i)"
    r"\barxiv\s*:"                                   # arXiv:2606.00411v1 [astro-ph.CO]
    r"|\b\d{4}\.\d{4,5}(?:v\d+)?\b"                  # 裸の arXiv ID
    r"|\bpreprint\b"
    r"|\bdraft\s+version\b"
    r"|\btypeset\s+using\b"
    r"|\bcompiled\s+using\b"
    r"|\bstyle\s+file\b"
    r"|\breceived\b"
    r"|\baccepted\s+(?:for|in|by|\d|x)"
    r"|@"
    r"|https?://|\bwww\."
    r"|\bdoi\s*[:.]?\s*10\."
    r"|\b(?:mnras|apj|a\s*&\s*a|phys\.?\s*rev\.?)\b[^a-z]*\d"   # MNRAS 000, 1–16 (2025)
    r"|^[a-z]{2,6}/\d+-[a-z]+$"                      # APS/123-QED
    r"|copyright|©"
    r"|\b" + _MONTHS + r"\.?\s+\d{1,2},?\s+\d{4}\b"  # June 2, 2026
    r"|\b\d{1,2}\s+" + _MONTHS + r"\.?\s+\d{4}\b"    # 27 May 2026
)

# 題名ではない見出し語だけのブロック。
_HEADING_ONLY_RE = re.compile(
    r"(?i)^(?:abstract|introduction|contents|references|keywords?|key\s+words)\W*$"
)

_WORD_RE = re.compile(r"[^\W\d_]{2,}", re.UNICODE)


def normalize_title_text(text: Any) -> str:
    """題名を1行の文字列に正規化する。

    - 改行・タブ・連続空白を1つの空白に畳む。
    - 行末ハイフンは残して改行だけを詰める（``Sub-\\nPopulations`` →
      ``Sub-Populations``）。題名は多くの組版でハイフネーションされないため、行末の
      ハイフンは複合語のハイフンとして扱う（``Physics-\\ninformed`` を
      ``Physicsinformed`` にしない）。
    - soft hyphen（U+00AD）は取り除く。
    """
    s = str(text or "")
    s = s.replace("­", "")
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"(\w)-[ \t]*\n\s*(?=\w)", r"\1-", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def looks_like_non_title(text: Any) -> bool:
    """組版ヘッダ・スタンプ・日付・連絡先・見出し語だけ、に見えるか。"""
    s = normalize_title_text(text)
    if not s:
        return True
    if _HEADING_ONLY_RE.match(s):
        return True
    return bool(_NON_TITLE_RE.search(s))


def acceptable_title(text: Any) -> str | None:
    """題名として採れるなら正規化した文字列を、採れなければ ``None`` を返す。"""
    s = normalize_title_text(text)
    if not s or len(s) > TITLE_MAX_CHARS:
        return None
    if looks_like_non_title(s):
        return None
    if len(_WORD_RE.findall(s)) < 2:
        return None
    return s


# ---------------------------------------------------------------------------
# (a) TEI
# ---------------------------------------------------------------------------


def tei_title_candidates(soup: Any) -> list[str]:
    """TEI ヘッダから題名の候補を優先順に返す（正規化済み・空は除く・重複なし）。

    参照文献（``listBibl``）内の題名は読まない。
    """
    header = soup.find("teiHeader") if soup is not None else None
    if header is None:
        return []

    def _text(tag) -> str:
        return normalize_title_text(tag.get_text()) if tag is not None else ""

    ordered: list = []
    for title_stmt in header.find_all("titleStmt"):
        titles = [t for t in title_stmt.find_all("title") if t.find_parent("listBibl") is None]
        ordered += [t for t in titles if t.get("type") == "main"]
        ordered += [t for t in titles if t.get("type") != "main" and t.get("level") == "a"]
        ordered += [t for t in titles if t.get("type") != "main" and t.get("level") != "a"]
    for analytic in header.find_all("analytic"):
        if analytic.find_parent("listBibl") is not None:
            continue
        titles = analytic.find_all("title")
        ordered += [t for t in titles if t.get("type") == "main"]
        ordered += [t for t in titles if t.get("type") != "main"]

    seen: set[str] = set()
    result: list[str] = []
    for tag in ordered:
        text = _text(tag)
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def select_tei_title(candidates: Iterable[str]) -> str | None:
    """TEI 候補のうち題名として採れる最初のものを返す。"""
    for cand in candidates or []:
        title = acceptable_title(cand)
        if title:
            return title
    return None


# ---------------------------------------------------------------------------
# (b) 1頁目の最大フォント
# ---------------------------------------------------------------------------


def _get(block: Any, key: str, default: Any = None) -> Any:
    if isinstance(block, dict):
        return block.get(key, default)
    return getattr(block, key, default)


def _block_size(block: Any) -> float | None:
    for key in ("dominant_font_size", "font_size"):
        value = _get(block, key)
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
    return None


def _is_vertical(block: Any) -> bool:
    """縦書きのブロック（arXiv の余白スタンプ等）か。"""
    bbox = _get(block, "bbox")
    if not bbox or len(bbox) != 4:
        return False
    width = float(bbox[2]) - float(bbox[0])
    height = float(bbox[3]) - float(bbox[1])
    return width > 0 and height > 3 * width and height > 60


def _weighted_median(pairs: list[tuple[float, int]]) -> float | None:
    pairs = [(v, w) for v, w in pairs if w > 0]
    if not pairs:
        return None
    pairs.sort()
    total = sum(w for _, w in pairs)
    acc = 0
    for value, weight in pairs:
        acc += weight
        if acc * 2 >= total:
            return value
    return pairs[-1][0]


def extract_title_from_layout(
    raw_blocks: Iterable[Any] | None,
    page_heights: dict | None = None,
) -> str | None:
    """1頁目の上半分で最も大きい文字の連なりを題名として返す（無ければ ``None``）。"""
    blocks = [
        b for b in (raw_blocks or [])
        if b is not None and isinstance(_get(b, "page"), int)
        and str(_get(b, "text", "") or "").strip()
    ]
    if not blocks:
        return None
    first_page = min(int(_get(b, "page")) for b in blocks)
    page_blocks = [b for b in blocks if _get(b, "page") == first_page]

    def _y0(b) -> float:
        bbox = _get(b, "bbox")
        return float(bbox[1]) if bbox and len(bbox) == 4 else 0.0

    def _x0(b) -> float:
        bbox = _get(b, "bbox")
        return float(bbox[0]) if bbox and len(bbox) == 4 else 0.0

    has_bbox = all(_get(b, "bbox") for b in page_blocks)
    if has_bbox:
        ordered = sorted(page_blocks, key=lambda b: (_y0(b), _x0(b), _get(b, "order", 0) or 0))
    else:
        ordered = sorted(page_blocks, key=lambda b: _get(b, "order", 0) or 0)

    body_size = _weighted_median(
        [
            (size, len(str(_get(b, "text", "") or "")))
            for b in page_blocks
            if (size := _block_size(b)) is not None and not _is_vertical(b)
        ]
    )
    if body_size is None:
        return None

    page_height = None
    if isinstance(page_heights, dict):
        page_height = page_heights.get(first_page)
    if not page_height and has_bbox:
        page_height = max(float(_get(b, "bbox")[3]) for b in page_blocks)

    def _in_top(b) -> bool:
        if not has_bbox or not page_height:
            return True
        return _y0(b) <= float(page_height) * _TITLE_TOP_FRACTION

    top = [b for b in ordered if _in_top(b)]

    def _skippable(b) -> bool:
        return _is_vertical(b) or looks_like_non_title(_get(b, "text", ""))

    candidate_sizes = [
        size for b in top
        if not _skippable(b) and (size := _block_size(b)) is not None
    ]
    if not candidate_sizes:
        return None
    max_size = max(candidate_sizes)
    if max_size < body_size * _TITLE_MIN_FONT_RATIO:
        return None
    tolerance = max_size * _TITLE_SIZE_TOLERANCE

    parts: list[str] = []
    started = False
    for b in top:
        size = _block_size(b)
        if _skippable(b):
            if started and size is not None and abs(size - max_size) <= tolerance:
                # 題名と同じ大きさの組版スタンプが挟まるなら題名の連なりではない。
                break
            continue
        if size is None:
            if started:
                break
            continue
        same = abs(size - max_size) <= tolerance
        if not started:
            if same:
                started = True
                parts.append(str(_get(b, "text", "") or ""))
            continue
        if not same:
            break
        parts.append(str(_get(b, "text", "") or ""))

    if not parts:
        return None
    return acceptable_title("\n".join(parts))


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


def build_title_extraction(
    *,
    tei_candidates: list[str] | None,
    font_size_title: str | None,
    tei_title: str | None = None,
) -> tuple[str | None, dict]:
    """採る題名と ``metadata.title_extraction`` を組み立てる。

    ``tei_title`` を渡さなければ ``tei_candidates`` から選び直す。
    """
    candidates = [c for c in (tei_candidates or []) if c]
    chosen_tei = tei_title if tei_title is not None else select_tei_title(candidates)
    candidate_sources: dict[str, list[str]] = {}
    if candidates:
        candidate_sources["tei"] = list(candidates)
    if font_size_title:
        candidate_sources["font_size"] = [font_size_title]

    if chosen_tei:
        title, source, needs_review, reasons = chosen_tei, "tei", False, []
    elif font_size_title:
        title, source, needs_review = font_size_title, "font_size", True
        reasons = ["title_from_font_size"]
        if candidates:
            reasons.append("tei_title_rejected")
    else:
        title, source, needs_review = None, "none", False
        reasons = ["tei_title_rejected"] if candidates else []

    return title, {
        "source": source,
        "confidence": _SOURCE_CONFIDENCE[source],
        "needs_review": needs_review,
        "review_reasons": reasons,
        "candidate_sources": candidate_sources,
    }
