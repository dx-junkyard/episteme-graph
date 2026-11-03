"""図を参照する本文の文（メンション文）の抽出 — 図の文脈の主役（純関数）。

正本文書: `docs/features/element_context_presentation_redesign.md` §11
（RC-F2「いちばん読める文が出ていない」/ RC-F6「掲載節が空」）。

読み手の問い「この図は何を示し、本文のどこで何のために参照されているか」に直接
答えるのは、caption と、図を参照する本文の文（``Figure 1.1 shows …``）である。
A層の ``crosslink.py`` は同じメンションを**段落単位**で拾って claim を結ぶが、
メンション文そのものは保存しない。本モジュールは既存 artifact
（``document_structure``）だけを材料に、読み時に同じ規約で**文単位**の言及を返す
（A層非改変・再解析不要・LLM 0 回）。

設計上の約束:

* 純関数のみ。FastAPI / sqlalchemy / ``core.postgres`` / LLM を**このファイルでは**
  import しない（図番号の導出とメンション正規表現の断片は
  ``core.document_pipeline.figure_context`` の同名ヘルパーを import して二重実装しない）。
* 文分割は ``core.text_excerpt.split_sentences``（文境界の唯一実装）。
* メンション文は**切らない**（空白正規化のみ）。表示の短縮はフロントの責務。
* 入力を mutate しない。例外を外に出さない（壊れた入力は空の結果）。
"""
from __future__ import annotations

import re
from typing import Any

from core.document_pipeline.figure_context import _derive_figure_number, _number_pattern_fragment
from core.text_excerpt import normalize_whitespace, split_sentences

__all__ = [
    "MENTION_LIMIT",
    "figure_mention_pattern",
    "find_figure_mentions",
    "mention_block_ids",
]

#: 1 図あたりの表示上限（§11.3「文書順・上限 6」）。
MENTION_LIMIT = 6

_BODY_BLOCK_TYPE = "body_paragraph"


# 付録の図番号（``Figure C.8`` / ``fig_c_8``）。``_derive_figure_number`` は先頭の
# 英字を落として ``8`` を返すため、本文の ``Figure 8 …`` に誤って当たる。英字1文字 +
# ``.数字`` の形だけ先に拾い、それ以外は ``_derive_figure_number`` に委ねる。
_APPENDIX_LABEL_RE = re.compile(r"\b([A-Z](?:\.[0-9]+)+)\b")
_APPENDIX_KEY_RE = re.compile(r"^fig_([A-Za-z](?:_[0-9]+)+)$")


def _figure_number(row: dict[str, Any]) -> str | None:
    label = str(row.get("figure_label") or "")
    match = _APPENDIX_LABEL_RE.search(label)
    if match:
        return match.group(1)
    key = str(row.get("figure_key") or "")
    if not re.search(r"[0-9]", label):
        key_match = _APPENDIX_KEY_RE.match(key)
        if key_match:
            return key_match.group(1).replace("_", ".")
    return _derive_figure_number(row.get("figure_label"), row.get("figure_key"))


def figure_mention_pattern(figure_row: dict[str, Any] | None) -> re.Pattern[str] | None:
    """図の行（``figure_label`` / ``figure_key``）からメンション正規表現を作る。

    形は ``figure_context`` / ``crosslink.py`` と同じ
    ``(?:Figs?\\.?|Figures?|図)\\s*<番号>``（大小無視）。番号内の空白混入
    （GROBID の ``Figure 1 .1``）は断片側が許容する。否定先読みはより長い番号への連続を
    弾くが、A層の ``(?!\\.?[0-9])`` は空白入りの連続（``Figure 8 .1`` が図 8 に当たる）を
    通してしまうため、ここでは ``(?![0-9]|\\s*\\.\\s*[0-9])`` と空白も許して弾く
    （読み時の表示だけの判定で、A層の crosslink は変えない）。番号が導出できなければ None。
    """
    row = figure_row if isinstance(figure_row, dict) else {}
    num = _figure_number(row)
    if not num:
        return None
    return re.compile(
        rf"(?:Figs?\.?|Figures?|図)\s*{_number_pattern_fragment(num)}(?![0-9]|\s*\.\s*[0-9])",
        re.IGNORECASE,
    )


def _caption_form_pattern(figure_row: dict[str, Any]) -> re.Pattern[str] | None:
    """文頭が ``Figure 6 .17:`` の形（図の caption が本文段落に重複して入ったもの）。

    caption は「本文で参照している文」ではないので言及から外す。
    """
    num = _figure_number(figure_row)
    if not num:
        return None
    return re.compile(
        rf"^\s*(?:Figs?\.?|Figures?|図)\s*{_number_pattern_fragment(num)}\s*[:：]",
        re.IGNORECASE,
    )


