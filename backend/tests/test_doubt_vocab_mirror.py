"""D層 / SL層の語彙表の Python ⇄ JS ミラーの完全一致（提案 §2-2 W3）。

`frontend/public/js/doubt-atlas.js` は D層 API が段階ラベルを返さない箇所
（Assumption Atlas の軸目盛り・状態バッジ・反実仮想の区別）を自前の表で描く。
表そのものは残す（API 追加でフロントの描画を変えるのは別件）が、**正本はサーバ**
に置き、ここで逐語一致を固定する。片側だけを直したらこのテストが落ちる。

JS 側のパースは `test_element_vocab_mirror.py` と同じ正規表現方式（Node を呼ばない）。

意図的な差分は下の `_INTENTIONAL_DIFFERENCES` に**理由付きで**列挙する
（無言の allowlist を作らない）。
"""

from __future__ import annotations

import re
from pathlib import Path

from core import label_vocab
from core.doubt import schema as doubt_schema

_ROOT = Path(__file__).resolve().parents[2]
_DOUBT_JS = _ROOT / "frontend" / "public" / "js" / "doubt-atlas.js"

_ENTRY_RE = re.compile(r"(?:\"([^\"]+)\"|([A-Za-z_][\w$]*))\s*:\s*\"([^\"]*)\"")


def _js() -> str:
    return _DOUBT_JS.read_text(encoding="utf-8")


def _js_table(name: str) -> dict[str, str]:
    src = _js()
    marker = "var " + name + " = {"
    assert marker in src, "JS に " + name + " が無い"
    start = src.index(marker)
    end = src.index("};", start)
    block = src[start + len(marker) : end]
    out: dict[str, str] = {}
    for quoted, bare, value in _ENTRY_RE.findall(block):
        out[quoted or bare] = value
    return out


#: JS の var 名 → Python 正本の値。
_MIRRORED = {
    "STATUS_LABELS": label_vocab.VERIFICATION_STATUS_LABELS_LEDGER,
    "LOAD_LABELS": doubt_schema.LOAD_LEVEL_LABELS,
    "COVERAGE_LABELS": doubt_schema.COVERAGE_LABELS,
    "CHALLENGE_STATUS_LABELS": doubt_schema.CHALLENGE_STATUS_LABELS,
    "PROPOSAL_STATUS_LABELS": doubt_schema.PROPOSAL_STATUS_LABELS,
    "FALSIFICATION_KIND_LABELS": doubt_schema.FALSIFICATION_KIND_LABELS,
    "REACHABILITY_LABELS": doubt_schema.REACHABILITY_LABELS,
    "FALSIFICATION_ASPECT_LABELS": doubt_schema.FALSIFICATION_ASPECT_LABELS,
    "SUPPORT_LEVEL_BADGE_LABELS": doubt_schema.SUPPORT_LEVEL_BADGE_LABELS,
}

#: 「サーバに同じ表があるのにフロントが別文言を持つ」ことが意図である箇所。
#: 現在は無し — CHALLENGE_TYPE_LABELS はフロントが各値に「という疑義」を付す
#: （`test_doubt_ui_wiring_static.py` が原文で固定）ため、そもそも同名の表を
#: ミラー対象にしない（下の TestChallengeTypeSuffix で関係を固定する）。
_INTENTIONAL_DIFFERENCES: dict[str, str] = {}


class TestTableParity:
    def test_every_mirrored_table_matches_exactly(self):
        for name, python_table in _MIRRORED.items():
            assert name not in _INTENTIONAL_DIFFERENCES
            assert _js_table(name) == python_table, name + " の Python ⇄ JS が不一致"

    def test_tables_are_not_empty(self):
        for name, python_table in _MIRRORED.items():
            assert python_table, name
            for key, value in python_table.items():
                assert value.strip(), name + ":" + key

    def test_keys_match_the_server_vocabularies(self):
        assert set(doubt_schema.LOAD_LEVEL_LABELS) == set(doubt_schema.LOAD_LEVELS)
        assert set(doubt_schema.COVERAGE_LABELS) == set(doubt_schema.COVERAGE_LEVELS)
        assert set(doubt_schema.FALSIFICATION_KIND_LABELS) == set(
            doubt_schema.FALSIFICATION_KINDS
        )
        assert set(doubt_schema.REACHABILITY_LABELS) == set(doubt_schema.REACHABILITY_LEVELS)
        assert set(doubt_schema.SUPPORT_LEVEL_BADGE_LABELS) == set(
            doubt_schema.SUPPORT_LINE_LEVELS
        )
        assert set(doubt_schema.CHALLENGE_STATUS_LABELS) == {
            s.value for s in doubt_schema.ChallengeStatus
        }
        assert set(doubt_schema.PROPOSAL_STATUS_LABELS) == {
            s.value for s in doubt_schema.ProposalStatus
        }
        # 反実仮想の aspect は routes 層の語彙（_OBSERVATION_ASPECTS）と一致させる。
        routes_src = (
            Path(__file__).resolve().parents[1] / "api" / "routes" / "doubt.py"
        ).read_text(encoding="utf-8")
        assert '_OBSERVATION_ASPECTS = ("value", "systematics")' in routes_src
        assert set(doubt_schema.FALSIFICATION_ASPECT_LABELS) == {"value", "systematics"}

    def test_coverage_levels_cover_the_derivation_function(self):
        produced = {
            doubt_schema.scope_coverage_level(n) for n in (0, 1, 2, 3, 4, 40)
        }
        assert produced == set(doubt_schema.COVERAGE_LABELS)


