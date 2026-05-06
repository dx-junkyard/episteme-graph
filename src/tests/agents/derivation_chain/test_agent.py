"""Tests for DerivationChainAgent — updated for nested EquationRecord schema (issue #261)."""
from __future__ import annotations

from episteme_graph.agents.derivation_chain import DerivationChainAgent
from episteme_graph.agents.derivation_chain.schema import OPERATION_ONTOLOGY
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


def _make_eq(
    eq_id: str,
    *,
    role: str = "relation",
    from_eqs: list[str] | None = None,
    section_id: str = "doc_test:sec_3_1",
    summary: str = "",
    assumptions: list[str] | None = None,
    linked_claim_ids: list[str] | None = None,
    source_evidence_ids: list[str] | None = None,
) -> EquationRecord:
    src = EquationSourceExtraction(
        raw_text="LaTeX placeholder",
        latex=None,
        plain_text=None,
        source_location={
            "page": 1,
            "section_id": section_id,
            "block_id": f"blk_{eq_id}",
            "bbox": [],
        },
        extraction_source="pdf_text_layer",
        extraction_status="complete",
        needs_math_review=False,
        review_reason=[],
    )
    rec = EquationReconstruction.make_none()
    sem = EquationSemantics(
        equation_type=role,
        secondary_types=[],
        semantic_status="source_backed",
        confidence=0.85,
        reason="",
        defined_symbols=[],
        used_symbols=[],
        assumptions=list(assumptions if assumptions is not None else ["heavy_quark_limit"]),
        input_equation_ids=list(from_eqs or []),
        output_equation_ids=[],
        linked_text_spans=[],
        source_evidence_ids=list(source_evidence_ids or []),
        linked_claim_ids=list(linked_claim_ids or []),
        summary=summary,
        review_flags=[],
    )
    cp = EquationConfidencePolicy.derive(src, rec, sem)
    return EquationRecord(
        equation_id=eq_id,
        document_id="doc_test",
        label=eq_id.replace("eq_", "").replace("_", "."),
        candidate_trace_ids=[f"eqcand_blk_{eq_id}"],
        source_extraction=src,
        reconstruction=rec,
        semantics=sem,
        confidence_policy=cp,
    )


def _make_result(eqs: list[EquationRecord]) -> EquationSemanticsResult:
    return EquationSemanticsResult(
        document_id="doc_test",
        cartridge_id="particle_physics",
        equation_candidates=[],
        equations=eqs,
    )


def test_chain_walks_back_from_leaf_result():
    result = _make_result([
        _make_eq("eq_1_2", role="definition"),
        _make_eq("eq_3_1", from_eqs=["eq_1_2"], role="transformation"),
        _make_eq("eq_3_5", from_eqs=["eq_3_1"], role="approximation"),
        _make_eq("eq_3_14", from_eqs=["eq_3_5"], role="result"),
    ])
    agent = DerivationChainAgent()
    chain_result = agent.run(result)

    assert len(chain_result.chains) == 1
    chain = chain_result.chains[0]
    assert chain.derivation_id == "derivation_eq_3_14"
    # Steps: eq_1_2→eq_3_1, eq_3_1→eq_3_5, eq_3_5→eq_3_14
    assert [s.output_equation_ids for s in chain.steps] == [
        ["eq_3_1"], ["eq_3_5"], ["eq_3_14"]
    ]
    assert chain.steps[0].input_equation_ids == ["eq_1_2"]
    assert chain.steps[-1].operation == "derive_result"
    # Assumption refs propagated
    assert all(s.assumption_refs == ["heavy_quark_limit"] for s in chain.steps)


def test_no_chains_when_no_links():
    result = _make_result([_make_eq("eq_a"), _make_eq("eq_b")])
    agent = DerivationChainAgent()
    chain_result = agent.run(result)
    assert chain_result.chains == []


def test_required_claim_ids_propagated():
    result = _make_result([
        _make_eq("eq_1"),
        _make_eq("eq_2", from_eqs=["eq_1"], role="result"),
    ])
    agent = DerivationChainAgent()
    chain_result = agent.run(result, claim_link_index={"eq_2": ["claim_xyz"]})
    assert "claim_xyz" in chain_result.chains[0].steps[0].required_claim_ids


def test_semantics_linked_claim_ids_propagated():
    """EquationSemantics.linked_claim_ids も step に伝播する (issue #261)."""
    result = _make_result([
        _make_eq("eq_1"),
        _make_eq("eq_2", from_eqs=["eq_1"], role="result", linked_claim_ids=["claim_abc"]),
    ])
    agent = DerivationChainAgent()
    chain_result = agent.run(result)
    assert "claim_abc" in chain_result.chains[0].steps[0].required_claim_ids


