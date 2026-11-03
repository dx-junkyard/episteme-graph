"""IK-0376: リリース前の確認で一括確認の対象外になった論文の事実文（skipped_note）の描画。

``POST /api/admin/landscape/courses/{course_id}/placements/accept`` が対象外の論文が
あったときだけ返す ``skipped_note`` を、admin-release-review.js が色で強調せず
そのまま描くことを固定する（文言の正本はサーバ）。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JS = ROOT / "frontend" / "public" / "js" / "admin-release-review.js"
ADMIN_HTML = ROOT / "frontend" / "public" / "admin.html"
STYLES_CSS = ROOT / "frontend" / "public" / "css" / "styles.css"


def _src() -> str:
    return JS.read_text(encoding="utf-8")


def test_reads_skipped_note_from_accept_response():
    src = _src()
    assert "data.skipped_note" in src
    assert "setSkippedNote(" in src


def test_skipped_note_carrier_is_plain_fact_line():
    src = _src()
    assert 'id="release-review-skipped-note"' in src
    idx = src.index('id="release-review-skipped-note"')
    line = src[idx - 20: idx + 160]
    assert "data-ui-anchor" not in line
    assert "danger" not in line
    # 本文はサーバ文言を textContent で素通しする（innerHTML にしない）
    fn = src[src.index("function setSkippedNote"): src.index("function errorDetail")]
    assert "textContent" in fn
    assert "innerHTML" not in fn
    assert "hidden = !message" in fn


def test_skipped_note_cleared_on_step_render():
    src = _src()
    fn = src[src.index("function renderStep()"):]
    fn = fn[: fn.index("\n  }\n")]
    assert 'setSkippedNote("")' in fn


def test_es5_only():
    src = _src()
    assert not re.search(r"\b(const|let)\s", src)
    assert "=>" not in src


def test_css_and_cache_busting():
    css = STYLES_CSS.read_text(encoding="utf-8")
    m = re.search(r"\.release-review-skipped-note\s*\{([^}]*)\}", css)
    assert m
    assert "danger" not in m.group(1)
    html = ADMIN_HTML.read_text(encoding="utf-8")
    assert re.search(r'/js/admin-release-review\.js\?v=[^"]+"', html)
    assert re.search(r'href="/css/styles\.css\?v=[^"]+"', html)
