"""グラフの論文層 — 文章層（支持構造）と DSL 層の読み時射影（P0-9）。

正本: `docs/architecture/knowledge_structure_review_2026-09-12.md` §4 Phase 0 の P0-9
（付属資料 A_fidelity.md の F-12「DSL 層が最も忠実なのに下流にも学習者にも届いて
いない」/ F-14「壊れているのは単位の側」）。DTO 契約は
`docs/features/graph_paper_layer_design.md` §3 を継承し、不変条項 PL1〜PL8 を守る。

検証観点:

1. `thesis_reconstruction.support_structure` が節ラベル付きで `paper.support_structure`
   に出る（claim は DB UUID、式は印字番号、ノードは thesis_ref 経由の既存対応）。
2. `dsl_linking` のノード・辺が `paper.dsl` に出る。`node_id`（`n_001` 等）は
   識別キーとしてだけ残り、**表示に使える値は `node_value`**（PL7）。
3. 骨格（logical_blocks）のうちノードが掛かっていないものが
   `coverage.unbound_backbone` に出る（失敗ではなく信号 = 設計 §3.2）。
4. artifact 欠落は例外にせず事実文1行（PL8）。
5. confidence / reason の生数値は出ない（PL4）。
6. 入力 dict を mutate しない（PL1）。
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

from core.graph_paper_layer import build_paper_layer  # noqa: E402
from core.graph_paper_layer import schema as pl_schema  # noqa: E402
from core.label_vocab import SUPPORT_SECTION_LABELS  # noqa: E402
from tests.test_graph_paper_layer_core import _build_full, _full_case, _graph, _node  # noqa: E402


def _iter_keys(payload):
    if isinstance(payload, dict):
        for key, value in payload.items():
            yield str(key)
            yield from _iter_keys(value)
    elif isinstance(payload, list):
        for item in payload:
            yield from _iter_keys(item)


# ---------------------------------------------------------------------------
# 中心命題の支持構造（文章層）
# ---------------------------------------------------------------------------


class TestSupportStructure:
    def test_sections_are_labelled_from_the_shared_vocabulary(self):
        sections = _build_full()["paper"]["support_structure"]
        assert [s["section_key"] for s in sections] == ["direct_supports"]
        assert sections[0]["section_label"] == SUPPORT_SECTION_LABELS["direct_supports"]

    def test_entry_carries_text_claims_equation_labels_and_nodes(self):
        entry = _build_full()["paper"]["support_structure"][0]["entries"][0]
        assert entry["text"] == "Because the residual is small."
        assert entry["support_type"] == "empirical"
        assert entry["thesis_ref"] == "support:direct_supports:0"
        # claim は DB UUID に解決できたものだけ（agent ID は出さない）。
        assert entry["claim_ids"] == ["11111111-1111-1111-1111-111111111111"]
        # 式は印字番号（PL7: equation_id を表示に使わない）。
        assert entry["equation_labels"] == ["式 (12)"]
        # ノードは thesis_roles の既存対応から（新しい推定をしていない）。
        assert entry["node_ids"] == ["eq_op_0001", "theory_op_0001"]

    def test_unresolved_claim_ids_are_dropped_not_faked(self):
        graph, artifacts, figure_rows, explanation_rows = _full_case()
        artifacts["thesis_reconstruction"]["support_structure"]["direct_supports"][0][
            "claim_ids"
        ] = ["claim_unknown"]
        out = build_paper_layer(
            graph, artifacts, figure_rows=figure_rows, explanation_rows=explanation_rows
        )
        entry = out["paper"]["support_structure"][0]["entries"][0]
        assert entry["claim_ids"] == []
        assert entry["text"]

    def test_empty_entries_do_not_create_a_section(self):
        graph, artifacts, figure_rows, explanation_rows = _full_case()
        artifacts["thesis_reconstruction"]["support_structure"] = {
            "assumptions": [{"text": "", "claim_ids": [], "equation_ids": []}]
        }
        out = build_paper_layer(
            graph, artifacts, figure_rows=figure_rows, explanation_rows=explanation_rows
        )
        assert out["paper"]["support_structure"] == []

    def test_missing_thesis_artifact_is_a_fact_not_an_error(self):
        graph, artifacts, figure_rows, explanation_rows = _full_case()
        artifacts.pop("thesis_reconstruction")
        out = build_paper_layer(
            graph, artifacts, figure_rows=figure_rows, explanation_rows=explanation_rows
        )
        assert out["paper"]["support_structure"] == []
        assert out["facts"] == [pl_schema.FACT_NO_THESIS]

    def test_reason_and_confidence_are_not_projected(self):
        keys = set(
            _iter_keys(_build_full()["paper"]["support_structure"])
        )
        assert "reason" not in keys
        assert not (set(pl_schema.FORBIDDEN_KEYS) & keys)


# ---------------------------------------------------------------------------
# DSL 層
# ---------------------------------------------------------------------------


class TestDslProjection:
    def test_nodes_expose_values_not_internal_ids(self):
        dsl = _build_full()["paper"]["dsl"]
        # 同じ章（s2）に落ちるので artifact の出力順がそのまま残る。
        assert [n["node_value"] for n in dsl["nodes"]] == [
            "linear regime approximation",
            "residual amplitude",
        ]
        # node_id は識別キーとしては残す（表示に使うのは node_value）。
        assert {n["node_id"] for n in dsl["nodes"]} == {"n_001", "n_002"}
        assert dsl["nodes"][0]["is_thesis_anchor"] is True
        assert dsl["nodes"][1]["is_thesis_anchor"] is False

    def test_nodes_are_ordered_by_the_paper_not_by_id(self):
        """章順（s1 → s2）で並べる。claim / 式のどちらからも章が引けないノードは末尾。"""
        graph, artifacts, figure_rows, explanation_rows = _full_case()
        artifacts["dsl_linking"]["nodes"].append({
            "node_id": "n_003",
            "node_type": "Result",
            "node_value": "no location",
            "source_kind": "mixed",
            "source_refs": {"claim_ids": [], "equation_ids": [], "thesis_refs": []},
        })
        out = build_paper_layer(
            graph, artifacts, figure_rows=figure_rows, explanation_rows=explanation_rows
        )
        assert [n["node_id"] for n in out["paper"]["dsl"]["nodes"]][-1] == "n_003"
        assert out["paper"]["dsl"]["nodes"][-1]["section_id"] == ""

    def test_nodes_link_back_to_graph_nodes_via_claims_and_equations(self):
        by_id = {n["node_id"]: n for n in _build_full()["paper"]["dsl"]["nodes"]}
        assert by_id["n_001"]["node_ids"] == ["eq_op_0001", "theory_op_0001"]
        assert by_id["n_002"]["node_ids"] == ["eq_op_0001", "theory_op_0001"]
        assert by_id["n_001"]["section_id"] == "s2"

    def test_nodes_without_a_value_are_dropped(self):
        """node_id しか出せないノードは画面に出さない（PL7）。"""
        graph, artifacts, figure_rows, explanation_rows = _full_case()
        artifacts["dsl_linking"]["nodes"][0]["node_value"] = "   "
        out = build_paper_layer(
            graph, artifacts, figure_rows=figure_rows, explanation_rows=explanation_rows
        )
        dsl = out["paper"]["dsl"]
        assert [n["node_id"] for n in dsl["nodes"]] == ["n_002"]
        # 端点が落ちた辺も消える（内部 ID が露出しないため）。
        assert dsl["edges"] == []

    def test_edges_carry_predicate_verb_and_a_polarity_word(self):
        edge = _build_full()["paper"]["dsl"]["edges"][0]
        assert edge["from_node_id"] == "n_001"
        assert edge["to_node_id"] == "n_002"
        assert edge["core_predicate"] == "DEFINES"
        assert edge["domain_verb"] == "defines"
        # 記号のままでは読めないので語も添える（正本の語彙は dsl_linking 側）。
        assert edge["polarity"] == "+"
        assert edge["polarity_label"] == pl_schema.DSL_POLARITY_LABELS["+"]

    def test_unknown_polarity_gets_no_invented_word(self):
        graph, artifacts, figure_rows, explanation_rows = _full_case()
        artifacts["dsl_linking"]["edges"][0]["polarity"] = "??"
        out = build_paper_layer(
            graph, artifacts, figure_rows=figure_rows, explanation_rows=explanation_rows
        )
        assert out["paper"]["dsl"]["edges"][0]["polarity_label"] == ""

    def test_duplicate_edges_are_collapsed(self):
        graph, artifacts, figure_rows, explanation_rows = _full_case()
        artifacts["dsl_linking"]["edges"].append(
            dict(artifacts["dsl_linking"]["edges"][0], edge_id="e_002")
        )
        out = build_paper_layer(
            graph, artifacts, figure_rows=figure_rows, explanation_rows=explanation_rows
        )
        assert len(out["paper"]["dsl"]["edges"]) == 1

    def test_missing_dsl_artifact_is_a_fact_not_an_error(self):
        graph, artifacts, figure_rows, explanation_rows = _full_case()
        artifacts.pop("dsl_linking")
        out = build_paper_layer(
            graph, artifacts, figure_rows=figure_rows, explanation_rows=explanation_rows
        )
        assert out["paper"]["dsl"] == {"nodes": [], "edges": []}
        assert out["facts"] == [pl_schema.FACT_NO_DSL]

    def test_reason_and_confidence_are_not_projected(self):
        keys = set(_iter_keys(_build_full()["paper"]["dsl"]))
        assert "reason" not in keys
        assert not (set(pl_schema.FORBIDDEN_KEYS) & keys)


# ---------------------------------------------------------------------------
# 被覆（掛かっていない骨格）
# ---------------------------------------------------------------------------


class TestUnboundBackbone:
    def test_backbone_with_nodes_is_not_reported_as_unbound(self):
        out = _build_full()
        assert out["paper"]["backbone"][0]["node_ids"]
        assert out["coverage"]["unbound_backbone"] == []

    def test_backbone_without_nodes_is_reported(self):
        """論文Bのように「骨格はあるがフレームが覆っていない章」を正直に出す。"""
        graph, artifacts, figure_rows, explanation_rows = _full_case()
        artifacts["paper_skeleton"]["logical_blocks"].append({
            "block_id": "lb_2",
            "block_type": "result",
            "label": "Performance evaluation",
            "section_ids": ["s-unbound"],
            "summary": "The actual result.",
        })
        out = build_paper_layer(
            graph, artifacts, figure_rows=figure_rows, explanation_rows=explanation_rows
        )
        assert out["coverage"]["unbound_backbone"] == [
            {"label": "Performance evaluation", "block_type": "result"}
        ]

    def test_unlabelled_blocks_are_not_reported_as_ids(self):
        graph, artifacts, figure_rows, explanation_rows = _full_case()
        artifacts["paper_skeleton"]["logical_blocks"].append({
            "block_id": "lb_3",
            "block_type": "result",
            "label": "",
            "section_ids": [],
            "summary": "",
        })
        out = build_paper_layer(
            graph, artifacts, figure_rows=figure_rows, explanation_rows=explanation_rows
        )
        assert out["coverage"]["unbound_backbone"] == []

    def test_unavailable_graph_still_has_the_key(self):
        out = build_paper_layer({"nodes": []}, {})
        assert out["coverage"]["unbound_backbone"] == []
        assert out["paper"]["support_structure"] == []
        assert out["paper"]["dsl"] == {"nodes": [], "edges": []}


# ---------------------------------------------------------------------------
# PL1: 入力を書き換えない
# ---------------------------------------------------------------------------


class TestNoMutation:
    def test_dsl_and_thesis_artifacts_are_not_mutated(self):
        graph, artifacts, figure_rows, explanation_rows = _full_case()
        before = copy.deepcopy(artifacts)
        build_paper_layer(
            graph, artifacts, figure_rows=figure_rows, explanation_rows=explanation_rows
        )
        assert artifacts == before

    def test_node_id_lists_are_not_shared_with_the_node_dtos(self):
        out = _build_full()
        entry = out["paper"]["support_structure"][0]["entries"][0]
        entry["node_ids"].append("injected")
        assert "injected" not in out["paper"]["dsl"]["nodes"][0]["node_ids"]


# ---------------------------------------------------------------------------
# 単体の graph でも壊れない（PL8 fail-soft）
# ---------------------------------------------------------------------------


class TestFailSoft:
    def test_dsl_survives_a_graph_without_reference_index(self):
        out = build_paper_layer(
            _graph([_node("theory_op_0001")]),
            {
                "dsl_linking": {
                    "nodes": [
                        {
                            "node_id": "n_001",
                            "node_value": "a value",
                            "source_refs": {"claim_ids": ["claim_a"]},
                        }
                    ],
                    "edges": [{"from_node_id": "n_001", "to_node_id": "n_missing"}],
                }
            },
        )
        dsl = out["paper"]["dsl"]
        assert [n["node_value"] for n in dsl["nodes"]] == ["a value"]
        assert dsl["nodes"][0]["node_ids"] == []
        assert dsl["edges"] == []

    def test_malformed_dsl_payload_does_not_raise(self):
        out = build_paper_layer(
            _graph([_node("theory_op_0001")]),
            {"dsl_linking": {"nodes": "not-a-list", "edges": None}},
        )
        assert out["paper"]["dsl"] == {"nodes": [], "edges": []}
