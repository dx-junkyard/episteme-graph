"""分野マップの関係表示（RE層）のガードレール（core / migration 部分）。

正本: ``docs/features/atlas_relation_edges_design.md`` §9（§2 の不変条項 RE1〜RE8 の写像）。

本ファイルは データ層（migration 076 / ``core/atlas_edges/`` / 監査定数）が担う構造的
検査を持つ。API・フロント側の検査は後続 Wave が
``test_atlas_edges_{api,ui_static}.py`` に書く。

検査観点:
  1. ``core/atlas_edges/`` が FastAPI / API 層 / LLM SDK を import しない（開発ルール2）
  2. **embedding を呼ばない**（RE6。保存済みベクトルの読みだけ = 学習者経路でも安全）
  3. ``DELETE FROM`` が無い・公開面に delete / purge 名が無い（RE5 / P4）
  4. **``atlas_skeletons`` への INSERT / UPDATE がゼロ**（RE3 / AB4 / KN-3 の不在証明）
  5. 状態遷移が ``core/candidate_flow.py`` を通る（直書き遷移の禁止 — 本番初適用）
  6. migration 076 の CHECK 語彙 == ``core/atlas_edges/schema.py`` の語彙（完全一致）+
     冪等スタイル + 設計書への参照 + **シード行なし**
  7. 数値非漏洩（RE4。段階ラベルの日本語表を層内に複製しない・生値キーを出さない）
  8. RE7 の上限が schema の定数で表現され、糸の DTO キーが固定であること
  9. 監査 entity_type がカタログに登録されている（§8）
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from core import schema as core_schema  # noqa: E402
from core.atlas_edges import schema, store, threads  # noqa: E402
from tests.guardrail_helpers import (  # noqa: E402
    assert_module_tree_does_not_import,
    assert_module_tree_forbids,
    assert_source_does_not_import,
    assert_source_forbids,
    extract_function_source,
    read_migration_sql,
)

EDGES_DIR = BACKEND / "core" / "atlas_edges"
MODULE_NAMES = ("__init__.py", "schema.py", "derive.py", "store.py", "patching.py", "threads.py")
SOURCES = {name: (EDGES_DIR / name).read_text(encoding="utf-8") for name in MODULE_NAMES}
STORE_SRC = SOURCES["store.py"]
DERIVE_SRC = SOURCES["derive.py"]
PATCHING_SRC = SOURCES["patching.py"]
THREADS_SRC = SOURCES["threads.py"]
MIGRATION_SQL = read_migration_sql(BACKEND, 76)


def _code_only(src: str) -> str:
    """docstring とコメントを除いた**実コード**の文字列（``atlas_gaps`` 同型）。

    語彙リントを「説明文にその語を書いてはいけない」ではなく「実装がその語を
    扱っていない」の検査にするために使う（docstring には「素の遷移は書かない」と
    書けるべきである）。
    """
    tree = ast.parse(src)
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


CODE = {name: _code_only(src) for name, src in SOURCES.items()}


# ---------------------------------------------------------------------------
# 1. core の純粋性
# ---------------------------------------------------------------------------


class TestCorePurity:
    def test_package_modules_exist(self):
        for name in MODULE_NAMES:
            assert (EDGES_DIR / name).is_file(), f"missing core/atlas_edges/{name}"

    def test_does_not_import_fastapi_or_api_layer(self):
        assert_module_tree_does_not_import(
            EDGES_DIR, ["fastapi", "api.", "api ", "services", "starlette"]
        )

    def test_does_not_import_llm_sdk_or_core_llm(self):
        """RE6: 候補生成に LLM ゼロ（保存済みベクトルと配置行の読みだけ）。"""
        assert_module_tree_does_not_import(
            EDGES_DIR,
            ["core.llm", "openai", "google.generativeai", "anthropic", "vertexai"],
        )

    def test_does_not_call_the_embedding_api(self):
        """RE6 / VA3 / CR7: 学習者経路（糸レイヤー）から呼ばれても外部 API に触れない。"""
        assert_module_tree_forbids(
            EDGES_DIR, ["embed_texts", "generate_embeddings", "embeddings.create"]
        )


# ---------------------------------------------------------------------------
# 2. 情報を落とさない（RE5 / P4）
# ---------------------------------------------------------------------------


class TestNoDeletion:
    def test_no_delete_statement_anywhere(self):
        assert_module_tree_forbids(EDGES_DIR, ["DELETE FROM", "DELETE\nFROM"])

    def test_no_delete_or_purge_in_the_public_surface(self):
        for name, src in SOURCES.items():
            names = re.findall(r"^def ([a-zA-Z_][a-zA-Z0-9_]*)", src, re.MULTILINE)
            offending = [
                n for n in names if "delete" in n or "purge" in n or "remove" in n
            ]
            assert offending == [], f"core/atlas_edges/{name}: {offending}"

    def test_dismissal_is_a_status_not_a_row_removal(self):
        assert schema.DECISION_STATUS_DISMISSED in schema.DECISION_STATUSES
        assert "dismissed_edge_keys" in STORE_SRC


# ---------------------------------------------------------------------------
# 3. 骨格を書かない（RE3 / AB4 / KN-3）
# ---------------------------------------------------------------------------


class TestSkeletonIsNeverWritten:
    def test_no_write_to_atlas_skeletons(self):
        pattern = re.compile(
            r"(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+atlas_skeletons", re.IGNORECASE
        )
        for name, src in SOURCES.items():
            assert pattern.search(src) is None, (
                f"core/atlas_edges/{name} が atlas_skeletons を書き換えている "
                "（骨格の変更は draft→freeze の既存フローのみ = RE3）"
            )

    def test_patching_only_returns_a_patch(self):
        """patching は DB に触れない（patch を返すだけ）。"""
        assert "sqlalchemy" not in PATCHING_SRC
        assert "session" not in PATCHING_SRC

    def test_patch_ops_are_add_only(self):
        ops = set(re.findall(r'"op":\s*"([a-z]+)"', PATCHING_SRC))
        assert ops == {"add"}, f"op は add のみ（additive-only）だが {ops} が現れた"
        assert_source_forbids(
            PATCHING_SRC,
            ['"op": "remove"', '"op": "replace"'],
            context="core/atlas_edges/patching.py",
        )


# ---------------------------------------------------------------------------
# 4. 遷移は CandidateFlow 経由（本番初適用）
# ---------------------------------------------------------------------------


class TestTransitionsGoThroughCandidateFlow:
    def test_store_uses_the_shared_flow(self):
        assert "from core.candidate_flow import" in STORE_SRC
        assert "CandidateFlow(" in STORE_SRC
        assert isinstance(store.VOCABULARY.statuses, tuple)
        assert set(store.VOCABULARY.statuses) == set(schema.DECISION_STATUSES)

    def test_store_does_not_reimplement_the_transition_rules(self):
        """許可遷移・却下理由の必須検査を層内に書き写さない（共通フローの責務）。"""
        assert_source_forbids(
            STORE_SRC,
            ["def resolve_transition", "def _resolve_transition", "ALLOWED_TRANSITIONS"],
            context="core/atlas_edges/store.py",
        )

    def test_no_literal_status_transition_in_sql(self):
        """``SET status = 'accepted'`` のような直書き遷移を作らない（バインド経由のみ）。"""
        literal = re.compile(
            r"status\s*=\s*'(candidate|accepted|dismissed)'", re.IGNORECASE
        )
        for name, src in CODE.items():
            assert literal.search(src) is None, (
                f"core/atlas_edges/{name} に直書きの status 遷移がある "
                "（遷移は CandidateFlow + バインド変数の apply_status に一本化する）"
            )

    def test_status_is_written_in_exactly_one_place(self):
        """``SET status`` を含む SQL は ``_make_apply_status`` の中だけに存在する。"""
        applier = extract_function_source(STORE_SRC, "_make_apply_status")
        assert "SET status = :new_status" in applier
        assert STORE_SRC.count("SET status") == 1

    def test_audit_callable_is_injected(self):
        """core は ``services`` を import しないので、監査は呼び出し側が注入する。"""
        assert "record_audit: Callable" in STORE_SRC
        assert_source_does_not_import(
            STORE_SRC, ["services"], context="core/atlas_edges/store.py"
        )

    def test_audit_action_vocabulary_is_the_designed_one(self):
        assert schema.AUDIT_ACTIONS == (
            "accept",
            "dismiss",
            "restore",
            "mark_incorporated",
        )
        # 候補は読み時導出なので detect の記帳は存在しない（§8）
        assert "detect" not in schema.AUDIT_ACTIONS


class TestAuditCatalog:
    def test_entity_type_is_registered(self):
        assert core_schema.AUDIT_ENTITY_ATLAS_EDGE == "atlas_edge"
        assert core_schema.AUDIT_ENTITY_ATLAS_EDGE in core_schema.AUDIT_ENTITY_TYPES

    def test_catalog_has_no_duplicates(self):
        types = list(core_schema.AUDIT_ENTITY_TYPES)
        assert len(types) == len(set(types))


# ---------------------------------------------------------------------------
# 5. migration 076
# ---------------------------------------------------------------------------


class TestMigration:
    def test_is_idempotent(self):
        assert "CREATE TABLE IF NOT EXISTS atlas_edge_decisions" in MIGRATION_SQL
        for match in re.finditer(r"CREATE\s+(UNIQUE\s+)?INDEX", MIGRATION_SQL):
            tail = MIGRATION_SQL[match.end(): match.end() + 30]
            assert "IF NOT EXISTS" in tail, "CREATE INDEX に IF NOT EXISTS がない"

    def test_references_the_design_document(self):
        assert "atlas_relation_edges_design.md" in MIGRATION_SQL

    def test_status_check_matches_the_schema_vocabulary(self):
        match = re.search(
            r"CHECK\s*\(status IN\s*\(([^)]*)\)", MIGRATION_SQL, re.IGNORECASE
        )
        assert match, "status の CHECK 制約が見つからない"
        sql_statuses = {v.strip().strip("'") for v in match.group(1).split(",")}
        assert sql_statuses == set(schema.DECISION_STATUSES)

    def test_edge_key_is_unique_and_undirected_by_construction(self):
        assert "edge_key TEXT NOT NULL UNIQUE" in MIGRATION_SQL
        assert schema.build_edge_key("d", "b", "a") == schema.build_edge_key(
            "d", "a", "b"
        )

    def test_no_seed_rows(self):
        """毎起動・全再実行方式では初期行が教員の判断を上書きする（070 と同じ判断）。"""
        assert re.search(
            r"INSERT\s+INTO\s+atlas_edge_decisions", MIGRATION_SQL, re.IGNORECASE
        ) is None

    def test_no_deletion_in_the_migration(self):
        assert "DELETE FROM" not in MIGRATION_SQL.upper()


# ---------------------------------------------------------------------------
# 6. 数値非表示（RE4）
# ---------------------------------------------------------------------------


class TestNoNumbersLeak:
    def test_scale_labels_are_not_redefined_in_this_layer(self):
        """段階ラベルの正本は ``core/label_vocab.py``（表を層内に複製しない）。"""
        assert_module_tree_forbids(
            EDGES_DIR, ["かなり近い", "近い可能性", "遠い"]
        )

    def test_nearness_label_comes_from_the_canonical_scale(self):
        assert "ANCHOR_NEARNESS_SCALE" in DERIVE_SRC
        assert "ANCHOR_NEARNESS_THRESHOLD_NEAR" in DERIVE_SRC

    def test_raw_similarity_never_reaches_the_public_dto(self):
        """``_similarity`` は導出の並び順のためだけの内部キー。"""
        candidate_builder = extract_function_source(DERIVE_SRC, "derive_edge_candidates")
        assert "_similarity" not in candidate_builder
        assert "_similarity" not in THREADS_SRC

    def test_no_count_fields_in_the_layer(self):
        """共起の支持は件数ではなく論文タイトルの列挙で示す（RE4）。"""
        assert_module_tree_forbids(
            EDGES_DIR,
            ['"document_count"', '"count"', '"score"', '"similarity"', '"cosine"'],
        )


# ---------------------------------------------------------------------------
# 7. ヘアボール防止（RE7）と糸の DTO（RE2 / RE8）
# ---------------------------------------------------------------------------


class TestThreadLimits:
    def test_limits_are_declared_in_schema(self):
        assert schema.THREADS_MAX_PER_NODE == 2
        assert schema.THREADS_MAX_TOTAL == 30
        assert schema.MIN_DOCUMENTS_FOR_EDGE == 2

    def test_threads_use_the_schema_constants_not_literals(self):
        assert "schema.THREADS_MAX_PER_NODE" in THREADS_SRC
        assert "schema.THREADS_MAX_TOTAL" in THREADS_SRC

    def test_item_keys_are_fixed(self):
        assert threads._ITEM_KEYS == (
            "from",
            "to",
            "from_label",
            "to_label",
            "nearness_label",
        )

    def test_threads_carry_the_skeleton_version(self):
        """RE2 出所必須: 推定の糸は必ず骨格版を伴う。"""
        assert '"skeleton_version": version' in THREADS_SRC

    def test_threads_exclude_dismissed_edges(self):
        """RE8: 見せない判断も判断。"""
        assert "dismissed_edge_keys" in THREADS_SRC

    def test_threads_are_fail_soft(self):
        """地図の表示を糸の失敗で止めない。"""
        assert "except Exception" in THREADS_SRC
        assert '{"available": False}' in THREADS_SRC

    def test_v1_threads_are_vector_only(self):
        """共起由来の糸は非スコープ（§10）。"""
        assert "derive_vector_pairs" in THREADS_SRC
        assert "derive_co_occurrence_pairs" not in THREADS_SRC
