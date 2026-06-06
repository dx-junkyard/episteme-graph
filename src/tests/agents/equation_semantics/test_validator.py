"""Tests for EquationSemanticsValidator (Issue #245 refactor)."""
from episteme_graph.agents.equation_semantics.schema import (
    CartridgeContext,
    DefinedSymbol,
    EquationCandidate,
    EquationConfidencePolicy,
    EquationRecord,
    EquationReconstruction,
    EquationSemantics,
    EquationSemanticsResult,
    EquationSourceExtraction,
)
from episteme_graph.agents.equation_semantics.validator import EquationSemanticsValidator

VALIDATOR = EquationSemanticsValidator()


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _src(block_id: str = "blk_e1", status: str = "complete", needs_review: bool = False) -> EquationSourceExtraction:
    return EquationSourceExtraction(
        raw_text="N = a + b (1.1)",
        latex=None,
        plain_text="N = a + b",
        source_location={"page": 1, "section_id": "sec_1", "block_id": block_id, "bbox": []},
        extraction_source="pdf_text_layer",
        extraction_status=status,
        needs_math_review=needs_review,
        review_reason=[],
    )


def _rec_none() -> EquationReconstruction:
    return EquationReconstruction.make_none()


def _rec_active(status: str = "inferred_from_context") -> EquationReconstruction:
    return EquationReconstruction(
        latex=None,
        plain_text="N approx a",
        status=status,
        method=["nearby_text"],
        supporting_refs=["blk_0"],
        confidence=0.6,
        review_required=True,
    )


def _sem(eq_type: str = "definition", sem_status: str = "source_backed", confidence: float = 0.9) -> EquationSemantics:
    return EquationSemantics(
        equation_type=eq_type,
        secondary_types=[],
        semantic_status=sem_status,
        confidence=confidence,
        reason="test",
        defined_symbols=[DefinedSymbol("N", "defined", None)],
        used_symbols=["a", "b"],
        assumptions=[],
        input_equation_ids=[],
        output_equation_ids=[],
        linked_text_spans=[],
        source_evidence_ids=[],
        linked_claim_ids=[],
        summary="Definition of N.",
        review_flags=[],
    )


def _record(
    eq_id: str = "eq_1_1",
    src: EquationSourceExtraction | None = None,
    rec: EquationReconstruction | None = None,
    sem: EquationSemantics | None = None,
    trace_ids: list | None = None,
    label: str | None = "1.1",
) -> EquationRecord:
    src = src or _src()
    rec = rec or _rec_none()
    sem = sem or _sem()
    cp = EquationConfidencePolicy.derive(src, rec, sem)
    return EquationRecord(
        equation_id=eq_id,
        document_id="doc_test",
        label=label,
        candidate_trace_ids=trace_ids if trace_ids is not None else ["eqcand_blk_e1"],
        source_extraction=src,
        reconstruction=rec,
        semantics=sem,
        confidence_policy=cp,
    )


def _candidate(cid: str = "eqcand_blk_e1", ext_status: str = "complete", acc_status: str = "accepted") -> EquationCandidate:
    return EquationCandidate(
        candidate_id=cid,
        document_id="doc_test",
        source_location={"page": 1, "section_id": "sec_1", "block_id": "blk_e1", "bbox": []},
        raw_text="N = a + b (1.1)",
        matched_label="1.1",
        detection_method=["document_structure_equation_block"],
        candidate_score=1.0,
        extraction_status=ext_status,
        acceptance_status=acc_status,
        needs_math_review=False,
        review_reason=[],
    )


def _result(records: list | None = None, candidates: list | None = None) -> EquationSemanticsResult:
    return EquationSemanticsResult(
        document_id="doc_test",
        cartridge_id=None,
        equation_candidates=candidates or [_candidate()],
        equations=records or [_record()],
    )


# ------------------------------------------------------------------
# Valid result
# ------------------------------------------------------------------

def test_valid_result_has_no_errors():
    issues = VALIDATOR.validate(_result())
    assert not [i for i in issues if i.severity == "error"]


# ------------------------------------------------------------------
# Candidate hard errors
# ------------------------------------------------------------------

def test_fragment_only_accepted_is_error():
    c = _candidate(ext_status="fragment_only", acc_status="accepted")
    issues = VALIDATOR.validate(EquationSemanticsResult("doc", None, [c], []))
    assert any(i.rule_id == "fragment_only_accepted" for i in issues)


def test_label_only_accepted_is_error():
    c = _candidate(ext_status="label_only", acc_status="accepted")
    issues = VALIDATOR.validate(EquationSemanticsResult("doc", None, [c], []))
    assert any(i.rule_id == "label_only_accepted" for i in issues)


def test_invalid_extraction_status_is_error():
    c = _candidate(ext_status="bad_status")
    issues = VALIDATOR.validate(EquationSemanticsResult("doc", None, [c], []))
    assert any(i.rule_id == "invalid_extraction_status" for i in issues)


def test_invalid_acceptance_status_is_error():
    c = _candidate(acc_status="dunno")
    issues = VALIDATOR.validate(EquationSemanticsResult("doc", None, [c], []))
    assert any(i.rule_id == "invalid_acceptance_status" for i in issues)


# ------------------------------------------------------------------
# EquationRecord hard errors
# ------------------------------------------------------------------

def test_missing_candidate_trace_is_error():
    issues = VALIDATOR.validate(_result(records=[_record(trace_ids=[])]))
    assert any(i.rule_id == "missing_candidate_trace" for i in issues)


