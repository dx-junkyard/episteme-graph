"""論文層が claim / 式の二層説明も読むこと（縦の糸 / 実データ由来の是正）。

実測（2026-09-19）では ``element_explanations`` の行は equation 56 / theory_claim 37 /
theory_component 35 / figure 8 と散っているのに、論文層は ``element_type =
'theory_component'`` の行しか渡されておらず、component の説明が1件も無い論文では
ノード詳細の「この論文での説明」が常に空だった。

既存の契約（``explanation`` は component の説明・キーは body / status の2つだけ）は
変えず、component に説明が無いときだけ claim / 式の説明へ降り、どの要素の説明かを
``explanation_element`` で明示する。
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.graph_paper_layer import build_paper_layer  # noqa: E402
from core.graph_paper_layer.schema import EXPLANATION_ELEMENT_TYPES  # noqa: E402

_NODE = "eq_op_0001"


def _graph():
    return {
        "document_id": "doc",
        "nodes": [
            {
                "component_id": _NODE,
                "label": "Linearize",
                "graph_layer": "equation_detail",
                "display_order": 1,
                "input_equation_ids": ["eq_12"],
                "linked_claim_ids": ["claim_a"],
                "linked_component_ids": ["comp_1"],
            }
        ],
        "edges": [],
        "reference_index": {"claims": {"claim_a": {"text": "主張の本文", "claim_id": "u1"}}},
    }


_ARTIFACTS = {
    "equation_semantics": {"equations": [{"equation_id": "eq_12", "label": "(12)"}]},
    "claim_object_builder": {"claims": [{"claim_id": "claim_a", "text": "主張の本文"}]},
    "component_assembly": {"components": [{"component_id": "comp_1", "summary": "部品の要約"}]},
}


def _build(rows):
    return build_paper_layer(_graph(), _ARTIFACTS, figure_rows=[], explanation_rows=rows)


class TestComponentExplanationStillWins:
    def test_component_explanation_is_used_when_present(self):
        out = _build([
            {"element_id": "comp_1", "element_type": "theory_component", "body": "部品の説明", "status": "approved"},
            {"element_id": "claim_a", "element_type": "theory_claim", "body": "主張の説明", "status": "approved"},
        ])
        node = out["nodes"][_NODE]
        assert node["explanation"] == {"body": "部品の説明", "status": "approved"}
        assert node["explanation_element"] == {
            "element_type": "theory_component",
            "element_id": "comp_1",
        }

    def test_rows_without_element_type_are_treated_as_component(self):
        """後方互換 — element_type を持たない呼び出しは従来どおり component 扱い。"""
        out = _build([{"element_id": "comp_1", "body": "部品の説明", "status": "candidate"}])
        assert out["nodes"][_NODE]["explanation"] == {"body": "部品の説明", "status": "candidate"}


class TestFallbackToClaimAndEquation:
    def test_claim_explanation_fills_in_when_the_component_has_none(self):
        out = _build([
            {"element_id": "claim_a", "element_type": "theory_claim", "body": "主張の説明", "status": "approved"},
        ])
        node = out["nodes"][_NODE]
        assert node["explanation"] == {"body": "主張の説明", "status": "approved"}
        assert node["explanation_element"] == {
            "element_type": "theory_claim",
            "element_id": "claim_a",
        }

    def test_equation_explanation_is_the_last_resort(self):
        out = _build([
            {"element_id": "eq_12", "element_type": "equation", "body": "式の説明", "status": "candidate"},
        ])
        node = out["nodes"][_NODE]
        assert node["explanation"] == {"body": "式の説明", "status": "candidate"}
        assert node["explanation_element"]["element_type"] == "equation"

    def test_nothing_is_invented_when_no_row_matches(self):
        out = _build([
            {"element_id": "claim_other", "element_type": "theory_claim", "body": "別の主張", "status": "approved"},
        ])
        node = out["nodes"][_NODE]
        assert node["explanation"] is None
        assert node["explanation_element"] is None

    def test_unknown_element_types_are_ignored(self):
        out = _build([
            {"element_id": "fig_1", "element_type": "figure", "body": "図の説明", "status": "approved"},
        ])
        assert out["nodes"][_NODE]["explanation"] is None


class TestPerItemExplanations:
    def test_claim_item_carries_its_own_explanation(self):
        out = _build([
            {"element_id": "claim_a", "element_type": "theory_claim", "body": "主張の説明", "status": "approved"},
        ])
        [claim] = out["nodes"][_NODE]["claims"]
        assert claim["explanation"] == {"body": "主張の説明", "status": "approved"}

    def test_equation_item_carries_its_own_explanation(self):
        out = _build([
            {"element_id": "eq_12", "element_type": "equation", "body": "式の説明", "status": "approved"},
        ])
        [equation] = out["nodes"][_NODE]["equations"]
        assert equation["explanation"] == {"body": "式の説明", "status": "approved"}

    def test_items_without_an_explanation_carry_none(self):
        out = _build([])
        assert out["nodes"][_NODE]["claims"][0]["explanation"] is None
        assert out["nodes"][_NODE]["equations"][0]["explanation"] is None

    def test_approved_wins_over_candidate_per_element(self):
        out = _build([
            {"element_id": "claim_a", "element_type": "theory_claim", "body": "候補", "status": "candidate"},
            {"element_id": "claim_a", "element_type": "theory_claim", "body": "承認済み", "status": "approved"},
        ])
        assert out["nodes"][_NODE]["claims"][0]["explanation"]["body"] == "承認済み"


class TestVocabulary:
    def test_element_types_are_the_three_the_layer_reads(self):
        assert EXPLANATION_ELEMENT_TYPES == ("theory_component", "theory_claim", "equation")
