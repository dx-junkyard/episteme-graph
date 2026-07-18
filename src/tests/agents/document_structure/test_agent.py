"""Integration test for DocumentStructureAgent with a mocked PDF extractor."""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from episteme_graph.agents.document_structure.agent import DocumentStructureAgent
from episteme_graph.agents.document_structure.schema import RawBlock, DocumentStructureResult


# Minimal set of raw blocks that simulate a 3-page academic paper
MOCK_BLOCKS = [
    RawBlock(page=1, order=0, text="Abstract", is_bold=True, font_size=12.0),
    RawBlock(page=1, order=1, text="This paper presents a method.", font_size=11.0),
    RawBlock(page=2, order=2, text="1 Introduction", font_size=11.0),
    RawBlock(page=2, order=3, text="Recent work has shown that...", font_size=11.0),
    RawBlock(page=2, order=4, text="Figure 1. The proposed pipeline.", font_size=11.0),
    RawBlock(page=3, order=5, text="2 Related Work", font_size=11.0),
    RawBlock(page=3, order=6, text="E = mc² (1)", is_centered=True, font_size=11.0),
    RawBlock(page=3, order=7, text="References", is_bold=True, font_size=12.0),
    RawBlock(page=3, order=8, text="[1] Smith et al. (2020) Nature.", font_size=11.0),
]

MOCK_PAGE_HEIGHTS = {1: 792.0, 2: 792.0, 3: 792.0}


@pytest.fixture
def agent(tmp_path) -> DocumentStructureAgent:
    return DocumentStructureAgent()


@pytest.fixture
def fake_pdf(tmp_path) -> str:
    pdf_path = str(tmp_path / "test_paper.pdf")
    # Create a dummy file (agent mock will intercept before reading)
    with open(pdf_path, "wb") as f:
        f.write(b"%PDF-1.4 fake")
    return pdf_path


def run_agent_with_mock(agent: DocumentStructureAgent, pdf_path: str) -> DocumentStructureResult:
    with patch.object(agent._extractor, "extract_blocks", return_value=MOCK_BLOCKS), \
         patch.object(agent._extractor, "get_page_heights", return_value=MOCK_PAGE_HEIGHTS):
        return agent.run(pdf_path)


def test_run_returns_result(agent, fake_pdf):
    result = run_agent_with_mock(agent, fake_pdf)
    assert isinstance(result, DocumentStructureResult)


def test_document_id_derived_from_filename(agent, fake_pdf):
    result = run_agent_with_mock(agent, fake_pdf)
    assert "test_paper" in result.document_id


def test_blocks_classified(agent, fake_pdf):
    result = run_agent_with_mock(agent, fake_pdf)
    types = {b.block_type for b in result.blocks}
    assert "section_heading" in types
    assert "body_paragraph" in types
    assert "reference_entry" in types


def test_sections_built(agent, fake_pdf):
    result = run_agent_with_mock(agent, fake_pdf)
    assert len(result.sections) > 0
    titles = [s.title for s in result.sections]
    assert any("Introduction" in t for t in titles)


def test_figure_caption_detected(agent, fake_pdf):
    result = run_agent_with_mock(agent, fake_pdf)
    caption_blocks = [b for b in result.blocks if b.block_type == "figure_caption"]
    assert len(caption_blocks) >= 1


def test_equation_detected(agent, fake_pdf):
    result = run_agent_with_mock(agent, fake_pdf)
    eq_blocks = [b for b in result.blocks if b.block_type == "equation_block"]
    assert len(eq_blocks) >= 1


def test_validation_issues_list(agent, fake_pdf):
    result = run_agent_with_mock(agent, fake_pdf)
    assert isinstance(result.validation_issues, list)


def test_source_file_is_absolute(agent, fake_pdf):
    result = run_agent_with_mock(agent, fake_pdf)
    assert os.path.isabs(result.source_file)


