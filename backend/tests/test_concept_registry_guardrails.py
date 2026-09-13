"""概念レジストリの不変条項ガードレール（concept_registry_design.md §2 KR1〜KR10）。

構造的に守るのは:

- KR5 core の純粋性（``core/library/`` が FastAPI / LLM / HTTP を import しない）
- KR7 行を消さない（``DELETE FROM`` が無い・削除ルートが無い）
- KR6 数値を見せない（関係 / node リンクの DTO に confidence を載せない）
- KR2 確定は人間（candidate 始まり・凍結は confirmed のみ・骨格へ書かない）
- KR4 正当化必須（identity link / ラベル / 関係 / node リンク）
- KR10 権限 fail-closed（新ルートが全て ``_require_teacher`` を通る）

DB にも LLM にも接続しない静的検査。
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import pytest

from core.deliberation import identity_links
from core.deliberation.schema import (
    DIALOGUE_SESSION_ELEMENT_TYPES,
    ELEMENT_SYMBOL,
    IDENTITY_LINKABLE_ELEMENT_TYPES,
)
from core.library import registry, schema as library_schema, store as library_store
from core.schema import AUDIT_ENTITY_LIBRARY_ENTRY, AUDIT_ENTITY_TYPES
from tests.guardrail_helpers import (
    assert_module_tree_does_not_import,
    assert_source_does_not_import,
    assert_source_forbids,
    extract_function_source,
)

BACKEND = Path(__file__).resolve().parents[1]
LIBRARY_DIR = BACKEND / "core" / "library"
REGISTRY_SRC = (LIBRARY_DIR / "registry.py").read_text(encoding="utf-8")
STORE_SRC = (LIBRARY_DIR / "store.py").read_text(encoding="utf-8")
ROUTE_SRC = (BACKEND / "api" / "routes" / "library.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. core の純粋性（KR5 / 開発ルール2）
# ---------------------------------------------------------------------------


class TestCorePurity:
    def test_core_library_does_not_import_fastapi_or_api_layer(self):
        assert_module_tree_does_not_import(
            LIBRARY_DIR, ["fastapi", "api.", "services", "starlette"]
        )

    def test_registry_does_not_import_the_llm_layer(self):
        """KR5: 本層は決定論・非LLM。発見層の LLM allowlist を増やさない。

        検査は import 文だけを見る（docstring で「LLM を呼ばない」と書いた文章まで
        違反にしないため）。``generate_embeddings`` の直呼びも同時に禁じる。
        """
        assert_source_does_not_import(
            REGISTRY_SRC, ["core.llm", "openai"], context="core/library/registry.py"
        )
        assert "generate_embeddings(" not in REGISTRY_SRC

    def test_registry_module_exists_with_the_documented_surface(self):
        for name in (
            "list_labels",
            "add_label",
            "dismiss_label",
            "list_relations",
            "create_relation",
            "decide_relation",
            "list_node_links",
            "create_node_link",
            "decide_node_link",
            "annotate_node_links",
            "decide_entry_review",
        ):
            assert callable(getattr(registry, name)), f"registry.{name} が無い"


# ---------------------------------------------------------------------------
# 2. 行を消さない（KR7 / P4）
# ---------------------------------------------------------------------------


class TestNoRowDeletion:
    _DELETE = re.compile(r"\bDELETE\s+FROM\s+\w", re.IGNORECASE)

    def test_core_library_has_no_delete_statements(self):
        """SQL 文としての ``DELETE FROM`` が無いこと（散文の言及は対象外）。"""
        offending = [
            str(path)
            for path in sorted(LIBRARY_DIR.rglob("*.py"))
            if self._DELETE.search(path.read_text(encoding="utf-8"))
        ]
        assert offending == [], f"core/library に行削除の SQL がある: {offending}"

    def test_route_module_registers_no_delete_endpoints(self):
        assert "@router.delete" not in ROUTE_SRC

    def test_dismiss_paths_are_status_transitions(self):
        for fn in ("dismiss_label", "decide_relation", "decide_node_link", "decide_entry_review"):
            body = extract_function_source(REGISTRY_SRC, fn)
            assert "DELETE" not in body.upper(), fn
            assert "status" in body, fn


# ---------------------------------------------------------------------------
# 3. 数値を見せない（KR6）
# ---------------------------------------------------------------------------


class TestNoNumbersLeak:
    @pytest.mark.parametrize("fn", ["_relation_row_to_dict", "_node_link_row_to_dict"])
    def test_row_projections_do_not_carry_confidence(self, fn):
        body = extract_function_source(REGISTRY_SRC, fn)
        assert '"confidence"' not in body, (
            f"{fn} が confidence を DTO に載せています（KR6: 数値は DB 界面で止める）"
        )

    def test_relation_and_node_link_dtos_have_no_numeric_keys(self):
        """射影に数値らしいキー（confidence / weight / score / count）が無いこと。"""
        forbidden = ('"confidence"', '"weight"', '"score"', '"count"', '"cosine"')
        for fn in ("_relation_row_to_dict", "_node_link_row_to_dict", "_label_row_to_dict"):
            body = extract_function_source(REGISTRY_SRC, fn)
            for key in forbidden:
                assert key not in body, f"{fn}: {key}"

    def test_confidence_is_still_persisted_for_the_db(self):
        """KR6 は「見せない」であって「捨てる」ではない（列には残す）。"""
        assert "confidence" in extract_function_source(REGISTRY_SRC, "create_relation")


# ---------------------------------------------------------------------------
# 4. 確定は人間（KR2）
# ---------------------------------------------------------------------------


class TestHumanConfirmationOnly:
    def test_relations_and_node_links_start_as_candidates(self):
        create_relation = extract_function_source(REGISTRY_SRC, "create_relation")
        assert "relations must be created as candidates" in create_relation
        create_link = extract_function_source(REGISTRY_SRC, "create_node_link")
        assert "CANDIDATE_STATUS_CANDIDATE" in create_link

    def test_transitions_go_through_candidate_flow(self):
        """遷移の可否・帰属・却下理由の判定を再実装しない（共通プリミティブに委ねる）。"""
        assert "from core.candidate_flow import CandidateFlow" in REGISTRY_SRC
        for fn in ("decide_relation", "decide_node_link", "decide_entry_review"):
            assert "CandidateFlow(" in extract_function_source(REGISTRY_SRC, fn), fn

    def test_actor_is_required_for_every_decision(self):
        for fn in ("decide_relation", "decide_node_link", "decide_entry_review", "add_label", "dismiss_label"):
            assert "_require_actor(" in extract_function_source(REGISTRY_SRC, fn), fn

    def test_dismiss_requires_a_reason(self):
        assert "require_dismiss_reason=True" in REGISTRY_SRC
        assert "review_note is required when dismissing a label" in REGISTRY_SRC

    def test_unconfirmed_entries_cannot_be_frozen(self):
        body = extract_function_source(STORE_SRC, "freeze_entry")
        assert "REVIEW_STATUS_CONFIRMED" in body
        assert "LibraryConflictError" in body

    def test_review_status_is_not_editable_through_draft_edits(self):
        """ガバナンス列は本文編集のホワイトリストに入れない（§4.2）。"""
        assert "review_status" not in library_schema.UPDATABLE_FIELDS
        assert "mapping_justification" not in library_schema.UPDATABLE_FIELDS
        assert "candidate_key" not in library_schema.UPDATABLE_FIELDS

    _SKELETON_WRITE = re.compile(
        r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+atlas_skeletons\b", re.IGNORECASE
    )

    def test_registry_never_writes_to_the_atlas_skeleton(self):
        """KR2 / LS7 / AB4: 骨格へ書き込む経路を増やさない（不在証明）。"""
        offending = [
            str(path)
            for path in sorted(LIBRARY_DIR.rglob("*.py"))
            if self._SKELETON_WRITE.search(path.read_text(encoding="utf-8"))
        ]
        assert offending == [], f"概念レジストリから骨格へ書き込んでいる: {offending}"


# ---------------------------------------------------------------------------
# 5. mapping_justification 必須（KR4）
# ---------------------------------------------------------------------------


class TestMappingJustificationIsRequired:
    def test_identity_link_creation_takes_a_justification(self):
        sig = inspect.signature(identity_links.create_candidate)
        assert "mapping_justification" in sig.parameters

    def test_identity_link_rejects_missing_or_unknown_justification(self):
        body = extract_function_source(
            (BACKEND / "core" / "deliberation" / "identity_links.py").read_text(encoding="utf-8"),
            "create_candidate",
        )
        assert "mapping_justification is required" in body
        assert "MAPPING_JUSTIFICATIONS" in body

    def test_registry_validates_the_vocabulary(self):
        body = extract_function_source(REGISTRY_SRC, "_require_justification")
        assert "mapping_justification is required" in body
        assert "is_valid_justification" in body

    @pytest.mark.parametrize(
        "fn", ["add_label", "create_relation", "create_node_link"]
    )
    def test_creation_paths_require_a_justification(self, fn):
        assert "_require_justification(" in extract_function_source(REGISTRY_SRC, fn), fn

    def test_existing_rows_are_left_null_rather_than_guessed(self):
        """KR4: 導出できない既存行は NULL のまま（migration が推測で埋めない）。"""
        sql = (BACKEND / "db" / "082_concept_registry.sql").read_text(encoding="utf-8")
        for table in ("element_identity_links", "atlas_gap_decisions", "atlas_edge_decisions"):
            assert not re.search(
                rf"UPDATE\s+{table}\s*\n\s*SET mapping_justification", sql
            ), f"{table} は導出不能なのでバックフィルしてはいけない"


# ---------------------------------------------------------------------------
# 6. 権限 fail-closed（KR10）と監査（§11）
# ---------------------------------------------------------------------------


class TestRoutesAreTeacherGated:
    def _route_functions(self) -> list[ast.FunctionDef]:
        tree = ast.parse(ROUTE_SRC)
        return [
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and any(
                isinstance(d, ast.Call)
                and isinstance(d.func, ast.Attribute)
                and isinstance(d.func.value, ast.Name)
                and d.func.value.id == "router"
                for d in node.decorator_list
            )
        ]

    def test_every_library_route_requires_a_teacher(self):
        offenders = [
            fn.name
            for fn in self._route_functions()
            if "_require_teacher" not in ast.unparse(fn.args)
        ]
        assert offenders == [], offenders

    def test_registry_routes_exist(self):
        for path in (
            '"/entries/{entry_id}/review"',
            '"/entries/{entry_id}/labels"',
            '"/entries/{entry_id}/labels/{label_id}/dismiss"',
            '"/relations"',
            '"/relations/{relation_id}/decide"',
            '"/atlas-links"',
            '"/atlas-links/{link_id}/decide"',
        ):
            assert path in ROUTE_SRC, path

    def test_audit_uses_the_existing_entity_type(self):
        """§11: 新しい entity_type を作らない（既存 library_entry を流用）。"""
        assert AUDIT_ENTITY_LIBRARY_ENTRY in AUDIT_ENTITY_TYPES
        assert "AUDIT_ENTITY_LIBRARY_ENTRY" in REGISTRY_SRC
        assert "AUDIT_ENTITY_CONCEPT" not in REGISTRY_SRC

    def test_registry_audit_actions_are_declared(self):
        for action in library_schema.REGISTRY_AUDIT_ACTIONS:
            assert isinstance(action, str) and action
        assert "review_dismiss" in library_schema.REGISTRY_AUDIT_ACTIONS
        assert "node_link_confirm" in library_schema.REGISTRY_AUDIT_ACTIONS


# ---------------------------------------------------------------------------
# 7. symbol の追加（§4.7）— 同一性リンクの source にはするが W層の対象にはしない
# ---------------------------------------------------------------------------


class TestSymbolElementType:
    def test_symbol_is_identity_linkable(self):
        assert ELEMENT_SYMBOL in IDENTITY_LINKABLE_ELEMENT_TYPES

    def test_symbol_is_not_a_dialogue_session_target(self):
        """W層モーダルの対象化は非スコープ（DB の CHECK と同じ集合で fail-closed）。"""
        assert ELEMENT_SYMBOL not in DIALOGUE_SESSION_ELEMENT_TYPES

    def test_symbol_resolution_uses_the_existing_artifact_reader(self):
        refs_src = (BACKEND / "core" / "deliberation" / "refs.py").read_text(encoding="utf-8")
        body = extract_function_source(refs_src, "_resolve_symbol")
        assert "symbol_records(" in body
        assert "symbol_id" in body


# ---------------------------------------------------------------------------
# 8. ラベル表の片方向ミラー（§4.3）
# ---------------------------------------------------------------------------


class TestAliasMirror:
    def test_mirror_runs_inside_the_caller_transaction(self):
        """``session`` を引数に取り、自前で commit / close しない。"""
        body = extract_function_source(STORE_SRC, "mirror_aliases_to_labels")
        assert "session.execute" in body
        assert "session.commit()" not in body
        assert "get_session()" not in body

    def test_mirror_is_one_way(self):
        """ラベル表から aliases へは書き戻さない（片方向 = §4.3）。"""
        body = extract_function_source(STORE_SRC, "mirror_aliases_to_labels")
        assert "UPDATE library_entries" not in body

    def test_mirror_does_not_revive_dismissed_rows(self):
        body = extract_function_source(STORE_SRC, "mirror_aliases_to_labels")
        assert "WHERE library_entry_labels.status = :confirmed" in body

    def test_normalizer_is_the_shared_source_of_truth(self):
        """正規化の正本は ``core/atlas_gaps/schema.py``（再実装しない）。"""
        schema_src = (LIBRARY_DIR / "schema.py").read_text(encoding="utf-8")
        assert "from core.atlas_gaps.schema import normalize_label" in schema_src
        assert "def normalize_label" not in schema_src


# ---------------------------------------------------------------------------
# 9. 候補導出（P3-4 / P3-6）— 決定論・embedding 呼び出しゼロ（KR5）
# ---------------------------------------------------------------------------

ATLAS_LINKS_SRC = (LIBRARY_DIR / "atlas_links.py").read_text(encoding="utf-8")
IDENTITY_CANDIDATES_SRC = (LIBRARY_DIR / "identity_candidates.py").read_text(encoding="utf-8")

#: 埋め込みを**生成**する関数（保存済みベクトルを読むのは可）。
_EMBEDDING_CALLS = ("generate_embeddings(", "embed_with_context(", "build_anchor_embeddings(")


class TestCandidateDerivationPurity:
    @pytest.mark.parametrize(
        "name,src",
        [("atlas_links.py", ATLAS_LINKS_SRC), ("identity_candidates.py", IDENTITY_CANDIDATES_SRC)],
    )
    def test_does_not_import_fastapi_or_the_llm_layer(self, name, src):
        assert_source_does_not_import(
            src, ["fastapi", "core.llm", "openai", "services"], context=name
        )

    @pytest.mark.parametrize(
        "name,src",
        [("atlas_links.py", ATLAS_LINKS_SRC), ("identity_candidates.py", IDENTITY_CANDIDATES_SRC)],
    )
    def test_never_generates_embeddings(self, name, src):
        """KR5: 読むのは保存済みベクトルだけ（発見層の LLM allowlist を増やさない）。"""
        offending = [call for call in _EMBEDDING_CALLS if call in src]
        assert offending == [], f"{name}: 埋め込み生成の呼び出し {offending}"

    @pytest.mark.parametrize(
        "name,src",
        [("atlas_links.py", ATLAS_LINKS_SRC), ("identity_candidates.py", IDENTITY_CANDIDATES_SRC)],
    )
    def test_has_no_row_deletion(self, name, src):
        assert not re.search(r"\bDELETE\s+FROM\s+\w", src, re.IGNORECASE), name

    @pytest.mark.parametrize(
        "name,src",
        [("atlas_links.py", ATLAS_LINKS_SRC), ("identity_candidates.py", IDENTITY_CANDIDATES_SRC)],
    )
    def test_candidates_are_always_candidates(self, name, src):
        """KR2: 生成する行は candidate 始まりで、確定側の遷移関数を呼ばない。

        ``REVIEW_STATUS_CONFIRMED`` は**読み取りフィルタ**（確定済みエントリだけを
        照合対象にする）として現れてよいので、禁じるのは書き込み側の
        ``review_status=...CONFIRMED`` と遷移関数の呼び出しの方。
        """
        assert "REVIEW_STATUS_CANDIDATE" in src
        assert "review_status=schema.REVIEW_STATUS_CONFIRMED" not in src
        for verb in ("decide_entry_review(", "decide_node_link(", "identity_links.decide("):
            assert verb not in src, f"{name}: {verb}"

    def test_atlas_links_never_writes_the_skeleton(self):
        """KR2 / LS7 / AB4: ``atlas_skeletons`` への書き込み経路を作らない。"""
        for verb in ("INSERT INTO atlas_skeletons", "UPDATE atlas_skeletons", "save_draft("):
            assert verb not in ATLAS_LINKS_SRC, verb

    def test_identity_candidates_reads_live_views_only(self):
        """KO5: 基表 ``theory_components`` を FROM / JOIN しない。"""
        assert not re.search(
            r"\b(?:FROM|JOIN)\s+theory_components\b(?!_live)", IDENTITY_CANDIDATES_SRC
        )
        assert "theory_components_live" in IDENTITY_CANDIDATES_SRC

    def test_base_table_update_lives_in_persistence(self):
        """``duplicate_candidates`` の書き込みは persistence.py に限る（§6.2 の 4）。"""
        assert "UPDATE theory_components" not in IDENTITY_CANDIDATES_SRC
        persistence_src = (
            BACKEND / "core" / "document_pipeline" / "persistence.py"
        ).read_text(encoding="utf-8")
        body = extract_function_source(persistence_src, "set_duplicate_candidates")
        assert "UPDATE theory_components" in body
        # 数値を入れない（KR6）。
        for key in ('"confidence"', '"similarity"', '"score"'):
            assert key not in body, key

    def test_derive_returns_labels_not_scores(self):
        """KR6: 候補 DTO に載るのは段階ラベルで、cosine の生値ではない。"""
        body = extract_function_source(ATLAS_LINKS_SRC, "_candidate_dto")
        assert "ANCHOR_NEARNESS_SCALE.label_for" in body
        assert 'item.pop("confidence", None)' in body

    def test_derive_route_drops_the_coverage_counts(self):
        """導出 API は core の coverage（件数報告）を教員に返さない（KR6）。"""
        body = extract_function_source(ROUTE_SRC, "derive_atlas_links")
        assert '"candidates"' in body and '"facts"' in body
        assert '"coverage"' not in body
