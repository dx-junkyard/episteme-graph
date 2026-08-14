"""core/label_vocab.py のガードレール（提案 §2-2: 段階ラベル辞書の正本化）。

`docs/features/label_vocab_design.md` が正本。ここで構造的に守るのは:

1. core/label_vocab.py は FastAPI / DB / LLM / A層 agents を import しない（純粋）
2. 段階の境界と「情報が無いときは最も慎重な段階へ倒す」不変条項（全スケール）
3. 移行した6モジュールの公開名が**同じ文字列**を返し続ける（出力ゼロ変化）
4. 移行済みモジュールに段階ラベルのリテラル再定義が復活していない
5. 日本語だけの module-level 表が**バイト一致で2箇所に**存在しない（重複表検出）
6. 同じキー集合で**黙って**値が違う表が増えていない（意図的な分裂は allowlist）
7. frontend の .js にリテラル NUL バイトが無い（grep が無言でファイルを落とす事故の防止）
"""

from __future__ import annotations

import ast
import unicodedata
from collections import defaultdict
from pathlib import Path

import pytest

from core import label_vocab
from core.label_vocab import (
    CONFIDENCE_LOW_MED_HIGH,
    CONFIDENCE_TENTATIVE_REFERENCE_HIGH,
    GradedScale,
    WEIGHT_LEVEL_SCALE,
    WEIGHT_RELATION,
)
from tests.guardrail_helpers import assert_source_does_not_import, assert_source_forbids

_BACKEND = Path(__file__).resolve().parents[1]
_ROOT = _BACKEND.parent
_JS_DIR = _ROOT / "frontend" / "public" / "js"
_LABEL_VOCAB_SRC = (_BACKEND / "core" / "label_vocab.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. 非依存（core/privacy.py・core/element_vocab.py と同じ立場）
# ---------------------------------------------------------------------------


class TestNoExternalDependency:
    def test_label_vocab_has_no_framework_dependency(self):
        assert_source_does_not_import(
            _LABEL_VOCAB_SRC,
            ["fastapi", "sqlalchemy", "core.postgres", "openai", "episteme_graph"],
            context="core/label_vocab.py",
        )

    def test_label_vocab_is_pure(self):
        """DB セッションを一切作らない（純データ + 純関数のみ）。"""
        assert "get_session" not in _LABEL_VOCAB_SRC

    def test_label_vocab_is_transitively_pure(self):
        """推移的にも重量依存を掴まない（宣言済みの唯一の内部依存は core.status.schema）。

        自ファイルのソース検査だけだと、依存先（core/status/schema.py）が将来
        sqlalchemy 等を import した時点で純粋性が無言で失われる。クリーンな
        インタプリタで import し、掴んだモジュール集合を実測で検査する。
        """
        import subprocess
        import sys

        code = (
            "import sys; import core.label_vocab; "
            "bad = sorted(m for m in sys.modules "
            "if m.split('.')[0] in ('fastapi', 'sqlalchemy', 'openai') "
            "or m == 'core.postgres'); "
            "print(','.join(bad)); sys.exit(1 if bad else 0)"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).resolve().parents[1]),
        )
        assert proc.returncode == 0, (
            f"core.label_vocab が推移的に重量依存を import している: {proc.stdout.strip()}"
        )


# ---------------------------------------------------------------------------
# 2. 境界値と「最も慎重な段階へ倒す」不変条項
# ---------------------------------------------------------------------------


_ALL_SCALES = (
    ("CONFIDENCE_LOW_MED_HIGH", CONFIDENCE_LOW_MED_HIGH),
    ("CONFIDENCE_TENTATIVE_REFERENCE_HIGH", CONFIDENCE_TENTATIVE_REFERENCE_HIGH),
    ("WEIGHT_LEVEL_SCALE", WEIGHT_LEVEL_SCALE),
    ("WEIGHT_RELATION", WEIGHT_RELATION),
)


