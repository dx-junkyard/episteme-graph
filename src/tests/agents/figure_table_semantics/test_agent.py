"""Tests for FigureTableSemanticsAgent."""
from __future__ import annotations

from episteme_graph.agents.document_structure.schema import (
    DocumentMetadata,
    DocumentStructureResult,
    Section,
    TypedBlock,
)
from episteme_graph.agents.figure_table_semantics import FigureTableSemanticsAgent


def _structure_with(blocks: list[TypedBlock]) -> DocumentStructureResult:
    return DocumentStructureResult(
        document_id="doc_test",
        source_file="/tmp/x.pdf",
        cartridge_id="particle_physics",
        metadata=DocumentMetadata(pages=10),
        sections=[Section(section_id="sec_a", title="A", level=1, order=1, page_start=1)],
        blocks=blocks,
    )


def test_extracts_figures_and_tables_from_captions():
    structure = _structure_with([
        TypedBlock(
            block_id="blk_p1_b1",
            page=1,
            order=1,
            text="Some body paragraph context for the comparison.",
            block_type="body_paragraph",
            section_id="sec_a",
        ),
        TypedBlock(
            block_id="blk_p1_b2",
            page=1,
            order=2,
            text="Figure 1: comparison of current vs. future projections of the form factor.",
            block_type="figure_caption",
            section_id="sec_a",
        ),
        TypedBlock(
            block_id="blk_p2_b1",
            page=2,
            order=10,
            text="Table 2: systematic uncertainty budget for each framework.",
            block_type="table_caption",
            section_id="sec_a",
        ),
    ])

    agent = FigureTableSemanticsAgent()
    result = agent.run(structure)

    assert len(result.figures) == 1
    assert len(result.tables) == 1

    fig = result.figures[0]
    assert fig.figure_id == "fig_1"
    assert "comparison" in fig.caption.lower()
    assert fig.figure_type == "comparison_plot"
    assert "current_vs_future" in fig.comparison_axes

    tbl = result.tables[0]
    assert tbl.table_id == "table_2"
    assert tbl.table_type == "uncertainty_table"


def test_evidence_index_attached():
    structure = _structure_with([
        TypedBlock(
            block_id="blk_fig",
            page=1,
            order=1,
            text="Figure 1: probability distribution of decay products.",
            block_type="figure_caption",
            section_id="sec_a",
        ),
    ])
    agent = FigureTableSemanticsAgent()
    result = agent.run(structure, evidence_index={"blk_fig": ["ev_0001"]})
    assert result.figures[0].source_evidence_ids == ["ev_0001"]
    assert result.figures[0].figure_type == "probability_distribution"


def test_empty_caption_emits_validation_warning():
    structure = _structure_with([
        TypedBlock(
            block_id="blk_fig",
            page=1,
            order=1,
            text="Figure 1: ",  # nothing meaningful after split
            block_type="figure_caption",
            section_id="sec_a",
        ),
    ])
    agent = FigureTableSemanticsAgent()
    result = agent.run(structure)
    rules = {i.rule_id for i in result.validation_issues}
    assert "figure_caption_empty" in rules
