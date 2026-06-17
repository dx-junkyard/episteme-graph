"""Tests for issue #386 — system-level (multi-equation) derivations."""

from __future__ import annotations

from episteme_graph.agents.derivation_chain import DerivationChainAgent
from episteme_graph.agents.derivation_chain.system_derivation import (
    detect_system_level_derivations,
)
from episteme_graph.agents.equation_semantics.schema import (
    DefinedSymbol,
    EquationConfidencePolicy,
    EquationReconstruction,
    EquationRecord,
    EquationSemantics,
    EquationSemanticsResult,
    EquationSourceExtraction,
)


def _make_eq(
    eq_id: str,
    *,
    role: str = "relation",
    defined: list[str] | None = None,
    used: list[str] | None = None,
    summary: str = "",
    evidence: list[str] | None = None,
) -> EquationRecord:
    src = EquationSourceExtraction(
        raw_text="x",
        latex="x = y",
        plain_text="x = y",
        source_location={"page": 1, "section_id": "doc_test:sec_1", "block_id": f"blk_{eq_id}", "bbox": []},
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
        defined_symbols=[DefinedSymbol(symbol=s, definition_status="defined") for s in (defined or [])],
        used_symbols=list(used or []),
        assumptions=["weak_field"],
        input_equation_ids=[],
        output_equation_ids=[],
        linked_text_spans=[],
        source_evidence_ids=list(evidence or []),
        linked_claim_ids=[],
        summary=summary,
        review_flags=[],
    )
    cp = EquationConfidencePolicy.derive(src, rec, sem)
    return EquationRecord(
        equation_id=eq_id,
        document_id="doc_test",
        label=eq_id,
        candidate_trace_ids=[f"eqcand_{eq_id}"],
        source_extraction=src,
        reconstruction=rec,
        semantics=sem,
        confidence_policy=cp,
    )


def _result(equations) -> EquationSemanticsResult:
    return EquationSemanticsResult(
        document_id="doc_test",
        cartridge_id=None,
        equation_candidates=[],
        equations=equations,
        validation_issues=[],
    )


def test_detects_elimination_system_over_equation_group():
    # Two equations share nuisance symbol b2 (eliminated), result keeps S3.
    eqs = [
        _make_eq("eq_1", role="relation", defined=["S3"], used=["b1", "b2"],
                 summary="linearized skewness bias dependence", evidence=["ev_1"]),
        _make_eq("eq_2", role="relation", defined=["K4"], used=["b1", "b2"],
                 summary="linearized kurtosis bias dependence", evidence=["ev_2"]),
        _make_eq("eq_3", role="result", defined=["S3"], used=["b1"],
                 summary="eliminate b2 to derive consistency relation", evidence=["ev_3"]),
    ]
    chains = detect_system_level_derivations(_result(eqs))
    assert chains, "expected at least one system-level derivation"
    sys = chains[0]
    assert sys.chain_type == "system_level"
    assert sys.operation in ("eliminate", "solve", "constrain", "approximate", "compare")
    # b2 is shared across inputs but absent from the result -> eliminated.
    assert "b2" in sys.eliminated_symbols
    assert sys.input_equation_ids and sys.output_equation_ids
    assert sys.source_evidence_ids  # source-backed by evidence


def test_system_derivation_without_result_is_review_required():
    eqs = [
        _make_eq("eq_1", role="relation", used=["a", "b"]),
        _make_eq("eq_2", role="relation", used=["a", "b"]),
    ]
    chains = detect_system_level_derivations(_result(eqs))
    assert chains
    sys = chains[0]
    assert sys.review_required is True
    assert "no_result_equation_identified" in sys.review_reasons


def test_no_shared_symbols_yields_no_system_derivation():
    eqs = [
        _make_eq("eq_1", defined=["x"], used=["x"]),
        _make_eq("eq_2", defined=["y"], used=["y"]),
    ]
    assert detect_system_level_derivations(_result(eqs)) == []


def test_agent_run_appends_system_level_chain():
    eqs = [
        _make_eq("eq_1", role="relation", defined=["S3"], used=["b1", "b2"], evidence=["ev_1"]),
        _make_eq("eq_2", role="relation", defined=["K4"], used=["b1", "b2"], evidence=["ev_2"]),
        _make_eq("eq_3", role="result", defined=["S3"], used=["b1"], evidence=["ev_3"]),
    ]
    result = DerivationChainAgent().run(_result(eqs))
    system_chains = [c for c in result.chains if c.chain_type == "system_level"]
    assert system_chains, "agent.run should append system-level derivations"
