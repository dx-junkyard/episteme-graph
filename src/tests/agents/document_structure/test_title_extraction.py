"""題名の決定論的フォールバック（TEI → 1頁目の最大フォント → None, IK-0420）。

レイアウトの合成ブロックは、砂場で題名が null になった 3 本（MNRAS / REVTeX(APS) /
AASTeX）の 1 頁目の並び（artifact に残った bbox と本文）を写し、文字の大きさは各組版の
既定値の見当で置いている。
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from episteme_graph.agents.document_structure.agent import DocumentStructureAgent
from episteme_graph.agents.document_structure.grobid_parser import GROBIDTEIParser
from episteme_graph.agents.document_structure.parser import PDFBlockExtractor
from episteme_graph.agents.document_structure.schema import RawBlock
from episteme_graph.agents.document_structure.title_extraction import (
    TITLE_MAX_CHARS,
    acceptable_title,
    build_title_extraction,
    extract_title_from_layout,
    looks_like_non_title,
    normalize_title_text,
)

PAGE_HEIGHTS = {1: 792.0, 2: 792.0}


def _b(order, text, size, bbox, page=1, dominant=None):
    return RawBlock(
        page=page, order=order, text=text, bbox=bbox, font_size=size,
        dominant_font_size=dominant,
    )


# MNRAS（2605.26810 の 1 頁目の並び）
MNRAS_BLOCKS = [
    _b(0, "MNRAS 000, 1–16 (2025)\nPreprint 27 May 2026\nCompiled using MNRAS LATEX style file v3.2",
       8.0, (42.1, 30.2, 548.2, 40.0)),
    _b(1, "Magnetic field Topology and Star Formation in the Cepheus B\nFilamentary cloud under External Feedback",
       17.2, (42.1, 75.7, 463.6, 109.5)),
    _b(2, "Panigrahy Sandhyarani1★, Chakali Eswaraiah2,1† , Manash R. Samal3",
       11.0, (42.1, 120.5, 579.1, 151.7)),
    _b(3, "1Department of Physics, Indian Institute of Science Education and Research Tirupati",
       7.0, (44.1, 152.8, 580.8, 243.2)),
    _b(4, "Accepted XXX. Received YYY; in original form ZZZ", 8.0, (42.1, 266.2, 214.1, 274.2)),
    _b(5, "ABSTRACT\nWe present a detailed study of the Cep B molecular cloud based on sub-mm dust "
          "polarization and 13CO (J=3–2) spectral observations " * 3,
       9.0, (42.1, 300.1, 548.2, 468.9)),
    # arXiv の余白スタンプ（縦書き・大きい）
    _b(6, "arXiv:2605.26810v1 [astro-ph.GA] 27 May 2026", 20.0, (10.0, 220.0, 37.0, 570.0)),
    _b(7, "1 INTRODUCTION", 9.0, (42.1, 525.9, 122.5, 534.9)),
    _b(8, "Body text of the introduction paragraph that continues for a while. " * 6,
       9.0, (42.1, 540.0, 290.0, 760.0)),
]

# REVTeX / APS（2606.00411 の 1 頁目の並び）
APS_BLOCKS = [
    _b(0, "APS/123-QED", 10.0, (501.8, 29.1, 562.1, 38.1)),
    _b(1, "The sound of dynamical dark energy and modified gravity", 12.0, (136.6, 52.7, 479.5, 64.6)),
    _b(2, "Jo˜ao Rebou¸cas,1, 2 Guilherme Brando,2 Felipe T. Falciano,2 and Vivian Miranda3",
       10.0, (126.4, 74.5, 489.2, 88.1)),
    _b(3, "1Department of Astronomy/Steward Observatory, University of Arizona,\n933 North Cherry Avenue",
       9.0, (99.2, 91.4, 517.0, 154.2)),
    _b(4, "Different candidate models are able to reproduce the dynamical dark energy signal " * 4,
       10.0, (108.8, 162.1, 507.3, 338.5)),
    _b(5, "I.\nINTRODUCTION", 10.0, (124.3, 360.6, 228.8, 369.6)),
    _b(6, "One of the main scientific goals of modern cosmology is to uncover the nature of dark energy. " * 5,
       10.0, (54.0, 390.0, 299.1, 606.2)),
]

# AASTeX 7（2606.02318 の 1 頁目の並び。題名が 2 ブロックに割れる）
AAS_BLOCKS = [
    _b(0, "Draft version June 2, 2026\nTypeset using LATEX twocolumn style in AASTeX7.0.1",
       8.0, (59.7, 50.9, 268.8, 71.7)),
    _b(1, "The First Detection of Sub-Populations in the Delay-Time Distribution of Binary Black Holes in",
       12.0, (63.1, 108.5, 549.5, 118.5)),
    _b(2, "GWTC-4 of LIGO-Virgo-KAGRA", 12.0, (220.5, 120.9, 392.1, 130.9)),
    _b(3, "Shaunak Padhyegurjar\n1 and Suvodip Mukherjee\n1", 10.0, (180.1, 135.8, 427.1, 148.6)),
    _b(4, "1Department of Astronomy and Astrophysics, Tata Institute of Fundamental Research",
       8.0, (46.8, 158.1, 562.1, 176.7)),
    _b(5, "ABSTRACT", 10.0, (277.7, 191.6, 333.9, 201.6)),
    _b(6, "The imprint of different formation channels of binary black holes (BBHs) is encoded in the distri-\n"
          "bution of time delays " * 4, 10.0, (84.6, 206.9, 527.0, 366.3)),
    _b(7, "1. INTRODUCTION", 10.0, (124.1, 382.7, 218.8, 392.7)),
]


class TestNormalize:
    def test_single_line_and_whitespace(self):
        assert normalize_title_text("  A  study\n of \tthings  ") == "A study of things"

    def test_hyphenated_line_break_keeps_hyphen(self):
        assert normalize_title_text("Sub-\nPopulations of Physics-\ninformed nets") == (
            "Sub-Populations of Physics-informed nets"
        )

    def test_soft_hyphen_removed(self):
        assert normalize_title_text("Neu­tron stars") == "Neutron stars"

    def test_none(self):
        assert normalize_title_text(None) == ""


class TestNonTitle:
    @pytest.mark.parametrize("text", [
        "arXiv:2606.00411v1 [astro-ph.CO] 30 May 2026",
        "MNRAS 000, 1–16 (2025)",
        "Preprint 27 May 2026",
        "Draft version June 2, 2026",
        "Typeset using LATEX twocolumn style in AASTeX7.0.1",
        "APS/123-QED",
        "Accepted XXX. Received YYY; in original form ZZZ",
        "someone@example.org",
        "https://example.org/paper",
        "ABSTRACT",
        "",
    ])
    def test_rejected(self, text):
        assert looks_like_non_title(text)
        assert acceptable_title(text) is None

    @pytest.mark.parametrize("text", [
        "The sound of dynamical dark energy and modified gravity",
        "Neutron Star Equation of State via Physics Informed Neural Network",
        "GWTC-4 of LIGO-Virgo-KAGRA sub-populations",
    ])
    def test_accepted(self, text):
        assert acceptable_title(text) == text

    def test_too_long_rejected(self):
        assert acceptable_title("word " * (TITLE_MAX_CHARS // 4)) is None

    def test_single_word_rejected(self):
        assert acceptable_title("Letter") is None


class TestLayoutTitle:
    def test_mnras_skips_stamp_and_vertical_arxiv_mark(self):
        assert extract_title_from_layout(MNRAS_BLOCKS, PAGE_HEIGHTS) == (
            "Magnetic field Topology and Star Formation in the Cepheus B "
            "Filamentary cloud under External Feedback"
        )

    def test_aps_skips_report_number(self):
        assert extract_title_from_layout(APS_BLOCKS, PAGE_HEIGHTS) == (
            "The sound of dynamical dark energy and modified gravity"
        )

    def test_aastex_joins_split_title_and_stops_at_size_drop(self):
        assert extract_title_from_layout(AAS_BLOCKS, PAGE_HEIGHTS) == (
            "The First Detection of Sub-Populations in the Delay-Time Distribution of "
            "Binary Black Holes in GWTC-4 of LIGO-Virgo-KAGRA"
        )

    def test_order_is_by_position_not_stream_order(self):
        shuffled = list(reversed(AAS_BLOCKS))
        assert extract_title_from_layout(shuffled, PAGE_HEIGHTS).startswith("The First Detection")

    def test_no_distinct_large_font_returns_none(self):
        flat = [
            _b(0, "A plain first line of text", 10.0, (50, 50, 500, 60)),
            _b(1, "Another line of body text that is long " * 5, 10.0, (50, 70, 500, 200)),
        ]
        assert extract_title_from_layout(flat, PAGE_HEIGHTS) is None

    def test_large_text_in_bottom_half_is_ignored(self):
        blocks = [
            _b(0, "Body text paragraph that is long enough " * 5, 10.0, (50, 50, 500, 300)),
            _b(1, "Big Figure Label Here", 18.0, (50, 600, 500, 620)),
        ]
        assert extract_title_from_layout(blocks, PAGE_HEIGHTS) is None

    def test_only_first_page(self):
        blocks = [
            _b(0, "Body text paragraph that is long enough " * 5, 10.0, (50, 50, 500, 300)),
            _b(1, "Large Heading On Page Two", 18.0, (50, 50, 500, 70), page=2),
        ]
        assert extract_title_from_layout(blocks, PAGE_HEIGHTS) is None

    def test_dominant_size_preferred_over_average(self):
        # 脚注記号で平均が下がった題名ブロックでも、文字数加重の大きさで拾う。
        blocks = [
            _b(0, "A Title With A Footnote Mark∗", 13.0, (50, 50, 500, 70), dominant=17.0),
            _b(1, "Author One and Author Two", 13.5, (50, 80, 500, 95)),
            _b(2, "Body text paragraph that is long enough " * 6, 10.0, (50, 100, 500, 380)),
        ]
        assert extract_title_from_layout(blocks, PAGE_HEIGHTS) == "A Title With A Footnote Mark∗"

    def test_empty_inputs(self):
        assert extract_title_from_layout([], PAGE_HEIGHTS) is None
        assert extract_title_from_layout(None, None) is None


class TestProvenance:
    def test_tei_wins(self):
        title, ext = build_title_extraction(
            tei_candidates=["Real Title Of Paper"], font_size_title="Other Title Words"
        )
        assert title == "Real Title Of Paper"
        assert ext["source"] == "tei"
        assert ext["needs_review"] is False
        assert ext["candidate_sources"]["font_size"] == ["Other Title Words"]

    def test_font_size_is_provisional(self):
        title, ext = build_title_extraction(tei_candidates=[], font_size_title="Layout Title Words")
        assert title == "Layout Title Words"
        assert ext["source"] == "font_size"
        assert ext["needs_review"] is True
        assert "title_from_font_size" in ext["review_reasons"]

    def test_rejected_tei_is_kept_as_candidate(self):
        title, ext = build_title_extraction(
            tei_candidates=["Draft version June 2, 2026"], font_size_title="Layout Title Words"
        )
        assert title == "Layout Title Words"
        assert ext["candidate_sources"]["tei"] == ["Draft version June 2, 2026"]
        assert "tei_title_rejected" in ext["review_reasons"]

    def test_none(self):
        title, ext = build_title_extraction(tei_candidates=None, font_size_title=None)
        assert title is None
        assert ext["source"] == "none"
        assert ext["confidence"] == 0.0
        assert set(ext) == {"source", "confidence", "needs_review", "review_reasons", "candidate_sources"}


def _tei(title_stmt: str, analytic_title: str = "") -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <teiHeader>
    <fileDesc>
      <titleStmt>{title_stmt}</titleStmt>
      <sourceDesc><biblStruct><analytic>{analytic_title}</analytic></biblStruct></sourceDesc>
    </fileDesc>
  </teiHeader>
  <text><body><div><head>Introduction</head><p>Some body text here.</p></div></body>
  <back><listBibl><biblStruct><analytic><title level="a" type="main">Cited Paper Title Words</title>
  </analytic></biblStruct></listBibl></back></text>
</TEI>"""


