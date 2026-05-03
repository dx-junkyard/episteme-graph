"""Tests for SectionHierarchyBuilder."""
import pytest

from episteme_graph.agents.document_structure.hierarchy import SectionHierarchyBuilder
from episteme_graph.agents.document_structure.schema import TypedBlock


DOC_ID = "doc_test"


def _typed(block_id: str, text: str, block_type: str, page: int = 1, order: int = 0) -> TypedBlock:
    return TypedBlock(
        block_id=block_id,
        page=page,
        order=order,
        text=text,
        block_type=block_type,
    )


BUILDER = SectionHierarchyBuilder()


def test_flat_sections():
    blocks = [
        _typed("b0", "Introduction", "section_heading", page=1, order=0),
        _typed("b1", "Some text.", "body_paragraph", page=1, order=1),
        _typed("b2", "Related Work", "section_heading", page=2, order=2),
    ]
    sections = BUILDER.build(blocks, DOC_ID)

    assert len(sections) == 2
    assert sections[0].title == "Introduction"
    assert sections[0].level == 1
    assert sections[1].title == "Related Work"
    assert sections[1].level == 1
    assert sections[0].parent_section_id is None


def test_subsection_parent_assignment():
    blocks = [
        _typed("b0", "Methods", "section_heading", page=1, order=0),
        _typed("b1", "2.1 Dataset", "subsection_heading", page=1, order=1),
        _typed("b2", "2.2 Evaluation", "subsection_heading", page=2, order=2),
    ]
    sections = BUILDER.build(blocks, DOC_ID)

    assert len(sections) == 3
    parent = sections[0]
    child1 = sections[1]
    child2 = sections[2]

    assert parent.level == 1
    assert child1.level == 2
    assert child2.level == 2
    assert child1.parent_section_id == parent.section_id
    assert child2.parent_section_id == parent.section_id


def test_blocks_assigned_to_sections():
    blocks = [
        _typed("b0", "Introduction", "section_heading", page=1, order=0),
        _typed("b1", "Body text.", "body_paragraph", page=1, order=1),
        _typed("b2", "More text.", "body_paragraph", page=1, order=2),
    ]
    sections = BUILDER.build(blocks, DOC_ID)

    sec_id = sections[0].section_id
    # heading block itself
    assert blocks[0].section_id == sec_id
    # body blocks assigned to that section
    assert blocks[1].section_id == sec_id
    assert blocks[2].section_id == sec_id


def test_blocks_before_first_heading_have_no_section():
    blocks = [
        _typed("b0", "Abstract text.", "body_paragraph", page=1, order=0),
        _typed("b1", "Introduction", "section_heading", page=1, order=1),
    ]
    BUILDER.build(blocks, DOC_ID)
    assert blocks[0].section_id is None


def test_page_end_set_correctly():
    blocks = [
        _typed("b0", "Intro", "section_heading", page=1, order=0),
        _typed("b1", "Related", "section_heading", page=3, order=1),
        _typed("b2", "Text", "body_paragraph", page=5, order=2),
    ]
    sections = BUILDER.build(blocks, DOC_ID)

    assert sections[0].page_end == 3   # next section starts at page 3
    assert sections[1].page_end == 5   # max page of all blocks


def test_no_headings_returns_empty():
    blocks = [
        _typed("b0", "Just a paragraph.", "body_paragraph"),
    ]
    sections = BUILDER.build(blocks, DOC_ID)
    assert sections == []


def test_section_ids_are_unique():
    blocks = [
        _typed("b0", "Sec 1", "section_heading", page=1),
        _typed("b1", "Sec 2", "section_heading", page=2),
        _typed("b2", "Sec 3", "section_heading", page=3),
    ]
    sections = BUILDER.build(blocks, DOC_ID)
    ids = [s.section_id for s in sections]
    assert len(ids) == len(set(ids))