def test_missing_source_block_id_is_error():
    src = EquationSourceExtraction(
        raw_text="N = a + b",
        latex=None,
        plain_text="N = a + b",
        source_location={"page": 1, "section_id": "sec_1", "block_id": "", "bbox": []},
        extraction_source="pdf_text_layer",
        extraction_status="complete",
        needs_math_review=False,
        review_reason=[],
    )
    issues = VALIDATOR.validate(_result(records=[_record(src=src)]))
    assert any(i.rule_id == "missing_source_block_id" for i in issues)


def test_invalid_equation_type_is_error():
    sem = _sem(eq_type="bad_type")
    issues = VALIDATOR.validate(_result(records=[_record(sem=sem)]))
    assert any(i.rule_id == "invalid_equation_type" for i in issues)


def test_invalid_semantic_status_is_error():
    sem = _sem(sem_status="bad_status")
    issues = VALIDATOR.validate(_result(records=[_record(sem=sem)]))
    assert any(i.rule_id == "invalid_semantic_status" for i in issues)


def test_confidence_out_of_range_is_error():
    sem = _sem(confidence=1.5)
    issues = VALIDATOR.validate(_result(records=[_record(sem=sem)]))
    assert any(i.rule_id == "confidence_out_of_range" for i in issues)


def test_empty_summary_is_error():
    sem = _sem()
    sem.summary = ""
    issues = VALIDATOR.validate(_result(records=[_record(sem=sem)]))
    assert any(i.rule_id == "empty_summary" for i in issues)


def test_reconstruction_only_supporting_claim_is_error():
    src = _src(status="missing")
    rec = _rec_active()
    sem = _sem(sem_status="reconstruction_based")
    # manually override policy to create invalid state
    cp = EquationConfidencePolicy(
        can_support_claim=True,
        can_be_used_in_derivation=False,
        can_be_rendered_as_final_formula=False,
        allowed_downstream_use="display_with_warning",
        can_be_displayed_in_course=True,
        display_requires_note=True,
        must_not_treat_as_source_extracted=True,
    )
    record = EquationRecord(
        equation_id="eq_bad",
        document_id="doc",
        label="1.1",
        candidate_trace_ids=["cand_1"],
        source_extraction=src,
        reconstruction=rec,
        semantics=sem,
        confidence_policy=cp,
    )
    issues = VALIDATOR.validate(_result(records=[record]))
    assert any(i.rule_id == "reconstruction_only_supporting_claim" for i in issues)


# ------------------------------------------------------------------
# Reconstruction hard errors
# ------------------------------------------------------------------

def test_reconstruction_missing_method_is_error():
    rec = EquationReconstruction(
        latex=None,
        plain_text=None,
        status="inferred_from_context",
        method=[],  # empty!
        supporting_refs=["blk_0"],
        confidence=0.6,
        review_required=True,
    )
    issues = VALIDATOR.validate(_result(records=[_record(rec=rec)]))
    assert any(i.rule_id == "reconstruction_missing_method" for i in issues)


def test_reconstruction_missing_supporting_refs_is_error():
    rec = EquationReconstruction(
        latex=None,
        plain_text=None,
        status="inferred_from_context",
        method=["nearby_text"],
        supporting_refs=[],  # empty!
        confidence=0.6,
        review_required=True,
    )
    issues = VALIDATOR.validate(_result(records=[_record(rec=rec)]))
    assert any(i.rule_id == "reconstruction_missing_supporting_refs" for i in issues)


def test_reconstruction_none_status_has_no_errors():
    rec = _rec_none()
    issues = VALIDATOR.validate(_result(records=[_record(rec=rec)]))
    recon_errors = [i for i in issues if i.rule_id.startswith("reconstruction_") and i.severity == "error"]
    assert not recon_errors


# ------------------------------------------------------------------
# Consistency warnings
# ------------------------------------------------------------------

def test_definition_without_symbols_is_warning():
    sem = _sem()
    sem.defined_symbols = []
    issues = VALIDATOR.validate(_result(records=[_record(sem=sem)]))
    assert any(i.rule_id == "definition_without_symbols" for i in issues)


def test_transformation_without_input_equations_is_warning():
    sem = _sem(eq_type="transformation")
    sem.input_equation_ids = []
    issues = VALIDATOR.validate(_result(records=[_record(sem=sem)]))
    assert any(i.rule_id == "transformation_without_input_equations" for i in issues)


def test_missing_label_requires_review_flag():
    issues = VALIDATOR.validate(_result(records=[_record(label=None)]))
    assert any(i.rule_id == "missing_label_without_review_flag" for i in issues)


def test_low_confidence_requires_review_flag():
    sem = _sem(confidence=0.3)
    issues = VALIDATOR.validate(_result(records=[_record(sem=sem)]))
    assert any(i.rule_id == "low_confidence_without_review_flag" for i in issues)


# ------------------------------------------------------------------
# Cartridge warning
# ------------------------------------------------------------------

def test_cartridge_notation_without_symbols_warning():
    cartridge = CartridgeContext(
        cartridge_id="test",
        ontology={},
        validation_rules={},
        aliases={"R Lambda c": ["RΛc"]},
    )
    sem = _sem(eq_type="relation")
    sem.defined_symbols = []
    sem.used_symbols = []
    src = EquationSourceExtraction(
        raw_text="RΛc = RD + RD*",
        latex=None,
        plain_text="R Lambda c = RD + RD*",
        source_location={"page": 1, "section_id": "sec_1", "block_id": "blk_e1", "bbox": []},
        extraction_source="pdf_text_layer",
        extraction_status="complete",
        needs_math_review=False,
        review_reason=[],
    )
    sem.summary = "Relation for R Lambda c."
    issues = VALIDATOR.validate(_result(records=[_record(src=src, sem=sem)]), cartridge)
    assert any(i.rule_id == "cartridge_notation_without_symbols" for i in issues)
