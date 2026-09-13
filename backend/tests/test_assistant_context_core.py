"""画面文脈アダプター — core の振る舞い（設計 §4.3）。

検証するのは3つ:

1. ``normalize_screen_context`` の上限・未知 screen・型不正（例外を出さない）。
2. ``registry`` の登録順適用・失敗した解決器の握り潰し・``render_block`` の
   空文字と行境界での打ち切り。
3. グラフレビュー解決器（graph_node / document_graph / view）が論文層 DTO
   （``docs/features/graph_paper_layer_design.md`` §3）から何を事実文にするか。
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

from core.assistant_context import (  # noqa: E402
    ScreenContext,
    normalize_screen_context,
    register,
    registered_kinds,
    render_block,
    resolve,
)
from core.assistant_context import schema as ac_schema  # noqa: E402
from core.assistant_context.resolvers import graph_review as gr  # noqa: E402


# ---------------------------------------------------------------------------
# fixture — 論文層 DTO（§3 の形）
# ---------------------------------------------------------------------------


def _full_dto() -> dict:
    """1ノードに全部品が揃った DTO（+ 位置の特定できないノード）。"""
    return {
        "document_id": "doc-1",
        "available": True,
        "facts": [],
        "paper": {
            "title": "A paper",
            "sections": [
                {
                    "section_id": "s1",
                    "title": "Introduction",
                    "page_start": 4,
                    "node_ids": ["eq_op_0001", "theory_op_0001"],
                },
                {"section_id": "s2", "title": "Method", "page_start": 6, "node_ids": []},
            ],
        },
        "nodes": {
            "eq_op_0001": {
                "node_id": "eq_op_0001",
                "graph_layer": "equation_detail",
                "label": "Linearize",
                "narrative_role": "setup",
                "component": {"summary": "線形化のコンポーネント", "teaching_takeaway": "…"},
                "explanation": {"body": "この段では線形化している", "status": "approved"},
                "thesis_roles": [
                    {
                        "thesis_ref": "support:direct_supports:0",
                        "section_label": "直接支持",
                        "text": "線形近似が成り立つ",
                    }
                ],
                "sections": [{"section_id": "s1", "title": "Introduction", "page_start": 4}],
                "equations": [
                    {
                        "equation_id": "eq_12",
                        "display_label": "式 (12)",
                        "latex": r"\\delta x = 0",
                        "plain_text": "delta x = 0",
                        "role": "input",
                        "section_id": "s1",
                        "page": 4,
                        "needs_math_review": False,
                    }
                ],
                "claims": [
                    {"agent_id": "claim_a", "claim_id": "", "text": "主張の本文", "resolution": "artifact"}
                ],
                "evidence": [
                    {
                        "evidence_id": "ev_0001",
                        "text": "we expand the equation to first order",
                        "section_id": "s1",
                        "page": 4,
                        "block_id": "b1",
                        "role": "source_quote",
                    }
                ],
                "figures": [
                    {
                        "figure_id": "fig_3_3",
                        "db_id": "fig-uuid",
                        "display_label": "Figure 3.3",
                        "caption": "Setup of the experiment",
                        "page": 5,
                        "via_claim_ids": ["claim_a"],
                    }
                ],
                "tables": [],
                "symbols": [
                    {
                        "symbol": "k",
                        "kind": "parameter",
                        "role": "eliminated",
                        "definition_quote": "k is the wavenumber",
                        "defining_equation_labels": ["式 (3)"],
                    }
                ],
                "derivations": [
                    {
                        "derivation_id": "der_1",
                        "operation": "linearize_system",
                        "chain_type": "equation_level",
                        "steps": [
                            {
                                "step_id": "step_1",
                                "operation": "expand",
                                "input_labels": ["式 (3)"],
                                "output_labels": ["式 (4)"],
                                "reason": "expand to first order",
                            }
                        ],
                    }
                ],
                "unlocated": False,
            },
            "theory_op_0001": {
                "node_id": "theory_op_0001",
                "graph_layer": "main",
                "label": "Equation system",
                "narrative_role": "",
                "component": None,
                "explanation": None,
                "thesis_roles": [],
                "sections": [],
                "equations": [],
                "claims": [],
                "evidence": [],
                "figures": [],
                "tables": [],
                "symbols": [],
                "derivations": [],
                "unlocated": True,
            },
        },
        "edges": {},
        "coverage": {
            "unbound_sections": [{"section_id": "s2", "title": "Method"}],
            "unbound_equations": [
                {"equation_id": "eq_7", "display_label": "式 (7)", "section_id": "s2"}
            ],
            "unbound_figures": [{"figure_id": "fig_2", "display_label": "Figure 2"}],
            "unbound_claims": [],
        },
        "narrative": {"graph_summary": "…"},
    }


def _ctx(**overrides) -> ScreenContext:
    payload = {
        "screen": ac_schema.SCREEN_GRAPH_REVIEW,
        "selection": {"document_id": "doc-1", "node_id": "eq_op_0001"},
        "view": {"mode": "graph", "layer": "main"},
        "visible_entities": [],
    }
    payload.update(overrides)
    ctx = normalize_screen_context(payload)
    assert ctx is not None
    return ctx


# ---------------------------------------------------------------------------
# 1. normalize_screen_context
# ---------------------------------------------------------------------------


class TestNormalize:
    def test_known_screen_is_accepted(self):
        ctx = normalize_screen_context(
            {"screen": "graph_review", "selection": {"node_id": "n1"}}
        )
        assert isinstance(ctx, ScreenContext)
        assert ctx.screen == "graph_review"
        assert ctx.selection == {"node_id": "n1"}
        assert ctx.view == {} and ctx.visible_entities == []

    def test_unknown_screen_is_ignored(self):
        assert normalize_screen_context({"screen": "lecture_studio"}) is None
        assert normalize_screen_context({"screen": ""}) is None
        assert normalize_screen_context({}) is None

    def test_non_dict_input_is_ignored_without_raising(self):
        for raw in (None, "graph_review", 3, [1, 2], object()):
            assert normalize_screen_context(raw) is None

    def test_ids_are_capped(self):
        long_id = "x" * 500
        ctx = normalize_screen_context(
            {
                "screen": "graph_review",
                "selection": {"document_id": long_id, "node_id": long_id},
            }
        )
        assert len(ctx.selection["document_id"]) == ac_schema.MAX_ID_CHARS == 160
        assert len(ctx.selection["node_id"]) == ac_schema.MAX_ID_CHARS

    def test_titles_are_capped_and_entities_limited(self):
        entities = [{"type": "node", "id": f"n{i}", "title": "あ" * 100} for i in range(50)]
        ctx = normalize_screen_context({"screen": "graph_review", "visible_entities": entities})
        assert len(ctx.visible_entities) == ac_schema.MAX_VISIBLE_ENTITIES == 20
        assert all(len(e["title"]) == ac_schema.MAX_TITLE_CHARS == 40 for e in ctx.visible_entities)

    def test_non_reference_values_are_dropped(self):
        """SA1: 描画テキスト・数値・入れ子の DTO は通さない（文字列と真偽値だけ）。"""
        ctx = normalize_screen_context(
            {
                "screen": "graph_review",
                "selection": {"node_id": "n1", "display_text": {"body": "本文"}, "score": 0.87},
                "view": {"mode": "graph", "expanded": True, "rows": [1, 2, 3]},
                "visible_entities": ["not-a-dict", {"id": "n2"}],
            }
        )
        assert ctx.selection == {"node_id": "n1"}
        assert ctx.view == {"mode": "graph", "expanded": True}
        assert ctx.visible_entities == [{"id": "n2"}]

    def test_malformed_shapes_never_raise(self):
        for raw in (
            {"screen": "graph_review", "selection": "oops"},
            {"screen": "graph_review", "view": 12},
            {"screen": "graph_review", "visible_entities": {"a": 1}},
            {"screen": ["graph_review"]},
        ):
            assert normalize_screen_context(raw) is None or isinstance(
                normalize_screen_context(raw), ScreenContext
            )

    def test_input_is_not_mutated(self):
        raw = {
            "screen": "graph_review",
            "selection": {"node_id": "x" * 300},
            "visible_entities": [{"title": "あ" * 100}],
        }
        snapshot = copy.deepcopy(raw)
        normalize_screen_context(raw)
        assert raw == snapshot


# ---------------------------------------------------------------------------
# 2. registry / render_block
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_graph_review_resolvers_are_registered_on_import(self):
        assert registered_kinds("graph_review") == ("graph_node", "document_graph", "view")

    def test_resolvers_apply_in_registration_order(self):
        screen = "test_screen_order"
        register(screen, "a", lambda ctx, sources: ["A1", "A2"])
        register(screen, "b", lambda ctx, sources: ["B1"])
        ctx = ScreenContext(screen=screen)
        assert resolve(ctx, {}) == ["A1", "A2", "B1"]

    def test_a_failing_resolver_contributes_nothing(self):
        screen = "test_screen_failure"

        def boom(ctx, sources):
            raise RuntimeError("boom")

        register(screen, "boom", boom)
        register(screen, "ok", lambda ctx, sources: ["生き残る事実"])
        assert resolve(ScreenContext(screen=screen), {}) == ["生き残る事実"]

    def test_re_registering_a_kind_replaces_it(self):
        screen = "test_screen_replace"
        register(screen, "a", lambda ctx, sources: ["old"])
        register(screen, "a", lambda ctx, sources: ["new"])
        assert resolve(ScreenContext(screen=screen), {}) == ["new"]

    def test_unknown_screen_and_none_context_resolve_to_nothing(self):
        assert resolve(ScreenContext(screen="never_registered"), {}) == []
        assert resolve(None, {}) == []

    def test_non_string_facts_are_dropped(self):
        screen = "test_screen_types"
        register(screen, "a", lambda ctx, sources: ["ok", "", "   ", 3, None])
        register(screen, "b", lambda ctx, sources: "not-a-list")
        assert resolve(ScreenContext(screen=screen), {}) == ["ok"]

    def test_sources_may_be_omitted(self):
        screen = "test_screen_sources"
        register(screen, "a", lambda ctx, sources: [f"{len(sources)}"])
        assert resolve(ScreenContext(screen=screen)) == ["0"]


class TestRenderBlock:
    def test_empty_facts_render_to_empty_string(self):
        assert render_block([]) == ""
        assert render_block(None) == ""
        assert render_block(["", "  "]) == ""

    def test_block_has_the_header_and_bullets(self):
        block = render_block(["事実1", "事実2"])
        lines = block.split("\n")
        assert lines[0] == ac_schema.BLOCK_HEADER
        assert lines[1:] == ["- 事実1", "- 事実2"]

    def test_block_is_truncated_at_a_line_boundary(self):
        facts = [f"事実{i}" + "あ" * 100 for i in range(60)]
        block = render_block(facts)
        assert len(block) <= ac_schema.MAX_BLOCK_CHARS
        lines = block.split("\n")
        assert lines[-1] == ac_schema.TRUNCATION_LINE
        # 打ち切りは行の途中では起きない（残った行は元の事実文と一致する）。
        for line in lines[1:-1]:
            assert line[2:] in facts

    def test_a_single_oversized_fact_still_fits_the_budget(self):
        block = render_block(["あ" * (ac_schema.MAX_BLOCK_CHARS * 2)])
        assert len(block) <= ac_schema.MAX_BLOCK_CHARS
        assert block.endswith(ac_schema.TRUNCATION_LINE)


# ---------------------------------------------------------------------------
# 3. graph_review 解決器
# ---------------------------------------------------------------------------


class TestGraphNodeResolver:
    def test_all_blocks_are_projected_in_order(self):
        facts = gr.resolve_graph_node(_ctx(), {"paper_layer": _full_dto()})
        assert facts[0] == "選択中のノード: 「Linearize」（式の詳細）"
        assert facts[1] == "論文の流れの中での役割: setup"
        assert facts[2] == "論文上の位置: 「Introduction」（p.4）"
        joined = "\n".join(facts)
        assert "中心命題での役割 — 直接支持: 線形近似が成り立つ" in joined
        assert "式 (12)（入力）: delta x = 0" in joined
        assert "根拠の逐語引用（Introduction, p.4）: 「we expand the equation to first order」" in joined
        assert "図・表: Figure 3.3 — Setup of the experiment" in joined
        assert "記号: k（消去） — k is the wavenumber" in joined
        assert "導出: expand（式 (3) → 式 (4)） — expand to first order" in joined
        assert "この要素の説明（承認済み）: この段では線形化している" in joined
        assert "コンポーネントの要約: 線形化のコンポーネント" in joined

    def test_latex_is_not_projected(self):
        """式は印字番号 + plain_text だけ（生 TeX はプロンプトに入れない）。"""
        dto = _full_dto()
        latex = dto["nodes"]["eq_op_0001"]["equations"][0]["latex"]
        facts = gr.resolve_graph_node(_ctx(), {"paper_layer": dto})
        assert latex and not any(latex in fact for fact in facts)

    def test_unlocated_node_gets_the_fixed_fact(self):
        ctx = _ctx(selection={"document_id": "doc-1", "node_id": "theory_op_0001"})
        facts = gr.resolve_graph_node(ctx, {"paper_layer": _full_dto()})
        assert facts == [
            "選択中のノード: 「Equation system」（主グラフ）",
            ac_schema.FACT_NODE_UNLOCATED,
        ]

    def test_unknown_node_or_missing_selection_yields_nothing(self):
        dto = _full_dto()
        assert gr.resolve_graph_node(_ctx(selection={"node_id": "nope"}), {"paper_layer": dto}) == []
        assert gr.resolve_graph_node(_ctx(selection={}), {"paper_layer": dto}) == []
        assert gr.resolve_graph_node(_ctx(), {}) == []

    def test_candidate_explanation_is_labelled_as_unapproved(self):
        dto = _full_dto()
        dto["nodes"]["eq_op_0001"]["explanation"] = {"body": "候補の説明", "status": "candidate"}
        facts = gr.resolve_graph_node(_ctx(), {"paper_layer": dto})
        assert "この要素の説明（候補（未承認））: 候補の説明" in facts

    def test_item_caps_are_enforced(self):
        dto = _full_dto()
        node = dto["nodes"]["eq_op_0001"]
        node["equations"] = [
            {"display_label": f"式 ({i})", "role": "linked", "plain_text": "x"} for i in range(10)
        ]
        node["evidence"] = [{"text": f"quote {i}", "section_id": "s1"} for i in range(10)]
        node["figures"] = [{"display_label": f"Figure {i}", "caption": "c"} for i in range(10)]
        node["tables"] = [{"display_label": "Table 1", "caption": "c"}]
        node["symbols"] = [{"symbol": f"s{i}", "role": "retained"} for i in range(10)]
        node["derivations"] = [
            {
                "derivation_id": "der_1",
                "steps": [
                    {"operation": f"op{i}", "input_labels": [], "output_labels": []}
                    for i in range(10)
                ],
            }
        ]
        node["thesis_roles"] = [
            {"section_label": "直接支持", "text": f"t{i}"} for i in range(10)
        ]
        facts = gr.resolve_graph_node(_ctx(), {"paper_layer": dto})
        assert sum(f.startswith("式 ") for f in facts) == ac_schema.MAX_EQUATION_ITEMS == 5
        assert sum(f.startswith("根拠の逐語引用") for f in facts) == ac_schema.MAX_EVIDENCE_ITEMS == 3
        assert sum(f.startswith("図・表: ") for f in facts) == ac_schema.MAX_FIGURE_ITEMS == 3
        assert sum(f.startswith("記号: ") for f in facts) == ac_schema.MAX_SYMBOL_ITEMS == 5
        assert sum(f.startswith("導出") for f in facts) == ac_schema.MAX_DERIVATION_STEPS == 3
        assert sum(f.startswith("中心命題での役割") for f in facts) == ac_schema.MAX_THESIS_ROLE_ITEMS

    def test_long_plain_text_is_clipped(self):
        dto = _full_dto()
        dto["nodes"]["eq_op_0001"]["equations"][0]["plain_text"] = "x" * 500
        facts = gr.resolve_graph_node(_ctx(), {"paper_layer": dto})
        equation_fact = next(f for f in facts if f.startswith("式 (12)"))
        assert equation_fact.count("x") == ac_schema.MAX_PLAIN_TEXT_CHARS == 120

    def test_verbatim_quotes_are_not_reworded(self):
        dto = _full_dto()
        quote = dto["nodes"]["eq_op_0001"]["evidence"][0]["text"]
        facts = gr.resolve_graph_node(_ctx(), {"paper_layer": dto})
        assert any(quote in fact for fact in facts)

    def test_inputs_are_not_mutated(self):
        dto = _full_dto()
        snapshot = copy.deepcopy(dto)
        ctx = _ctx()
        ctx_snapshot = (dict(ctx.selection), dict(ctx.view), list(ctx.visible_entities))
        gr.resolve_graph_node(ctx, {"paper_layer": dto})
        assert dto == snapshot
        assert (ctx.selection, ctx.view, ctx.visible_entities) == ctx_snapshot


class TestDocumentGraphResolver:
    def test_sections_are_listed_in_paper_order_with_node_labels(self):
        facts = gr.resolve_document_graph(_ctx(), {"paper_layer": _full_dto()})
        assert facts[0] == "章「Introduction」 → ノード: Linearize／Equation system"
        assert facts[1] == f"章「Method」 → {ac_schema.FACT_SECTION_UNBOUND}"

    def test_coverage_is_listed_as_labels_without_counts(self):
        facts = gr.resolve_document_graph(_ctx(), {"paper_layer": _full_dto()})
        joined = "\n".join(facts)
        assert "フレームに掛かっていない章: Method" in joined
        assert "フレームに掛かっていない式: 式 (7)" in joined
        assert "フレームに掛かっていない図: Figure 2" in joined
        assert "件" not in joined

    def test_coverage_is_capped_without_naming_a_number(self):
        dto = _full_dto()
        dto["coverage"]["unbound_sections"] = [
            {"section_id": f"s{i}", "title": f"Section {i}"} for i in range(12)
        ]
        facts = gr.resolve_document_graph(_ctx(), {"paper_layer": dto})
        line = next(f for f in facts if f.startswith("フレームに掛かっていない章"))
        labels = line.split(": ", 1)[1].split("／")
        assert len(labels) == ac_schema.MAX_COVERAGE_ITEMS + 1
        assert labels[-1] == ac_schema.MORE_ITEMS_MARK
        assert "12" not in line

    def test_section_lines_are_capped(self):
        dto = _full_dto()
        dto["paper"]["sections"] = [
            {"section_id": f"s{i}", "title": f"Section {i}", "node_ids": []} for i in range(60)
        ]
        facts = gr.resolve_document_graph(_ctx(), {"paper_layer": dto})
        assert sum(f.startswith("章「") for f in facts) == ac_schema.MAX_SECTION_LINES == 30

    def test_pl8_facts_are_passed_through_verbatim_first(self):
        dto = _full_dto()
        dto["facts"] = ["章構成の解析結果が無いため、論文の順では表示できません。"]
        facts = gr.resolve_document_graph(_ctx(), {"paper_layer": dto})
        assert facts[0] == "章構成の解析結果が無いため、論文の順では表示できません。"

    def test_unavailable_dto_only_yields_its_facts(self):
        dto = {
            "available": False,
            "facts": ["理論操作グラフが構築されていないため、論文層を表示できません。"],
            "paper": {"sections": [{"section_id": "s1", "title": "Introduction", "node_ids": []}]},
            "nodes": {},
            "coverage": {},
        }
        facts = gr.resolve_document_graph(_ctx(), {"paper_layer": dto})
        assert facts == ["理論操作グラフが構築されていないため、論文層を表示できません。"]

    def test_missing_sources_yield_nothing(self):
        assert gr.resolve_document_graph(_ctx(), {}) == []
        assert gr.resolve_document_graph(_ctx(), {"paper_layer": None}) == []


class TestViewResolver:
    def test_paper_mode(self):
        ctx = _ctx(view={"mode": "paper"})
        assert gr.resolve_view(ctx, {}) == ["教員はいま「論文の順」ビューを見ています"]

    def test_graph_mode_maps_the_layer(self):
        for layer, label in (("main", "主グラフ"), ("detail", "式の詳細"), ("all", "すべて")):
            ctx = _ctx(view={"mode": "graph", "layer": layer})
            assert gr.resolve_view(ctx, {}) == [
                f"教員はいまグラフ表示を見ています（表示層: {label}）"
            ]

    def test_graph_mode_without_a_known_layer(self):
        ctx = _ctx(view={"mode": "graph", "layer": "nope"})
        assert gr.resolve_view(ctx, {}) == ["教員はいまグラフ表示を見ています"]

    def test_unknown_mode_yields_nothing(self):
        assert gr.resolve_view(_ctx(view={}), {}) == []
        assert gr.resolve_view(_ctx(view={"mode": "kanban"}), {}) == []


class TestEndToEnd:
    def test_resolve_and_render_produce_one_block(self):
        block = render_block(resolve(_ctx(), {"paper_layer": _full_dto()}))
        assert block.startswith(ac_schema.BLOCK_HEADER)
        assert len(block) <= ac_schema.MAX_BLOCK_CHARS
        assert "選択中のノード: 「Linearize」（式の詳細）" in block
        assert "章「Introduction」 → ノード:" in block
        assert "教員はいまグラフ表示を見ています（表示層: 主グラフ）" in block

    def test_empty_sources_render_to_an_empty_block(self):
        ctx = normalize_screen_context({"screen": "graph_review", "selection": {}, "view": {}})
        assert render_block(resolve(ctx, {})) == ""
