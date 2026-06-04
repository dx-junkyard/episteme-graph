"""Integration tests for ClaimQualificationAgent with mocked LLM."""
from __future__ import annotations

from unittest.mock import patch

from episteme_graph.agents.claim_qualification.agent import ClaimQualificationAgent
from episteme_graph.agents.claim_qualification.schema import ClaimQualificationResult
from episteme_graph.agents.document_structure.schema import (
    DocumentMetadata,
    DocumentStructureResult,
    Section,
    TypedBlock,
)
from episteme_graph.agents.paper_skeleton.schema import (
    LogicalBlock,
    PaperSkeletonResult,
    SKELETON_VERSION,
)
from episteme_graph.agents.rhetorical_role.schema import (
    BlockRoleAnnotation,
    RhetoricalRoleResult,
    SpanAnnotation,
)


def _typed(block_id, text, order=0):
    b = TypedBlock(block_id, 1, order, text, "body_paragraph")
    b.section_id = "sec_1"
    return b


def _structure():
    return DocumentStructureResult(
        document_id="doc_test",
        source_file="/tmp/test.pdf",
        cartridge_id=None,
        metadata=DocumentMetadata(title="Test", pages=1),
        sections=[Section("sec_1", "Results", 1, 1, 1)],
        blocks=[
            _typed("b1", "We assume X."),
            _typed("b2", "In figure 1, we show Y.", order=1),
            _typed("b3", "Reference [6] employed the BGL approach.", order=2),
        ],
    )


def _skeleton():
    return PaperSkeletonResult(
        document_id="doc_test",
        skeleton_version=SKELETON_VERSION,
        cartridge_id=None,
        paper_goal={"text": "goal", "evidence_block_ids": ["b1"], "reason": "", "confidence": 0.8},
        central_question={"text": "question", "evidence_block_ids": ["b1"], "reason": "", "confidence": 0.8},
        headline_claim={"text": "headline", "evidence_block_ids": ["b1"], "reason": "", "confidence": 0.8},
        supporting_subclaims=[],
        logical_blocks=[LogicalBlock("l1", "result", "Results", ["sec_1"], ["b1"], "summary", "reason", 0.8)],
        excluded_regions=[],
        review_notes=[],
        confidence=0.8,
    )


def _span(span_id, text, roles, claim=True, reject=False):
    return SpanAnnotation(
        span_id=span_id,
        text=text,
        char_start=0,
        char_end=len(text),
        role_labels=roles,
        is_claim_candidate=claim,
        is_reject_candidate=reject,
        confidence=0.9,
        reason="role reason",
    )


def _roles():
    annotations = [
        BlockRoleAnnotation("b1", "sec_1", "assumptions", [_span("s1", "We assume X.", ["assumption"])]),
        BlockRoleAnnotation("b2", "sec_1", "meta", [_span("s2", "In figure 1, we show Y.", ["figure_narration"], claim=False, reject=True)]),
        BlockRoleAnnotation("b3", "sec_1", "background", [_span("s3", "Reference [6] employed the BGL approach.", ["prior_work"], claim=False, reject=True)]),
    ]
    return RhetoricalRoleResult(
        document_id="doc_test",
        cartridge_id=None,
        role_annotations=annotations,
        summary_stats={},
    )


def _response(span_id, block_id, text, roles, status, tier, claim_type, granularity="good", evidence="sufficient", split=False, merge_prev=False, merge_next=False):
    return {
        "span_id": span_id,
        "block_id": block_id,
        "section_id": "sec_1",
        "text": text,
        "role_labels": roles,
        "qualification": {
            "status": status,
            "claim_tier": tier,
            "claim_type_candidate": claim_type,
            "granularity": granularity,
            "evidence_adequacy": evidence,
            "reviewability": "good" if evidence == "sufficient" else "moderate",
        },
        "edit_suggestions": {
            "should_split": split,
            "should_merge_with_prev": merge_prev,
            "should_merge_with_next": merge_next,
            "normalized_text_hint": text,
        },
        "reason": "mocked qualification",
        "confidence": 0.9,
    }


