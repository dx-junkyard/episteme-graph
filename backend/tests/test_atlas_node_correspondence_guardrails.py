"""分野マップのノード版間対応 — ガードレール（設計書 §9・不変条項 NC1〜NC8）。

構造として守らせるもの:

1. **NC1 / LS7 / AB4**: 本層のコードから ``atlas_skeletons`` へ書く SQL が無い
   （骨格を書くのは ``core/atlas_store.py`` の凍結だけ）。
2. **NC2**: core が FastAPI / sqlalchemy / ``core.llm`` / embedding を import しない。
   候補の ``justification`` は ``lexical_match`` / ``manual_curation`` の2語彙だけ。
3. **NC5**: ``landscape_placements`` の ``node_id`` を UPDATE する文がどこにも無い。
4. **NC3 / DC3**: 候補ゼロのとき確定文脈を記帳しない（route ソースの構造検査）。
5. **NC6**: 事実文に数値が無い。学習者 DTO に ``via`` / 旧 node_id を出さない。
6. **NC7**: ``from`` の重複・骨格外 id は ``ValueError`` → route が **文字列** detail の 422。
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import atlas_correspondence as ac  # noqa: E402
from core import decision_context  # noqa: E402
from core.schema import MAPPING_JUSTIFICATIONS  # noqa: E402
from tests.guardrail_helpers import (  # noqa: E402
    assert_source_does_not_import,
    assert_source_forbids,
    extract_function_source,
)

_DOCSTRING = re.compile(r'("""|\'\'\')[\s\S]*?\1')


def _code_only(path: Path) -> str:
    """コメント・docstring を落とした「実行されるコードだけ」の文字列。

    禁止語の検査を「説明文に書いた語」で落とさないため（本層は NC2 の禁止語
    そのものを docstring で説明している）。"""
    src = _DOCSTRING.sub("", path.read_text(encoding="utf-8"))
    return "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("#")
    )

_BACKEND = Path(__file__).resolve().parent.parent
_CORE = _BACKEND / "core" / "atlas_correspondence.py"
_STORE = _BACKEND / "core" / "atlas_store.py"
_ROUTE_ATLAS = _BACKEND / "api" / "routes" / "atlas.py"
_ROUTE_LANDSCAPE = _BACKEND / "api" / "routes" / "landscape.py"
_PROJECTION = _BACKEND / "core" / "landscape" / "projection.py"
_CORPUS_VIEW = _BACKEND / "core" / "corpus_view.py"

def _backend_sources() -> list[Path]:
    """``backend/core`` と ``backend/api`` の全 .py（P3-R13: 列挙をハードコードしない）。

    層のファイル一式を手で並べると、新しい読み手（概念レジストリの ``core/library/`` のように
    後から増える層）が検査から漏れる。走査根を固定して全件を見る。
    """
    return sorted((_BACKEND / "core").rglob("*.py")) + sorted((_BACKEND / "api").rglob("*.py"))


# ---------------------------------------------------------------------------
# 1. core の純粋性（NC2）
# ---------------------------------------------------------------------------


class TestCorePurity:
    def test_core_does_not_import_web_db_or_llm(self):
        assert_source_does_not_import(
            _code_only(_CORE),
            ["fastapi", "sqlalchemy", "core.llm", "openai", "numpy"],
            context=str(_CORE),
        )

    def test_core_has_no_sql(self):
        assert_source_forbids(
            _code_only(_CORE),
            ["SELECT ", "INSERT INTO", "UPDATE ", "DELETE FROM", "sa_text"],
            context=str(_CORE),
        )

    def test_core_does_not_embed_or_compare_vectors(self):
        """候補は決定論・非LLM・embedding 0 回（NC2）。"""
        assert_source_forbids(
            _code_only(_CORE),
            ["generate_embeddings", "cosine", "atlas_vectors", "embedding"],
            context=str(_CORE),
        )

    def test_module_docstring_declares_the_invariants(self):
        src = _CORE.read_text(encoding="utf-8")
        for marker in ("NC1", "NC2", "NC5", "NC6", "NC7", "NC8"):
            assert marker in src, marker


# ---------------------------------------------------------------------------
# 2. 語彙（NC2）
# ---------------------------------------------------------------------------


