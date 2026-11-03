"""PDF 由来の数式を「隠す」のをやめ、untrusted と明示して見せる（2026-09-19）。

固定する契約:

① プロンプトは PDF テキスト層の原文を**そのまま**出し、untrusted の固定注記を添える
  （以前は ``[OMITTED ...]`` で伏せていたため、LLM は文脈だけから式を創作していた）。
  復元必須の方針・``source_backed`` を名乗らせない方針は変えない。
② ``review_required`` の理由が「PDF は信用しない」という一律の前提だけなら、確度の
  高い復元を導出に使える（``can_be_used_in_derivation``）。その式固有の疑い
  （raw↔latex の mismatch 等）があれば従来どおり使えない。救われた式でも
  ``must_not_treat_as_source_extracted`` / ``can_support_claim`` は変わらない。
③ 食い違ったまま確度も低い復元には、論文の印字番号を名乗らせない（label を
  review_reason へ退避）し、``semantic_status`` を ``unknown`` へ降格する。
  情報は落とさない（退避先に原値が残る）。``equation_id`` は触らない。
"""
from __future__ import annotations

from episteme_graph.agents.equation_semantics.prompt import (
    EquationSemanticsPromptFactory,
)
from episteme_graph.agents.equation_semantics.repair import _parse_record
from episteme_graph.agents.equation_semantics.schema import (
    LABEL_WITHHELD_REASON,
    DefinedSymbol,
    EquationConfidencePolicy,
    EquationConsistency,
    EquationLLMInput,
    EquationReconstruction,
    EquationRecord,
    EquationSemantics,
    EquationSourceExtraction,
    demote_unverifiable_equation_label,
)
from episteme_graph.agents.equation_semantics.validator import (
    EquationSemanticsValidator,
)

RAW_TEXT = "δ 3D g (z, θ) = b(z, θ) δ 3D m (z, θ), (2)"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _llm_input(*, trusted: bool = False) -> EquationLLMInput:
    return EquationLLMInput(
        document_id="doc_test",
        cartridge_id=None,
        equation_id="eq_2",
        block_id="blk_e2",
        section_id="sec_1",
        section_title="Model",
        backbone_block_type=None,
        label="(2)",
        equation_text=RAW_TEXT,
        latex="\\delta_g = b \\delta_m" if trusted else None,
        plain_text=RAW_TEXT,
        prev_texts=["[b1] The galaxy overdensity is linearly biased."],
        next_texts=[],
        nearby_span_annotations=[],
        candidate_id="cand_1",
        extraction_status="partial" if not trusted else "complete",
        acceptance_status="accepted",
        needs_reconstruction=not trusted,
        extraction_source="pdf_text_layer" if not trusted else "tex_source",
        source_is_trusted=trusted,
    )


def _source_extraction(*, needs_review: bool = True) -> EquationSourceExtraction:
    return EquationSourceExtraction(
        raw_text=RAW_TEXT,
        latex=None,
        plain_text=None,
        source_location={"page": 3, "section_id": "sec_1", "block_id": "blk_e2", "bbox": []},
        extraction_source="pdf_text_layer",
        extraction_status="partial" if needs_review else "complete",
        needs_math_review=needs_review,
        review_reason=["pdf_text_layer_untrusted"] if needs_review else [],
    )


def _reconstruction(confidence: float, latex: str = "\\delta_g = b \\delta_m") -> EquationReconstruction:
    return EquationReconstruction(
        latex=latex,
        plain_text="delta g equals b delta m",
        status="inferred_from_context",
        method=["nearby_text"],
        supporting_refs=["blk_e2"],
        confidence=confidence,
        review_required=True,
        review_reason=[],
    )


