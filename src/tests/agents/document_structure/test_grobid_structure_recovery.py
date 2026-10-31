"""GROBID TEI から落ちていた論文の層を回収するテスト。

対象（実測で全件落ちていた層）:
1. 付録 ``<back><div type="annex">``（節・段落・式ごと 0 件だった）
2. ``<body>`` 直下の ``<figure>`` / ``<figure type="table">``（caption 0 件だった）
3. ``head/@n`` / head 本文の番号からの level / parent_section_id 導出（全て level 1 だった）
4. REVTeX のピリオド区切り caption（``FIG. 1.`` / ``Table 1.``）
5. 走り込み見出し（``Introduction.—``）からの section 起こし
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

pytest.importorskip("bs4", reason="beautifulsoup4 not installed")

from episteme_graph.agents.document_structure.agent import (  # noqa: E402
    _PDF_FIGURE_CAPTION_RE,
    _PDF_TABLE_CAPTION_RE,
    DocumentStructureAgent,
)
from episteme_graph.agents.document_structure.grobid_parser import (  # noqa: E402
    GROBIDTEIParser,
)
from episteme_graph.agents.document_structure.parser import RawBlock  # noqa: E402
from episteme_graph.agents.document_structure.schema import TypedBlock  # noqa: E402


def _parse(tei: str):
    return GROBIDTEIParser().parse(tei)


# ---------------------------------------------------------------------------
# 1. Appendix (<back><div type="annex">)
# ---------------------------------------------------------------------------

_TEI_WITH_ANNEX = """\
<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <text>
    <body>
      <div><head n="1.">Introduction</head><p>Main body paragraph.</p></div>
    </body>
    <back>
      <div type="annex">
        <div>
          <head>Appendix A: Impact of tile size</head>
          <p>The tile size changes the estimator variance.</p>
          <formula xml:id="eqA1">V = a b <label>(A1)</label></formula>
        </div>
        <div>
          <head>Appendix B: Null tests</head>
          <p>All null tests pass.</p>
        </div>
      </div>
      <div type="acknowledgement">
        <div><head>Acknowledgments</head><p>We thank the reviewers.</p></div>
      </div>
      <div type="references"><listBibl><biblStruct/></listBibl></div>
    </back>
  </text>
</TEI>
"""


class TestAppendixRecovery:
    def test_annex_sections_become_sections(self):
        result = _parse(_TEI_WITH_ANNEX)
        titles = [s.title for s in result.sections]
        assert "Appendix A: Impact of tile size" in titles
        assert "Appendix B: Null tests" in titles

    def test_annex_sections_are_flagged_and_top_level(self):
        result = _parse(_TEI_WITH_ANNEX)
        appendix = [s for s in result.sections if s.is_appendix]
        assert len(appendix) == 2
        assert all(s.level == 1 for s in appendix)
        assert all(s.parent_section_id is None for s in appendix)

    def test_body_sections_are_not_flagged_as_appendix(self):
        result = _parse(_TEI_WITH_ANNEX)
        intro = next(s for s in result.sections if s.title == "Introduction")
        assert intro.is_appendix is False

    def test_annex_paragraphs_and_formulas_become_blocks(self):
        result = _parse(_TEI_WITH_ANNEX)
        texts = [b.text for b in result.blocks]
        assert any("estimator variance" in t for t in texts)
        assert any("All null tests pass" in t for t in texts)
        eq_labels = [b.equation_label for b in result.blocks if b.block_type == "equation_block"]
        assert "(A1)" in eq_labels

    def test_annex_blocks_carry_in_appendix_provenance(self):
        result = _parse(_TEI_WITH_ANNEX)
        appendix_ids = {s.section_id for s in result.sections if s.is_appendix}
        appendix_blocks = [b for b in result.blocks if b.section_id in appendix_ids]
        assert appendix_blocks
        assert all(b.raw.get("in_appendix") is True for b in appendix_blocks)

    def test_references_and_acknowledgments_still_excluded(self):
        result = _parse(_TEI_WITH_ANNEX)
        titles = [s.title for s in result.sections]
        assert "Acknowledgments" not in titles
        assert not any("thank the reviewers" in b.text for b in result.blocks)

    def test_structure_recovery_counts_appendix(self):
        result = _parse(_TEI_WITH_ANNEX)
        recovery = result.metadata.structure_recovery or {}
        assert recovery.get("appendix_sections") == 2
        assert recovery.get("appendix_blocks", 0) >= 3

    def test_annex_wrapper_with_its_own_head_becomes_one_section(self):
        tei = (
            '<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body>'
            '<div><head n="1.">Intro</head><p>Body.</p></div></body>'
            '<back><div type="appendix"><head>Appendix</head>'
            '<p>Single appendix paragraph.</p></div></back></text></TEI>'
        )
        result = _parse(tei)
        appendix = [s for s in result.sections if s.is_appendix]
        assert [s.title for s in appendix] == ["Appendix"]
        assert any("Single appendix paragraph" in b.text for b in result.blocks)


# ---------------------------------------------------------------------------
# 2. <body>-level <figure> / <figure type="table">
# ---------------------------------------------------------------------------

_TEI_BODY_LEVEL_FIGURES = """\
<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <text>
    <body>
      <div><head n="1.">Results</head><p>The layout is shown in Fig. 1.</p></div>
      <figure xml:id="fig_0">
        <head>FIG. 1.</head>
        <label>1</label>
        <figDesc>FIG. 1. Sketch of the apparatus.</figDesc>
        <graphic coords="3,100,200,300,400" />
      </figure>
      <figure type="table" xml:id="tab_0">
        <head>TABLE I.</head>
        <figDesc>TABLE I. Fit results for each configuration.</figDesc>
        <table>
          <row><cell>Parameter</cell><cell>Value</cell></row>
          <row><cell>alpha</cell><cell>0.51</cell></row>
        </table>
      </figure>
    </body>
  </text>
