"""Tests for EquationNormalizer."""
from episteme_graph.agents.document_structure.schema import TypedBlock
from episteme_graph.agents.equation_semantics.normalizer import EquationNormalizer


def test_extracts_trailing_numeric_label():
    block = TypedBlock("b1", 1, 0, "N = a + b (3.14)", "equation_block")
    eq = EquationNormalizer().normalize(block)
    assert eq.label == "3.14"
    assert eq.equation_id == "eq_3_14"


def test_extracts_appendix_label():
    block = TypedBlock("b1", 1, 0, "F = ma (B.2)", "equation_block")
    eq = EquationNormalizer().normalize(block)
    assert eq.label == "B.2"
    assert eq.equation_id == "eq_B_2"


def test_prefers_existing_equation_label():
    block = TypedBlock("b1", 1, 0, "F = ma", "equation_block", equation_label="1.2")
    eq = EquationNormalizer().normalize(block)
    assert eq.label == "1.2"
    assert eq.equation_id == "eq_1_2"


def test_missing_label_uses_block_id():
    block = TypedBlock("blk_203", 1, 0, "F = ma", "equation_block")
    eq = EquationNormalizer().normalize(block)
    assert eq.label is None
    assert eq.equation_id == "eq_blk_203"


def test_plain_text_replaces_common_symbols():
    block = TypedBlock("b1", 1, 0, "RΛc = Γτ/Γν (1.1)", "equation_block")
    eq = EquationNormalizer().normalize(block)
    assert "Lambda" in eq.plain_text
    assert "Gamma" in eq.plain_text
    assert "tau" in eq.plain_text
    assert "nu" in eq.plain_text