class TestChallengeTypeSuffix:
    """疑義の種別はフロントが「という疑義」を付した文にしている（意図）。"""

    def test_front_labels_are_the_server_labels_plus_the_suffix(self):
        js = _js_table("CHALLENGE_TYPE_LABELS")
        from core.deliberation import positioning

        server = positioning._CHALLENGE_TYPE_LABELS
        assert set(js) == set(server)
        # サーバは種別名（「検証スコープ外への外挿」）、フロントは文
        #（「…という疑義」）。語幹の共有だけを固定し、文末は UI 側の自由にする。
        for key, text in js.items():
            assert text.endswith("という疑義"), key


class TestNoDoubtTypeTableOnTheFront:
    """疑いの様相（doubt_type）の表を doubt-atlas.js に持たない（参照ゼロだった死表）。

    正本は `core/structure_anchor/schema.py::DOUBT_TYPE_LABELS` で、学習者画面は
    API の `doubt_type_label` をそのまま描く。教員画面用に再定義すると
    「サーバが返す語」と「画面が持つ語」の二重管理が復活する。
    """

    def test_doubt_atlas_has_no_doubt_type_labels_table(self):
        assert "DOUBT_TYPE_LABELS" not in _js()

    def test_server_still_owns_the_doubt_type_vocabulary(self):
        from core.structure_anchor.schema import DOUBT_TYPE_LABELS

        assert DOUBT_TYPE_LABELS["justification_gap"]


class TestFrontEndMirrorPointsAtTheServer:
    def test_js_names_the_server_source_of_truth(self):
        src = _js()
        assert "core/doubt/schema.py" in src
        assert "core/label_vocab.py" in src
        assert "test_doubt_vocab_mirror.py" in src


class TestLandscapeStatusFallbackMirror:
    """fail-soft フォールバック表（サーバの status_label が取れないとき）のミラー。

    `admin.js::LANDSCAPE_STATUS_FALLBACK_LABELS` と
    `admin-release-review.js::STATUS_FALLBACK_LABELS` はバイト一致の2表。
    どちらも「相手ファイルが未読み込みでも成立する」ことが目的なので共有参照には
    せず（fail-soft の意味が失われる）、一致だけをここで固定する。
    正本はサーバの `core/landscape/schema.py::STATUS_LABELS`。
    """

    def _table(self, filename: str, var_name: str) -> dict[str, str]:
        src = (_ROOT / "frontend" / "public" / "js" / filename).read_text(encoding="utf-8")
        marker = "var " + var_name + " = {"
        assert marker in src, filename + " に " + var_name + " が無い"
        start = src.index(marker)
        block = src[start + len(marker) : src.index("};", start)]
        return {
            (quoted or bare): value for quoted, bare, value in _ENTRY_RE.findall(block)
        }

    def test_the_two_fallback_tables_are_identical(self):
        admin = self._table("admin.js", "LANDSCAPE_STATUS_FALLBACK_LABELS")
        release = self._table("admin-release-review.js", "STATUS_FALLBACK_LABELS")
        assert admin == release

    def test_ai_related_fallback_labels_are_the_server_provenance_labels(self):
        """出所ラベル（AC-005）はフォールバックでも崩さない。

        フォールバック表は状態ラベルと出所ラベルの折衷（``rejected`` /
        ``superseded`` は画面用の短縮語）だが、**AI 推定を教員確認済みに見せない**
        という一線はサーバの `PROVENANCE_LABELS` と逐語一致させる。
        """
        from core.landscape import schema as ls

        admin = self._table("admin.js", "LANDSCAPE_STATUS_FALLBACK_LABELS")
        for key in ("confirmed", "inferred", "review_required"):
            assert admin[key] == ls.PROVENANCE_LABELS[key], key
        # 残りは画面用の短縮語（サーバの状態ラベルとは別文言でよい）。
        assert admin["rejected"] and admin["superseded"]


class TestLectureStudioGenericTitlesMirror:
    """`admin-lecture-studio.js` の genericTitles は ElementVocab の部分集合。

    委譲（`ElementVocab.kindLabel`）にはできない — この関数だけを抽出して Node で
    評価する harness（`test_learning_material_embed_resolution.py`）が `window` を
    持たないため、`window.ElementVocab` 参照は ReferenceError になる。
    そこで表は残し、正本（`core/element_vocab.py` ⇄ `element-vocab.js` の
    KIND_LABELS）との一致をここで固定する。
    """

    def test_generic_titles_agree_with_element_vocab_kind_labels(self):
        studio = (
            _ROOT / "frontend" / "public" / "js" / "admin-lecture-studio.js"
        ).read_text(encoding="utf-8")
        marker = "var genericTitles = {"
        assert marker in studio
        start = studio.index(marker)
        block = studio[start + len(marker) : studio.index("};", start)]
        titles = {
            (quoted or bare): value for quoted, bare, value in _ENTRY_RE.findall(block)
        }
        assert titles

        vocab_js = (
            _ROOT / "frontend" / "public" / "js" / "element-vocab.js"
        ).read_text(encoding="utf-8")
        kind_start = vocab_js.index("var KIND_LABELS = {")
        kind_block = vocab_js[kind_start : vocab_js.index("};", kind_start)]
        kind_labels = {
            (quoted or bare): value for quoted, bare, value in _ENTRY_RE.findall(kind_block)
        }
        for key, text in titles.items():
            assert kind_labels.get(key) == text, key