class TestJustificationVocabulary:
    def test_only_two_justifications_are_allowed(self):
        assert ac.ALLOWED_JUSTIFICATIONS == ("lexical_match", "manual_curation")

    def test_they_are_a_subset_of_the_shared_catalog(self):
        assert set(ac.ALLOWED_JUSTIFICATIONS) <= set(MAPPING_JUSTIFICATIONS)

    def test_no_vector_or_llm_justification_appears_in_the_module(self):
        src = _code_only(_CORE)
        assert "vector_similarity" not in src
        assert "llm_candidate" not in src

    def test_every_route_carries_only_allowed_justifications(self):
        """``_VIA_JUSTIFICATION`` の値が語彙外にならない（経路を足すときの歯止め）。"""
        for justification in ac._VIA_JUSTIFICATION.values():
            assert justification in ac.ALLOWED_JUSTIFICATIONS

    def test_node_status_vocabulary_is_fixed(self):
        assert ac.NODE_STATUSES == ("current", "migrated", "unmapped")


# ---------------------------------------------------------------------------
# 3. 骨格・配置への書き込み不在（NC1 / NC5）
# ---------------------------------------------------------------------------

_SKELETON_WRITE = re.compile(
    r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+atlas_skeletons\b", re.IGNORECASE
)


class TestNoWrites:
    def test_only_atlas_store_writes_atlas_skeletons(self):
        """骨格へ書けるのは ``core/atlas_store.py`` だけ（NC1 / KN-3 / AB4）。"""
        for path in _backend_sources():
            if path == _STORE:
                continue
            src = path.read_text(encoding="utf-8")
            assert _SKELETON_WRITE.search(src) is None, path
        # 正本側には書き込みがある（この検査が空振りでないことの確認）。
        assert _SKELETON_WRITE.search(_STORE.read_text(encoding="utf-8")) is not None

    def test_load_frozen_history_only_reads(self):
        src = extract_function_source(
            _STORE.read_text(encoding="utf-8"), "load_frozen_history"
        )
        assert "SELECT content FROM atlas_skeletons" in src
        for verb in ("INSERT", "UPDATE", "DELETE"):
            assert verb not in src

    def test_no_statement_updates_a_placements_node_id(self):
        """読み替えであって付け替えではない（NC5）。backend 全体を走査する。"""
        pattern = re.compile(
            r"UPDATE\s+landscape_placements[\s\S]{0,400}?\bSET\b[\s\S]{0,400}?\bnode_id\s*=",
            re.IGNORECASE,
        )
        offenders = []
        for path in sorted((_BACKEND / "core").rglob("*.py")) + sorted(
            (_BACKEND / "api").rglob("*.py")
        ):
            if pattern.search(path.read_text(encoding="utf-8")):
                offenders.append(str(path))
        assert offenders == []


# ---------------------------------------------------------------------------
# 4. 確定文脈（NC3 / DC1 / DC3）
# ---------------------------------------------------------------------------


class TestDecisionContext:
    def _freeze_src(self) -> str:
        return extract_function_source(
            _ROUTE_ATLAS.read_text(encoding="utf-8"), "freeze_atlas_skeleton"
        )

    def test_basis_constant_exists_and_follows_the_convention(self):
        assert (
            decision_context.BASIS_ATLAS_NODE_CORRESPONDENCE
            == "atlas_skeleton.node_correspondence"
        )
        assert (
            decision_context.BASIS_ATLAS_NODE_CORRESPONDENCE
            in decision_context.BASIS_VALUES
        )

    def test_freeze_builds_and_attaches_the_context(self):
        src = self._freeze_src()
        assert "BASIS_ATLAS_NODE_CORRESPONDENCE" in src
        assert "attach_decision_context" in src
        assert '"action": "node_correspondence"' in src

    def test_the_record_is_guarded_by_having_candidates(self):
        """候補ゼロなら記帳しない（判断の機会が無いものを判断として残さない）。"""
        src = self._freeze_src()
        assert 'if correspondence.get("candidates"):' in src
        guard = src.index('if correspondence.get("candidates"):')
        assert src.index("BASIS_ATLAS_NODE_CORRESPONDENCE") > guard

    def test_presented_and_applied_come_from_different_objects(self):
        """DC2: 一致は集合比較で導出させる（呼び出し側が申告しない）。"""
        src = self._freeze_src()
        assert 'presented_ids=[' in src and "applied_ids=[" in src
        assert 'correspondence.get("candidates")' in src
        assert "applied_correspondence" in src
        assert "presented_matches_applied" not in src


