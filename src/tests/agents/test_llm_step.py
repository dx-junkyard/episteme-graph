"""Contract tests for the shared repair loop (``agents/llm_step``).

These pin the semantics every migrated repairer relies on: an LLM exception
*breaks* the loop, warnings are accepted as success, exhaustion hands the last
issues to the caller's fallback, and the attempt ceiling is honoured.
"""
from __future__ import annotations

import pytest

from episteme_graph.agents.llm_step import (
    MAX_REPAIR_ATTEMPTS,
    error_issues,
    has_errors,
    run_repair_loop,
)
from episteme_graph.agents.validation import ValidationIssue


def _error(rule_id="bad"):
    return ValidationIssue(rule_id=rule_id, severity="error", message="boom")


def _warning(rule_id="meh"):
    return ValidationIssue(rule_id=rule_id, severity="warning", message="hmm")


class _Harness:
    """Records every interaction so the tests can assert call counts/order."""

    def __init__(self, *, generate_results, validate_results):
        self._generate_results = list(generate_results)
        self._validate_results = list(validate_results)
        self.messages_built: list[tuple] = []
        self.generate_calls: list[object] = []
        self.parsed: list[dict] = []
        self.success: list[tuple] = []
        self.exhausted_with: list[list] = []

    def build_messages(self, raw, issues):
        self.messages_built.append((raw, list(issues)))
        return {"prompt": len(self.messages_built)}

    def generate(self, messages):
        self.generate_calls.append(messages)
        outcome = self._generate_results.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def parse(self, raw):
        self.parsed.append(raw)
        return {"parsed_from": raw}

    def validate(self, _result):
        return self._validate_results.pop(0)

    def on_success(self, result, remaining):
        self.success.append((result, remaining))
        return ("success", result, remaining)

    def on_exhausted(self, issues):
        self.exhausted_with.append(issues)
        return ("fallback", issues)

    def run(self, **kwargs):
        return run_repair_loop(
            build_messages=self.build_messages,
            generate=self.generate,
            parse=self.parse,
            validate=self.validate,
            on_success=self.on_success,
            on_exhausted=self.on_exhausted,
            raw_output={"initial": True},
            validation_issues=[_error("initial")],
            log_label="Test",
            **kwargs,
        )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def test_error_issues_and_has_errors_ignore_warnings():
    issues = [_warning(), _error("e1"), _warning(), _error("e2")]

    assert [i.rule_id for i in error_issues(issues)] == ["e1", "e2"]
    assert has_errors(issues) is True
    assert has_errors([_warning()]) is False
    assert has_errors([]) is False


def test_max_repair_attempts_is_two():
    """1 initial call (made by the caller) + 2 repairs = 3 LLM calls max."""
    assert MAX_REPAIR_ATTEMPTS == 2


# ---------------------------------------------------------------------------
# loop semantics
# ---------------------------------------------------------------------------


def test_first_clean_attempt_returns_on_success_without_further_calls():
    h = _Harness(generate_results=[{"a": 1}, {"a": 2}], validate_results=[[]])

    outcome = h.run()

    assert outcome == ("success", {"parsed_from": {"a": 1}}, [])
    assert len(h.generate_calls) == 1
    assert h.exhausted_with == []


def test_warnings_only_is_accepted_as_success():
    warnings = [_warning("w1")]
    h = _Harness(generate_results=[{"a": 1}], validate_results=[warnings])

    kind, _result, remaining = h.run()

    assert kind == "success"
    assert remaining == warnings
    assert len(h.generate_calls) == 1


def test_exception_breaks_the_loop_immediately():
    h = _Harness(
        generate_results=[RuntimeError("llm down"), {"a": 2}],
        validate_results=[[]],
    )

    outcome = h.run()

    # No second attempt, no parse, and the caller's fallback is used.
    assert len(h.generate_calls) == 1
    assert h.parsed == []
    assert outcome == ("fallback", [_error("initial")])


def test_exhaustion_passes_the_last_issues_to_on_exhausted():
    last = [_error("third")]
    h = _Harness(
        generate_results=[{"a": 1}, {"a": 2}, {"a": 3}],
        validate_results=[[_error("second")], last],
    )

    outcome = h.run()

    assert len(h.generate_calls) == MAX_REPAIR_ATTEMPTS
    assert outcome == ("fallback", last)
    assert h.exhausted_with == [last]


def test_second_attempt_prompt_sees_previous_raw_and_remaining_issues():
    second_issues = [_error("still")]
    h = _Harness(
        generate_results=[{"a": 1}, {"a": 2}],
        validate_results=[second_issues, []],
    )

    kind, result, _remaining = h.run()

    assert kind == "success"
    assert result == {"parsed_from": {"a": 2}}
    assert h.messages_built == [
        ({"initial": True}, [_error("initial")]),
        ({"a": 1}, second_issues),
    ]


def test_max_attempts_is_overridable():
    h = _Harness(
        generate_results=[{"a": 1}],
        validate_results=[[_error("nope")]],
    )

    outcome = h.run(max_attempts=1)

    assert len(h.generate_calls) == 1
    assert outcome == ("fallback", [_error("nope")])


# ---------------------------------------------------------------------------
# hooks
# ---------------------------------------------------------------------------


def test_hooks_receive_attempt_numbers_and_payloads():
    seen: dict[str, list] = {"parsed": [], "validated": [], "error": []}
    issues_1 = [_error("one")]
    h = _Harness(
        generate_results=[{"a": 1}, {"a": 2}],
        validate_results=[issues_1, []],
    )

    h.run(
        on_attempt_parsed=lambda n, raw: seen["parsed"].append((n, raw)),
        on_attempt_validated=lambda n, issues: seen["validated"].append((n, issues)),
        on_attempt_llm_error=lambda n, exc: seen["error"].append((n, exc)),
    )

    assert seen["parsed"] == [(1, {"a": 1}), (2, {"a": 2})]
    assert seen["validated"] == [(1, issues_1), (2, [])]
    assert seen["error"] == []


def test_llm_error_hook_fires_with_the_attempt_number_and_no_parse_hook():
    seen: dict[str, list] = {"parsed": [], "error": []}
    exc = RuntimeError("down")
    h = _Harness(generate_results=[{"a": 1}, exc], validate_results=[[_error("one")]])

    h.run(
        on_attempt_parsed=lambda n, raw: seen["parsed"].append(n),
        on_attempt_llm_error=lambda n, e: seen["error"].append((n, e)),
    )

    assert seen["parsed"] == [1]
    assert seen["error"] == [(2, exc)]


def test_hooks_are_optional():
    h = _Harness(generate_results=[{"a": 1}], validate_results=[[]])

    kind, _result, _remaining = h.run()

    assert kind == "success"


@pytest.mark.parametrize("failing", ["parse", "validate"])
def test_non_llm_exceptions_are_not_swallowed(failing):
    """Only the LLM call is guarded; a broken parser/validator must surface."""

    def boom(*_args):
        raise ValueError("programmer error")

    kwargs = dict(
        build_messages=lambda raw, issues: {},
        generate=lambda messages: {"a": 1},
        parse=lambda raw: {"ok": True},
        validate=lambda result: [],
        on_success=lambda result, remaining: "success",
        on_exhausted=lambda issues: "fallback",
        raw_output={},
        validation_issues=[_error()],
        log_label="Test",
    )
    kwargs[failing] = boom

    with pytest.raises(ValueError):
        run_repair_loop(**kwargs)
