"""IK-0803: 決定論で reject と決まる span に LLM を使わない / prior_work は reject にしない。"""
from __future__ import annotations

from unittest.mock import patch

from episteme_graph.agents.claim_qualification.agent import ClaimQualificationAgent
from episteme_graph.agents.claim_qualification.schema import (
    DETERMINISTIC_REJECT_REASON,
    is_deterministic_reject,
)
from episteme_graph.agents.rhetorical_role.schema import (
    BlockRoleAnnotation,
    RhetoricalRoleResult,
    SpanAnnotation,
    normalize_reject_flag,
)
from tests.agents.claim_qualification.test_agent import (
    _response,
    _skeleton,
    _structure,
)


def _span(span_id, text, roles, claim, reject, confidence=0.9):
    return SpanAnnotation(
        span_id=span_id, text=text, char_start=0, char_end=len(text),
        role_labels=roles, is_claim_candidate=claim, is_reject_candidate=reject,
        confidence=confidence, reason="role reason",
    )


def _roles(annotations):
    return RhetoricalRoleResult(
        document_id="doc_test", cartridge_id=None,
        role_annotations=annotations, summary_stats={},
    )


def test_formatting_reject_candidate_is_rejected_without_llm():
    agent = ClaimQualificationAgent()
    roles = _roles([
        BlockRoleAnnotation("b1", None, None, [
            _span("s1", "APS/123-QED", ["section_meta"], claim=False, reject=True, confidence=0.62),
        ]),
    ])
    with patch.object(agent._llm_client, "generate") as generate:
        result = agent.run(_structure(), _skeleton(), roles)
    generate.assert_not_called()
    assert result.summary_stats["rejected"] == 1
    assert result.summary_stats["skipped_reject_candidates"] == 1
    record = result.rejected_spans[0]
    assert record["qualification"]["status"] == "rejected"
    assert record["qualification"]["qualification_reason"] == DETERMINISTIC_REJECT_REASON
    assert record["confidence"] == 0.62
    details = result.summary_stats["coverage"]["details"]
    assert details["skipped_reject_candidates"] == [{
        "span_id": "s1", "block_id": "b1", "role_labels": ["section_meta"],
        "reason": DETERMINISTIC_REJECT_REASON,
    }]
    assert not [i for i in result.validation_issues if i.severity == "error"]


def test_mixed_spans_only_call_llm_for_claimable_ones():
    agent = ClaimQualificationAgent()
    roles = _roles([
        BlockRoleAnnotation("b1", "sec_1", "assumptions", [
            _span("s1", "We assume X.", ["assumption"], claim=True, reject=False),
        ]),
        BlockRoleAnnotation("b2", "sec_1", "meta", [
            _span("s2", "In this section we summarize.", ["meta_discourse"], claim=False, reject=True),
        ]),
    ])
    responses = [_response("s1", "b1", "We assume X.", ["assumption"], "accepted", "paper_core", "assumption")]
    with patch.object(agent._llm_client, "generate", side_effect=responses) as generate:
        result = agent.run(_structure(), _skeleton(), roles)
    assert generate.call_count == 1
    assert result.summary_stats["accepted"] == 1
    assert result.summary_stats["rejected"] == 1
    assert result.summary_stats["skipped_reject_candidates"] == 1


def test_prior_work_reject_candidate_is_not_deterministically_rejected():
    # 旧 artifact（prior_work に reject 印が残る）でも決定論 reject にしない。
    assert not is_deterministic_reject(["prior_work", "citation_context"], False, True)
    assert not is_deterministic_reject(["figure_narration"], False, True)
    assert is_deterministic_reject(["citation_context"], False, True)
    agent = ClaimQualificationAgent()
    roles = _roles([
        BlockRoleAnnotation("b3", "sec_1", "background", [
            _span("s3", "Reference [6] employed the BGL approach.", ["prior_work", "citation_context"], claim=False, reject=True),
        ]),
    ])
    responses = [_response("s3", "b3", "Reference [6] employed the BGL approach.", ["prior_work"], "deferred", "prior_work", "prior_work")]
    with patch.object(agent._llm_client, "generate", side_effect=responses) as generate:
        result = agent.run(_structure(), _skeleton(), roles)
    assert generate.call_count == 1
    assert result.summary_stats["rejected"] == 0
    assert result.summary_stats["skipped_reject_candidates"] == 0


def test_prior_work_without_flags_stays_in_population():
    # rhetorical_role が reject 印を外した prior_work span も採否判定に残す（情報を落とさない）。
    flag, changed = normalize_reject_flag(["prior_work", "citation_context"], True)
    assert (flag, changed) == (False, True)
    assert normalize_reject_flag(["section_meta"], True) == (True, False)
    agent = ClaimQualificationAgent()
    roles = _roles([
        BlockRoleAnnotation("b3", "sec_1", "background", [
            _span("s3", "Reference [6] employed the BGL approach.", ["prior_work"], claim=False, reject=False),
        ]),
    ])
    responses = [_response("s3", "b3", "Reference [6] employed the BGL approach.", ["prior_work"], "accepted", "prior_work", "prior_work")]
    with patch.object(agent._llm_client, "generate", side_effect=responses) as generate:
        result = agent.run(_structure(), _skeleton(), roles)
    assert generate.call_count == 1
    assert result.qualified_spans[0].qualification["claim_tier"] == "prior_work"
