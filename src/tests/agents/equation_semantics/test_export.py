"""Tests for EquationSemanticsResult.to_equations_export()."""
from __future__ import annotations

from episteme_graph.agents.equation_semantics.schema import (
    DefinedSymbol,
    DerivationLinks,
    EquationRolePrediction,
    EquationSemanticsRecord,
    EquationSemanticsResult,
    LocalAssumption,
)


def _make_record(eq_id: str = "eq_3_14") -> EquationSemanticsRecord:
    return EquationSemanticsRecord(
        equation_id=eq_id,
        block_id="blk_eq_3_14",
        section_id="doc_test:sec_3_1",
        section_title="Formulation",
        label="3.14",
        text="LaTeX placeholder",
        latex=r"\Gamma_{tot} = \sum_i \Gamma_i",
        plain_text="Gamma_tot = sum_i Gamma_i",
        equation_role=EquationRolePrediction(
            primary="equation_result",
            secondary=["equation_relation"],
            confidence=0.91,
            reason="",
        ),
        defined_symbols=[
            DefinedSymbol(symbol="A", definition_status="defined"),
            DefinedSymbol(symbol="B", definition_status="used"),
        ],
        local_assumptions=[LocalAssumption(text="heavy_quark_limit", source_block_ids=[])],
        derivation_links=DerivationLinks(
            from_equations=["eq_3_5", "eq_3_6"],
            to_equations=[],
            linked_text_spans=[],
        ),
        summary="normalized_total_decay_rate_sum_rule",
        review_flags=[],
    )


def test_export_shape_matches_spec():
    result = EquationSemanticsResult(
        document_id="doc_test",
        cartridge_id="particle_physics",
        equations=[_make_record()],
    )
    exported = result.to_equations_export(
        evidence_index={"blk_eq_3_14": ["ev_0001"]},
        claim_index={"eq_3_14": ["claim_xxx"]},
    )

    assert len(exported) == 1
    e = exported[0]
    assert e["equation_id"] == "eq_3_14"
    assert e["document_id"] == "doc_test"
    assert e["label"] == "3.14"
    assert e["latex"].startswith(r"\Gamma")
    assert e["equation_role"] == {"primary": "equation_result", "secondary": ["equation_relation"]}
    assert e["semantic_kind"] == "normalized_total_decay_rate_sum_rule"
    assert e["used_symbols"] == ["B"]
    assert e["defined_symbols"] == ["A"]
    assert e["introduced_symbols"] == ["A"]
    assert e["local_assumptions"] == ["heavy_quark_limit"]
    assert e["derivation_links"] == {"from_equations": ["eq_3_5", "eq_3_6"], "to_equations": []}
    assert e["linked_claim_ids"] == ["claim_xxx"]
    assert e["source_evidence_ids"] == ["ev_0001"]
    assert e["section_id"] == "doc_test:sec_3_1"


def test_export_handles_missing_indexes():
    result = EquationSemanticsResult(
        document_id="doc_test",
        cartridge_id=None,
        equations=[_make_record("eq_1")],
    )
    exported = result.to_equations_export()
    assert exported[0]["linked_claim_ids"] == []
    assert exported[0]["source_evidence_ids"] == []
