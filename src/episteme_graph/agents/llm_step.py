"""Shared repair loop for the LLM structured-output agents.

Every LLM-first agent in :mod:`episteme_graph.agents` follows the same
"generate → parse → validate → repair" shape. This module owns that loop once;
before the consolidation it existed as ~11 near-identical copies in the agents'
``repair.py`` files.

Behavioral contract
-------------------
The caller (``agent.py``) makes the **initial** LLM call itself, validates the
result, and only enters :func:`run_repair_loop` when that first validation
produced at least one ``severity == "error"`` issue. The loop then makes **up
to** :data:`MAX_REPAIR_ATTEMPTS` further calls, so the ceiling per item is
1 + 2 = 3 LLM calls.

Per attempt:

1. ``build_messages(raw_output, validation_issues)`` builds the repair prompt
   from the *previous* raw output and the *current* issues.
2. ``generate(messages)`` calls the LLM. **An exception breaks the loop** — no
   further attempts are made and the caller's ``on_exhausted`` fallback is
   returned. (Contrast with ``core/llm_worker`` below.)
3. ``parse(raw_output)`` turns the raw dict into the agent's record/result.
4. ``validate(result)`` returns the remaining issues.
5. **Warnings are accepted**: if no issue has ``severity == "error"`` the loop
   returns ``on_success(result, remaining_issues)`` immediately. Otherwise the
   remaining issues become the input of the next attempt.

After the attempts are exhausted (or the loop broke on an LLM exception) the
loop returns ``on_exhausted(validation_issues)`` with the *last* issue list it
saw. The fallback object is always the caller's business: this module never
constructs one, never attaches ``validation_issues`` to it, and never decides
the fallback reason string.

Not to be merged with ``backend/core/llm_worker/repair.run_with_repair``
-----------------------------------------------------------------------
That runner looks similar but has a deliberately **different** contract and the
two must not be unified:

* it *continues* to the next attempt when the LLM call raises (this one breaks);
* it speaks the ``complete_json(prompt: str) -> str`` protocol of the async
  worker clients, while this one speaks the agents' ``generate(messages) -> dict``
  structured-output protocol;
* it owns the post-failure handling through ``on_repair_failed``, while here the
  caller owns the fallback.

``discuss_opening`` and ``landscape_placement`` are built on that worker runner
and are intentionally *not* migrated here.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Iterable, TypeVar

logger = logging.getLogger(__name__)

__all__ = [
    "MAX_REPAIR_ATTEMPTS",
    "attach_issues",
    "error_issues",
    "has_errors",
    "run_repair_loop",
]

#: 1 initial call (made by the caller) + this many repair attempts.
MAX_REPAIR_ATTEMPTS = 2

R = TypeVar("R")


def error_issues(issues: Iterable[Any]) -> list[Any]:
    """The subset of ``issues`` whose ``severity`` is ``"error"``."""
    return [issue for issue in issues if issue.severity == "error"]


def has_errors(issues: Iterable[Any]) -> bool:
    """True when at least one issue is a hard error (warnings are accepted)."""
    return any(issue.severity == "error" for issue in issues)


def attach_issues(result: Any, issues: list[Any]) -> Any:
    """``on_success`` for agents whose result object carries its own issues.

    Agents that return a bare sub-record (a single annotation / span / equation
    record) instead pass ``lambda record, _remaining: record``.
    """
    result.validation_issues = issues
    return result


def run_repair_loop(
    *,
    build_messages: Callable[[Any, list[Any]], Any],
    generate: Callable[[Any], dict],
    parse: Callable[[dict], Any],
    validate: Callable[[Any], list[Any]],
    on_success: Callable[[Any, list[Any]], R],
    on_exhausted: Callable[[list[Any]], R],
    raw_output: dict,
    validation_issues: list[Any],
    log_label: str,
    log_suffix: str = "",
    max_attempts: int = MAX_REPAIR_ATTEMPTS,
    on_attempt_llm_error: Callable[[int, BaseException], None] | None = None,
    on_attempt_parsed: Callable[[int, dict], None] | None = None,
    on_attempt_validated: Callable[[int, list[Any]], None] | None = None,
) -> R:
    """Run the shared repair loop; see the module docstring for the contract.

    Parameters
    ----------
    build_messages:
        ``(raw_output, validation_issues) -> messages`` for the repair prompt.
    generate:
        ``(messages) -> raw dict``. Any per-agent extras (vision ``image=`` /
        ``images=`` payloads, a client resolved at call time) belong in this
        closure. An exception raised here breaks the loop.
    parse:
        ``(raw) -> result``. Deterministic post-processing (cleanup, enrichment,
        id canonicalization) belongs in this closure.
    validate:
        ``(result) -> issues``. Partial-result wrappers belong in this closure.
    on_success:
        ``(result, remaining_issues) -> R``. Agents that expose the issues on the
        result do ``result.validation_issues = remaining`` here; agents that
        return a bare sub-record just return the record.
    on_exhausted:
        ``(last_issues) -> R``. Builds the agent-specific fallback.
    log_label / log_suffix:
        Used for the uniform ``"%s repair attempt %d/%d%s"`` log line; the suffix
        carries per-item context (e.g. ``" figure=fig_1"``).
    on_attempt_llm_error / on_attempt_parsed / on_attempt_validated:
        Optional per-attempt diagnostics hooks, called with the 1-based attempt
        number and, respectively, the exception, the raw LLM output (before
        ``parse``) and the issues returned by ``validate``.
    """
    for attempt in range(1, max_attempts + 1):
        logger.info("%s repair attempt %d/%d%s", log_label, attempt, max_attempts, log_suffix)
        messages = build_messages(raw_output, validation_issues)
        try:
            raw_output = generate(messages)
        except Exception as exc:  # noqa: BLE001 — an LLM failure ends the loop
            logger.warning("%s repair LLM call failed%s: %s", log_label, log_suffix, exc)
            if on_attempt_llm_error is not None:
                on_attempt_llm_error(attempt, exc)
            break

        if on_attempt_parsed is not None:
            on_attempt_parsed(attempt, raw_output)

        result = parse(raw_output)
        remaining = validate(result)
        if on_attempt_validated is not None:
            on_attempt_validated(attempt, remaining)

        if not has_errors(remaining):
            return on_success(result, remaining)
        validation_issues = remaining

    return on_exhausted(validation_issues)
