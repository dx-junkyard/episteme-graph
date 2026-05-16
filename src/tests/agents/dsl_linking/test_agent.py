"""Integration tests for DSLLinkingAgent with mocked LLM."""
from __future__ import annotations

from unittest.mock import patch

from episteme_graph.agents.claim_qualification.schema import ClaimQualificationResult, QualifiedSpanRecord
from episteme_graph.agents.dsl_linking.agent import DSLLinkingAgent
from episteme_graph.agents.dsl_linking.schema import DSLLinkingResult
from episteme_graph.agents.equation_semantics.schema import (
    DefinedSymbol,
    EquationCandidate,
    EquationConfidencePolicy,
    EquationRecord,
    EquationReconstruction,
    EquationSemantics,
    EquationSemanticsResult,
    EquationSourceExtraction,
)
from episteme_graph.agents.thesis_reconstruction.schema import ThesisReconstructionResult, THESIS_VERSION


def _claim(span_id, block_id, text, claim_type):
    return QualifiedSpanRecord(
        span_id=span_id,
        block_id=block_id,
        section_id="sec_1",
        text=text,
        role_labels=[claim_type],
        qualification={
            "status": "accepted",
            "claim_tier": "paper_core",
            "claim_type_candidate": claim_type,
            "granularity": "good",
            "evidence_adequacy": "sufficient",
            "reviewability": "good",
        },
        edit_suggestions={},
        reason="mock",
        confidence=0.9,
    )


def _qualified():
    return ClaimQualificationResult(
        "doc_test",
        None,
        [
            _claim("s1", "b1", "Heavy quark limit is assumed.", "assumption"),
            _claim("s2", "b2", "The relation integrates to the total-rate sum rule.", "derivation_step"),
            _claim("s3", "b3", "Form factors dominate the uncertainty.", "uncertainty"),
            _claim("s4", "b4", "The sum rule checks experimental consistency.", "diagnostic_claim"),
        ],
        [{"span_id": "s0", "block_id": "b0", "qualification": {"claim_tier": "prior_work"}, "reason": "background"}],
        [],
        {"accepted": 4},
    )


def _make_eq_record(eq_id: str, label: str, block_id: str, section_id: str, from_eqs: list[str] | None = None) -> EquationRecord:
    src = EquationSourceExtraction(
        raw_text="R Lambda c = RD + RD*",
        latex=None,
        plain_text="R Lambda c = RD + RD*",
        source_location={
            "page": 1,
            "section_id": section_id,
            "block_id": block_id,
            "bbox": [],
        },
        extraction_source="pdf_text_layer",
        extraction_status="complete",
        needs_math_review=False,
        review_reason=[],
    )
    rec = EquationReconstruction.make_none()
    sem = EquationSemantics(
        equation_type="equation_relation",
        secondary_types=["equation_result"],
        semantic_status="source_backed",
        confidence=0.9,
        reason="relation",
        defined_symbols=[DefinedSymbol("R Lambda c", "used", None)],
        used_symbols=[],
        assumptions=[],
        input_equation_ids=list(from_eqs or []),
        output_equation_ids=[],
        linked_text_spans=[],
        source_evidence_ids=[],
        linked_claim_ids=[],
        summary="Total-rate sum rule relation.",
        review_flags=[],
    )
    cp = EquationConfidencePolicy.derive(src, rec, sem)
    return EquationRecord(
        equation_id=eq_id,
        document_id="doc_test",
        label=label,
        candidate_trace_ids=[f"eqcand_{block_id}"],
        source_extraction=src,
        reconstruction=rec,
        semantics=sem,
        confidence_policy=cp,
    )


def _equations():
    return EquationSemanticsResult(
        document_id="doc_test",
        cartridge_id=None,
        equation_candidates=[],
        equations=[
            _make_eq_record("eq_3_14", "3.14", "e1", "sec_1", from_eqs=["eq_1_2"]),
        ],
    )


def _thesis():
    return ThesisReconstructionResult(
        "doc_test",
        THESIS_VERSION,
        None,
        {"text": "A sum rule relates R Lambda c, RD, and RD*.", "claim_ids": ["claim:b2:s2"], "equation_ids": ["eq_3_14"], "evidence_block_ids": ["b2"], "reason": "", "confidence": 0.9},
        [],
        {
            "direct_supports": [{"text": "Derivation support.", "claim_ids": ["claim:b2:s2"], "equation_ids": ["eq_3_14"], "support_type": "derivation_support", "confidence": 0.8}],
            "assumptions": [{"text": "Heavy quark limit.", "claim_ids": ["claim:b1:s1"], "equation_ids": [], "confidence": 0.8}],
            "uncertainty_sources": [{"text": "Form factors dominate uncertainty.", "claim_ids": ["claim:b3:s3"], "equation_ids": [], "confidence": 0.8}],
        },
        [{"category": "prior_work", "claim_ids": ["claim:b0:s0"], "reason": "background"}],
        [],
        [],
        0.9,
    )


