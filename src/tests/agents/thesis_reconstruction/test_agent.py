"""Integration tests for ThesisReconstructionAgent with mocked LLM."""
from __future__ import annotations

from unittest.mock import patch

from episteme_graph.agents.claim_qualification.schema import ClaimQualificationResult, QualifiedSpanRecord
from episteme_graph.agents.equation_semantics.schema import (
    DefinedSymbol,
    EquationRecord,
    EquationSourceExtraction,
    EquationReconstruction,
    EquationSemantics,
    EquationConfidencePolicy,
    EquationSemanticsResult,
)
from episteme_graph.agents.paper_skeleton.schema import LogicalBlock, PaperSkeletonResult, SKELETON_VERSION
from episteme_graph.agents.thesis_reconstruction.agent import ThesisReconstructionAgent
from episteme_graph.agents.thesis_reconstruction.schema import ThesisReconstructionResult


def _skeleton():
    return PaperSkeletonResult(
        document_id="doc_test",
        skeleton_version=SKELETON_VERSION,
        cartridge_id=None,
        paper_goal={"text": "Build a predictive sum rule.", "evidence_block_ids": ["b1"], "reason": "", "confidence": 0.8},
        central_question={"text": "How are decay-rate ratios related?", "evidence_block_ids": ["b1"], "reason": "", "confidence": 0.8},
        headline_claim={"text": "The paper derives a sum rule relating R Lambda c, RD, and RD*.", "evidence_block_ids": ["b2"], "reason": "", "confidence": 0.8},
        supporting_subclaims=[],
        logical_blocks=[
            LogicalBlock("logic_1", "derivation", "Derivation", ["sec_1"], ["b2"], "Derives the core sum rule.", "reason", 0.9),
            LogicalBlock("logic_2", "uncertainty", "Uncertainty", ["sec_2"], ["b3"], "Form factors dominate uncertainties.", "reason", 0.8),
        ],
        excluded_regions=[{"region_type": "meta", "block_ids": ["m1"], "reason": "section transition"}],
        review_notes=[],
        confidence=0.84,
    )


def _claim(span_id, block_id, text, claim_type, tier="paper_core"):
    return QualifiedSpanRecord(
        span_id=span_id,
        block_id=block_id,
        section_id="sec_1",
        text=text,
        role_labels=[claim_type],
        qualification={
            "status": "accepted",
            "claim_tier": tier,
            "claim_type_candidate": claim_type,
            "granularity": "good",
            "evidence_adequacy": "sufficient",
            "reviewability": "good",
        },
        edit_suggestions={},
        reason="mock claim",
        confidence=0.9,
    )


def _qualified():
    return ClaimQualificationResult(
        document_id="doc_test",
        cartridge_id=None,
        qualified_spans=[
            _claim("s1", "b2", "A sum rule relates R Lambda c, RD, and RD*.", "relation"),
            _claim("s2", "b3", "Form factors dominate theoretical uncertainties.", "uncertainty", "paper_supporting"),
            _claim("s3", "b4", "Improved form factor precision is required.", "method_choice", "paper_supporting"),
        ],
        rejected_spans=[
            {
                "span_id": "s0",
                "block_id": "b0",
                "role_labels": ["prior_work"],
                "qualification": {"status": "rejected", "claim_tier": "prior_work"},
                "reason": "prior work context",
            }
        ],
        deferred_spans=[],
        summary_stats={"accepted": 3, "rejected": 1},
    )


def _make_equation_record():
    src = EquationSourceExtraction(
        raw_text="R Lambda c = RD + RD*",
        latex=None,
        plain_text="R Lambda c = RD + RD*",
        source_location={"page": 1, "section_id": "sec_1", "block_id": "e1", "bbox": None},
        extraction_source="pdf_text_layer",
        extraction_status="complete",
    )
    rec = EquationReconstruction.make_none()
    sem = EquationSemantics(
        equation_type="relation",
        secondary_types=["result"],
        semantic_status="source_backed",
        confidence=0.9,
        reason="sum rule",
        defined_symbols=[DefinedSymbol("R Lambda c", "used", None)],
        used_symbols=[],
        assumptions=[],
        input_equation_ids=["eq_1_2"],
        output_equation_ids=[],
        linked_text_spans=[],
        source_evidence_ids=[],
        linked_claim_ids=[],
        summary="Core sum rule relation between R Lambda c, RD, and RD*.",
        review_flags=[],
    )
    policy = EquationConfidencePolicy.derive(src, rec, sem)
    return EquationRecord(
        equation_id="eq_3_14",
        document_id="doc_test",
        label="3.14",
        candidate_trace_ids=[],
        source_extraction=src,
        reconstruction=rec,
        semantics=sem,
        confidence_policy=policy,
    )


