"""Tests for the atomic-claim context-dependency lint + retry (issue #357)."""
from __future__ import annotations

from unittest.mock import patch

from episteme_graph.agents.claim_qualification.agent import ClaimQualificationAgent
from episteme_graph.agents.claim_qualification.context_lint import (
    apply_context_lint,
    extract_context_refs,
    find_unresolved_reference,
    unresolved_fragments,
)
from episteme_graph.agents.claim_qualification.repair import _parse_record
from episteme_graph.agents.claim_qualification.schema import (
    ClaimQualificationResult,
    QualificationLLMInput,
    QualifiedSpanRecord,
)
from episteme_graph.agents.claim_qualification.validator import (
    ClaimQualificationValidator,
)
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


# ---------------------------------------------------------------------------
# find_unresolved_reference
# ---------------------------------------------------------------------------

def test_detects_demonstrative_plus_discourse_noun():
    assert find_unresolved_reference("This result shows the rate vanishes.")
    assert find_unresolved_reference("These findings imply a lower bound.")


def test_detects_bare_demonstrative_subject():
    assert find_unresolved_reference("This shows the bias is small.")
    assert find_unresolved_reference("Those were computed at leading order.")


def test_detects_bare_pronoun_subject():
    assert find_unresolved_reference("It implies the sum rule holds.")
    assert find_unresolved_reference("They are suppressed by the mass ratio.")


def test_detects_former_latter_and_as_shown_above():
    assert find_unresolved_reference("The latter dominates the error budget.")
    assert find_unresolved_reference("As shown above, the integral converges.")


def test_detects_japanese_leading_anaphora():
    assert find_unresolved_reference("この結果はバイアスが小さいことを示す。")
    assert find_unresolved_reference("上記の関係式は線形近似で成り立つ。")


def test_self_contained_claims_are_not_flagged():
    assert find_unresolved_reference(
        "The bias parameter measures clustering strength."
    ) is None
    assert find_unresolved_reference(
        "The sum rule relates the three decay ratios."
    ) is None


def test_paper_self_reference_is_allowed():
    assert find_unresolved_reference("This paper shows the rate vanishes.") is None
    assert find_unresolved_reference("This work derives a new bound.") is None
    assert find_unresolved_reference("The present study confirms the relation.") is None


def test_mid_sentence_demonstrative_is_not_flagged():
    assert find_unresolved_reference(
        "The fit shows that this bias parameter is small."
    ) is None


def test_demonstrative_with_concrete_noun_is_not_flagged():
    # "This bias parameter…" names a concrete subject — not a discourse noun.
    assert find_unresolved_reference(
        "This bias parameter controls the clustering amplitude."
    ) is None


# ---------------------------------------------------------------------------
# extract_context_refs
# ---------------------------------------------------------------------------

def test_equation_refs_are_normalized_to_eq_ids():
    refs = extract_context_refs("Combining Eq. (2.7) with Eq. (3.1) gives the bound.")
    assert "eq_2_7" in refs
    assert "eq_3_1" in refs


def test_section_figure_table_refs_are_structured():
    refs = extract_context_refs(
        "The method of Section 3 is validated in Fig. 2 and Table 1."
    )
    assert "Section 3" in refs
    assert "Fig 2" in refs
    assert "Table 1" in refs


def test_document_refs_do_not_demote_the_claim():
    claims = [{
        "text": "The bound from Eq. (2.7) excludes the scalar scenario.",
        "normalized_text": "The bound from Eq. (2.7) excludes the scalar scenario.",
        "atomicity": "atomic",
        "status": "accepted",
        "qualification_reason": "",
    }]
    fragments = apply_context_lint(claims)
    assert fragments == []
    assert claims[0]["status"] == "accepted"
    assert "eq_2_7" in claims[0]["context_refs"]


# ---------------------------------------------------------------------------
# apply_context_lint
# ---------------------------------------------------------------------------

def test_accepted_atomic_claim_with_anaphora_is_demoted():
    claims = [{
        "text": "This result shows the rate vanishes.",
        "normalized_text": "This result shows the rate vanishes.",
        "atomicity": "atomic",
        "status": "accepted",
        "qualification_reason": "",
    }]
    fragments = apply_context_lint(claims)
    assert fragments
    assert claims[0]["status"] == "review_required"
    assert "unresolved_reference" in claims[0]["qualification_reason"]