class TestTeiTitle:
    def test_main_type_preferred(self):
        xml = _tei('<title level="m">Monograph Words Here</title>'
                   '<title level="a" type="main">Main Paper Title</title>')
        result = GROBIDTEIParser().parse(xml)
        assert result.metadata.title == "Main Paper Title"
        assert result.metadata.title_extraction["source"] == "tei"

    def test_multiline_title_is_single_line(self):
        xml = _tei('<title level="a" type="main">A Multi\n    Line <hi rend="italic">Title</hi></title>')
        assert GROBIDTEIParser().parse(xml).metadata.title == "A Multi Line Title"

    def test_empty_title_stmt_falls_to_analytic(self):
        xml = _tei('<title level="a" type="main"/>', '<title level="a" type="main">Analytic Title Words</title>')
        assert GROBIDTEIParser().parse(xml).metadata.title == "Analytic Title Words"

    def test_empty_everywhere_is_none_and_ignores_bibliography(self):
        result = GROBIDTEIParser().parse(_tei('<title level="a" type="main"/>'))
        assert result.metadata.title is None
        assert result.metadata.title_extraction["source"] == "none"

    def test_stamp_title_rejected(self):
        result = GROBIDTEIParser().parse(_tei('<title level="a" type="main">Draft version June 2, 2026</title>'))
        assert result.metadata.title is None
        assert result.metadata.title_extraction["candidate_sources"]["tei"] == ["Draft version June 2, 2026"]


