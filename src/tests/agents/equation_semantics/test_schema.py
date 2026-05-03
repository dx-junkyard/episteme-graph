"""Tests for EquationSemantics schema helpers."""
import json

from episteme_graph.agents.equation_semantics.schema import (
    DefinedSymbol,
    DerivationLinks,
    EquationRolePrediction,
    EquationSemanticsRecord,
    EquationSemanticsResult,
)


def test_to_json_round_trip():
    record = EquationSemanticsRecord(
        equation_id="eq_1_1",
        block_id="e1",
        section_id="sec_1",
        section_title="Formulation",
        label="1.1",
        text="N = a + b",
        latex=None,
        plain_text="N = a + b",
        equation_role=EquationRolePrediction("equation_definition", [], 0.9, "definition"),
        defined_symbols=[DefinedSymbol("N", "defined", "where N is defined as")],
        local_assumptions=[],
        derivation_links=DerivationLinks([], [], []),
        summary="Definition of prefactor N.",
        review_flags=[],
    )
    result = EquationSemanticsResult("doc", None, [record])
    restored = EquationSemanticsResult.from_dict(json.loads(result.to_json()))
    assert restored.equations[0].equation_role.primary == "equation_definition"
    assert restored.equations[0].defined_symbols[0].symbol == "N"
