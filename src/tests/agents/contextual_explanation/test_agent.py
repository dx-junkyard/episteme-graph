"""Tests for ContextualExplanationAgent (design §5.1)."""
from __future__ import annotations

from unittest.mock import patch

from episteme_graph.agents.contextual_explanation.agent import ContextualExplanationAgent
from episteme_graph.agents.contextual_explanation.schema import (
    SKIPPED_REASON_LLM_CALL_FAILED,
    SKIPPED_REASON_REPAIR_FAILED,
    ElementExplanationInput,
)


def _element(element_id: str, **kwargs) -> ElementExplanationInput:
    defaults = dict(
        element_type="theory_claim",
        element_id=element_id,
        local_text=f"Claim text for {element_id}.",
        upper_context=[{"relation": "supports", "kind": "thesis", "text": "Central thesis."}],
        lower_context=[],
        library_excerpt=None,
    )
    defaults.update(kwargs)
    return ElementExplanationInput(**defaults)


def _entry(element_id: str, **kwargs) -> dict:
    defaults = dict(
        element_id=element_id,
        element_type="theory_claim",
        contextual_explanation="This claim supports the central thesis.",
        generic_explanation=None,
        evidence_quote="Central thesis.",
        reason="grounded in upper_context",
        confidence=0.8,
        skipped_reason=None,
    )
    defaults.update(kwargs)
    return defaults


def test_run_with_no_elements_returns_empty_result_without_llm_call():
    agent = ContextualExplanationAgent()
    with patch.object(agent._llm_client, "generate") as mock_generate:
        result = agent.run([])
    mock_generate.assert_not_called()
    assert result.elements == []
    assert result.llm_call_count == 0


def test_run_single_batch_success():
    elements = [_element("e1"), _element("e2")]
    agent = ContextualExplanationAgent()
    raw = {"elements": [_entry("e1"), _entry("e2")]}
    with patch.object(agent._llm_client, "generate", return_value=raw):
        result = agent.run(elements)
    assert result.llm_call_count == 1
    assert {e.element_id for e in result.elements} == {"e1", "e2"}
    assert not [i for i in result.validation_issues if i.severity == "error"]
    for e in result.elements:
        assert e.contextual_explanation
        assert e.skipped_reason is None


def test_run_batches_across_max_elements_per_call():
    elements = [_element(f"e{i}") for i in range(10)]
    agent = ContextualExplanationAgent(max_elements_per_call=4)

    # Batching is deterministic and order-preserving (input_builder.build_batches):
    # chunks of size 4 -> [e0..e3], [e4..e7], [e8,e9]. Answer each call with
    # exactly the ids that batch asked about, so no repair is triggered and the
    # call count reflects one call per batch.
    expected_batches = [["e0", "e1", "e2", "e3"], ["e4", "e5", "e6", "e7"], ["e8", "e9"]]
    responses = [{"elements": [_entry(eid) for eid in ids]} for ids in expected_batches]

    with patch.object(agent._llm_client, "generate", side_effect=responses):
        result = agent.run(elements)

    assert result.llm_call_count == 3  # ceil(10/4) batches -> 3 calls, no repairs needed
    assert {e.element_id for e in result.elements} == {f"e{i}" for i in range(10)}
    assert not [i for i in result.validation_issues if i.severity == "error"]


def test_run_config_overrides_max_elements_per_call():
    elements = [_element(f"e{i}") for i in range(5)]
    agent = ContextualExplanationAgent(max_elements_per_call=8)
    calls = []

    def _fake_generate(messages):
        calls.append(messages)
        return {"elements": [_entry(f"e{i}") for i in range(5)]}

    with patch.object(agent._llm_client, "generate", side_effect=_fake_generate):
        result = agent.run(elements, config={"max_elements_per_call": 2})

    assert result.llm_call_count == 3  # ceil(5/2)


