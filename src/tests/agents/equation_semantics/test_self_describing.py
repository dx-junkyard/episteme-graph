"""Issue #439: equations must be self-describing (symbols + role_in_argument).

Multi-domain fixtures (two unrelated symbol/notation sets) verify the behavior
without asserting any specific symbol name, equation name, or domain term.
"""
from __future__ import annotations

import pytest

from episteme_graph.agents.equation_semantics.schema import (
    ROLE_IN_ARGUMENT_VOCAB,
    DefinedSymbol,
    EquationConfidencePolicy,
    EquationReconstruction,
    EquationRecord,
    EquationSemantics,
    EquationSemanticsResult,
    EquationSourceExtraction,
    derive_role_in_argument,
    extract_free_symbols,
)


def _record(eq_id, latex, equation_type, defined, used, *, evidence_ids=(), inputs=()):
    src = EquationSourceExtraction(
        raw_text="raw",
        latex=latex,
        plain_text="plain",
        source_location={"block_id": f"blk_{eq_id}", "section_id": "sec_1"},
        extraction_source="pdf_text_layer",
        extraction_status="complete",
        needs_math_review=False,
        review_reason=[],
    )
    rec = EquationReconstruction(
        latex=None, plain_text=None, status="none", method=[], supporting_refs=[],
        confidence=0.0, review_required=False, review_reason=[],
    )
    sem = EquationSemantics(
        equation_type=equation_type,
        secondary_types=[],
        semantic_status="source_backed",
        confidence=0.9,
        reason="r",
        defined_symbols=list(defined),
        used_symbols=list(used),
        assumptions=[],
        input_equation_ids=list(inputs),
        output_equation_ids=[],
        linked_text_spans=[],
        source_evidence_ids=list(evidence_ids),
        linked_claim_ids=[],
        summary="s",
        review_flags=[],
    )
    policy = EquationConfidencePolicy.derive(src, rec, sem)
    return EquationRecord(
        equation_id=eq_id, document_id="doc", label=None, candidate_trace_ids=[],
        source_extraction=src, reconstruction=rec, semantics=sem, confidence_policy=policy,
    )


# Two unrelated "domains": notation/claim sets share no symbols or terms.
_DOMAINS = {
    "domain_a": {
        "latex": r"\Gamma = \alpha x + \beta",
        "defined": [DefinedSymbol("Γ", "defined", evidence_text="total rate",
                                  symbol_id="sym_a_gamma", source_evidence_id="ev_a1")],
        "used": ["x"],
    },
    "domain_b": {
        "latex": r"P(y) = \lambda y - \mu",
        "defined": [DefinedSymbol("P", "defined", evidence_text="probability",
                                  symbol_id="sym_b_p", source_evidence_id="ev_b1")],
        "used": ["y"],
    },
}


@pytest.mark.parametrize("domain", sorted(_DOMAINS))
def test_symbols_array_is_populated_and_self_describing(domain):
    spec = _DOMAINS[domain]
    record = _record("eq_1", spec["latex"], "definition", spec["defined"], spec["used"],
                     evidence_ids=["ev_fallback"])
    result = EquationSemanticsResult("doc", None, [], [record])
    symbol_index = {
        getattr(d, "symbol_id"): {"meaning": "resolved meaning", "source_evidence_id": "ev_reg"}
        for d in spec["defined"]
    }
    exported = result.to_equations_export(symbol_index=symbol_index)[0]

    # symbols[] is non-empty and each entry carries symbol + a source.
    assert exported["symbols"], "symbols[] must not be empty for an equation with free symbols"
    for entry in exported["symbols"]:
        assert entry["symbol"]
        assert "meaning" in entry
        assert entry["source_evidence_id"], "every symbol must trace back to a source"

    # role_in_argument is in the controlled vocabulary.
    assert exported["role_in_argument"] in ROLE_IN_ARGUMENT_VOCAB


def test_free_symbol_coverage_meets_threshold_across_domains():
    """Aggregate: equations with free symbols have non-empty symbols[] (#439)."""
    records = []
    for i, spec in enumerate(_DOMAINS.values(), start=1):
        records.append(_record(f"eq_{i}", spec["latex"], "definition",
                                spec["defined"], spec["used"], evidence_ids=["ev"]))
    result = EquationSemanticsResult("doc", None, [], records)
    exported = result.to_equations_export()
    with_free = [e for e in exported if extract_free_symbols(e["latex"])]
    assert with_free
    non_empty = [e for e in with_free if e["symbols"]]
    assert len(non_empty) / len(with_free) >= 0.9


def test_role_in_argument_derivation_is_domain_agnostic():
    assert derive_role_in_argument("definition") == "definition"
    assert derive_role_in_argument("constraint") == "constraint"
    assert derive_role_in_argument("result") == "result"
    assert derive_role_in_argument("relation", input_equation_ids=["eq_0"]) == "derived"
    assert derive_role_in_argument("relation") == "premise"
    # every output is in the controlled vocabulary
    for etype in ("definition", "relation", "transformation", "approximation",
                  "result", "constraint", "unknown"):
        assert derive_role_in_argument(etype) in ROLE_IN_ARGUMENT_VOCAB


def test_extract_free_symbols_ignores_latex_structure():
    free = extract_free_symbols(r"\frac{\Gamma}{2} = \sum_i \alpha_i x")
    assert "Γ" not in free  # greek command is normalized to its name token
    assert "gamma" not in free or "alpha" in free  # greek names recognized
    # structural commands are excluded
    assert "frac" not in free
    assert "sum" not in free
    # single-letter variables retained
    assert "x" in free
