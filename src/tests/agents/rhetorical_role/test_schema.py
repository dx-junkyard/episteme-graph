"""Tests for RhetoricalRoleAgent schema helpers."""
import json

from episteme_graph.agents.rhetorical_role.schema import (
    BlockRoleAnnotation,
    RhetoricalRoleResult,
    SpanAnnotation,
)


def test_to_json_round_trip():
    span = SpanAnnotation(
        span_id="span_001",
        text="A relation holds.",
        char_start=0,
        char_end=len("A relation holds."),
        role_labels=["relation"],
        is_claim_candidate=True,
        is_reject_candidate=False,
        confidence=0.8,
        reason="relation statement",
    )
    result = RhetoricalRoleResult(
        document_id="doc",
        cartridge_id=None,
        role_annotations=[BlockRoleAnnotation("b1", "sec_1", "core_relation", [span])],
        summary_stats={"claim_candidate_spans": 1, "reject_candidate_spans": 0},
    )
    raw = result.to_json()
    loaded = json.loads(raw)
    restored = RhetoricalRoleResult.from_dict(loaded)
    assert restored.document_id == "doc"
    assert restored.role_annotations[0].span_annotations[0].role_labels == ["relation"]
