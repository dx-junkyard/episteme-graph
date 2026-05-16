"""Tests for ClaimQualificationInputBuilder."""
from episteme_graph.agents.claim_qualification.input_builder import ClaimQualificationInputBuilder
from episteme_graph.agents.claim_qualification.schema import CartridgeContext
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

BUILDER = ClaimQualificationInputBuilder()


def _structure():
    block = TypedBlock("b1", 1, 0, "We assume X. Figure 1 shows Y.", "body_paragraph")
    block.section_id = "sec_1"
    return DocumentStructureResult(
        document_id="doc",
        source_file="/tmp/test.pdf",
        cartridge_id=None,
        metadata=DocumentMetadata(title="Test", pages=1),
        sections=[Section("sec_1", "Assumptions", 1, 1, 1)],
        blocks=[block],
    )


def _skeleton():
    return PaperSkeletonResult(
        document_id="doc",
        skeleton_version=SKELETON_VERSION,
        cartridge_id=None,
        paper_goal={"text": "goal", "evidence_block_ids": ["b1"], "reason": "", "confidence": 0.8},
        central_question={"text": "question", "evidence_block_ids": ["b1"], "reason": "", "confidence": 0.8},
        headline_claim={"text": "headline", "evidence_block_ids": ["b1"], "reason": "", "confidence": 0.8},
        supporting_subclaims=[],
        logical_blocks=[LogicalBlock("l1", "assumptions", "Assumptions", ["sec_1"], ["b1"], "summary", "reason", 0.8)],
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
        reason="mock",
    )


def _roles():
    annotation = BlockRoleAnnotation(
        block_id="b1",
        section_id="sec_1",
        backbone_block_type="assumptions",
        span_annotations=[
            _span("s1", "We assume X.", ["assumption"], claim=True),
            _span("s2", "Figure 1 shows Y.", ["figure_narration"], claim=False, reject=True),
        ],
    )
    return RhetoricalRoleResult(
        document_id="doc",
        cartridge_id=None,
        role_annotations=[annotation],
        summary_stats={},
    )


def test_build_includes_claim_and_reject_candidates():
    inputs = BUILDER.build(_structure(), _skeleton(), _roles())
    assert [i.span_id for i in inputs] == ["s1", "s2"]
    assert inputs[0].source_block_text == "We assume X. Figure 1 shows Y."


def test_can_exclude_reject_candidates():
    inputs = BUILDER.build(
        _structure(), _skeleton(), _roles(), config={"include_reject_candidates": False}
    )
    assert [i.span_id for i in inputs] == ["s1"]


def test_context_and_headline_are_propagated():
    inputs = BUILDER.build(_structure(), _skeleton(), _roles())
    assert inputs[0].section_title == "Assumptions"
    assert inputs[0].backbone_block_type == "assumptions"
    assert inputs[0].headline_claim == "headline"
    assert inputs[0].next_span_text == "Figure 1 shows Y."
    assert inputs[1].prev_span_text == "We assume X."


def test_cartridge_terms_are_attached():
    cartridge = CartridgeContext(
        cartridge_id="test",
        ontology={},
        validation_rules={},
        aliases={"R Lambda c": ["RΛc"]},
    )
    inputs = BUILDER.build(_structure(), _skeleton(), _roles(), cartridge=cartridge)
    assert inputs[0].cartridge_id == "test"
    assert inputs[0].normalized_terms[0]["canonical"] == "R Lambda c"
