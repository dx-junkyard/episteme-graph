"""Issue #439: SymbolRegistryBuilder projects meaning/source onto equations and
build_symbol_index exposes them for the equation export."""
from __future__ import annotations

from episteme_graph.agents.equation_semantics.schema import (
    DefinedSymbol,
    EquationConfidencePolicy,
    EquationReconstruction,
    EquationRecord,
    EquationSemantics,
    EquationSemanticsResult,
    EquationSourceExtraction,
)
from episteme_graph.agents.symbol_registry.builder import (
    SymbolRegistryBuilder,
    build_symbol_index,
)


def _record(symbol, evidence_text):
    src = EquationSourceExtraction(
        raw_text="raw", latex=f"{symbol} = 1", plain_text="p",
        source_location={"block_id": "blk_1", "section_id": "sec_1"},
        extraction_source="pdf_text_layer", extraction_status="complete",
        needs_math_review=False, review_reason=[],
    )
    rec = EquationReconstruction(None, None, "none", [], [], 0.0, False, [])
    sem = EquationSemantics(
        equation_type="definition", secondary_types=[], semantic_status="source_backed",
        confidence=0.9, reason="r",
        defined_symbols=[DefinedSymbol(symbol, "defined", evidence_text=evidence_text)],
        used_symbols=[], assumptions=[], input_equation_ids=[], output_equation_ids=[],
        linked_text_spans=[], source_evidence_ids=["ev_1"], linked_claim_ids=[],
        summary="s", review_flags=[],
    )
    policy = EquationConfidencePolicy.derive(src, rec, sem)
    return EquationRecord("eq_1", "doc", None, [], src, rec, sem, policy)


def test_builder_projects_meaning_and_source_onto_equations():
    record = _record("θ", "the angle parameter")
    equations = EquationSemanticsResult("doc", None, [], [record])
    registry = SymbolRegistryBuilder().run(equations)

    ds = record.semantics.defined_symbols[0]
    assert ds.symbol_id  # linked to the registry
    assert ds.meaning  # self-describing meaning projected onto the equation
    assert ds.source_evidence_id == "ev_1"

    index = build_symbol_index(registry)
    assert ds.symbol_id in index
    assert index[ds.symbol_id]["source_evidence_id"] == "ev_1"


def test_build_symbol_index_accepts_dict_and_none():
    assert build_symbol_index(None) == {}
    record = _record("k", "rate constant")
    equations = EquationSemanticsResult("doc", None, [], [record])
    registry = SymbolRegistryBuilder().run(equations)
    from_obj = build_symbol_index(registry)
    from_dict = build_symbol_index(registry.to_dict())
    assert from_obj.keys() == from_dict.keys()
