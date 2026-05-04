"""Tests for DerivationChainAgent."""
from __future__ import annotations

from episteme_graph.agents.derivation_chain import DerivationChainAgent
from episteme_graph.agents.equation_semantics.schema import (
    DefinedSymbol,
    DerivationLinks,
    EquationRolePrediction,
    EquationSemanticsRecord,
    EquationSemanticsResult,
    LocalAssumption,
)


def _make_eq(
    eq_id: str,
    *,
    role: str = "equation_relation",
    from_eqs: list[str] | None = None,
    section_id: str = "doc_test:sec_3_1",
    summary: str = "",
) -> EquationSemanticsRecord:
    return EquationSemanticsRecord(
        equation_id=eq_id,
        block_id=f"blk_{eq_id}",
        section_id=section_id,
        section_title=None,
        label=eq_id.replace("eq_", "").replace("_", "."),
        text="LaTeX placeholder",
        latex=None,
        plain_text=None,
        equation_role=EquationRolePrediction(
            primary=role, secondary=[], confidence=0.85, reason=""
        ),
        defined_symbols=[],
        local_assumptions=[LocalAssumption(text="heavy_quark_limit", source_block_ids=[])],
        derivation_links=DerivationLinks(
            from_equations=list(from_eqs or []),
            to_equations=[],
            linked_text_spans=[],
        ),
        summary=summary,
        review_flags=[],
    )


def test_chain_walks_back_from_leaf_result():
    equations = EquationSemanticsResult(
        document_id="doc_test",
        cartridge_id="particle_physics",
        equations=[
            _make_eq("eq_1_2", role="equation_definition"),
            _make_eq("eq_3_1", from_eqs=["eq_1_2"], role="equation_transformation"),
            _make_eq("eq_3_5", from_eqs=["eq_3_1"], role="equation_approximation"),
            _make_eq("eq_3_14", from_eqs=["eq_3_5"], role="equation_result"),
        ],
    )
    agent = DerivationChainAgent()
    result = agent.run(equations)

    assert len(result.chains) == 1
    chain = result.chains[0]
    assert chain.derivation_id == "derivation_eq_3_14"
    # 3 steps: eq_1_2 -> eq_3_1 -> eq_3_5 -> eq_3_14
    assert [s.output_equation_ids for s in chain.steps] == [
        ["eq_3_1"], ["eq_3_5"], ["eq_3_14"]
    ]
    assert chain.steps[0].input_equation_ids == ["eq_1_2"]
    assert chain.steps[-1].operation == "derive_result"
    # Each step should have assumption_refs propagated
    assert all(s.assumption_refs == ["heavy_quark_limit"] for s in chain.steps)


def test_no_chains_when_no_links():
    equations = EquationSemanticsResult(
        document_id="doc_test",
        cartridge_id=None,
        equations=[_make_eq("eq_a"), _make_eq("eq_b")],
    )
    agent = DerivationChainAgent()
    result = agent.run(equations)
    assert result.chains == []


def test_required_claim_ids_propagated():
    equations = EquationSemanticsResult(
        document_id="doc_test",
        cartridge_id=None,
        equations=[
            _make_eq("eq_1"),
            _make_eq("eq_2", from_eqs=["eq_1"], role="equation_result"),
        ],
    )
    agent = DerivationChainAgent()
    result = agent.run(equations, claim_link_index={"eq_2": ["claim_xyz"]})
    assert result.chains[0].steps[0].required_claim_ids == ["claim_xyz"]


def test_teaching_takeaway_is_generated():
    equations = EquationSemanticsResult(
        document_id="doc_test",
        cartridge_id=None,
        equations=[
            _make_eq("eq_1"),
            _make_eq("eq_2", from_eqs=["eq_1"], role="equation_result"),
        ],
    )
    agent = DerivationChainAgent()
    result = agent.run(equations)
    assert result.chains[0].teaching_takeaway
    assert "eq_2" in result.chains[0].teaching_takeaway
    assert "eq_1" in result.chains[0].teaching_takeaway


def test_chain_missing_assumptions_emits_warning():
    def _no_assumption(eq_id, **kw):
        rec = _make_eq(eq_id, **kw)
        rec.local_assumptions = []
        return rec

    equations = EquationSemanticsResult(
        document_id="doc_test",
        cartridge_id=None,
        equations=[
            _no_assumption("eq_1"),
            _no_assumption("eq_2", from_eqs=["eq_1"], role="equation_result"),
        ],
    )
    agent = DerivationChainAgent()
    result = agent.run(equations)
    rules = {i.rule_id for i in result.validation_issues}
    assert "derivation_chain_missing_assumptions" in rules


def test_missing_claim_links_emits_warning_when_index_supplied():
    equations = EquationSemanticsResult(
        document_id="doc_test",
        cartridge_id=None,
        equations=[
            _make_eq("eq_a"),
            _make_eq("eq_b", from_eqs=["eq_a"], role="equation_result"),
        ],
    )
    agent = DerivationChainAgent()
    # claim_link_index supplied but no matching keys -> empty required_claim_ids
    result = agent.run(equations, claim_link_index={"eq_xxx": ["c1"]})
    rules = {i.rule_id for i in result.validation_issues}
    assert "derivation_chain_missing_claim_links" in rules
