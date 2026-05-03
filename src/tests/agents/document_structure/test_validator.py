"""Tests for StructureValidator."""
import pytest

from episteme_graph.agents.document_structure.schema import (
    DocumentMetadata,
    DocumentStructureResult,
    TypedBlock,
    ValidationIssue,
    Section,
)
from episteme_graph.agents.document_structure.validator import StructureValidator


VALIDATOR = StructureValidator()
DOC_ID = "doc_test"


def _result(blocks: list[TypedBlock]) -> DocumentStructureResult:
    return DocumentStructureResult(
        document_id=DOC_ID,
        source_file="/tmp/test.pdf",
        cartridge_id=None,
        metadata=DocumentMetadata(pages=1),
        blocks=blocks,
        sections=[],
    )


def _block(block_id: str, text: str, block_type: str) -> TypedBlock:
    return TypedBlock(
        block_id=block_id,
        page=1,
        order=0,
        text=text,
        block_type=block_type,
    )


# ── invalid_block_type ────────────────────────────────────────────────────────

def test_invalid_block_type_raises_error():
    blocks = [_block("b0", "text", "invalid_type")]
    issues = VALIDATOR.validate(_result(blocks))
    assert any(i.rule_id == "invalid_block_type" and i.severity == "error" for i in issues)


def test_valid_block_types_no_error():
    from episteme_graph.agents.document_structure.schema import BLOCK_TYPES
    for bt in BLOCK_TYPES:
        blocks = [_block("b0", "text", bt)]
        issues = VALIDATOR.validate(_result(blocks))
        assert not any(i.rule_id == "invalid_block_type" for i in issues)


# ── section_title_too_long ────────────────────────────────────────────────────

def test_section_title_too_long():
    long_title = "A" * 250
    blocks = [_block("b0", long_title, "section_heading")]
    issues = VALIDATOR.validate(_result(blocks))
    assert any(i.rule_id == "section_title_too_long" for i in issues)


def test_section_title_ok():
    blocks = [_block("b0", "Introduction", "section_heading")]
    issues = VALIDATOR.validate(_result(blocks))
    assert not any(i.rule_id == "section_title_too_long" for i in issues)


# ── figure_caption_as_body ────────────────────────────────────────────────────

def test_figure_caption_detected_in_body():
    blocks = [_block("b0", "Figure 1. The apparatus.", "body_paragraph")]
    issues = VALIDATOR.validate(_result(blocks))
    assert any(i.rule_id == "figure_caption_as_body" for i in issues)


def test_regular_body_no_figure_warning():
    blocks = [_block("b0", "This is a normal paragraph.", "body_paragraph")]
    issues = VALIDATOR.validate(_result(blocks))
    assert not any(i.rule_id == "figure_caption_as_body" for i in issues)


# ── equation_as_body ──────────────────────────────────────────────────────────

def test_equation_label_in_body():
    blocks = [_block("b0", "(3.14)", "body_paragraph")]
    issues = VALIDATOR.validate(_result(blocks))
    assert any(i.rule_id == "equation_as_body" for i in issues)


def test_non_equation_body_no_warning():
    blocks = [_block("b0", "Some sentence (3.14) here.", "body_paragraph")]
    issues = VALIDATOR.validate(_result(blocks))
    assert not any(i.rule_id == "equation_as_body" for i in issues)


# ── reference_as_body ─────────────────────────────────────────────────────────

def test_reference_entry_as_body():
    blocks = [_block("b0", "[1] Smith et al. (2020).", "body_paragraph")]
    issues = VALIDATOR.validate(_result(blocks))
    assert any(i.rule_id == "reference_as_body" for i in issues)


# ── high_unknown_ratio ────────────────────────────────────────────────────────

def test_high_unknown_ratio():
    blocks = [_block(f"b{i}", "x", "unknown") for i in range(6)]
    blocks += [_block("b6", "Normal.", "body_paragraph")]
    issues = VALIDATOR.validate(_result(blocks))
    assert any(i.rule_id == "high_unknown_ratio" for i in issues)


def test_low_unknown_ratio_no_warning():
    blocks = [_block("b0", "x", "unknown")]
    blocks += [_block(f"b{i+1}", "Normal.", "body_paragraph") for i in range(5)]
    issues = VALIDATOR.validate(_result(blocks))
    assert not any(i.rule_id == "high_unknown_ratio" for i in issues)


def test_empty_blocks_no_crash():
    issues = VALIDATOR.validate(_result([]))
    assert isinstance(issues, list)


# ── notation_in_heading (cartridge) ──────────────────────────────────────────

def test_notation_in_heading_with_cartridge():
    from episteme_graph.agents.document_structure.schema import CartridgeContext
    cartridge = CartridgeContext(
        cartridge_id="test",
        ontology={},
        validation_rules={},
        notation_patterns=[{"pattern": r"\bH_0\b"}],
    )
    blocks = [_block("b0", "The role of H_0 in cosmology", "section_heading")]
    issues = VALIDATOR.validate(_result(blocks), cartridge=cartridge)
    assert any(i.rule_id == "notation_in_heading" for i in issues)


def test_no_notation_warning_without_cartridge():
    blocks = [_block("b0", "H_0 is discussed here", "section_heading")]
    issues = VALIDATOR.validate(_result(blocks))
    assert not any(i.rule_id == "notation_in_heading" for i in issues)