</TEI>
"""


class TestBodyLevelFigures:
    def test_body_level_figure_becomes_figure_caption(self):
        result = _parse(_TEI_BODY_LEVEL_FIGURES)
        captions = [b for b in result.blocks if b.block_type == "figure_caption"]
        assert len(captions) == 1
        assert captions[0].text.startswith("FIG. 1.")
        assert captions[0].raw.get("tei_container") == "body"
        assert captions[0].raw.get("tei_fig_id") == "fig_0"

    def test_body_level_table_becomes_table_caption(self):
        result = _parse(_TEI_BODY_LEVEL_FIGURES)
        captions = [b for b in result.blocks if b.block_type == "table_caption"]
        assert len(captions) == 1
        assert captions[0].text.startswith("TABLE I.")

    def test_caption_keeps_figure_number_for_figure_key_derivation(self):
        """``document_figures.figure_key`` は caption 先頭のラベルから導く。

        backend の ``_normalize_figure_key`` は ``^(?:Figure|Fig\\.?)\\s*([0-9A-Za-z.]+)``
        で番号を読むので、caption 本文が図番号で始まっていることが前提になる。
        """
        import re

        result = _parse(_TEI_BODY_LEVEL_FIGURES)
        caption = next(b for b in result.blocks if b.block_type == "figure_caption")
        match = re.match(r"^(?:Figure|Fig\.?)\s*([0-9A-Za-z.]+)", caption.text, re.IGNORECASE)
        assert match is not None
        assert match.group(1).rstrip(".") == "1"

    def test_table_body_is_kept_as_text_with_table_provenance(self):
        result = _parse(_TEI_BODY_LEVEL_FIGURES)
        table_bodies = [
            b for b in result.blocks
            if b.block_type == "body_paragraph" and b.raw.get("in_table")
        ]
        assert len(table_bodies) == 1
        body = table_bodies[0]
        assert "Parameter | Value" in body.text
        assert "alpha | 0.51" in body.text
        assert body.raw.get("container_type") == "table"
        assert body.raw.get("table_id") == "tab_0"
        assert body.raw.get("table_rows") == 2

    def test_figure_caption_falls_back_to_head_label_prefix(self):
        tei = (
            '<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body>'
            '<div><head n="1.">Results</head><p>Body.</p></div>'
            '<figure xml:id="fig_1"><head>Fig. 2.</head>'
            '<figDesc>Layout of the detector.</figDesc></figure>'
            '</body></text></TEI>'
        )
        result = _parse(tei)
        caption = next(b for b in result.blocks if b.block_type == "figure_caption")
        assert caption.text == "Fig. 2. Layout of the detector."

    def test_table_without_figdesc_uses_head(self):
        tei = (
            '<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body>'
            '<figure type="table" xml:id="tab_9"><head>TABLE II. Systematics budget</head>'
            '<table><row><cell>source</cell><cell>size</cell></row></table>'
            '</figure></body></text></TEI>'
        )
        result = _parse(tei)
        caption = next(b for b in result.blocks if b.block_type == "table_caption")
        assert caption.text == "TABLE II. Systematics budget"

    def test_figures_under_back_are_recovered(self):
        tei = (
            '<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body>'
            '<div><head n="1.">Intro</head><p>Body.</p></div></body>'
            '<back><figure xml:id="fig_b"><figDesc>FIG. 7. Appendix figure.</figDesc>'
            '</figure></back></text></TEI>'
        )
        result = _parse(tei)
        captions = [b for b in result.blocks if b.block_type == "figure_caption"]
        assert len(captions) == 1
        assert captions[0].raw.get("in_appendix") is True

    def test_nested_figure_inside_div_still_works(self):
        tei = (
            '<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body>'
            '<div><head n="1.">Results</head><p>Body.</p>'
            '<figure><figDesc>Figure 1. Inline figure.</figDesc></figure>'
            '</div></body></text></TEI>'
        )
        result = _parse(tei)
        caption = next(b for b in result.blocks if b.block_type == "figure_caption")
        assert caption.section_id is not None
        assert caption.raw.get("tei_container") == "div"


# ---------------------------------------------------------------------------
# 3. head/@n → level / parent_section_id
# ---------------------------------------------------------------------------

class TestSectionNumbering:
    def test_head_n_attribute_sets_level_and_parent(self):
        tei = (
            '<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body>'
            '<div><head n="3.">Methodology</head><p>a</p></div>'
            '<div><head n="3.1.">Systematics</head><p>b</p></div>'
            '<div><head n="3.1.2.">Photometry</head><p>c</p></div>'
            '</body></text></TEI>'
        )
        result = _parse(tei)
        by_title = {s.title: s for s in result.sections}
        assert by_title["Methodology"].level == 1
        assert by_title["Systematics"].level == 2
        assert by_title["Systematics"].parent_section_id == by_title["Methodology"].section_id
        assert by_title["Photometry"].level == 3
        assert by_title["Photometry"].parent_section_id == by_title["Systematics"].section_id

    def test_section_number_is_recorded(self):
        tei = (
            '<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body>'
            '<div><head n="3.1.">Systematics</head><p>b</p></div>'
            '</body></text></TEI>'
        )
        result = _parse(tei)
        assert result.sections[0].section_number == "3.1"

    def test_number_in_head_text_is_used_when_attribute_missing(self):
        tei = (
            '<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body>'
            '<div><head>A. Useful relations</head><p>a</p></div>'
            '<div><head>A.2 Radial and angular decomposition</head><p>b</p></div>'
            '</body></text></TEI>'
        )
        result = _parse(tei)
        parent, child = result.sections
        assert parent.section_number == "A"
        assert child.section_number == "A.2"
        assert child.level == 2
        assert child.parent_section_id == parent.section_id

    def test_plain_heading_starting_with_capital_letter_is_not_a_number(self):
        """``A new formalism ...`` を付録 A と誤認しない。"""
        tei = (
            '<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body>'
            '<div><head>A new formalism for angular polyspectra</head><p>a</p></div>'
            '</body></text></TEI>'
        )
        result = _parse(tei)
        section = result.sections[0]
        assert section.section_number is None
        assert section.level == 1

    def test_unnumbered_nested_divs_keep_nesting_level(self):
        tei = (
            '<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body>'
            '<div><head>Methods</head><p>a</p>'
            '<div><head>Sub</head><p>b</p></div></div>'
            '</body></text></TEI>'
        )
        result = _parse(tei)
        parent, child = result.sections
        assert child.level == 2
        assert child.parent_section_id == parent.section_id

    def test_orphan_subsection_number_attaches_to_nearest_shallower_section(self):
        tei = (
            '<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body>'
            '<div><head n="2.">Formalism</head><p>a</p></div>'
            '<div><head n="3.1.">Systematics</head><p>b</p></div>'
            '</body></text></TEI>'
        )
        result = _parse(tei)
        formalism, systematics = result.sections
        assert systematics.level == 2
        assert systematics.parent_section_id == formalism.section_id


# ---------------------------------------------------------------------------
# 4. REVTeX period-separated captions (PyMuPDF supplement)
# ---------------------------------------------------------------------------

class TestPdfCaptionPatterns:
    @pytest.mark.parametrize("text", [
        "FIG. 1. Sketch of the interferometer.",
        "Figure 2. The measured spectrum.",
        "Fig. 3.2. Residuals after subtraction.",
        "Figure 6 .8: Transmissions under resonance.",
        "Figure 4 - Detector response.",
        "図 5. 装置の概略.",
    ])
    def test_figure_caption_patterns_match(self, text):
        assert _PDF_FIGURE_CAPTION_RE.match(text)

    @pytest.mark.parametrize("text", [
        "Figure 6 .9 shows the measured transfer function.",
        "Figure 2 shows the spectrum.",
        "Fig. 1 is discussed below.",
        "As shown in Figure 3. the trend continues.",
    ])
    def test_body_references_do_not_match_figure_pattern(self, text):
        assert not _PDF_FIGURE_CAPTION_RE.match(text)

    @pytest.mark.parametrize("text", [
        "TABLE I. Fit results.",
        "Table 1. Summary statistics.",
        "Table 2: Systematic error budget.",
        "表 3. 測定結果.",
    ])
    def test_table_caption_patterns_match(self, text):
        assert _PDF_TABLE_CAPTION_RE.match(text)

    @pytest.mark.parametrize("text", [
        "Table 2 lists the systematic errors.",
        "The values in Table 1 are consistent.",
    ])
    def test_body_references_do_not_match_table_pattern(self, text):
        assert not _PDF_TABLE_CAPTION_RE.match(text)

    def test_supplement_adds_table_caption_block(self):
        typed_blocks = [TypedBlock(
            block_id="body-1",
            page=4,
            order=10,
            text="Nearby discussion of the fit.",
            block_type="body_paragraph",
            section_id="sec-2",
            raw={"pdf_alignment": {"page": 4, "pdf_order": 10, "score": 0.92}},
        )]
        pdf_blocks = [
            RawBlock(page=4, order=11, text="TABLE I. Fit results for each configuration.",
                     bbox=(10, 20, 30, 40)),
        ]

        DocumentStructureAgent._supplement_grobid_figure_captions(typed_blocks, pdf_blocks)

        tables = [b for b in typed_blocks if b.block_type == "table_caption"]
        assert len(tables) == 1
        assert tables[0].section_id == "sec-2"
        assert tables[0].raw["parser_source"] == "grobid_hybrid_pdf_supplement"

    def test_supplement_does_not_duplicate_tei_caption(self):
        typed_blocks = [TypedBlock(
            block_id="tei-cap",
            page=1,
            order=5,
            text="FIG. 1. Sketch of the apparatus.",
            block_type="figure_caption",
            section_id=None,
        )]
        pdf_blocks = [
            RawBlock(page=3, order=40, text="FIG. 1. Sketch of the apparatus .",
                     bbox=(10, 20, 30, 40)),
        ]

        DocumentStructureAgent._supplement_grobid_figure_captions(typed_blocks, pdf_blocks)

        captions = [b for b in typed_blocks if b.block_type == "figure_caption"]
        assert len(captions) == 1

    def test_global_caption_alignment_gives_real_page_to_body_level_caption(self):
        typed_blocks = [TypedBlock(
            block_id="tei-cap",
            page=1,
            order=90,
            text="FIG. 1. Sketch of the apparatus used in this measurement.",
            block_type="figure_caption",
            section_id=None,
            raw={"parser_source": "grobid_tei"},
        )]
        pdf_blocks = [
            RawBlock(page=1, order=0, text="Unrelated title page text of the paper."),
            RawBlock(page=5, order=42,
                     text="FIG. 1. Sketch of the apparatus used in this measurement.",
                     bbox=(1, 2, 3, 4)),
        ]

        DocumentStructureAgent._align_caption_blocks_globally(typed_blocks, pdf_blocks)

        assert typed_blocks[0].page == 5
        assert typed_blocks[0].bbox == (1, 2, 3, 4)
        assert typed_blocks[0].raw["pdf_alignment"]["source"] == "global_caption_scan"

    def test_global_caption_alignment_keeps_page_when_no_good_match(self):
        typed_blocks = [TypedBlock(
            block_id="tei-cap",
            page=1,
            order=90,
            text="FIG. 9. A caption that is absent from the PDF text layer.",
            block_type="figure_caption",
            section_id=None,
            raw={"parser_source": "grobid_tei"},
        )]
        pdf_blocks = [RawBlock(page=5, order=42, text="Completely different wording here.")]

        DocumentStructureAgent._align_caption_blocks_globally(typed_blocks, pdf_blocks)

        assert typed_blocks[0].page == 1
        assert "pdf_alignment" not in typed_blocks[0].raw


# ---------------------------------------------------------------------------
# 5. Run-in headings (PRL style)
# ---------------------------------------------------------------------------

_TEI_RUN_IN = """\
<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <text>
    <body>
      <div>
        <p>Introduction.-Detecting a primordial gravitational-wave background
        would allow us to observe the early universe.</p>
        <p>A Gaussian stochastic background signal will be obscured by a foreground.</p>
        <p>The templated background search.-We seek to simultaneously fit the
        parameters of binary black hole mergers.</p>
        <formula xml:id="e1">Omega = A f <label>(1)</label></formula>
      </div>
    </body>
  </text>
