"""Tests for cross-artifact claim ID canonicalization (issue #340).

The claim_object_builder stage is the single source of truth for claim IDs.
Provisional ``claim_span_*`` refs left on equation_semantics / derivation_chain
must be re-mapped to the final claim ID or dropped (and reported), never kept.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from episteme_graph.agents.id_canonicalization import (
    canonical_claim_id_set,
    canonicalize_derivation_claim_refs,
    canonicalize_equation_claim_links,
    filter_claim_ref_list,
)
from episteme_graph.agents.derivation_chain.schema import (
    DerivationChainRecord,
    DerivationChainResult,
    DerivationStep,
)
from episteme_graph.agents.equation_semantics.schema import (
    EquationConfidencePolicy,
    EquationReconstruction,
    EquationRecord,
    EquationSemantics,
    EquationSemanticsResult,
    EquationSourceExtraction,
)


# ---------------------------------------------------------------------------
# Minimal claim_objects stub
# ---------------------------------------------------------------------------

@dataclass
class _Claim:
    claim_id: str
    source_span_ids: list = field(default_factory=list)
    section_id: str | None = None


@dataclass
class _ClaimObjects:
    claims: list = field(default_factory=list)


def _eq(eq_id: str, linked_claim_ids: list[str]) -> EquationRecord:
    src = EquationSourceExtraction(
        raw_text="x = y",
        latex=None,
        plain_text=None,
        source_location={"block_id": f"blk_{eq_id}", "section_id": "sec_1"},
        extraction_source="pdf_text_layer",
        extraction_status="complete",
        needs_math_review=False,
        review_reason=[],
    )
    rec = EquationReconstruction.make_none()
    sem = EquationSemantics(
        equation_type="relation",
        secondary_types=[],
        semantic_status="source_backed",
        confidence=0.9,
        reason="",
        defined_symbols=[],
        used_symbols=[],
        assumptions=[],
        input_equation_ids=[],
        output_equation_ids=[],
        linked_text_spans=[],
        source_evidence_ids=[],
        linked_claim_ids=list(linked_claim_ids),
        summary="",
        review_flags=[],
    )
    cp = EquationConfidencePolicy.derive(src, rec, sem)
    return EquationRecord(
        equation_id=eq_id,
        document_id="doc",
        label=eq_id,
        candidate_trace_ids=[],
        source_extraction=src,
        reconstruction=rec,
        semantics=sem,
        confidence_policy=cp,
    )


def _equations(*records: EquationRecord) -> EquationSemanticsResult:
    return EquationSemanticsResult(
        document_id="doc",
        cartridge_id=None,
        equation_candidates=[],
        equations=list(records),
    )


def _derivations(*steps: DerivationStep) -> DerivationChainResult:
    chain = DerivationChainRecord(
        derivation_id="derivation_eq_2",
        document_id="doc",
        source_section_ids=["sec_1"],
        steps=list(steps),
    )
    return DerivationChainResult(document_id="doc", cartridge_id=None, chains=[chain])


# ---------------------------------------------------------------------------
# filter_claim_ref_list / canonical_claim_id_set
# ---------------------------------------------------------------------------

def test_canonical_claim_id_set():
    objs = _ClaimObjects(claims=[_Claim("claim_a"), _Claim("claim_b")])
    assert canonical_claim_id_set(objs) == {"claim_a", "claim_b"}


def test_filter_drops_unresolved_refs():
    objs = _ClaimObjects(claims=[_Claim("claim_real")])
    kept, dropped = filter_claim_ref_list(
        ["claim_real", "claim_span_001_5_sub07"], objs
    )
    assert kept == ["claim_real"]
    assert dropped == ["claim_span_001_5_sub07"]


def test_filter_remaps_span_provisional_to_final():
    # A provisional ref keyed by span_id resolves to the final claim ID.
    objs = _ClaimObjects(
        claims=[_Claim("claim_final", source_span_ids=["span_001"], section_id="sec_1")]
    )
    kept, dropped = filter_claim_ref_list(["claim_span_001"], objs)
    assert kept == ["claim_final"]
    assert dropped == []


def test_filter_none_claim_objects_is_noop():
    kept, dropped = filter_claim_ref_list(["claim_span_001"], None)
    assert kept == ["claim_span_001"]
    assert dropped == []


def test_filter_empty_claims_drops_everything():
    objs = _ClaimObjects(claims=[])
    kept, dropped = filter_claim_ref_list(
        ["claim_span_001_5_sub07", "claim_span_001_5_sub08"], objs
    )
    assert kept == []
    assert dropped == ["claim_span_001_5_sub07", "claim_span_001_5_sub08"]


# ---------------------------------------------------------------------------
# Equation canonicalization (case 1 / 3: empty / unresolved)
# ---------------------------------------------------------------------------

def test_equation_links_stripped_when_claims_empty():
    equations = _equations(_eq("eq_tex_b62", ["claim_span_001_5_sub07", "claim_span_001_5_sub08"]))
    objs = _ClaimObjects(claims=[])

    dropped = canonicalize_equation_claim_links(equations, objs)

    assert equations.equations[0].semantics.linked_claim_ids == []
    assert dropped == [{
        "equation_id": "eq_tex_b62",
        "dropped_claim_ids": ["claim_span_001_5_sub07", "claim_span_001_5_sub08"],
    }]


def test_equation_links_remapped_when_resolvable():
    equations = _equations(_eq("eq_1", ["claim_span_001"]))
    objs = _ClaimObjects(
        claims=[_Claim("claim_final", source_span_ids=["span_001"], section_id="sec_1")]
    )

    dropped = canonicalize_equation_claim_links(equations, objs)

    assert equations.equations[0].semantics.linked_claim_ids == ["claim_final"]
    assert dropped == []


# ---------------------------------------------------------------------------
# Derivation canonicalization (case 1 / 3)
# ---------------------------------------------------------------------------

def test_derivation_claim_refs_stripped_when_claims_empty():
    step = DerivationStep(
        step_id="step_001",
        input_equation_ids=["eq_tex_b62"],
        operation="derive_result",
        output_equation_ids=["eq_tex_b62"],
        required_claim_ids=["claim_span_001_5_sub07", "claim_span_001_5_sub08"],
        input_claim_ids=[],
        output_claim_ids=["claim_span_001_5_sub08"],
    )
    derivations = _derivations(step)
    objs = _ClaimObjects(claims=[])

    dropped = canonicalize_derivation_claim_refs(derivations, objs)

    s = derivations.chains[0].steps[0]
    assert s.required_claim_ids == []
    assert s.output_claim_ids == []
    assert len(dropped) == 1
    assert dropped[0]["derivation_id"] == "derivation_eq_2"
    assert set(dropped[0]["dropped_claim_ids"]) == {
        "claim_span_001_5_sub07",
        "claim_span_001_5_sub08",
    }


def test_derivation_claim_refs_remapped_when_resolvable():
    step = DerivationStep(
        step_id="step_001",
        input_equation_ids=["eq_1"],
        operation="derive_result",
        output_equation_ids=["eq_2"],
        required_claim_ids=["claim_span_001"],
        output_claim_ids=["claim_span_001"],
    )
    derivations = _derivations(step)
    objs = _ClaimObjects(
        claims=[_Claim("claim_final", source_span_ids=["span_001"], section_id="sec_1")]
    )

    dropped = canonicalize_derivation_claim_refs(derivations, objs)

    s = derivations.chains[0].steps[0]
    assert s.required_claim_ids == ["claim_final"]
    assert s.output_claim_ids == ["claim_final"]
    assert dropped == []
