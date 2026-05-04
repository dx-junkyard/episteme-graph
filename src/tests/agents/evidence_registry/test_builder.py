"""Tests for EvidenceRegistryBuilder."""
from __future__ import annotations

import json

import pytest

from episteme_graph.agents.document_structure.schema import (
    DocumentMetadata,
    DocumentStructureResult,
    Section,
    TypedBlock,
)
from episteme_graph.agents.evidence_registry import (
    EvidenceRegistryBuilder,
    EvidenceRegistryResult,
)


def _make_structure() -> DocumentStructureResult:
    blocks = [
        TypedBlock(
            block_id="blk_001_0001",
            page=1,
            order=1,
            text="Introduction to the sum rule formalism.",
            block_type="body_paragraph",
            section_id="doc_test:sec_1",
        ),
        TypedBlock(
            block_id="blk_004_0065",
            page=4,
            order=65,
            text="In the heavy-quark limit the rate ratio reduces to a clean expression.",
            block_type="body_paragraph",
            section_id="doc_test:sec_3_1",
        ),
    ]
    sections = [
        Section(section_id="doc_test:sec_1", title="Intro", level=1, order=1, page_start=1),
        Section(section_id="doc_test:sec_3_1", title="Formulation", level=2, order=2, page_start=4),
    ]
    return DocumentStructureResult(
        document_id="doc_test",
        source_file="/tmp/x.pdf",
        cartridge_id="particle_physics",
        metadata=DocumentMetadata(pages=10),
        sections=sections,
        blocks=blocks,
    )


def test_add_for_block_uses_pdf_text_only():
    structure = _make_structure()
    builder = EvidenceRegistryBuilder(structure)

    ev_id = builder.add_for_block("blk_004_0065")
    result = builder.build("doc_test", "particle_physics")

    assert ev_id is not None
    rec = result.index_by_id()[ev_id]
    assert rec.evidence_text.startswith("In the heavy-quark limit")
    assert rec.source.block_id == "blk_004_0065"
    assert rec.source.page == 4
    assert rec.source.section_id == "doc_test:sec_3_1"
    # No review note leaked into evidence_text
    assert rec.review_note == ""
    # Document id propagated
    assert rec.document_id == "doc_test"


def test_add_for_span_extracts_substring():
    structure = _make_structure()
    builder = EvidenceRegistryBuilder(structure)

    ev_id = builder.add_for_span("blk_004_0065", 0, 24)
    result = builder.build("doc_test", "particle_physics")

    rec = result.index_by_id()[ev_id]
    assert rec.evidence_text == "In the heavy-quark limit"
    assert rec.source.span_start == 0
    assert rec.source.span_end == 24


def test_attach_review_note_keeps_text_clean():
    structure = _make_structure()
    builder = EvidenceRegistryBuilder(structure)
    ev_id = builder.add_for_block("blk_004_0065")

    builder.attach_review_note(ev_id, "This span should be split into multiple claims.")
    result = builder.build("doc_test")
    rec = result.index_by_id()[ev_id]

    assert "should be split" not in rec.evidence_text
    assert "should be split" in rec.review_note


def test_dedup_returns_same_id():
    structure = _make_structure()
    builder = EvidenceRegistryBuilder(structure)
    a = builder.add_for_span("blk_004_0065", 0, 25)
    b = builder.add_for_span("blk_004_0065", 0, 25)
    assert a == b


def test_unknown_block_id_records_validation_issue():
    builder = EvidenceRegistryBuilder()
    out = builder.add_for_block("blk_does_not_exist")
    result = builder.build("doc_x")
    assert out is None
    assert any(i.rule_id == "evidence_block_missing" for i in result.validation_issues)


def test_validator_flags_review_text_in_evidence():
    structure = _make_structure()
    # Manually inject a block whose text reads like a review note.
    structure.blocks.append(TypedBlock(
        block_id="blk_bad",
        page=5,
        order=99,
        text="This span should be split into multiple claims.",
        block_type="body_paragraph",
    ))
    builder = EvidenceRegistryBuilder(structure)
    builder.add_for_block("blk_bad")
    result = builder.build("doc_test")
    rules = {i.rule_id for i in result.validation_issues}
    assert "evidence_text_looks_like_review_note" in rules


def test_serialization_roundtrip():
    structure = _make_structure()
    builder = EvidenceRegistryBuilder(structure)
    builder.add_for_block("blk_001_0001")
    builder.add_for_span("blk_004_0065", 0, 25, review_note="needs reviewer attention")
    result = builder.build("doc_test", "particle_physics")

    raw = result.to_json()
    restored = EvidenceRegistryResult.from_dict(json.loads(raw))
    assert len(restored.records) == 2
    assert restored.records[0].evidence_id == "ev_0001"
    assert restored.records[1].review_note == "needs reviewer attention"
