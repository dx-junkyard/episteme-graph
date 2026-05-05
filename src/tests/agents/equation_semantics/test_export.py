"""Tests for EquationSemanticsResult.to_equations_export() (Issue #245 refactor)."""
from __future__ import annotations

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


def _make_record(eq_id: str = "eq_3_14") -> EquationRecord:
    src = EquationSourceExtraction(
        raw_text="LaTeX placeholder",
        latex=r"\Gamma_{tot} = \sum_i \Gamma_i",
        plain_text="Gamma_tot = sum_i Gamma_i",
        source_location={
            "page": 3,
            "section_id": "doc_test:sec_3_1",
            "block_id": "blk_eq_3_14",
            "bbox": [],
        },
        extraction_source="pdf_text_layer",
        extraction_status="complete",
        needs_math_review=False,
        review_reason=[],
    )
    rec = EquationReconstruction.make_none()
    sem = EquationSemantics(
        equation_type="result",
        secondary_types=["relation"],
        semantic_status="source_backed",
        confidence=0.91,
        reason="",
        defined_symbols=[
            DefinedSymbol(symbol="A", definition_status="defined"),
            DefinedSymbol(symbol="B", definition_status="used"),
        ],
        used_symbols=["B"],
        assumptions=["heavy_quark_limit"],
        input_equation_ids=["eq_3_5", "eq_3_6"],
        output_equation_ids=[],
        linked_text_spans=[],
        source_evidence_ids=[],
        linked_claim_ids=[],
        summary="normalized_total_decay_rate_sum_rule",
        review_flags=[],
    )
    cp = EquationConfidencePolicy.derive(src, rec, sem)
    return EquationRecord(
        equation_id=eq_id,
        document_id="doc_test",
        label="3.14",
        candidate_trace_ids=["eqcand_blk_eq_3_14_abc"],
        source_extraction=src,
        reconstruction=rec,
        semantics=sem,
        confidence_policy=cp,
    )


def _make_candidate() -> EquationCandidate:
    return EquationCandidate(
        candidate_id="eqcand_blk_eq_3_14_abc",
        document_id="doc_test",
        source_location={"page": 3, "section_id": "doc_test:sec_3_1", "block_id": "blk_eq_3_14", "bbox": []},
        raw_text="LaTeX placeholder",
        matched_label="3.14",
        detection_method=["document_structure_equation_block"],
        candidate_score=1.0,
        extraction_status="complete",
        acceptance_status="accepted",
        accepted_equation_id="eq_3_14",
        needs_math_review=False,
        review_reason=[],
    )


def test_export_shape_matches_spec():
    result = EquationSemanticsResult(
        document_id="doc_test",
        cartridge_id="particle_physics",
        equation_candidates=[_make_candidate()],
        equations=[_make_record()],
    )
    exported = result.to_equations_export(
        evidence_index={"blk_eq_3_14": ["ev_0001"]},
        claim_index={"eq_3_14": ["claim_xxx"]},
    )

    assert len(exported) == 1
    e = exported[0]
    assert e["equation_id"] == "eq_3_14"
    assert e["document_id"] == "doc_test"
    assert e["label"] == "3.14"
    assert e["latex"].startswith(r"\Gamma")
    assert e["equation_role"] == {"primary": "result", "secondary": ["relation"]}
    assert e["semantic_kind"] == "normalized_total_decay_rate_sum_rule"
    # "B" is listed in both used_symbols and defined_symbols (used) — export shows used_symbols from sem
    assert "B" in e["used_symbols"]
    assert "A" in e["defined_symbols"]
    assert "A" in e["introduced_symbols"]
    assert e["local_assumptions"] == ["heavy_quark_limit"]
    assert e["derivation_links"] == {"from_equations": ["eq_3_5", "eq_3_6"], "to_equations": []}
    assert e["linked_claim_ids"] == ["claim_xxx"]
    assert e["source_evidence_ids"] == ["ev_0001"]
    assert e["section_id"] == "doc_test:sec_3_1"
    assert "confidence_policy" in e
    assert e["confidence_policy"]["can_support_claim"] is True


def test_export_handles_missing_indexes():
    result = EquationSemanticsResult(
        document_id="doc_test",
        cartridge_id=None,
        equation_candidates=[_make_candidate()],
        equations=[_make_record("eq_1")],
    )
    exported = result.to_equations_export()
    assert exported[0]["linked_claim_ids"] == []
    assert exported[0]["source_evidence_ids"] == []


def test_export_reconstruction_fallback_for_latex():
    """reconstruction の latex を source_extraction が null の場合に使う。"""
    src = EquationSourceExtraction(
        raw_text="N approx a",
        latex=None,  # no latex in source
        plain_text=None,
        source_location={"page": 1, "section_id": "sec_1", "block_id": "blk_rec", "bbox": []},
        extraction_source="pdf_text_layer",
        extraction_status="partial",
        needs_math_review=True,
        review_reason=["partial_extraction"],
    )
    rec = EquationReconstruction(
        latex=r"N \approx a",
        plain_text="N approx a",
        status="inferred_from_context",
        method=["nearby_text"],
        supporting_refs=["blk_0"],
        confidence=0.7,
        review_required=True,
    )
    sem = EquationSemantics(
        equation_type="approximation",
        secondary_types=[],
        semantic_status="context_inferred",
        confidence=0.7,
        reason="from context",
        defined_symbols=[],
        used_symbols=["N", "a"],
        assumptions=[],
        input_equation_ids=[],
        output_equation_ids=[],
        linked_text_spans=[],
        source_evidence_ids=[],
        linked_claim_ids=[],
        summary="Approximate relation for N.",
        review_flags=["needs_reconstruction"],
    )
    cp = EquationConfidencePolicy.derive(src, rec, sem)
    record = EquationRecord(
        equation_id="eq_rec_1",
        document_id="doc_test",
        label="1.1",
        candidate_trace_ids=["eqcand_blk_rec"],
        source_extraction=src,
        reconstruction=rec,
        semantics=sem,
        confidence_policy=cp,
    )
    result = EquationSemanticsResult("doc_test", None, [], [record])
    exported = result.to_equations_export()
    e = exported[0]
    # latex falls back to reconstruction
    assert e["latex"] == r"N \approx a"
    assert e["plain_text"] == "N approx a"
    # confidence_policy reflects reconstruction
    assert e["confidence_policy"]["display_requires_note"] is True
