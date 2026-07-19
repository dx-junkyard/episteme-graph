"""Tests for ContextualExplanationAgent repair helpers.

Covers the core "targeted per-element repair" behavior: when a batch has
several elements and only some fail validation, repair must retry ONLY the
failing ones and must not discard the already-correct siblings. Elements
that are still invalid after the repair ceiling are kept with
skipped_reason="repair_failed" -- never dropped (P4).
"""
from __future__ import annotations

from unittest.mock import MagicMock

from episteme_graph.agents.contextual_explanation.repair import (
    ContextualExplanationRepairer,
    error_element_ids,
    parse_batch_output,
)
from episteme_graph.agents.contextual_explanation.schema import (
    ElementExplanationInput,
    SKIPPED_REASON_REPAIR_FAILED,
    ValidationIssue,
)
from episteme_graph.agents.contextual_explanation.validator import (
    ContextualExplanationValidator,
)


def _input(element_id: str, **kwargs) -> ElementExplanationInput:
    defaults = dict(
        element_type="theory_claim",
        element_id=element_id,
        local_text="local text",
        upper_context=[],
        lower_context=[],
        library_excerpt=None,
    )
    defaults.update(kwargs)
    return ElementExplanationInput(**defaults)


def _entry(element_id: str, **kwargs) -> dict:
    defaults = dict(
        element_id=element_id,
        element_type="theory_claim",
        contextual_explanation="An explanation.",
        generic_explanation=None,
        evidence_quote="quote",
        reason="reason",
        confidence=0.7,
        skipped_reason=None,
    )
    defaults.update(kwargs)
    return defaults


# ---------------------------------------------------------------------------
# parse_batch_output / error_element_ids
# ---------------------------------------------------------------------------

def test_parse_batch_output_builds_results_and_clamps_confidence():
    batch = [_input("e1")]
    raw = {"elements": [_entry("e1", confidence=5.0)]}
    results = parse_batch_output(raw, batch)
    assert len(results) == 1
    assert results[0].confidence == 1.0
    assert results[0].contextual_explanation == "An explanation."


def test_parse_batch_output_keeps_unknown_and_duplicate_entries_for_validator():
    batch = [_input("e1")]
    raw = {"elements": [_entry("e1"), _entry("e1"), _entry("ghost")]}
    results = parse_batch_output(raw, batch)
    assert len(results) == 3


def test_error_element_ids_extracts_only_error_severity():
    issues = [
        ValidationIssue("r1", "error", "msg", "elements[e1]"),
        ValidationIssue("r2", "warning", "msg", "elements[e2]"),
        ValidationIssue("r3", "error", "msg", None),
    ]
    assert error_element_ids(issues) == {"e1"}


# ---------------------------------------------------------------------------
# ContextualExplanationRepairer.repair
# ---------------------------------------------------------------------------

def _validator_stub():
    return ContextualExplanationValidator()


def _prompt_factory_stub():
    factory = MagicMock()
    factory.build_repair_messages.return_value = [{"role": "user", "content": "repair"}]
    return factory


def test_repair_fixes_the_pending_element_and_reports_one_call():
    batch = [_input("e1"), _input("e2")]
    llm_client = MagicMock()
    llm_client.generate.return_value = {"elements": [_entry("e2")]}

    repairer = ContextualExplanationRepairer()
    results, calls = repairer.repair(
        batch=batch,
        pending_ids={"e2"},
        raw_output={"elements": [_entry("e1")]},
        validation_issues=[ValidationIssue("missing_element_output", "error", "m", "elements[e2]")],
        cartridge=None,
        llm_client=llm_client,
        prompt_factory=_prompt_factory_stub(),
        validator=_validator_stub(),
    )
    assert len(results) == 1
    assert results[0].element_id == "e2"
    assert results[0].skipped_reason is None
    assert calls == 1


def test_repair_keeps_stubborn_element_as_skipped_after_ceiling():
    batch = [_input("e1"), _input("e2")]
    llm_client = MagicMock()
    # Every repair attempt returns an unresolvable element (missing evidence_quote).
    llm_client.generate.return_value = {
        "elements": [_entry("e2", evidence_quote="", contextual_explanation="still bad")]
    }

    repairer = ContextualExplanationRepairer()
    results, calls = repairer.repair(
        batch=batch,
        pending_ids={"e2"},
        raw_output={"elements": [_entry("e1")]},
        validation_issues=[ValidationIssue(
            "explanation_missing_evidence_quote", "error", "m", "elements[e2]"
        )],
        cartridge=None,
        llm_client=llm_client,
        prompt_factory=_prompt_factory_stub(),
        validator=_validator_stub(),
    )
    assert len(results) == 1
    assert results[0].element_id == "e2"
    assert results[0].skipped_reason == SKIPPED_REASON_REPAIR_FAILED
    assert calls == 2  # exhausted both repair attempts


def test_repair_does_not_touch_ids_outside_pending_set():
    batch = [_input("e1"), _input("e2")]
    llm_client = MagicMock()
    llm_client.generate.return_value = {"elements": [_entry("e2")]}

    repairer = ContextualExplanationRepairer()
    results, _calls = repairer.repair(
        batch=batch,
        pending_ids={"e2"},
        raw_output={},
        validation_issues=[],
        cartridge=None,
        llm_client=llm_client,
        prompt_factory=_prompt_factory_stub(),
        validator=_validator_stub(),
    )
    assert {r.element_id for r in results} == {"e2"}


def test_repair_stops_early_when_llm_call_raises():
    batch = [_input("e1")]
    llm_client = MagicMock()
    llm_client.generate.side_effect = RuntimeError("network down")

    repairer = ContextualExplanationRepairer()
    results, calls = repairer.repair(
        batch=batch,
        pending_ids={"e1"},
        raw_output={},
        validation_issues=[ValidationIssue("missing_element_output", "error", "m", "elements[e1]")],
        cartridge=None,
        llm_client=llm_client,
        prompt_factory=_prompt_factory_stub(),
        validator=_validator_stub(),
    )
    assert len(results) == 1
    assert results[0].skipped_reason == SKIPPED_REASON_REPAIR_FAILED
    assert calls == 0
