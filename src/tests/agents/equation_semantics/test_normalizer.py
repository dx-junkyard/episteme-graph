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


# ------------------------------------------------------------------
# Issue #368: source-aware label validation
# ------------------------------------------------------------------

def test_invalid_pdf_label_is_rejected_and_id_falls_back_to_block():
    block = TypedBlock("blk_7", 1, 0, "F = ma", "equation_block", equation_label="(1) g , K (3)")
    eq = EquationNormalizer().normalize(block)
    assert eq.label is None
    assert eq.label_is_valid is False
    assert eq.rejected_label == "(1) g , K (3)"
    # Equation id must not be derived from the garbage label.
    assert eq.equation_id == "eq_blk_7"


def test_stray_paren_label_is_rejected():
    block = TypedBlock("blk_8", 1, 0, "F = ma", "equation_block", equation_label="(")
    eq = EquationNormalizer().normalize(block)
    assert eq.label is None
    assert eq.label_is_valid is False
    assert eq.equation_id == "eq_blk_8"


def test_numeric_label_with_parens_is_accepted():
    block = TypedBlock("blk_9", 1, 0, "F = ma", "equation_block", equation_label="(3.14)")
    eq = EquationNormalizer().normalize(block)
    assert eq.label == "3.14"
    assert eq.label_is_valid is True
    assert eq.equation_id == "eq_3_14"


def test_missing_label_is_valid_unnumbered_equation():
    block = TypedBlock("blk_10", 1, 0, "F = ma", "equation_block")
    eq = EquationNormalizer().normalize(block)
    assert eq.label is None
    assert eq.label_is_valid is True
    assert eq.rejected_label is None


def test_tex_semantic_label_is_preserved():
    raw = {"extraction_source": "tex_source"}
    block = TypedBlock("blk_11", 1, 0, "F = ma", "equation_block", equation_label="eq:dispersion", raw=raw)
    eq = EquationNormalizer().normalize(block)
    assert eq.label == "eq:dispersion"
    assert eq.label_is_valid is True
    assert eq.equation_id == "eq_eq_dispersion"


def test_cartridge_extra_pattern_accepts_custom_label():
    block = TypedBlock("blk_12", 1, 0, "F = ma", "equation_block", equation_label="EQ-X42")
    norm = EquationNormalizer(extra_label_patterns=[r"^EQ-[A-Z]\d+$"])
    eq = norm.normalize(block)
    assert eq.label == "EQ-X42"
    assert eq.label_is_valid is True
