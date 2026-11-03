"""学習チャットの検索結果で「同じ論文の別の版」の同じ箇所を 1 件に畳む（第 25 周 第 5 波・IK-0996）。

同じ論文を TeX と PDF で取り込むと、同じ段落が 2 つの document のチャンクとして検索に
並び、出典の枠を同じ内容で占める（学生には同じ題名の同じ文が 2 回見え、どちらが TeX 版かも
分からない）。分野の地図の反復の数え方で直した IK-0846 と同じ根（document 単位で数え、
論文単位の同一性を見ない）の RAG の面。

- **同じ論文かの判定は正本を使う**（``core.library.identity_candidates.same_paper`` —
  arXiv ID が両方取れれば ID の一致、取れなければ題名の正規化完全一致。新しい同一視の
  規則を作らない）。
- **同じ箇所かの判定**は、数式のプレースホルダ（``[[FORMULA_i]]``）を除いた本文を英数字だけに
  正規化して比べる（版ごとに式の LaTeX が違っても本文が同じなら同じ箇所）。別の箇所は畳まない。
- **残す版**: ⓪会話で既に出典番号を持つチャンク（``prefer_chunk_ids``・第 6 波）→
  ①呼び出し側が優先する document（焦点の論文）→ ②AI が復元した式の少ない版
  （TeX 版は原文の LaTeX を持つ）→ ③TeX の取り込み → ④検索の順位。残した行は畳んだ行の
  最も高い類似度を引き継ぎ（採用の下限で落ちない）、群の先頭の位置に置く。
- 畳んだ事実は残した行の ``version_note``（事実文・数値なし）で出典に添える。

純関数（FastAPI / DB / LLM を import しない。``same_paper`` は呼び出し時に遅延 import）。
"""

from __future__ import annotations

import difflib
import re
from typing import Any, Iterable, Mapping

__all__ = [
    "PAPER_VERSION_NOTE",
    "collapse_paper_versions",
    "is_tex_ingest",
    "same_passage",
]

#: 畳んだ出典に添える事実文（学習者向け。数値を書かない）。
PAPER_VERSION_NOTE = "同じ論文の別の版（TeX 版・PDF 版など）にも同じ箇所があり、1 つにまとめています。"

#: 同じ箇所とみなす類似度の下限（正規化した本文の一致の割合。内部の閾値で表示しない）。
_SAME_PASSAGE_RATIO = 0.9
#: 比べる本文の長さの上限（長い段落でも比較を有界にする）。
_COMPARE_MAX_CHARS = 1200
#: 片方がもう片方に含まれるとみなすための短い側の最小長（短すぎる一致で畳まない）。
_CONTAINMENT_MIN_CHARS = 80

_FORMULA_PLACEHOLDER_RE = re.compile(r"\[\[FORMULA_\d+\]\]")
_NON_ALNUM_RE = re.compile(r"[\W_]+", re.UNICODE)
_TEX_SUFFIXES = (".tar.gz", ".tgz", ".tex", ".gz", ".tar")


def _normalized_passage(text: Any) -> str:
    raw = _FORMULA_PLACEHOLDER_RE.sub(" ", str(text or ""))
    return _NON_ALNUM_RE.sub("", raw).casefold()[:_COMPARE_MAX_CHARS]


def same_passage(a: Any, b: Any) -> bool:
    """2 つのチャンク本文が同じ箇所か（式のプレースホルダと空白・記号の差を無視する）。"""
    left = _normalized_passage(a)
    right = _normalized_passage(b)
    if not left or not right:
        return False
    if left == right:
        return True
    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    if len(shorter) >= _CONTAINMENT_MIN_CHARS and shorter in longer:
        return True
    return difflib.SequenceMatcher(None, left, right, autojunk=False).ratio() >= _SAME_PASSAGE_RATIO


def is_tex_ingest(meta: Mapping[str, Any] | None) -> bool:
    """document が TeX（arXiv の e-print）から取り込まれたか（source_url / filename / source_path）。"""
    if not meta:
        return False
    url = str(meta.get("source_url") or "").lower()
    if "/src/" in url or "/e-print/" in url:
        return True
    for key in ("filename", "source_path", "source_url"):
        value = str(meta.get(key) or "").lower().split("?")[0]
        if value.endswith(_TEX_SUFFIXES):
            return True
    return False