class TestGradedScaleBoundaries:
    def test_confidence_boundaries(self):
        for scale, (high, medium, low) in (
            (CONFIDENCE_LOW_MED_HIGH, ("高", "中", "低")),
            (CONFIDENCE_TENTATIVE_REFERENCE_HIGH, ("確度高", "参考", "暫定")),
        ):
            assert scale.label_for(1.0) == high
            assert scale.label_for(0.75) == high
            assert scale.label_for(0.74) == medium
            assert scale.label_for(0.5) == medium
            assert scale.label_for(0.49) == low
            assert scale.label_for(0.0) == low

    def test_weight_boundaries(self):
        for scale, (strong, medium, weak) in (
            (WEIGHT_LEVEL_SCALE, ("strong", "medium", "weak")),
            (WEIGHT_RELATION, ("強い関連", "関連", "弱い関連")),
        ):
            assert scale.label_for(1.0) == strong
            assert scale.label_for(0.7) == strong
            assert scale.label_for(0.69) == medium
            assert scale.label_for(0.4) == medium
            assert scale.label_for(0.39) == weak
            assert scale.label_for(0.0) == weak

    @pytest.mark.parametrize("value", [None, "abc", "", -1, float("-inf"), object()])
    def test_unmeasurable_values_fall_to_the_most_cautious_label(self, value):
        """不変条項: 情報が無いことを高確度に見せない（全スケール共通）。"""
        for name, scale in _ALL_SCALES:
            assert scale.label_for(value) == scale.cautious_label, name

    def test_cautious_label_is_the_last_one(self):
        for name, scale in _ALL_SCALES:
            assert scale.cautious_label == scale.labels[-1], name

    def test_scale_shape_is_validated(self):
        with pytest.raises(ValueError):
            GradedScale((0.5,), ("高", "中", "低"))  # ラベルが1つ多すぎる
        with pytest.raises(ValueError):
            GradedScale((0.4, 0.7), ("高", "中", "低"))  # 昇順は誤り

    def test_weight_label_table_is_derived_from_the_two_scales(self):
        assert label_vocab.WEIGHT_LABELS == {
            "strong": "強い関連",
            "medium": "関連",
            "weak": "弱い関連",
        }


# ---------------------------------------------------------------------------
# 3. 委譲先が同一文字列を返す（移行の出力ゼロ変化）
# ---------------------------------------------------------------------------


