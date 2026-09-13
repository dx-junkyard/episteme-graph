"""画面文脈アダプター — ガードレール（設計 §8 の core 側）。

不変条項 SA1〜SA7 のうち、構造で守れるものを機械検査する:

- core が fastapi / sqlalchemy / core.llm / openai / routes / services を import しない（SA3）。
- 解決器が入力を mutate しない・例外を外へ出さない（§4.3）。
- 数値（``core.graph_paper_layer.schema.FORBIDDEN_KEYS``）の値が事実文に出ない（SA4）。
- 内部 ID（``eq_op_`` / ``theory_op_`` / ``ev_`` / ``claim_`` / ノード ID）が
  事実文に出ない（SA4 / PL7）。
- 書き込み・SQL の語彙が core に無い（SA5）。
- ``render_block`` の出力が ``MAX_BLOCK_CHARS`` を超えない・空 facts は空文字（SA7）。
- 上限が env から読まれない（SA7: プロンプト予算はコード定数）。
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _path in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from tests.guardrail_helpers import (  # noqa: E402
    assert_module_tree_does_not_import,
    assert_module_tree_forbids,
    assert_source_forbids,
)
from tests.test_assistant_context_core import _ctx, _full_dto  # noqa: E402

from core.assistant_context import (  # noqa: E402
    ScreenContext,
    normalize_screen_context,
    render_block,
    resolve,
)
from core.assistant_context import schema as ac_schema  # noqa: E402
from core.assistant_context.resolvers import graph_review as gr  # noqa: E402
from core.graph_paper_layer import schema as pl_schema  # noqa: E402

CORE_DIR = BACKEND / "core" / "assistant_context"
RESOLVER_DIR = CORE_DIR / "resolvers"


def _all_facts(dto: dict) -> list[str]:
    """全解決器を通した事実文（node 選択 / グラフ全体 / 表示モード）。"""
    facts = resolve(_ctx(), {"paper_layer": dto})
    facts += resolve(
        _ctx(selection={"document_id": "doc-1", "node_id": "theory_op_0001"}, view={"mode": "paper"}),
        {"paper_layer": dto},
    )
    return facts


# ---------------------------------------------------------------------------
# SA3 — core の純粋性
# ---------------------------------------------------------------------------


class TestCoreIsPure:
    def test_core_does_not_import_frameworks_db_or_llm(self):
        assert_module_tree_does_not_import(
            CORE_DIR,
            ["fastapi", "sqlalchemy", "core.llm", "openai", "routes", "services", "pydantic"],
        )

    def test_core_contains_no_sql_or_session_handling(self):
        assert_module_tree_forbids(
            CORE_DIR,
            ["DELETE FROM", "INSERT INTO", "UPDATE ", "SELECT ", "sa_text", "get_session"],
        )

    def test_core_has_no_write_verbs_in_the_public_surface(self):
        source = "\n".join(
            path.read_text(encoding="utf-8") for path in sorted(CORE_DIR.rglob("*.py"))
        )
        assert_source_forbids(
            source,
            ["def save_", "def store_", "def persist_", "def delete_", "def approve_"],
            context="assistant_context",
        )

    def test_prompt_budget_is_a_code_constant(self):
        """SA7: 上限を env で緩めない。"""
        assert_module_tree_forbids(CORE_DIR, ["os.environ", "getenv", "Settings", "settings."])

    def test_core_is_importable_on_its_own(self):
        import importlib

        module = importlib.import_module("core.assistant_context")
        assert callable(module.resolve) and callable(module.render_block)


# ---------------------------------------------------------------------------
# SA4 — 数値を出さない
# ---------------------------------------------------------------------------


class TestNoNumbersLeak:
    def test_resolvers_do_not_read_numeric_keys(self):
        """解決器のソースに confidence / weight / score の読み取りが無い。"""
        for path in sorted(RESOLVER_DIR.rglob("*.py")):
            assert_source_forbids(
                path.read_text(encoding="utf-8"),
                ["confidence", "weight", "score"],
                context=str(path),
            )

    def test_forbidden_key_values_never_reach_the_facts(self):
        dto = _full_dto()
        node = dto["nodes"]["eq_op_0001"]
        # DTO 側に生数値が混ざっても事実文には出ない（PL4 / SA4）。
        for key in pl_schema.FORBIDDEN_KEYS:
            node[key] = 0.87
            node["equations"][0][key] = 0.87
            node["evidence"][0][key] = 0.87
            node["symbols"][0][key] = 0.87
            node["derivations"][0]["steps"][0][key] = 0.87
            dto["coverage"]["unbound_equations"][0][key] = 0.87
        facts = _all_facts(dto)
        assert facts
        for fact in facts:
            assert "0.87" not in fact
            for key in pl_schema.FORBIDDEN_KEYS:
                assert key not in fact

    def test_no_counts_are_reported(self):
        dto = _full_dto()
        dto["coverage"]["unbound_sections"] = [
            {"section_id": f"s{i}", "title": f"Section {i}"} for i in range(30)
        ]
        for fact in _all_facts(dto):
            assert "件" not in fact
            assert "個" not in fact


# ---------------------------------------------------------------------------
# SA4 / PL7 — 内部 ID を出さない
# ---------------------------------------------------------------------------


class TestNoInternalIds:
    def test_internal_id_prefixes_are_absent_from_the_facts(self):
        facts = _all_facts(_full_dto())
        assert facts
        for fact in facts:
            for prefix in pl_schema.INTERNAL_ID_PREFIXES:
                assert prefix not in fact, f"内部 ID が事実文に出ている: {fact}"

    def test_node_ids_and_element_ids_are_absent_from_the_facts(self):
        facts = _all_facts(_full_dto())
        for identifier in (
            "eq_op_0001",
            "theory_op_0001",
            "ev_0001",
            "claim_a",
            "der_1",
            "step_1",
            "fig_3_3",
            "fig-uuid",
            "s1",
            "b1",
        ):
            assert all(identifier not in fact for fact in facts), identifier

    def test_the_fixed_facts_are_plain_japanese_without_ids(self):
        for fact in (ac_schema.FACT_NODE_UNLOCATED, ac_schema.FACT_SECTION_UNBOUND):
            for prefix in pl_schema.INTERNAL_ID_PREFIXES:
                assert prefix not in fact


# ---------------------------------------------------------------------------
# §4.3 — 読み取り専用・例外を出さない
# ---------------------------------------------------------------------------


class TestResolversAreReadOnlyAndSafe:
    def test_inputs_are_not_mutated(self):
        dto = _full_dto()
        snapshot = copy.deepcopy(dto)
        ctx = _ctx()
        ctx_snapshot = copy.deepcopy((ctx.selection, ctx.view, ctx.visible_entities))
        resolve(ctx, {"paper_layer": dto})
        assert dto == snapshot
        assert (ctx.selection, ctx.view, ctx.visible_entities) == ctx_snapshot

    def test_malformed_dtos_do_not_raise(self):
        broken_payloads = (
            {"paper_layer": None},
            {"paper_layer": "oops"},
            {"paper_layer": {"nodes": "oops", "paper": 3, "coverage": []}},
            {"paper_layer": {"nodes": {"eq_op_0001": "oops"}}},
            {"paper_layer": {"nodes": {"eq_op_0001": {"equations": "oops", "sections": 5}}}},
            {},
        )
        for sources in broken_payloads:
            facts = resolve(_ctx(), sources)
            assert isinstance(facts, list)
            assert all(isinstance(fact, str) for fact in facts)

    def test_unknown_screen_context_is_ignored(self):
        assert normalize_screen_context({"screen": "unknown_screen"}) is None
        assert resolve(ScreenContext(screen="unknown_screen"), {}) == []


# ---------------------------------------------------------------------------
# SA7 — ブロック予算
# ---------------------------------------------------------------------------


class TestBlockBudget:
    def test_empty_facts_render_to_an_empty_block(self):
        assert render_block([]) == ""
        assert render_block(resolve(ScreenContext(screen="unknown_screen"), {})) == ""

    def test_block_never_exceeds_the_budget(self):
        dto = _full_dto()
        node = dto["nodes"]["eq_op_0001"]
        node["evidence"] = [
            {"text": "あ" * 200, "section_id": "s1", "page": 4} for _ in range(10)
        ]
        node["component"]["summary"] = "い" * 5000
        block = render_block(_all_facts(dto) * 20)
        assert len(block) <= ac_schema.MAX_BLOCK_CHARS
        assert block.startswith(ac_schema.BLOCK_HEADER)

    def test_header_states_that_the_block_is_not_evidence(self):
        """SA1: 「根拠ではなく範囲の手がかり」であることをブロック自身が明示する。"""
        assert "根拠ではなく" in ac_schema.BLOCK_HEADER


# ---------------------------------------------------------------------------
# 語彙宣言
# ---------------------------------------------------------------------------


class TestVocabularyIsDeclared:
    def test_known_screens_only_contains_registered_screens(self):
        # Phase 4（§11.2）で学習チャットが加わった。語彙に足した画面には必ず
        # 解決器が登録されていること（未知の screen は正規化で落ちる = fail-closed）。
        assert ac_schema.KNOWN_SCREENS == (
            ac_schema.SCREEN_GRAPH_REVIEW,
            ac_schema.SCREEN_LEARNING,
        )
        from core.assistant_context import registered_kinds

        for screen in ac_schema.KNOWN_SCREENS:
            assert registered_kinds(screen), screen

    def test_equation_and_symbol_roles_match_the_paper_layer_vocabulary(self):
        assert set(ac_schema.EQUATION_ROLE_LABELS) == set(pl_schema.EQUATION_ROLES)
        assert set(ac_schema.SYMBOL_ROLE_LABELS) == set(pl_schema.SYMBOL_ROLES)

    def test_graph_layer_labels_cover_the_dto_layers(self):
        assert set(ac_schema.GRAPH_LAYER_LABELS) == {"main", "equation_detail", "debug"}

    def test_view_layer_labels_cover_the_screen_toggle(self):
        assert set(ac_schema.VIEW_LAYER_LABELS) == {"main", "detail", "all"}

    def test_resolver_kinds_are_the_three_declared_in_the_design(self):
        assert gr.resolve_graph_node and gr.resolve_document_graph and gr.resolve_view
