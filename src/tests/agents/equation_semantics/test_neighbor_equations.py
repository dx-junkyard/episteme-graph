"""2026-10-16 radar_loop S1（seq 2839 / 2845 / 2848・題材 2609.35757）の回帰。

equation_semantics の LLM 入力に近傍の式の ``equation_id`` 一覧が無く、
``input_equation_ids`` / ``output_equation_ids`` を材料から埋められなかった（別 req の
ID を持ち込む誘因）。``neighbor_equations`` を渡し、一覧に無い ID は warning で外す。
"""
from __future__ import annotations

from unittest.mock import patch

from episteme_graph.agents.document_structure.schema import (
    DocumentMetadata,
    DocumentStructureResult,
    Section,
    TypedBlock,
)
from episteme_graph.agents.equation_semantics.acceptance_gate import EquationAcceptanceGate
from episteme_graph.agents.equation_semantics.agent import EquationSemanticsAgent
from episteme_graph.agents.equation_semantics.input_builder import EquationSemanticsInputBuilder
from episteme_graph.agents.equation_semantics.prompt import EquationSemanticsPromptFactory

from .test_agent import _response


def _typed(block_id, text, block_type, order, section_id="sec_1", tex=True):
    b = TypedBlock(block_id, 1, order, text, block_type,
                   raw={"parser_source": "tex_archive"} if tex else {})
    b.section_id = section_id
    return b


def _structure(blocks):
    return DocumentStructureResult(
        document_id="doc", source_file="paper.tar.gz", cartridge_id=None,
        metadata=DocumentMetadata(title="T", pages=1),
        sections=[Section("sec_1", "A", 1, 1, 1), Section("sec_2", "B", 1, 1, 1)],
        blocks=blocks,
    )


def _llm_inputs(structure):
    builder = EquationSemanticsInputBuilder()
    candidates = EquationAcceptanceGate().process(builder.build_candidates(structure))
    return builder.build_llm_inputs(
        structure, candidates, accepted_statuses={"accepted", "provisional"}
    )


def _blocks():
    return [
        _typed("p0", "We define the moments.", "body_paragraph", 0),
        _typed("e1", r"M_1 = \int u f \, du (2.1)", "equation_block", 1),
        _typed("p1", "and the second moment", "body_paragraph", 2),
        _typed("e2", r"M_2 = \int u^2 f \, du (2.2)", "equation_block", 3),
        _typed("p2", "Combining them,", "body_paragraph", 4),
        _typed("e3", r"\sigma^2 = M_2 - M_1^2 (2.3)", "equation_block", 5),
        _typed("e9", r"Z = 1 (5.1)", "equation_block", 6, section_id="sec_2"),
    ]


def test_neighbor_equations_are_same_section_first_and_in_document_order():
    structure = _structure(_blocks())
    inputs = _llm_inputs(structure)
    by_id = {i.equation_id: i for i in inputs}
    neighbors = by_id["eq_2_3"].neighbor_equations
    assert [n["equation_id"] for n in neighbors] == ["eq_2_1", "eq_2_2", "eq_5_1"]
    assert neighbors[0]["label"] == "2.1"
    assert neighbors[0]["latex_head"].startswith(r"M_1 = \int u f")
    assert all(n["equation_id"] != "eq_2_3" for n in neighbors)
    content = EquationSemanticsPromptFactory().build_messages(by_id["eq_2_3"], None)[-1]["content"]
    assert "## Neighbor Equations" in content and "eq_2_2" in content
    assert "may only contain equation_id values listed in neighbor_equations" in content


def test_neighbor_equations_are_capped_and_pdf_math_is_not_shown():
    blocks = [
        _typed(f"e{i}", f"x_{i} = {i} ({i}.0)", "equation_block", i, tex=False)
        for i in range(1, 20)
    ]
    inputs = _llm_inputs(_structure(blocks))
    neighbors = inputs[9].neighbor_equations
    assert len(neighbors) == 12
    assert all(n["latex_head"] is None for n in neighbors)  # PDF 由来の式の文字列は渡さない


def test_io_ids_outside_neighbor_equations_are_dropped_with_a_warning():
    structure = _structure(_blocks())
    agent = EquationSemanticsAgent()
    responses = [
        _response("eq_2_1", "e1", "2.1", "M_1", "definition", "First moment."),
        _response("eq_2_2", "e2", "2.2", "M_2", "definition", "Second moment."),
        _response("eq_2_3", "e3", "2.3", "s", "transformation", "Variance.",
                  from_equations=["eq_2_1", "eq_2_2", "eq_9_9"]),
        _response("eq_5_1", "e9", "5.1", "Z", "relation", "Normalization."),
    ]
    with patch.object(agent._llm_client, "generate", side_effect=responses):
        result = agent.run(structure)
    record = next(r for r in result.equations if r.equation_id == "eq_2_3")
    assert record.semantics.input_equation_ids == ["eq_2_1", "eq_2_2"]
    assert "io_id_not_in_neighbor_equations" in record.semantics.review_flags
    issues = [i for i in result.validation_issues if i.rule_id == "io_id_not_in_neighbor_equations"]
    assert issues and issues[0].severity == "warning" and "input:eq_9_9" in issues[0].message
    assert not [i for i in result.validation_issues if i.rule_id == "invalid_review_flag"]


def test_unlabeled_equation_label_is_not_rendered_as_none():
    structure = _structure([_typed("e1", r"x = y", "equation_block", 0)])
    llm_input = _llm_inputs(structure)[0]
    assert llm_input.label is None
    content = EquationSemanticsPromptFactory().build_messages(llm_input, None)[-1]["content"]
    assert "label: None" not in content
    assert "\nlabel: \n" in content