</TEI>
"""


class TestRunInHeadings:
    def test_run_in_headings_create_sections(self):
        result = _parse(_TEI_RUN_IN)
        titles = [s.title for s in result.sections]
        assert "Introduction" in titles
        assert "The templated background search" in titles

    def test_paragraph_text_is_not_truncated(self):
        result = _parse(_TEI_RUN_IN)
        assert any(b.text.startswith("Introduction.-Detecting") for b in result.blocks)

    def test_following_blocks_join_the_run_in_section(self):
        result = _parse(_TEI_RUN_IN)
        intro = next(s for s in result.sections if s.title == "Introduction")
        search = next(
            s for s in result.sections if s.title == "The templated background search"
        )
        by_section = {}
        for block in result.blocks:
            by_section.setdefault(block.section_id, []).append(block)
        assert len(by_section[intro.section_id]) == 2  # run-in para + next para
        assert len(by_section[search.section_id]) == 2  # run-in para + formula

    def test_structure_recovery_counts_run_in_headings(self):
        result = _parse(_TEI_RUN_IN)
        assert (result.metadata.structure_recovery or {}).get("run_in_headings") == 2

    def test_run_in_detection_is_conservative(self):
        detect = GROBIDTEIParser._detect_run_in_heading
        assert detect("Introduction.-Detecting a background") == "Introduction"
        assert detect("Results and discussion.—We find that") == "Results and discussion"
        # 図参照・数式ラベル・普通の文は見出しにしない
        assert detect("FIG. 1.-Sketch of the apparatus") is None
        assert detect("We find that the amplitude is small.") is None
        assert detect("The measured value.-is consistent") is None
        assert detect(
            "A very long sentence with far too many words to be a run in heading.-Yes"
        ) is None

    def test_run_in_headings_are_not_started_when_grobid_gave_a_head(self):
        tei = (
            '<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body>'
            '<div><head n="1.">Introduction</head>'
            '<p>Motivation.-We start from the observation that ...</p>'
            '</div></body></text></TEI>'
        )
        result = _parse(tei)
        assert [s.title for s in result.sections] == ["Introduction"]


# ---------------------------------------------------------------------------
# 6. 要旨末尾に連結された表の数値羅列
# ---------------------------------------------------------------------------

_ABSTRACT_PROSE = (
    "We revisit sixty publicly available gravitational wave events and reanalyse "
    "them with several waveform models to quantify the systematic differences "
    "between the analyses that have been published so far by different groups. "
    "These results emphasize the need to use multiple state of the art waveform "
    "models and carefully validated analysis settings to characterize uncertainty "
    "in gravitational wave source properties."
)
_ABSTRACT_TABLE_DUMP = " ".join(
    ["-1.07", "34.91", "+4.67", "-3.63", "25.00", "+3.17", "-3.46", "0.38", "20", "2.12"] * 12
)


class TestAbstractNumericDump:
    def _tei(self, abstract_text: str) -> str:
        return (
            '<TEI xmlns="http://www.tei-c.org/ns/1.0"><teiHeader><profileDesc>'
            f"<abstract><div><p>{abstract_text}</p></div></abstract>"
            '</profileDesc></teiHeader><text><body>'
            '<div><head n="1.">Intro</head><p>Body.</p></div>'
            '</body></text></TEI>'
        )

    def test_trailing_table_numbers_move_to_their_own_block(self):
        result = _parse(self._tei(f"{_ABSTRACT_PROSE} {_ABSTRACT_TABLE_DUMP}"))
        abstract_blocks = [b for b in result.blocks if b.section_id == "sec_abstract"]
        assert len(abstract_blocks) == 2
        prose, dump = abstract_blocks
        assert prose.text.endswith("source properties.")
        assert "34.91" not in prose.text
        assert dump.text.startswith("-1.07")
        assert dump.raw.get("detached_from_abstract") is True
        assert dump.raw.get("numeric_table_like") is True

    def test_no_text_is_lost(self):
        raw_text = f"{_ABSTRACT_PROSE} {_ABSTRACT_TABLE_DUMP}"
        result = _parse(self._tei(raw_text))
        joined = " ".join(
            b.text for b in result.blocks if b.section_id == "sec_abstract"
        )
        assert joined.split() == raw_text.split()

    def test_plain_abstract_stays_one_block(self):
        result = _parse(self._tei(_ABSTRACT_PROSE))
        abstract_blocks = [b for b in result.blocks if b.section_id == "sec_abstract"]
        assert len(abstract_blocks) == 1
        assert not (result.metadata.structure_recovery or {}).get("abstract_numeric_dumps")

    def test_split_helper_leaves_numeric_prose_alone(self):
        """数値を多く含むが散文の要旨は切らない。"""
        text = (
            "We measure the Hubble constant to be 70.1 km/s/Mpc with a 1.2 percent "
            "uncertainty, improving on the previous value of 69.8 km/s/Mpc reported "
            "in 2019, and we compare it with the 67.4 km/s/Mpc inferred from the "
            "cosmic microwave background under the standard model assumptions. "
        ) * 4
        prose, dump = GROBIDTEIParser._split_trailing_numeric_dump(text)
        assert dump == ""
        assert prose == text


# ---------------------------------------------------------------------------
# Ordering invariants must survive all of the above
# ---------------------------------------------------------------------------

class TestOrderingInvariants:
    @pytest.mark.parametrize("tei", [_TEI_WITH_ANNEX, _TEI_BODY_LEVEL_FIGURES, _TEI_RUN_IN])
    def test_orders_are_monotonic(self, tei):
        result = _parse(tei)
        assert [s.order for s in result.sections] == sorted(s.order for s in result.sections)
        assert [b.order for b in result.blocks] == sorted(b.order for b in result.blocks)

    @pytest.mark.parametrize("tei", [_TEI_WITH_ANNEX, _TEI_BODY_LEVEL_FIGURES, _TEI_RUN_IN])
    def test_section_ids_are_unique(self, tei):
        result = _parse(tei)
        ids = [s.section_id for s in result.sections]
        assert len(ids) == len(set(ids))
