"""Tests for SkeletonInputBuilder."""
import pytest

from episteme_graph.agents.document_structure.schema import (
    DocumentMetadata,
    DocumentStructureResult,
    Section,
    TypedBlock,
)
from episteme_graph.agents.paper_skeleton.input_builder import SkeletonInputBuilder
from episteme_graph.agents.paper_skeleton.schema import CartridgeContext, SkeletonLLMInput

BUILDER = SkeletonInputBuilder()


def _typed(block_id, text, block_type, page=1, order=0, section_id=None):
    b = TypedBlock(
        block_id=block_id, page=page, order=order,
        text=text, block_type=block_type,
    )
    b.section_id = section_id
    return b


def _section(section_id, title, level=1, page_start=1, parent=None):
    return Section(
        section_id=section_id,
        title=title,
        level=level,
        order=1,
        page_start=page_start,
        parent_section_id=parent,
    )


def _result(blocks, sections, title=None):
    return DocumentStructureResult(
        document_id="doc_test",
        source_file="/tmp/test.pdf",
        cartridge_id=None,
        metadata=DocumentMetadata(title=title, pages=10),
        blocks=blocks,
        sections=sections,
    )


# ── abstract extraction ───────────────────────────────────────────────────────

def test_abstract_from_named_section():
    sec = _section("sec_abs", "Abstract")
    blocks = [
        _typed("b0", "Introduction", "section_heading", section_id="sec_intro"),
        _typed("b1", "Abstract text here.", "body_paragraph", section_id="sec_abs"),
    ]
    result = _result(blocks, [sec])
    llm_input = BUILDER.build(result)
    assert len(llm_input.abstract_blocks) == 1
    assert llm_input.abstract_blocks[0]["block_id"] == "b1"


def test_abstract_from_pre_heading_blocks():
    sec = _section("sec_1", "Introduction", page_start=2)
    blocks = [
        _typed("b0", "Pre-heading paragraph.", "body_paragraph", order=0, section_id=None),
        _typed("b1", "Introduction", "section_heading", order=1, section_id="sec_1"),
        _typed("b2", "Body text.", "body_paragraph", order=2, section_id="sec_1"),
    ]
    result = _result(blocks, [sec])
    llm_input = BUILDER.build(result)
    assert any(b["block_id"] == "b0" for b in llm_input.abstract_blocks)


# ── section headers ───────────────────────────────────────────────────────────

def test_section_headers_only_level1():
    sections = [
        _section("sec_1", "Introduction", level=1),
        _section("sec_1_1", "Background", level=2, parent="sec_1"),
        _section("sec_2", "Methods", level=1),
    ]
    result = _result([], sections)
    llm_input = BUILDER.build(result)
    titles = [s["title"] for s in llm_input.section_headers]
    assert "Introduction" in titles
    assert "Methods" in titles
    assert "Background" not in titles  # subsection excluded


def test_appendix_excluded_from_section_headers():
    sections = [
        _section("sec_1", "Methods", level=1),
        _section("sec_app", "Appendix A", level=1),
    ]
    result = _result([], sections)
    llm_input = BUILDER.build(result)
    titles = [s["title"] for s in llm_input.section_headers]
    assert "Methods" in titles
    assert "Appendix A" not in titles


# ── representative blocks ─────────────────────────────────────────────────────

def test_representative_blocks_first_and_last():
    sec = _section("sec_1", "Methods", level=1)
    blocks = [
        _typed("b0", "Methods", "section_heading", order=0, section_id="sec_1"),
        _typed("b1", "First body.", "body_paragraph", order=1, section_id="sec_1"),
        _typed("b2", "Middle body.", "body_paragraph", order=2, section_id="sec_1"),
        _typed("b3", "Last body.", "body_paragraph", order=3, section_id="sec_1"),
    ]
    result = _result(blocks, [sec])
    llm_input = BUILDER.build(result)
    rep_ids = [b["block_id"] for b in llm_input.representative_blocks]
    assert "b1" in rep_ids
    assert "b3" in rep_ids
    assert "b2" not in rep_ids  # middle excluded


def test_conclusion_blocks_extracted():
    sec = _section("sec_conc", "Conclusion", level=1)
    blocks = [
        _typed("b0", "Conclusion text.", "body_paragraph", section_id="sec_conc"),
    ]
    result = _result(blocks, [sec])
    llm_input = BUILDER.build(result)
    assert len(llm_input.conclusion_blocks) == 1
    assert llm_input.conclusion_blocks[0]["block_id"] == "b0"


# ── cartridge normalized terms ────────────────────────────────────────────────

def test_normalized_terms_from_cartridge_aliases():
    cartridge = CartridgeContext(
        cartridge_id="test",
        ontology={},
        validation_rules={},
        aliases={"Hamiltonian": ["H", "H-operator"]},
    )
    result = _result([], [])
    llm_input = BUILDER.build(result, cartridge=cartridge)
    assert llm_input.normalized_terms is not None
    canonicals = [t["canonical"] for t in llm_input.normalized_terms]
    assert "Hamiltonian" in canonicals


def test_no_cartridge_normalized_terms_is_none():
    result = _result([], [])
    llm_input = BUILDER.build(result)
    assert llm_input.normalized_terms is None


# ── document_id / cartridge_id propagation ────────────────────────────────────

def test_document_id_propagated():
    result = _result([], [])
    llm_input = BUILDER.build(result)
    assert llm_input.document_id == "doc_test"


def test_cartridge_id_propagated_when_present():
    cartridge = CartridgeContext("phys", {}, {})
    result = _result([], [])
    llm_input = BUILDER.build(result, cartridge=cartridge)
    assert llm_input.cartridge_id == "phys"


def test_cartridge_id_none_without_cartridge():
    result = _result([], [])
    llm_input = BUILDER.build(result)
    assert llm_input.cartridge_id is None


# ── title propagation ─────────────────────────────────────────────────────────

def test_title_propagated():
    result = _result([], [], title="My Paper")
    llm_input = BUILDER.build(result)
    assert llm_input.title == "My Paper"
