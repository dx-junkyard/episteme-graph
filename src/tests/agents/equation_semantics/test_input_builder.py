"""Tests for EquationSemanticsInputBuilder (Issue #245 refactor)."""
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


# ------------------------------------------------------------------
# build_candidates
# ------------------------------------------------------------------

def test_build_candidates_returns_candidate_for_each_equation_block():
    candidates = BUILDER.build_candidates(_structure())
    assert len(candidates) == 1
    c = candidates[0]
    assert c.document_id == "doc"
    assert c.source_location["block_id"] == "e1"
    assert c.raw_text == "N = a + b (1.1)"
    assert "document_structure_equation_block" in c.detection_method
    assert "equation_number_pattern" in c.detection_method  # label found
    assert c.matched_label == "1.1"


def test_build_candidates_max_limit():
    structure = _structure()
    structure.blocks.append(_typed("e2", "M = c + d (1.2)", "equation_block", 3))
    candidates = BUILDER.build_candidates(structure, config={"max_equations": 1})
    assert len(candidates) == 1
    assert candidates[0].source_location["block_id"] == "e1"


def test_build_candidates_flags_invalid_label():
    # Issue #368: a garbage GROBID label is rejected and recorded for audit.
    structure = _structure()
    bad = _typed("e_bad", "F = ma", "equation_block", 4)
    bad.equation_label = "(1) g , K (3)"
    structure.blocks.append(bad)
    candidates = BUILDER.build_candidates(structure)
    cand = next(c for c in candidates if c.source_location["block_id"] == "e_bad")
    assert cand.matched_label is None
    assert "invalid_equation_label" in cand.review_reason
    assert cand.source_location["rejected_equation_label"] == "(1) g , K (3)"


def test_build_candidates_flags_table_provenance():
    # Issue #368: a block carrying table provenance is marked table-derived.
    structure = _structure()
    tbl = _typed("e_tbl", "a = b + c (5.1)", "equation_block", 5)
    tbl.raw = {"table_id": "tbl_2", "row": 1}
    structure.blocks.append(tbl)
    candidates = BUILDER.build_candidates(structure)
    cand = next(c for c in candidates if c.source_location["block_id"] == "e_tbl")
    assert "table_derived_equation_candidate" in cand.review_reason
    assert cand.source_location["table_provenance"]["table_id"] == "tbl_2"


def test_table_provenance_propagates_to_acceptance_gate():
    # Issue #368 integration: block.raw table provenance -> candidate -> gate
    # keeps the equation out of the auto-confirmed set.
    from episteme_graph.agents.equation_semantics.acceptance_gate import (
        EquationAcceptanceGate,
    )

    structure = _structure()
    tbl = _typed("e_tabcell", "a = b + c (9.1)", "equation_block", 9)
    tbl.raw = {"container_type": "table", "in_table": True, "table_id": "tab1"}
    structure.blocks.append(tbl)

    candidates = BUILDER.build_candidates(structure)
    classified = EquationAcceptanceGate().process(candidates)
    cell = next(c for c in classified if c.source_location["block_id"] == "e_tabcell")
    assert cell.acceptance_status != "accepted"
    assert "table_derived_equation_candidate" in cell.review_reason

    # a normal equation block in the same structure is still accepted
    normal = next(c for c in classified if c.source_location["block_id"] == "e1")
    assert normal.acceptance_status == "accepted"


# ------------------------------------------------------------------
# build_llm_inputs
# ------------------------------------------------------------------

def test_build_llm_inputs_only_for_accepted():
    from episteme_graph.agents.equation_semantics.schema import EquationCandidate

    candidates = BUILDER.build_candidates(_structure())
    # Mark as accepted manually (gate would do this)
    for c in candidates:
        c.acceptance_status = "accepted"
        c.extraction_status = "complete"

    inputs = BUILDER.build_llm_inputs(_structure(), candidates, skeleton=_skeleton(), roles=_roles())
    assert len(inputs) == 1
    inp = inputs[0]
    assert inp.equation_id == "eq_1_1"
    assert inp.label == "1.1"
    assert inp.section_title == "Formulation"
    assert inp.backbone_block_type == "derivation"
    assert "prefactor" in inp.prev_texts[0]
    assert "Starting from" in inp.next_texts[0]
    assert inp.nearby_span_annotations[0]["span_id"] == "s1"
    assert inp.extraction_status == "complete"
    assert inp.acceptance_status == "accepted"
    assert inp.needs_reconstruction is False


def test_build_llm_inputs_empty_when_no_accepted():
    candidates = BUILDER.build_candidates(_structure())
    # Mark all as rejected
    for c in candidates:
        c.acceptance_status = "rejected"
    inputs = BUILDER.build_llm_inputs(_structure(), candidates)
    assert inputs == []


def test_needs_reconstruction_flag_for_partial():
    from episteme_graph.agents.equation_semantics.schema import EquationCandidate

    candidates = BUILDER.build_candidates(_structure())
    for c in candidates:
        c.acceptance_status = "accepted"
        c.extraction_status = "partial"  # triggers needs_reconstruction

    inputs = BUILDER.build_llm_inputs(_structure(), candidates)
    assert inputs[0].needs_reconstruction is True


# ------------------------------------------------------------------
# Cartridge terms
# ------------------------------------------------------------------

def test_build_llm_inputs_with_cartridge_terms():
    cartridge = CartridgeContext(
        cartridge_id="test",
        ontology={},
        validation_rules={},
        aliases={"R Lambda c": ["RΛc"]},
    )
    candidates = BUILDER.build_candidates(_structure(), cartridge=cartridge)
    for c in candidates:
        c.acceptance_status = "accepted"
        c.extraction_status = "complete"
    inputs = BUILDER.build_llm_inputs(_structure(), candidates, cartridge=cartridge)
    assert inputs[0].cartridge_id == "test"
    assert inputs[0].normalized_terms[0]["canonical"] == "R Lambda c"


def test_tex_source_equation_is_trusted_and_not_reconstructed():
    structure = DocumentStructureResult(
        document_id="doc",
        source_file="paper.tar.gz:main.tex",
        cartridge_id=None,
        metadata=DocumentMetadata(title="Test", pages=0),
        sections=[Section("sec_1", "Formulation", 1, 1, 1)],
        blocks=[
            _typed("b0", "The source defines the energy relation.", "body_paragraph", 0),
            _typed("e1", r"E = mc^2", "equation_block", 1),
        ],
    )
    structure.blocks[1].raw = {
        "parser_source": "tex_archive",
        "extraction_source": "tex_source",
        "latex": r"E = mc^2",
    }
    candidates = BUILDER.build_candidates(structure)
    for c in candidates:
        c.acceptance_status = "accepted"
        c.extraction_status = "complete"

    inputs = BUILDER.build_llm_inputs(structure, candidates, force_reconstruction=True)

    assert inputs[0].latex == r"E = mc^2"
    assert inputs[0].extraction_source == "tex_source"
    assert inputs[0].source_is_trusted is True
    assert inputs[0].needs_reconstruction is False
