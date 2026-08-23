"""L層 standardization_status の Python ⇄ JS ミラーの完全一致（訳語統一 D2）。

`standardization_status` の日本語ラベルは、かつて3画面でそれぞれ別語だった:

- `deliberation.js`（教員の「深く検討」）      … emerging_common = 「普及しつつある」
- `app.js`（学習者の「共通部品として」面）      … 「共通化進行中」/ unknown = 「未判定」
- `admin.js`（ナレッジライブラリ一覧）          … standard = 「標準（教科書級）」

同じ DB 列の同じ値が画面ごとに違う語で出ていたため、サーバ
（`core/library/schema.py::STANDARDIZATION_STATUS_LABELS`）を正本に一本化した。
表そのものはフロントに残す（API に label を足してレンダリングを変えるのは別件）が、
ここで逐語一致を固定する。片側だけを直したらこのテストが落ちる。

JS 側のパースは `test_doubt_vocab_mirror.py` と同じ正規表現方式（Node を呼ばない）。
"""

from __future__ import annotations

import re
from pathlib import Path

from core.library import schema as library_schema

_ROOT = Path(__file__).resolve().parents[2]
_JS_DIR = _ROOT / "frontend" / "public" / "js"

_ENTRY_RE = re.compile(r"(?:\"([^\"]+)\"|([A-Za-z_][\w$]*))\s*:\s*\"([^\"]*)\"")

#: JS ファイル名 → その中の語彙表の var 名。3表すべてが同じ DB 列を描く。
_JS_TABLES = {
    "deliberation.js": "STANDARDIZATION_STATUS_LABELS",
    "app.js": "MATERIAL_LIBRARY_STATUS_LABELS",
    "admin.js": "_libraryStandardizationLabels",
}


def _js_table(filename: str, var_name: str) -> dict[str, str]:
    src = (_JS_DIR / filename).read_text(encoding="utf-8")
    marker = "var " + var_name + " = {"
    assert marker in src, filename + " に " + var_name + " が無い"
    start = src.index(marker)
    block = src[start + len(marker) : src.index("};", start)]
    return {
        (quoted or bare): value for quoted, bare, value in _ENTRY_RE.findall(block)
    }


class TestServerCanon:
    def test_labels_cover_exactly_the_status_vocabulary(self):
        assert set(library_schema.STANDARDIZATION_STATUS_LABELS) == set(
            library_schema.STANDARDIZATION_STATUSES
        )

    def test_no_label_is_blank(self):
        for key, text in library_schema.STANDARDIZATION_STATUS_LABELS.items():
            assert text.strip(), key

    def test_emerging_common_does_not_claim_external_adoption(self):
        """emerging_common は「コーパス内で反復するが外部標準ではない」状態。

        「普及」「浸透」のような語は**外部の分野での定着**を示唆してしまい、
        本システムが見つける発見的価値（knowledge_network_vision 修正④）を
        既成事実にすり替える（knowledge_network_vision §3 修正③）。
        """
        text = library_schema.STANDARDIZATION_STATUS_LABELS[
            library_schema.STANDARDIZATION_STATUS_EMERGING_COMMON
        ]
        for forbidden in ("普及", "浸透", "定着"):
            assert forbidden not in text


class TestTableParity:
    def test_every_js_table_matches_the_server_canon(self):
        for filename, var_name in _JS_TABLES.items():
            assert _js_table(filename, var_name) == dict(
                library_schema.STANDARDIZATION_STATUS_LABELS
            ), filename + "::" + var_name + " の Python ⇄ JS が不一致"

    def test_the_three_js_tables_are_identical(self):
        tables = [
            _js_table(filename, var_name) for filename, var_name in _JS_TABLES.items()
        ]
        for other in tables[1:]:
            assert other == tables[0], "3画面の standardization_status 語彙が分裂している"

    def test_old_split_labels_are_gone_from_the_frontend(self):
        """統一前の語が復活していないこと（負のアサーション）。"""
        for path in sorted(_JS_DIR.glob("*.js")):
            src = path.read_text(encoding="utf-8")
            for stale in ("普及しつつある", "共通化進行中", "分野内標準", "標準（教科書級）"):
                assert stale not in src, path.name + " に旧語 " + stale + " が残っている"


class TestFrontEndMirrorPointsAtTheServer:
    def test_each_js_table_names_the_server_source_of_truth(self):
        for filename in _JS_TABLES:
            src = (_JS_DIR / filename).read_text(encoding="utf-8")
            assert "core/library/schema.py" in src, filename
            assert "test_library_vocab_mirror.py" in src, filename


class TestAdminBadgeKeepsTheDroppedGloss:
    """admin.js の旧ラベルが括弧で持っていた語釈を捨てない（P4）。

    「標準（教科書級）」→「標準」に短縮した分の情報は、同じ関数のバッジ
    `title` 属性へ移した。
    """

    def test_badge_title_explains_the_shortened_labels(self):
        src = (_JS_DIR / "admin.js").read_text(encoding="utf-8")
        start = src.index("function _libraryStandardizationBadgeHtml(status)")
        block = src[start : src.index("\n  }\n", start)]
        assert "教科書級" in block
        assert "分野標準" in block