def _same_paper(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    from core.library.identity_candidates import same_paper  # 同一視の正本（IK-0919）

    try:
        return bool(same_paper(dict(a), dict(b)))
    except Exception:  # noqa: BLE001 - 判定できなければ畳まない（情報を落とさない）
        return False


def collapse_paper_versions(
    rows: Iterable[Mapping[str, Any]] | None,
    document_meta: Mapping[str, Mapping[str, Any]] | None,
    *,
    prefer_document_ids: Iterable[str] = (),
    prefer_chunk_ids: Iterable[str] = (),
    reconstructed_key: str = "reconstructed_formula_count",
) -> list[dict]:
    """同じ論文の別の版の同じ箇所を 1 行に畳む（入力は変更しない・並びは保つ）。

    ``rows`` は検索結果（``document_id`` / ``raw_text`` か ``text`` / ``score`` を持つ dict）。
    ``document_meta`` は ``{document_id: {title, filename, source_url, source_path}}``。
    メタの無い行・同じ document の行どうしは畳まない。
    """
    items = [dict(r) for r in (rows or [])]
    meta = {str(k): dict(v) for k, v in (document_meta or {}).items() if v}
    prefer = {str(d) for d in (prefer_document_ids or ()) if d}
    # 第 25 周 第 6 波: 会話で既に出典番号を持つチャンク（前の往復で引用した版）を最優先で残す。
    # 同じ箇所の別の版を往復ごとに選び直すと、別のチャンクとして新しい番号が振られ、前の回答の
    # [出典5] が今回 [出典12] になる（出典番号は chunk 単位で会話の中に固定 = IK-0432）。
    prefer_chunks = {str(c) for c in (prefer_chunk_ids or ()) if c}
    if len(items) < 2 or len({str(r.get("document_id") or "") for r in items}) < 2:
        return items

    parent = list(range(len(items)))

    def _find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    paper_cache: dict[tuple[str, str], bool] = {}

    def _papers_match(doc_a: str, doc_b: str) -> bool:
        key = (doc_a, doc_b) if doc_a <= doc_b else (doc_b, doc_a)
        if key not in paper_cache:
            paper_cache[key] = bool(
                doc_a in meta and doc_b in meta and _same_paper(meta[doc_a], meta[doc_b])
            )
        return paper_cache[key]

    for i in range(len(items)):
        doc_i = str(items[i].get("document_id") or "")
        for j in range(i + 1, len(items)):
            doc_j = str(items[j].get("document_id") or "")
            if not doc_i or not doc_j or doc_i == doc_j:
                continue
            if _find(i) == _find(j):
                continue
            if not _papers_match(doc_i, doc_j):
                continue
            text_i = items[i].get("raw_text") or items[i].get("text")
            text_j = items[j].get("raw_text") or items[j].get("text")
            if same_passage(text_i, text_j):
                parent[_find(j)] = _find(i)

    groups: dict[int, list[int]] = {}
    for idx in range(len(items)):
        groups.setdefault(_find(idx), []).append(idx)

    def _rank(idx: int) -> tuple:
        row = items[idx]
        doc = str(row.get("document_id") or "")
        try:
            reconstructed = int(row.get(reconstructed_key) or 0)
        except (TypeError, ValueError):
            reconstructed = 0
        return (
            str(row.get("id") or "") not in prefer_chunks,
            doc not in prefer,
            reconstructed,
            not is_tex_ingest(meta.get(doc)),
            idx,
        )

    keep: dict[int, dict] = {}
    for members in groups.values():
        if len(members) == 1:
            keep[members[0]] = items[members[0]]
            continue
        chosen_idx = min(members, key=_rank)
        chosen = dict(items[chosen_idx])
        scores = []
        for m in members:
            try:
                scores.append(float(items[m].get("score")))
            except (TypeError, ValueError):
                continue
        if scores:
            chosen["score"] = max(scores)
        chosen["version_note"] = PAPER_VERSION_NOTE
        keep[min(members)] = chosen
    return [keep[idx] for idx in sorted(keep)]
