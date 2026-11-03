"""A層の LLM 入力に載せる本文の切り詰め（正本・stdlib のみ）。

IK-0808: 対象ブロック・近傍ブロックの本文を文字数で切るとき、語の途中
（「…evidence of the robustnes」）や先頭側（主語の手前）で印なしに切ると、主語や
結論の欠けた断片が完全な本文として読まれる。学習者向けの抜粋
（``backend/core/text_excerpt.excerpt`` — IK-0514 / IK-0393）と同じ流儀で切る:

1. 上限以下なら切らない（印も付けない）。
2. 文境界（. ! ? の後ろに空白 + 大文字・数式・括弧）を優先する。ただし残りが上限の
   半分を割る位置では使わない（略語の後ろ・``$…$`` の中の「.」は文境界にしない）。
3. 文境界が使えなければ語境界（空白）で切る。``$…$`` の数式の途中では切らない
   （数式が上限より長ければ数式ごと落とす — 壊れた ``$`` を残さない）。
4. 切った側に必ず ``…`` を付ける（後ろを切れば末尾、前を切れば先頭）。

学習者向けと違い空白を正規化しない（span のオフセットが元の本文と同じ位置を指す
ことを壊さないため）。A層は ``backend/`` を import しないので規則をここに持つ。
"""
from __future__ import annotations

import re

ELLIPSIS = "…"

#: 文の終わり（「. 」「! 」「? 」の後ろに大文字か数式・括弧が続く）。
_SENTENCE_END_RE = re.compile(r"[.!?](?=\s+[A-Z\\$(\[\"'])")
_ABBREVIATION_TAIL_RE = re.compile(
    r"(?:\b(?:Eqs?|Eqns?|Figs?|Secs?|Refs?|Tabs?|Apps?|Ch|Chap|Dr|Prof|al|vs|cf|e\.g|i\.e|etc|resp|approx)"
    r"|\b[A-Z])$"
)
#: 文境界で切るのは、残りが上限の半分以上になるときだけ。
_MIN_SENTENCE_KEEP_RATIO = 0.5


def _dollar_positions(text: str) -> list[int]:
    return [i for i, ch in enumerate(text) if ch == "$" and (i == 0 or text[i - 1] != "\\")]


def _math_spans(text: str) -> list[tuple[int, int]]:
    dollars = _dollar_positions(text)
    return [(dollars[k], dollars[k + 1]) for k in range(0, len(dollars) - 1, 2)]


def _sentence_ends(text: str, spans: list[tuple[int, int]]) -> list[int]:
    """文の終わりの直後の位置（略語・数式の中を除く）。"""
    ends: list[int] = []
    for match in _SENTENCE_END_RE.finditer(text):
        pos = match.start()
        if _ABBREVIATION_TAIL_RE.search(text[:pos]):
            continue
        if any(start < pos < end for start, end in spans):
            continue
        ends.append(match.end())
    return ends


def truncate_text(text: str, limit: int, *, keep_end: bool = False) -> tuple[str, bool]:
    """``text`` を ``limit`` 字以内（印を除く）に切る。戻り値は (本文, 切ったか)。

    ``keep_end=False`` は先頭側を残し末尾に ``…``、``keep_end=True`` は末尾側
    （式や対象に近い側）を残し先頭に ``…`` を付ける。
    """
    text = text or ""
    if limit <= 0 or len(text) <= limit:
        return text, False
    spans = _math_spans(text)

    def inside_math(pos: int) -> tuple[int, int] | None:
        for start, end in spans:
            if start < pos <= end:
                return start, end
        return None

    floor = int(limit * _MIN_SENTENCE_KEEP_RATIO)
    if keep_end:
        start_min = len(text) - limit
        sentence_starts = [
            e for e in _sentence_ends(text, spans)
            if start_min <= e <= len(text) - floor
        ]
        if sentence_starts:
            cut = sentence_starts[0]
            while cut < len(text) and text[cut].isspace():
                cut += 1
            return ELLIPSIS + text[cut:], True
        cut = start_min
        span = inside_math(cut)
        if span:
            cut = span[1] + 1
        while cut < len(text) and cut > 0 and not text[cut].isspace() and not text[cut - 1].isspace():
            span = inside_math(cut)
            if span:
                cut = span[1] + 1
                continue
            cut += 1
        return ELLIPSIS + text[cut:].lstrip(), True

    sentence_cuts = [e for e in _sentence_ends(text[: limit + 1], spans) if floor <= e <= limit]
    if sentence_cuts:
        return text[: sentence_cuts[-1]] + ELLIPSIS, True
    cut = limit
    span = inside_math(cut)
    if span:
        cut = span[0]
    while cut > 0 and not text[cut].isspace() and not text[cut - 1].isspace():
        cut -= 1
        span = inside_math(cut)
        if span:
            cut = span[0]
    return text[:cut].rstrip() + ELLIPSIS, True


def excerpt_around(text: str, start: int, end: int, limit: int) -> tuple[str, bool]:
    """``text[start:end]``（対象）を切らずに残し、前後を語境界で切って ``limit`` 字に収める。

    対象が上限を超えるときは対象だけを返す（対象は切らない）。前後の配分は 2:1
    （前置きの主語を残す）。
    """
    text = text or ""
    if limit <= 0 or len(text) <= limit:
        return text, False
    start = max(0, min(start, len(text)))
    end = max(start, min(end, len(text)))
    target = text[start:end]
    room = max(0, limit - len(target))
    before_budget = room * 2 // 3
    after_budget = room - before_budget
    before, cut_before = truncate_text(text[:start], before_budget, keep_end=True) if before_budget else (
        ELLIPSIS if start else "", bool(start)
    )
    after, cut_after = truncate_text(text[end:], after_budget) if after_budget else (
        ELLIPSIS if end < len(text) else "", end < len(text)
    )
    return before + target + after, bool(cut_before or cut_after)
