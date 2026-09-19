"""知識の転用層 P4-5 の語彙表の Python ⇄ JS ミラーの完全一致（§8「ラベル」）。

`CHALLENGE_MODE_LABELS` / `EVIDENCE_LINE_KIND_LABELS` / `CITATION_INTENT_LABELS` は
`backend/core/label_vocab.py` が正本で、管理UI 側（`doubt-atlas.js` /
`admin-lecture-studio.js`）はそれを**逐語ミラー**する。片側だけを直したらここが落ちる。

JS 側のパースは `test_doubt_vocab_mirror.py` / `test_element_vocab_mirror.py` と
同じ正規表現方式（Node を呼ばない）。
"""

from __future__ import annotations

import re
from pathlib import Path

from core import label_vocab

_ROOT = Path(__file__).resolve().parents[2]
_JS_DIR = _ROOT / "frontend" / "public" / "js"
_DOUBT_JS = _JS_DIR / "doubt-atlas.js"
_STUDIO_JS = _JS_DIR / "admin-lecture-studio.js"

_ENTRY_RE = re.compile(r"(?:\"([^\"]+)\"|([A-Za-z_][\w$]*))\s*:\s*\"([^\"]*)\"")


def _js_table(path: Path, name: str) -> dict[str, str]:
    src = path.read_text(encoding="utf-8")
    marker = "var " + name + " = {"
    assert marker in src, f"{path.name} に {name} が無い"
    start = src.index(marker)
    end = src.index("};", start)
    block = src[start + len(marker) : end]
    out: dict[str, str] = {}
    for quoted, bare, value in _ENTRY_RE.findall(block):
        out[quoted or bare] = value
    return out


class TestVocabMirror:
    """3表とも JS 側が正本と逐語一致すること（キー集合も値も）。"""

    def test_challenge_mode_labels_mirror(self):
        assert _js_table(_DOUBT_JS, "CHALLENGE_MODE_LABELS") == dict(
            label_vocab.CHALLENGE_MODE_LABELS
        )

    def test_evidence_line_kind_labels_mirror(self):
        assert _js_table(_DOUBT_JS, "EVIDENCE_LINE_KIND_LABELS") == dict(
            label_vocab.EVIDENCE_LINE_KIND_LABELS
        )

    def test_citation_intent_labels_mirror(self):
        assert _js_table(_STUDIO_JS, "LS_CITATION_INTENT_LABELS") == dict(
            label_vocab.CITATION_INTENT_LABELS
        )

    def test_citation_intent_order_covers_every_key(self):
        """select の明示順序が語彙を過不足なく覆うこと（表示漏れ・幽霊選択肢の防止）。"""
        src = _STUDIO_JS.read_text(encoding="utf-8")
        marker = "var LS_CITATION_INTENT_ORDER = ["
        assert marker in src
        block = src[src.index(marker) + len(marker) : src.index("];", src.index(marker))]
        order = re.findall(r"\"([^\"]+)\"", block)
        assert order
        assert set(order) == set(label_vocab.CITATION_INTENT_LABELS)
        assert len(order) == len(set(order))

    def test_no_intent_label_for_unrecorded(self):
        """「記録なし」(NULL) は語彙表に入れない — 正本にもミラーにも無いこと。

        未選択は「記録しない」という UI 上の選択肢であって、引用の意図の 1 つでは
        ない（DB は NULL = 記録なし）。表に混ぜると意図を推定したことになる。
        """
        assert "" not in label_vocab.CITATION_INTENT_LABELS
        js = _js_table(_STUDIO_JS, "LS_CITATION_INTENT_LABELS")
        assert "記録しない" not in js.values()