class TestMigratedSitesReturnTheSameStrings:
    def test_teaching_figures_confidence_label(self):
        from core.teaching_figures import schema as tf

        assert tf.confidence_label(0.9) == "高" == tf.CONFIDENCE_LABEL_HIGH
        assert tf.confidence_label(0.6) == "中" == tf.CONFIDENCE_LABEL_MEDIUM
        assert tf.confidence_label(0.2) == "低" == tf.CONFIDENCE_LABEL_LOW
        assert tf.confidence_label(None) == "低"
        assert tf.CONFIDENCE_LABELS == ("低", "中", "高")

    def test_atlas_gaps_confidence_label(self):
        from core.atlas_gaps import schema as gaps

        assert gaps.confidence_label(0.9) == "高"
        assert gaps.confidence_label(0.5) == "中"
        assert gaps.confidence_label(None) == "低"
        assert gaps.CONFIDENCE_LABELS == ("低", "中", "高")
        assert gaps.CONFIDENCE_THRESHOLD_HIGH == 0.75
        assert gaps.CONFIDENCE_THRESHOLD_MEDIUM == 0.5

    def test_identity_links_confidence_label_keeps_its_own_vocabulary(self):
        from core.deliberation import identity_links

        assert identity_links.confidence_label(0.9) == "確度高"
        assert identity_links.confidence_label(0.5) == "参考"
        assert identity_links.confidence_label(0.1) == "暫定"
        assert identity_links.confidence_label(None) == "暫定"
        assert identity_links.CONFIDENCE_LABEL_TENTATIVE == "暫定"
        assert identity_links.CONFIDENCE_LABEL_REFERENCE == "参考"
        assert identity_links.CONFIDENCE_LABEL_HIGH == "確度高"

    def test_landscape_weight_level_and_label(self):
        from core.landscape import schema as ls

        assert ls.weight_level(0.8) == ls.WEIGHT_LEVEL_STRONG == "strong"
        assert ls.weight_level(0.5) == ls.WEIGHT_LEVEL_MEDIUM == "medium"
        assert ls.weight_level(0.1) == ls.WEIGHT_LEVEL_WEAK == "weak"
        assert ls.weight_level(None) == "weak"
        assert ls.weight_label(0.8) == "強い関連"
        assert ls.weight_label(0.5) == "関連"
        assert ls.weight_label(None) == "弱い関連"
        assert ls.WEIGHT_LABELS == {"strong": "強い関連", "medium": "関連", "weak": "弱い関連"}
        assert ls.WEIGHT_THRESHOLD_STRONG == 0.7
        assert ls.WEIGHT_THRESHOLD_MEDIUM == 0.4

    def test_support_section_labels_are_one_table_in_three_places(self):
        from core.deliberation import context_lens, positioning
        from core.discuss import opening

        expected = {
            "direct_supports": "直接支持",
            "assumptions": "前提",
            "derivation_core": "導出の核",
            "correction_sources": "訂正の源",
            "uncertainty_sources": "不確実性の源",
            "diagnostic_consequences": "診断的帰結",
            "future_requirements": "将来要件",
        }
        assert label_vocab.SUPPORT_SECTION_LABELS == expected
        for module in (positioning, context_lens, opening):
            assert module._SUPPORT_SECTION_LABELS is label_vocab.SUPPORT_SECTION_LABELS

    def test_verification_status_labels_stay_two_tables_by_audience(self):
        """宛先ごとの文言差は意図（統合すると出力文字列が変わる）。"""
        from core.deliberation import positioning

        assert label_vocab.VERIFICATION_STATUS_LABELS_LEDGER == {
            "directly_verified": "直接検証の記帳あり",
            "indirectly_supported": "間接的な支持あり",
            "untested": "未検証",
            "refuted": "反証の記帳あり",
            "unknown": "検証情報なし",
        }
        assert positioning._VERIFICATION_STATUS_LABELS == {
            "directly_verified": "直接検証済み",
            "indirectly_supported": "間接的に支持",
            "untested": "未検証",
            "refuted": "反証あり",
            "unknown": "不明",
        }
        # routes 層は import 面（名前）を維持したまま正本へ委譲している。
        src = (_BACKEND / "api" / "routes" / "doubt.py").read_text(encoding="utf-8")
        assert "from core.label_vocab import VERIFICATION_STATUS_LABELS_LEDGER" in src
        assert "_VERIFICATION_STATUS_LABELS = VERIFICATION_STATUS_LABELS_LEDGER" in src

    def test_status_projection_labels_moved_out_of_the_routes_layer(self):
        assert label_vocab.MATERIAL_STATE_LABELS == {
            "uploaded": "アップロード済み（未解析）",
            "chunking": "解析待ち",
            "analyzing": "解析実行中",
            "analyzed": "解析完了",
            "analysis_failed": "解析失敗",
            "unknown": "状態不明",
        }
        assert label_vocab.SCRIPT_STATUS_LABELS == {
            "draft": "未生成",
            "partial": "一部生成",
            "generated": "生成済み",
        }
        assert label_vocab.AUDIO_STATUS_LABELS == {
            "none": "未生成",
            "partial": "一部生成",
            "generated": "生成済み",
        }
        src = (_BACKEND / "api" / "routes" / "admin_assistant.py").read_text(encoding="utf-8")
        assert "_MATERIAL_STATE_LABELS = MATERIAL_STATE_LABELS" in src
        assert "_SCRIPT_STATUS_LABELS = SCRIPT_STATUS_LABELS" in src
        assert "_AUDIO_STATUS_LABELS = AUDIO_STATUS_LABELS" in src

    def test_normalization_is_not_pulled_into_the_canon(self):
        """範囲外の扱い（破棄 vs クランプ）は層ごとに違う。正本へ寄せない。"""
        from core.atlas_gaps import schema as gaps
        from core.landscape import schema as ls
        from core.teaching_figures import schema as tf

        assert tf.normalize_confidence(1.5) is None       # 範囲外は破棄（未測定）
        assert gaps.normalize_confidence(1.5) == 1.0      # 範囲外はクランプ
        assert gaps.normalize_confidence("abc") is None
        assert ls.normalize_weight(1.5) == 1.0
        assert "def normalize_" not in _LABEL_VOCAB_SRC


# ---------------------------------------------------------------------------
# 4. リテラル再定義の禁止（委譲を静かに剥がさない）
# ---------------------------------------------------------------------------