def _equations():
    return EquationSemanticsResult("doc_test", None, [], [_make_equation_record()])


def _valid_response():
    return {
        "document_id": "doc_test",
        "thesis_version": "v1",
        "cartridge_id": None,
        "central_thesis": {
            "text": "The paper derives a corrected and uncertainty-qualified sum rule relating R Lambda c, RD, and RD*.",
            "claim_ids": ["claim:b2:s1", "claim:b3:s2"],
            "equation_ids": ["eq_3_14"],
            "evidence_block_ids": ["b2", "b3", "e1"],
            "reason": "The accepted relation, uncertainty claim, and main equation form the argument center.",
            "confidence": 0.9,
        },
        "alternative_theses": [],
        "support_structure": {
            "direct_supports": [
                {
                    "support_id": "sup_1",
                    "text": "The derivation core supplies the sum-rule relation.",
                    "claim_ids": ["claim:b2:s1"],
                    "equation_ids": ["eq_3_14"],
                    "support_type": "derivation_support",
                    "reason": "The main equation is a relation/result.",
                    "confidence": 0.88,
                }
            ],
            "assumptions": [],
            "derivation_core": [
                {"text": "Core derivation of the sum rule.", "claim_ids": ["claim:b2:s1"], "equation_ids": ["eq_3_14"], "reason": "derivation", "confidence": 0.86}
            ],
            "correction_sources": [],
            "uncertainty_sources": [
                {"text": "Form factors dominate uncertainties.", "claim_ids": ["claim:b3:s2"], "equation_ids": [], "reason": "uncertainty claim", "confidence": 0.84}
            ],
            "diagnostic_consequences": [],
            "future_requirements": [
                {"text": "Improved form factor precision remains the bottleneck.", "claim_ids": ["claim:b4:s3"], "equation_ids": [], "reason": "future requirement", "confidence": 0.8}
            ],
        },
        "excluded_from_core": [
            {"category": "prior_work", "claim_ids": ["claim:b0:s0"], "reason": "prior work is context"}
        ],
        "thesis_graph_hints": [
            {"from": "central_thesis", "to": "support:direct_supports:0", "relation": "supported_by"},
            {"from": "central_thesis", "to": "support:uncertainty_sources:0", "relation": "qualified_by"},
        ],
        "review_notes": [],
        "confidence": 0.87,
    }


def test_run_returns_thesis_structure():
    agent = ThesisReconstructionAgent()
    with patch.object(agent._llm_client, "generate", return_value=_valid_response()):
        result = agent.run(_skeleton(), _qualified(), _equations())
    assert isinstance(result, ThesisReconstructionResult)
    assert "sum rule" in result.central_thesis["text"]
    assert result.support_structure["direct_supports"][0]["support_type"] == "derivation_support"
    assert result.support_structure["future_requirements"]
    assert result.excluded_from_core[0]["category"] == "prior_work"


def test_repair_called_on_empty_thesis():
    agent = ThesisReconstructionAgent()
    bad = {**_valid_response(), "central_thesis": {**_valid_response()["central_thesis"], "text": ""}}
    with patch.object(agent._llm_client, "generate", side_effect=[bad, _valid_response()]) as mocked:
        result = agent.run(_skeleton(), _qualified(), _equations())
    assert mocked.call_count >= 2
    assert result.central_thesis["text"]


def test_llm_failure_returns_fallback():
    agent = ThesisReconstructionAgent()
    with patch.object(agent._llm_client, "generate", side_effect=RuntimeError("LLM error")):
        result = agent.run(_skeleton(), _qualified(), _equations())
    assert result.confidence == 0.0
    assert result.validation_issues[0].rule_id == "thesis_reconstruction_failed"
