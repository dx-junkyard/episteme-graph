"""IK-0797 / IK-0798: deterministic split points and synthesised-claim atomicity."""

from __future__ import annotations

from episteme_graph.agents.claim_object_builder.builder import ClaimObjectBuilder
from episteme_graph.agents.claim_object_builder.equation_claim_synthesis import (
    MAX_SYMBOLS_PER_ATOMIC_SYSTEM_CLAIM,
    NON_ATOMIC_SYSTEM_CLAIM_REASON,
    synthesize_equation_claims,
)
from episteme_graph.agents.derivation_chain.schema import (
    DerivationChainRecord,
    DerivationChainResult,
    DerivationStep,
)
from episteme_graph.agents.equation_semantics.schema import EquationSemanticsResult

# Verbatim shape of the radar-loop case (seq 2934).
_FOG = (
    'The resulting "Fingers-of-God" (FoG) distortions [ref] can therefore become '
    "relevant on scales where the real-space density field and the long-wavelength "
    "velocity field are still accurately described"
)


def test_no_split_after_auxiliary_and_inside_open_relative_clause():
    parts = ClaimObjectBuilder._split_into_atomic(_FOG)
    assert len(parts) == 1
    assert not any(p.rstrip(".").endswith(" can") for p in parts)
    assert "and the long-wavelength" in parts[0]
    assert ClaimObjectBuilder._analyze_atomicity(_FOG)[0] == "atomic"


def test_mid_clause_discourse_adverb_is_not_a_split_point():
    text = "The correction may thus dominate the signal on small scales."
    assert ClaimObjectBuilder._split_into_atomic(text) == [text]


def test_coordinated_finite_clauses_still_split():
    text = (
        "The paper derives consistency relations by eliminating nuisance parameters "
        "and shows they can be used to test a target theory."
    )
    parts = ClaimObjectBuilder._split_into_atomic(text)
    assert len(parts) == 2


def test_verb_less_fragment_is_merged_back_with_its_connector():
    text = "The model is accurate, therefore it can be used and the bias is small."
    parts = ClaimObjectBuilder._split_into_atomic(text)
    joined = " ".join(parts).lower()
    for word in ("accurate", "used", "bias", "small"):
        assert word in joined


def _system_claim(eliminated, retained):
    chain = DerivationChainRecord(
        derivation_id="system_derivation_0001",
        document_id="doc",
        source_section_ids=[],
        steps=[DerivationStep(step_id="s1", input_equation_ids=["eq_1"], operation="solve", output_equation_ids=["eq_2"])],
        chain_type="system_level",
        operation="solve",
        input_equation_ids=["eq_1"],
        output_equation_ids=["eq_2"],
        eliminated_symbols=eliminated,
        retained_symbols=retained,
        # Evidence volume must not matter (IK-0798): give it many ids either way.
        source_evidence_ids=[f"ev_{i:04d}" for i in range(150)],
    )
    eqs = EquationSemanticsResult(
        document_id="doc", cartridge_id=None, equation_candidates=[], equations=[], validation_issues=[],
    )
    claims = synthesize_equation_claims(
        eqs, derivations=DerivationChainResult(document_id="doc", cartridge_id=None, chains=[chain])
    )
    return [c for c in claims if c.claim_type == "equation_system_claim"][0]


def test_system_claim_bundling_many_symbols_is_not_atomic():
    claim = _system_claim(["D", "M", "N", "N_g"], ["W", "delta_s", "delta_g", "lambda"])
    assert claim.is_atomic is False
    assert claim.atomicity == "split_required"
    assert claim.support_status == "review_required"
    assert NON_ATOMIC_SYSTEM_CLAIM_REASON in claim.review_reasons


def test_small_system_claim_stays_atomic_regardless_of_evidence_count():
    claim = _system_claim(["b"], ["a"])
    assert claim.is_atomic is True
    assert claim.support_status == "derived_from_linked_artifacts"
    assert MAX_SYMBOLS_PER_ATOMIC_SYSTEM_CLAIM >= 2
