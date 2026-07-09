"""操作ナレッジベース（KB）ローダ — 説明モードの根拠（設計 §5.1）。

- 実体: ``docs/admin_operations/*.md``。各節は見出しで区切り、`{#anchor}` で
  capability の ``howto_doc`` に結び付ける（KB を registry に強く紐付ける）。
- role / screen フィルタは capability 側が持つため、KB 検索は
  **role で絞った capability の howto_doc** に限定する（P1: 権限外の手順を出さない）。
- リアルタイム生成はしない（P4 / P6）。KB に節が無ければ「未整備」を返す。
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

_HEADING_RE = re.compile(r"^(#{2,4})\s+(.*?)\s*$")
_ANCHOR_RE = re.compile(r"\{#([A-Za-z0-9_\-]+)\}")
_WORD_RE = re.compile(r"[0-9A-Za-z]+|[぀-ヿ一-鿿]")


def _candidate_dirs() -> list[Path]:
    here = Path(__file__).resolve()
    cands = []
    # backend/core/admin_assistant/knowledge.py -> parents[3] = repo root
    try:
        cands.append(here.parents[3] / "docs" / "admin_operations")
    except IndexError:
        pass
    # 実行 cwd からの探索（docker / 別レイアウト対策）
    cwd = Path.cwd()
    cands.append(cwd / "docs" / "admin_operations")
    cands.append(cwd.parent / "docs" / "admin_operations")
    return cands


def _kb_dir() -> Optional[Path]:
    for d in _candidate_dirs():
        if d.is_dir():
            return d
    return None


def _slugify(text: str) -> str:
    explicit = _ANCHOR_RE.search(text)
    if explicit:
        return explicit.group(1)
    cleaned = _ANCHOR_RE.sub("", text).strip().lower()
    cleaned = re.sub(r"[^0-9a-z぀-ヿ一-鿿]+", "-", cleaned)
    return cleaned.strip("-")


def _strip_front_matter(lines: list[str]) -> list[str]:
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return lines[i + 1 :]
    return lines


def _tokenize(text: str) -> set:
    return set(m.group(0).lower() for m in _WORD_RE.finditer(text or ""))


@lru_cache(maxsize=1)
def _load_index() -> dict:
    """`{ "<file>.md#<anchor>": {title, body, file} }` を構築する。"""
    index: dict = {}
    kb_dir = _kb_dir()
    if kb_dir is None:
        return index
    for md in sorted(kb_dir.glob("*.md")):
        try:
            raw = md.read_text(encoding="utf-8")
        except OSError:
            continue
        lines = _strip_front_matter(raw.splitlines())
        cur_anchor = None
        cur_title = ""
        buf: list[str] = []

        def _flush():
            if cur_anchor:
                key = f"{md.name}#{cur_anchor}"
                index[key] = {
                    "title": _ANCHOR_RE.sub("", cur_title).strip(),
                    "body": "\n".join(buf).strip(),
                    "file": md.name,
                    "anchor": cur_anchor,
                }

        for line in lines:
            m = _HEADING_RE.match(line)
            if m:
                _flush()
                cur_title = m.group(2)
                cur_anchor = _slugify(cur_title)
                buf = []
            else:
                buf.append(line)
        _flush()
    return index


def _normalize_howto(howto_doc: str) -> str:
    """`admin_operations/materials.md#upload` -> `materials.md#upload`。"""
    doc = (howto_doc or "").strip()
    if doc.startswith("admin_operations/"):
        doc = doc[len("admin_operations/") :]
    return doc


def clear_cache() -> None:
    _load_index.cache_clear()


def section_for_howto(howto_doc: str) -> Optional[dict]:
    if not howto_doc:
        return None
    return _load_index().get(_normalize_howto(howto_doc))


def search(query: str, capabilities: list, limit: int = 3) -> list[dict]:
    """role で絞り込み済みの capability 群から、query に最も近い KB 節を返す。

    各結果: ``{capability_id, title, body, citation}``。KB に節が無い capability は
    citation を持つが body="" の「未整備」マーカーとして返しうる（呼び出し側で扱う）。
    """
    q_tokens = _tokenize(query)
    scored: list[tuple[float, dict]] = []
    for cap in capabilities:
        section = section_for_howto(cap.howto_doc)
        body = section["body"] if section else ""
        title = section["title"] if section else cap.title
        # スコア: capability title / KB 本文とクエリの語重なり + タイトル一致ボーナス。
        hay = _tokenize(f"{cap.title} {cap.description} {title} {body}")
        overlap = len(q_tokens & hay)
        title_bonus = 2 if q_tokens & _tokenize(cap.title) else 0
        score = overlap + title_bonus
        if score <= 0:
            continue
        scored.append(
            (
                float(score),
                {
                    "capability_id": cap.id,
                    "screen": cap.screen,
                    "title": title,
                    "body": body,
                    "citation": f"admin_operations/{section['file']}#{section['anchor']}" if section else "",
                    "documented": bool(body),
                },
            )
        )
    scored.sort(key=lambda t: t[0], reverse=True)
    return [item for _, item in scored[: max(1, limit)]]


def kb_available() -> bool:
    return bool(_load_index())
