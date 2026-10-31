"""IK-0809 / IK-0808: display 式にも役割付き span の候補を渡し、候補外の linked_text_spans は落とす。

文脈ブロックの切り詰めは coverage ``details.truncated_blocks`` に理由付きで残す。
"""
from __future__ import annotations

from episteme_graph.agents.document_structure.schema import (
    DocumentMetadata,
    DocumentStructureResult,
    Section,
    TypedBlock,
)
from episteme_graph.agents.equation_semantics.acceptance_gate import EquationAcceptanceGate
from episteme_graph.agents.equation_semantics.input_builder import EquationSemanticsInputBuilder
from episteme_graph.agents.equation_semantics.prompt import EquationSemanticsPromptFactory
from episteme_graph.agents.equation_semantics.validator import (
    restrict_linked_spans_to_candidates,
)
from episteme_graph.agents.rhetorical_role.schema import (
    BlockRoleAnnotation,
    RhetoricalRoleResult,
    SpanAnnotation,
)

from .test_validator import _record


def _typed(block_id, text, block_type, order, section_id="sec_1"):
    b = TypedBlock(block_id, 1, order, text, block_type, raw={"parser_source": "tex_archive"})
    b.section_id = section_id
    return b


def _span(span_id, text, roles):
    return SpanAnnotation(span_id, text, 0, len(text), roles, False, False, 0.9, "r")


def _structure(blocks):
    return DocumentStructureResult(
        document_id="doc", source_file="paper.tar.gz", cartridge_id=None,
        metadata=DocumentMetadata(title="T", pages=1),
        sections=[Section("sec_1", "A", 1, 1, 1)],
        blocks=blocks,
    )


def _roles():
    return RhetoricalRoleResult(
        document_id="doc", cartridge_id=None,
        role_annotations=[
            BlockRoleAnnotation("p0", "sec_1", None, [_span("span_001", "The posterior is written as", ["equation_definition"])]),
            BlockRoleAnnotation("p1", "sec_1", None, [_span("span_001", "where n are nuisance parameters", ["definition"])]),
            BlockRoleAnnotation("p9", "sec_1", None, [_span("span_001", "far away", ["result"])]),
        ],
        summary_stats={},
    )


def _inputs(blocks, roles=None):
    structure = _structure(blocks)
    builder = EquationSemanticsInputBuilder()
    candidates = EquationAcceptanceGate().process(builder.build_candidates(structure))
    return builder.build_llm_inputs(
        structure, candidates, roles=roles, accepted_statuses={"accepted", "provisional"}
    )


def _blocks():
    return [
        _typed("p9", "far away", "body_paragraph", 0),
        _typed("p0", "The posterior is written as", "body_paragraph", 1),
        _typed("e1", r"P \propto L \Pi (2.1)", "equation_block", 2),
        _typed("p1", "where n are nuisance parameters", "body_paragraph", 3),
    ]


def test_display_equation_gets_spans_from_previous_and_next_prose_blocks():
    inputs = _inputs(_blocks(), roles=_roles())
    display = next(i for i in inputs if i.block_id == "e1")
    pairs = [(c["block_id"], c["span_id"]) for c in display.candidate_text_spans]
    assert pairs == [("p0", "span_001"), ("p1", "span_001")]
    assert display.candidate_text_spans[0]["role_labels"] == ["equation_definition"]
    content = EquationSemanticsPromptFactory().build_messages(display, None)[-1]["content"]
    assert "## Candidate Text Spans" in content
    assert "linked_text_spans may only contain span_id (with its block_id) listed in candidate_text_spans" in content
    assert "exactly as the equation text" not in content


def test_without_roles_the_candidates_are_not_restricted():
    display = next(i for i in _inputs(_blocks()) if i.block_id == "e1")
    assert display.candidate_text_spans is None


def test_links_outside_the_candidates_are_dropped_with_a_warning():
    record = _record()
    record.semantics.linked_text_spans = [
        {"span_id": "span_001", "block_id": "p0", "relation": "introduced_by"},
        {"span_id": "span_001", "block_id": "p9", "relation": "explained_by"},
        {"span_id": "span_007", "relation": "explained_by"},
    ]
    candidates = [
        {"span_id": "span_001", "block_id": "p0"},
        {"span_id": "span_002", "block_id": "p1"},
    ]
    issues = restrict_linked_spans_to_candidates(record, candidates)
    assert record.semantics.linked_text_spans == [
        {"span_id": "span_001", "block_id": "p0", "relation": "introduced_by"}
    ]
    assert issues and issues[0].severity == "warning"
    assert "linked_span_not_in_candidates" in record.semantics.review_flags


def test_link_without_block_id_is_completed_when_the_span_id_is_unique():
    record = _record()
    record.semantics.linked_text_spans = [{"span_id": "span_002", "relation": "explained_by"}]
    issues = restrict_linked_spans_to_candidates(record, [{"span_id": "span_002", "block_id": "p1"}])
    assert issues == []
    assert record.semantics.linked_text_spans[0]["block_id"] == "p1"


def test_long_context_blocks_are_recorded_as_truncated():
    long_text = "The nuisance parameters are marginalised analytically here. " * 40
    blocks = [
        _typed("p0", long_text, "body_paragraph", 0),
        _typed("e1", r"P \propto L \Pi (2.1)", "equation_block", 1),
    ]
    facts = EquationSemanticsInputBuilder().compute_candidate_coverage(_structure(blocks))
    assert {"block_id": "p0", "reason": "context_block_over_char_limit"} in facts["details"]["truncated_blocks"]
    display = next(i for i in _inputs(blocks) if i.block_id == "e1")
    assert display.prev_texts[0].startswith("[p0] …")
