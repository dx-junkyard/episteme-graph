"""IK-0375: 教材の準備中・未生成の事実文（preparation_notice）の描画（静的検査）。

サーバ（``GET .../topics/{tid}/material``）が PDF 由来チャンクへ縮退したときだけ返す
``preparation_notice`` を、app.js が教材本文の上に事実文1行で描くことを固定する。
文言の正本は ``core/label_vocab.py``（``MATERIAL_PREPARING_NOTICE`` /
``MATERIAL_NOT_GENERATED_NOTICE``）で、JS に書き写さない。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from core.label_vocab import (  # noqa: E402
    MATERIAL_NOT_GENERATED_NOTICE,
    MATERIAL_PREPARING_NOTICE,
)

APP_JS = ROOT / "frontend" / "public" / "js" / "app.js"
INDEX_HTML = ROOT / "frontend" / "public" / "index.html"
STYLES_CSS = ROOT / "frontend" / "public" / "css" / "styles.css"


def _app() -> str:
    return APP_JS.read_text(encoding="utf-8")


def test_app_reads_preparation_notice_from_material_response():
    src = _app()
    assert "preparation_notice" in src
    assert "topicMaterialNotice" in src


def test_app_does_not_copy_notice_sentences():
    src = _app()
    for sentence in (MATERIAL_PREPARING_NOTICE, MATERIAL_NOT_GENERATED_NOTICE):
        assert sentence not in src
    # 文の一部（冒頭句）も書き写していない
    assert "論文の本文をそのまま表示しています" not in src


def test_notice_rendered_as_fact_line_without_ui_anchor():
    src = _app()
    idx = src.index('class="material-notice"')
    snippet = src[max(0, idx - 400): idx + 200]
    assert "escHtml(state.topicMaterialNotice)" in snippet
    assert "if (state.topicMaterialNotice)" in snippet
    # 事実文なので data-ui-anchor を付けない（描画する行そのものを検査する）
    line = src[src.rfind("\n", 0, idx): src.index("\n", idx)]
    assert "data-ui-anchor" not in line


def test_notice_is_reset_on_topic_switch():
    src = _app()
    assert "state.topicMaterialNotice = null;" in src


def test_material_notice_css_is_not_warning_colored():
    css = STYLES_CSS.read_text(encoding="utf-8")
    m = re.search(r"\.material-notice\s*\{([^}]*)\}", css)
    assert m, ".material-notice の CSS が無い"
    body = m.group(1)
    assert "flex: 0 0 auto" in body
    assert "danger" not in body and "warning" not in body
    assert "background" not in body


def test_index_html_carries_cache_busting_versions():
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert re.search(r'<script src="/js/app\.js\?v=[^"]+"></script>', html)
    assert re.search(r'href="/css/styles\.css\?v=[^"]+"', html)
