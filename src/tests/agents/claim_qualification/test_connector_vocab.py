"""Connector vocabulary: single source shared by validator / prompt / repair message.

Regression for the radar loop (seq 2757/2760/2763): an atomic claim starting with
"Because" failed validation three times because the prompt listed only a subset
of the forbidden connectors (no because / since).
"""
from types import SimpleNamespace

from episteme_graph.agents.claim_qualification import prompt as prompt_mod
from episteme_graph.agents.claim_qualification.schema import (
    ATOMIC_CLAIM_LEADING_CONNECTOR_WORDS,
    ATOMIC_CLAIM_LEADING_CONNECTORS,
    CAUSAL_REWRITE_GUIDANCE,
)
from episteme_graph.agents.claim_qualification.validator import ClaimQualificationValidator

from .test_validator import _atomic, _record, _result


def test_set_is_derived_from_ordered_words():
    assert ATOMIC_CLAIM_LEADING_CONNECTORS == frozenset(ATOMIC_CLAIM_LEADING_CONNECTOR_WORDS)
    assert len(set(ATOMIC_CLAIM_LEADING_CONNECTOR_WORDS)) == len(ATOMIC_CLAIM_LEADING_CONNECTOR_WORDS)
    assert {"because", "since"} <= ATOMIC_CLAIM_LEADING_CONNECTORS


def test_system_prompt_enumerates_every_connector():
    system = prompt_mod._SYSTEM_CONTENT
    for word in ATOMIC_CLAIM_LEADING_CONNECTOR_WORDS:
        assert f" {word} " in system or f"({word} " in system or f" {word})" in system, word
    assert CAUSAL_REWRITE_GUIDANCE in system
    assert "{connectors}" not in system and "{causal_rewrite}" not in system


def test_prompt_module_has_no_hand_written_connector_subset():
    src = open(prompt_mod.__file__, encoding="utf-8").read()
    assert "and / but / therefore" not in src


def test_because_leading_atomic_claim_is_error_with_full_list():
    r = _record(atomic_claims=[_atomic(text="Because the coupling is small, the series converges.")])
    issues = ClaimQualificationValidator().validate(_result(r))
    hits = [i for i in issues if i.rule_id == "atomic_claim_starts_with_connector"]
    assert hits and hits[0].severity == "error"
    for word in ATOMIC_CLAIM_LEADING_CONNECTOR_WORDS:
        assert word in hits[0].message
    assert "qualification_reason" in hits[0].message  # causal-rewrite guidance included


def test_subject_first_causal_restatement_passes():
    r = _record(atomic_claims=[_atomic(text="The series converges because the coupling is small.")])
    issues = ClaimQualificationValidator().validate(_result(r))
    assert not any(i.rule_id == "atomic_claim_starts_with_connector" for i in issues)


def test_repair_message_carries_full_connector_list():
    r = _record(atomic_claims=[_atomic(text="Since X holds, Y follows from it.")])
    issues = ClaimQualificationValidator().validate(_result(r))
    llm_input = SimpleNamespace()
    builder = prompt_mod.ClaimQualificationPromptFactory()
    builder._build_user_content = lambda *_a, **_k: "SPAN"
    messages = builder.build_repair_messages(llm_input, {}, issues)
    user = messages[-1]["content"]
    for word in ATOMIC_CLAIM_LEADING_CONNECTOR_WORDS:
        assert word in user
