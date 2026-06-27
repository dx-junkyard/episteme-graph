"""Tests for GROBIDTEIParser (issue #282).

TEI XML の fixture を使ってパーサの動作を検証する。
GROBID サーバへの接続は行わず、純粋なロジックテストのみ。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# bs4 が無い環境では skip
pytest.importorskip("bs4", reason="beautifulsoup4 not installed")

from episteme_graph.agents.document_structure.grobid_parser import GROBIDTEIParser


# ---------------------------------------------------------------------------
# TEI XML fixtures
# ---------------------------------------------------------------------------

_TEI_SIMPLE = """\
<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <teiHeader>
    <fileDesc>
      <titleStmt>
        <title level="a" type="main">Test Paper Title</title>
      </titleStmt>
    </fileDesc>
    <profileDesc>
      <abstract>
        <div><p>This is the abstract paragraph.</p></div>
      </abstract>
    </profileDesc>
  </teiHeader>
  <text>
    <body>
      <div n="1">
        <head>Introduction</head>
        <p>First paragraph of introduction.</p>
        <p>Second paragraph of introduction.</p>
      </div>
      <div n="2">
        <head>Method</head>
        <p>Method paragraph.</p>
      </div>
    </body>
  </text>
</TEI>
"""

_TEI_WITH_REFERENCES = """\
<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <text>
    <body>
      <div n="1">
        <head>Introduction</head>
        <p>Body text.</p>
      </div>
      <div n="2">
        <head>References</head>
        <p>Should be excluded.</p>
      </div>
      <div n="3">
        <head>Acknowledgments</head>
        <p>Also excluded.</p>
      </div>
    </body>
  </text>
</TEI>
"""

_TEI_WITH_EQUATIONS = """\
<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <text>
    <body>
      <div n="1">
        <head>Theory</head>
        <p>The main equation is:</p>
        <formula xml:id="eq1">E = mc <label>(1)</label></formula>
        <p>Where E is energy.</p>
      </div>
    </body>
  </text>
</TEI>
"""

_TEI_WITH_FIGURES = """\
<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <text>
    <body>
      <div n="1">
        <head>Results</head>
        <p>See Figure 1.</p>
        <figure>
          <figDesc>Figure 1. A plot of X vs Y.</figDesc>
        </figure>
        <figure type="table">
          <figDesc>Table 1. Summary statistics.</figDesc>
        </figure>
      </div>
    </body>
  </text>
</TEI>
"""

_TEI_WITH_NESTED_SECTIONS = """\
<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <text>
    <body>
      <div n="1">
        <head>Methods</head>
        <p>Overview paragraph.</p>
        <div n="1.1">
          <head>Sub-method A</head>
          <p>Sub-method content.</p>
        </div>
      </div>
    </body>
  </text>
