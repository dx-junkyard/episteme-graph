"""Integration tests for RhetoricalRoleAgent with mocked LLM."""
from __future__ import annotations

from unittest.mock import patch

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
from episteme_graph.agents.rhetorical_role.agent import RhetoricalRoleAgent
from episteme_graph.agents.rhetorical_role.schema import RhetoricalRoleResult


def _typed(block_id, text, block_type="body_paragraph", order=0, section_id="sec_1"):
    b = TypedBlock(block_id, 1, order, text, block_type)
    b.section_id = section_id
    return b


def _structure():
    blocks = [
        _typed("b1", "We assume that NP contributes only to b -> c tau nu.", order=0),
        _typed("b2", "In figure 1, we show the probability distribution of R Lambda c.", order=1),
    ]
    return DocumentStructureResult(
        document_id="doc_test",
        source_file="/tmp/test.pdf",
        cartridge_id=None,
        metadata=DocumentMetadata(title="Test", pages=1),
        sections=[Section("sec_1", "Assumptions", 1, 1, 1)],
        blocks=blocks,
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
        logical_blocks=[
            LogicalBlock("logic_1", "assumptions", "Assumptions", ["sec_1"], ["b1"], "summary", "reason", 0.8)
        ],
        excluded_regions=[],
        review_notes=[],
        confidence=0.8,
    )


def _mock_response_for_text(text: str, block_id: str, role_labels: list[str], claim: bool, reject: bool):
    return {
        "block_id": block_id,
        "section_id": "sec_1",
        "backbone_block_type": "assumptions",
        "span_annotations": [
            {
                "span_id": "span_001",
                "text": text,
                "char_start": 0,
                "char_end": len(text),
                "role_labels": role_labels,
                "is_claim_candidate": claim,
                "is_reject_candidate": reject,
                "confidence": 0.9,
                "reason": "mocked",
            }
        ],
    }


def test_run_returns_role_result():
    agent = RhetoricalRoleAgent()
    responses = [
        _mock_response_for_text(
            "We assume that NP contributes only to b -> c tau nu.",
            "b1",
            ["assumption"],
            True,
            False,
        ),
        _mock_response_for_text(
            "In figure 1, we show the probability distribution of R Lambda c.",
            "b2",
            ["figure_narration", "meta_discourse"],
            False,
            True,
        ),
    ]
    with patch.object(agent._llm_client, "generate", side_effect=responses):
        result = agent.run(_structure(), _skeleton())

    assert isinstance(result, RhetoricalRoleResult)
    assert len(result.role_annotations) == 2
    assert result.summary_stats["claim_candidate_spans"] == 1
    assert result.summary_stats["reject_candidate_spans"] == 1


def test_assumption_and_figure_roles_are_preserved():
    agent = RhetoricalRoleAgent()
    responses = [
        _mock_response_for_text(
            "We assume that NP contributes only to b -> c tau nu.",
            "b1",
            ["assumption"],
            True,
            False,
        ),
        _mock_response_for_text(
            "In figure 1, we show the probability distribution of R Lambda c.",
            "b2",
            ["figure_narration", "meta_discourse"],
            False,
            True,
        ),
    ]
    with patch.object(agent._llm_client, "generate", side_effect=responses):
        result = agent.run(_structure(), _skeleton())

    spans = {
        a.block_id: a.span_annotations[0]
        for a in result.role_annotations
    }
    assert "assumption" in spans["b1"].role_labels
    assert spans["b1"].is_claim_candidate is True
    assert "figure_narration" in spans["b2"].role_labels
    assert spans["b2"].is_reject_candidate is True


def test_repair_called_on_invalid_role_label():
    agent = RhetoricalRoleAgent()
    text1 = "We assume that NP contributes only to b -> c tau nu."
    text2 = "In figure 1, we show the probability distribution of R Lambda c."
    bad = _mock_response_for_text(text1, "b1", ["invalid"], True, False)
    fixed = _mock_response_for_text(text1, "b1", ["assumption"], True, False)
    second = _mock_response_for_text(text2, "b2", ["figure_narration"], False, True)

    with patch.object(agent._llm_client, "generate", side_effect=[bad, fixed, second]) as mocked:
        result = agent.run(_structure(), _skeleton())

    assert mocked.call_count >= 3
    assert result.role_annotations[0].span_annotations[0].role_labels == ["assumption"]