def test_non_atomic_claims_are_not_demoted_again():
    claims = [{
        "text": "This result shows X and also Y.",
        "normalized_text": "This result shows X and also Y.",
        "atomicity": "non_atomic",
        "status": "review_required",
        "qualification_reason": "multiple propositions",
    }]
    fragments = apply_context_lint(claims)
    assert fragments == []
    assert claims[0]["qualification_reason"] == "multiple propositions"


def test_parse_record_applies_lint():
    llm_input = QualificationLLMInput(
        document_id="doc", cartridge_id=None, span_id="s1", block_id="b1",
        section_id="sec_1", section_title=None, backbone_block_type=None,
        headline_claim=None, span_text="span", role_labels=["relation"],
        is_claim_candidate=True, is_reject_candidate=False,
        prev_span_text=None, next_span_text=None, source_block_text=None,
    )
    raw = {
        "span_id": "s1", "block_id": "b1", "section_id": "sec_1",
        "text": "span", "role_labels": ["relation"],
        "qualification": {
            "status": "accepted", "claim_tier": "paper_core",
            "claim_type_candidate": "relation", "granularity": "too_broad",
            "evidence_adequacy": "sufficient", "reviewability": "good",
        },
        "edit_suggestions": {"should_split": True},
        "atomic_claims": [
            {"text": "It implies the sum rule holds.", "atomicity": "atomic",
             "status": "accepted", "source_span_id": "s1",
             "evidence_quote": "implies the sum rule"},
            {"text": "The decay width is suppressed.", "atomicity": "atomic",
             "status": "accepted", "source_span_id": "s1",
             "evidence_quote": "width is suppressed"},
        ],
        "reason": "r", "confidence": 0.9,
    }
    record = _parse_record(raw, llm_input)
    statuses = [c["status"] for c in record.atomic_claims]
    assert statuses == ["review_required", "accepted"]
    assert unresolved_fragments(record.atomic_claims)


# ---------------------------------------------------------------------------
# validator safety net
# ---------------------------------------------------------------------------

def test_validator_warns_on_unresolved_reference_in_reloaded_artifact():
    record = QualifiedSpanRecord(
        span_id="s1", block_id="b1", section_id="sec_1",
        text="span", role_labels=["relation"],
        qualification={
            "status": "accepted", "claim_tier": "paper_core",
            "claim_type_candidate": "relation", "granularity": "good",
            "evidence_adequacy": "sufficient", "reviewability": "good",
        },
        edit_suggestions={},
        reason="r", confidence=0.9,
        atomic_claims=[{
            "text": "This result shows the rate vanishes.",
            "atomicity": "atomic", "status": "accepted",
            "source_span_id": "s1", "evidence_quote": "rate vanishes",
        }],
    )
    result = ClaimQualificationResult(
        "doc", None, [record], [], [], {"accepted": 1},
    )
    issues = ClaimQualificationValidator().validate(result)
    assert any(i.rule_id == "atomic_claim_unresolved_reference" for i in issues)
    assert all(
        i.severity == "warning"
        for i in issues if i.rule_id == "atomic_claim_unresolved_reference"
    )


# ---------------------------------------------------------------------------
# agent retry (single shot)
# ---------------------------------------------------------------------------

def _structure():
    block = TypedBlock("b1", 1, 0, "The sum rule implies the rate vanishes.", "body_paragraph")
    block.section_id = "sec_1"
    return DocumentStructureResult(
        document_id="doc_test",
        source_file="/tmp/test.pdf",
        cartridge_id=None,
        metadata=DocumentMetadata(title="Test", pages=1),
        sections=[Section("sec_1", "Results", 1, 1, 1)],
        blocks=[block],
    )


