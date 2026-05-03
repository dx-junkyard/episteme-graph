"""Tests for ComponentAssemblyInputBuilder."""
from episteme_graph.agents.claim_qualification.schema import ClaimQualificationResult, QualifiedSpanRecord
from episteme_graph.agents.component_assembly.input_builder import ComponentAssemblyInputBuilder
from episteme_graph.agents.component_assembly.schema import CartridgeContext
from episteme_graph.agents.dsl_linking.schema import DSLEdge, DSLLinkingResult, DSLNode, DSL_VERSION
from episteme_graph.agents.equation_semantics.schema import DefinedSymbol, DerivationLinks, EquationRolePrediction, EquationSemanticsRecord, EquationSemanticsResult
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
    record = EquationSemanticsRecord(
        "eq_1_1", "e1", "sec_1", "Derivation", "1.1", "R = A", None, "R = A",
        EquationRolePrediction("equation_relation", [], 0.9, "relation"),
        [DefinedSymbol("R", "used", None)], [], DerivationLinks([], [], []),
        "Relation for R.", []
    )
    return EquationSemanticsResult("doc", None, [record])


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
    assert llm_input.dsl_nodes[0]["node_id"] == "n1"
    assert "PaperRelationComponent" in llm_input.allowed_component_types
    assert "requires" in llm_input.allowed_dependency_types
    assert llm_input.normalized_terms[0]["canonical"] == "HQET"


def test_limits_claims():
    qualified = ClaimQualificationResult("doc", None, [_claim("s1", "b1", "A", "relation"), _claim("s2", "b2", "B", "relation")], [], [], {})
    llm_input = BUILDER.build(qualified, config={"max_claims": 1})
    assert len(llm_input.accepted_claims) == 1
