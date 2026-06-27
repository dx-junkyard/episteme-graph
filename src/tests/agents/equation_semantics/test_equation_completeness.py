"""Candidate→EquationRecord completeness regression tests (#416).

Accepted/provisional equation candidates must always resolve to a final
EquationRecord; a candidate that produced no record is recovered as a reasoned
provisional record (never silently dropped), and any unresolved
``accepted_equation_id`` is a hard validation error.
"""
from episteme_graph.agents.equation_semantics.agent import (
    EquationSemanticsAgent,
    _make_provisional_record,
)
from episteme_graph.agents.equation_semantics.schema import (
    EquationCandidate,
    EquationSemanticsResult,
)
from episteme_graph.agents.equation_semantics.validator import (
    EquationSemanticsValidator,
)

VALIDATOR = EquationSemanticsValidator()


def _candidate(cid, acc_status="accepted", accepted_equation_id=None, block_id="blk_1"):
    return EquationCandidate(
        candidate_id=cid,
        document_id="doc",
        source_location={"page": 1, "section_id": "s1", "block_id": block_id, "bbox": []},
        raw_text="N = a + b (1)",
        matched_label="1",
        detection_method=["document_structure_equation_block"],
        candidate_score=1.0,
        extraction_status="partial",
        acceptance_status=acc_status,
        accepted_equation_id=accepted_equation_id,
        needs_math_review=True,
        review_reason=[],
    )


# ---------------------------------------------------------------------------
# Validator: hard error on unresolved accepted_equation_id
# ---------------------------------------------------------------------------

def test_accepted_candidate_without_equation_id_is_hard_error():
    candidate = _candidate("cand_1", acc_status="accepted", accepted_equation_id=None)
    result = EquationSemanticsResult("doc", None, [candidate], [])
    issues = VALIDATOR.validate(result)
    assert any(
        i.rule_id == "accepted_candidate_missing_equation" and i.severity == "error"
        for i in issues
    )


def test_accepted_candidate_with_dangling_equation_id_is_hard_error():
    candidate = _candidate("cand_1", acc_status="accepted", accepted_equation_id="eq_ghost")
    # A record exists, but not the one the candidate points at.
    record = _make_provisional_record(_candidate("cand_other"))
    result = EquationSemanticsResult("doc", None, [candidate], [record])
    issues = VALIDATOR.validate(result)
    assert any(
        i.rule_id == "accepted_candidate_missing_equation" and i.severity == "error"
        for i in issues
    )


def test_provisional_candidate_must_resolve_to_record():
    candidate = _candidate("cand_1", acc_status="provisional", accepted_equation_id=None)
    result = EquationSemanticsResult("doc", None, [candidate], [])
    issues = VALIDATOR.validate(result)
    assert any(i.rule_id == "accepted_candidate_missing_equation" for i in issues)


def test_resolved_candidate_has_no_completeness_error():
    candidate = _candidate("cand_1", acc_status="accepted")
    record = _make_provisional_record(candidate)  # sets accepted_equation_id
    result = EquationSemanticsResult("doc", None, [candidate], [record])
    issues = VALIDATOR.validate(result)
    assert not any(
        i.rule_id == "accepted_candidate_missing_equation" for i in issues
    )


def test_rejected_candidate_is_not_required_to_resolve():
    # rejected / context_only candidates never need a record.
    for status in ("rejected", "needs_merge", "context_only"):
        candidate = _candidate("cand_x", acc_status=status, accepted_equation_id=None)
        result = EquationSemanticsResult("doc", None, [candidate], [])
        issues = VALIDATOR.validate(result)
        assert not any(
            i.rule_id == "accepted_candidate_missing_equation" for i in issues
        ), status


# ---------------------------------------------------------------------------
# Agent recovery: dropped candidates become reasoned provisional records
# ---------------------------------------------------------------------------

def test_recover_dropped_candidate_creates_provisional_record():
    # A reconstructable accepted candidate that produced no record (its source
    # block could not be resolved) is recovered, not silently dropped (#416).
    dropped = _candidate("cand_dropped", acc_status="accepted", accepted_equation_id=None)
    records = []
    EquationSemanticsAgent._recover_dropped_candidates([dropped], records)

    assert len(records) == 1
    assert dropped.accepted_equation_id == records[0].equation_id
    assert "candidate_dropped_before_record" in dropped.review_reason
    # The recovered record is a non-source-backed provisional record.
    assert records[0].confidence_policy.can_support_claim is False


def test_recovery_then_validation_is_complete():
    dropped = _candidate("cand_dropped", acc_status="provisional", accepted_equation_id=None)
    records = []
    EquationSemanticsAgent._recover_dropped_candidates([dropped], records)
    result = EquationSemanticsResult("doc", None, [dropped], records)
    issues = VALIDATOR.validate(result)
    assert not any(
        i.rule_id == "accepted_candidate_missing_equation" for i in issues
    )


def test_already_resolved_candidate_is_not_duplicated():
    candidate = _candidate("cand_ok", acc_status="accepted")
    record = _make_provisional_record(candidate)  # sets accepted_equation_id
    records = [record]
    EquationSemanticsAgent._recover_dropped_candidates([candidate], records)
    # No duplicate record was created.
    assert len(records) == 1


def test_candidate_registered_only_via_trace_is_not_duplicated():
    # A record traces the candidate via candidate_trace_ids even though the
    # candidate's own accepted_equation_id was never written back. The recovery
    # must treat it as registered (same ID-set definition as the export gate)
    # and not create a duplicate provisional record (#431).
    candidate = _candidate("cand_traced", acc_status="accepted", accepted_equation_id=None)
    record = _make_provisional_record(_candidate("cand_other"))
    record.candidate_trace_ids = ["cand_traced"]
    records = [record]
    EquationSemanticsAgent._recover_dropped_candidates([candidate], records)
    assert len(records) == 1


def test_recovery_makes_accepted_subset_of_registry():
    # After recovery, the accepted-candidate set is a subset of the registry by
    # construction: every accepted candidate is traceable to a record. Asserted
    # via set inclusion, not formula names or counts (#431).
    accepted = [
        _candidate("cand_1", acc_status="accepted", accepted_equation_id=None),
        _candidate("cand_2", acc_status="accepted", accepted_equation_id=None),
    ]
    records = []
    EquationSemanticsAgent._recover_dropped_candidates(accepted, records)

    registry_equation_ids = {r.equation_id for r in records}
    traced = {cid for r in records for cid in r.candidate_trace_ids}
    accepted_ids = {c.candidate_id for c in accepted}
    registered = traced | {
        c.candidate_id for c in accepted
        if c.accepted_equation_id in registry_equation_ids
    }
    assert accepted_ids - registered == set()


# ---------------------------------------------------------------------------
# Silent shrink detection
# ---------------------------------------------------------------------------

def test_large_silent_shrink_is_reported():
    # Five accepted candidates but only one resolves to a record.
    candidates = [
        _candidate(f"cand_{i}", acc_status="accepted", accepted_equation_id=None)
        for i in range(5)
    ]
    resolved = _make_provisional_record(candidates[0])
    result = EquationSemanticsResult("doc", None, candidates, [resolved])
    issues = VALIDATOR.validate(result)
    assert any(i.rule_id == "equation_candidate_silent_shrink" for i in issues)