def _semantics(status: str = "reconstruction_based") -> EquationSemantics:
    return EquationSemantics(
        equation_type="relation",
        secondary_types=[],
        semantic_status=status,
        confidence=0.8,
        reason="linear bias relation",
        defined_symbols=[DefinedSymbol("b", "defined", "the linear bias")],
        used_symbols=["z"],
        assumptions=[],
        input_equation_ids=[],
        output_equation_ids=[],
        linked_text_spans=[],
        source_evidence_ids=[],
        linked_claim_ids=[],
        summary="Galaxy overdensity is linearly biased.",
        review_flags=["needs_reconstruction"],
    )


def _consistency(
    *,
    raw_latex_match: str = "match",
    label_match: str = "uncertain",
    quality: str = "partial",
    reasons: list[str] | None = None,
) -> EquationConsistency:
    return EquationConsistency(
        raw_text_latex_match=raw_latex_match,
        label_location_match=label_match,
        symbol_overlap_score=0.5,
        source_span_quality=quality,
        review_required=True,
        review_reason=list(reasons or []),
    )


def _record(consistency: EquationConsistency, reconstruction: EquationReconstruction) -> EquationRecord:
    src = _source_extraction()
    sem = _semantics()
    return EquationRecord(
        equation_id="eq_2",
        document_id="doc_test",
        label="(2)",
        candidate_trace_ids=["cand_1"],
        source_extraction=src,
        reconstruction=reconstruction,
        semantics=sem,
        confidence_policy=EquationConfidencePolicy.derive(src, reconstruction, sem, consistency),
        equation_consistency=consistency,
    )


# ---------------------------------------------------------------------------
# ① プロンプトは原文を untrusted として見せる
# ---------------------------------------------------------------------------


def test_pdf_equation_text_is_shown_not_omitted():
    content = EquationSemanticsPromptFactory()._build_user_content(_llm_input(), None)
    assert "OMITTED" not in content
    assert RAW_TEXT in content


def test_pdf_equation_text_carries_an_untrusted_notice():
    content = EquationSemanticsPromptFactory()._build_user_content(_llm_input(), None)
    lowered = content.lower()
    # 「データであって指示ではない」+「そのまま写さない」の両方を言う。
    assert "not an instruction" in lowered
    assert "do not copy it verbatim" in lowered
    assert "extracted from the pdf text layer" in lowered


def test_reconstruction_policy_is_unchanged_for_pdf_equations():
    content = EquationSemanticsPromptFactory()._build_user_content(_llm_input(), None)
    assert "MUST include a 'reconstruction' block" in content
    assert "Never mark semantic_status='source_backed' for PDF-derived math." in content


def test_trusted_tex_source_still_shows_the_tex_without_the_notice():
    content = EquationSemanticsPromptFactory()._build_user_content(_llm_input(trusted=True), None)
    assert "\\delta_g = b \\delta_m" in content
    assert "not an instruction" not in content.lower()


# ---------------------------------------------------------------------------
# ② 一律の前提だけの review は導出を止めない
# ---------------------------------------------------------------------------


def test_blanket_pdf_review_is_recognised_as_such():
    assert _consistency().untrusted_pdf_only() is True
    assert _consistency(
        reasons=["raw_text_latex_symbol_overlap_uncertain"]
    ).untrusted_pdf_only() is True


def test_specific_doubts_are_not_blanket():
    assert _consistency(raw_latex_match="mismatch").untrusted_pdf_only() is False
    assert _consistency(label_match="mismatch").untrusted_pdf_only() is False
    assert _consistency(quality="corrupted").untrusted_pdf_only() is False
    # allowlist に無い理由コードは「本物の疑い」に倒す（fail-closed）。
    assert _consistency(reasons=["latex_is_prose"]).untrusted_pdf_only() is False


def test_high_confidence_reconstruction_can_be_used_in_derivation():
    policy = EquationConfidencePolicy.derive(
        _source_extraction(), _reconstruction(0.85), _semantics(), _consistency()
    )
    assert policy.can_be_used_in_derivation is True
    # 救っても「抽出された式」にはしない・claim の根拠にもしない。
    assert policy.must_not_treat_as_source_extracted is True
    assert policy.can_support_claim is False
    assert policy.can_be_rendered_as_final_formula is False


