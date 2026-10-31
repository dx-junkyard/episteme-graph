"""再構成の予測（predict）の下地の是正（IK-0483 / IK-0502 / IK-0503・ペルソナ通し受講）。

- IK-0502 主張の行が持つ式の参照（``equation.equation_ids`` / ``equation_stable_keys``）を
         knowledge_equations で解決し、PDF からそのまま抽出できた関係型の式にだけ
         ``relation_type`` を付ける（復元した式は答えキーにしない）
- IK-0503 ``defined_symbols`` の dict（``{"symbol": ...}``）を記号名として扱い、
         ``str(dict)`` を問いに出さない
- IK-0483 predict を下地にしない理由を語彙で返し、item 生成の監査 metadata と報告に残す

DB・実 LLM には触れない。
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from core.reconstruction import worker  # noqa: E402
from core.reconstruction.claim_context import (  # noqa: E402
    RELATION_WITHHELD_FEW_SYMBOLS,
    RELATION_WITHHELD_NOT_RELATIONAL,
    RELATION_WITHHELD_NOT_SOURCE_EXTRACTED,
    enrich_claim,
    pick_equation,
    symbol_names,
)
from core.reconstruction.input_builder import build_user_content  # noqa: E402
from core.reconstruction.item_builder import (  # noqa: E402
    RESTATE_CLAIM_TYPE_NOT_RELATIONAL,
    RESTATE_NO_CONCEPTS_OR_EQUATION,
    elicit_mode_decision,
    preferred_elicit_mode,
    symbol_probe,
)
from tests.guardrail_helpers import extract_function_source  # noqa: E402

_WORKER_SRC = (BACKEND / "core" / "reconstruction" / "worker.py").read_text(encoding="utf-8")

_TRUSTED = {"can_support_claim": True, "must_not_treat_as_source_extracted": False}
_RECONSTRUCTED = {"can_support_claim": False, "must_not_treat_as_source_extracted": True}


def _eq(agent_id="eq_3", *, equation_type="relation", policy=None, **extra):
    row = {
        "agent_equation_id": agent_id,
        "stable_key": "k1:" + agent_id,
        "label": "(3)",
        "latex": "O = a g",
        "equation_type": equation_type,
        "defined_symbols": [{"symbol": "O", "definition_status": "defined"}],
        "used_symbols": ["a", "g"],
        "linked_claim_ids": [],
        "confidence_policy": dict(_TRUSTED if policy is None else policy),
    }
    row.update(extra)
    return row


def _claim(**extra):
    claim = {
        "id": "uuid-1",
        "agent_claim_id": "claim_obj_1",
        "claim_type": "result",
        "text": "The observable grows linearly with the coupling.",
        "normalized_text": "The observable grows linearly with the coupling.",
        "concepts": [],
        "equation": {"equation_ids": ["eq_3"], "equation_stable_keys": ["k1:eq_3"]},
        "source_scope": {"block_id": "b1", "legacy_ids": ["claim_span_7"]},
    }
    claim.update(extra)
    return claim


class TestClaimOwnEquationRefs:
    def test_claim_equation_ids_resolve_to_knowledge_equations(self):
        picked = pick_equation(_claim(), [_eq(), _eq("eq_9", latex="x = y")])
        assert picked["latex"] == "O = a g"
        assert picked["source"] == "claim_equation_ids"
        assert picked["relation_type"] == "relation"
        assert picked["symbols"] == ["O", "a", "g"]

    def test_stable_key_alone_is_enough(self):
        claim = _claim(equation={"equation_stable_keys": ["k1:eq_3"]})
        assert pick_equation(claim, [_eq()])["label"] == "(3)"

    def test_parent_equation_refs_when_the_atomic_child_has_none(self):
        child = _claim(equation={}, source_scope={})
        parent = {"id": "p", "equation": {"equation_ids": ["eq_3"]}}
        assert pick_equation(child, [_eq()], parent)["latex"] == "O = a g"

    def test_linked_claim_ids_can_point_at_legacy_ids(self):
        claim = _claim(equation={})
        eq = _eq(linked_claim_ids=["claim_span_7"])
        assert pick_equation(claim, [eq])["source"] == "knowledge_equations"

    def test_enrich_replaces_the_id_only_payload_with_the_resolved_equation(self):
        out = enrich_claim(_claim(), equations=[_eq()])
        assert out["equation"]["latex"] == "O = a g"
        assert "equation" in out["enriched_fields"]


class TestRelationTypeIsOnlyForSourceExtractedRelations:
    def test_reconstructed_equation_is_not_an_answer_key(self):
        picked = pick_equation(_claim(), [_eq(policy=_RECONSTRUCTED)])
        assert picked["relation_type"] == ""
        assert picked["relation_withheld"] == RELATION_WITHHELD_NOT_SOURCE_EXTRACTED

    def test_missing_policy_is_treated_as_untrusted(self):
        picked = pick_equation(_claim(), [_eq(policy={})])
        assert picked["relation_type"] == ""
        assert picked["relation_withheld"] == RELATION_WITHHELD_NOT_SOURCE_EXTRACTED

    def test_definition_equation_is_not_relational(self):
        picked = pick_equation(_claim(), [_eq(equation_type="definition")])
        assert picked["relation_withheld"] == RELATION_WITHHELD_NOT_RELATIONAL

    def test_a_single_symbol_is_not_a_relation(self):
        picked = pick_equation(_claim(), [_eq(defined_symbols=[{"symbol": "O"}], used_symbols=[])])
        assert picked["relation_withheld"] == RELATION_WITHHELD_FEW_SYMBOLS


class TestElicitModeDecision:
    def test_source_extracted_relation_makes_predict_eligible(self):
        claim = enrich_claim(_claim(), equations=[_eq()])
        assert elicit_mode_decision(claim) == ("predict", None)
        assert preferred_elicit_mode(claim) == "predict"

    def test_reconstructed_equation_keeps_restate_with_its_reason(self):
        claim = enrich_claim(_claim(), equations=[_eq(policy=_RECONSTRUCTED)])
        assert elicit_mode_decision(claim) == ("restate", RELATION_WITHHELD_NOT_SOURCE_EXTRACTED)

    def test_non_relational_claim_type(self):
        assert elicit_mode_decision(_claim(claim_type="definition")) == (
            "restate", RESTATE_CLAIM_TYPE_NOT_RELATIONAL,
        )

    def test_no_equation_and_no_concepts(self):
        assert elicit_mode_decision(_claim(equation={})) == ("restate", RESTATE_NO_CONCEPTS_OR_EQUATION)

    def test_two_concepts_still_suffice(self):
        claim = _claim(equation={}, concepts=[{"name": "coupling"}, {"name": "observable"}])
        assert elicit_mode_decision(claim) == ("predict", None)


class TestSymbolNames:
    def test_dict_and_string_forms(self):
        assert symbol_names([{"symbol": "x"}, "y", {"name": "z"}, {"symbol": ""}, "x"]) == ["x", "y", "z"]

    def test_symbol_probe_does_not_render_a_dict(self):
        claim = {"equation": {"defined_symbols": [{"symbol": "O", "definition_status": "defined"}]}}
        probe = symbol_probe(claim)
        assert probe["target_symbol"] == "O"
        assert "{" not in probe["prompt"]

    def test_llm_input_lists_symbol_names(self):
        content = build_user_content(enrich_claim(_claim(), equations=[_eq()]))
        assert '"O"' in content
        assert "definition_status" not in content


class TestRestateReasonIsRecorded:
    def test_metadata_carries_the_reason_only_for_restate(self):
        restate = worker._authoring_event_metadata(_claim(claim_type="definition"), "restate")
        assert restate == {
            "author": "llm", "mode": "restate", "restate_reason": RESTATE_CLAIM_TYPE_NOT_RELATIONAL,
        }
        predicted = enrich_claim(_claim(), equations=[_eq()])
        assert worker._authoring_event_metadata(predicted, "predict") == {"author": "llm", "mode": "predict"}

    def test_author_downgrade_is_distinguished(self):
        predicted = enrich_claim(_claim(), equations=[_eq()])
        assert worker._restate_reason(predicted, "restate") == worker.RESTATE_AUTHOR_DOWNGRADED

    def test_enrichment_reads_type_and_policy_for_all_live_equations(self):
        body = extract_function_source(_WORKER_SRC, "_enrich_claims")
        assert "agent_payload->'confidence_policy'" in body
        assert "equation_type" in body and "stable_key" in body
        assert "jsonb_array_length(linked_claim_ids) > 0" not in body
