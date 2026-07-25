"""非ベクトルKBの汎用索引エンジン（正本）。

``backend/core/admin_assistant/knowledge.py``（161行・見出し節単位索引 + トークン
重なり検索）を一般化したもの。見出し（``##``〜``####``）単位でチャンクを切り出し、
語彙重なりスコアで検索する非ベクトル方式は変えず、以下を汎用パラメータ化する:

- front-matter の扱い（読み飛ばすだけ / 解釈してメタデータ化する）
- マニュアル専用の決定論変換（HTMLコメント除去・Markdownテーブル平坦化）
- TODO マーカー入りチャンクの索引除外

``core/help_kb`` は FastAPI を import しない（core 層の規約）。索引の削除 API は
持たない（追記・再構築のみ）。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

HEADING_RE = re.compile(r"^(#{2,4})\s+(.*?)\s*$")
ANCHOR_RE = re.compile(r"\{#([A-Za-z0-9_\-]+)\}")
WORD_RE = re.compile(r"[0-9A-Za-z]+|[぀-ヿ一-鿿]")

_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_TABLE_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$")


def slugify(text: str) -> str:
    """明示 ``{#anchor}`` を優先し、無ければ見出しテキストから決定論的に生成する。"""
    explicit = ANCHOR_RE.search(text)
    if explicit:
        return explicit.group(1)
    cleaned = ANCHOR_RE.sub("", text).strip().lower()
    cleaned = re.sub(r"[^0-9a-z぀-ヿ一-鿿]+", "-", cleaned)
    return cleaned.strip("-")


def tokenize(text: str) -> set:
    return set(m.group(0).lower() for m in WORD_RE.finditer(text or ""))


def strip_front_matter(lines: list[str]) -> list[str]:
    """front-matter を解釈せず読み飛ばすだけ（admin_operations の現行挙動）。"""
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return lines[i + 1 :]
    return lines


def parse_front_matter(lines: list[str]) -> tuple[dict, list[str]]:
    """front-matter を単純な ``key: value`` / インラインリストとして解釈する。

    pyyaml には依存しない（本KBの規模・用途に対して過剰投資のため）。
    front-matter が無い、または閉じ ``---`` が見つからない場合は ``({}, lines)``。
    """
    if not lines or lines[0].strip() != "---":
        return {}, lines
    meta: dict = {}
    end: Optional[int] = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
        line = lines[i]
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if not key:
            continue
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            meta[key] = [x.strip() for x in inner.split(",") if x.strip()]
        else:
            meta[key] = val
    if end is None:
        return {}, lines
    return meta, lines[end + 1 :]


def _contains_todo_comment(text: str) -> bool:
    """本文（変換前・生テキスト）に ``TODO`` を含む HTML コメントがあるか。"""
    for m in _COMMENT_RE.finditer(text or ""):
        if "TODO" in m.group(0):
            return True
    return False


def strip_comments(text: str) -> str:
    """HTMLコメントを除去する（TODO マーカーを含まないもののみ呼び出し側が通す）。"""
    return _COMMENT_RE.sub("", text or "")


_SEP_CELL_RE = re.compile(r"^:?-+:?$")


def _is_table_separator(line: str) -> bool:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return False
    inner = stripped.strip("|")
    cells = inner.split("|")
    return bool(cells) and all(_SEP_CELL_RE.match(c.strip()) for c in cells)


def flatten_tables(text: str) -> str:
    """Markdown テーブルを「見出し: 値」形式の行群へ平坦化する。

    フロントの ``renderMarkdown`` / ``mdBlocksToHtml`` がテーブル非対応のため、
    索引側でのみ決定論変換する（GitHub 上の docs 本体は表のまま）。
    """
    lines = (text or "").splitlines()
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if _TABLE_ROW_RE.match(line) and i + 1 < n and _is_table_separator(lines[i + 1]):
            headers = [c.strip() for c in line.strip().strip("|").split("|")]
            i += 2
            while i < n and _TABLE_ROW_RE.match(lines[i]) and not _is_table_separator(lines[i]):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                pairs = [f"{h}: {c}" for h, c in zip(headers, cells) if h]
                if pairs:
                    out.append(" / ".join(pairs))
                i += 1
            out.append("")
        else:
            out.append(line)
            i += 1
    return "\n".join(out)


def build_section_index(
    directory: Path,
    *,
    manual_transforms: bool = False,
    audience_tag: Optional[str] = None,
) -> tuple[dict, list[dict]]:
    """``directory`` 内の ``*.md`` を見出し単位でパースし索引を構築する。

    Returns:
        ``(index, excluded)``。``index`` は ``"<file>#<anchor>" -> {title, body,
        file, anchor[, audience]}``。``excluded`` は TODO マーカー入りコメントを
        含むために索引から外されたチャンクのリスト
        （``manual_transforms=True`` のときのみ判定・適用。admin_operations 側
        （``manual_transforms=False``）は現行挙動を変えないため常に空）。
    """
    index: dict = {}
    excluded: list[dict] = []
    if directory is None or not directory.is_dir():
        return index, excluded

    for md in sorted(directory.glob("*.md")):
        try:
            raw = md.read_text(encoding="utf-8")
        except OSError:
            continue
        lines = raw.splitlines()
        if manual_transforms:
            _front_matter, lines = parse_front_matter(lines)
        else:
            lines = strip_front_matter(lines)

        cur_anchor: Optional[str] = None
        cur_title = ""
        buf: list[str] = []

        def _flush() -> None:
            if not cur_anchor:
                return
            raw_body = "\n".join(buf).strip()
            title = ANCHOR_RE.sub("", cur_title).strip()
            if manual_transforms and _contains_todo_comment(raw_body):
                entry = {"file": md.name, "anchor": cur_anchor, "title": title}
                if audience_tag:
                    entry["audience"] = audience_tag
                excluded.append(entry)
                return
            body = raw_body
            if manual_transforms:
                body = strip_comments(body)
                body = flatten_tables(body)
            section = {
                "title": title,
                "body": body.strip(),
                "file": md.name,
                "anchor": cur_anchor,
            }
            if audience_tag:
                section["audience"] = audience_tag
            index[f"{md.name}#{cur_anchor}"] = section

        for line in lines:
            m = HEADING_RE.match(line)
            if m:
                _flush()
                cur_title = m.group(2)
                cur_anchor = slugify(cur_title)
                buf = []
            else:
                buf.append(line)
        _flush()
    return index, excluded


def score_sections(query: str, sections: list[dict], *, limit: int = 3) -> list[dict]:
    """節（title/body を持つ dict）を query との語彙重なりでスコアリングして返す。"""
    q_tokens = tokenize(query)
    scored: list[tuple[float, dict]] = []
    for sec in sections:
        title = sec.get("title", "")
        body = sec.get("body", "")
        hay = tokenize(f"{title} {body}")
        overlap = len(q_tokens & hay)
        title_bonus = 2 if q_tokens & tokenize(title) else 0
        score = overlap + title_bonus
        if score <= 0:
            continue
        scored.append((float(score), sec))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [sec for _, sec in scored[: max(1, limit)]]
