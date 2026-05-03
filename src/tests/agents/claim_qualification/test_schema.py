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
