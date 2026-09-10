"""Snapshot of the per-attempt repair diagnostics keys.

``ComponentAssemblyRepairer`` is the only repairer that writes per-attempt
diagnostics; the shared runner (``agents/llm_step.run_repair_loop``) exposes
them through optional hooks. These tests pin the exact key set and shape so a
refactor of the loop cannot silently drop or rename a diagnostics entry.
"""
from __future__ import annotations

from unittest.mock import patch

from episteme_graph.agents.component_assembly.agent import ComponentAssemblyAgent

from .test_agent import _dsl, _empty_response, _equations, _qualified, _thesis


def _run(side_effect):
    agent = ComponentAssemblyAgent()
    with patch.object(agent._llm_client, "generate", side_effect=side_effect) as mocked:
        result = agent.run(_qualified(), equations=_equations(), thesis=_thesis(), dsl=_dsl())
    return result, mocked


def test_exhausted_repair_writes_every_per_attempt_key():
    result, mocked = _run([_empty_response(), _empty_response(), _empty_response()])

    assert mocked.call_count == 3  # 1 initial + 2 repairs
    diag = result.diagnostics

    for attempt in (1, 2):
        output = diag[f"repair_attempt_{attempt}_output"]
        assert set(output) == {"parsed", "component_count", "raw_text", "parse_error"}
        assert output["parsed"] == _empty_response()
        assert output["component_count"] == 0
        assert diag[f"repair_attempt_{attempt}_component_count"] == 0
        assert diag[f"repair_attempt_{attempt}_error_codes"] == ["no_components"]
        issues = diag[f"repair_attempt_{attempt}_issues"]
        assert [i["code"] for i in issues] == ["no_components"]
        assert set(issues[0]) >= {"code", "severity", "message", "field"}
        assert f"repair_attempt_{attempt}_exception" not in diag

    assert "repair_attempt_3_output" not in diag
    assert diag["fallback_reason"] == "LLM component assembly returned no components"
    assert diag["original_failure_codes"] == ["no_components"]


def test_repair_llm_exception_writes_exception_key_and_breaks_loop():
    result, mocked = _run([_empty_response(), RuntimeError("boom")])

    # The exception on the first repair ends the loop — no second repair call.
    assert mocked.call_count == 2
    diag = result.diagnostics

    assert diag["repair_attempt_1_exception"] == "boom"
    assert "repair_attempt_1_output" not in diag
    assert "repair_attempt_1_issues" not in diag
    assert "repair_attempt_2_exception" not in diag
    assert diag["fallback_reason"] == "LLM component assembly returned no components"
    assert diag["original_failure_codes"] == ["no_components"]


# ---------------------------------------------------------------------------
# The repair prompt must see the id-canonicalized raw output, not the model's
# original one: the validator judged the canonicalized ids, so the "fix this"
# prompt has to quote the same ids.
# ---------------------------------------------------------------------------


class _RecordingPromptFactory:
    def __init__(self):
        self.raws: list[dict] = []

    def build_repair_messages(self, _llm_input, raw, _issues):
        self.raws.append(raw)
        return [{"role": "user", "content": "fix it"}]


class _ScriptedClient:
    def __init__(self, outputs):
        self._outputs = list(outputs)
        self.last_raw_text = None
        self.last_parse_error = None

    def generate(self, _messages):
        return self._outputs.pop(0)


class _AlwaysFailingValidator:
    def validate(self, _result, _cartridge, llm_input=None):  # noqa: ARG002
        from episteme_graph.agents.component_assembly.schema import ValidationIssue

        return [ValidationIssue("still_bad", "error", "nope")]


def test_second_repair_prompt_sees_the_canonicalized_raw_output():
    from episteme_graph.agents.component_assembly.repair import ComponentAssemblyRepairer
    from episteme_graph.agents.component_assembly.schema import ComponentAssemblyLLMInput

    llm_input = ComponentAssemblyLLMInput(
        document_id="doc_test",
        cartridge_id=None,
        accepted_claims=[{"claim_id": "claim:b1:s1", "legacy_claim_id": "legacy_1"}],
        equations=[],
        thesis_nodes=[],
        dsl_nodes=[],
        dsl_edges=[],
        allowed_component_types=["ClaimBundleComponent"],
        allowed_dependency_types=["supports"],
    )
    # The model answers with the *legacy* claim id; canonicalization rewrites it.
    llm_answer = {"components": [{"evidence_refs": {"claim_ids": ["legacy_1"]}}]}
    prompt_factory = _RecordingPromptFactory()

    ComponentAssemblyRepairer().repair(
        llm_input,
        {"components": []},
        [],
        None,
        _ScriptedClient([llm_answer, llm_answer]),
        prompt_factory,
        _AlwaysFailingValidator(),
    )

    assert len(prompt_factory.raws) == 2
    # attempt 1 quotes the caller's initial raw output verbatim
    assert prompt_factory.raws[0] == {"components": []}
    # attempt 2 quotes the canonicalized version of attempt 1's answer
    assert prompt_factory.raws[1]["components"][0]["evidence_refs"]["claim_ids"] == [
        "claim:b1:s1"
    ]
