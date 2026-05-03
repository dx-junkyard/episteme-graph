"""Tests for RhetoricalRoleInputBuilder."""
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
from episteme_graph.agents.rhetorical_role.input_builder import RhetoricalRoleInputBuilder
from episteme_graph.agents.rhetorical_role.schema import CartridgeContext

BUILDER = RhetoricalRoleInputBuilder()


def _typed(block_id, text, block_type, order=0, section_id="sec_1"):
    b = TypedBlock(block_id, 1, order, text, block_type)
    b.section_id = section_id
    return b


def _structure(blocks):
    return DocumentStructureResult(
        document_id="doc_test",
        source_file="/tmp/test.pdf",
        cartridge_id=None,
        metadata=DocumentMetadata(title="Test", pages=1),
        sections=[Section("sec_1", "Derivation", 1, 1, 1)],
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
            LogicalBlock("logic_1", "derivation", "Derivation", ["sec_1"], ["b1"], "summary", "reason", 0.8)
        ],
        excluded_regions=[],
        review_notes=[],
        confidence=0.8,
    )


def test_build_targets_body_paragraphs_only_by_default():
    structure = _structure([
        _typed("h1", "1 Introduction", "section_heading", order=0),
        _typed("b1", "We assume a narrow-width limit.", "body_paragraph", order=1),
        _typed("e1", "E = mc^2", "equation_block", order=2),
        _typed("f1", "Figure 1 shows...", "figure_caption", order=3),
    ])
    inputs = BUILDER.build(structure, _skeleton())
    assert [i.block_id for i in inputs] == ["b1"]


def test_build_can_include_equation_blocks():
    structure = _structure([
        _typed("b1", "Body.", "body_paragraph", order=1),
        _typed("e1", "E = mc^2", "equation_block", order=2),
    ])
    inputs = BUILDER.build(structure, _skeleton(), config={"include_equation_blocks": True})
    assert [i.block_id for i in inputs] == ["b1", "e1"]


def test_backbone_and_headline_context_propagated():
    structure = _structure([_typed("b1", "By integrating both sides, ...", "body_paragraph")])
    role_input = BUILDER.build(structure, _skeleton())[0]
    assert role_input.backbone_block_type == "derivation"
    assert role_input.headline_claim == "headline"
    assert role_input.section_title == "Derivation"


def test_neighbor_context_uses_adjacent_body_or_equation():
    structure = _structure([
        _typed("b0", "Previous paragraph.", "body_paragraph", order=0),
        _typed("b1", "Current paragraph.", "body_paragraph", order=1),
        _typed("e1", "Equation context.", "equation_block", order=2),
    ])
    role_input = [i for i in BUILDER.build(structure, _skeleton()) if i.block_id == "b1"][0]
    assert role_input.prev_text == "Previous paragraph."
    assert role_input.next_text == "Equation context."


def test_normalized_terms_from_cartridge_aliases():
    cartridge = CartridgeContext(
        cartridge_id="test",
        ontology={},
        validation_rules={},
        aliases={"R Lambda c": ["RΛc", "R_Lambdac"]},
    )
    structure = _structure([_typed("b1", "RΛc is constrained.", "body_paragraph")])
    role_input = BUILDER.build(structure, _skeleton(), cartridge=cartridge)[0]
    assert role_input.cartridge_id == "test"
    assert role_input.normalized_terms is not None
    assert role_input.normalized_terms[0]["canonical"] == "R Lambda c"
