"""Repair helpers for ContextualExplanationAgent.

Design: ``docs/features/hierarchical_context_explanation_design.md`` §5.1 —
"修復失敗要素は結果から落とさず skipped_reason='repair_failed' で保持".

Unlike the 1-call-per-item agents elsewhere in this codebase, this agent
batches several elements into one LLM call, so a naive whole-batch repair
(regenerate everything, discard everything on failure) would throw away
already-correct sibling explanations whenever a single stubborn element
keeps failing. Instead, each repair attempt is *targeted*: only the
element_ids that still carry a hard-error validation issue are resent to the
model (1 initial call + up to 2 repair attempts, same ceiling as every other
agent in this codebase). Elements that still fail after the ceiling are kept
in the result — never dropped — tagged ``skipped_reason="repair_failed"``.
"""
from __future__ import annotations

import logging
import re

from .schema import (
    ElementExplanationInput,
    ElementExplanationResult,
    SKIPPED_REASON_REPAIR_FAILED,
)

logger = logging.getLogger(__name__)

_MAX_REPAIR_ATTEMPTS = 2

_FIELD_ID_RE = re.compile(r"^elements\[(.*)\]$")


def parse_batch_output(
    raw: dict,
    batch: list[ElementExplanationInput],
) -> list[ElementExplanationResult]:
    """Parse a raw LLM JSON batch response into result objects.

    Entries are kept even when their ``element_id`` is unknown or duplicated
    — the validator is responsible for flagging those; dropping them here
    would hide the problem instead of surfacing it for repair.
    """
    type_by_id = {item.element_id: item.element_type for item in batch}
    results: list[ElementExplanationResult] = []
    for entry in raw.get("elements") or []:
        if not isinstance(entry, dict):
            continue
        element_id = str(entry.get("element_id") or "")
        results.append(ElementExplanationResult(
            element_id=element_id,
            element_type=str(entry.get("element_type") or type_by_id.get(element_id, "")),
            contextual_explanation=_clean_text(entry.get("contextual_explanation")),
            generic_explanation=_clean_text(entry.get("generic_explanation")),
            evidence_quote=str(entry.get("evidence_quote") or "").strip(),
            reason=str(entry.get("reason") or "").strip(),
            confidence=_clamp(entry.get("confidence")),
            skipped_reason=_clean_text(entry.get("skipped_reason")),
        ))
    return results


def _clean_text(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _clamp(value) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def error_element_ids(issues) -> set[str]:
    """element_ids that have at least one severity='error' issue attached."""
    ids: set[str] = set()
    for issue in issues:
        if issue.severity != "error":
            continue
        match = _FIELD_ID_RE.match(issue.field or "")
        if match:
            ids.add(match.group(1))
    return ids


def _issue_element_id(issue) -> str | None:
    match = _FIELD_ID_RE.match(issue.field or "")
    return match.group(1) if match else None


class ContextualExplanationRepairer:
    def repair(
        self,
        *,
        batch: list[ElementExplanationInput],
        pending_ids: set[str],
        raw_output: dict,
        validation_issues: list,
        cartridge,
        llm_client,
        prompt_factory,
        validator,
    ) -> tuple[list[ElementExplanationResult], int]:
        """Retry only the still-invalid elements, up to ``_MAX_REPAIR_ATTEMPTS`` times.

        Returns ``(results, calls_made)`` where ``results`` covers every id in
        ``pending_ids`` (either repaired successfully, or a skipped
        placeholder — P4: never dropped).
        """
        by_id = {item.element_id: item for item in batch}
        pending_ids = {eid for eid in pending_ids if eid in by_id}
        valid: dict[str, ElementExplanationResult] = {}
        calls = 0
        current_pending = set(pending_ids)
        current_raw = raw_output
        current_issues = validation_issues

        for attempt in range(1, _MAX_REPAIR_ATTEMPTS + 1):
            if not current_pending:
                break
            sub_batch = [by_id[eid] for eid in current_pending]
            issues_for_sub = [
                i for i in current_issues
                if _issue_element_id(i) in current_pending or _issue_element_id(i) is None
            ]
            logger.info(
                "Contextual explanation repair attempt %d/%d for %d element(s)",
                attempt, _MAX_REPAIR_ATTEMPTS, len(sub_batch),
            )
            messages = prompt_factory.build_repair_messages(
                sub_batch, current_raw, issues_for_sub, cartridge
            )
            try:
                current_raw = llm_client.generate(messages)
                calls += 1
            except Exception as exc:
                logger.warning("Contextual explanation repair LLM call failed: %s", exc)
                break
            results = parse_batch_output(current_raw, sub_batch)
            current_issues = validator.validate(results, sub_batch)
            bad_ids = error_element_ids(current_issues)
            for result in results:
                if result.element_id in current_pending and result.element_id not in bad_ids:
                    valid[result.element_id] = result
            current_pending -= set(valid.keys())

        finalized: list[ElementExplanationResult] = []
        for eid in pending_ids:
            if eid in valid:
                finalized.append(valid[eid])
            else:
                finalized.append(ElementExplanationResult.make_skipped(
                    by_id[eid],
                    "validation failed after repair attempts",
                    SKIPPED_REASON_REPAIR_FAILED,
                ))
        return finalized, calls