def test_llm_failure_returns_unknown_fallback_span():
    agent = RhetoricalRoleAgent()
    with patch.object(agent._llm_client, "generate", side_effect=RuntimeError("LLM error")):
        result = agent.run(_structure(), _skeleton(), config={"max_blocks": 1})

    span = result.role_annotations[0].span_annotations[0]
    assert span.role_labels == ["unknown"]
    assert span.confidence == 0.0


def test_prior_work_and_section_meta_are_reject_candidates():
    agent = RhetoricalRoleAgent()
    structure = DocumentStructureResult(
        document_id="doc_test",
        source_file="/tmp/test.pdf",
        cartridge_id=None,
        metadata=DocumentMetadata(title="Test", pages=1),
        sections=[Section("sec_1", "Background", 1, 1, 1)],
        blocks=[
            _typed("b1", "Reference [6] employed the BGL approach for both transitions.", order=0),
            _typed("b2", "In this section, we summarize the form factor parametrizations.", order=1),
        ],
    )
    responses = [
        _mock_response_for_text(
            "Reference [6] employed the BGL approach for both transitions.",
            "b1",
            ["prior_work", "citation_context"],
            False,
            True,
        ),
        _mock_response_for_text(
            "In this section, we summarize the form factor parametrizations.",
            "b2",
            ["section_meta"],
            False,
            True,
        ),
    ]
    with patch.object(agent._llm_client, "generate", side_effect=responses):
        result = agent.run(structure, _skeleton())

    spans = {a.block_id: a.span_annotations[0] for a in result.role_annotations}
    assert "prior_work" in spans["b1"].role_labels
    assert spans["b1"].is_reject_candidate is True
    assert "section_meta" in spans["b2"].role_labels
    assert spans["b2"].is_reject_candidate is True


def test_compound_sentence_can_return_multiple_spans():
    agent = RhetoricalRoleAgent()
    text = "Starting from eq. (1.2), we derive the sum rule, and figure 1 shows its prediction for R Lambda c."
    structure = DocumentStructureResult(
        document_id="doc_test",
        source_file="/tmp/test.pdf",
        cartridge_id=None,
        metadata=DocumentMetadata(title="Test", pages=1),
        sections=[Section("sec_1", "Derivation", 1, 1, 1)],
        blocks=[_typed("b1", text, order=0)],
    )
    first = "Starting from eq. (1.2), we derive the sum rule"
    second_start = text.index("figure")
    response = {
        "block_id": "b1",
        "section_id": "sec_1",
        "backbone_block_type": "assumptions",
        "span_annotations": [
            {
                "span_id": "span_001",
                "text": first,
                "char_start": 0,
                "char_end": len(first),
                "role_labels": ["derivation_step", "equation_transformation"],
                "is_claim_candidate": True,
                "is_reject_candidate": False,
                "confidence": 0.91,
                "reason": "derivation from equation",
            },
            {
                "span_id": "span_002",
                "text": text[second_start:],
                "char_start": second_start,
                "char_end": len(text),
                "role_labels": ["figure_narration"],
                "is_claim_candidate": False,
                "is_reject_candidate": True,
                "confidence": 0.9,
                "reason": "figure narration",
            },
        ],
    }
    with patch.object(agent._llm_client, "generate", return_value=response):
        result = agent.run(structure, _skeleton())

    spans = result.role_annotations[0].span_annotations
    assert len(spans) == 2
    assert "derivation_step" in spans[0].role_labels
    assert spans[0].is_claim_candidate is True
    assert "figure_narration" in spans[1].role_labels
    assert spans[1].is_reject_candidate is True
