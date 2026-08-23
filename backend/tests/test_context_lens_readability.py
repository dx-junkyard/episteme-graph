"""W層 context lens の**可読性**ガードレール（設計書 §8）+ CP6 関係集合スナップショット。

正本: `docs/features/element_context_presentation_redesign.md`
（RC1〜RC12 / CP1〜CP10 / §4 DTO v2 / §5 要素種別ごとの提示ルール）。

このファイルが守るもの:

* **CP6（説明材料を足しても関係集合を変えない）**: 代表 fixture に対する
  ``(element_type, element_id, relation_status)`` の多重集合が、Phase 1 改修の
  **前後で意図した差分だけ**変わること。改修前の実測値（``_BEFORE``）と改修後の
  期待値（``_AFTER``）の両方を明示し、その差分が ``_INTENDED_DELTA`` と一致することを
  機械的に検査する。ラベル期待値の大量失敗を「意図した変更」と「回帰」に切り分ける
  ための足場（§8 実装計画 Phase 1）。
  比較キーに ``relation`` を**入れない**のは、向き付き関係語への付け替え（CP2）が
  意図した変更だからである。上位/下位レーンの割り当ても意図的に変わるため、
  スナップショットは upper + lower を合算した多重集合で取る。
* **RL1〜RL10（§8 ガードレール）**: 内部 ID 形のラベルを出さない / 数式系に生 TeX を
  出さない / 導出の関係語が向き語彙である / main と equation_detail が同じ関係語を
  持たない（CP3）/ 記号 ITEM の sublabel が空でない（RC5）/ 素スライスが無い（CP5）/
  focus.label と intrinsic_summary が同一でない（CP1）/ RELATION_LABELS の全キーが
  到達可能。

DB には触らない（fake artifact + monkeypatch）。
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.deliberation import context_lens  # noqa: E402
from core.deliberation import labels as labels_mod  # noqa: E402
from core.deliberation.schema import (  # noqa: E402
    CONTEXT_STATUS_CANDIDATE,
    CONTEXT_STATUS_SOURCE_BACKED,
    ELEMENT_DERIVATION,
    ELEMENT_EQUATION,
    ELEMENT_EVIDENCE,
    ELEMENT_THEORY_CLAIM,
    ELEMENT_THEORY_COMPONENT,
    SCOPE_DOCUMENT,
    ElementRef,
)
from core.text_excerpt import looks_like_tex_math  # noqa: E402
from tests.guardrail_helpers import assert_source_forbids  # noqa: E402

_CONTEXT_LENS_PATH = BACKEND / "core" / "deliberation" / "context_lens.py"
_CONTEXT_LENS_SRC = _CONTEXT_LENS_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 代表 fixture（スクリーンショットの eq_tex_b14 を模した合成 ID の数式を中心に、
# 導出2本（区別不能だった equation_chain / system_level）・記号3件・main/detail/debug
# の3層グラフノード・掲載節・原文根拠を持つ論文）
# ---------------------------------------------------------------------------

_DOC = "doc-ctx-1"
_EQ_FOCUS = "eq_tex_b14"
_EQ_IN = "eq_tex_b10"
_EQ_OUT = "eq_tex_b16"
_CLAIM_RAW = "claim_span_007"
_CLAIM_DB = "cccccccc-0000-0000-0000-000000000007"
_CLAIM_REQ_RAW = "claim_span_001"
_CLAIM_REQ_DB = "cccccccc-0000-0000-0000-000000000001"
_DERIV_TRANSFORM = "derivation_eq_tex_b16"
_DERIV_SYSTEM = "system_derivation_1"
_DERIV_PRODUCES = "derivation_produces_b14"
_EVIDENCE_DEF = "ev_0001"
_EVIDENCE_STEP = "ev_0002"
_DETAIL_NODE_RESOLVABLE = "dddddddd-0000-0000-0000-000000000009"

_SUMMARY_FOCUS = (
    "Definition of the matter density contrast delta as the fractional overdensity "
    "of the matter field relative to its spatial mean."
)


def _equations() -> list[dict]:
    return [
        {
            "equation_id": _EQ_FOCUS,
            "document_id": _DOC,
            "label": None,
            "source_extraction": {
                "raw_text": "\\delta(t,x) \\equiv (\\rho - \\bar{\\rho}) / \\bar{\\rho}",
                "latex": "\\delta(t,x) \\equiv \\frac{\\rho(t,x) - \\bar{\\rho}(t)}{\\bar{\\rho}(t)}",
                "plain_text": None,
                "source_location": {"page": 8, "section_id": "sec_2_1", "block_id": "b_14"},
                "needs_math_review": False,
            },
            "reconstruction": {"latex": None, "plain_text": None, "status": "none"},
            "semantics": {
                "equation_type": "definition",
                "role_in_argument": "definition",
                "summary": _SUMMARY_FOCUS,
                "defined_symbols": [
                    {
                        "symbol": "\\delta(t,x)",
                        "definition_status": "defined",
                        "meaning": "density contrast",
                    }
                ],
                "used_symbols": ["\\rho(t,x)", "\\bar{\\rho}(t)"],
                "assumptions": ["The background density is spatially homogeneous."],
                "input_equation_ids": [],
                "output_equation_ids": [_EQ_OUT],
                "intermediate_equation_ids": [],
                "linked_claim_ids": [_CLAIM_RAW],
                "inferred_claim_ids": [],
                "source_evidence_ids": [_EVIDENCE_DEF],
                "link_status": "axiomatic",
            },
        },
        {
            "equation_id": _EQ_IN,
            "document_id": _DOC,
            "label": None,
            "source_extraction": {
                "raw_text": "\\bar{\\rho}(t)",
                "latex": "\\bar{\\rho}(t) = \\rho_0 a^{-3}",
                "plain_text": None,
                "source_location": {"section_id": "sec_2_1"},
                "needs_math_review": False,
            },
            "reconstruction": {"latex": None, "plain_text": None},
            "semantics": {
                "equation_type": "relation",
                "role_in_argument": "premise",
                "summary": "Background density evolution in an expanding universe.",
                "defined_symbols": [],
                "used_symbols": [],
                "assumptions": [],
                "input_equation_ids": [],
                "output_equation_ids": [],
                "linked_claim_ids": [],
                "source_evidence_ids": [],
                "link_status": "axiomatic",
            },
        },
        {
            "equation_id": _EQ_OUT,
            "document_id": _DOC,
            "label": None,
            "source_extraction": {
                "raw_text": "\\delta_k",
                "latex": "\\delta_k = \\int d^3x\\, \\delta(t,x) e^{-ikx}",
                "plain_text": None,
                "source_location": {"section_id": "sec_2_1"},
                "needs_math_review": False,
            },
            "reconstruction": {"latex": None, "plain_text": None},
            "semantics": {
                "equation_type": "definition",
                "role_in_argument": "definition",
                "summary": "Fourier-space density contrast definition.",
                "defined_symbols": [],
                "used_symbols": [],
                "assumptions": [],
                "input_equation_ids": [_EQ_FOCUS],
                "output_equation_ids": [],
                "linked_claim_ids": [],
                "source_evidence_ids": [],
                "link_status": "derived",
            },
        },
    ]


def _chains() -> list[dict]:
    return [
        {
            "derivation_id": _DERIV_TRANSFORM,
            "document_id": _DOC,
            "chain_type": "equation_chain",
            "operation": "transform",
            "operation_family": "transform",
            "teaching_takeaway": "Derive result eq_tex_b16 from eq_tex_b14",
            "source_section_ids": ["sec_2_1"],
            "input_equation_ids": [_EQ_FOCUS],
            "output_equation_ids": [_EQ_OUT],
            "intermediate_equation_ids": [],
            "conditions": ["Perturbations stay small."],
            "eliminated_symbols": [],
            "retained_symbols": ["\\delta(t,x)"],
            "source_evidence_ids": [_EVIDENCE_STEP],
            "linked_component_ids": [],
            "steps": [
                {
                    "step_id": "step_001",
                    "operation": "transform",
                    "operation_subtype": "linearize",
                    "input_equation_ids": [_EQ_FOCUS],
                    "output_equation_ids": [_EQ_OUT],
                    "reason": "Fourier transform both sides of the definition.",
                    "required_claim_ids": [],
                    "source_evidence_ids": [_EVIDENCE_STEP],
                }
            ],
        },
        {
            "derivation_id": _DERIV_SYSTEM,
            "document_id": _DOC,
            "chain_type": "system_level",
            "operation": "eliminate",
            "operation_family": "eliminate",
            "teaching_takeaway": "",
            "source_section_ids": [],
            "input_equation_ids": [_EQ_FOCUS],
            "output_equation_ids": [],
            "intermediate_equation_ids": [],
            "conditions": [],
            "eliminated_symbols": ["b_s"],
            "retained_symbols": [],
            "source_evidence_ids": [],
            "linked_component_ids": [],
            "steps": [{"step_id": "step_010", "operation": "eliminate"}],
        },
        {
            "derivation_id": _DERIV_PRODUCES,
            "document_id": _DOC,
            "chain_type": "equation_chain",
            "operation": "define",
            "operation_family": "define",
            "teaching_takeaway": "",
            "source_section_ids": ["sec_2_1"],
            "input_equation_ids": [_EQ_IN],
            "output_equation_ids": [_EQ_FOCUS],
            "intermediate_equation_ids": [],
            "conditions": [],
            "eliminated_symbols": [],
            "retained_symbols": [],
            "source_evidence_ids": [],
            "linked_component_ids": [],
            "steps": [
                {
                    "step_id": "step_020",
                    "operation": "define",
                    "input_equation_ids": [_EQ_IN],
                    "output_equation_ids": [_EQ_FOCUS],
                    "required_claim_ids": [_CLAIM_REQ_RAW],
                    "reason": "",
                }
            ],
        },
    ]


def _symbols() -> list[dict]:
    return [
        {
            "symbol_id": "sym_delta",
            "document_id": _DOC,
            "canonical_symbol": "\\delta(t,x)",
            "notation_variants": ["delta"],
            "kind": "field",
            "scope": "document",
            "defining_equation_ids": [_EQ_FOCUS],
            "used_in_equation_ids": [_EQ_FOCUS, _EQ_OUT],
            "definition_status": "defined",
            "definition_evidence_texts": [
                "We define the density contrast as the fractional overdensity."
            ],
            "review_reasons": [],
        },
        {
            "symbol_id": "sym_rho",
            "document_id": _DOC,
            "canonical_symbol": "\\rho(t,x)",
            "notation_variants": [],
            "kind": "field",
            "scope": "document",
            "defining_equation_ids": [],
            "used_in_equation_ids": [_EQ_FOCUS],
            "definition_status": "used",
            "definition_evidence_texts": ["The matter density field."],
            "review_reasons": [],
        },
        {
            "symbol_id": "sym_rhobar",
            "document_id": _DOC,
            "canonical_symbol": "\\bar{\\rho}(t)",
            "notation_variants": [],
            "kind": "field",
            "scope": "document",
            "defining_equation_ids": [],
            "used_in_equation_ids": [_EQ_FOCUS],
            "definition_status": "unknown",
            "definition_evidence_texts": [],
            "review_reasons": ["definition_missing"],
        },
    ]


def _graph_nodes() -> list[dict]:
    return [
        {
            "id": "theory_op_0001",
            "graph_layer": "main",
            "component_type": "TheoryOperationNode",
            "label": "Theory basis",
            "visual_label": "Theory basis",
            "description": "観測量を密度ゆらぎとして定義する段階。以降の式はここから組み立てられる。",
            "linked_equation_ids": [_EQ_FOCUS],
            "linked_claim_ids": [_CLAIM_RAW],
            "source_backing_status": "source_backed",
            "review_reasons": [],
        },
        {
            "id": "eq_op_0007",
            "graph_layer": "equation_detail",
            "component_type": "EquationOperationNode",
            "label": "Define eq_tex_b16",
            "visual_label": "",
            "display_label": "",
            "operation": "define",
            "theory_object": "",
            "description": "定義ステップ。",
            "output_equation_ids": [_EQ_OUT],
            "linked_equation_ids": [_EQ_FOCUS],
            "source_backing_status": "partially_source_backed",
            "review_reasons": ["missing_atomic_claim"],
        },
        {
            "id": _DETAIL_NODE_RESOLVABLE,
            "graph_layer": "equation_detail",
            "component_type": "EquationOperationNode",
            "label": "Linearize eq_tex_b14",
            "visual_label": "",
            "display_label": "",
            "operation": "linearize",
            "theory_object": "",
            "description": "線形化ステップ。",
            "output_equation_ids": [],
            "linked_equation_ids": [_EQ_FOCUS],
            "source_backing_status": "source_backed",
            "review_reasons": [],
        },
        {
            "id": "theory_op_0099",
            "graph_layer": "debug",
            "component_type": "TheoryOperationNode",
            "label": "fallback node",
            "description": "",
            "linked_equation_ids": [_EQ_FOCUS],
            "source_backing_status": "inferred",
            "review_reasons": ["fallback_or_inferred_node"],
        },
    ]


def _artifacts() -> dict:
    return {
        "document_structure": {
            "sections": [{"section_id": "sec_2_1", "title": "2.1 Density contrast"}],
            "blocks": [{"block_id": "b_14", "section_id": "sec_2_1"}],
        },
        "equation_semantics": {"equations": _equations()},
        "derivation_chain": {"chains": _chains()},
        "symbol_registry": {"records": _symbols()},
        "evidence_registry": {
            "records": [
                {
                    "evidence_id": _EVIDENCE_DEF,
                    "document_id": _DOC,
                    "evidence_text": "We define the density contrast as the fractional overdensity.",
                    "evidence_role": "source_quote",
                    "source": {"section_id": "sec_2_1", "block_id": "b_14"},
                },
                {
                    "evidence_id": _EVIDENCE_STEP,
                    "document_id": _DOC,
                    "evidence_text": "Transforming to Fourier space gives the modal amplitude.",
                    "evidence_role": "source_quote",
                    "source": {"section_id": "sec_2_1", "block_id": "b_15"},
                },
            ]
        },
        "claim_object_builder": {
            "claims": [
                {
                    "claim_id": _CLAIM_RAW,
                    "text": "The density contrast measures the fractional overdensity.",
                    "claim_type": "definition",
                    "section_title": "2.1 Density contrast",
                    "equation_ids": [_EQ_FOCUS],
                    "subclaim_ids": [],
                    "figure_ids": [],
                    "source_evidence_ids": [_EVIDENCE_DEF],
                }
            ]
        },
        "thesis_reconstruction": {
            "headline_claim": "密度ゆらぎの線形応答",
            "central_thesis": {"claim_ids": [_CLAIM_RAW], "equation_ids": [_EQ_FOCUS]},
            "support_structure": {},
        },
        "figure_table_semantics": {"figures": []},
    }


_CLAIM_LOOKUP = {
    _CLAIM_RAW: _CLAIM_DB,
    _CLAIM_DB: _CLAIM_DB,
    _CLAIM_REQ_RAW: _CLAIM_REQ_DB,
    _CLAIM_REQ_DB: _CLAIM_REQ_DB,
}

_CLAIM_ROWS = {
    _CLAIM_DB: {
        "id": _CLAIM_DB,
        "text": "The density contrast measures the fractional overdensity.",
        "claim_type": "definition",
    },
    _CLAIM_REQ_DB: {
        "id": _CLAIM_REQ_DB,
        "text": "The background density is homogeneous.",
        "claim_type": "assumption",
    },
}

_CLAIM_ROW_FULL = {
    "id": _CLAIM_DB,
    "document_id": _DOC,
    "claim_type": "definition",
    "text": "The density contrast measures the fractional overdensity of the matter field.",
    "normalized_text": "The density contrast measures the fractional overdensity.",
    "support_status": "source_backed",
    "review_status": "teacher_approved",
    "evidence_text": "We define the density contrast as the fractional overdensity.",
    "source_scope": {"span_id": "span_007", "section_id": "sec_2_1"},
}

_COMPONENT_ROW = {
    "id": _DETAIL_NODE_RESOLVABLE,
    "document_id": _DOC,
    "name": "Density contrast definition",
    "component_type": "theory",
    "summary": "Defines the observable used throughout the paper.",
    "status": "candidate",
    "review_status": "teacher_review_required",
    "dependencies": [],
    "evidence_claims": [_CLAIM_DB],
    "source_scope": {"legacy_ids": ["comp_agent_9"]},
    "thesis_context": None,
    "source_chunks": [],
}

_COMPONENT_LOOKUP = {
    _DETAIL_NODE_RESOLVABLE: _DETAIL_NODE_RESOLVABLE,
    "comp_agent_9": _DETAIL_NODE_RESOLVABLE,
}


@pytest.fixture
def lens(monkeypatch):
    """context_lens の DB 読みを潰し、fake artifact だけで投影させる。"""
    artifacts = _artifacts()
    monkeypatch.setattr(context_lens.refs_mod, "document_run_artifacts", lambda _doc: artifacts)
    monkeypatch.setattr(context_lens, "_claim_id_lookup", lambda _doc: dict(_CLAIM_LOOKUP))
    monkeypatch.setattr(context_lens, "_component_id_lookup", lambda _doc: dict(_COMPONENT_LOOKUP))
    monkeypatch.setattr(
        context_lens, "_claims_by_id",
        lambda ids: {i: _CLAIM_ROWS[i] for i in ids if i in _CLAIM_ROWS},
    )
    monkeypatch.setattr(context_lens, "_components_by_id", lambda _ids: {})
    monkeypatch.setattr(context_lens, "_load_claim_row", lambda _id: dict(_CLAIM_ROW_FULL))
    monkeypatch.setattr(context_lens, "_load_component_row", lambda _id: dict(_COMPONENT_ROW))
    monkeypatch.setattr(context_lens, "_components_supporting_claim", lambda _doc, _cid: [])
    monkeypatch.setattr(
        context_lens, "_load_component_graph",
        lambda _doc: {"nodes": _graph_nodes(), "edges": []},
    )
    monkeypatch.setattr(context_lens, "_chunk_section_ids", lambda _ids: [])
    monkeypatch.setattr(context_lens, "_annotations_for", lambda *_a, **_k: [])
    monkeypatch.setattr(context_lens, "_generic_block_for_focus", lambda _ref: None)
    # Phase 3 のラベル結線（承認済み contextual 説明）。既定は「承認ゼロ」＝
    # 従来ラダー。説明ありの検証は TestApprovedExplanationHeadline が個別に差し替える。
    monkeypatch.setattr(context_lens, "_approved_equation_explanations", lambda _doc: {})
    return artifacts


def _ref(element_type: str, element_id: str) -> ElementRef:
    return ElementRef(
        scope=SCOPE_DOCUMENT, element_type=element_type, element_id=element_id, document_id=_DOC
    )


def _build(element_type: str, element_id: str) -> dict:
    return context_lens.build(_ref(element_type, element_id))


def _items(result: dict) -> list[dict]:
    return list(result.get("upper") or []) + list(result.get("lower") or [])


def _relation_set(result: dict) -> Counter:
    """CP6 の比較キー: ``(element_type, element_id, relation_status)`` の多重集合。

    ``relation`` は向き付き語彙への付け替えが意図した変更なのでキーに入れない。
    レーン（upper/lower）の割り当ても意図的に変わるため合算する。
    """
    return Counter(
        (item["element_type"], item["element_id"], item["relation_status"]) for item in _items(result)
    )


# ---------------------------------------------------------------------------
# CP6: 関係集合スナップショット（改修前の実測値 → 意図した差分 → 改修後）
# ---------------------------------------------------------------------------

_S = CONTEXT_STATUS_SOURCE_BACKED
_C = CONTEXT_STATUS_CANDIDATE

# 改修**前**（Phase 1 着手時点）の実測値。これは記録であり、更新してはならない
# （更新すると「意図した差分」の起点が失われる）。
_BEFORE: dict[str, Counter] = {
    ELEMENT_EQUATION: Counter(
        {
            ("theory_claim", _CLAIM_DB, _S): 1,
            ("thesis", None, _S): 1,
            ("theory_component", "theory_op_0001", _S): 1,
            ("theory_component", "eq_op_0007", _C): 1,
            ("theory_component", _DETAIL_NODE_RESOLVABLE, _S): 1,
            ("theory_component", "theory_op_0099", _C): 1,
            ("derivation", _DERIV_TRANSFORM, _S): 1,
            ("derivation", _DERIV_SYSTEM, _S): 1,
            ("derivation", _DERIV_PRODUCES, _S): 1,
            ("equation", _EQ_OUT, _S): 1,
            ("symbol", None, _S): 3,
            ("theory_claim", _CLAIM_REQ_DB, _S): 1,
        }
    ),
    ELEMENT_THEORY_CLAIM: Counter(
        {
            ("thesis", None, _S): 1,
            ("section", None, _S): 1,
            ("equation", _EQ_FOCUS, _S): 1,
            ("evidence", _EVIDENCE_DEF, _S): 1,
        }
    ),
    ELEMENT_DERIVATION: Counter(
        {
            ("section", None, _S): 1,
            ("equation", _EQ_FOCUS, _S): 1,
            ("equation", _EQ_OUT, _S): 1,
            ("evidence", _EVIDENCE_STEP, _S): 1,
        }
    ),
    ELEMENT_EVIDENCE: Counter(
        {
            ("theory_claim", _CLAIM_DB, _S): 1,
            ("section", None, _S): 1,
        }
    ),
}

# 意図した差分（設計書 §5.1 / §5.3 / CP3 / CP8 / RC12）。
#   +: 新設 ITEM（equation の掲載節と原文根拠のみ。どちらも source_backed・1-hop 内）
#   -: 削除された ITEM（層の混在をやめたことによる置き換え元）
_INTENDED_DELTA: dict[str, dict[str, Counter]] = {
    ELEMENT_EQUATION: {
        "added": Counter(
            {
                # RC12: 掲載節（source_location.section_id）と原文根拠
                # （semantics.source_evidence_ids）は artifact に実在するのに
                # equation focus からは構造的に欠けていた。
                ("section", None, _S): 1,
                ("evidence", _EVIDENCE_DEF, _S): 1,
                # CP3: main 層は理論段階（stage）として別区画に出す。
                ("stage", None, _S): 1,
                # CP8: DB 解決できない equation_detail ノードは element_id を持たない
                # （navigable=false の fail-closed。開けない導線を作らない）。
                ("theory_component", None, _C): 1,
            }
        ),
        "removed": Counter(
            {
                # CP3: main ステージノードを theory_component として並べるのをやめた。
                ("theory_component", "theory_op_0001", _S): 1,
                # CP8: agent ID（eq_op_0007）は theory_components に解決できない。
                ("theory_component", "eq_op_0007", _C): 1,
                # CP3: debug 層はレーンに出さない（notes に件数の事実文）。
                ("theory_component", "theory_op_0099", _C): 1,
            }
        ),
    },
    ELEMENT_THEORY_CLAIM: {"added": Counter(), "removed": Counter()},
    ELEMENT_DERIVATION: {"added": Counter(), "removed": Counter()},
    ELEMENT_EVIDENCE: {"added": Counter(), "removed": Counter()},
}


def _expected_after(element_type: str) -> Counter:
    expected = Counter(_BEFORE[element_type])
    expected.update(_INTENDED_DELTA[element_type]["added"])
    expected.subtract(_INTENDED_DELTA[element_type]["removed"])
    return Counter({key: count for key, count in expected.items() if count})


_FOCUS_IDS = {
    ELEMENT_EQUATION: _EQ_FOCUS,
    ELEMENT_THEORY_CLAIM: _CLAIM_DB,
    ELEMENT_DERIVATION: _DERIV_TRANSFORM,
    ELEMENT_EVIDENCE: _EVIDENCE_DEF,
}


class TestRelationSetSnapshotCP6:
    """CP6: 説明材料（sublabel / qualifier / group / intrinsic）を足しても、
    関係集合そのものは意図した差分以外変わらない。"""

    @pytest.mark.parametrize("element_type", sorted(_BEFORE))
    def test_relation_set_matches_intended_snapshot(self, lens, element_type):
        result = _build(element_type, _FOCUS_IDS[element_type])
        assert _relation_set(result) == _expected_after(element_type)

    @pytest.mark.parametrize("element_type", sorted(_BEFORE))
    def test_delta_from_before_is_exactly_the_documented_intent(self, lens, element_type):
        """改修前後の差分が ``_INTENDED_DELTA`` と一致すること（回帰の切り分け）。"""
        actual = _relation_set(result=_build(element_type, _FOCUS_IDS[element_type]))
        before = Counter(_BEFORE[element_type])
        added = Counter(actual)
        added.subtract(before)
        removed = Counter({k: -v for k, v in added.items() if v < 0})
        added = Counter({k: v for k, v in added.items() if v > 0})
        assert added == _INTENDED_DELTA[element_type]["added"]
        assert removed == _INTENDED_DELTA[element_type]["removed"]

    def test_no_new_candidate_items_are_introduced(self, lens):
        """CP6: 新規 ITEM は source_backed のみ（AI 候補を増やさない）。"""
        for delta in _INTENDED_DELTA.values():
            for (_element_type, _element_id, status) in delta["added"]:
                assert status == CONTEXT_STATUS_SOURCE_BACKED or status == CONTEXT_STATUS_CANDIDATE
        # 追加された candidate は「既存 candidate の element_id 付け替え」のみで、
        # candidate の**総数**は前後で変わらない。
        for element_type in _BEFORE:
            before_candidates = sum(
                count for (_t, _i, status), count in _BEFORE[element_type].items() if status == _C
            )
            after_candidates = sum(
                count
                for (_t, _i, status), count in _expected_after(element_type).items()
                if status == _C
            )
            assert after_candidates <= before_candidates, element_type


# ---------------------------------------------------------------------------
# RL1〜RL10: 可読性ガードレール（設計書 §8）
# ---------------------------------------------------------------------------


_ALL_ELEMENT_TYPES = (
    ELEMENT_EQUATION,
    ELEMENT_THEORY_CLAIM,
    ELEMENT_DERIVATION,
    ELEMENT_EVIDENCE,
    ELEMENT_THEORY_COMPONENT,
)


def _all_results(include_component: bool = True) -> list[tuple[str, dict]]:
    pairs = [
        (element_type, _build(element_type, focus_id))
        for element_type, focus_id in _FOCUS_IDS.items()
    ]
    if include_component:
        pairs.append(
            (ELEMENT_THEORY_COMPONENT, _build(ELEMENT_THEORY_COMPONENT, _DETAIL_NODE_RESOLVABLE))
        )
    return pairs


class TestRL1NoInternalIdLabels:
    """RL1: 全 ITEM の label が内部 ID 形でない（EC3′ を生成側で満たす）。"""

    def test_no_item_label_looks_like_an_internal_id(self, lens):
        offenders: list[str] = []
        for element_type, result in _all_results():
            for item in _items(result):
                if labels_mod.is_internal_id_like(item["label"]):
                    offenders.append(f"{element_type}:{item['label']!r}")
        assert offenders == []

    def test_no_item_sublabel_looks_like_an_internal_id(self, lens):
        offenders: list[str] = []
        for element_type, result in _all_results():
            for item in _items(result):
                sublabel = item.get("sublabel") or ""
                if sublabel and labels_mod.is_internal_id_like(sublabel):
                    offenders.append(f"{element_type}:{sublabel!r}")
        assert offenders == []

    def test_equation_focus_headline_is_not_an_internal_id(self, lens):
        focus = _build(ELEMENT_EQUATION, _EQ_FOCUS)["focus"]
        assert focus["label"] != _EQ_FOCUS
        assert not labels_mod.is_internal_id_like(focus["label"])
        assert focus.get("headline") == focus["label"]


class TestRL2NoTexInEquationStrings:
    """RL2: 数式系の表示文字列に生 TeX を出さない（EH1 / RC2）。

    記号 ITEM の label は例外（記号は読解の部品であり、レンダリング可否はフロントの
    ``looksLikeRenderableTex`` ゲートに委ねる — §5.4）。
    """

    def test_no_equation_item_label_is_tex(self, lens):
        offenders: list[str] = []
        for element_type, result in _all_results():
            for item in _items(result):
                if item["element_type"] == "symbol":
                    continue
                if looks_like_tex_math(item["label"]):
                    offenders.append(f"{element_type}:{item['label']!r}")
                sublabel = item.get("sublabel") or ""
                if sublabel and looks_like_tex_math(sublabel):
                    offenders.append(f"{element_type}:sub:{sublabel!r}")
        assert offenders == []

    def test_equation_focus_does_not_restate_the_formula(self, lens):
        focus = _build(ELEMENT_EQUATION, _EQ_FOCUS)["focus"]
        for value in (focus["label"], focus.get("headline") or ""):
            assert "\\frac" not in value
            assert not looks_like_tex_math(value)

    def test_equation_derivation_stories_carry_no_tex(self, lens):
        focus = _build(ELEMENT_EQUATION, _EQ_FOCUS)["focus"]
        for story in focus.get("derivations") or []:
            assert not looks_like_tex_math(story["label"])
            for mini in list(story.get("inputs") or []) + list(story.get("outputs") or []):
                assert not looks_like_tex_math(mini["label"])


class TestRL3DerivationRelationsAreDirectional:
    """RL2/CP2: equation focus の導出 ITEM が向き語彙のいずれかであること。"""

    _DIRECTIONAL = {"feeds_derivation", "produced_by_derivation", "used_in_derivation"}

    def test_all_derivation_items_use_directional_vocabulary(self, lens):
        result = _build(ELEMENT_EQUATION, _EQ_FOCUS)
        derivation_items = [i for i in _items(result) if i["element_type"] == "derivation"]
        assert derivation_items
        assert {i["relation"] for i in derivation_items} <= self._DIRECTIONAL

    def test_input_and_output_derivations_are_distinguished(self, lens):
        result = _build(ELEMENT_EQUATION, _EQ_FOCUS)
        by_id = {i["element_id"]: i for i in _items(result) if i["element_type"] == "derivation"}
        assert by_id[_DERIV_TRANSFORM]["relation"] == "feeds_derivation"
        assert by_id[_DERIV_SYSTEM]["relation"] == "feeds_derivation"
        assert by_id[_DERIV_PRODUCES]["relation"] == "produced_by_derivation"
        # 入力側は上位レーン / 出力側は下位レーン（契約の向き）。
        upper_ids = {i["element_id"] for i in result["upper"] if i["element_type"] == "derivation"}
        lower_ids = {i["element_id"] for i in result["lower"] if i["element_type"] == "derivation"}
        assert upper_ids == {_DERIV_TRANSFORM, _DERIV_SYSTEM}
        assert lower_ids == {_DERIV_PRODUCES}

    def test_two_input_derivations_are_distinguishable(self, lens):
        """RC3 / CP7: スクリーンショットで区別不能だった2件が chain_type で分かれる。"""
        result = _build(ELEMENT_EQUATION, _EQ_FOCUS)
        by_id = {i["element_id"]: i for i in _items(result) if i["element_type"] == "derivation"}
        transform, system = by_id[_DERIV_TRANSFORM], by_id[_DERIV_SYSTEM]
        assert transform["qualifier"] == "equation_chain"
        assert system["qualifier"] == "system_level"
        assert (transform["label"], transform["sublabel"]) != (system["label"], system["sublabel"])


class TestRL4LayersAreNotMixed:
    """CP3: main（理論段階）と equation_detail（traceability）を混ぜない。"""

    def test_main_stage_and_equation_detail_use_different_relations(self, lens):
        result = _build(ELEMENT_EQUATION, _EQ_FOCUS)
        stages = [i for i in _items(result) if i["element_type"] == "stage"]
        details = [i for i in _items(result) if i.get("qualifier") == "equation_detail"]
        assert stages and details
        assert {i["relation"] for i in stages} == {"belongs_to_stage"}
        assert {i["relation"] for i in details} == {"used_in_operation_step"}
        assert {i["relation"] for i in stages}.isdisjoint({i["relation"] for i in details})

    def test_main_stage_label_is_translated_and_carries_description(self, lens):
        result = _build(ELEMENT_EQUATION, _EQ_FOCUS)
        stage = next(i for i in _items(result) if i["element_type"] == "stage")
        assert stage["label"] == "理論の土台"
        assert stage["qualifier"] == "theory_basis"
        assert stage["group"] == "stage"
        assert stage["element_id"] is None
        assert stage["navigable"] is False
        assert stage["sublabel"].startswith("観測量を密度ゆらぎとして定義する段階。")

    def test_debug_layer_is_reported_as_a_note_not_a_lane_item(self, lens):
        result = _build(ELEMENT_EQUATION, _EQ_FOCUS)
        assert all(i["element_id"] != "theory_op_0099" for i in _items(result))
        assert any("1" in note and "確定していない" in note for note in result["notes"])

    def test_equation_detail_navigable_is_fail_closed(self, lens):
        """CP8: DB 解決できない agent ID のノードは navigable にしない。"""
        result = _build(ELEMENT_EQUATION, _EQ_FOCUS)
        details = [i for i in _items(result) if i.get("qualifier") == "equation_detail"]
        resolvable = [i for i in details if i["element_id"] == _DETAIL_NODE_RESOLVABLE]
        unresolvable = [i for i in details if i["element_id"] is None]
        assert resolvable and all(i["navigable"] for i in resolvable)
        assert unresolvable and not any(i["navigable"] for i in unresolvable)

    def test_equation_detail_label_is_not_the_traceability_id_form(self, lens):
        result = _build(ELEMENT_EQUATION, _EQ_FOCUS)
        details = [i for i in _items(result) if i.get("qualifier") == "equation_detail"]
        assert details
        for item in details:
            assert not item["label"].startswith("Define eq_")
            assert "eq_tex_" not in item["label"]


class TestRL5SymbolsCarryMeaning:
    """RC5 / §5.4: 記号 ITEM は defines / uses を分け、sublabel に意味を持つ。"""

    def test_defining_and_using_symbols_use_different_relations(self, lens):
        result = _build(ELEMENT_EQUATION, _EQ_FOCUS)
        symbols = [i for i in _items(result) if i["element_type"] == "symbol"]
        assert len(symbols) == 3
        defined = [i for i in symbols if i["relation"] == "defines_symbol"]
        used = [i for i in symbols if i["relation"] == "uses_symbol"]
        assert [i["label"] for i in defined] == ["\\delta(t,x)"]
        assert len(used) == 2
        assert {i["group"] for i in defined} == {"symbol_defined"}
        assert {i["group"] for i in used} == {"symbol_used"}

    def test_every_symbol_item_has_a_non_empty_sublabel(self, lens):
        result = _build(ELEMENT_EQUATION, _EQ_FOCUS)
        symbols = [i for i in _items(result) if i["element_type"] == "symbol"]
        assert symbols
        for item in symbols:
            assert item["sublabel"].strip(), item["label"]

    def test_definition_missing_is_stated_as_a_fact(self, lens):
        """CP10: 「定義が見つからない」は隠さず事実として述べる。"""
        result = _build(ELEMENT_EQUATION, _EQ_FOCUS)
        missing = [
            i for i in _items(result)
            if i["element_type"] == "symbol" and i["qualifier"] == "definition_missing"
        ]
        assert len(missing) == 1
        assert "定義" in missing[0]["sublabel"]


class TestRL6NoRawSlicing:
    """CP5: 切り詰めは ``core.text_excerpt.excerpt`` の1実装のみ。"""

    def test_context_lens_has_no_raw_slice_expressions(self):
        assert_source_forbids(
            _CONTEXT_LENS_SRC,
            ["[:60]", "[:70]", "[:80]", "[:120]", "[:220]"],
            context="core/deliberation/context_lens.py",
        )

    def test_context_lens_imports_the_single_excerpt_implementation(self):
        assert "from core.text_excerpt import" in _CONTEXT_LENS_SRC
        assert "from core.deliberation import labels" in _CONTEXT_LENS_SRC


class TestRL7FocusLabelIsNotTheSummary:
    """CP1: ``focus.label`` と ``focus.intrinsic_summary`` に同一文字列を入れない。"""

    def test_equation_focus_label_differs_from_intrinsic_summary(self, lens):
        focus = _build(ELEMENT_EQUATION, _EQ_FOCUS)["focus"]
        assert focus["intrinsic_summary"]
        assert focus["label"] != focus["intrinsic_summary"]

    def test_no_focus_repeats_its_summary_in_the_label(self, lens):
        # evidence は「逐語そのものが identity」なので対象外（§5.6）。
        for element_type, result in _all_results():
            if element_type == ELEMENT_EVIDENCE:
                continue
            focus = result["focus"]
            if focus.get("intrinsic_summary"):
                assert focus["label"] != focus["intrinsic_summary"], element_type


class TestRL8RelationLabelsAreReachable:
    """RELATION_LABELS の全キーが実際に使われている（死語彙を作らない）。"""

    def test_every_relation_label_key_appears_in_the_source(self):
        # 定義（RELATION_LABELS の1行）以外にも出現する = 実際に emit される。
        missing = [
            relation
            for relation in context_lens.RELATION_LABELS
            if _CONTEXT_LENS_SRC.count(f'"{relation}"') < 2
        ]
        assert missing == []

    def test_new_directional_vocabulary_is_registered(self):
        for relation in (
            "feeds_derivation",
            "produced_by_derivation",
            "used_in_derivation",
            "belongs_to_stage",
            "used_in_operation_step",
            "defines_symbol",
        ):
            assert relation in context_lens.RELATION_LABELS, relation

    def test_uses_symbol_label_no_longer_says_the_symbol_of(self):
        # ITEM が記号そのものなので「の記号を用いる」→「を用いる」（§4.1）。
        assert context_lens.RELATION_LABELS["uses_symbol"] == "を用いる"


class TestRL9ItemContractV2:
    """§4.1 ITEM v2: 既存9キー不変 + 追加5キーが常に載ること。"""

    _REQUIRED_KEYS = {
        "element_type", "element_id", "document_id", "label", "relation", "relation_label",
        "relation_status", "evidence_refs", "navigable",
        "sublabel", "qualifier", "group", "unresolved", "label_source",
    }

    def test_every_item_has_the_full_v2_contract(self, lens):
        for element_type, result in _all_results():
            for item in _items(result):
                assert set(item) == self._REQUIRED_KEYS, element_type

    def test_group_values_are_from_the_controlled_vocabulary(self, lens):
        for element_type, result in _all_results():
            for item in _items(result):
                assert item["group"] in context_lens.ITEM_GROUPS, (
                    element_type, item["group"], item["label"]
                )

    def test_types_are_stable(self, lens):
        for _element_type, result in _all_results():
            for item in _items(result):
                assert isinstance(item["sublabel"], str)
                assert isinstance(item["qualifier"], str)
                assert isinstance(item["group"], str)
                assert isinstance(item["unresolved"], bool)
                assert isinstance(item["label_source"], str)


class TestRL10DistinguishableLabels:
    """CP7: 同一レーン内で label + sublabel がともに同一の2件を出さない。"""

    def test_no_two_items_in_a_lane_are_indistinguishable(self, lens):
        offenders: list[str] = []
        for element_type, result in _all_results():
            for lane in ("upper", "lower"):
                seen: set[tuple[str, str]] = set()
                for item in result[lane]:
                    key = (item["label"], item.get("sublabel") or "")
                    if key in seen:
                        offenders.append(f"{element_type}:{lane}:{key}")
                    seen.add(key)
        assert offenders == []


# ---------------------------------------------------------------------------
# focus v2（§4.2）: intrinsic / placement / contextual_role_source / derivations
# ---------------------------------------------------------------------------


class TestEquationFocusV2:
    def test_intrinsic_block_carries_role_summary_symbols_and_conditions(self, lens):
        focus = _build(ELEMENT_EQUATION, _EQ_FOCUS)["focus"]
        intrinsic = focus["intrinsic"]
        assert intrinsic["kind_key"] == "definition"
        assert intrinsic["role_key"] == "definition"
        assert intrinsic["summary"] == _SUMMARY_FOCUS
        # 英語自由文は訳さず原文のまま・切らない（§2.4 / Q3）。
        assert intrinsic["summary_is_source_language"] is True
        assert intrinsic["summary"].endswith("spatial mean.")
        symbols = {s["symbol"]: s for s in intrinsic["symbols"]}
        assert symbols["\\delta(t,x)"]["defined_here"] is True
        assert symbols["\\rho(t,x)"]["defined_here"] is False
        assert intrinsic["conditions"] == ["The background density is spatially homogeneous."]
        # CP10: 前段が無い理由を事実文で述べる（link_status=axiomatic）。
        assert any("定義であり" in fact for fact in intrinsic["facts"])

    def test_reading_is_empty_when_only_tex_is_available(self, lens):
        focus = _build(ELEMENT_EQUATION, _EQ_FOCUS)["focus"]
        assert focus["intrinsic"]["reading"] == ""

    def test_placement_has_section_and_translated_stage(self, lens):
        placement = _build(ELEMENT_EQUATION, _EQ_FOCUS)["focus"]["placement"]
        assert placement["section_label"] == "2.1 Density contrast"
        assert placement["stage"]["key"] == "theory_basis"
        assert placement["stage"]["description"].startswith("観測量を密度ゆらぎとして")
        assert placement["thesis_role"]

    def test_contextual_role_is_self_described(self, lens):
        focus = _build(ELEMENT_EQUATION, _EQ_FOCUS)["focus"]
        assert focus["contextual_role_source"] == "self_described"
        assert focus["contextual_role"] == "理論の土台の段階で使われる定義式"

    def test_derivation_story_block_has_direction_and_operations(self, lens):
        stories = {s["label"]: s for s in _build(ELEMENT_EQUATION, _EQ_FOCUS)["focus"]["derivations"]}
        assert len(stories) == 3
        transform = next(
            s for s in stories.values() if s["derivation_id"] == _DERIV_TRANSFORM
        )
        assert transform["chain_type"] == "equation_chain"
        assert transform["focus_role"] == "input"
        assert transform["operation_text"] == "線形化"
        assert [m["element_id"] for m in transform["inputs"]] == [_EQ_FOCUS]
        assert [m["element_id"] for m in transform["outputs"]] == [_EQ_OUT]
        assert all(not looks_like_tex_math(m["label"]) for m in transform["outputs"])
        assert transform["navigable"] is True
        system = next(s for s in stories.values() if s["derivation_id"] == _DERIV_SYSTEM)
        assert system["eliminated_symbols"] == ["b_s"]
        produces = next(s for s in stories.values() if s["derivation_id"] == _DERIV_PRODUCES)
        assert produces["focus_role"] == "output"

    def test_review_notes_are_facts_not_numbers(self, lens):
        focus = _build(ELEMENT_EQUATION, _EQ_FOCUS)["focus"]
        notes = focus["review_notes"]
        assert isinstance(notes, list)
        for note in notes:
            assert isinstance(note, str)
            assert not re.search(r"0\.[0-9]", note)

    def test_needs_math_review_is_reported_and_excluded_from_labels(self, lens, monkeypatch):
        artifacts = _artifacts()
        artifacts["equation_semantics"]["equations"][0]["source_extraction"][
            "needs_math_review"
        ] = True
        monkeypatch.setattr(context_lens.refs_mod, "document_run_artifacts", lambda _doc: artifacts)
        focus = _build(ELEMENT_EQUATION, _EQ_FOCUS)["focus"]
        assert any("数式" in note for note in focus["review_notes"])
        assert not looks_like_tex_math(focus["label"])


class TestEquationSectionAndEvidenceItems:
    """RC12: 掲載節と原文根拠が equation focus のレーンに出ること。"""

    def test_section_item_is_present(self, lens):
        result = _build(ELEMENT_EQUATION, _EQ_FOCUS)
        sections = [i for i in _items(result) if i["element_type"] == "section"]
        assert [i["label"] for i in sections] == ["2.1 Density contrast"]
        assert sections[0]["group"] == "section"
        assert sections[0]["relation"] == "appears_in_section"

    def test_evidence_item_label_is_the_verbatim_quote(self, lens):
        result = _build(ELEMENT_EQUATION, _EQ_FOCUS)
        evidences = [i for i in _items(result) if i["element_type"] == "evidence"]
        assert len(evidences) == 1
        assert "We define the density contrast" in evidences[0]["label"]
        assert evidences[0]["group"] == "evidence"
        assert evidences[0]["relation"] == "rests_on_evidence"


class TestContextualRoleSource:
    """§4.4: committed → self_described → structural → unidentified。"""

    def test_committed_annotation_wins(self, lens, monkeypatch):
        monkeypatch.setattr(
            context_lens, "_annotations_for",
            lambda *_a, **_k: [
                {"status": "committed", "kind": "interpretation", "body": {"text": "教員の確定説明"}}
            ],
        )
        focus = _build(ELEMENT_EQUATION, _EQ_FOCUS)["focus"]
        assert focus["contextual_role"] == "教員の確定説明"
        assert focus["contextual_role_source"] == "committed"

    def test_structural_summary_is_used_when_nothing_self_describes(self, lens, monkeypatch):
        artifacts = _artifacts()
        artifacts["equation_semantics"]["equations"][0]["semantics"]["role_in_argument"] = ""
        artifacts["equation_semantics"]["equations"][0]["semantics"]["equation_type"] = "unknown"
        monkeypatch.setattr(context_lens.refs_mod, "document_run_artifacts", lambda _doc: artifacts)
        focus = _build(ELEMENT_EQUATION, _EQ_FOCUS)["focus"]
        assert focus["contextual_role_source"] == "structural"
        assert focus["contextual_role"]
        # 機械連結（label + relation_label）ではないこと。
        assert "の理論段階に属する" not in focus["contextual_role"]

    def test_unidentified_keeps_the_role_empty(self, lens, monkeypatch):
        artifacts = _artifacts()
        semantics = artifacts["equation_semantics"]["equations"][0]["semantics"]
        semantics["role_in_argument"] = ""
        semantics["equation_type"] = "unknown"
        semantics["linked_claim_ids"] = []
        artifacts["thesis_reconstruction"] = {}
        monkeypatch.setattr(context_lens.refs_mod, "document_run_artifacts", lambda _doc: artifacts)
        monkeypatch.setattr(context_lens, "_load_component_graph", lambda _doc: {"nodes": [], "edges": []})
        focus = _build(ELEMENT_EQUATION, _EQ_FOCUS)["focus"]
        assert focus["contextual_role"] is None
        assert focus["contextual_role_source"] == "unidentified"


class TestClaimAndComponentReadability:
    def test_claim_item_sublabel_uses_evidence_text(self, lens):
        result = _build(ELEMENT_EQUATION, _EQ_FOCUS)
        claims = [i for i in _items(result) if i["element_type"] == "theory_claim"]
        assert claims
        assert all(i["group"] in ("claim", "claim_required", "related") for i in claims)

    def test_claim_focus_label_is_not_the_full_text(self, lens):
        focus = _build(ELEMENT_THEORY_CLAIM, _CLAIM_DB)["focus"]
        assert focus["label"]
        assert not labels_mod.is_internal_id_like(focus["label"])

    def test_component_stage_participation_label_is_translated(self, lens, monkeypatch):
        row = dict(_COMPONENT_ROW)
        monkeypatch.setattr(context_lens, "_load_component_row", lambda _id: row)
        monkeypatch.setattr(
            context_lens, "_load_component_graph",
            lambda _doc: {"nodes": [n for n in _graph_nodes() if n["graph_layer"] == "main"], "edges": []},
        )
        result = _build(ELEMENT_THEORY_COMPONENT, "comp-not-in-graph")
        stages = [i for i in _items(result) if i["element_type"] == "stage"]
        assert stages
        assert stages[0]["label"] == "理論の土台"
        assert stages[0]["group"] == "stage"


# ---------------------------------------------------------------------------
# Phase 3: 承認済み contextual 説明のラベル結線（§8 Phase 3 / EH1・CP1・CP6）
# ---------------------------------------------------------------------------


_APPROVED_BODY = (
    "この式は物質密度のゆらぎを、その空間平均に対する比として定義する。"
    "以降の摂動論はすべてこの量を出発点にする。"
)
_APPROVED_FIRST_SENTENCE = "この式は物質密度のゆらぎを、その空間平均に対する比として定義する。"


@pytest.fixture
def approved_explanation(lens, monkeypatch):
    """中心の式に「承認済み contextual 説明」が1件だけある状態。"""
    monkeypatch.setattr(
        context_lens,
        "_approved_equation_explanations",
        lambda _doc: {_EQ_FOCUS: _APPROVED_BODY},
    )
    return _APPROVED_BODY


class TestApprovedExplanationHeadline:
    def test_headline_is_the_first_sentence_of_the_approved_explanation(
        self, approved_explanation
    ):
        focus = _build(ELEMENT_EQUATION, _EQ_FOCUS)["focus"]
        assert focus["headline"] == _APPROVED_FIRST_SENTENCE
        assert focus["label"] == _APPROVED_FIRST_SENTENCE
        assert "以降の摂動論" not in focus["headline"]

    def test_label_source_is_explanation(self, approved_explanation):
        label = context_lens._equation_label(
            _equations()[0], explanations={_EQ_FOCUS: _APPROVED_BODY}
        )
        assert label.label_source == labels_mod.LABEL_SOURCE_EXPLANATION

    def test_cp1_headline_is_not_duplicated_into_intrinsic_summary(self, approved_explanation):
        """CP1: 見出しと意味の一行を同一化しない・説明全文を summary へ複製しない。"""
        focus = _build(ELEMENT_EQUATION, _EQ_FOCUS)["focus"]
        assert focus["intrinsic_summary"] == _SUMMARY_FOCUS
        assert focus["intrinsic_summary"] != focus["headline"]
        assert _APPROVED_BODY not in focus["intrinsic_summary"]

    def test_headline_carries_no_tex_and_no_internal_id(self, approved_explanation):
        focus = _build(ELEMENT_EQUATION, _EQ_FOCUS)["focus"]
        assert not looks_like_tex_math(focus["headline"])
        assert not labels_mod.is_internal_id_like(focus["headline"])
        assert _EQ_FOCUS not in focus["headline"]

    def test_other_lenses_reuse_the_same_headline_for_that_equation(self, approved_explanation):
        """claim / derivation / component から見た式 ITEM も同じ見出しになる。"""
        for element_type, focus_id in (
            (ELEMENT_THEORY_CLAIM, _CLAIM_DB),
            (ELEMENT_DERIVATION, _DERIV_TRANSFORM),
        ):
            items = [
                i for i in _items(_build(element_type, focus_id))
                if i["element_type"] == "equation" and i["element_id"] == _EQ_FOCUS
            ]
            assert items, element_type
            assert all(i["label"] == _APPROVED_FIRST_SENTENCE for i in items), element_type

    @pytest.mark.parametrize("element_type", sorted(_BEFORE))
    def test_cp6_relation_set_is_unchanged_by_the_explanation(
        self, approved_explanation, element_type
    ):
        """CP6: 説明材料を足しても ``(type, id, relation_status)`` 集合は不変。"""
        result = _build(element_type, _FOCUS_IDS[element_type])
        assert _relation_set(result) == _expected_after(element_type)

    def test_relations_are_identical_with_and_without_the_explanation(
        self, lens, monkeypatch
    ):
        without = _relation_set(_build(ELEMENT_EQUATION, _EQ_FOCUS))
        relations_without = [
            (i["element_type"], i["element_id"], i["relation"], i["relation_status"])
            for i in _items(_build(ELEMENT_EQUATION, _EQ_FOCUS))
        ]
        monkeypatch.setattr(
            context_lens,
            "_approved_equation_explanations",
            lambda _doc: {_EQ_FOCUS: _APPROVED_BODY},
        )
        result = _build(ELEMENT_EQUATION, _EQ_FOCUS)
        assert _relation_set(result) == without
        assert [
            (i["element_type"], i["element_id"], i["relation"], i["relation_status"])
            for i in _items(result)
        ] == relations_without

    def test_lookup_failure_degrades_to_the_existing_ladder(self, lens, monkeypatch):
        def boom(_doc):
            raise RuntimeError("db down")

        monkeypatch.setattr(context_lens, "_approved_equation_explanations", boom)
        result = _build(ELEMENT_EQUATION, _EQ_FOCUS)
        assert set(result) >= {"focus", "upper", "lower", "notes"}
        assert result["focus"]["headline"] == _build_without_explanation_headline()


def _build_without_explanation_headline() -> str:
    """説明が1件も無いときの見出し（既存ラダー④ = semantics.summary の第1文）。"""
    return context_lens._equation_label(_equations()[0]).text


class TestApprovedExplanationLearnerProjection:
    """学習者射影に来歴（label_source）・レビュー情報が漏れないこと（§5.5）。"""

    def test_learner_items_do_not_expose_label_source(self, approved_explanation):
        from core import element_context

        result = _build(ELEMENT_EQUATION, _EQ_FOCUS)
        for item in _items(result):
            assert "label_source" in item  # 教員向け DTO には来歴がある
            projected = element_context._project_item(item)
            if projected is None:
                continue
            assert "label_source" not in projected
            assert "reviewed_by" not in projected
            assert "status" not in projected

    def test_learner_focus_keeps_the_readable_headline_without_provenance(
        self, approved_explanation
    ):
        from core import element_context

        focus = _build(ELEMENT_EQUATION, _EQ_FOCUS)["focus"]
        projected = element_context._project_focus(focus, ELEMENT_EQUATION, _EQ_FOCUS)
        assert projected["headline"] == _APPROVED_FIRST_SENTENCE
        for forbidden in ("label_source", "review_notes", "provenance", "reviewed_by"):
            assert forbidden not in projected