def test_cartridge_id_none_when_not_provided(agent, fake_pdf):
    result = run_agent_with_mock(agent, fake_pdf)
    assert result.cartridge_id is None


def test_run_without_cartridge_does_not_raise(agent, fake_pdf):
    # Should work without any cartridge
    result = run_agent_with_mock(agent, fake_pdf)
    assert result is not None


def test_to_json_round_trip(agent, fake_pdf):
    import json
    result = run_agent_with_mock(agent, fake_pdf)
    json_str = result.to_json()
    d = json.loads(json_str)
    assert d["document_id"] == result.document_id
    assert len(d["blocks"]) == len(result.blocks)


def test_metadata_uses_source_page_count_not_last_block_page(agent, fake_pdf):
    page_heights = {page: 792.0 for page in range(1, 21)}
    with patch.object(agent._extractor, "extract_blocks", return_value=MOCK_BLOCKS), \
         patch.object(agent._extractor, "get_page_heights", return_value=page_heights):
        result = agent.run(fake_pdf, config={"max_pages": 3})

    assert result.metadata.pages == 20
    assert result.metadata.parser_pages_processed == 3
    assert result.metadata.parser_reached_eof is False
    assert max(block.page for block in result.blocks) == 3


def test_metadata_records_full_scan_when_trailing_pages_are_blank(agent, fake_pdf):
    page_heights = {page: 792.0 for page in range(1, 21)}
    with patch.object(agent._extractor, "extract_blocks", return_value=MOCK_BLOCKS), \
         patch.object(agent._extractor, "get_page_heights", return_value=page_heights):
        result = agent.run(fake_pdf)

    assert result.metadata.pages == 20
    assert result.metadata.parser_pages_processed == 20
    assert result.metadata.parser_reached_eof is True


def test_grobid_hybrid_alignment_updates_block_and_section_pages():
    from episteme_graph.agents.document_structure.agent import DocumentStructureAgent
    from episteme_graph.agents.document_structure.schema import Section, TypedBlock
    from episteme_graph.agents.document_structure.parser import RawBlock

    agent = DocumentStructureAgent()
    typed_blocks = [
        TypedBlock(
            block_id="b1",
            page=1,
            order=0,
            text="This paragraph begins on the second physical page.",
            block_type="body_paragraph",
            section_id="s1",
            raw={"parser_source": "grobid_tei"},
        )
    ]
    pymupdf_blocks = [
        RawBlock(page=1, order=0, text="Title page text"),
        RawBlock(page=2, order=1, text="This paragraph begins on the second physical page."),
    ]
    sections = [Section(section_id="s1", title="Intro", level=1, order=1, page_start=1)]

    agent._align_grobid_blocks_to_pdf_blocks(typed_blocks, pymupdf_blocks)
    agent._refresh_section_pages_from_blocks(sections, typed_blocks, total_pages=2)

    assert typed_blocks[0].page == 2
    assert typed_blocks[0].raw["pdf_alignment"]["page"] == 2
    assert sections[0].page_start == 2
    assert sections[0].page_end == 2


def test_grobid_hybrid_alignment_accepts_substantial_pdf_fragment_inside_long_paragraph():
    """十分長いPyMuPDF断片なら、長いGROBID段落内の完全一致をpage anchorにする。"""
    from episteme_graph.agents.document_structure.agent import DocumentStructureAgent
    from episteme_graph.agents.document_structure.parser import RawBlock

    fragment = (
        "The optical cavity uses a high finesse bow tie geometry and preserves the polarization "
        "state across repeated round trips."
    )
    target = DocumentStructureAgent._normalize_match_text(
        fragment + " This paragraph continues with substantially more explanatory text."
    )
    raw = RawBlock(page=39, order=120, text=fragment)
    records = [(0, raw, DocumentStructureAgent._normalize_match_text(raw.text))]

    match = DocumentStructureAgent._find_best_pdf_match(target, records, 0)

    assert match is not None
    assert match[1].page == 39
    assert match[2] >= 0.92


