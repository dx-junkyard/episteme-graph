"""知識の転用層 P4-2 — 構造 1 hop のガードレール。

正本: ``docs/features/knowledge_transfer_design.md`` §5 / §11（ガードレール）。

構造で守るのは4つ:

- **登録 kind は5つ**（``element`` / ``verification`` / ``placement`` / ``view`` /
  ``retrieved_structure``）。4-c（``topic`` / ``visible``）は依然として保留で、
  関数としても定義されていない（§11.13-3 のオーナー判断は生きている）。
- **LLM 回数不変**（KT3 / §5「決定論・LLM 0 回・embedding 0 回」）。
  ``_learning_chat_core`` の ``generate_text`` / ``generate_text_stream`` の
  呼び出し箇所が増えていない。
- **core は純粋**（KT3）。解決器は ``fastapi`` / ``sqlalchemy`` / ``core.llm`` を
  推移的にも import せず、成果テーブル名を文字列としても持たない。
- **スコープ強制 SQL の字面**（KT6）。射影は ``chunk_id`` と ``document_id`` の
  両方を ``ANY(...)`` で SQL の中で縛り、live ビューだけを読む。
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _path in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from tests.guardrail_helpers import (  # noqa: E402
    assert_module_tree_does_not_import,
    assert_source_forbids,
    extract_function_source,
)

from core.assistant_context import (  # noqa: E402
    SCREEN_LEARNING,
    registered_kinds,
)
from core.assistant_context import schema as ac_schema  # noqa: E402

LEARNING_RESOLVER = BACKEND / "core" / "assistant_context" / "resolvers" / "learning.py"
LEARNING_ROUTE = BACKEND / "api" / "routes" / "learning.py"
_ROUTE_SRC = LEARNING_ROUTE.read_text(encoding="utf-8")
_CORE_SRC = extract_function_source(_ROUTE_SRC, "_learning_chat_core")
_RESOLVER_SRC = LEARNING_RESOLVER.read_text(encoding="utf-8")


def _route_function(name: str) -> str:
    return extract_function_source(_ROUTE_SRC, name)


# ---------------------------------------------------------------------------
# 1. 登録 kind は5つ（4-c は保留のまま）
# ---------------------------------------------------------------------------


class TestRegisteredKinds:
    def test_exactly_five_kinds_are_registered(self):
        assert registered_kinds(SCREEN_LEARNING) == (
            "element",
            "verification",
            "placement",
            "view",
            "retrieved_structure",
        )

    def test_deferred_resolvers_are_still_absent(self):
        """4-c（``topic`` / ``visible``）は本層でも実装しない。"""
        tree = ast.parse(_RESOLVER_SRC)
        names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        assert "resolve_topic" not in names
        assert "resolve_visible" not in names
        assert "topic" not in registered_kinds(SCREEN_LEARNING)
        assert "visible" not in registered_kinds(SCREEN_LEARNING)

    def test_retrieved_block_has_its_own_header_and_shares_the_learning_budget(self):
        assert ac_schema.BLOCK_HEADER_RETRIEVED != ac_schema.BLOCK_HEADER_LEARNING
        assert ac_schema.BLOCK_HEADER_RETRIEVED != ac_schema.BLOCK_HEADER
        assert ac_schema.MAX_BLOCK_CHARS_LEARNING > 0

    def test_limits_are_code_constants(self):
        """SA7: 上限は env で緩めない。"""
        assert ac_schema.MAX_LEARNING_RETRIEVED_FACTS == 8
        assert ac_schema.MAX_LEARNING_RETRIEVED_CLAIMS_PER_SOURCE == 2
        assert ac_schema.MAX_LEARNING_RETRIEVED_CLAIM_CHARS == 120


# ---------------------------------------------------------------------------
# 2. LLM 回数不変（KT3）
# ---------------------------------------------------------------------------


class TestNoAdditionalLlmCalls:
    def test_generate_text_is_called_once_in_the_core(self):
        assert _CORE_SRC.count("answer = generate_text(") == 1
        assert _CORE_SRC.count("generate_text_stream(") == 0

    def test_the_new_helpers_do_not_touch_the_llm(self):
        for name in (
            "_retrieved_structure_claims",
            "_retrieved_structure_node_index",
            "_retrieved_structure_sources",
            "_learning_retrieved_structure_block",
        ):
            assert_source_forbids(
                _route_function(name),
                ["generate_text", "generate_embeddings", "generate_conversation_turn"],
                context=name,
            )

    def test_the_block_is_built_after_the_quota(self):
        """KT3 / ST2: 429 で返るリクエストでは射影を走らせない。"""
        quota_idx = _CORE_SRC.index("\n    _consume_quota()")
        block_idx = _CORE_SRC.index("_learning_retrieved_structure_block(")
        assert quota_idx < block_idx

    def test_the_block_is_built_before_the_stream_seam(self):
        """注入は継ぎ目の前（ストリーミング設計 §3.3 の規律を継承）。"""
        block_idx = _CORE_SRC.index("_learning_retrieved_structure_block(")
        start_idx = _CORE_SRC.index('yield ("start"')
        assert block_idx < start_idx


# ---------------------------------------------------------------------------
# 3. core の純粋性（KT3）
# ---------------------------------------------------------------------------


class TestResolverPurity:
    def test_resolver_does_not_import_frameworks_db_or_llm(self):
        assert_module_tree_does_not_import(
            LEARNING_RESOLVER.parent,
            ["fastapi", "sqlalchemy", "core.llm", "core.postgres", "openai", "routes"],
            glob=LEARNING_RESOLVER.name,
        )

    def test_purity_holds_transitively(self):
        code = (
            "import sys; import core.assistant_context.resolvers.learning; "
            "bad = sorted(m for m in sys.modules "
            "if m.split('.')[0] in ('fastapi', 'sqlalchemy', 'openai') "
            "or m in ('core.postgres', 'core.llm')); "
            "print(','.join(bad)); sys.exit(1 if bad else 0)"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, cwd=str(BACKEND)
        )
        assert proc.returncode == 0, (
            f"P4-2 の解決器が推移的に重量依存を掴んでいる: "
            f"{proc.stdout.strip()}{proc.stderr.strip()}"
        )

    def test_resolver_holds_no_table_names_or_sql(self):
        assert_source_forbids(
            _RESOLVER_SRC,
            [
                "theory_claims",
                "theory_component_graphs",
                "chunks",
                "SELECT ",
                "sa_text",
                "get_session",
            ],
            context=str(LEARNING_RESOLVER),
        )

    def test_resolver_does_not_reimplement_the_stage_label_table(self):
        """訳語の正本は ``core/element_vocab.py``（新しい表を作らない）。"""
        assert "from core.element_vocab import" in _RESOLVER_SRC
        for stage_label in ("理論の土台", "観測モデル", "式の体系", "整合関係"):
            assert stage_label not in _RESOLVER_SRC


# ---------------------------------------------------------------------------
# 4. スコープ強制 SQL（KT6）
# ---------------------------------------------------------------------------


class TestScopeIsEnforcedInSql:
    def test_projection_binds_both_axes_in_the_where_clause(self):
        source = _route_function("_retrieved_structure_claims")
        assert "chunk_id = ANY(CAST(:chunk_ids AS uuid[]))" in source
        assert "document_id = ANY(CAST(:doc_ids AS uuid[]))" in source

    def test_projection_reads_the_live_view_only(self):
        source = _route_function("_retrieved_structure_claims")
        assert "FROM theory_claims_live" in source
        assert "FROM theory_claims\n" not in source

    def test_projection_excludes_equation_synthesis_claims(self):
        source = _route_function("_retrieved_structure_claims")
        assert "'equation_synthesis'" in source

    def test_projection_does_not_interpolate_source_text_into_sql(self):
        """信頼境界（TB）: 資料由来の文字列を SQL に補間しない（bind param のみ）。"""
        source = _route_function("_retrieved_structure_claims")
        # f-string で埋めてよいのはコード定数の LIMIT だけ。
        assert "{_RETRIEVED_STRUCTURE_ROW_LIMIT}" in source
        for needle in ("{chunk", "{doc", "{text", "format(", "% (", ' + "'):
            assert needle not in source, needle

    def test_only_main_layer_nodes_are_indexed(self):
        """detail / debug 層のノードを事実にしない（§5「main 層ノード」）。"""
        source = _route_function("_retrieved_structure_node_index")
        assert '!= "main"' in source
        code = "\n".join(
            line for line in source.splitlines() if not line.lstrip().startswith("#")
        )
        for layer in ('"equation_detail"', '"debug"'):
            assert layer not in code, layer

    def test_the_scope_passed_is_the_turn_scope(self):
        """構造側でスコープを組み直さない（検索と同一の集合をそのまま渡す = KT6）。"""
        assert (
            "_learning_retrieved_structure_block(\n"
            "            cited_sources, allowed_document_ids\n"
            "        )"
        ) in _CORE_SRC


# ---------------------------------------------------------------------------
# 5. 数値・観測（KT7 / §11.7）
# ---------------------------------------------------------------------------


class TestNumbersAndObservation:
    def test_resolver_does_not_name_numeric_keys(self):
        for needle in ("confidence", "weight", "score", "candidate_score"):
            assert needle not in _RESOLVER_SRC, needle

    def test_projection_does_not_select_numeric_columns(self):
        source = _route_function("_retrieved_structure_claims")
        for needle in ("confidence", "weight", "score"):
            assert needle not in source, needle

    def test_observation_reuses_the_existing_single_bit_event(self):
        assert _CORE_SRC.count('"structured_grounding_present"') == 1
        assert "if _screen_block or _retrieved_block:" in _CORE_SRC
