"""Tests for the equation reconstruction fidelity guards (issue #368)."""
from episteme_graph.agents.equation_semantics.fidelity import (
    apply_fidelity_guards,
    apply_prose_latex_guard,
    apply_symbol_loss_guard,
    is_prose_latex,
    validate_io_links,
)
from episteme_graph.agents.equation_semantics.schema import (
    DefinedSymbol,
    EquationConsistency,
    EquationRecord,
    EquationReconstruction,
    EquationSemantics,
    EquationSemanticsResult,
    EquationSourceExtraction,
)


# ------------------------------------------------------------------
# Builders
# ------------------------------------------------------------------

def _src(needs_review=True, image=None, review_reason=None):
    return EquationSourceExtraction(
        raw_text="r-q = a",
        latex=None,
        plain_text=None,
        source_location={"page": 1, "section_id": "sec_1", "block_id": "blk_e1", "bbox": []},
        extraction_source="pdf_text_layer",
        extraction_status="partial",
        needs_math_review=needs_review,
        review_reason=list(review_reason or []),
        source_image=image,
    )


def _rec(latex, status="reconstructed_from_neighbors", review_reason=None):
    return EquationReconstruction(
        latex=latex,
        plain_text=None,
        status=status,
        method=["text_context_fallback"],
        supporting_refs=["blk_0"],
        confidence=0.6,
        review_required=False,
        review_reason=list(review_reason or []),
    )


def _sem(inputs=None, outputs=None):
    return EquationSemantics(
        equation_type="relation",
        secondary_types=[],
        semantic_status="reconstruction_based",
        confidence=0.6,
        reason="test",
        defined_symbols=[DefinedSymbol("a", "defined", None)],
        used_symbols=["r", "q"],
        assumptions=[],
        input_equation_ids=list(inputs or []),
        output_equation_ids=list(outputs or []),
        linked_text_spans=[],
        source_evidence_ids=[],
        linked_claim_ids=[],
        summary="A relation.",
        review_flags=[],
    )


def _record(eq_id="eq_1", src=None, rec=None, sem=None, consistency=None):
    src = src if src is not None else _src()
    rec = rec if rec is not None else _rec("a = b")
    sem = sem if sem is not None else _sem()
    consistency = consistency if consistency is not None else EquationConsistency.derive(
        source_extraction=src, reconstruction=rec, label=None, semantics=sem,
    )
    return EquationRecord(
        equation_id=eq_id,
        document_id="doc",
        label=None,
        candidate_trace_ids=["eqcand_blk_e1"],
        source_extraction=src,
        reconstruction=rec,
        semantics=sem,
        confidence_policy=None,  # populated by guard / not needed for these asserts
        equation_consistency=consistency,
    )


# ------------------------------------------------------------------
# 1. prose-as-latex
# ------------------------------------------------------------------

def test_prose_latex_is_detected():
    latex = r"\xi_c \equiv \text{(specific convolution-symmetric combination of moments)}"
    assert is_prose_latex(latex) is True


def test_normal_equation_with_subject_to_text_is_not_prose():
    latex = r"\min_x f(x) \quad \text{subject to } g(x) \le 0"
    assert is_prose_latex(latex) is False


def test_real_equation_with_inline_annotation_is_not_prose():
    latex = r"E = m c^2 \text{ where m is the rest mass and c the speed of light}"
    assert is_prose_latex(latex) is False


def test_equation_without_text_block_is_not_prose():
    assert is_prose_latex(r"a^2 + b^2 = c^2") is False
    assert is_prose_latex("") is False
    assert is_prose_latex(None) is False


def test_apply_prose_latex_guard_blocks_downstream_use():
    rec = _rec(r"\xi_c \equiv \text{(a specific convolution symmetric combination)}")
    record = _record(rec=rec)
    flagged = apply_prose_latex_guard(record)
    assert flagged is True
    assert "latex_is_prose" in record.reconstruction.review_reason
    assert "latex_is_prose" in record.equation_consistency.review_reason
    assert record.equation_consistency.review_required is True
    assert record.confidence_policy.can_support_claim is False
    assert record.confidence_policy.can_be_used_in_derivation is False
    assert record.confidence_policy.can_be_rendered_as_final_formula is False


