"""Tests for EquationLinkNormalizer (issue #432).

Inter-equation links must be derived from structural cues (shared symbols /
textual references / resolvable derivation steps), carry link_provenance, never
dangle, and result/relation equations must carry an explicit link_status. The
fixtures use two unrelated domain vocabularies and assert link *topology* and
*provenance*, never specific formula text or hardcoded equation pairs.
"""
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
from episteme_graph.agents.symbol_registry.builder import SymbolRegistryBuilder
from episteme_graph.agents.symbol_registry.link_normalizer import (
    EquationLinkNormalizer,
)


def _equation(
    equation_id,
    *,
    label=None,
    equation_type="relation",
    defined=None,
    used=None,
    raw_text="x = y",
    summary="",
    input_ids=None,
    output_ids=None,
    can_derive=True,
):
    src = EquationSourceExtraction(
        raw_text=raw_text,
        latex=None,
        plain_text=raw_text,
        source_location={"page": 1, "section_id": "s1",
                         "block_id": f"b_{equation_id}", "bbox": None},
        extraction_source="pdf_text_layer",
        extraction_status="complete",
    )
    rec = EquationReconstruction.make_none()
    sem = EquationSemantics(
        equation_type=equation_type,
        secondary_types=[],
        semantic_status="source_backed",
        confidence=0.9,
        reason="",
        defined_symbols=list(defined or []),
        used_symbols=list(used or []),
        assumptions=[],
        input_equation_ids=list(input_ids or []),
        output_equation_ids=list(output_ids or []),
        linked_text_spans=[],
        source_evidence_ids=[],
        linked_claim_ids=[],
        summary=summary,
        review_flags=[],
    )
    policy = EquationConfidencePolicy(
        can_support_claim=can_derive,
        can_be_used_in_derivation=can_derive,
        can_be_rendered_as_final_formula=can_derive,
        allowed_downstream_use="unrestricted" if can_derive else "semantic_hint_only",
        can_be_displayed_in_course=True,
        display_requires_note=not can_derive,
        must_not_treat_as_source_extracted=not can_derive,
    )
    return EquationRecord(
        equation_id=equation_id,
        document_id="doc",
        label=label,
        candidate_trace_ids=[],
        source_extraction=src,
        reconstruction=rec,
        semantics=sem,
        confidence_policy=policy,
    )


def _result(equations):
    return EquationSemanticsResult("doc", None, [], list(equations))


def _provenance(eq):
    return eq.semantics.link_provenance


# ---------------------------------------------------------------------------
# shared_symbol provenance
# ---------------------------------------------------------------------------

def test_shared_symbol_link_is_derived_with_provenance():
    definer = _equation("eq_def", defined=[DefinedSymbol("β", "defined")])
    user = _equation("eq_use", used=["β"])
    result = _result([definer, user])
    EquationLinkNormalizer().normalize(result, None)

    # definer → user (the symbol's definer is the upstream input of the user).
    assert user.semantics.input_equation_ids == ["eq_def"]
    assert definer.semantics.output_equation_ids == ["eq_use"]
    assert _provenance(user)["eq_def"] == ["shared_symbol"]


def test_shared_symbol_uses_symbol_registry_when_provided():
    definer = _equation("eq_def", defined=[DefinedSymbol("\\beta", "defined")])
    user = _equation("eq_use", used=["β"])  # latex/unicode variant resolves to same symbol
    result = _result([definer, user])
    registry = SymbolRegistryBuilder().run(result)
    EquationLinkNormalizer().normalize(result, registry)

    assert user.semantics.input_equation_ids == ["eq_def"]
    assert _provenance(user)["eq_def"] == ["shared_symbol"]


# ---------------------------------------------------------------------------
# textual_reference provenance
# ---------------------------------------------------------------------------

def test_textual_reference_link_is_derived_with_provenance():
    referenced = _equation("eq_a", label="1")
    referencing = _equation("eq_b", label="2", summary="This result follows from Eq. (1).")
    result = _result([referenced, referencing])
    EquationLinkNormalizer().normalize(result, None)

    assert referencing.semantics.input_equation_ids == ["eq_a"]
    assert _provenance(referencing)["eq_a"] == ["textual_reference"]