def test_repair_preserves_valid_sibling_and_skips_stubborn_element():
    """The core batching contract: a single bad element in a batch must not
    discard its already-correct sibling's explanation."""
    elements = [_element("good"), _element("bad")]
    agent = ContextualExplanationAgent()

    initial_raw = {
        "elements": [
            _entry("good"),
            _entry("bad", evidence_quote="", contextual_explanation="unsupported claim"),
        ]
    }
    repair_raw = {
        "elements": [
            _entry("bad", evidence_quote="", contextual_explanation="still unsupported"),
        ]
    }

    with patch.object(
        agent._llm_client, "generate", side_effect=[initial_raw, repair_raw, repair_raw]
    ):
        result = agent.run(elements)

    by_id = {e.element_id: e for e in result.elements}
    assert by_id["good"].contextual_explanation == "This claim supports the central thesis."
    assert by_id["good"].skipped_reason is None
    assert by_id["bad"].skipped_reason == SKIPPED_REASON_REPAIR_FAILED
    assert by_id["bad"].contextual_explanation is None
    assert result.llm_call_count == 3  # 1 initial + 2 repair attempts


def test_run_llm_exception_marks_whole_batch_skipped():
    elements = [_element("e1"), _element("e2")]
    agent = ContextualExplanationAgent()
    with patch.object(agent._llm_client, "generate", side_effect=RuntimeError("down")):
        result = agent.run(elements)
    assert {e.element_id for e in result.elements} == {"e1", "e2"}
    for e in result.elements:
        assert e.skipped_reason == SKIPPED_REASON_LLM_CALL_FAILED
        assert e.contextual_explanation is None
    assert result.llm_call_count == 1


def test_run_hallucination_guard_forces_repair_and_drops_generic_explanation():
    """E3: generic_explanation without a library_excerpt must never survive."""
    elements = [_element("e1", library_excerpt=None)]
    agent = ContextualExplanationAgent()
    bad_raw = {
        "elements": [_entry(
            "e1", generic_explanation="A generic explanation invented from model knowledge."
        )]
    }
    good_raw = {"elements": [_entry("e1", generic_explanation=None)]}
    with patch.object(agent._llm_client, "generate", side_effect=[bad_raw, good_raw]):
        result = agent.run(elements)
    assert result.llm_call_count == 2
    assert result.elements[0].generic_explanation is None
    assert result.elements[0].contextual_explanation


def test_run_generic_explanation_allowed_with_library_excerpt():
    excerpt = {
        "entry_id": "lib_1", "version_no": 1, "name": "Effective Hamiltonian",
        "summary": "summary", "body_excerpt": "body",
    }
    elements = [_element("e1", library_excerpt=excerpt)]
    agent = ContextualExplanationAgent()
    raw = {"elements": [_entry("e1", generic_explanation="Rephrased library summary.")]}
    with patch.object(agent._llm_client, "generate", return_value=raw):
        result = agent.run(elements)
    assert result.llm_call_count == 1
    assert result.elements[0].generic_explanation == "Rephrased library summary."


def test_run_works_without_cartridge():
    elements = [_element("e1")]
    agent = ContextualExplanationAgent()
    raw = {"elements": [_entry("e1")]}
    with patch.object(agent._llm_client, "generate", return_value=raw):
        result = agent.run(elements, cartridge_id=None)
    assert result.cartridge_id is None
    assert result.elements[0].contextual_explanation


def test_run_unknown_cartridge_id_degrades_gracefully():
    elements = [_element("e1")]
    agent = ContextualExplanationAgent(cartridge_base_dir="/nonexistent/cartridge/dir")
    raw = {"elements": [_entry("e1")]}
    with patch.object(agent._llm_client, "generate", return_value=raw):
        result = agent.run(elements, cartridge_id="does_not_exist")
    assert result.elements[0].contextual_explanation
    assert not [i for i in result.validation_issues if i.severity == "error"]