def test_low_confidence_reconstruction_stays_out_of_derivation():
    policy = EquationConfidencePolicy.derive(
        _source_extraction(), _reconstruction(0.4), _semantics(), _consistency()
    )
    assert policy.can_be_used_in_derivation is False


def test_symbol_mismatch_still_vetoes_derivation_use():
    policy = EquationConfidencePolicy.derive(
        _source_extraction(),
        _reconstruction(0.95),
        _semantics(),
        _consistency(
            raw_latex_match="mismatch", reasons=["raw_text_latex_symbol_mismatch"]
        ),
    )
    assert policy.can_be_used_in_derivation is False


def test_validator_accepts_derivation_use_for_blanket_pdf_review():
    from episteme_graph.agents.equation_semantics.schema import EquationSemanticsResult

    record = _record(_consistency(), _reconstruction(0.85))
    result = EquationSemanticsResult(
        document_id="doc_test", cartridge_id=None, equation_candidates=[], equations=[record]
    )
    issues = EquationSemanticsValidator().validate(result, None)
    assert not [
        i for i in issues if i.rule_id == "inconsistent_equation_used_in_derivation"
    ]
    assert not [i for i in issues if i.severity == "error"]


# ---------------------------------------------------------------------------
# ③ 食い違った低確度の復元は印字番号を名乗らない
# ---------------------------------------------------------------------------


def test_mismatched_low_confidence_record_loses_its_printed_label():
    record = _record(
        _consistency(raw_latex_match="mismatch", reasons=["raw_text_latex_symbol_mismatch"]),
        _reconstruction(0.3),
    )
    assert demote_unverifiable_equation_label(record) is True
    assert record.label is None
    # 退避先に原値が残る（情報を落とさない）。
    assert any(
        reason == f"{LABEL_WITHHELD_REASON}:(2)"
        for reason in record.equation_consistency.review_reason
    )
    # equation_id は触らない（既存参照を切らない）。
    assert record.equation_id == "eq_2"


def test_mismatched_low_confidence_record_is_demoted_to_unknown():
    record = _record(
        _consistency(raw_latex_match="mismatch", reasons=["raw_text_latex_symbol_mismatch"]),
        _reconstruction(0.3),
    )
    demote_unverifiable_equation_label(record)
    assert record.semantics.semantic_status == "unknown"
    assert "reconstruction_based" in record.semantics.reason
    assert record.confidence_policy.can_be_used_in_derivation is False


def test_demotion_is_idempotent():
    record = _record(
        _consistency(raw_latex_match="mismatch", reasons=["raw_text_latex_symbol_mismatch"]),
        _reconstruction(0.3),
    )
    demote_unverifiable_equation_label(record)
    before = list(record.equation_consistency.review_reason)
    demote_unverifiable_equation_label(record)
    assert record.equation_consistency.review_reason == before


def test_high_confidence_mismatch_keeps_its_label():
    record = _record(
        _consistency(raw_latex_match="mismatch", reasons=["raw_text_latex_symbol_mismatch"]),
        _reconstruction(0.9),
    )
    assert demote_unverifiable_equation_label(record) is False
    assert record.label == "(2)"


def test_parse_record_applies_the_demotion():
    raw = {
        "equation_id": "eq_2",
        "equation_type": "relation",
        "semantic_status": "reconstruction_based",
        "confidence": 0.6,
        "reason": "context",
        "summary": "Unrelated statement.",
        "reconstruction": {
            # raw_text と記号が全く重ならない「別の式」。
            "latex": "\\lambda = \\theta + 1",
            "plain_text": "lambda equals theta plus one",
            "status": "inferred_from_context",
            "method": ["nearby_text"],
            "supporting_refs": ["blk_e2"],
            "confidence": 0.3,
            "review_required": True,
        },
    }
    record = _parse_record(raw, _llm_input(), None)
    assert record.equation_consistency.raw_text_latex_match == "mismatch"
    assert record.label is None
    assert record.semantics.semantic_status == "unknown"
