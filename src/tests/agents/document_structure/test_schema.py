"""Tests for DocumentStructureAgent schema serialization."""
import json

import pytest

from episteme_graph.agents.document_structure.schema import (
    DocumentMetadata,
    DocumentStructureResult,
    Section,
    TypedBlock,
    ValidationIssue,
)


def _make_result() -> DocumentStructureResult:
    block = TypedBlock(
        block_id="blk_001_0000",
        page=1,
        order=0,
        text="Introduction",
        block_type="section_heading",
        confidence=0.88,
    )
    section = Section(
        section_id="doc_test:sec_1",
        title="Introduction",
        level=1,
        order=1,
        page_start=1,
        page_end=3,
    )
    issue = ValidationIssue(
        rule_id="section_title_too_long",
        severity="warning",
        message="Section title too long (250 chars)",
        block_id="blk_001_0000",
    )
    return DocumentStructureResult(
        document_id="doc_test",
        source_file="/tmp/test.pdf",
        cartridge_id="particle_physics",
        metadata=DocumentMetadata(pages=10),
        blocks=[block],
        sections=[section],
        validation_issues=[issue],
    )


def test_to_dict_has_expected_keys():
    result = _make_result()
    d = result.to_dict()
    assert d["document_id"] == "doc_test"
    assert d["cartridge_id"] == "particle_physics"
    assert len(d["blocks"]) == 1
    assert len(d["sections"]) == 1
    assert len(d["validation_issues"]) == 1


def test_to_json_is_valid_json():
    result = _make_result()
    raw = result.to_json()
    parsed = json.loads(raw)
    assert parsed["document_id"] == "doc_test"


def test_round_trip():
    original = _make_result()
    d = original.to_dict()
    restored = DocumentStructureResult.from_dict(d)

    assert restored.document_id == original.document_id
    assert restored.source_file == original.source_file
    assert restored.cartridge_id == original.cartridge_id
    assert len(restored.blocks) == 1
    assert restored.blocks[0].block_type == "section_heading"
    assert len(restored.sections) == 1
    assert restored.sections[0].title == "Introduction"
    assert len(restored.validation_issues) == 1
    assert restored.validation_issues[0].rule_id == "section_title_too_long"


def test_metadata_defaults():
    m = DocumentMetadata()
    assert m.title is None
    assert m.authors == []
    assert m.pages == 0


def test_typed_block_defaults():
    b = TypedBlock(
        block_id="blk_001_0001",
        page=1,
        order=1,
        text="Some text",
        block_type="body_paragraph",
    )
    assert b.confidence == 1.0
    assert b.equation_label is None
    assert b.section_id is None
    assert b.raw == {}


def test_section_defaults():
    s = Section(
        section_id="doc:sec_1",
        title="Intro",
        level=1,
        order=1,
        page_start=1,
    )
    assert s.page_end is None
    assert s.parent_section_id is None
