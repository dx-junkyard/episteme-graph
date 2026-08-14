"""分野の地図（Field Atlas）の状態語彙の Python ⇄ JS ミラー（訳語統一 D3）。

地図の状態ピル・凡例の語彙は `core/atlas_state.py::PILL_LABELS` を正本とする。
かつて `core/atlas_path.py::STATUS_LABELS` は verified を「実験で確認 / 原文に裏付け」
と両義で持ち、フロント3ファイル（overlay の凡例 / draft-preview の凡例と SEED_LABEL /
fixture の pill）は「実験で確認」だけを表示していた。

**verified が意味するのは「原文に裏付け」だけ**である:

- SL1（閉世界の正直さ）: 台帳はコーパスの射影であって分野の射影ではない。
  コーパスに記帳があることを「実験で確認された」と言い換えると、システムが
  持っていない外部世界の事実を主張したことになる。
- D層の「合意と検証は別軸」: 実験による検証の強さは
  `epistemic_ledger.verification_status` が帰属つきで持つ（地図の色ではない）。

JS 側のパースは `test_doubt_vocab_mirror.py` と同じ正規表現方式（Node を呼ばない）。
"""

from __future__ import annotations

import re
from pathlib import Path

from core import atlas_path, atlas_state

_BACKEND = Path(__file__).resolve().parents[1]
_ROOT = _BACKEND.parent
_JS_DIR = _ROOT / "frontend" / "public" / "js"

_ENTRY_RE = re.compile(r"(?:\"([^\"]+)\"|([A-Za-z_][\w$]*))\s*:\s*\"([^\"]*)\"")

#: 統一前の語。表示にも語釈にも二度と出さない。
_STALE = "実験で確認"


def _js(filename: str) -> str:
    return (_JS_DIR / filename).read_text(encoding="utf-8")


def _js_table(filename: str, var_name: str) -> dict[str, str]:
    src = _js(filename)
    marker = "var " + var_name + " = {"
    assert marker in src, filename + " に " + var_name + " が無い"
    start = src.index(marker)
    block = src[start + len(marker) : src.index("};", start)]
    return {
        (quoted or bare): value for quoted, bare, value in _ENTRY_RE.findall(block)
    }


class TestServerCanon:
    def test_pill_labels_are_the_canon_for_the_shared_keys(self):
        """atlas_path.STATUS_LABELS は PILL_LABELS の共通キーと逐語一致する。

        STATUS_LABELS は "link"（次の領域への接続）を余分に持つ = パスカード専用の
        追加キーであり、これは意図的な差分。逆に PILL_LABELS の "unknown"（記帳なし）は
        パスカードに現れない。共通キーの**値**が分裂しないことだけを固定する。
        """
        shared = set(atlas_path.STATUS_LABELS) & set(atlas_state.PILL_LABELS)
        assert shared, "共通キーが無い（どちらかの表が壊れている）"
        for key in sorted(shared):
            assert atlas_path.STATUS_LABELS[key] == atlas_state.PILL_LABELS[key], key

    def test_path_only_keys_are_declared(self):
        assert set(atlas_path.STATUS_LABELS) - set(atlas_state.PILL_LABELS) == {"link"}

    def test_verified_means_backed_by_the_source_text(self):
        assert atlas_state.PILL_LABELS[atlas_state.STATUS_VERIFIED] == "原文に裏付け"
        assert atlas_path.STATUS_LABELS["verified"] == "原文に裏付け"

    def test_ledger_notes_cover_the_same_statuses(self):
        """状態ラベルと台帳注記は用途が違う2表だが、キー集合は揃っている。"""
        assert set(atlas_path.LEDGER_NOTES) == set(atlas_path.STATUS_LABELS)


class TestFrontendMirror:
    def test_draft_preview_seed_label_matches_the_canon(self):
        table = _js_table("atlas-draft-preview.js", "SEED_LABEL")
        assert table, "SEED_LABEL が空"
        for key, text in table.items():
            assert text == atlas_state.PILL_LABELS[key], key

    def test_overlay_legend_uses_the_canon_labels(self):
        """凡例は canon の短形をそのまま使う（合成項は canon の語を含む形）。

        assumed と gap は同じ破線スタイルで描くため1項に束ねている。項数・視覚構造は
        変えず、文言が canon の語から離れないことだけを固定する。
        """
        src = _js("atlas-overlay.js")
        start = src.index("function buildLegend()")
        block = src[start : src.index("\n  }\n", start)]
        assert '"' + atlas_state.PILL_LABELS[atlas_state.STATUS_VERIFIED] + '"' in block
        assert '"' + atlas_state.PILL_LABELS[atlas_state.STATUS_CONTESTED] + '"' in block
        # 合成項（暗黙の前提 + 行間）は canon の語幹を含むこと。
        assert atlas_state.PILL_LABELS[atlas_state.STATUS_ASSUMED] in block
        assert "行間" in block
        assert "AIが補完" in block

    def test_draft_preview_legend_uses_the_canon_labels(self):
        src = _js("atlas-draft-preview.js")
        start = src.index("function legendRow()")
        block = src[start : src.index("\n  }\n", start)]
        for status in (
            atlas_state.STATUS_VERIFIED,
            atlas_state.STATUS_CONTESTED,
            atlas_state.STATUS_ASSUMED,
        ):
            assert '"' + atlas_state.PILL_LABELS[status] + '"' in block, status

    def test_fixture_pills_come_from_the_canon(self):
        """フィクスチャ（ATLAS_DATA_SOURCE=fixture の明示時のみ使用）も同じ語彙。

        pill は本文中で装飾されることがある（例「…・習得済み」）ので、
        canon のいずれかの語で始まることを検査する。
        """
        src = _js("atlas-fixture.js")
        pills = re.findall(r'"pill":\s*"([^"]+)"', src)
        assert pills, "フィクスチャに pill が無い"
        canon = set(atlas_state.PILL_LABELS.values()) | {"応用"}
        for pill in pills:
            assert any(pill.startswith(text) for text in canon), pill

    def test_each_frontend_file_names_the_server_source_of_truth(self):
        for filename in (
            "atlas-overlay.js",
            "atlas-draft-preview.js",
            "atlas-fixture.js",
        ):
            src = _js(filename)
            assert "atlas_state.py" in src, filename
            assert "test_atlas_vocab_mirror.py" in src, filename


class TestStalePhraseIsGone:
    """「実験で確認」は backend/core と frontend/public/js のどこにも無い（SL1）。

    コーパスへの記帳を「実験で確認」と表示すると、このシステムが観測していない
    外部世界の検証を主張してしまう。検証の強さは D層台帳（verification_status）が
    帰属つきで持ち、地図はコーパスの射影であることに徹する。
    """

    def test_backend_core_has_no_stale_phrase(self):
        offenders = [
            str(path.relative_to(_BACKEND))
            for path in sorted((_BACKEND / "core").rglob("*.py"))
            if _STALE in path.read_text(encoding="utf-8")
        ]
        assert offenders == [], "backend/core に旧語が残っている: " + str(offenders)

    def test_frontend_js_has_no_stale_phrase(self):
        offenders = [
            path.name
            for path in sorted(_JS_DIR.glob("*.js"))
            if _STALE in path.read_text(encoding="utf-8")
        ]
        assert offenders == [], "frontend の JS に旧語が残っている: " + str(offenders)
