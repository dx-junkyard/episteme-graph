"""Tests for EquationSemanticsInputBuilder."""
from episteme_graph.agents.document_structure.schema import (
    DocumentMetadata,
    DocumentStructureResult,
    Section,
    TypedBlock,
)
from episteme_graph.agents.equation_semantics.input_builder import EquationSemanticsInputBuilder
from episteme_graph.agents.equation_semantics.schema import CartridgeContext
from episteme_graph.agents.paper_skeleton.schema import LogicalBlock, PaperSkeletonResult, SKELETON_VERSION
from episteme_graph.agents.rhetorical_role.schema import (
    BlockRoleAnnotation,
    RhetoricalRoleResult,
    SpanAnnotation,
)

BUILDER = EquationSemanticsInputBuilder()


def _typed(block_id, text, block_type, order, section_id="sec_1"):
    b = TypedBlock(block_id, 1, order, text, block_type)
    b.section_id = section_id
    return b


def _structure():
    return DocumentStructureResult(
        document_id="doc",
        source_file="/tmp/test.pdf",
        cartridge_id=None,
        metadata=DocumentMetadata(title="Test", pages=1),
        sections=[Section("sec_1", "Formulation", 1, 1, 1)],
        blocks=[
            _typed("b0", "where the prefactor is defined as", "body_paragraph", 0),
            _typed("e1", "N = a + b (1.1)", "equation_block", 1),
            _typed("b2", "Starting from eq. (1.1), we obtain", "body_paragraph", 2),
        ],
    )


def _skeleton():
    return PaperSkeletonResult(
        document_id="doc",
        skeleton_version=SKELETON_VERSION,
        cartridge_id=None,
        paper_goal={"text": "goal", "evidence_block_ids": ["b0"], "reason": "", "confidence": 0.8},
        central_question={"text": "question", "evidence_block_ids": ["b0"], "reason": "", "confidence": 0.8},
        headline_claim={"text": "headline", "evidence_block_ids": ["b0"], "reason": "", "confidence": 0.8},
        supporting_subclaims=[],
        logical_blocks=[LogicalBlock("l1", "derivation", "Derivation", ["sec_1"], ["e1"], "summary", "reason", 0.8)],
        excluded_regions=[],
        review_notes=[],
        confidence=0.8,
    )


def _roles():
    span = SpanAnnotation(
        "s1",
        "where the prefactor is defined as",
        0,
        len("where the prefactor is defined as"),
        ["definition"],
        True,
        False,
        0.9,
        "definition context",
    )
    return RhetoricalRoleResult(
        document_id="doc",
        cartridge_id=None,
        role_annotations=[BlockRoleAnnotation("e1", "sec_1", "derivation", [span])],
        summary_stats={},
    )


def test_build_extracts_equation_block_context():
    inputs = BUILDER.build(_structure(), skeleton=_skeleton(), roles=_roles())
    assert len(inputs) == 1
    inp = inputs[0]
    assert inp.equation_id == "eq_1_1"
    assert inp.label == "1.1"
    assert inp.section_title == "Formulation"
    assert inp.backbone_block_type == "derivation"
    assert "prefactor" in inp.prev_texts[0]
    assert "Starting from" in inp.next_texts[0]
    assert inp.nearby_span_annotations[0]["span_id"] == "s1"


def test_build_with_cartridge_terms():
    cartridge = CartridgeContext(
        cartridge_id="test",
        ontology={},
        validation_rules={},
        aliases={"R Lambda c": ["RΛc"]},
    )
    inputs = BUILDER.build(_structure(), cartridge=cartridge)
    assert inputs[0].cartridge_id == "test"
    assert inputs[0].normalized_terms[0]["canonical"] == "R Lambda c"


def test_max_equations_limit():
    structure = _structure()
    structure.blocks.append(_typed("e2", "M = c + d (1.2)", "equation_block", 3))
    inputs = BUILDER.build(structure, config={"max_equations": 1})
    assert [i.block_id for i in inputs] == ["e1"]