@pytest.fixture
def fake_pdf(tmp_path) -> str:
    path = tmp_path / "2606.02318v1.pdf"
    path.write_bytes(b"%PDF-1.4 fake")
    return str(path)


class TestAgentFallbackChain:
    def test_pymupdf_path_sets_title_from_layout(self, fake_pdf):
        # 砂場で起きた経路: GROBID が TEI を返さず PyMuPDF だけで解析された。
        agent = DocumentStructureAgent()
        with patch.object(agent._extractor, "extract_blocks", return_value=AAS_BLOCKS), \
             patch.object(agent._extractor, "get_page_heights", return_value=PAGE_HEIGHTS):
            result = agent.run(fake_pdf)
        assert result.metadata.title.startswith("The First Detection of Sub-Populations")
        assert "\n" not in result.metadata.title
        assert result.metadata.title_extraction["source"] == "font_size"
        assert result.metadata.title_extraction["needs_review"] is True
        assert result.to_dict()["metadata"]["title_extraction"]["source"] == "font_size"

    def test_grobid_path_with_empty_header_uses_layout(self, fake_pdf):
        agent = DocumentStructureAgent(parser_backend="grobid_hybrid")
        with patch.object(agent._extractor, "extract_blocks", return_value=APS_BLOCKS), \
             patch.object(agent._extractor, "get_page_heights", return_value=PAGE_HEIGHTS):
            result = agent.run(fake_pdf, tei_xml=_tei('<title level="a" type="main"/>'))
        assert result.metadata.title == "The sound of dynamical dark energy and modified gravity"
        assert result.metadata.title_extraction["source"] == "font_size"

    def test_grobid_path_tei_title_wins(self, fake_pdf):
        agent = DocumentStructureAgent(parser_backend="grobid_hybrid")
        with patch.object(agent._extractor, "extract_blocks", return_value=APS_BLOCKS), \
             patch.object(agent._extractor, "get_page_heights", return_value=PAGE_HEIGHTS):
            result = agent.run(fake_pdf, tei_xml=_tei('<title level="a" type="main">Header Title Words</title>'))
        assert result.metadata.title == "Header Title Words"
        assert result.metadata.title_extraction["source"] == "tei"
        assert result.metadata.title_extraction["needs_review"] is False

    def test_no_signal_leaves_title_none(self, fake_pdf):
        agent = DocumentStructureAgent()
        flat = [RawBlock(page=1, order=0, text="Body text words " * 10, font_size=10.0)]
        with patch.object(agent._extractor, "extract_blocks", return_value=flat), \
             patch.object(agent._extractor, "get_page_heights", return_value=PAGE_HEIGHTS):
            result = agent.run(fake_pdf)
        assert result.metadata.title is None
        assert result.metadata.title_extraction["source"] == "none"


class TestDominantFontSize:
    def test_char_weighted(self):
        raw = {"lines": [{"spans": [
            {"text": "A Long Title Text", "size": 17.2},
            {"text": "∗", "size": 11.0},
        ]}]}
        assert PDFBlockExtractor._dominant_font_size(raw) == 17.2

    def test_empty(self):
        assert PDFBlockExtractor._dominant_font_size({"lines": []}) is None