def _valid_response():
    return {
        "document_id": "doc_test",
        "dsl_version": "v1",
        "nodes": [
            {"node_id": "n1", "node_type": "Approximation", "node_value": "heavy_quark_limit", "source_kind": "claim", "source_refs": {"claim_ids": ["claim:b1:s1"], "equation_ids": [], "thesis_refs": ["support:assumptions:0"]}, "reason": "assumption node", "confidence": 0.9},
            {"node_id": "n2", "node_type": "Relation", "node_value": "total_rate_sum_rule", "source_kind": "mixed", "source_refs": {"claim_ids": ["claim:b2:s2"], "equation_ids": ["eq_3_14"], "thesis_refs": ["support:direct_supports:0"]}, "reason": "relation node", "confidence": 0.92},
            {"node_id": "n3", "node_type": "UncertaintySource", "node_value": "form_factor_uncertainty", "source_kind": "claim", "source_refs": {"claim_ids": ["claim:b3:s3"], "equation_ids": [], "thesis_refs": ["support:uncertainty_sources:0"]}, "reason": "uncertainty node", "confidence": 0.88},
            {"node_id": "n4", "node_type": "Diagnostic", "node_value": "experimental_consistency_check", "source_kind": "claim", "source_refs": {"claim_ids": ["claim:b4:s4"], "equation_ids": [], "thesis_refs": []}, "reason": "diagnostic node", "confidence": 0.84},
        ],
        "edges": [
            {"edge_id": "e1", "from_node_id": "n1", "to_node_id": "n2", "core_predicate": "REQUIRES", "domain_verb": "assumes", "polarity": "+", "evidence_refs": {"claim_ids": ["claim:b1:s1"], "equation_ids": ["eq_3_14"], "thesis_refs": ["support:assumptions:0"]}, "reason": "assumption required by relation", "confidence": 0.9},
            {"edge_id": "e2", "from_node_id": "n2", "to_node_id": "n4", "core_predicate": "MEASURES", "domain_verb": "checks_consistency_of", "polarity": "+", "evidence_refs": {"claim_ids": ["claim:b4:s4"], "equation_ids": ["eq_3_14"], "thesis_refs": []}, "reason": "sum rule used diagnostically", "confidence": 0.82},
            {"edge_id": "e3", "from_node_id": "n3", "to_node_id": "n2", "core_predicate": "CORRELATES", "domain_verb": "contributes_to_uncertainty", "polarity": "?", "evidence_refs": {"claim_ids": ["claim:b3:s3"], "equation_ids": [], "thesis_refs": ["support:uncertainty_sources:0"]}, "reason": "uncertainty qualifies relation", "confidence": 0.78},
        ],
        "graph_hints": [{"hint_type": "component_seed", "node_ids": ["n1", "n2"], "reason": "shared derivation component"}],
        "review_notes": [],
        "confidence": 0.86,
    }


def test_run_returns_dsl_graph():
    agent = DSLLinkingAgent()
    with patch.object(agent._llm_client, "generate", return_value=_valid_response()):
        result = agent.run(_qualified(), equations=_equations(), thesis=_thesis())
    assert isinstance(result, DSLLinkingResult)
    assert len(result.nodes) == 4
    assert any(e.core_predicate == "REQUIRES" and e.domain_verb == "assumes" for e in result.edges)
    assert any(e.core_predicate == "CORRELATES" for e in result.edges)
    assert any(e.core_predicate == "MEASURES" for e in result.edges)


def test_repair_called_on_invalid_core_predicate():
    agent = DSLLinkingAgent()
    bad = _valid_response()
    bad["edges"] = [{**bad["edges"][0], "core_predicate": "BAD"}]
    with patch.object(agent._llm_client, "generate", side_effect=[bad, _valid_response()]) as mocked:
        result = agent.run(_qualified(), equations=_equations(), thesis=_thesis())
    assert mocked.call_count >= 2
    assert result.edges[0].core_predicate == "REQUIRES"


def test_llm_failure_returns_fallback():
    agent = DSLLinkingAgent()
    with patch.object(agent._llm_client, "generate", side_effect=RuntimeError("LLM error")):
        result = agent.run(_qualified(), equations=_equations(), thesis=_thesis())
    assert result.confidence == 0.0
    assert result.validation_issues[0].rule_id == "dsl_linking_failed"