def test_link_can_have_multiple_provenance_kinds():
    # eq_a defines a symbol eq_b uses AND eq_b textually references eq_a's label.
    eq_a = _equation("eq_a", label="1", defined=[DefinedSymbol("γ", "defined")])
    eq_b = _equation("eq_b", label="2", used=["γ"], summary="From Eq. (1) we obtain ...")
    result = _result([eq_a, eq_b])
    EquationLinkNormalizer().normalize(result, None)

    assert eq_b.semantics.input_equation_ids == ["eq_a"]
    assert _provenance(eq_b)["eq_a"] == ["shared_symbol", "textual_reference"]


# ---------------------------------------------------------------------------
# derivation_step preservation + dangling removal
# ---------------------------------------------------------------------------

def test_resolvable_derivation_step_preserved_dangling_dropped():
    upstream = _equation("eq_up")
    downstream = _equation("eq_down", input_ids=["eq_up", "eq_ghost"])
    result = _result([upstream, downstream])
    summary = EquationLinkNormalizer().normalize(result, None)

    assert downstream.semantics.input_equation_ids == ["eq_up"]
    assert _provenance(downstream)["eq_up"] == ["derivation_step"]
    assert "eq_ghost" not in downstream.semantics.input_equation_ids
    assert summary["dangling_dropped"] >= 1


def test_no_dangling_links_after_normalization():
    eq_a = _equation("eq_a", defined=[DefinedSymbol("σ", "defined")])
    eq_b = _equation("eq_b", used=["σ"], input_ids=["eq_missing"])
    result = _result([eq_a, eq_b])
    EquationLinkNormalizer().normalize(result, None)

    registry_ids = {e.equation_id for e in result.equations}
    for eq in result.equations:
        for ref in eq.semantics.input_equation_ids + eq.semantics.output_equation_ids:
            assert ref in registry_ids


# ---------------------------------------------------------------------------
# link_status assignment
# ---------------------------------------------------------------------------

def test_link_status_derived_when_inputs_present():
    definer = _equation("eq_def", defined=[DefinedSymbol("k", "defined")])
    user = _equation("eq_use", equation_type="result", used=["k"])
    result = _result([definer, user])
    EquationLinkNormalizer().normalize(result, None)
    assert user.semantics.link_status == "derived"


def test_link_status_axiomatic_when_defines_without_upstream():
    # A result that introduces a symbol with no resolvable upstream is axiomatic.
    axiom = _equation("eq_ax", equation_type="result",
                      defined=[DefinedSymbol("Z", "defined")])
    result = _result([axiom])
    EquationLinkNormalizer().normalize(result, None)
    assert axiom.semantics.link_status == "axiomatic"


def test_link_status_external_reference_on_citation():
    cited = _equation("eq_cite", equation_type="result",
                      summary="Taken from prior work [12].")
    result = _result([cited])
    EquationLinkNormalizer().normalize(result, None)
    assert cited.semantics.link_status == "external_reference"


def test_link_status_unresolved_when_no_cue():
    orphan = _equation("eq_orphan", equation_type="result", raw_text="42")
    result = _result([orphan])
    EquationLinkNormalizer().normalize(result, None)
    assert orphan.semantics.link_status == "unresolved"


# ---------------------------------------------------------------------------
# Multi-domain reproducibility: identical structure, different vocabulary →
# identical link topology and provenance (no hardcoded equation pairs).
# ---------------------------------------------------------------------------

def _two_equation_chain(symbol):
    definer = _equation("eq_1", label="1", defined=[DefinedSymbol(symbol, "defined")])
    user = _equation("eq_2", label="2", equation_type="result", used=[symbol],
                     summary="Result obtained from Eq. (1).")
    return _result([definer, user])


def test_link_topology_reproduces_across_domains():
    physics = _two_equation_chain("β")        # domain A vocabulary
    economics = _two_equation_chain("MPC")    # domain B vocabulary

    EquationLinkNormalizer().normalize(physics, None)
    EquationLinkNormalizer().normalize(economics, None)

    def topo(res):
        return {
            e.equation_id: (
                tuple(e.semantics.input_equation_ids),
                {k: tuple(v) for k, v in e.semantics.link_provenance.items()},
                e.semantics.link_status,
            )
            for e in res.equations
        }

    # Same structure ⇒ same links / provenance / status regardless of vocabulary.
    assert topo(physics) == topo(economics)
    # And the result equation is properly linked (not "from nowhere").
    result_eq = next(e for e in physics.equations if e.equation_id == "eq_2")
    assert result_eq.semantics.input_equation_ids == ["eq_1"]
    assert result_eq.semantics.link_status == "derived"
    assert set(result_eq.semantics.link_provenance["eq_1"]) == {
        "shared_symbol", "textual_reference",
    }
