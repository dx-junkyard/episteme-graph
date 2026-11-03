"""回答本文の逐語引用と、引用先の出典チャンク本文の一致検査（IK-0545）。

学習チャットの回答本文に ``「…」[出典N]`` のような逐語引用があるとき、その引用文が
出典 N のチャンク本文（LLM に文脈として渡した写し）に実際に含まれるかを**決定論的に**
突き合わせる。LLM が別のチャンクの文を ``[出典N]`` に帰属させた（誤帰属）ことを、
「出典が付いている＝裏付けがある」という見かけのまま通さないための計器。

規律:

- **非LLM・同期・決定論**（P6）。DB・FastAPI・LLM を import しない純関数。
- **引用を落とさない・書き換えない**（P4）。本文は一切変えず、結果は事実として返すだけ。
- **数値を返さない**（件数・一致度・類似度を出さない）。返すのは番号（本文の ``[出典N]``
  の N。番地であって指標ではない）と引用の抜粋、事実文だけ。
- 検査するのは**引用符で括られた逐語**（「」『』“” ""）だけ。言い換え（引用符の無い文）は
  逐語一致の対象にならないので検査しない（一致しないのが正常な文を「不一致」と言わない）。
  短い引用（用語の強調など、``MIN_QUOTE_CHARS`` 未満）も検査しない。
- 正規化は SA層 ``core/assistant_context/selection.py`` と同じ作法（制御シーケンス除去 →
  空白の除去）に、全角半角（NFKC）と大文字小文字・引用末尾の句点の違いを吸収するだけ。
  比較用のキーを作るだけで、載せる抜粋は原文のまま。
"""

from __future__ import annotations

import re
import unicodedata

from core.text_hygiene import strip_control_sequences

#: 一致を確認できなかった引用に添える事実文（警告ではなく事実。閉世界 — 「誤り」と断定しない）。
CITATION_UNMATCHED_NOTE = "引用文は出典本文と一致を確認できていません"

#: 逐語検査の対象にする引用の最短長（正規化後の字数）。これより短い引用は用語の強調とみなす。
MIN_QUOTE_CHARS = 8

#: 抜粋の最大字数（DTO に載せる引用の写し）。
QUOTE_EXCERPT_CHARS = 60

#: 本文の出典マーカー（``_reconcile_citation_markers`` で半角 ``[出典N]`` に正規化済みの前提）。
_MARKER_RE = re.compile(r"\[出典(\d+)\]")

#: 逐語引用（入れ子は想定しない）。鏡の検査（core/discuss/mirroring.py）と同じ 3 種に『』を足す。
_QUOTE_RE = re.compile(r"「([^「」]+)」|『([^『』]+)』|“([^“”]+)”|\"([^\"\n]+)\"")

#: 文の区切り（引用の外にあるときだけ、引用とマーカーの結び付きを切る）。
_TERMINATORS = set("。．！？!?\n")

#: 引用の中の省略記号（前後の断片をそれぞれ順に検査する）。
_ELLIPSIS_RE = re.compile(r"…+|\.{3,}|‥+")

_WHITESPACE_RE = re.compile(r"[\s　]+")
_TRAILING_PUNCT_RE = re.compile(r"[。．、，,.;；:：]+$")


def _match_key(text: str) -> str:
    """比較用キー（原文は書き換えない）。"""
    cleaned = strip_control_sequences(str(text or ""))
    cleaned = unicodedata.normalize("NFKC", cleaned).casefold()
    cleaned = cleaned.replace("$", "")
    cleaned = _WHITESPACE_RE.sub("", cleaned)
    return _TRAILING_PUNCT_RE.sub("", cleaned)


def _quote_spans(answer: str) -> list[tuple[int, int, str]]:
    out: list[tuple[int, int, str]] = []
    for match in _QUOTE_RE.finditer(answer):
        inner = next((part for part in match.groups() if part), "")
        out.append((match.start(), match.end(), inner))
    return out


