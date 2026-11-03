"""IK-0729 / IK-0763: the edge repair loop only sees edge-fixable issues and a
structurally valid excerpt of the previous output."""

from __future__ import annotations

import json
from unittest.mock import patch

from episteme_graph.agents.component_graph.prompt import (
    ComponentGraphPromptFactory,
    previous_output_excerpt,
)
from episteme_graph.agents.component_graph.repair import (
    EDGE_REPAIRABLE_RULE_IDS,
    edge_repairable_issues,
    needs_edge_repair,
)
from episteme_graph.agents.component_graph.schema import ValidationIssue


def _issue(rule, sev="error"):
    return ValidationIssue(rule, sev, f"{rule} message", "x")


NODE_SIDE = [
    "main_graph_generic_operation",
    "output_node_missing_linked_equations",
    "derive_node_missing_derivation_link",
    "theory_node_missing_claim_link",
    "component_graph_node_missing_label",
]


def test_node_side_errors_never_trigger_repair():
    issues = [_issue(r) for r in NODE_SIDE]
    assert not needs_edge_repair(issues)
    assert edge_repairable_issues(issues) == []
    assert not (set(NODE_SIDE) & EDGE_REPAIRABLE_RULE_IDS)


def test_edge_errors_trigger_repair_and_are_the_only_ones_passed():
    issues = [_issue("main_graph_generic_operation"), _issue("unknown_target_component")]
    assert needs_edge_repair(issues)
    assert [i.rule_id for i in edge_repairable_issues(issues)] == ["unknown_target_component"]


def test_edge_warning_alone_does_not_trigger_repair():
    assert not needs_edge_repair([_issue("duplicate_edge", "warning")])


def _edges(n):
    return {
        "edges": [
            {
                "edge_id": f"component_edge_{i:04d}",
                "source": "comp_001",
                "target": "comp_002",
                "edge_type": "REQUIRES",
                "reasoning": "x" * 120,
            }
            for i in range(1, n + 1)
        ]
    }


def test_short_previous_output_is_quoted_whole():
    text, note = previous_output_excerpt(_edges(2))
    assert json.loads(text) == _edges(2)
    assert note == ""


def test_long_previous_output_stays_valid_json_and_names_the_range():
    text, note = previous_output_excerpt(_edges(60), max_chars=3000)
    assert len(text) <= 3000
    parsed = json.loads(text)  # never cut mid-string
    kept = parsed["edges"]
    assert 0 < len(kept) < 60
    assert kept[0]["edge_id"] == "component_edge_0001"
    assert kept[-1]["edge_id"] in note
    assert "complete" in note


def test_repair_prompt_embeds_parseable_previous_output():
    from episteme_graph.agents.component_graph.schema import ComponentGraphLLMInput

    factory = ComponentGraphPromptFactory()
    llm_input = ComponentGraphLLMInput.__new__(ComponentGraphLLMInput)
    with patch.object(ComponentGraphPromptFactory, "_build_user_content", return_value="MATERIALS"):
        msgs = factory.build_repair_messages(llm_input, _edges(60), [_issue("self_loop")])
    content = msgs[0]["content"]
    block = content.split("Previous output:\n", 1)[1].split("\n", 1)[0]
    json.loads(block)


def test_agent_makes_no_repair_call_when_only_node_errors():
    from episteme_graph.agents.component_graph.agent import ComponentGraphAgent
    from tests.agents.component_graph.test_agent import (
        _VALID_LLM_RESPONSE,
        _make_assembly,
        _make_component,
    )

    agent = ComponentGraphAgent()
    comps = _make_assembly([_make_component("comp_001", "A"), _make_component("comp_002", "B")])
    real_validate = agent._validator.validate

    def validate(result, cartridge=None, llm_input=None):
        return real_validate(result, cartridge, llm_input=llm_input) + [
            _issue("main_graph_generic_operation")
        ]

    with patch.object(agent._validator, "validate", side_effect=validate), patch.object(
        agent._llm_client, "generate", return_value=_VALID_LLM_RESPONSE
    ) as gen:
        result = agent.run(comps)
    assert gen.call_count == 1  # no repair call
    assert len(result.edges) == 1  # LLM edges kept, not replaced by the fallback
