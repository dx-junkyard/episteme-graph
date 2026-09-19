"""P4-5（D層・C層の表現語彙）のガードレール。

正本: docs/features/knowledge_transfer_design.md §8。不変条項は同 §2。

ここで構造的に守るのは:

1. **根拠の線は人間の記帳専用**（SL3 と同型）— ledger_builder（非LLM バックフィル）と
   D層の各 LLM worker のソースに ``evidence_lines`` への書き込みが無いこと
2. **学習者へは事実文 1 行だけ**（KT7）— 学習者向け投影に ``recorded_by`` / 根拠 ID /
   件数が出ないこと
3. **ラベル表のキー集合が語彙と一致**していること（正本は 3 モジュールに分かれている）
4. **行を消さない**（KT5）— 新設した記帳経路に DELETE / 削除エンドポイントが無いこと
5. **監査の新 entity_type を作らない**（既存 ledger / challenge / citation を流用）
6. **数値を持ち込まない**（confidence / score / weight のフィールドを作らない）
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from core import label_vocab
from core import schema as core_schema
from core.doubt.schema import (
    CHALLENGE_MODES,
    EVIDENCE_LINE_KINDS,
    HUMAN_ONLY_LEDGER_FIELDS,
    EvidenceLine,
)
from tests.guardrail_helpers import (
    assert_module_tree_forbids,
    assert_paths_forbid,
    assert_source_forbids,
    extract_function_source,
)

_BACKEND = Path(__file__).resolve().parents[1]
_DOUBT_DIR = _BACKEND / "core" / "doubt"
_ROUTES_DOUBT_SRC = (_BACKEND / "api" / "routes" / "doubt.py").read_text(encoding="utf-8")
_THEORY_COMPONENTS_SRC = (
    _BACKEND / "api" / "routes" / "theory_components.py"
).read_text(encoding="utf-8")
_LEDGER_BUILDER = _DOUBT_DIR / "ledger_builder.py"


def _function_code(src: str, name: str) -> str:
    """関数の**コードだけ**（docstring を除いた本体）を返す。

    ガードレールは「実装が何に触れているか」を見たいので、説明文に書かれた語
    （``recorded_by`` は出さない、等）を誤検出しないよう docstring を落とす。
    """
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            body = list(node.body)
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                body = body[1:]
            return "\n".join(ast.unparse(stmt) for stmt in body)
    raise AssertionError(f"function {name} not found")


#: evidence_lines を書いてはならない自動処理（非LLM バックフィル + LLM worker 群）。
_AUTOMATED_WRITER_PATHS = (
    _LEDGER_BUILDER,
    _DOUBT_DIR / "falsification_conditions" / "worker.py",
    _DOUBT_DIR / "scope_candidates" / "worker.py",
    _DOUBT_DIR / "assumption_mining" / "worker.py",
    _DOUBT_DIR / "load_calculator.py",
    _DOUBT_DIR / "support_paths.py",
)


# ===========================================================================
# 1. 根拠の線は人間の記帳専用（SL3 と同型の分離）
# ===========================================================================


class TestEvidenceLinesAreHumanOnly:
    def test_declared_as_human_only(self):
        assert "evidence_lines" in HUMAN_ONLY_LEDGER_FIELDS

    @pytest.mark.parametrize("path", _AUTOMATED_WRITER_PATHS, ids=lambda p: p.name)
    def test_automated_modules_never_mention_evidence_lines(self, path):
        assert path.exists(), f"{path} が見つからない（構成が変わったらこの表も直す）"
        assert_paths_forbid([path], ["evidence_lines"])

    def test_no_update_sets_evidence_lines_outside_the_two_human_endpoints(self):
        """``SET ... evidence_lines`` を書いてよいのは記帳 / 訂正の 2 経路だけ。"""
        human_endpoints = "".join(
            extract_function_source(_ROUTES_DOUBT_SRC, name)
            for name in ("add_evidence_line", "patch_evidence_line")
        )
        writes = re.findall(r"SET\s+([\s\S]{0,200}?)WHERE", _ROUTES_DOUBT_SRC)
        evidence_writes = [w for w in writes if "evidence_lines" in w]
        assert evidence_writes, "記帳経路の UPDATE が見つからない"
        for write in evidence_writes:
            assert write in human_endpoints, (
                f"evidence_lines への書き込みが人間の記帳経路の外にある: {write!r}"
            )

    def test_support_paths_result_is_not_recorded(self):
        """支持線（導出物）は記帳しない（PN-2）。"""
        for name in ("add_evidence_line", "patch_evidence_line"):
            src = extract_function_source(_ROUTES_DOUBT_SRC, name)
            assert_source_forbids(
                src, ["compute_support_lines", "support_lines"], context=name,
            )

    def test_recorded_by_is_taken_from_the_authenticated_user(self):
        src = extract_function_source(_ROUTES_DOUBT_SRC, "add_evidence_line")
        assert '"recorded_by": str(current_user.get("id") or "")' in src
        assert "body.recorded_by" not in src


# ===========================================================================
# 2. 学習者へは事実文 1 行だけ（KT7）
# ===========================================================================


class TestLearnerProjection:
    def test_learner_projection_drops_attribution_and_ids(self):
        src = _function_code(_ROUTES_DOUBT_SRC, "_learner_evidence_lines_fact")
        for forbidden in ("recorded_by", "evidence_ids", "claim_ids", "equation_ids", "line_id"):
            assert forbidden not in src, f"学習者向け投影に {forbidden} が漏れている"

    def test_learner_projection_has_no_count(self):
        """件数（len / count）を事実文に出さない。"""
        src = _function_code(_ROUTES_DOUBT_SRC, "_learner_evidence_lines_fact")
        assert "len(" not in src
        assert "count" not in src.lower().replace("kinds", "")

    def test_learner_ledger_line_exposes_only_the_fact_key(self):
        src = extract_function_source(_ROUTES_DOUBT_SRC, "learner_ledger_line")
        assert '"evidence_lines_fact"' in src
        assert '"evidence_lines":' not in src

    def test_fact_line_wording_is_fixed(self):
        assert "根拠の線が記帳されています（種類: " in _ROUTES_DOUBT_SRC


# ===========================================================================
# 3. ラベル表と語彙の一致（正本は 3 モジュールに分かれている）
# ===========================================================================


class TestLabelTablesMatchVocabulary:
    def test_challenge_mode_labels(self):
        assert set(label_vocab.CHALLENGE_MODE_LABELS) == set(CHALLENGE_MODES)
        assert label_vocab.CHALLENGE_MODE_LABELS["direct"] == "主張そのものへ"
        assert label_vocab.CHALLENGE_MODE_LABELS["undercut"] == "主張と根拠のつながりへ"

    def test_evidence_line_kind_labels(self):
        assert set(label_vocab.EVIDENCE_LINE_KIND_LABELS) == set(EVIDENCE_LINE_KINDS)
        assert label_vocab.EVIDENCE_LINE_KIND_LABELS == {
            "observation": "観測",
            "derivation": "導出",
            "external_reference": "外部文献",
            "consistency": "整合性",
        }

    def test_citation_intent_labels(self):
        assert set(label_vocab.CITATION_INTENT_LABELS) == set(core_schema.CITATION_INTENTS)
        assert label_vocab.CITATION_INTENT_LABELS == {
            "uses_as_evidence": "根拠として使う",
            "extends": "発展させる",
            "qualifies": "条件を付ける",
            "contrasts_with": "対比する",
            "cites_for_background": "背景として引く",
        }

    def test_all_three_tables_are_exported(self):
        for name in (
            "CHALLENGE_MODE_LABELS", "EVIDENCE_LINE_KIND_LABELS", "CITATION_INTENT_LABELS",
        ):
            assert name in label_vocab.__all__

    def test_routes_do_not_redefine_the_japanese_labels(self):
        """ラベルの正本は core/label_vocab.py（routes 層に写さない）。"""
        for src, context in (
            (_ROUTES_DOUBT_SRC, "api/routes/doubt.py"),
            (_THEORY_COMPONENTS_SRC, "api/routes/theory_components.py"),
        ):
            assert_source_forbids(
                src,
                ['"主張そのものへ"', '"外部文献"', '"根拠として使う"'],
                context=context,
            )


# ===========================================================================
# 4. 行を消さない（KT5）
# ===========================================================================


class TestNoDeletion:
    def test_no_delete_from_in_the_doubt_core_tree(self):
        """D層全体の既存不変条項（削除しない）を新語彙でも維持する。"""
        assert_module_tree_forbids(_DOUBT_DIR, ["DELETE FROM"])
        assert "DELETE FROM" not in _ROUTES_DOUBT_SRC

    def test_no_delete_endpoint_for_evidence_lines(self):
        tree = ast.parse(_ROUTES_DOUBT_SRC)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                source = ast.unparse(decorator)
                if ".delete(" in source:
                    assert "evidence-lines" not in source, (
                        f"根拠の線に DELETE ルートがある: {node.name}"
                    )

    def test_patch_keeps_the_other_lines(self):
        src = extract_function_source(_ROUTES_DOUBT_SRC, "patch_evidence_line")
        # 配列全体を書き戻す（当該要素だけを取り除く実装になっていない）
        assert "evidence_lines = CAST(:lines AS jsonb)" in src
        assert ".remove(" not in src
        assert ".pop(" not in src


# ===========================================================================
# 5. 監査は既存 entity_type を流用する
# ===========================================================================


class TestAuditVocabularyNotExtended:
    def test_no_new_entity_type_for_the_new_vocabulary(self):
        for entity_type in core_schema.AUDIT_ENTITY_TYPES:
            assert "evidence_line" not in entity_type
            assert "challenge_mode" not in entity_type
            assert "citation_intent" not in entity_type

    def test_existing_entity_types_are_reused(self):
        assert core_schema.AUDIT_ENTITY_LEDGER in core_schema.AUDIT_ENTITY_TYPES
        assert core_schema.AUDIT_ENTITY_CHALLENGE in core_schema.AUDIT_ENTITY_TYPES
        assert core_schema.AUDIT_ENTITY_CITATION in core_schema.AUDIT_ENTITY_TYPES

    def test_evidence_line_endpoints_record_against_the_ledger(self):
        for name in ("add_evidence_line", "patch_evidence_line"):
            src = extract_function_source(_ROUTES_DOUBT_SRC, name)
            assert "AUDIT_ENTITY_LEDGER" in src


# ===========================================================================
# 6. 数値を持ち込まない（KT7）
# ===========================================================================


class TestNoNumbers:
    def test_evidence_line_model_has_no_score_fields(self):
        for forbidden in ("confidence", "score", "weight", "strength"):
            assert forbidden not in EvidenceLine.model_fields

    def test_target_element_ref_allows_only_three_string_keys(self):
        assert (
            '_TARGET_ELEMENT_REF_KEYS = ("element_type", "element_id", "document_id")'
            in _ROUTES_DOUBT_SRC
        )
        # 正規化はこの表のキーだけを通す（他は捨てる）
        src = _function_code(_ROUTES_DOUBT_SRC, "_normalize_target_element_ref")
        assert "_TARGET_ELEMENT_REF_KEYS" in src

    def test_evidence_line_dto_has_no_numeric_keys(self):
        src = _function_code(_ROUTES_DOUBT_SRC, "_evidence_line_out")
        for forbidden in ("confidence", "score", "weight"):
            assert forbidden not in src