def _section_titles(structure: dict[str, Any]) -> dict[str, str]:
    """section_id → 見出し（``context_lens._section_label`` と同じ ``title`` の strip）。

    context_lens を import すると DB 依存を引き込むうえ循環参照になるため、同じ意味の
    最小限の索引をここで作る。
    """
    sections = structure.get("sections")
    if not isinstance(sections, list):
        return {}
    out: dict[str, str] = {}
    for section in sections:
        if not isinstance(section, dict) or not section.get("section_id"):
            continue
        out[str(section["section_id"])] = str(section.get("title") or "").strip()
    return out


def _page(value: Any) -> int | None:
    try:
        return int(value) if value is not None and str(value).strip() != "" else None
    except (TypeError, ValueError):
        return None


def _sort_key(block: dict[str, Any]) -> tuple[int, int]:
    page = _page(block.get("page"))
    try:
        order = int(block.get("order") or 0)
    except (TypeError, ValueError):
        order = 0
    return (page if page is not None else 1 << 30, order)


def find_figure_mentions(
    structure_artifact: dict[str, Any] | None,
    figure_row: dict[str, Any] | None,
    *,
    limit: int = MENTION_LIMIT,
) -> tuple[list[dict[str, Any]], list[str]]:
    """図を参照する本文の文を文書順に返す（``(mentions, notes)``）。

    各 mention = ``{text, section_label, section_id, block_id, page}``。``text`` は文の
    逐語（空白正規化のみ・切らない）。同じ文は1回だけ。対象は ``body_paragraph``
    （caption block 自身は除く）で、``(page, order)`` 順に走査する。上限を超えたら
    ``notes`` に「本文での言及に他 N 件あるが表示上限のため省略しました」を足す。
    """
    try:
        return _find(structure_artifact, figure_row, limit)
    except Exception:  # noqa: BLE001 — 読み時の補助情報。壊れた入力は空で返す
        return [], []


def _find(
    structure_artifact: dict[str, Any] | None,
    figure_row: dict[str, Any] | None,
    limit: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    structure = structure_artifact if isinstance(structure_artifact, dict) else {}
    row = figure_row if isinstance(figure_row, dict) else {}
    pattern = figure_mention_pattern(row)
    if pattern is None:
        return [], []
    blocks = structure.get("blocks")
    if not isinstance(blocks, list):
        return [], []
    caption_block_id = str(row.get("caption_block_id") or "").strip()
    caption_form = _caption_form_pattern(row)
    titles = _section_titles(structure)

    body_blocks = [
        b for b in blocks
        if isinstance(b, dict)
        and b.get("block_type") == _BODY_BLOCK_TYPE
        and not (caption_block_id and str(b.get("block_id") or "") == caption_block_id)
    ]
    body_blocks.sort(key=_sort_key)

    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for block in body_blocks:
        text = block.get("text")
        if not text or not pattern.search(str(text)):
            continue
        section_id = str(block.get("section_id") or "")
        for sentence in _mention_sentences(str(text), pattern):
            if sentence in seen:
                continue
            if caption_form is not None and caption_form.match(sentence):
                continue
            seen.add(sentence)
            found.append(
                {
                    "text": sentence,
                    "section_label": titles.get(section_id, ""),
                    "section_id": section_id,
                    "block_id": str(block.get("block_id") or ""),
                    "page": _page(block.get("page")),
                }
            )

    try:
        cap = int(limit)
    except (TypeError, ValueError):
        cap = MENTION_LIMIT
    cap = max(cap, 0)
    notes: list[str] = []
    if len(found) > cap:
        omitted = len(found) - cap
        notes.append(f"本文での言及に他 {omitted} 件あるが表示上限のため省略しました")
        found = found[:cap]
    return found, notes


def _mention_sentences(text: str, pattern: re.Pattern[str]) -> list[str]:
    """``text`` のうちメンションに掛かる文を文書順に返す。

    文ごとに照合するのではなく、空白正規化した本文上のメンション位置に**重なる文**を
    取る。``Figure 1 . 1`` のように番号の中に文境界と誤認される空白入りピリオドが
    あっても、重なる2文を1つに繋いで取りこぼさない。
    """
    body = normalize_whitespace(text)
    sentences = split_sentences(body)
    spans: list[tuple[int, int, str]] = []
    cursor = 0
    for sentence in sentences:
        start = body.find(sentence, cursor)
        if start < 0:
            continue
        end = start + len(sentence)
        spans.append((start, end, sentence))
        cursor = end
    out: list[str] = []
    for match in pattern.finditer(body):
        hit = [s for (a, b, s) in spans if a < match.end() and match.start() < b]
        if not hit:
            continue
        joined = " ".join(hit)
        if joined not in out:
            out.append(joined)
    return out


def mention_block_ids(mentions: list[dict[str, Any]] | None) -> set[str]:
    """メンションを含む本文ブロックの block_id 集合。"""
    return {
        str(m.get("block_id"))
        for m in (mentions or [])
        if isinstance(m, dict) and str(m.get("block_id") or "").strip()
    }