def test_grobid_hybrid_alignment_uses_filtered_record_indices():
    """空raw blockの除外後も単調カーソルとlist sliceの座標を一致させる。"""
    from episteme_graph.agents.document_structure.agent import DocumentStructureAgent
    from episteme_graph.agents.document_structure.schema import TypedBlock
    from episteme_graph.agents.document_structure.parser import RawBlock

    typed_blocks = [
        TypedBlock("t1", 1, 0, "First substantial paragraph used as an anchor.", "body_paragraph"),
        TypedBlock("t2", 1, 1, "Second substantial paragraph immediately after it.", "body_paragraph"),
    ]
    raw_blocks = [
        RawBlock(page=1, order=i, text="") for i in range(5)
    ] + [
        RawBlock(page=38, order=5, text=typed_blocks[0].text),
        RawBlock(page=39, order=6, text=typed_blocks[1].text),
    ]

    DocumentStructureAgent._align_grobid_blocks_to_pdf_blocks(typed_blocks, raw_blocks)

    assert [block.page for block in typed_blocks] == [38, 39]
    assert [block.raw["pdf_alignment"]["pdf_order"] for block in typed_blocks] == [5, 6]


def test_grobid_hybrid_alignment_rejects_short_label_inside_long_paragraph():
    """図中の短い軸ラベルを段落のpage anchorとして採用しない。"""
    from episteme_graph.agents.document_structure.agent import DocumentStructureAgent
    from episteme_graph.agents.document_structure.parser import RawBlock

    target = DocumentStructureAgent._normalize_match_text(
        "Figure 2.2 shows upper limits on the axion-photon coupling constant from experiments."
    )
    raw = RawBlock(page=35, order=404, text="Axion-photon coupling constant")
    records = [(0, raw, DocumentStructureAgent._normalize_match_text(raw.text))]

    assert DocumentStructureAgent._find_best_pdf_match(target, records, 0) is None


def test_grobid_hybrid_alignment_rejects_repeated_short_number():
    from episteme_graph.agents.document_structure.agent import DocumentStructureAgent
    from episteme_graph.agents.document_structure.parser import RawBlock

    raw = RawBlock(page=77, order=1214, text="3)")
    records = [(0, raw, DocumentStructureAgent._normalize_match_text(raw.text))]

    assert DocumentStructureAgent._find_best_pdf_match("3", records, 0) is None


def test_grobid_hybrid_supplements_only_explicit_pdf_figure_captions():
    """colon付きcaptionを補い、本文の ``Figure ... shows`` は追加しない。"""
    from episteme_graph.agents.document_structure.agent import DocumentStructureAgent
    from episteme_graph.agents.document_structure.schema import TypedBlock
    from episteme_graph.agents.document_structure.parser import RawBlock

    typed_blocks = [TypedBlock(
        block_id="body-1",
        page=39,
        order=118,
        text="Nearby discussion of simultaneous resonance.",
        block_type="body_paragraph",
        section_id="sec-6",
        raw={"pdf_alignment": {"page": 39, "pdf_order": 118, "score": 0.92}},
    )]
    pdf_blocks = [
        RawBlock(
            page=39,
            order=120,
            text="Figure 6 .8: Transmissions under the simultaneous resonance condition.",
            bbox=(100, 400, 500, 450),
        ),
        RawBlock(
            page=39,
            order=121,
            text="Figure 6 .9 shows the measured transfer function from PDs to PDp.",
        ),
    ]

    DocumentStructureAgent._supplement_grobid_figure_captions(typed_blocks, pdf_blocks)

    captions = [block for block in typed_blocks if block.block_type == "figure_caption"]
    assert len(captions) == 1
    assert captions[0].page == 39
    assert captions[0].section_id == "sec-6"
    assert captions[0].bbox == (100, 400, 500, 450)
    assert captions[0].raw["parser_source"] == "grobid_hybrid_pdf_supplement"
