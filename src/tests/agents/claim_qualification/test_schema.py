"""Tests for claim qualification schema helpers."""
import json

from episteme_graph.agents.claim_qualification.schema import (
    ClaimQualificationResult,
    QualifiedSpanRecord,
)


def test_to_json_round_trip():
    record = QualifiedSpanRecord(
        span_id="s1",
        block_id="b1",
        section_id="sec_1",
        text="We assume X.",
        role_labels=["assumption"],
        qualification={
            "status": "accepted",
            "claim_tier": "paper_core",
            "claim_type_candidate": "assumption",
            "granularity": "good",
            "evidence_adequacy": "sufficient",
            "reviewability": "good",
        },
        edit_suggestions={
            "should_split": False,
            "should_merge_with_prev": False,
            "should_merge_with_next": False,
            "normalized_text_hint": "",
        },
        reason="good",
        confidence=0.9,
    )
    result = ClaimQualificationResult(
        document_id="doc",
        cartridge_id=None,
        qualified_spans=[record],
        rejected_spans=[],
        deferred_spans=[],
        summary_stats={"accepted": 1},
    )
    raw = result.to_json()
    loaded = json.loads(raw)
    restored = ClaimQualificationResult.from_dict(loaded)
    assert restored.qualified_spans[0].qualification["status"] == "accepted"
    assert restored.qualified_spans[0].atomic_claims == []


def test_atomic_claims_round_trip():
    record = QualifiedSpanRecord(
        span_id="s1",
        block_id="b1",
        section_id="sec_1",
        text="The paper derives X and shows Y.",
        role_labels=["result"],
        qualification={
            "status": "accepted",
            "claim_tier": "paper_core",
            "claim_type_candidate": "main_result",
            "granularity": "too_broad",
            "evidence_adequacy": "sufficient",
            "reviewability": "good",
        },
        edit_suggestions={"should_split": True},
        reason="paper-level summary; rewritten",
        confidence=0.85,
        atomic_claims=[
            {
                "text": "The paper derives X.",
                "normalized_text": "The paper derives X.",
                "claim_type_candidate": "derivation_result",
                "atomicity": "atomic",
                "status": "accepted",
                "source_span_id": "s1",
                "evidence_quote": "derives X",
                "qualification_reason": "single proposition",
                "confidence": 0.9,
            }
        ],
    )
    result = ClaimQualificationResult(
        document_id="doc",
        cartridge_id=None,
        qualified_spans=[record],
        rejected_spans=[],
        deferred_spans=[],
        summary_stats={"accepted": 1, "atomic_claims": 1},
    )
    restored = ClaimQualificationResult.from_dict(json.loads(result.to_json()))
    atomic = restored.qualified_spans[0].atomic_claims
    assert len(atomic) == 1
    assert atomic[0]["atomicity"] == "atomic"
    assert atomic[0]["source_span_id"] == "s1"
