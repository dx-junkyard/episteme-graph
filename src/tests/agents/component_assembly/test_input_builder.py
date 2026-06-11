"""Tests for ComponentAssemblyInputBuilder."""
from episteme_graph.agents.claim_qualification.schema import ClaimQualificationResult, QualifiedSpanRecord
from episteme_graph.agents.component_assembly.input_builder import ComponentAssemblyInputBuilder
from episteme_graph.agents.component_assembly.schema import CartridgeContext
from episteme_graph.agents.id_canonicalization import (
    canonicalize_claim_refs,
    claim_aliases_from_accepted_claims,
)
from episteme_graph.agents.claim_object_builder.schema import (
    ClaimConcept,
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
    assert llm_input.equations[0]["confidence_policy"]["can_be_rendered_as_final_formula"] is True
    assert llm_input.equations[0]["confidence_policy"]["allowed_downstream_use"] == "unrestricted"
    assert llm_input.available_equations[0]["confidence_policy"]["must_not_treat_as_source_extracted"] is False
    assert llm_input.dsl_nodes[0]["node_id"] == "n1"
    assert "PaperRelationComponent" in llm_input.allowed_component_types
    assert "requires" in llm_input.allowed_dependency_types
    assert llm_input.normalized_terms[0]["canonical"] == "HQET"
    assert llm_input.claim_centered_plan["headline_claim"] == "Central thesis."
    assert llm_input.claim_centered_plan["headline_claim_ids"] == ["claim:b1:s1"]
    assert [layer["layer"] for layer in llm_input.claim_centered_plan["support_layers"]] == [
        "theory_base",
        "observable_bridge",
        "derivation_core",
        "result_and_application",
    ]


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


def test_build_uses_atomic_claim_objects_before_component_construction():
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
                atomicity="composite",
                is_atomic=False,
                subclaim_ids=["claim_atomic_1", "claim_atomic_2"],
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


def test_build_carries_claim_concepts_into_available_claims():
    qualified = ClaimQualificationResult(
        "doc",
        None,
        [_claim("span_001", "blk_a", "Skewness constrains gravity.", "result")],
        [],
        [],
        {},
    )
    claim_objects = ClaimObjectBuildResult(
        "doc",
        None,
        claims=[
            ClaimObjectRecord(
                claim_id="claim_atomic_1",
                document_id="doc",
                claim_type="result",
                text="Skewness constrains gravity.",
                source_evidence_ids=["ev_001"],
                source_span_ids=["span_001"],
                concepts=[
                    ClaimConcept(name="skewness", normalized="Skewness", concept_type="observable"),
                    ClaimConcept(name="gravity", normalized="Gravity", concept_type="theory"),
                ],
                section_id="sec_1",
                atomicity="atomic",
                is_atomic=True,
                concept_assignment_status="source_backed",
            ),
        ],
    )

    llm_input = BUILDER.build(qualified, claim_objects=claim_objects)

    available = {c["claim_id"]: c for c in llm_input.available_claims}
    row = available["claim_atomic_1"]
    assert row["concepts"] == ["Skewness", "Gravity"]
    assert row["concept_assignment_status"] == "source_backed"
    accepted = {c["claim_id"]: c for c in llm_input.accepted_claims}
    assert accepted["claim_atomic_1"]["concepts"] == ["Skewness", "Gravity"]


def test_limits_claims():
    qualified = ClaimQualificationResult("doc", None, [_claim("s1", "b1", "A", "relation"), _claim("s2", "b2", "B", "relation")], [], [], {})
    llm_input = BUILDER.build(qualified, config={"max_claims": 1})
    assert len(llm_input.accepted_claims) == 1


def test_span_without_section_id_split_into_atomic_claims_resolves_canonically():
    """Regression: a span with no section_id whose claim was atomically rewritten
    (composite parent + atomic children sharing the span_id) must not leak the
    legacy `claim:<block>:<span>` ID into accepted_claims — that ID is absent
    from available_claims and hard-fails the assembly preflight
    (accepted_claim_ids_not_available → deterministic fallback → export gate error).
    """
    span = QualifiedSpanRecord(
        "span_07", "blk_3", None, "The model derives X and shows Y.", ["result"],
        {"status": "accepted", "claim_tier": "paper_core", "claim_type_candidate": "main_result"},
        {}, "reason", 0.9,
    )
    qualified = ClaimQualificationResult("doc", None, [span], [], [], {})
    claim_objects = ClaimObjectBuildResult(
        "doc",
        None,
        claims=[
            ClaimObjectRecord(
                claim_id="claim_span_07_sub01",
                document_id="doc",
                claim_type="derivation_result",
                text="The model derives X.",
                source_evidence_ids=["ev_1"],
                source_span_ids=["span_07"],
                concepts=[],
                section_id=None,
                atomicity="atomic",
                is_atomic=True,
                parent_claim_id="claim_span_07",
            ),
            ClaimObjectRecord(
                claim_id="claim_span_07_sub02",
                document_id="doc",
                claim_type="main_result",
                text="The model shows Y.",
                source_evidence_ids=["ev_1"],
                source_span_ids=["span_07"],
                concepts=[],
                section_id=None,
                atomicity="atomic",
                is_atomic=True,
                parent_claim_id="claim_span_07",
            ),
            ClaimObjectRecord(
                claim_id="claim_span_07",
                document_id="doc",
                claim_type="main_result",
                text="The model derives X and shows Y.",
                source_evidence_ids=["ev_1"],
                source_span_ids=["span_07"],
                concepts=[],
                section_id=None,
                atomicity="composite",
                is_atomic=False,
                subclaim_ids=["claim_span_07_sub01", "claim_span_07_sub02"],
            ),
        ],
    )

    llm_input = BUILDER.build(qualified, claim_objects=claim_objects)

    accepted_ids = [c["claim_id"] for c in llm_input.accepted_claims]
    available_ids = {c["claim_id"] for c in llm_input.available_claims}
    assert set(accepted_ids) <= available_ids
    assert accepted_ids == ["claim_span_07_sub01", "claim_span_07_sub02"]


def test_span_unresolvable_against_claim_objects_is_skipped():
    """Regression: when claim_objects exist (e.g. resumed artifact) but a span
    has no corresponding claim object, the span must be skipped instead of
    injecting a provisional/legacy ID that fails the assembly preflight.
    """
    qualified = ClaimQualificationResult(
        "doc", None,
        [
            _claim("s1", "b1", "Known claim.", "result"),
            _claim("s_orphan", "b9", "Orphan claim.", "result"),
        ],
        [], [], {},
    )
    claim_objects = ClaimObjectBuildResult(
        "doc",
        None,
        claims=[
            ClaimObjectRecord(
                claim_id="claim_s1",
                document_id="doc",
                claim_type="result",
                text="Known claim.",
                source_evidence_ids=["ev_1"],
                source_span_ids=["s1"],
                concepts=[],
                section_id="sec_1",
            ),
        ],
    )

    llm_input = BUILDER.build(qualified, claim_objects=claim_objects)

    accepted_ids = [c["claim_id"] for c in llm_input.accepted_claims]
    available_ids = {c["claim_id"] for c in llm_input.available_claims}
    assert accepted_ids == ["claim_s1"]
    assert set(accepted_ids) <= available_ids
