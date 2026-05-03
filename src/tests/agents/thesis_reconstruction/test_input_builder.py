"""Tests for ThesisReconstructionInputBuilder."""
from episteme_graph.agents.claim_qualification.schema import ClaimQualificationResult, QualifiedSpanRecord
from episteme_graph.agents.equation_semantics.schema import (
    DefinedSymbol,
    DerivationLinks,
    EquationRolePrediction,
    EquationSemanticsRecord,
    EquationSemanticsResult,
)
from episteme_graph.agents.paper_skeleton.schema import LogicalBlock, PaperSkeletonResult, SKELETON_VERSION
from episteme_graph.agents.thesis_reconstruction.input_builder import ThesisReconstructionInputBuilder
from episteme_graph.agents.thesis_reconstruction.schema import CartridgeContext

BUILDER = ThesisReconstructionInputBuilder()


def _skeleton():
    return PaperSkeletonResult(
        document_id="doc",
        skeleton_version=SKELETON_VERSION,
        cartridge_id=None,
        paper_goal={"text": "Study the sum rule.", "evidence_block_ids": ["b1"], "reason": "", "confidence": 0.8},
        central_question={"text": "How are rates related?", "evidence_block_ids": ["b1"], "reason": "", "confidence": 0.8},
        headline_claim={"text": "A sum rule relates R Lambda c, RD, and RD*.", "evidence_block_ids": ["b2"], "reason": "", "confidence": 0.8},
        supporting_subclaims=[],
        logical_blocks=[
            LogicalBlock("l1", "derivation", "Derivation", ["sec_1"], ["b2"], "Derives the sum rule.", "reason", 0.9)
        ],
        excluded_regions=[{"region_type": "meta", "block_ids": ["m1"], "reason": "section meta"}],
        review_notes=[],
        confidence=0.8,
    )


def _qualified():
    accepted = QualifiedSpanRecord(
        span_id="s1",
        block_id="b2",
        section_id="sec_1",
        text="A sum rule relates R Lambda c, RD, and RD*.",
        role_labels=["relation"],
        qualification={
            "status": "accepted",
            "claim_tier": "paper_core",
            "claim_type_candidate": "relation",
            "granularity": "good",
            "evidence_adequacy": "sufficient",
            "reviewability": "good",
        },
        edit_suggestions={},
        reason="core relation",
        confidence=0.9,
    )
    rejected = {
        "span_id": "s2",
        "block_id": "b0",
        "role_labels": ["prior_work"],
        "qualification": {"status": "rejected", "claim_tier": "prior_work"},
        "reason": "literature context",
    }
    return ClaimQualificationResult(
        "doc",
        None,
        [accepted],
        [rejected],
        [],
        {"accepted": 1, "rejected": 1},
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
        equation_role=EquationRolePrediction("equation_relation", ["equation_result"], 0.9, "relation"),
        defined_symbols=[DefinedSymbol("R Lambda c", "used", None)],
        local_assumptions=[],
        derivation_links=DerivationLinks(["eq_1_2"], [], []),
        summary="Sum rule relation between R Lambda c, RD, and RD*.",
        review_flags=[],
    )
    return EquationSemanticsResult("doc", None, [record])


def test_build_packages_argument_materials():
    llm_input = BUILDER.build(_skeleton(), _qualified(), _equations())
    assert llm_input.paper_goal == "Study the sum rule."
    assert llm_input.headline_claim.startswith("A sum rule")
    assert llm_input.accepted_claims[0]["claim_id"] == "claim:b2:s1"
    assert llm_input.major_equations[0]["equation_id"] == "eq_3_14"
    assert any(r.get("category") == "prior_work" for r in llm_input.excluded_regions)


def test_cartridge_terms_are_included():
    cartridge = CartridgeContext(
        cartridge_id="test",
        ontology={},
        validation_rules={},
        aliases={"R Lambda c": ["RΛc"]},
    )
    llm_input = BUILDER.build(_skeleton(), _qualified(), _equations(), cartridge=cartridge)
    assert llm_input.cartridge_id == "test"
    assert llm_input.normalized_terms[0]["canonical"] == "R Lambda c"
