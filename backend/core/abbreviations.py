"""abbreviations: 「フル表記 (略語)」「略語 (フル表記)」の決定論抽出の正本（純関数・非LLM）。

もとは ``core/document_pipeline/figure_context.py`` の図単位の略語辞書の実装で、論文レーダーの
キーフレーズ供給（``core/paper_discovery/radar.py`` — 略語はフル表記の句を優先する・設計書
``paper_radar_design.md`` §23）でも同じ規則を使うため、ここへ切り出した。figure_context は
ここを参照する（規則を 2 箇所に書かない）。

FastAPI / DB / LLM / os.environ は import しない。
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable

# 「フル表記 (略語)」。略語側の末尾 's' は複数形として別扱い（group 3）。
ABBR_AFTER_FULL_RE = re.compile(
    r"\b([A-Za-z][A-Za-z\-/ ]{2,80}?)\s*\(\s*([A-Z][A-Za-z0-9\-]{1,11}?)(s)?\s*\)"
)
# 「略語 (フル表記)」。
FULL_AFTER_ABBR_RE = re.compile(
    r"\b([A-Z][A-Za-z0-9\-]{1,11})\s*\(\s*([a-z][^)]{3,80})\s*\)"
)


def initials_match(full: str, abbr: str) -> bool:
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


def trim_full_to_match(full: str, abbr: str, *, shortest: bool = False) -> str | None:
    """full 句の先頭語を 1 つずつ落とした接尾辞のうち、initials が一致するものを返す。

    既定（``shortest=False``）は最長の接尾辞（図の略語辞書の従来の挙動）。``shortest=True`` は
    最短の接尾辞（検索語として、文中の前置き ``measurement of the`` を飲み込まない）。
    全接尾辞が不一致なら None。
    """
    words = full.strip().split()
    starts = range(len(words))
    if shortest:
        starts = reversed(range(len(words)))
    for start in starts:
        candidate = " ".join(words[start:])
        if initials_match(candidate, abbr):
            return candidate
    return None


def register_abbreviation(result: dict[str, str], abbr: str, full: str) -> None:
    """``result`` に (abbr → full) を first-wins で登録する（initials が合わなければ登録しない）。"""
    full = full.strip().rstrip(".,;:　 ")
    if not full or not abbr:
        return
    if not initials_match(full, abbr):
        return
    if abbr in result:
        return
    result[abbr] = full


def extract_abbreviations(
    texts: Iterable[Any], *, shortest: bool = False, normalize_width: bool = False,
) -> dict[str, str]:
    """複数のテキストから略語辞書（abbr → full・初出順）を抽出する。

    ``normalize_width=True`` は NFKC で全角括弧（``（FoG）``）を半角へ寄せてから照合する
    （日本語の文中の英語の定義を拾うため。抽出にだけ使い、表示はフル表記の原文の語のまま）。
    複数形の略語（``FoGs``）は ``abbr + "s"`` としても登録する（図の略語辞書と同じ）。
    """
    result: dict[str, str] = {}
    for raw in texts or ():
        text = str(raw or "")
        if normalize_width:
            text = unicodedata.normalize("NFKC", text)
        if not text.strip():
            continue
        for m in ABBR_AFTER_FULL_RE.finditer(text):
            abbr = m.group(2)
            has_plural = m.group(3) == "s"
            full = trim_full_to_match(m.group(1), abbr, shortest=shortest)
            if full is None:
                continue
            register_abbreviation(result, abbr, full)
            if has_plural:
                register_abbreviation(result, abbr + "s", full)
        for m in FULL_AFTER_ABBR_RE.finditer(text):
            register_abbreviation(result, m.group(1), m.group(2))
    return result