def test_source_evidence_ids_propagated():
    """EquationSemantics.source_evidence_ids が step に伝播する (issue #261)."""
    result = _make_result([
        _make_eq("eq_1"),
        _make_eq("eq_2", from_eqs=["eq_1"], role="result", source_evidence_ids=["ev_0001"]),
    ])
    agent = DerivationChainAgent()
    chain_result = agent.run(result)
    assert "ev_0001" in chain_result.chains[0].steps[0].source_evidence_ids


def test_teaching_takeaway_is_generated():
    result = _make_result([
        _make_eq("eq_1"),
        _make_eq("eq_2", from_eqs=["eq_1"], role="result"),
    ])
    agent = DerivationChainAgent()
    chain_result = agent.run(result)
    assert chain_result.chains[0].teaching_takeaway
    assert "eq_2" in chain_result.chains[0].teaching_takeaway
    assert "eq_1" in chain_result.chains[0].teaching_takeaway


def test_chain_missing_assumptions_emits_warning():
    result = _make_result([
        _make_eq("eq_1", assumptions=[]),
        _make_eq("eq_2", from_eqs=["eq_1"], role="result", assumptions=[]),
    ])
    agent = DerivationChainAgent()
    chain_result = agent.run(result)
    rules = {i.rule_id for i in chain_result.validation_issues}
    assert "derivation_chain_missing_assumptions" in rules


def test_missing_claim_links_emits_warning_when_index_supplied():
    result = _make_result([
        _make_eq("eq_a"),
        _make_eq("eq_b", from_eqs=["eq_a"], role="result"),
    ])
    agent = DerivationChainAgent()
    chain_result = agent.run(result, claim_link_index={"eq_xxx": ["c1"]})
    rules = {i.rule_id for i in chain_result.validation_issues}
    assert "derivation_chain_missing_claim_links" in rules


def test_claim_chain_generated_when_no_equations():
    """equation がない場合に claim order ベースの chain を生成する (issue #261)."""
    from episteme_graph.agents.claim_object_builder.schema import ClaimObjectBuildResult, ClaimObjectRecord, ClaimConcept

    claim_result = ClaimObjectBuildResult(
        document_id="doc_test",
        cartridge_id=None,
        claims=[
            ClaimObjectRecord(
                claim_id="claim_001",
                document_id="doc_test",
                claim_type="assumption",
                text="Assume heavy quark limit.",
                source_evidence_ids=["ev_0001"],
                source_span_ids=["span_001"],
                concepts=[],
                section_id="doc_test:sec_1",
            ),
            ClaimObjectRecord(
                claim_id="claim_002",
                document_id="doc_test",
                claim_type="result",
                text="The decay rate is bounded.",
                source_evidence_ids=[],
                source_span_ids=["span_002"],
                concepts=[],
                section_id="doc_test:sec_1",
            ),
        ],
    )
    empty_equations = _make_result([])
    agent = DerivationChainAgent()
    chain_result = agent.run(empty_equations, claim_build_result=claim_result)

    assert len(chain_result.chains) == 1
    chain = chain_result.chains[0]
    assert chain.chain_type == "claim_chain"
    assert len(chain.steps) == 2
    assert chain.steps[-1].operation == "infer_conclusion"


def test_step_schema_has_extended_fields():
    """DerivationStep に issue #261 拡張フィールドがある。"""
    result = _make_result([
        _make_eq("eq_x"),
        _make_eq("eq_y", from_eqs=["eq_x"], role="result"),
    ])
    agent = DerivationChainAgent()
    chain_result = agent.run(result)
    step = chain_result.chains[0].steps[0]
    assert hasattr(step, "input_claim_ids")
    assert hasattr(step, "output_claim_ids")
    assert hasattr(step, "assumption_ids")
    assert hasattr(step, "source_evidence_ids")
    assert hasattr(step, "review_status")


def test_operation_ontology_is_valid():
    """OPERATION_ONTOLOGY に期待するエントリが含まれる (issue #261)."""
    required = [
        "state_assumption", "apply_definition", "apply_criterion",
        "introduce_observable", "apply_equation", "substitute",
        "normalize", "approximate", "compare",
        "infer_intermediate_claim", "infer_conclusion", "flag_limitation",
    ]
    for op in required:
        assert op in OPERATION_ONTOLOGY, f"{op!r} missing from OPERATION_ONTOLOGY"
