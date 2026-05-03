"""Tests for DSLLinkingInputBuilder."""
from episteme_graph.agents.claim_qualification.schema import ClaimQualificationResult, QualifiedSpanRecord
from episteme_graph.agents.dsl_linking.input_builder import DSLLinkingInputBuilder
from episteme_graph.agents.equation_semantics.schema import (
    DefinedSymbol,
    DerivationLinks,
    EquationRolePrediction,
    EquationSemanticsRecord,
    EquationSemanticsResult,
)
from episteme_graph.agents.thesis_reconstruction.schema import ThesisReconstructionResult, THESIS_VERSION

BUILDER = DSLLinkingInputBuilder()


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
        "doc",
        None,
        [
            _claim("s1", "b1", "Heavy quark limit is assumed.", "assumption"),
            _claim("s2", "b2", "The sum rule relates R Lambda c and RD.", "relation"),
        ],
        [],
        [],
        {"accepted": 2},
    )


def _equations():
    record = EquationSemanticsRecord(
        equation_id="eq_3_14",
        block_id="e1",
        section_id="sec_1",
        section_title="Derivation",
        label="3.14",
        text="R Lambda c = RD + RD*",
        latex=None,
        plain_text="R Lambda c = RD + RD*",
        equation_role=EquationRolePrediction("equation_relation", [], 0.9, "relation"),
        defined_symbols=[DefinedSymbol("R Lambda c", "used", None)],
        local_assumptions=[],
        derivation_links=DerivationLinks(["eq_1_2"], [], []),
        summary="Sum rule relation.",
        review_flags=[],
    )
    return EquationSemanticsResult("doc", None, [record])


def _thesis():
    return ThesisReconstructionResult(
        document_id="doc",
        thesis_version=THESIS_VERSION,
        cartridge_id=None,
        central_thesis={"text": "A sum rule is derived.", "claim_ids": ["claim:b2:s2"], "equation_ids": ["eq_3_14"], "evidence_block_ids": ["b2"], "reason": "", "confidence": 0.9},
        alternative_theses=[],
        support_structure={
            "direct_supports": [{"text": "Derivation support.", "claim_ids": ["claim:b2:s2"], "equation_ids": ["eq_3_14"], "support_type": "derivation_support", "confidence": 0.8}],
            "assumptions": [{"text": "Heavy quark limit.", "claim_ids": ["claim:b1:s1"], "equation_ids": [], "confidence": 0.8}],
        },
        excluded_from_core=[{"category": "prior_work", "claim_ids": ["claim:b0:s0"], "reason": "background"}],
        thesis_graph_hints=[],
        review_notes=[],
        confidence=0.9,
    )


def test_build_packages_claim_equation_and_thesis_materials():
    llm_input = BUILDER.build(_qualified(), equations=_equations(), thesis=_thesis())
    assert llm_input.document_id == "doc"
    assert llm_input.accepted_claims[0]["claim_id"] == "claim:b1:s1"
    assert llm_input.equations[0]["equation_id"] == "eq_3_14"
    assert any(n["thesis_ref"] == "central_thesis" for n in llm_input.thesis_nodes)
    assert llm_input.excluded_from_core[0]["category"] == "prior_work"


def test_limits_claims():
    llm_input = BUILDER.build(_qualified(), config={"max_claims": 1})
    assert len(llm_input.accepted_claims) == 1