_MIGRATED_SOURCES = {
    "core/teaching_figures/schema.py": ('CONFIDENCE_LABEL_LOW = "低"', ">= 0.75", ">= 0.5"),
    "core/atlas_gaps/schema.py": ('CONFIDENCE_LABEL_LOW = "低"', ">= 0.75", ">= 0.5"),
    "core/deliberation/identity_links.py": ('CONFIDENCE_LABEL_TENTATIVE = "暫定"', ">= 0.75"),
    "core/landscape/schema.py": ('WEIGHT_LEVEL_STRONG: "強い関連"', ">= 0.7", ">= 0.4"),
    "core/deliberation/positioning.py": ('"direct_supports": "直接支持"',),
    "core/deliberation/context_lens.py": ('"direct_supports": "直接支持"',),
    "core/discuss/opening.py": ('"direct_supports": "直接支持"',),
    "api/routes/doubt.py": ('"directly_verified": "直接検証の記帳あり"',),
    "api/routes/admin_assistant.py": ('"解析実行中"', '"一部生成"'),
}


class TestNoLiteralRedefinition:
    @pytest.mark.parametrize("relative,forbidden", sorted(_MIGRATED_SOURCES.items()))
    def test_migrated_module_does_not_redefine_the_scale(self, relative, forbidden):
        src = (_BACKEND / relative).read_text(encoding="utf-8")
        assert_source_forbids(src, list(forbidden), context=relative)


# ---------------------------------------------------------------------------
# 5 / 6. 表の重複・黙った分裂の検出（AST 走査）
# ---------------------------------------------------------------------------
#
# 対象は backend/core + backend/api の module-level 代入で、キーも値も文字列
# （キーは同一ファイル内の module-level 文字列定数も解決する）、かつ**値が全て
# 日本語を含む**表（= 表示ラベル表）。訳語表以外（SQL 断片・英語語彙）は拾わない。

_ALLOWED_VALUE_SPLITS = {
    # 状態ラベルと、その状態に添える台帳注記。用途が違う（同じ画面に併記される）。
    ("core/atlas_path.py::LEDGER_NOTES", "core/atlas_path.py::STATUS_LABELS"),
    # theory stage の学習者向け表示名（discuss 開幕）と統制語彙の訳語（W層 / admin）。
    # 「方程式系」/「式の体系」の差は既存の静的テストが原文で固定しており、
    # 訳語統一はオーナー判断事項として別途繰り延べ（提案 §2-2 の裁定）。
    (
        "core/discuss/opening.py::_STAGE_LABELS",
        "core/element_vocab.py::THEORY_STAGE_LABELS",
    ),
    # 宛先別の文言差を**並べて可視化**している正本の2表（統合すると出力が変わる）。
    (
        "core/label_vocab.py::VERIFICATION_STATUS_LABELS_LEDGER",
        "core/label_vocab.py::VERIFICATION_STATUS_LABELS_LENS",
    ),
    # ペルソナ別のプロンプト（読み上げ用 / 応答用）。ラベルではなく本文。
    ("core/personas.py::_NARRATION_PROMPTS", "core/personas.py::_RESPONSE_PROMPTS"),
    # 選択式 DIFF の3テンプレート（焦点 / 選択文 / 一致文）。役割が違う3表。
    (
        "core/reconstruction/diff.py::_CHOICE_FOCUS_LABELS",
        "core/reconstruction/diff.py::_CHOICE_MATCH_STATEMENTS",
        "core/reconstruction/diff.py::_CHOICE_SELECTION_TEMPLATES",
    ),
    # 図タイプの表示名と、その図が埋めるギャップの説明文。
    (
        "core/teaching_figures/schema.py::FIGURE_KIND_GAP_HINTS",
        "core/teaching_figures/schema.py::FIGURE_KIND_LABELS",
    ),
}

#: 重複（バイト一致）の許容は空でなければならない — 一致した表は委譲に置き換える。
_ALLOWED_IDENTICAL_TABLES: set[tuple[str, ...]] = set()


def _has_japanese(value: str) -> bool:
    return any(
        unicodedata.name(ch, "").startswith(("CJK UNIFIED", "HIRAGANA", "KATAKANA"))
        for ch in value
    )