</TEI>
"""


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestGROBIDTEIParserBasic:
    def test_parse_returns_metadata_with_title(self):
        parser = GROBIDTEIParser()
        result = parser.parse(_TEI_SIMPLE)
        assert result.metadata.title == "Test Paper Title"

    def test_parse_abstract_creates_section_and_block(self):
        parser = GROBIDTEIParser()
        result = parser.parse(_TEI_SIMPLE)

        section_titles = [s.title for s in result.sections]
        assert "Abstract" in section_titles

        abstract_blocks = [
            b for b in result.blocks
            if b.section_id == next(s.section_id for s in result.sections if s.title == "Abstract")
        ]
        assert abstract_blocks
        assert "abstract paragraph" in abstract_blocks[0].text.lower()

    def test_parse_body_sections(self):
        parser = GROBIDTEIParser()
        result = parser.parse(_TEI_SIMPLE)

        titles = [s.title for s in result.sections]
        assert "Introduction" in titles
        assert "Method" in titles

    def test_parse_body_paragraphs_in_blocks(self):
        parser = GROBIDTEIParser()
        result = parser.parse(_TEI_SIMPLE)

        texts = [b.text for b in result.blocks]
        assert any("First paragraph of introduction" in t for t in texts)
        assert any("Second paragraph of introduction" in t for t in texts)
        assert any("Method paragraph" in t for t in texts)

    def test_block_types_are_body_paragraph(self):
        parser = GROBIDTEIParser()
        result = parser.parse(_TEI_SIMPLE)

        paragraph_blocks = [b for b in result.blocks if b.block_type == "body_paragraph"]
        assert len(paragraph_blocks) >= 3  # abstract + 2 intro + method


class TestGROBIDTEIParserExclusions:
    def test_references_section_excluded(self):
        parser = GROBIDTEIParser()
        result = parser.parse(_TEI_WITH_REFERENCES)

        titles = [s.title for s in result.sections]
        assert "References" not in titles

        texts = [b.text for b in result.blocks]
        assert not any("Should be excluded" in t for t in texts)

    def test_acknowledgments_section_excluded(self):
        parser = GROBIDTEIParser()
        result = parser.parse(_TEI_WITH_REFERENCES)

        titles = [s.title for s in result.sections]
        assert "Acknowledgments" not in titles
        assert not any("Also excluded" in b.text for b in result.blocks)

    def test_introduction_included(self):
        parser = GROBIDTEIParser()
        result = parser.parse(_TEI_WITH_REFERENCES)

        titles = [s.title for s in result.sections]
        assert "Introduction" in titles
        assert any("Body text" in b.text for b in result.blocks)


class TestGROBIDTEIParserEquations:
    def test_formula_creates_equation_block(self):
        parser = GROBIDTEIParser()
        result = parser.parse(_TEI_WITH_EQUATIONS)

        eq_blocks = [b for b in result.blocks if b.block_type == "equation_block"]
        assert eq_blocks, "No equation_block found"

    def test_formula_has_equation_label(self):
        parser = GROBIDTEIParser()
        result = parser.parse(_TEI_WITH_EQUATIONS)

        eq_blocks = [b for b in result.blocks if b.block_type == "equation_block"]
        assert eq_blocks[0].equation_label == "(1)"

    def test_formula_text_preserved(self):
        parser = GROBIDTEIParser()
        result = parser.parse(_TEI_WITH_EQUATIONS)

        eq_blocks = [b for b in result.blocks if b.block_type == "equation_block"]
        assert "E = mc" in eq_blocks[0].text


class TestGROBIDTEIParserFigures:
    def test_figure_caption_block(self):
        parser = GROBIDTEIParser()
        result = parser.parse(_TEI_WITH_FIGURES)

        fig_blocks = [b for b in result.blocks if b.block_type == "figure_caption"]
        assert fig_blocks
        assert any("Figure 1" in b.text for b in fig_blocks)

    def test_table_caption_block(self):
        parser = GROBIDTEIParser()
        result = parser.parse(_TEI_WITH_FIGURES)

        tbl_blocks = [b for b in result.blocks if b.block_type == "table_caption"]
        assert tbl_blocks
        assert any("Table 1" in b.text for b in tbl_blocks)

    def test_table_cell_formula_carries_table_provenance(self):
        """Issue #368: a formula inside <figure type=table> keeps table provenance."""
        tei = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body>'
            '<div n="1"><head>Results</head>'
            '<figure type="table" xml:id="tab1">'
            '<figDesc>Table 1. Coefficients.</figDesc>'
            '<formula xml:id="t1f1">S = 3.488 beta_2 + 0.28 beta_K2</formula>'
            '</figure></div></body></text></TEI>'
        )
        parser = GROBIDTEIParser()
        result = parser.parse(tei)
        eq_blocks = [b for b in result.blocks if b.block_type == "equation_block"]
        assert len(eq_blocks) == 1
        raw = eq_blocks[0].raw
        assert raw.get("container_type") == "table"
        assert raw.get("in_table") is True
        assert raw.get("table_id") == "tab1"


class TestGROBIDTEIParserProvenance:
    def test_parser_source_set_on_blocks(self):
        parser = GROBIDTEIParser()
        result = parser.parse(_TEI_SIMPLE)

        for block in result.blocks:
            src = block.raw.get("parser_source")
            assert src == "grobid_tei", (
                f"Expected parser_source='grobid_tei', got {src!r} for block {block.block_id}"
            )

    def test_tei_section_id_set_on_blocks(self):
        parser = GROBIDTEIParser()
        result = parser.parse(_TEI_SIMPLE)

        intro_section = next(s for s in result.sections if s.title == "Introduction")
        intro_blocks = [b for b in result.blocks if b.section_id == intro_section.section_id]
        for b in intro_blocks:
            assert "tei_section_id" in b.raw

    def test_section_order_monotonic(self):
        parser = GROBIDTEIParser()
        result = parser.parse(_TEI_SIMPLE)

        orders = [s.order for s in result.sections]
        assert orders == sorted(orders), "Section orders must be monotonically increasing"

    def test_block_order_monotonic(self):
        parser = GROBIDTEIParser()
        result = parser.parse(_TEI_SIMPLE)

        orders = [b.order for b in result.blocks]
        assert orders == sorted(orders), "Block orders must be monotonically increasing"


class TestGROBIDTEIParserEdgeCases:
    def test_empty_string_returns_empty_result(self):
        parser = GROBIDTEIParser()
        result = parser.parse("")
        assert result.blocks == []
        assert result.sections == []

    def test_none_like_empty_body(self):
        tei = '<?xml version="1.0"?><TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body></body></text></TEI>'
        parser = GROBIDTEIParser()
        result = parser.parse(tei)
        assert result.blocks == []
        assert result.sections == []

    def test_nested_sections_create_subsections(self):
        parser = GROBIDTEIParser()
        result = parser.parse(_TEI_WITH_NESTED_SECTIONS)

        titles = [s.title for s in result.sections]
        assert "Methods" in titles
        assert "Sub-method A" in titles

        parent = next(s for s in result.sections if s.title == "Methods")
        child = next(s for s in result.sections if s.title == "Sub-method A")
        assert child.level > parent.level
        assert child.parent_section_id == parent.section_id