def _skeleton():
    return PaperSkeletonResult(
        document_id="doc_test",
        skeleton_version=SKELETON_VERSION,
        cartridge_id=None,
        paper_goal={"text": "goal", "evidence_block_ids": ["b1"], "reason": "", "confidence": 0.8},
        central_question={"text": "q", "evidence_block_ids": ["b1"], "reason": "", "confidence": 0.8},
        headline_claim={"text": "h", "evidence_block_ids": ["b1"], "reason": "", "confidence": 0.8},
        supporting_subclaims=[],
        logical_blocks=[LogicalBlock("l1", "result", "Results", ["sec_1"], ["b1"], "s", "r", 0.8)],
        excluded_regions=[],
        review_notes=[],
        confidence=0.8,
    )


def _roles():
    span = SpanAnnotation(
        span_id="s1", text="The sum rule implies the rate vanishes.",
        char_start=0, char_end=39, role_labels=["relation"],
        is_claim_candidate=True, is_reject_candidate=False,
        confidence=0.9, reason="r",
    )
    return RhetoricalRoleResult(
        document_id="doc_test", cartridge_id=None,
        role_annotations=[BlockRoleAnnotation("b1", "sec_1", "result", [span])],
        summary_stats={},
    )


def _llm_response(atomic_text):
    return {
        "span_id": "s1", "block_id": "b1", "section_id": "sec_1",
        "text": "The sum rule implies the rate vanishes.",
        "role_labels": ["relation"],
        "qualification": {
            "status": "accepted", "claim_tier": "paper_core",
            "claim_type_candidate": "relation", "granularity": "too_broad",
            "evidence_adequacy": "sufficient", "reviewability": "good",
        },
        "edit_suggestions": {"should_split": True},
        "atomic_claims": [{
            "text": atomic_text, "atomicity": "atomic", "status": "accepted",
            "source_span_id": "s1", "evidence_quote": "the rate vanishes",
        }],
        "reason": "mocked", "confidence": 0.9,
    }


def test_agent_retries_once_and_accepts_resolved_rewrite():
    agent = ClaimQualificationAgent()
    responses = [
        _llm_response("This result shows the rate vanishes."),  # unresolved
        _llm_response("The decay rate vanishes in the heavy-quark limit."),  # fixed
    ]
    with patch.object(agent._llm_client, "generate", side_effect=responses) as mocked:
        result = agent.run(_structure(), _skeleton(), _roles())
    assert mocked.call_count == 2  # initial + one retry, no loop
    claim = result.qualified_spans[0].atomic_claims[0]
    assert claim["status"] == "accepted"
    assert claim["text"].startswith("The decay rate")


def test_agent_keeps_demotion_when_retry_still_unresolved():
    agent = ClaimQualificationAgent()
    responses = [
        _llm_response("This result shows the rate vanishes."),
        _llm_response("It implies the rate vanishes."),  # still unresolved
    ]
    with patch.object(agent._llm_client, "generate", side_effect=responses) as mocked:
        result = agent.run(_structure(), _skeleton(), _roles())
    assert mocked.call_count == 2  # exactly one retry, then stop
    claim = result.qualified_spans[0].atomic_claims[0]
    assert claim["status"] == "review_required"
    assert "unresolved_reference" in claim["qualification_reason"]


def test_agent_keeps_demotion_when_retry_raises():
    agent = ClaimQualificationAgent()
    responses = [
        _llm_response("This result shows the rate vanishes."),
        RuntimeError("LLM down"),
    ]
    with patch.object(agent._llm_client, "generate", side_effect=responses):
        result = agent.run(_structure(), _skeleton(), _roles())
    claim = result.qualified_spans[0].atomic_claims[0]
    assert claim["status"] == "review_required"


def test_agent_rejects_retry_that_drops_atomic_claims():
    """repair が atomic claim を消した場合は採用しない（#357 レビュー対応）。"""
    agent = ClaimQualificationAgent()
    bad = _llm_response("This result shows the rate vanishes.")
    dropped = _llm_response("placeholder")
    dropped["atomic_claims"] = []  # retry silently drops the claims
    with patch.object(agent._llm_client, "generate", side_effect=[bad, dropped]):
        result = agent.run(_structure(), _skeleton(), _roles())
    claim = result.qualified_spans[0].atomic_claims[0]
    # 元の降格済み record を保持（情報を落とさない）
    assert claim["status"] == "review_required"
    assert "unresolved_reference" in claim["qualification_reason"]
    assert len(result.qualified_spans[0].atomic_claims) == 1