# ---------------------------------------------------------------------------
# 5. 検証と 422（NC7）
# ---------------------------------------------------------------------------


class TestValidationWiring:
    def test_route_converts_value_error_to_a_string_detail_422(self):
        src = extract_function_source(
            _ROUTE_ATLAS.read_text(encoding="utf-8"), "freeze_atlas_skeleton"
        )
        assert "atlas_correspondence.validate_migrations(" in src
        assert "except ValueError as exc:" in src
        assert "status_code=422, detail=str(exc)" in src

    def test_validation_runs_before_the_skeleton_is_frozen(self):
        src = extract_function_source(
            _ROUTE_ATLAS.read_text(encoding="utf-8"), "freeze_atlas_skeleton"
        )
        assert src.index("validate_migrations(") < src.index("atlas.freeze_skeleton(")

    def test_error_messages_are_plain_facts_without_numbers(self):
        for template in (
            ac._ERR_EMPTY_PAIR,
            ac._ERR_NO_FROZEN,
            ac._ERR_FROM_NOT_IN_FROZEN,
            ac._ERR_TO_NOT_IN_DRAFT,
            ac._ERR_DUPLICATE_FROM,
        ):
            body = template.replace("{node_id}", "")
            assert not any(ch.isdigit() for ch in body), template


# ---------------------------------------------------------------------------
# 6. 数値・内部情報の非表示（NC6・§6）
# ---------------------------------------------------------------------------


class TestNoNumbersAndNoLeaks:
    def test_facts_carry_no_counts_or_rates(self):
        for fact in (
            ac.FACT_LEXICAL_ONLY,
            ac.FACT_CANDIDATES_NOT_PRESELECTED,
            ac.FACT_UNMATCHED_REMOVED,
        ):
            assert not any(ch.isdigit() for ch in fact)
            for word in ("件", "％", "%", "率", "スコア"):
                assert word not in fact

    def test_candidate_dto_has_no_numeric_keys(self):
        candidate = ac._candidate(
            {"id": "a", "label": "A", "kind": "concept"},
            {"id": "b", "label": "B", "kind": "concept"},
            ac.VIA_LABEL,
        )
        for key in candidate:
            assert not any(
                term in key for term in ("score", "count", "confidence", "similarity")
            )

    def test_learner_placement_dto_hides_via_and_the_old_node_id(self):
        """§6: 学習者には現行 node_id と事実文だけ（``via`` を出さない）。"""
        src = extract_function_source(
            _PROJECTION.read_text(encoding="utf-8"), "_learner_placement_dto"
        )
        assert '"via"' not in src
        assert "current_node_id" in src

    def test_learner_dto_uses_the_resolved_id_not_the_row_one(self):
        tree = ast.parse(_PROJECTION.read_text(encoding="utf-8"))
        fn = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_learner_placement_dto"
        )
        # ``resolved`` （読み替え後）を node_id に入れている。
        assigned = [
            n.value.id
            for n in ast.walk(fn)
            if isinstance(n, ast.Assign) and isinstance(n.value, ast.Name)
        ]
        assert "resolved" in ast.unparse(fn)
        assert assigned is not None  # 構文木が読めていること

    def test_no_placement_dto_carries_via(self):
        """配置 DTO は教員向けにも ``via``（対応の経路）を載せない。"""
        src = _code_only(_PROJECTION)
        assert '"via"' not in src


# ---------------------------------------------------------------------------
# 7. 読み口の一本化（NC8）
# ---------------------------------------------------------------------------


class TestSingleReadPath:
    def test_readers_go_through_atlas_store_load_frozen_history(self):
        """読み手は自前 SQL を書かず ``atlas_store.load_frozen_history`` を通す。"""
        for path in (_ROUTE_LANDSCAPE, _CORPUS_VIEW, _BACKEND / "api" / "routes" / "library.py"):
            src = path.read_text(encoding="utf-8")
            assert "load_frozen_history" in src, path
            assert "FROM atlas_skeletons" not in src, path

    def test_resolver_is_built_from_the_history_helper(self):
        for path in (_ROUTE_LANDSCAPE, _CORPUS_VIEW):
            src = path.read_text(encoding="utf-8")
            assert "atlas_correspondence.build_node_resolver(" in src, path
