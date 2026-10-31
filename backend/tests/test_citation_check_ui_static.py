"""IK-0545: 逐語引用一致検査の学習画面の担体（静的検査）。"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_APP = (_ROOT / "frontend" / "public" / "js" / "app.js").read_text(encoding="utf-8")
_CSS = (_ROOT / "frontend" / "public" / "css" / "styles.css").read_text(encoding="utf-8")


def _fn(src: str, name: str) -> str:
    start = src.index(f"function {name}(")
    depth = 0
    for i in range(src.index("{", start), len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError(name)


def test_response_field_is_kept_on_the_message():
    assert "citation_check: data.citation_check || null" in _APP


def test_line_is_rendered_from_server_note_and_inside_render_ai_content():
    body = _fn(_APP, "renderCitationCheckLine")
    assert "item.note" in body and "escHtml(item.note)" in body
    # 文言をフロントに焼き込まない。
    assert "一致を確認できていません" not in _APP
    assert "data-citation-check" in body
    assert "renderCitationCheckLine(msg)" in _fn(_APP, "renderAiContent")


def test_line_is_drawn_before_citation_linkify():
    body = _fn(_APP, "renderAiContent")
    assert body.index("renderCitationCheckLine(msg)") < body.index("linkifyCitations(html, msg)")


def test_css_uses_no_warning_color():
    block = _CSS[_CSS.index(".citation-check-line"):]
    block = block[: block.index(".citation-check-fact")] + block[block.index(".citation-check-fact"):][:200]
    for bad in ("--color-text-danger", "--color-text-warning", "red", "orange", "#f", "background"):
        assert bad not in block, bad
    assert re.search(r"color:\s*var\(--color-text-tertiary\)", block)
