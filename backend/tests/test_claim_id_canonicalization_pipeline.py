"""Pipeline-level claim ID canonicalization contract (issue #340).

Exercises the orchestrator helpers that re-map / drop provisional claim refs on
equation_semantics and derivation_chain against the final claim set produced by
claim_object_builder, recording dropped refs as review warnings.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.document_pipeline import orchestrator
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


@dataclass
class _Claim:
    claim_id: str
    source_span_ids: list = field(default_factory=list)
    section_id: str | None = None


@dataclass
class _ClaimObjects:
    claims: list = field(default_factory=list)


def _equation(eq_id: str, linked_claim_ids: list[str]) -> EquationRecord:
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
        derivation_id="derivation_eq_tex_b62",
        document_id="doc",
        source_section_ids=["sec_1"],
        steps=list(steps),
    )
    return DerivationChainResult(document_id="doc", cartridge_id=None, chains=[chain])


# ---------------------------------------------------------------------------
# Issue #340 acceptance: claims empty → no claim_span_* left downstream
# ---------------------------------------------------------------------------

def test_equation_links_purged_when_claims_empty_and_warning_recorded():
    equations = _equations(
        _equation("eq_tex_b62", ["claim_span_001_5_sub07", "claim_span_001_5_sub08"])
    )
    claim_objects = _ClaimObjects(claims=[])

    dropped = orchestrator._canonicalize_equation_claim_links(equations, claim_objects)

    assert equations.equations[0].semantics.linked_claim_ids == []
    assert dropped
    assert any(
        i.rule_id == "unresolved_claim_ref_dropped"
        for i in equations.validation_issues
    )


def test_derivation_refs_purged_when_claims_empty_and_warning_recorded():
    step = DerivationStep(
        step_id="step_001",
        input_equation_ids=["eq_tex_b62"],
        operation="derive_result",
        output_equation_ids=["eq_tex_b62"],
        required_claim_ids=["claim_span_001_5_sub07"],
        output_claim_ids=["claim_span_001_5_sub08"],
    )
    derivations = _derivations(step)
    claim_objects = _ClaimObjects(claims=[])

    dropped = orchestrator._canonicalize_derivation_claim_refs(derivations, claim_objects)

    s = derivations.chains[0].steps[0]
    assert s.required_claim_ids == []
    assert s.output_claim_ids == []
    assert dropped
    assert any(
        i.rule_id == "unresolved_claim_ref_dropped"
        for i in derivations.validation_issues
    )


def test_provisional_refs_remapped_to_final_claim_id():
    equations = _equations(_equation("eq_1", ["claim_span_001"]))
    claim_objects = _ClaimObjects(
        claims=[_Claim("claim_final", source_span_ids=["span_001"], section_id="sec_1")]
    )

    dropped = orchestrator._canonicalize_equation_claim_links(equations, claim_objects)

    assert equations.equations[0].semantics.linked_claim_ids == ["claim_final"]
    assert dropped == []
    assert all(
        i.rule_id != "unresolved_claim_ref_dropped"
        for i in equations.validation_issues
    )