def _module_level_label_tables() -> list[tuple[str, str, dict[str, str]]]:
    found: list[tuple[str, str, dict[str, str]]] = []
    for root in ("core", "api"):
        for path in sorted((_BACKEND / root).rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:  # pragma: no cover - 走査対象は常に有効な Python
                continue
            constants: dict[str, str] = {}
            for node in tree.body:
                if (
                    isinstance(node, ast.Assign)
                    and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)
                ):
                    constants[node.targets[0].id] = node.value.value
            for node in tree.body:
                if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(
                    node.targets[0], ast.Name
                ):
                    name, value = node.targets[0].id, node.value
                elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    name, value = node.target.id, node.value
                else:
                    continue
                # `NAME = {...}` に加え、不変化された `NAME = MappingProxyType({...})` も
                # 語彙表として解釈する（label_vocab の canon 表を走査から漏らさない）。
                if (
                    isinstance(value, ast.Call)
                    and isinstance(value.func, ast.Name)
                    and value.func.id == "MappingProxyType"
                    and len(value.args) == 1
                    and isinstance(value.args[0], ast.Dict)
                ):
                    value = value.args[0]
                if not isinstance(value, ast.Dict):
                    continue
                items: dict[str, str] = {}
                usable = True
                for key_node, value_node in zip(value.keys, value.values):
                    if isinstance(key_node, ast.Constant) and isinstance(key_node.value, str):
                        key = key_node.value
                    elif isinstance(key_node, ast.Name) and key_node.id in constants:
                        key = constants[key_node.id]
                    else:
                        usable = False
                        break
                    if not (
                        isinstance(value_node, ast.Constant)
                        and isinstance(value_node.value, str)
                    ):
                        usable = False
                        break
                    items[key] = value_node.value
                if not usable or len(items) < 2:
                    continue
                if not all(_has_japanese(text) for text in items.values()):
                    continue
                found.append((str(path.relative_to(_BACKEND)), name, items))
    return found


class TestNoDuplicatedLabelTables:
    def test_the_scan_finds_tables_at_all(self):
        """走査自体が壊れて「違反ゼロ」になっていないことを先に確かめる。"""
        tables = _module_level_label_tables()
        assert len(tables) >= 20
        assert any(name == "SUPPORT_SECTION_LABELS" for _, name, _ in tables)

    def test_no_two_tables_are_byte_identical(self):
        groups: dict[tuple[tuple[str, str], ...], list[str]] = defaultdict(list)
        for relative, name, items in _module_level_label_tables():
            groups[tuple(sorted(items.items()))].append(relative + "::" + name)
        duplicates = {
            tuple(sorted(sites)) for sites in groups.values() if len(sites) > 1
        }
        assert duplicates <= _ALLOWED_IDENTICAL_TABLES, (
            "同一の日本語ラベル表が複数箇所にある（core/label_vocab.py へ委譲すること）: "
            + str(sorted(duplicates))
        )

    def test_same_keys_with_different_values_are_declared_intentional(self):
        by_keys: dict[tuple[str, ...], dict[str, tuple[tuple[str, str], ...]]] = defaultdict(dict)
        for relative, name, items in _module_level_label_tables():
            by_keys[tuple(sorted(items))][relative + "::" + name] = tuple(sorted(items.items()))
        splits = set()
        for sites in by_keys.values():
            if len(sites) > 1 and len(set(sites.values())) > 1:
                splits.add(tuple(sorted(sites)))
        assert splits <= _ALLOWED_VALUE_SPLITS, (
            "同じキーで値が違う表が黙って増えている（意図なら理由コメント付きで "
            "_ALLOWED_VALUE_SPLITS に登録すること）: " + str(sorted(splits - _ALLOWED_VALUE_SPLITS))
        )

    def test_allowlist_entries_still_exist(self):
        """解消済みの allowlist を残さない（allowlist が墓場にならないように）。"""
        names = {
            relative + "::" + name for relative, name, _ in _module_level_label_tables()
        }
        for group in _ALLOWED_VALUE_SPLITS:
            assert set(group) <= names, "存在しない表が allowlist に残っている: " + str(group)


# ---------------------------------------------------------------------------
# 7. フロントの JS にリテラル NUL バイトが無い
# ---------------------------------------------------------------------------


class TestNoNulBytesInFrontendJs:
    def test_no_js_file_contains_a_raw_nul_byte(self):
        """NUL が1つあるだけで grep が当該ファイルをバイナリ扱いし、
        `--include=*.js` の一斉調査から**無言で**外れる（実際に起きた事故）。
        文字列として NUL が必要なら JS のエスケープ ``"\\x00"`` を書く。
        """
        offenders = [
            path.name for path in sorted(_JS_DIR.glob("*.js")) if b"\x00" in path.read_bytes()
        ]
        assert offenders == [], "リテラル NUL バイトを含む JS: " + str(offenders)
