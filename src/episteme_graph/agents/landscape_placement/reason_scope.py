"""配置の理由文が、並べて出す逐語引用の範囲を越えていないかの照合（IK-0967 / IK-0677）。

LS4（evidence 必須・``evidence_quote`` の verbatim 検査）は**緩めない**。ここは理由文
（LLM が書く日本語の要約）に現れる**固有の語**（ラテン文字の略語・固有名・型番。
``BAO`` / ``Ia`` / ``DESI`` / ``DR2`` 等）が引用に現れるかを見るだけで、現れない語は
validator が warning で残し、画面は理由に「引用に無い語」の一文を添える
（理由を削らない・配置を落とさない = P4）。

日本語の語は形態素の切り方で誤検出が増えるので v1 は対象にしない（未照合として残す）。
照合は ``alias_matching.text_mentions_alias``（語境界付き・3 文字以下は大小区別）を
使い、部分文字列一致を再実装しない。

stdlib + 同パッケージの alias_matching のみに依存（backend の読み時投影からも使う）。
"""

from __future__ import annotations

import re
from typing import Iterable

from episteme_graph.agents.alias_matching import text_mentions_alias

#: 照合する固有の語（ラテン文字で始まる英数字・ハイフン列。2 文字以上）。
_TERM_RE = re.compile(r"(?<![0-9A-Za-z_])[A-Za-z][A-Za-z0-9\-]*[A-Za-z0-9]")


def reason_terms(reason: str) -> list[str]:
    """理由文に現れる固有の語（出現順・重複なし・2 文字以上）。"""
    seen: set[str] = set()
    out: list[str] = []
    for match in _TERM_RE.finditer(str(reason or "")):
        term = match.group(0).strip("-")
        if len(term) < 2 or term in seen:
            continue
        seen.add(term)
        out.append(term)
    return out


def reason_terms_beyond_quote(reason: str, quotes: Iterable[str]) -> list[str]:
    """理由文の固有の語のうち、どの引用にも現れない語（出現順）。

    引用が 1 つも無ければ照合しない（空リスト — 引用の欠落は LS4 の別の検査が扱う）。
    """
    texts = [str(q or "") for q in quotes or () if str(q or "").strip()]
    if not texts:
        return []
    joined = " ␟ ".join(texts)
    return [term for term in reason_terms(reason) if not text_mentions_alias(joined, term)]


__all__ = ["reason_terms", "reason_terms_beyond_quote"]