def _attributed_markers(masked: str, start: int, end: int) -> list[str]:
    """引用 ``[start, end)`` に結び付く出典番号（同じ文の中で引用の直後、無ければ直前）。"""
    # 直後: 引用の終わりから文の区切りまでに現れるマーカー（連続するマーカーはすべて）。
    after: list[str] = []
    pos = end
    while pos < len(masked):
        ch = masked[pos]
        if ch in _TERMINATORS:
            break
        match = _MARKER_RE.match(masked, pos)
        if match:
            after.append(match.group(1))
            pos = match.end()
            continue
        if after:
            # マーカーの並びが途切れたら止める（「…」[出典1]と[出典2] の [出典2] は別の文脈）。
            break
        pos += 1
    if after:
        return after
    # 直前: 文の始まりから引用の始まりまでに現れる最後のマーカー（「[出典2]によると「…」」）。
    pos = start - 1
    while pos >= 0 and masked[pos] not in _TERMINATORS:
        pos -= 1
    segment = masked[pos + 1:start]
    before = _MARKER_RE.findall(segment)
    return [before[-1]] if before else []


def _quote_found_in(quote: str, passage_key: str) -> bool:
    """引用（省略記号で区切られた断片は順に）が出典本文のキーに含まれるか。"""
    cursor = 0
    parts = [_match_key(part) for part in _ELLIPSIS_RE.split(quote)]
    parts = [part for part in parts if part]
    if not parts:
        return False
    for part in parts:
        found = passage_key.find(part, cursor)
        if found < 0:
            return False
        cursor = found + len(part)
    return True


def _excerpt(text: str) -> str:
    text = _WHITESPACE_RE.sub(" ", strip_control_sequences(str(text or ""))).strip()
    if len(text) <= QUOTE_EXCERPT_CHARS:
        return text
    return text[:QUOTE_EXCERPT_CHARS].rstrip() + "…"


def check_citations(answer: str, passages: dict) -> dict | None:
    """回答本文の逐語引用を出典本文と突き合わせる。

    Args:
        answer: 回答本文（出典マーカーは半角 ``[出典N]`` に正規化済み）。
        passages: 出典番号 → その出典として LLM に渡したチャンク本文。キーは int / str どちらでも。

    Returns:
        検査した引用が 1 つも無ければ ``None``。あれば::

            {"checked": ["1", "3"],            # 逐語引用を検査した出典番号（本文の出現順）
             "unmatched": [{"citation": "3",    # 一致を確認できなかった出典番号
                            "quotes": ["…"],    # その番号に結び付いた不一致の引用（抜粋）
                            "note": CITATION_UNMATCHED_NOTE}]}

        数値（件数・一致度）は含めない。番号は本文の ``[出典N]`` の N（文字列）。
        本文に無い番号（``passages`` に無い番号）は検査しない。例外は外へ出さない。
    """
    try:
        text = str(answer or "")
        if "出典" not in text:
            return None
        keyed: dict[str, str] = {}
        for raw_index, passage in (passages or {}).items():
            index = str(raw_index).strip()
            if index.isdigit() and passage:
                keyed[str(int(index))] = _match_key(passage)
        if not keyed:
            return None
        spans = _quote_spans(text)
        if not spans:
            return None
        # 引用の内側は区切り判定から外す（「…である。」の句点で結び付きを切らない）。
        masked_chars = list(text)
        for start, end, _inner in spans:
            for i in range(start + 1, end - 1):
                masked_chars[i] = "_"
        masked = "".join(masked_chars)

        checked: list[str] = []
        unmatched: dict[str, list[str]] = {}
        for start, end, inner in spans:
            if len(_match_key(inner)) < MIN_QUOTE_CHARS:
                continue
            markers = [m for m in _attributed_markers(masked, start, end) if str(int(m)) in keyed]
            if not markers:
                continue
            markers = [str(int(m)) for m in markers]
            for marker in markers:
                if marker not in checked:
                    checked.append(marker)
            # 連続するマーカー（「…」[出典1][出典2]）はどれかの本文に含まれれば一致とみなす。
            if any(_quote_found_in(inner, keyed[m]) for m in markers):
                continue
            for marker in markers:
                quotes = unmatched.setdefault(marker, [])
                snippet = _excerpt(inner)
                if snippet not in quotes:
                    quotes.append(snippet)
        if not checked:
            return None
        return {
            "checked": checked,
            "unmatched": [
                {"citation": marker, "quotes": quotes, "note": CITATION_UNMATCHED_NOTE}
                for marker, quotes in unmatched.items()
            ],
        }
    except Exception:  # pragma: no cover - 防御的（計器で会話を止めない）
        return None


__all__ = [
    "CITATION_UNMATCHED_NOTE",
    "MIN_QUOTE_CHARS",
    "QUOTE_EXCERPT_CHARS",
    "check_citations",
]
