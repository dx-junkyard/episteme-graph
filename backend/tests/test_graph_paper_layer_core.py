"""グラフの論文層 — core（build_paper_layer）の単体テスト。

正本: ``docs/features/graph_paper_layer_design.md`` §3（DTO）/ §3.1（結び付け規則）/
§3.2（被覆）。DB / LLM への実接続なしで、決定論的な読み時射影だけを検証する。
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _path in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from core.graph_paper_layer import build_paper_layer  # noqa: E402
from core.graph_paper_layer import schema as pl_schema  # noqa: E402
from core.label_vocab import SUPPORT_SECTION_LABELS  # noqa: E402


# ---------------------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------------------


def _node(node_id, **overrides):
    node = {
        "component_id": node_id,
        "label": "Theory basis",
        "graph_layer": "main",
        "display_order": 0,
        "linked_equation_ids": [],
        "linked_derivation_ids": [],
        "linked_claim_ids": [],
        "linked_evidence_ids": [],
        "input_claim_ids": [],
        "output_claim_ids": [],
        "required_claim_ids": [],
        "input_equation_ids": [],
        "intermediate_equation_ids": [],
        "output_equation_ids": [],
        "definition_equation_ids": [],
        "constraint_equation_ids": [],
        "eliminated_symbols": [],
        "retained_symbols": [],
        "member_component_ids": [],
        "detail_node_ids": [],
        "linked_component_ids": [],
    }
    node.update(overrides)
    return node


def _equation(equation_id, *, label=None, section_id=None, block_id="", page=4, plain_text="x = y"):
    return {
        "equation_id": equation_id,
        "label": label,
        "source_extraction": {
            "raw_text": plain_text,
            "latex": "x = y",
            "plain_text": plain_text,
            "source_location": {"page": page, "section_id": section_id, "block_id": block_id},
            "needs_math_review": False,
        },
        "reconstruction": {"latex": "x = y", "plain_text": plain_text, "status": "complete"},
        "semantics": {"linked_claim_ids": [], "defined_symbols": []},
    }


def _graph(nodes, *, edges=None, reference_index=None, **extra):
    payload = {
        "document_id": "doc-1",
        "nodes": nodes,
        "edges": edges or [],
        "reference_index": reference_index or {"claims": {}, "evidence": {}, "derivations": {}},
    }
    payload.update(extra)
    return payload


def _full_artifacts():
    """全ステージが揃ったフィクスチャ（facts が空になる最小構成）。"""
    return {
        "document_structure": {
            "metadata": {"title": "A Paper"},
            "sections": [
                {"section_id": "s1", "title": "Introduction", "level": 1, "order": 0, "page_start": 1},
                {"section_id": "s2", "title": "Method", "level": 1, "order": 1, "page_start": 4},
            ],
            "blocks": [
                {"block_id": "b9", "page": 4, "order": 3, "text": "…", "section_id": "s2"},
            ],
        },
        "evidence_registry": {
            "records": [
                {
                    "evidence_id": "ev_0001",
                    "source": {"page": 4, "section_id": None, "block_id": "b9"},
                    "evidence_text": "we assume the linear regime",
                    "evidence_role": "source_quote",
                }
            ]
        },
        "claim_object_builder": {
            "claims": [
                {
                    "claim_id": "claim_a",
                    "text": "The linear regime holds.",
                    "source_evidence_ids": ["ev_0001"],
                    "source_span_ids": [],
                    "support_status": "source_backed",
                    "is_atomic": True,
                    "section_id": "s2",
                    "confidence": 0.9,
                    "qualification_reason": "…",
                },
                {
                    "claim_id": "claim_free",
                    "text": "An unbound but atomic claim.",
                    "source_evidence_ids": [],
                    "source_span_ids": [],
                    "support_status": "source_backed",
                    "is_atomic": True,
                    "section_id": "s1",
                },
            ]
        },
        "equation_semantics": {
            "equations": [
                _equation("eq_12", label="12", section_id="s2"),
                _equation("eq_free", label="7", section_id="s1"),
            ]
        },
        "symbol_registry": {
            "records": [
                {
                    "symbol_id": "sym_1",
                    "canonical_symbol": "k",
                    "notation_variants": ["\\mathbf{k}"],
                    "kind": "parameter",
                    "scope": "document",
                    "defining_equation_ids": ["eq_12"],
                    "used_in_equation_ids": [],
                    "definition_evidence_texts": ["k is the wavenumber"],
                    "definition_status": "defined",
                    "confidence": 0.5,
                }
            ]
        },
        "derivation_chain": {
            "chains": [
                {
                    "derivation_id": "der_1",
                    "operation": "linearize_system",
                    "chain_type": "equation_chain",
                    "source_section_ids": ["s2"],
                    "input_equation_ids": ["eq_12"],
                    "output_equation_ids": [],
                    "steps": [
                        {
                            "step_id": "der_1_s1",
                            "operation": "linearize_system",
                            "input_equation_ids": ["eq_12"],
                            "output_equation_ids": [],
                            "reason": "expand to first order",
                            "required_claim_ids": [],
                            "source_evidence_ids": ["ev_0001"],
                            "confidence": 0.7,
                        }
                    ],
                }
            ]
        },
        "figure_table_semantics": {
            "figures": [
                {
                    "figure_id": "fig_3.3",
                    "caption": "Setup of the apparatus",
                    "source_location": {"page": 5, "caption_block_id": "b9", "section_id": "s2"},
                    "linked_claim_ids": ["claim_a"],
                }
            ],
            "tables": [
                {
                    "table_id": "table_1",
                    "caption": "Parameters",
                    "source_location": {"page": 6, "caption_block_id": "b9", "section_id": "s2"},
                    "linked_claim_ids": ["claim_a"],
                }
            ],
        },
        "paper_skeleton": {
            "paper_goal": {"text": "Measure the thing."},
            "central_question": {"text": "Is it linear?"},
            "headline_claim": {"text": "It is."},
            "logical_blocks": [
                {
                    "block_id": "lb_1",
                    "block_type": "derivation",
                    "label": "Linearisation",
                    "section_ids": ["s2"],
                    "evidence_block_ids": ["b9"],
                    "summary": "The derivation.",
                }
            ],
        },
        "thesis_reconstruction": {
            "central_thesis": {"text": "The system is linear.", "claim_ids": ["claim_a"], "equation_ids": []},
            "central_question": "Is it linear in this regime?",
            "support_structure": {
                "direct_supports": [
                    {"text": "Because the residual is small.", "claim_ids": ["claim_a"], "equation_ids": []}
                ]
            },
        },
        "component_assembly": {
            "components": [
                {
                    "component_id": "comp_1",
                    "label": "Linearisation",
                    "summary": "Linearises the system.",
                    "teaching_takeaway": "Take the first order.",
                    "role_in_thesis": "Provides the theoretical basis",
                    "linked_claim_ids": ["claim_a"],
                    "evidence_refs": {"claim_ids": ["claim_a"]},
                }
            ]
        },
    }


def _reference_index():
    return {
        "claims": {
            "claim_a": {
                "claim_id": "11111111-1111-1111-1111-111111111111",
                "text": "The linear regime holds.",
                "review_status": "teacher_approved",
                "resolution": "db",
            },
            "claim_free": {
                "claim_id": "",
                "text": "An unbound but atomic claim.",
                "review_status": "",
                "resolution": "artifact",
                "is_atomic": True,
            },
        },
        "evidence": {},
        "derivations": {},
    }


def _full_case():
    detail = _node(
        "eq_op_0001",
        graph_layer="equation_detail",
        label="Linearize",
        display_order=1,
        input_equation_ids=["eq_12"],
        linked_claim_ids=["claim_a"],
        linked_evidence_ids=["ev_0001"],
        linked_derivation_ids=["der_1"],
        eliminated_symbols=["k"],
        parent_component_id="comp_1",
        linked_component_ids=["comp_1"],
    )
    main = _node(
        "theory_op_0001",
        graph_layer="main",
        label="Equation system",
        member_component_ids=["eq_op_0001"],
        linked_component_ids=["comp_1"],
    )
    graph = _graph(
        [main, detail],
        edges=[
            {
                "edge_id": "edge_1",
                "source_component_id": "theory_op_0001",
                "target_component_id": "eq_op_0001",
                "evidence_equation_ids": ["eq_12"],
            }
        ],
        reference_index=_reference_index(),
        narrative={
            "graph_summary": "The paper linearises the system.",
            "node_narratives": {"theory_op_0001": {"narrative_role": "setup", "reason": "…", "confidence": 0.4}},
            "edge_narratives": {"edge_1": {"transition_text": "then it solves.", "confidence": 0.3}},
        },
        graph_updated_at="2026-09-03T00:00:00",
    )
    figure_rows = [
        {"id": "fig-uuid-1", "figure_key": "fig_3_3", "figure_label": "Figure 3.3", "page": 5, "caption_text": "Setup"}
    ]
    explanation_rows = [
        {"element_id": "comp_1", "body": "候補の説明", "status": "candidate"},
        {"element_id": "comp_1", "body": "承認済みの説明", "status": "approved"},
    ]
    return graph, _full_artifacts(), figure_rows, explanation_rows


def _build_full():
    graph, artifacts, figure_rows, explanation_rows = _full_case()
    return build_paper_layer(
        graph, artifacts, figure_rows=figure_rows, explanation_rows=explanation_rows
    )


# ---------------------------------------------------------------------------
# 可用性 / fail-soft（PL8）
# ---------------------------------------------------------------------------


class TestAvailabilityAndFacts:
    def test_no_nodes_yields_unavailable(self):
        out = build_paper_layer({"nodes": [], "edges": []}, _full_artifacts())
        assert out["available"] is False
        assert pl_schema.FACT_NO_GRAPH in out["facts"]
        assert out["nodes"] == {}
        assert out["paper"]["sections"] == []

    def test_empty_graph_dict_is_unavailable(self):
        out = build_paper_layer({}, {})
        assert out["available"] is False
        assert out["facts"][0] == pl_schema.FACT_NO_GRAPH

    def test_every_missing_artifact_adds_exactly_one_fact(self):
        out = build_paper_layer(_graph([_node("theory_op_0001")]), {})
        for _stage, _key, fact in pl_schema.MISSING_ARTIFACT_FACTS:
            assert out["facts"].count(fact) == 1
        assert len(out["facts"]) == len(pl_schema.MISSING_ARTIFACT_FACTS)

    @pytest.mark.parametrize("stage,key,fact", list(pl_schema.MISSING_ARTIFACT_FACTS))
    def test_one_missing_stage_adds_only_its_own_fact(self, stage, key, fact):
        artifacts = _full_artifacts()
        artifacts.pop(stage)
        graph, _artifacts, figure_rows, explanation_rows = _full_case()
        out = build_paper_layer(
            graph, artifacts, figure_rows=figure_rows, explanation_rows=explanation_rows
        )
        assert out["facts"] == [fact]

    def test_full_artifacts_produce_no_facts(self):
        assert _build_full()["facts"] == []

    def test_missing_artifacts_do_not_raise(self):
        out = build_paper_layer(
            _graph([
                _node(
                    "eq_op_0001",
                    graph_layer="equation_detail",
                    linked_equation_ids=["eq_99"],
                    linked_claim_ids=["claim_x"],
                    linked_evidence_ids=["ev_9"],
                    linked_derivation_ids=["der_9"],
                    eliminated_symbols=["z"],
                )
            ]),
            {},
        )
        node = out["nodes"]["eq_op_0001"]
        assert node["unlocated"] is True
        assert node["equations"][0]["display_label"] == "番号なし"
        assert node["derivations"] == []
        assert node["symbols"][0]["symbol"] == "z"


# ---------------------------------------------------------------------------
# 章の解決（PL3）
# ---------------------------------------------------------------------------


class TestSectionResolution:
    def test_direct_section_id_wins(self):
        artifacts = _full_artifacts()
        out = build_paper_layer(
            _graph([_node("eq_op_0001", graph_layer="equation_detail", input_equation_ids=["eq_12"])]),
            artifacts,
        )
        node = out["nodes"]["eq_op_0001"]
        assert node["equations"][0]["section_id"] == "s2"
        assert node["sections"] == [{"section_id": "s2", "title": "Method", "page_start": 4}]
        assert node["unlocated"] is False

    def test_block_id_resolves_when_section_id_is_absent(self):
        artifacts = _full_artifacts()
        artifacts["equation_semantics"]["equations"][0]["source_extraction"]["source_location"] = {
            "page": 4, "section_id": None, "block_id": "b9",
        }
        out = build_paper_layer(
            _graph([_node("eq_op_0001", graph_layer="equation_detail", input_equation_ids=["eq_12"])]),
            artifacts,
        )
        assert out["nodes"]["eq_op_0001"]["equations"][0]["section_id"] == "s2"

    def test_unknown_block_id_stays_unlocated(self):
        artifacts = _full_artifacts()
        artifacts["equation_semantics"]["equations"][0]["source_extraction"]["source_location"] = {
            "page": 4, "section_id": None, "block_id": "b-unknown",
        }
        out = build_paper_layer(
            _graph([_node("eq_op_0001", graph_layer="equation_detail", input_equation_ids=["eq_12"])]),
            artifacts,
        )
        node = out["nodes"]["eq_op_0001"]
        assert node["equations"][0]["section_id"] == ""
        assert node["sections"] == []
        assert node["unlocated"] is True

    def test_evidence_block_id_resolves_the_section(self):
        out = build_paper_layer(
            _graph([_node("eq_op_0001", graph_layer="equation_detail", linked_evidence_ids=["ev_0001"])]),
            _full_artifacts(),
        )
        node = out["nodes"]["eq_op_0001"]
        assert node["evidence"][0]["section_id"] == "s2"
        assert node["evidence"][0]["block_id"] == "b9"

    def test_claim_falls_back_to_its_evidence_section(self):
        artifacts = _full_artifacts()
        artifacts["claim_object_builder"]["claims"][0]["section_id"] = None
        out = build_paper_layer(
            _graph(
                [_node("eq_op_0001", graph_layer="equation_detail", linked_claim_ids=["claim_a"])],
                reference_index=_reference_index(),
            ),
            artifacts,
        )
        assert out["nodes"]["eq_op_0001"]["claims"][0]["section_id"] == "s2"

    def test_no_section_guessing_from_titles(self):
        """章のタイトル・近接だけでは結び付けない（PL3）。"""
        artifacts = _full_artifacts()
        artifacts["claim_object_builder"]["claims"][0]["section_id"] = None
        artifacts["claim_object_builder"]["claims"][0]["source_evidence_ids"] = []
        artifacts["claim_object_builder"]["claims"][0]["section_title"] = "Method"
        out = build_paper_layer(
            _graph(
                [_node("eq_op_0001", graph_layer="equation_detail", linked_claim_ids=["claim_a"])],
                reference_index=_reference_index(),
            ),
            artifacts,
        )
        assert out["nodes"]["eq_op_0001"]["claims"][0]["section_id"] == ""


# ---------------------------------------------------------------------------
# main の member 合算
# ---------------------------------------------------------------------------


class TestMainAggregation:
    def test_main_node_inherits_member_refs(self):
        out = _build_full()
        main = out["nodes"]["theory_op_0001"]
        assert [e["equation_id"] for e in main["equations"]] == ["eq_12"]
        assert [c["agent_id"] for c in main["claims"]] == ["claim_a"]
        assert [e["evidence_id"] for e in main["evidence"]] == ["ev_0001"]
        assert [d["derivation_id"] for d in main["derivations"]] == ["der_1"]
        assert [s["symbol"] for s in main["symbols"]] == ["k"]
        assert main["sections"][0]["section_id"] == "s2"

    def test_member_ids_from_detail_node_ids_key_also_count(self):
        detail = _node(
            "eq_op_0001", graph_layer="equation_detail", input_equation_ids=["eq_12"], display_order=1
        )
        main = _node("theory_op_0001", detail_node_ids=["eq_op_0001"])
        out = build_paper_layer(_graph([main, detail]), _full_artifacts())
        assert [e["equation_id"] for e in out["nodes"]["theory_op_0001"]["equations"]] == ["eq_12"]

    def test_member_refs_are_deduplicated(self):
        detail = _node(
            "eq_op_0001", graph_layer="equation_detail", input_equation_ids=["eq_12"], display_order=1
        )
        main = _node("theory_op_0001", input_equation_ids=["eq_12"], member_component_ids=["eq_op_0001"])
        out = build_paper_layer(_graph([main, detail]), _full_artifacts())
        assert len(out["nodes"]["theory_op_0001"]["equations"]) == 1

    def test_unknown_member_id_is_ignored(self):
        main = _node("theory_op_0001", member_component_ids=["eq_op_missing"])
        out = build_paper_layer(_graph([main]), _full_artifacts())
        assert out["nodes"]["theory_op_0001"]["equations"] == []

    def test_equation_role_priority_is_first_match(self):
        detail = _node(
            "eq_op_0001",
            graph_layer="equation_detail",
            output_equation_ids=["eq_12"],
            linked_equation_ids=["eq_12"],
        )
        out = build_paper_layer(_graph([detail]), _full_artifacts())
        assert out["nodes"]["eq_op_0001"]["equations"][0]["role"] == "output"


# ---------------------------------------------------------------------------
# 図の二段キー照合
# ---------------------------------------------------------------------------


class TestFigureJoin:
    def test_figure_id_and_figure_key_join_across_notations(self):
        out = _build_full()
        figures = out["nodes"]["eq_op_0001"]["figures"]
        assert len(figures) == 1
        assert figures[0]["figure_id"] == "fig_3.3"
        assert figures[0]["db_id"] == "fig-uuid-1"
        assert figures[0]["display_label"] == "Figure 3.3"
        assert figures[0]["via_claim_ids"] == ["claim_a"]

    def test_missing_document_figures_row_leaves_db_id_null(self):
        graph, artifacts, _rows, explanations = _full_case()
        out = build_paper_layer(graph, artifacts, figure_rows=[], explanation_rows=explanations)
        figure = out["nodes"]["eq_op_0001"]["figures"][0]
        assert figure["db_id"] is None
        # figure_label が無くても ID から印字番号を導ける（内部 ID は出さない）。
        assert figure["display_label"] == "Figure 3.3"

    def test_figure_without_shared_claim_is_not_attached(self):
        artifacts = _full_artifacts()
        artifacts["figure_table_semantics"]["figures"][0]["linked_claim_ids"] = ["claim_other"]
        graph, _a, rows, explanations = _full_case()
        out = build_paper_layer(graph, artifacts, figure_rows=rows, explanation_rows=explanations)
        assert out["nodes"]["eq_op_0001"]["figures"] == []

    def test_tables_join_by_claim_too(self):
        out = _build_full()
        tables = out["nodes"]["eq_op_0001"]["tables"]
        assert tables[0]["table_id"] == "table_1"
        assert tables[0]["display_label"] == "Table 1"


# ---------------------------------------------------------------------------
# thesis 上の役割
# ---------------------------------------------------------------------------


class TestThesisRoles:
    def test_thesis_ref_convention_and_section_labels(self):
        out = _build_full()
        roles = out["nodes"]["eq_op_0001"]["thesis_roles"]
        refs = [r["thesis_ref"] for r in roles]
        assert refs == ["central_thesis", "support:direct_supports:0"]
        assert roles[0]["section_label"] == ""
        assert roles[1]["section_label"] == SUPPORT_SECTION_LABELS["direct_supports"]

    def test_support_section_label_table_is_shared_not_redefined(self):
        from core.graph_paper_layer import builder

        assert builder._SUPPORT_SECTION_LABELS is SUPPORT_SECTION_LABELS

    def test_unknown_support_section_falls_back_to_its_key(self):
        artifacts = _full_artifacts()
        artifacts["thesis_reconstruction"]["support_structure"] = {
            "brand_new_section": [{"text": "…", "claim_ids": ["claim_a"]}]
        }
        graph, _a, rows, explanations = _full_case()
        out = build_paper_layer(graph, artifacts, figure_rows=rows, explanation_rows=explanations)
        roles = out["nodes"]["eq_op_0001"]["thesis_roles"]
        assert roles[1]["thesis_ref"] == "support:brand_new_section:0"
        assert roles[1]["section_label"] == "brand_new_section"

    def test_central_thesis_carries_db_uuids_and_node_ids(self):
        out = _build_full()
        central = out["paper"]["central_thesis"]
        assert central["claim_ids"] == ["11111111-1111-1111-1111-111111111111"]
        assert central["node_ids"] == ["eq_op_0001", "theory_op_0001"]


# ---------------------------------------------------------------------------
# 被覆（§3.2）
# ---------------------------------------------------------------------------


class TestCoverage:
    def test_unbound_equations_and_sections(self):
        out = _build_full()
        coverage = out["coverage"]
        assert [e["equation_id"] for e in coverage["unbound_equations"]] == ["eq_free"]
        assert coverage["unbound_equations"][0]["display_label"] == "式 (7)"
        assert [s["section_id"] for s in coverage["unbound_sections"]] == ["s1"]

    def test_unbound_claims_require_atomic_and_source_backed(self):
        out = _build_full()
        assert [c["agent_id"] for c in out["coverage"]["unbound_claims"]] == ["claim_free"]

    def test_non_atomic_unbound_claim_is_excluded(self):
        artifacts = _full_artifacts()
        artifacts["claim_object_builder"]["claims"][1]["is_atomic"] = False
        graph, _a, rows, explanations = _full_case()
        out = build_paper_layer(graph, artifacts, figure_rows=rows, explanation_rows=explanations)
        assert out["coverage"]["unbound_claims"] == []

    def test_unsupported_unbound_claim_is_excluded(self):
        artifacts = _full_artifacts()
        artifacts["claim_object_builder"]["claims"][1]["support_status"] = "review_required"
        graph, _a, rows, explanations = _full_case()
        out = build_paper_layer(graph, artifacts, figure_rows=rows, explanation_rows=explanations)
        assert out["coverage"]["unbound_claims"] == []

    def test_bound_elements_are_not_reported_as_unbound(self):
        out = _build_full()
        assert all(e["equation_id"] != "eq_12" for e in out["coverage"]["unbound_equations"])
        assert out["coverage"]["unbound_figures"] == []


# ---------------------------------------------------------------------------
# 論文の背骨（論文→フレーム）
# ---------------------------------------------------------------------------


class TestPaperSpine:
    def test_sections_are_ordered_and_carry_node_ids(self):
        out = _build_full()
        sections = out["paper"]["sections"]
        assert [s["section_id"] for s in sections] == ["s1", "s2"]
        # 並びはグラフの display_order（main が先、その member の detail が続く）。
        assert sections[1]["node_ids"] == ["theory_op_0001", "eq_op_0001"]
        assert [e["equation_id"] for e in sections[1]["equations"]] == ["eq_12"]
        assert [f["figure_id"] for f in sections[1]["figures"]] == ["fig_3.3"]
        assert [t["table_id"] for t in sections[1]["tables"]] == ["table_1"]
        assert [c["agent_id"] for c in sections[1]["claims"]] == ["claim_a"]

    def test_goal_and_central_question_prefer_their_own_artifacts(self):
        out = _build_full()
        assert out["paper"]["title"] == "A Paper"
        assert out["paper"]["goal"] == "Measure the thing."
        # thesis の central_question が skeleton より優先される（§3）。
        assert out["paper"]["central_question"] == "Is it linear in this regime?"

    def test_central_question_falls_back_to_the_skeleton(self):
        artifacts = _full_artifacts()
        artifacts["thesis_reconstruction"]["central_question"] = ""
        graph, _a, rows, explanations = _full_case()
        out = build_paper_layer(graph, artifacts, figure_rows=rows, explanation_rows=explanations)
        assert out["paper"]["central_question"] == "Is it linear?"

    def test_backbone_blocks_carry_the_nodes_of_their_sections(self):
        out = _build_full()
        block = out["paper"]["backbone"][0]
        assert block["block_type"] == "derivation"
        assert block["node_ids"] == ["theory_op_0001", "eq_op_0001"]

    def test_edges_expose_transition_text_and_equation_labels(self):
        out = _build_full()
        assert out["edges"]["edge_1"] == {
            "transition_text": "then it solves.",
            "equation_labels": ["式 (12)"],
        }

    def test_edge_without_narrative_or_equations_is_omitted(self):
        graph, artifacts, rows, explanations = _full_case()
        graph["edges"][0].pop("evidence_equation_ids")
        graph["narrative"]["edge_narratives"] = {}
        out = build_paper_layer(graph, artifacts, figure_rows=rows, explanation_rows=explanations)
        assert out["edges"] == {}

    def test_narrative_role_and_graph_summary_are_projected(self):
        out = _build_full()
        assert out["nodes"]["theory_op_0001"]["narrative_role"] == "setup"
        assert out["narrative"]["graph_summary"] == "The paper linearises the system."
        assert out["graph_updated_at"] == "2026-09-03T00:00:00"


# ---------------------------------------------------------------------------
# component 要約 / contextual 説明
# ---------------------------------------------------------------------------


class TestComponentAndExplanation:
    def test_component_summary_is_joined_by_agent_id(self):
        out = _build_full()
        component = out["nodes"]["eq_op_0001"]["component"]
        assert component["summary"] == "Linearises the system."
        assert component["teaching_takeaway"] == "Take the first order."
        assert component["role_in_thesis"] == "Provides the theoretical basis"

    def test_approved_explanation_wins_over_candidate(self):
        out = _build_full()
        explanation = out["nodes"]["eq_op_0001"]["explanation"]
        assert explanation == {"body": "承認済みの説明", "status": "approved"}

    def test_candidate_explanation_is_returned_when_no_approved_row(self):
        graph, artifacts, rows, _explanations = _full_case()
        out = build_paper_layer(
            graph,
            artifacts,
            figure_rows=rows,
            explanation_rows=[{"element_id": "comp_1", "body": "候補", "status": "candidate"}],
        )
        assert out["nodes"]["eq_op_0001"]["explanation"] == {"body": "候補", "status": "candidate"}

    def test_missing_explanation_is_null(self):
        graph, artifacts, rows, _explanations = _full_case()
        out = build_paper_layer(graph, artifacts, figure_rows=rows, explanation_rows=[])
        assert out["nodes"]["eq_op_0001"]["explanation"] is None


# ---------------------------------------------------------------------------
# 表示ラベル（PL7）
# ---------------------------------------------------------------------------


def _iter_display_labels(payload):
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in ("display_label", "section_label") and isinstance(value, str):
                yield value
            elif key in ("input_labels", "output_labels", "defining_equation_labels", "equation_labels"):
                for item in value or []:
                    yield item
            else:
                yield from _iter_display_labels(value)
    elif isinstance(payload, list):
        for item in payload:
            yield from _iter_display_labels(item)


class TestDisplayLabels:
    def test_no_internal_ids_appear_in_display_labels(self):
        labels = list(_iter_display_labels(_build_full()))
        assert labels
        for label in labels:
            for prefix in pl_schema.INTERNAL_ID_PREFIXES:
                assert prefix not in label, f"internal id in display_label: {label!r}"

    def test_numbered_equation_uses_the_printed_number(self):
        assert pl_schema.equation_display_label(_equation("eq_12", label="12")) == "式 (12)"

    def test_unnumbered_equation_uses_the_body_head(self):
        record = _equation("eq_op_0007", label=None, plain_text="alpha = beta gamma")
        assert pl_schema.equation_display_label(record) == "番号なし: alpha = beta gamma"

    def test_unnumbered_equation_without_body_degrades(self):
        record = {"equation_id": "eq_9", "label": None, "source_extraction": {}, "reconstruction": {}}
        assert pl_schema.equation_display_label(record) == "番号なし"

    def test_figure_label_falls_back_to_caption_then_generic(self):
        assert pl_schema.figure_display_label("p2_i0", caption="Setup photo") == "Setup photo"
        assert pl_schema.figure_display_label("p2_i0") == "図"
        assert pl_schema.figure_display_label("t9", kind="table") == "表"

    def test_normalize_figure_join_key_matches_both_notations(self):
        assert pl_schema.normalize_figure_join_key("fig_3.3") == pl_schema.normalize_figure_join_key("fig_3_3")

    def test_snippet_truncation_does_not_split_inline_math(self):
        text = "a" * 195 + "$xyz$"
        out = pl_schema.truncate_snippet(text, 198)
        assert out.count("$") % 2 == 0
        assert len(out) <= 198

    def test_snippet_truncation_respects_the_limit(self):
        out = pl_schema.truncate_snippet("b" * 500)
        assert len(out) == pl_schema.TEXT_SNIPPET_MAX


# ---------------------------------------------------------------------------
# 非破壊性（PL1）
# ---------------------------------------------------------------------------


class TestInputsAreNotMutated:
    def test_graph_artifacts_and_rows_are_untouched(self):
        graph, artifacts, figure_rows, explanation_rows = _full_case()
        before = copy.deepcopy((graph, artifacts, figure_rows, explanation_rows))
        build_paper_layer(graph, artifacts, figure_rows=figure_rows, explanation_rows=explanation_rows)
        assert (graph, artifacts, figure_rows, explanation_rows) == before

    def test_repeated_calls_are_deterministic(self):
        graph, artifacts, figure_rows, explanation_rows = _full_case()
        first = build_paper_layer(graph, artifacts, figure_rows=figure_rows, explanation_rows=explanation_rows)
        second = build_paper_layer(graph, artifacts, figure_rows=figure_rows, explanation_rows=explanation_rows)
        assert first == second


# ---------------------------------------------------------------------------
# 壊れた数値フィールドでの fail-soft（PL8）
# ---------------------------------------------------------------------------


class TestMalformedOrderingValuesDegradeSoftly:
    """``level`` / ``order`` / ``display_order`` は LLM 由来 artifact と過去に保存された
    ``graph_json`` から読む。非整数が1つ紛れ込んだだけで論文層まるごとが落ちてはいけない
    （route は例外を ``available: false`` に畳むため、1論文の論文層が全滅する）。
    """

    def test_non_numeric_display_order_does_not_raise(self):
        graph = _graph([_node("n1", display_order="second"), _node("n2", display_order=1)])
        result = build_paper_layer(graph, _full_artifacts())
        assert result["available"] is True
        assert set(result["nodes"]) == {"n1", "n2"}

    def test_non_numeric_section_level_and_order_do_not_raise(self):
        artifacts = _full_artifacts()
        artifacts["document_structure"]["sections"][0]["level"] = "1.1"
        artifacts["document_structure"]["sections"][1]["order"] = "second"
        result = build_paper_layer(_graph([_node("n1")]), artifacts)
        assert result["available"] is True
        levels = {s["section_id"]: s["level"] for s in result["paper"]["sections"]}
        orders = {s["section_id"] for s in result["paper"]["sections"]}
        assert orders == {"s1", "s2"}
        # 変換できない値は既定へ倒し、節そのものは落とさない（情報を落とさない）。
        assert levels["s1"] == 1

    def test_valid_integer_ordering_is_unchanged(self):
        """既定へ倒すのは変換不能なときだけ（正常値の挙動は変えない）。"""
        artifacts = _full_artifacts()
        artifacts["document_structure"]["sections"][0]["order"] = 5
        artifacts["document_structure"]["sections"][1]["order"] = 2
        result = build_paper_layer(_graph([_node("n1")]), artifacts)
        assert [s["section_id"] for s in result["paper"]["sections"]] == ["s2", "s1"]
