"""Tests for ComponentAssemblyInputBuilder."""
from episteme_graph.agents.claim_qualification.schema import ClaimQualificationResult, QualifiedSpanRecord
from episteme_graph.agents.component_assembly.input_builder import ComponentAssemblyInputBuilder
from episteme_graph.agents.component_assembly.schema import CartridgeContext
from episteme_graph.agents.id_canonicalization import (
    canonicalize_claim_refs,
    claim_aliases_from_accepted_claims,
)
from episteme_graph.agents.claim_object_builder.schema import (
    ClaimObjectBuildResult,
    ClaimObjectRecord,
)
from episteme_graph.agents.dsl_linking.schema import DSLEdge, DSLLinkingResult, DSLNode, DSL_VERSION
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

BUILDER = ComponentAssemblyInputBuilder()


def _claim(span_id, block_id, text, claim_type):
    return QualifiedSpanRecord(
        span_id, block_id, "sec_1", text, [claim_type],
        {"status": "accepted", "claim_tier": "paper_core", "claim_type_candidate": claim_type},
        {}, "reason", 0.9
    )


def _qualified():
    return ClaimQualificationResult("doc", None, [_claim("s1", "b1", "Heavy quark limit.", "assumption")], [], [], {})


def _equations():
    source_extraction = EquationSourceExtraction(
        raw_text="R = A",
        latex=None,
        plain_text="R = A",
        source_location={"page": 1, "section_id": "sec_1", "block_id": "e1", "bbox": None},
        extraction_source="pdf_text_layer",
        extraction_status="complete",
    )
    reconstruction = EquationReconstruction.make_none()
    semantics = EquationSemantics(
        equation_type="relation",
        secondary_types=[],
        semantic_status="source_backed",
        confidence=0.9,
        reason="Relation for R.",
        defined_symbols=[DefinedSymbol("R", "used", None)],
        used_symbols=[],
        assumptions=[],
        input_equation_ids=[],
        output_equation_ids=[],
        linked_text_spans=[],
        source_evidence_ids=[],
        linked_claim_ids=[],
        summary="Relation for R.",
        review_flags=[],
    )
    confidence_policy = EquationConfidencePolicy.derive(source_extraction, reconstruction, semantics)
    record = EquationRecord(
        equation_id="eq_1_1",
        document_id="doc",
        label="1.1",
        candidate_trace_ids=[],
        source_extraction=source_extraction,
        reconstruction=reconstruction,
        semantics=semantics,
        confidence_policy=confidence_policy,
    )
    return EquationSemanticsResult("doc", None, [], [record])


def _thesis():
    return ThesisReconstructionResult(
        "doc", THESIS_VERSION, None,
        {"text": "Central thesis.", "claim_ids": ["claim:b1:s1"], "equation_ids": ["eq_1_1"], "evidence_block_ids": ["b1"], "confidence": 0.9},
        [], {"direct_supports": [{"text": "support", "claim_ids": ["claim:b1:s1"], "equation_ids": ["eq_1_1"], "support_type": "derivation_support"}]},
        [], [], [], 0.9
    )


def _dsl():
    return DSLLinkingResult(
        "doc", DSL_VERSION,
        [DSLNode("n1", "Relation", "relation_r", "mixed", {"claim_ids": ["claim:b1:s1"], "equation_ids": ["eq_1_1"], "thesis_refs": []}, "reason", 0.9)],
        [DSLEdge("e1", "n1", "n1", "CONTAINS", "summarizes", "?", {"claim_ids": ["claim:b1:s1"], "equation_ids": [], "thesis_refs": []}, "reason", 0.5)],
        [], [], 0.8
    )


def test_build_packages_inputs_and_allowed_vocabularies():
    cartridge = CartridgeContext(
        "test", {}, {"component_types": [{"id": "PaperRelationComponent"}]},
        {"relation_types": [{"id": "REQUIRES"}]}, {}, aliases={"HQET": ["Heavy Quark Effective Theory"]}
    )
    llm_input = BUILDER.build(_qualified(), _equations(), _thesis(), _dsl(), cartridge)
    assert llm_input.accepted_claims[0]["claim_id"] == "claim:b1:s1"
    assert llm_input.equations[0]["equation_id"] == "eq_1_1"
    assert llm_input.equations[0]["block_id"] == "e1"
    assert llm_input.equations[0]["role"] == "relation"
    assert llm_input.equations[0]["plain_text"] == "R = A"
    assert llm_input.equations[0]["confidence_policy"]["can_support_claim"] is True
    assert llm_input.available_equations[0]["confidence_policy"]["must_not_treat_as_source_extracted"] is False
    assert llm_input.dsl_nodes[0]["node_id"] == "n1"
    assert "PaperRelationComponent" in llm_input.allowed_component_types
    assert "requires" in llm_input.allowed_dependency_types
    assert llm_input.normalized_terms[0]["canonical"] == "HQET"


def test_build_uses_canonical_claim_ids_from_claim_objects():
    qualified = ClaimQualificationResult(
        "doc",
        None,
        [
            _claim("span_001", "blk_a", "First claim.", "result"),
            _claim("span_001", "blk_b", "Second claim.", "result"),
        ],
        [],
        [],
        {},
    )
    claim_objects = ClaimObjectBuildResult(
        "doc",
        None,
        claims=[
            ClaimObjectRecord(
                claim_id="claim_span_001",
                document_id="doc",
                claim_type="result",
                text="First claim.",
                source_evidence_ids=["ev_0001"],
                source_span_ids=["span_001"],
                concepts=[],
                section_id="sec_1",
            ),
            ClaimObjectRecord(
                claim_id="claim_span_001_2",
                document_id="doc",
                claim_type="result",
                text="Second claim.",
                source_evidence_ids=["ev_0002"],
                source_span_ids=["span_001"],
                concepts=[],
                section_id="sec_1",
            ),
        ],
    )

    qualified.qualified_spans[1].section_id = "sec_2"
    claim_objects.claims[1].section_id = "sec_2"
    llm_input = BUILDER.build(qualified, claim_objects=claim_objects)

    assert [c["claim_id"] for c in llm_input.accepted_claims] == [
        "claim_span_001",
        "claim_span_001_2",
    ]
    assert llm_input.accepted_claims[0]["legacy_claim_id"] == "claim:blk_a:span_001"
    raw_refs = {"evidence_refs": {"claim_ids": ["claim:blk_b:span_001"]}}
    normalized = canonicalize_claim_refs(
        raw_refs,
        claim_objects,
        claim_aliases_from_accepted_claims(llm_input.accepted_claims),
    )
    assert normalized["evidence_refs"]["claim_ids"] == ["claim_span_001_2"]


def test_limits_claims():
    qualified = ClaimQualificationResult("doc", None, [_claim("s1", "b1", "A", "relation"), _claim("s2", "b2", "B", "relation")], [], [], {})
    llm_input = BUILDER.build(qualified, config={"max_claims": 1})
    assert len(llm_input.accepted_claims) == 1