def test_run_returns_accepted_rejected_and_prior_work_separation():
    agent = ClaimQualificationAgent()
    responses = [
        _response("s1", "b1", "We assume X.", ["assumption"], "accepted", "paper_core", "assumption"),
        _response("s2", "b2", "In figure 1, we show Y.", ["figure_narration"], "rejected", "meta", "meta"),
        _response("s3", "b3", "Reference [6] employed the BGL approach.", ["prior_work"], "rejected", "prior_work", "prior_work"),
    ]
    with patch.object(agent._llm_client, "generate", side_effect=responses):
        result = agent.run(_structure(), _skeleton(), _roles())

    assert isinstance(result, ClaimQualificationResult)
    assert result.summary_stats["accepted"] == 1
    assert result.summary_stats["rejected"] == 2
    assert result.qualified_spans[0].qualification["claim_type_candidate"] == "assumption"
    assert result.rejected_spans[1]["qualification"]["claim_tier"] == "prior_work"


def test_correction_uncertainty_and_result_can_be_accepted():
    agent = ClaimQualificationAgent()
    structure = DocumentStructureResult(
        document_id="doc_test",
        source_file="/tmp/test.pdf",
        cartridge_id=None,
        metadata=DocumentMetadata(title="Test", pages=1),
        sections=[Section("sec_1", "Analysis", 1, 1, 1)],
        blocks=[
            _typed("b1", "The correction arises from the mass spectrum."),
            _typed("b2", "The dominant uncertainties stem from form factors.", order=1),
            _typed("b3", "The result constrains R Lambda c.", order=2),
        ],
    )
    roles = RhetoricalRoleResult(
        document_id="doc_test",
        cartridge_id=None,
        role_annotations=[
            BlockRoleAnnotation("b1", "sec_1", "correction", [_span("s1", "The correction arises from the mass spectrum.", ["correction"])]),
            BlockRoleAnnotation("b2", "sec_1", "uncertainty", [_span("s2", "The dominant uncertainties stem from form factors.", ["uncertainty"])]),
            BlockRoleAnnotation("b3", "sec_1", "result", [_span("s3", "The result constrains R Lambda c.", ["result"])]),
        ],
        summary_stats={},
    )
    responses = [
        _response("s1", "b1", "The correction arises from the mass spectrum.", ["correction"], "accepted", "paper_core", "correction"),
        _response("s2", "b2", "The dominant uncertainties stem from form factors.", ["uncertainty"], "accepted", "paper_supporting", "uncertainty"),
        _response("s3", "b3", "The result constrains R Lambda c.", ["result"], "accepted", "paper_core", "result"),
    ]
    with patch.object(agent._llm_client, "generate", side_effect=responses):
        result = agent.run(structure, _skeleton(), roles)

    assert result.summary_stats["accepted"] == 3
    assert [r.qualification["claim_type_candidate"] for r in result.qualified_spans] == [
        "correction",
        "uncertainty",
        "result",
    ]


def test_split_and_merge_suggestions_are_counted():
    agent = ClaimQualificationAgent()
    text = "We assume X, and therefore Y holds."
    structure = DocumentStructureResult(
        document_id="doc_test",
        source_file="/tmp/test.pdf",
        cartridge_id=None,
        metadata=DocumentMetadata(title="Test", pages=1),
        sections=[Section("sec_1", "Assumptions", 1, 1, 1)],
        blocks=[_typed("b1", text)],
    )
    roles = RhetoricalRoleResult(
        document_id="doc_test",
        cartridge_id=None,
        role_annotations=[
            BlockRoleAnnotation("b1", "sec_1", "assumptions", [_span("s1", text, ["assumption", "relation"])])
        ],
        summary_stats={},
    )
    response = _response(
        "s1",
        "b1",
        text,
        ["assumption", "relation"],
        "deferred",
        "paper_supporting",
        "assumption",
        granularity="too_broad",
        evidence="weak",
        split=True,
    )
    with patch.object(agent._llm_client, "generate", return_value=response):
        result = agent.run(structure, _skeleton(), roles)

    assert result.summary_stats["deferred"] == 1
    assert result.summary_stats["split_suggested"] == 1


