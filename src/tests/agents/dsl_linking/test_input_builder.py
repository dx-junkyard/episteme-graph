"""Tests for DSLLinkingInputBuilder."""
from episteme_graph.agents.claim_qualification.schema import ClaimQualificationResult, QualifiedSpanRecord
from episteme_graph.agents.claim_object_builder.schema import (
    ClaimObjectBuildResult,
    ClaimObjectRecord,
)
from episteme_graph.agents.dsl_linking.input_builder import DSLLinkingInputBuilder
from episteme_graph.agents.equation_semantics.schema import (
    DefinedSymbol,
    EquationConfidencePolicy,
    EquationRecord,
    EquationReconstruction,
    EquationSemantics,
    EquationSemanticsResult,
    EquationSourceExtraction,
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
    src = EquationSourceExtraction(
        raw_text="R Lambda c = RD + RD*",
        latex=None,
        plain_text="R Lambda c = RD + RD*",
        source_location={"page": 3, "section_id": "sec_1", "block_id": "e1", "bbox": []},
        extraction_source="pdf_text_layer",
        extraction_status="complete",
    )
    rec = EquationReconstruction.make_none()
    sem = EquationSemantics(
        equation_type="equation_relation",
        secondary_types=[],
        semantic_status="source_extracted",
        confidence=0.9,
        reason="relation",
        defined_symbols=[DefinedSymbol("R Lambda c", "used", None)],
        used_symbols=["R Lambda c", "RD"],
        assumptions=[],
        input_equation_ids=["eq_1_2"],
        output_equation_ids=[],
        linked_text_spans=[],
        source_evidence_ids=[],
        linked_claim_ids=[],
        summary="Sum rule relation.",
        review_flags=[],
    )
    policy = EquationConfidencePolicy(
        can_support_claim=True,
        can_be_used_in_derivation=True,
        can_be_rendered_as_final_formula=True,
        allowed_downstream_use="unrestricted",
        can_be_displayed_in_course=True,
        display_requires_note=False,
        must_not_treat_as_source_extracted=False,
    )
    record = EquationRecord(
        equation_id="eq_3_14",
        document_id="doc",
        label="3.14",
        candidate_trace_ids=[],
        source_extraction=src,
        reconstruction=rec,
        semantics=sem,
        confidence_policy=policy,
    )
    return EquationSemanticsResult("doc", None, [], [record])


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


def test_equation_fields_map_to_nested_schema():
    llm_input = BUILDER.build(_qualified(), equations=_equations())
    eq = llm_input.equations[0]
    assert eq["block_id"] == "e1"
    assert eq["section_id"] == "sec_1"
    assert eq["role"] == "equation_relation"
    assert eq["summary"] == "Sum rule relation."
    assert eq["from_equations"] == ["eq_1_2"]
    assert eq["confidence"] == 0.9


def test_limits_claims():
    llm_input = BUILDER.build(_qualified(), config={"max_claims": 1})
    assert len(llm_input.accepted_claims) == 1


def test_build_uses_atomic_claim_objects_for_dsl_materials():
    qualified = ClaimQualificationResult(
        "doc",
        None,
        [_claim("span_001", "blk_a", "Broad claim with multiple propositions.", "result")],
        [],
        [],
        {},
    )
    claim_objects = ClaimObjectBuildResult(
        "doc",
        None,
        claims=[
            ClaimObjectRecord(
                claim_id="claim_parent",
                document_id="doc",
                claim_type="main_result",
                text="Broad claim with multiple propositions.",
                source_evidence_ids=["ev_parent"],
                source_span_ids=["span_001"],
                concepts=[],
                section_id="sec_1",
                atomicity="split_required",
                is_atomic=False,
                split_suggestions=[
                    {"text": "Nonlinear galaxy bias obstructs direct gravity tests."},
                    {"text": "Bias parameters can be eliminated algebraically."},
                ],
            ),
            ClaimObjectRecord(
                claim_id="claim_atomic_1",
                document_id="doc",
                claim_type="problem_statement",
                text="Nonlinear galaxy bias obstructs direct gravity tests.",
                source_evidence_ids=["ev_001"],
                source_span_ids=["span_001"],
                concepts=[],
                section_id="sec_1",
                atomicity="atomic",
                is_atomic=True,
            ),
            ClaimObjectRecord(
                claim_id="claim_atomic_2",
                document_id="doc",
                claim_type="derivation_result",
                text="Bias parameters can be eliminated algebraically.",
                source_evidence_ids=["ev_002"],
                source_span_ids=["span_001"],
                concepts=[],
                section_id="sec_1",
                atomicity="atomic",
                is_atomic=True,
            ),
        ],
    )

    llm_input = BUILDER.build(qualified, claim_objects=claim_objects)

    claim_ids = [c["claim_id"] for c in llm_input.accepted_claims]
    assert "claim_parent" not in claim_ids
    assert claim_ids == ["claim_atomic_1", "claim_atomic_2"]
    assert all(c["atomicity"] == "atomic" and c["is_atomic"] for c in llm_input.accepted_claims)
