"""知識ランドスケープ配置の状態名 — Python 正本 ⇄ 管理画面 JS の逐語ミラー（第 23 波 F5）。

正本は ``core/label_vocab.py::LANDSCAPE_STATUS_LABELS``（``core/landscape/schema.py::
STATUS_LABELS`` はその別名）。教材管理の位置づけモーダル（``admin.js`` の
``LANDSCAPE_STATUS_FALLBACK_LABELS``）とリリース前の確認（``admin-release-review.js``
の ``STATUS_FALLBACK_LABELS``）は、サーバの ``status_label`` が取れないときだけ使う
フォールバック表で、正本と逐語一致させる。行のボタン「再検討」を押した結果の状態名が
「確認待ち（AI推定）」（学習者向けの出所ラベル）と別の語になっていた不一致を、ここで固定する。
"""

from __future__ import annotations

import re
from pathlib import Path

from core import label_vocab
from core.landscape import schema as landscape_schema

_ROOT = Path(__file__).resolve().parents[2]
_JS_DIR = _ROOT / "frontend" / "public" / "js"
_ENTRY_RE = re.compile(r"(?:\"([^\"]+)\"|([A-Za-z_][\w$]*))\s*:\s*\"([^\"]*)\"")


def _js_table(filename: str, name: str) -> dict[str, str]:
    src = (_JS_DIR / filename).read_text(encoding="utf-8")
    marker = "var " + name + " = {"
    assert marker in src, f"{filename} に {name} が無い"
    start = src.index(marker)
    block = src[start + len(marker) : src.index("};", start)]
    return {quoted or bare: value for quoted, bare, value in _ENTRY_RE.findall(block)}


def test_landscape_schema_labels_are_the_label_vocab_table():
    assert landscape_schema.STATUS_LABELS is label_vocab.LANDSCAPE_STATUS_LABELS
    assert set(label_vocab.LANDSCAPE_STATUS_LABELS) == set(landscape_schema.STATUSES)


def test_admin_js_fallback_mirrors_the_canonical_table():
    table = _js_table("admin.js", "LANDSCAPE_STATUS_FALLBACK_LABELS")
    assert table == dict(label_vocab.LANDSCAPE_STATUS_LABELS)


def test_release_review_fallback_mirrors_the_canonical_table():
    table = _js_table("admin-release-review.js", "STATUS_FALLBACK_LABELS")
    assert table == dict(label_vocab.LANDSCAPE_STATUS_LABELS)


def test_reconsider_button_and_resulting_status_share_the_word():
    """押したボタン「再検討」と、押した後の状態名が同じ語で読める。"""
    assert "再検討" in label_vocab.LANDSCAPE_STATUS_LABELS["review_required"]