def test_too_narrow_fragment_can_be_deferred_with_merge():
    agent = ClaimQualificationAgent()
    text = "In this limit,"
    structure = DocumentStructureResult(
        document_id="doc_test",
        source_file="/tmp/test.pdf",
        cartridge_id=None,
        metadata=DocumentMetadata(title="Test", pages=1),
        sections=[Section("sec_1", "Assumptions", 1, 1, 1)],
        blocks=[_typed("b1", text)],
    )
    roles = RhetoricalRoleResult(
        document_id="doc_test",
        cartridge_id=None,
        role_annotations=[
            BlockRoleAnnotation("b1", "sec_1", "assumptions", [_span("s1", text, ["unknown"], claim=False)])
        ],
        summary_stats={},
    )
    response = _response(
        "s1",
        "b1",
        text,
        ["unknown"],
        "deferred",
        "background",
        "unknown",
        granularity="too_narrow",
        evidence="weak",
        merge_next=True,
    )
    with patch.object(agent._llm_client, "generate", return_value=response):
        result = agent.run(structure, _skeleton(), roles)

    assert result.summary_stats["deferred"] == 1
    assert result.summary_stats["merge_suggested"] == 1


def test_repair_called_on_invalid_status():
    agent = ClaimQualificationAgent()
    bad = _response("s1", "b1", "We assume X.", ["assumption"], "bad", "paper_core", "assumption")
    fixed = _response("s1", "b1", "We assume X.", ["assumption"], "accepted", "paper_core", "assumption")
    second = _response("s2", "b2", "In figure 1, we show Y.", ["figure_narration"], "rejected", "meta", "meta")
    third = _response("s3", "b3", "Reference [6] employed the BGL approach.", ["prior_work"], "rejected", "prior_work", "prior_work")

    with patch.object(agent._llm_client, "generate", side_effect=[bad, fixed, second, third]) as mocked:
        result = agent.run(_structure(), _skeleton(), _roles())

    assert mocked.call_count >= 4
    assert result.qualified_spans[0].qualification["status"] == "accepted"


def test_atomic_claims_flow_through_to_qualified_span():
    # issue #317: the agent's atomic rewrite output is preserved on the record and
    # counted in summary_stats.
    agent = ClaimQualificationAgent()
    text = "The paper derives consistency relations and shows they test a theory."
    structure = DocumentStructureResult(
        document_id="doc_test",
        source_file="/tmp/test.pdf",
        cartridge_id=None,
        metadata=DocumentMetadata(title="Test", pages=1),
        sections=[Section("sec_1", "Results", 1, 1, 1)],
        blocks=[_typed("b1", text)],
    )
    roles = RhetoricalRoleResult(
        document_id="doc_test",
        cartridge_id=None,
        role_annotations=[
            BlockRoleAnnotation("b1", "sec_1", "result", [_span("s1", text, ["result"])])
        ],
        summary_stats={},
    )
    response = _response(
        "s1", "b1", text, ["result"], "accepted", "paper_core", "main_result",
        granularity="too_broad", split=True,
    )
    response["atomic_claims"] = [
        {
            "text": "The paper derives consistency relations.",
            "claim_type_candidate": "derivation_result",
            "atomicity": "atomic",
            "status": "accepted",
            "source_span_id": "s1",
            "evidence_quote": "derives consistency relations",
        },
        {
            "text": "The consistency relations can test a theory.",
            "claim_type_candidate": "main_result",
            "atomicity": "atomic",
            "status": "accepted",
            "source_span_id": "s1",
            "evidence_quote": "shows they test a theory",
        },
    ]
    with patch.object(agent._llm_client, "generate", return_value=response):
        result = agent.run(structure, _skeleton(), roles)

    span = result.qualified_spans[0]
    assert len(span.atomic_claims) == 2
    assert span.atomic_claims[0]["atomicity"] == "atomic"
    assert span.atomic_claims[0]["source_span_id"] == "s1"
    assert result.summary_stats["atomic_claims"] == 2


def test_llm_failure_returns_deferred_fallback_record():
    agent = ClaimQualificationAgent()
    with patch.object(agent._llm_client, "generate", side_effect=RuntimeError("LLM error")):
        result = agent.run(_structure(), _skeleton(), _roles(), config={"max_spans": 1})

    assert result.summary_stats["deferred"] == 1
    assert result.deferred_spans[0]["qualification"]["status"] == "deferred"
    assert result.deferred_spans[0]["confidence"] == 0.0
