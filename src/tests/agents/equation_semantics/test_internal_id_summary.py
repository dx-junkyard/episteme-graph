"""IK-0819: 生成要約に内部のブロック ID（``(tex_b80)``）を残さない。"""
from __future__ import annotations

from unittest.mock import patch

from episteme_graph.agents.equation_semantics.agent import EquationSemanticsAgent
from episteme_graph.agents.equation_semantics.prompt import EquationSemanticsPromptFactory
from episteme_graph.agents.equation_semantics.schema import REVIEW_FLAGS
from episteme_graph.agents.internal_id_text import (
    contains_internal_id,
    find_internal_ids,
    strip_internal_ids,
)

from .test_agent import _response
from .test_neighbor_equations import _blocks, _llm_inputs, _structure


def test_strip_internal_ids_removes_block_ids_and_keeps_paper_equation_numbers():
    assert strip_internal_ids("Defines the scale factor (tex_b80).") == "Defines the scale factor."
    assert strip_internal_ids("Relates eq_tex_b78 to eq_12.") == "Relates to eq_12."
    assert find_internal_ids("see [tex_b80, tex_b81]") == ["tex_b80", "tex_b81"]
    assert not contains_internal_id("eq_2_7 and (3.14) and span_id")
    assert strip_internal_ids("No ids here.") == "No ids here."


def test_prompt_forbids_internal_ids_in_the_summary():
    inputs = _llm_inputs(_structure(_blocks()))
    system = EquationSemanticsPromptFactory().build_messages(inputs[0], None)[0]["content"]
    assert "never put" in system and "tex_b80" in system


def test_summary_with_block_id_is_scrubbed_and_flagged():
    structure = _structure(_blocks())
    agent = EquationSemanticsAgent()
    responses = [
        _response("eq_2_1", "e1", "2.1", "M_1", "definition", "First moment (tex_b80)."),
        _response("eq_2_2", "e2", "2.2", "M_2", "definition", "Second moment."),
        _response("eq_2_3", "e3", "2.3", "s", "transformation", "Variance."),
        _response("eq_5_1", "e9", "5.1", "Z", "relation", "Normalization."),
    ]
    with patch.object(agent._llm_client, "generate", side_effect=responses):
        result = agent.run(structure)
    record = next(r for r in result.equations if r.equation_id == "eq_2_1")
    assert record.semantics.summary == "First moment."
    assert "internal_id_in_summary" in record.semantics.review_flags
    assert "internal_id_in_summary" in REVIEW_FLAGS
    issues = [i for i in result.validation_issues if i.rule_id == "internal_id_in_summary"]
    assert issues and issues[0].severity == "warning" and "tex_b80" in issues[0].message
    assert not [i for i in result.validation_issues if i.rule_id == "invalid_review_flag"]
    clean = next(r for r in result.equations if r.equation_id == "eq_2_2")
    assert "internal_id_in_summary" not in clean.semantics.review_flags