def test_apply_prose_latex_guard_leaves_real_equation_alone():
    record = _record(rec=_rec("a = b + c"))
    assert apply_prose_latex_guard(record) is False
    assert "latex_is_prose" not in record.reconstruction.review_reason


# ------------------------------------------------------------------
# 4. I/O link referential integrity
# ------------------------------------------------------------------

def test_io_links_membership_filters_nonequation_ids():
    eq1 = _record(eq_id="eq_1", sem=_sem(inputs=["eq_2", "blk_99", "eq_1"], outputs=["eq_3"]))
    eq2 = _record(eq_id="eq_2")
    issues = validate_io_links([eq1, eq2])
    # eq_2 kept; blk_99 (not a final id) and eq_1 (self) removed; eq_3 dangling removed
    assert eq1.semantics.input_equation_ids == ["eq_2"]
    assert eq1.semantics.output_equation_ids == []
    assert "nonequation_id_in_links" in eq1.semantics.review_flags
    assert len(issues) == 1
    assert "blk_99" in issues[0].message
    assert "eq_3" in issues[0].message


def test_io_links_all_valid_no_issue():
    eq1 = _record(eq_id="eq_1", sem=_sem(inputs=["eq_2"]))
    eq2 = _record(eq_id="eq_2")
    issues = validate_io_links([eq1, eq2])
    assert issues == []
    assert eq1.semantics.input_equation_ids == ["eq_2"]
    assert "nonequation_id_in_links" not in eq1.semantics.review_flags


# ------------------------------------------------------------------
# 5. unverifiable symbol loss
# ------------------------------------------------------------------

def test_symbol_loss_unverifiable_when_image_missing():
    src = _src(review_reason=["vision_ocr_unavailable:equation_image_unavailable"])
    consistency = EquationConsistency(
        raw_text_latex_match="mismatch",
        label_location_match="uncertain",
        symbol_overlap_score=0.1,
        source_span_quality="partial",
        review_required=True,
        review_reason=["raw_text_latex_symbol_mismatch"],
    )
    record = _record(src=src, consistency=consistency)
    assert apply_symbol_loss_guard(record) is True
    assert "symbol_loss_unverifiable" in record.equation_consistency.review_reason


def test_symbol_loss_not_flagged_when_image_available():
    consistency = EquationConsistency(
        raw_text_latex_match="mismatch",
        label_location_match="uncertain",
        symbol_overlap_score=0.1,
        source_span_quality="partial",
        review_required=True,
        review_reason=["raw_text_latex_symbol_mismatch"],
    )
    src = _src(image={"mime_type": "image/png", "data_base64": "x"})
    record = _record(src=src, consistency=consistency)
    assert apply_symbol_loss_guard(record) is False
    assert "symbol_loss_unverifiable" not in record.equation_consistency.review_reason


def test_symbol_loss_not_flagged_without_mismatch():
    consistency = EquationConsistency(
        raw_text_latex_match="match",
        label_location_match="match",
        symbol_overlap_score=0.9,
        source_span_quality="clean",
        review_required=False,
        review_reason=[],
    )
    record = _record(consistency=consistency)
    assert apply_symbol_loss_guard(record) is False


# ------------------------------------------------------------------
# orchestration
# ------------------------------------------------------------------

def test_apply_fidelity_guards_runs_all():
    prose = _record(
        eq_id="eq_1",
        rec=_rec(r"x \equiv \text{(some long paraphrased prose combination here)}"),
        sem=_sem(inputs=["blk_55"]),
    )
    result = EquationSemanticsResult(
        document_id="doc",
        cartridge_id=None,
        equation_candidates=[],
        equations=[prose],
    )
    issues = apply_fidelity_guards(result)
    assert "latex_is_prose" in prose.reconstruction.review_reason
    assert "nonequation_id_in_links" in prose.semantics.review_flags
    assert any(i.rule_id == "nonequation_id_in_links" for i in issues)
