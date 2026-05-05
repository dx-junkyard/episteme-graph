"""Tests for EquationSemantics schema helpers (Issue #245 refactor)."""
import json

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


def _make_source_extraction(block_id: str = "blk_e1", status: str = "complete") -> EquationSourceExtraction:
    return EquationSourceExtraction(
        raw_text="N = a + b (1.1)",
        latex=None,
        plain_text="N = a + b",
        source_location={"page": 1, "section_id": "sec_1", "block_id": block_id, "bbox": []},
        extraction_source="pdf_text_layer",
        extraction_status=status,
        needs_math_review=False,
        review_reason=[],
    )


def _make_semantics(eq_type: str = "definition") -> EquationSemantics:
    return EquationSemantics(
        equation_type=eq_type,
        secondary_types=[],
        semantic_status="source_backed",
        confidence=0.9,
        reason="clear definition",
        defined_symbols=[DefinedSymbol("N", "defined", "where N is defined as")],
        used_symbols=["a", "b"],
        assumptions=[],
        input_equation_ids=[],
        output_equation_ids=[],
        linked_text_spans=[],
        source_evidence_ids=[],
        linked_claim_ids=[],
        summary="Definition of prefactor N.",
        review_flags=[],
    )


def _make_record(eq_id: str = "eq_1_1") -> EquationRecord:
    src = _make_source_extraction()
    rec = EquationReconstruction.make_none()
    sem = _make_semantics()
    cp = EquationConfidencePolicy.derive(src, rec, sem)
    return EquationRecord(
        equation_id=eq_id,
        document_id="doc_test",
        label="1.1",
        candidate_trace_ids=["eqcand_blk_e1_abc123"],
        source_extraction=src,
        reconstruction=rec,
        semantics=sem,
        confidence_policy=cp,
    )


def _make_candidate(candidate_id: str = "eqcand_blk_e1_abc123") -> EquationCandidate:
    return EquationCandidate(
        candidate_id=candidate_id,
        document_id="doc_test",
        source_location={"page": 1, "section_id": "sec_1", "block_id": "blk_e1", "bbox": []},
        raw_text="N = a + b (1.1)",
        matched_label="1.1",
        detection_method=["document_structure_equation_block", "equation_number_pattern"],
        candidate_score=1.0,
        extraction_status="complete",
        acceptance_status="accepted",
        accepted_equation_id="eq_1_1",
        needs_math_review=False,
        review_reason=[],
    )


# ------------------------------------------------------------------
# EquationRecord serialization
# ------------------------------------------------------------------

def test_to_json_round_trip():
    record = _make_record()
    result = EquationSemanticsResult("doc_test", None, [_make_candidate()], [record])
    restored = EquationSemanticsResult.from_dict(json.loads(result.to_json()))
    eq = restored.equations[0]
    assert eq.equation_id == "eq_1_1"
    assert eq.semantics.equation_type == "definition"
    assert eq.semantics.defined_symbols[0].symbol == "N"
    assert eq.candidate_trace_ids == ["eqcand_blk_e1_abc123"]


def test_candidate_round_trip():
    candidate = _make_candidate()
    result = EquationSemanticsResult("doc_test", None, [candidate], [])
    restored = EquationSemanticsResult.from_dict(json.loads(result.to_json()))
    c = restored.equation_candidates[0]
    assert c.candidate_id == "eqcand_blk_e1_abc123"
    assert c.extraction_status == "complete"
    assert c.acceptance_status == "accepted"
    assert c.matched_label == "1.1"


# ------------------------------------------------------------------
# EquationConfidencePolicy derivation
# ------------------------------------------------------------------

def test_confidence_policy_complete_source_backed():
    src = _make_source_extraction(status="complete")
    rec = EquationReconstruction.make_none()
    sem = _make_semantics()
    cp = EquationConfidencePolicy.derive(src, rec, sem)
    assert cp.can_support_claim is True
    assert cp.can_be_used_in_derivation is True
    assert cp.must_not_treat_as_source_extracted is False
    assert cp.display_requires_note is False


def test_confidence_policy_reconstruction_based():
    src = _make_source_extraction(status="missing")
    rec = EquationReconstruction(
        latex=None,
        plain_text="N approx a",
        status="inferred_from_context",
        method=["nearby_text"],
        supporting_refs=["blk_0"],
        confidence=0.6,
        review_required=True,
    )
    sem = EquationSemantics(
        equation_type="relation",
        secondary_types=[],
        semantic_status="reconstruction_based",
        confidence=0.6,
        reason="reconstructed",
        defined_symbols=[],
        used_symbols=[],
        assumptions=[],
        input_equation_ids=[],
        output_equation_ids=[],
        linked_text_spans=[],
        source_evidence_ids=[],
        linked_claim_ids=[],
        summary="Approximate relation.",
        review_flags=["reconstruction_only"],
    )
    cp = EquationConfidencePolicy.derive(src, rec, sem)
    assert cp.can_support_claim is False
    assert cp.must_not_treat_as_source_extracted is True
    assert cp.display_requires_note is True


def test_reconstruction_none_does_not_affect_policy():
    src = _make_source_extraction(status="complete")
    rec = EquationReconstruction.make_none()
    sem = _make_semantics()
    cp = EquationConfidencePolicy.derive(src, rec, sem)
    assert cp.must_not_treat_as_source_extracted is False
