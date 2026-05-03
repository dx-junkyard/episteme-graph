"""Tests for BlockClassifier — known text patterns → expected block_type."""
import pytest

from episteme_graph.agents.document_structure.classifier import BlockClassifier
from episteme_graph.agents.document_structure.schema import RawBlock


def _block(text: str, **kwargs) -> RawBlock:
    defaults = dict(page=1, order=0, font_size=11.0, is_bold=False, is_centered=False)
    defaults.update(kwargs)
    return RawBlock(text=text, **defaults)


CLASSIFIER = BlockClassifier()


def classify(text: str, **kwargs) -> str:
    b = _block(text, **kwargs)
    blocks = CLASSIFIER.classify([b])
    return blocks[0].block_type


# ── section_heading ──────────────────────────────────────────────────────────

def test_numbered_section_heading():
    assert classify("1 Introduction") == "section_heading"


def test_numbered_section_heading_dot():
    assert classify("2. Related Work") == "section_heading"


def test_references_header():
    assert classify("References") == "section_heading"


def test_bold_short_capitalized_heading():
    assert classify("Methods", is_bold=True) == "section_heading"


def test_large_font_heading():
    # 3 body blocks (11pt) drive the median down; 16pt heading should be classified as heading
    blocks = [
        _block("Background", font_size=16.0, order=0),
        _block("Body text one.", font_size=11.0, order=1),
        _block("Body text two.", font_size=11.0, order=2),
        _block("Body text three.", font_size=11.0, order=3),
    ]
    typed = CLASSIFIER.classify(blocks)
    assert typed[0].block_type == "section_heading"


# ── subsection_heading ───────────────────────────────────────────────────────

def test_subsection_heading_two_dots():
    assert classify("2.1 Dataset") == "subsection_heading"


def test_subsubsection_heading():
    assert classify("3.2.1 Details") == "subsection_heading"


# ── figure_caption ───────────────────────────────────────────────────────────

def test_figure_caption_english():
    assert classify("Figure 1. Schematic of the apparatus.") == "figure_caption"


def test_fig_abbreviation():
    assert classify("Fig. 3 Results.") == "figure_caption"


def test_figure_caption_japanese():
    assert classify("図 2 実験装置の概略図") == "figure_caption"


# ── table_caption ────────────────────────────────────────────────────────────

def test_table_caption_english():
    assert classify("Table 1. Summary statistics.") == "table_caption"


def test_table_caption_japanese():
    assert classify("表 3 実験結果") == "table_caption"


# ── reference_entry ──────────────────────────────────────────────────────────

def test_reference_entry_numbered():
    blocks = [
        _block("References"),
        _block("[1] Smith et al. (2020) Nature.", order=1),
    ]
    typed = CLASSIFIER.classify(blocks)
    assert typed[1].block_type == "reference_entry"


# ── equation_block ───────────────────────────────────────────────────────────

def test_equation_with_label():
    assert classify("E = mc² (1)") == "equation_block"


def test_centered_equation():
    assert classify("x = α + β", is_centered=True) == "equation_block"


def test_math_heavy_text():
    # >12% math symbols
    assert classify("∫∑∂∇αβγδ") == "equation_block"


# ── body_paragraph ───────────────────────────────────────────────────────────

def test_body_paragraph():
    text = ("This paper presents a novel approach to solving the problem "
            "of knowledge graph construction from scientific literature.")
    assert classify(text) == "body_paragraph"


def test_long_text_is_body_not_heading():
    long_heading = "A" * 200
    assert classify(long_heading) == "body_paragraph"


# ── footnote ─────────────────────────────────────────────────────────────────

def test_footnote_small_font_with_prefix():
    assert classify("1 This is a footnote.", font_size=8.0) == "footnote"


# ── empty text ───────────────────────────────────────────────────────────────

def test_empty_text_is_unknown():
    assert classify("   ") == "unknown"
