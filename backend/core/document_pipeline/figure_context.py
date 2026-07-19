"""figure_context: 図ごとの「周辺本文」「略語辞書」を決定論的に収集する非LLMユーティリティ。

設計: /Users/Shared/issues/apparatus_diagram_understanding_design.md §4-3
（G1: nearby_text が常に空 / G3: 文脈収集が図の近傍に限定される、の2ギャップ解消）。

- ``apparatus_semantics`` ステージの LLM 入力として使う周辺文脈を、
  ① caption 直近 ② "Fig. N" 参照メンション ③ 同一セクション本文
  の優先順位で ``max_items`` / ``max_chars`` 予算内に収める。
- 略語辞書は「フル表記 (略語)」「略語 (フル表記)」の2パターンを全文検索し、
  ``inner_labels``（図中ラベル）または caption/nearby_text への出現で図単位に絞り込む。
- block の平坦化は既存の ``figure_images._blocks_from_structure`` を再利用する
  （コピペ禁止・structure の dict/dataclass 両対応をここで再実装しない）。
- FastAPI / DB / LLM / os.environ は一切 import しない（core/ 共通ルール、決定論的・純関数）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .figure_images import _blocks_from_structure

# 図番号らしき数値・ドット区切り表記（"5.2" / "12" / "3.2a"）を figure_label から拾う。
_LABEL_NUM_RE = re.compile(r"([0-9]+(?:\.[0-9A-Za-z]+)*)")

# 「フル表記 (略語)」。略語側の末尾 's' は複数形として別扱い（group 3）。
_ABBR_AFTER_FULL_RE = re.compile(
    r"\b([A-Za-z][A-Za-z\-/ ]{2,80}?)\s*\(\s*([A-Z][A-Za-z0-9\-]{1,11}?)(s)?\s*\)"
)
# 「略語 (フル表記)」。
_FULL_AFTER_ABBR_RE = re.compile(
    r"\b([A-Z][A-Za-z0-9\-]{1,11})\s*\(\s*([a-z][^)]{3,80})\s*\)"
)

# nearby_text 候補プールから除外する block_type（caption 自身は別途 id/identity で除外）。
_CAPTION_BLOCK_TYPES = ("figure_caption", "table_caption")


def _number_pattern_fragment(num: str) -> str:
    """図番号 num を参照メンション検索用の正規表現断片に変換する。

    GROBID 由来の本文では図番号の小数点前後にスペースが混入する表記
    （実データ例: "Fig. 3 .3"）が生じるため、``.`` は ``\\s*\\.\\s*`` に、
    それ以外の英数字は ``re.escape(ch)`` として連結する
    （``figure_table_semantics/crosslink.py`` の同名ヘルパーと同じ規約）。
    """
    parts: list[str] = []
    for ch in num:
        if ch == ".":
            parts.append(r"\s*\.\s*")
        else:
            parts.append(re.escape(ch))
    return "".join(parts)

# 予算超過時、切り詰めてでも採用するか丸ごとスキップするかの閾値（文字数）。
_TRUNCATE_MIN_REMAINING = 80

_SOURCE_KEYS = ("abbreviation_defs", "caption_adjacent", "reference_mentions", "section_body")


def _empty_source_counts() -> dict:
    return {key: 0 for key in _SOURCE_KEYS}


@dataclass
class FigureContext:
    """apparatus_semantics への入力となる、図1件分の周辺文脈。"""

    nearby_text: list[str] = field(default_factory=list)
    abbreviations: dict[str, str] = field(default_factory=dict)
    # 収集内訳の正直な開示（P4: どこから何件採用したかを常に返す）。
    source_counts: dict = field(default_factory=_empty_source_counts)


# ---------------------------------------------------------------------------
# 図番号の導出
# ---------------------------------------------------------------------------


def _derive_figure_number(figure_label: str | None, figure_key: str | None) -> str | None:
    """figure_label / figure_key から参照メンション検索用の図番号文字列を導出する。"""
    if figure_label:
        match = _LABEL_NUM_RE.search(figure_label)
        if match:
            return match.group(1)
    if figure_key and figure_key.startswith("fig_"):
        rest = figure_key[len("fig_"):]
        if rest:
            return rest.replace("_", ".")
    return None


# ---------------------------------------------------------------------------
# caption block の特定 / 共通除外
# ---------------------------------------------------------------------------


def _find_caption_block(
    sorted_blocks: list[dict], caption_block_id: str | None
) -> tuple[dict | None, int | None]:
    if caption_block_id is None:
        return None, None
    for idx, b in enumerate(sorted_blocks):
        if b.get("block_id") == caption_block_id:
            return b, idx
    return None, None


def _is_caption_self(
    b: dict, cap_block: dict | None, caption_block_id: str | None
) -> bool:
    if cap_block is not None and b is cap_block:
        return True
    if caption_block_id is not None and b.get("block_id") == caption_block_id:
        return True
    return False


def _common_excluded(
    b: dict, cap_block: dict | None, caption_block_id: str | None
) -> bool:
    """nearby_text 候補プール共通の除外条件（caption自身 / caption・table caption / 空文）。"""
    if _is_caption_self(b, cap_block, caption_block_id):
        return True
    if b.get("block_type") in _CAPTION_BLOCK_TYPES:
        return True
    if not (b.get("text") or "").strip():
        return True
    return False


def _block_ident(b: dict) -> Any:
    block_id = b.get("block_id")
    if block_id is not None:
        return block_id
    return (b.get("page"), b.get("order"))


# ---------------------------------------------------------------------------
# nearby_text 候補プールの構築（優先度順・dedupe 済み）
# ---------------------------------------------------------------------------


def _build_candidate_pool(
    sorted_blocks: list[dict],
    cap_block: dict | None,
    cap_idx: int | None,
    caption_block_id: str | None,
    figure_row: dict,
) -> list[tuple[str, str]]:
    """(source, text) の優先度順リストを返す（caption_adjacent → reference_mentions → section_body）。"""
    used: set = set()
    candidates: list[tuple[str, str]] = []

    def _try_add(b: dict, source: str) -> None:
        ident = _block_ident(b)
        if ident in used:
            return
        if _common_excluded(b, cap_block, caption_block_id):
            return
        used.add(ident)
        candidates.append((source, b.get("text", "") or ""))

    # (1) caption 直近: caption の直前2ブロック + 直後2ブロック。
    if cap_idx is not None:
        pre = sorted_blocks[max(0, cap_idx - 2):cap_idx]
        post = sorted_blocks[cap_idx + 1: cap_idx + 3]
        for b in pre + post:
            _try_add(b, "caption_adjacent")

    # (2) 参照メンション: "Fig. N" / "Figure N" / "図N" にヒットしたブロック + 前後1ブロック。
    num = _derive_figure_number(figure_row.get("figure_label"), figure_row.get("figure_key"))
    if num:
        # 番号内スペース混入（"Fig. 3 .3"）と文末ピリオド直後（"Fig. 3.3."）の両方を
        # 許容する。否定先読み (?!\.?[0-9]) は「より長い番号への連続」だけを弾く
        # （crosslink.py の _figure_mention_pattern と同じ規約）。
        mention_re = re.compile(
            rf"(?:Fig\.?|Figure|図)\s*{_number_pattern_fragment(num)}(?!\.?[0-9])",
            re.IGNORECASE,
        )
        for idx, b in enumerate(sorted_blocks):
            if _is_caption_self(b, cap_block, caption_block_id):
                continue
            if not mention_re.search(b.get("text") or ""):
                continue
            group = []
            if idx - 1 >= 0:
                group.append(sorted_blocks[idx - 1])
            group.append(b)
            if idx + 1 < len(sorted_blocks):
                group.append(sorted_blocks[idx + 1])
            for gb in group:
                _try_add(gb, "reference_mentions")

    # (3) セクション残り: caption と同一 section_id の残り全ブロック（文書順）。
    section_id = cap_block.get("section_id") if cap_block is not None else None
    if section_id is not None:
        for b in sorted_blocks:
            if b.get("section_id") != section_id:
                continue
            _try_add(b, "section_body")

    return candidates


# ---------------------------------------------------------------------------
# 予算適用（max_items / max_chars）
# ---------------------------------------------------------------------------


def _apply_budget(
    candidates: list[tuple[str, str]], max_items: int, max_chars: int
) -> tuple[list[str], dict]:
    nearby_text: list[str] = []
    source_counts = _empty_source_counts()
    total_chars = 0

    for source, raw_text in candidates:
        if len(nearby_text) >= max_items:
            break
        text = raw_text.strip()
        if not text:
            continue
        new_total = total_chars + len(text)
        if new_total <= max_chars:
            nearby_text.append(text)
            total_chars = new_total
            source_counts[source] += 1
            continue
        remaining = max_chars - total_chars
        if remaining >= _TRUNCATE_MIN_REMAINING:
            nearby_text.append(text[:remaining])
            source_counts[source] += 1
        break

    return nearby_text, source_counts


# ---------------------------------------------------------------------------
# 略語辞書
# ---------------------------------------------------------------------------


def _initials_match(full: str, abbr: str) -> bool:
    """abbr が full の頭字語として妥当か（先頭文字一致 + 部分列としての整合）を判定する。"""
    abbr_chars = re.sub(r"[^A-Za-z0-9]", "", abbr).upper()
    if not abbr_chars or not full:
        return False
    full_upper = full.upper()
    if full_upper[0] != abbr_chars[0]:
        return False
    idx = 0
    for ch in full_upper:
        if idx < len(abbr_chars) and ch == abbr_chars[idx]:
            idx += 1
    return idx == len(abbr_chars)


def _maybe_register(result: dict[str, str], abbr: str, full: str) -> None:
    full = full.strip().rstrip(".,;:　 ")
    if not full or not abbr:
        return
    if not _initials_match(full, abbr):
        return
    if abbr in result:
        return  # first-wins（同一キーは最初の定義を保持）
    result[abbr] = full


def _trim_full_to_match(full: str, abbr: str) -> str | None:
    """full 句の先頭語を1つずつ落とした接尾辞のうち、最初に initials が一致する最長のものを返す。

    「フル表記 (略語)」パターンは文字クラスに空白を含むため、
    "The setup uses an external cavity diode laser (ECDL)" のように定義が文中に埋まると
    先行する無関係な語まで group(1) に飲み込まれる。initials 検査を通る最長接尾辞へ
    決定論的に縮めることで、文中定義（実際の論文で大半）を落とさない。
    全接尾辞が不一致なら None（従来どおり棄却）。
    """
    words = full.strip().split()
    for start in range(len(words)):
        candidate = " ".join(words[start:])
        if _initials_match(candidate, abbr):
            return candidate
    return None


def _extract_abbreviations(sorted_blocks: list[dict]) -> dict[str, str]:
    """全ブロック text から「フル表記(略語)」「略語(フル表記)」の2パターンを抽出する。"""
    result: dict[str, str] = {}
    for b in sorted_blocks:
        text = b.get("text") or ""
        if not text.strip():
            continue
        for m in _ABBR_AFTER_FULL_RE.finditer(text):
            abbr = m.group(2)
            has_plural = m.group(3) == "s"
            full = _trim_full_to_match(m.group(1), abbr)
            if full is None:
                continue
            _maybe_register(result, abbr, full)
            if has_plural:
                _maybe_register(result, abbr + "s", full)
        for m in _FULL_AFTER_ABBR_RE.finditer(text):
            abbr = m.group(1)
            full = m.group(2)
            _maybe_register(result, abbr, full)
    return result


def _label_matches_key(label_texts: list[str], key: str) -> bool:
    """inner_labels のいずれかが略語キーに一致するか（完全一致 → 大小無視 → prefix+3文字）。"""
    for label in label_texts:
        if not label:
            continue
        if label == key:
            return True
        if label.lower() == key.lower():
            return True
        if label.startswith(key) and (len(label) - len(key)) <= 3:
            return True
    return False


def _filter_abbreviations_for_figure(
    abbreviations: dict[str, str],
    inner_labels: list[dict] | None,
    caption_text: str,
    nearby_text: list[str],
) -> dict[str, str]:
    """図ごとの絞り込み: inner_labels があればラベル一致、無ければ caption/nearby 出現で絞る。"""
    if inner_labels:
        label_texts = [lbl.get("text", "") for lbl in inner_labels if lbl.get("text")]
        return {
            key: full
            for key, full in abbreviations.items()
            if _label_matches_key(label_texts, key)
        }

    haystacks = [caption_text or ""] + list(nearby_text)
    filtered: dict[str, str] = {}
    for key, full in abbreviations.items():
        pattern = re.compile(rf"\b{re.escape(key)}\b")
        if any(pattern.search(h) for h in haystacks):
            filtered[key] = full
    return filtered


# ---------------------------------------------------------------------------
# 公開 API
# ---------------------------------------------------------------------------


def collect_figure_context(
    structure: Any,
    figure_row: dict,
    *,
    inner_labels: list[dict] | None = None,
    max_items: int = 12,
    max_chars: int = 6000,
) -> FigureContext:
    """図1件分の周辺本文・略語辞書を決定論的に収集する（非LLM、apparatus_semantics 入力用）。

    優先順位（§4-3）: 略語定義 > caption 直近 > 参照段落 > セクション残り。
    ``structure`` が None または blocks が空なら例外にせず空の FigureContext を返す。
    """
    blocks = _blocks_from_structure(structure)
    if not blocks:
        return FigureContext([], {}, _empty_source_counts())

    sorted_blocks = sorted(blocks, key=lambda b: (b.get("page") or 0, b.get("order") or 0))

    caption_block_id = figure_row.get("caption_block_id")
    cap_block, cap_idx = _find_caption_block(sorted_blocks, caption_block_id)

    candidates = _build_candidate_pool(sorted_blocks, cap_block, cap_idx, caption_block_id, figure_row)
    nearby_text, source_counts = _apply_budget(candidates, max_items, max_chars)

    abbreviations = _extract_abbreviations(sorted_blocks)
    abbreviations = _filter_abbreviations_for_figure(
        abbreviations, inner_labels, figure_row.get("caption_text") or "", nearby_text
    )
    source_counts["abbreviation_defs"] = len(abbreviations)

    return FigureContext(nearby_text=nearby_text, abbreviations=abbreviations, source_counts=source_counts)
