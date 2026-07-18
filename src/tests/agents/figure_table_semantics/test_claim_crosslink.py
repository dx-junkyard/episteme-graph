"""Tests for the F1 mention-based claim <-> figure/table cross-link pass.

design: docs/features/figure_concept_linking_design.md (F1)
"""
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


def _figure_caption_block(block_id: str, order: int, text: str) -> TypedBlock:
    return TypedBlock(
        block_id=block_id,
        page=1,
        order=order,
        text=text,
        block_type="figure_caption",
        section_id="sec_a",
    )


def _table_caption_block(block_id: str, order: int, text: str) -> TypedBlock:
    return TypedBlock(
        block_id=block_id,
        page=1,
        order=order,
        text=text,
        block_type="table_caption",
        section_id="sec_a",
    )


def _body_block(block_id: str, order: int, text: str) -> TypedBlock:
    return TypedBlock(
        block_id=block_id,
        page=1,
        order=order,
        text=text,
        block_type="body_paragraph",
        section_id="sec_a",
    )


def test_mention_variants_link_claims_to_figure():
    """(a) Fig./Figure/図 variants of "5.2" all resolve to the same figure."""
    variants = [
        "Fig. 5.2 shows the setup.",
        "Figure 5.2 shows the setup.",
        "図5.2 shows the setup.",
    ]
    for text in variants:
        structure = _structure_with([
            _figure_caption_block("blk_fig", 1, "Figure 5.2: comparison plot."),
            _body_block("blk_body", 2, text),
        ])
        agent = FigureTableSemanticsAgent()
        result = agent.run(
            structure,
            claim_link_index={"blk_body": ["claim_mention"]},
        )
        assert result.figures[0].figure_id == "fig_5.2"
        assert result.figures[0].linked_claim_ids == ["claim_mention"], text


def test_figs_abbreviation_with_plain_number():
    """(a) "Figs. 3" (plural abbreviation) links to figure 3."""
    structure = _structure_with([
        _figure_caption_block("blk_fig", 1, "Figure 3: overview diagram."),
        _body_block("blk_body", 2, "Figs. 3 illustrates the overall trend."),
    ])
    agent = FigureTableSemanticsAgent()
    result = agent.run(structure, claim_link_index={"blk_body": ["claim_a"]})
    assert result.figures[0].figure_id == "fig_3"
    assert result.figures[0].linked_claim_ids == ["claim_a"]


def test_mention_does_not_false_match_longer_number():
    """(b) "Fig. 5.21" must not cross-link to figure 5.2."""
    structure = _structure_with([
        _figure_caption_block("blk_fig", 1, "Figure 5.2: comparison plot."),
        _body_block("blk_body", 2, "Fig. 5.21 shows something unrelated."),
    ])
    agent = FigureTableSemanticsAgent()
    result = agent.run(structure, claim_link_index={"blk_body": ["claim_a"]})
    assert result.figures[0].linked_claim_ids == []


def test_block_without_claim_link_produces_no_link():
    """(c) A mentioning block with no entry in claim_link_index yields no link."""
    structure = _structure_with([
        _figure_caption_block("blk_fig", 1, "Figure 5.2: comparison plot."),
        _body_block("blk_body", 2, "Fig. 5.2 shows the setup."),
    ])
    agent = FigureTableSemanticsAgent()
    result = agent.run(structure, claim_link_index={})
    assert result.figures[0].linked_claim_ids == []


def test_dedup_between_caption_and_mention_link():
    """(d) A claim linked via both caption block_id and mention is not duplicated."""
    structure = _structure_with([
        _figure_caption_block("blk_fig", 1, "Figure 5.2: comparison plot."),
        _body_block("blk_body", 2, "Fig. 5.2 shows the setup."),
    ])
    agent = FigureTableSemanticsAgent()
    result = agent.run(
        structure,
        claim_link_index={
            "blk_fig": ["claim_shared"],
            "blk_body": ["claim_shared", "claim_extra"],
        },
    )
    # Caption-derived id stays first; mention-derived additions are appended,
    # de-duplicated, in document order.
    assert result.figures[0].linked_claim_ids == ["claim_shared", "claim_extra"]


def test_no_mention_figure_keeps_empty_links_and_warning():
    """(e) A figure never mentioned in the body keeps linked_claim_ids empty and
    the figure_missing_linked_claim_ids warning stays (validation runs after
    the cross-link pass, so the warning reflects the post-crosslink state)."""
    structure = _structure_with([
        _figure_caption_block("blk_fig", 1, "Figure 5.2: comparison plot."),
        _body_block("blk_body", 2, "This paragraph never references the figure."),
    ])
    agent = FigureTableSemanticsAgent()
    result = agent.run(structure, claim_link_index={"blk_body": ["claim_a"]})
    assert result.figures[0].linked_claim_ids == []
    rules = {i.rule_id for i in result.validation_issues}
    assert "figure_missing_linked_claim_ids" in rules


def test_mention_removes_warning_when_linked():
    """Cross-link pass runs before validation: a mentioned + linked figure must
    NOT carry the figure_missing_linked_claim_ids warning."""
    structure = _structure_with([
        _figure_caption_block("blk_fig", 1, "Figure 5.2: comparison plot."),
        _body_block("blk_body", 2, "Fig. 5.2 shows the setup."),
    ])
    agent = FigureTableSemanticsAgent()
    result = agent.run(structure, claim_link_index={"blk_body": ["claim_a"]})
    assert result.figures[0].linked_claim_ids == ["claim_a"]
    rules = {i.rule_id for i in result.validation_issues}
    assert "figure_missing_linked_claim_ids" not in rules


def test_table_mention_crosslink():
    """(f) Table cross-linking via "Table N" mentions."""
    structure = _structure_with([
        _table_caption_block("blk_tbl", 1, "Table 2: systematic uncertainty budget."),
        _body_block("blk_body", 2, "Table 2 summarizes the budget breakdown."),
    ])
    agent = FigureTableSemanticsAgent()
    result = agent.run(structure, claim_link_index={"blk_body": ["claim_t"]})
    assert result.tables[0].table_id == "table_2"
    assert result.tables[0].linked_claim_ids == ["claim_t"]


def test_table_mention_does_not_false_match_longer_number():
    """(b) analogue for tables: "Table 21" must not cross-link to table 2."""
    structure = _structure_with([
        _table_caption_block("blk_tbl", 1, "Table 2: systematic uncertainty budget."),
        _body_block("blk_body", 2, "Table 21 is unrelated to this budget."),
    ])
    agent = FigureTableSemanticsAgent()
    result = agent.run(structure, claim_link_index={"blk_body": ["claim_t"]})
    assert result.tables[0].linked_claim_ids == []


def test_caption_block_itself_not_treated_as_mention_source():
    """Defensive: even though the caption text itself contains "Figure 5.2",
    the caption block is not a body_paragraph and must not be used as a
    mention source (claim spans never originate from caption blocks)."""
    structure = _structure_with([
        _figure_caption_block("blk_fig", 1, "Figure 5.2: comparison plot."),
    ])
    agent = FigureTableSemanticsAgent()
    result = agent.run(structure, claim_link_index={"blk_fig": ["claim_caption_only"]})
    # The pre-existing caption block_id lookup in _build_figure_record still
    # applies (unchanged, unrelated to the mention pass).
    assert result.figures[0].linked_claim_ids == ["claim_caption_only"]
