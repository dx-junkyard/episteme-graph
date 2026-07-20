"""Validation for ContextualExplanationAgent outputs.

Design: ``docs/features/hierarchical_context_explanation_design.md`` §5.1,
"validator の hard error（設計原則 E3/E6）":

- ``generic_explanation`` set but the input has no ``library_excerpt`` ->
  hard error (E3, hallucination guard — an un-linked element must never get
  a generic explanation, no matter how confident the model is).
- Checking whether ``contextual_explanation`` overreaches when upper/lower
  context are both empty is intentionally NOT attempted here (too fuzzy to
  validate reliably — explicitly out of scope per the design). The one
  narrower case that IS checked: any explanation text without an
  ``evidence_quote`` is a repair target (an explanation must always be
  grounded in *something* quotable).
- An output ``element_id`` that does not exist in the input batch -> hard
  error.

A few additional structural checks are included because they are needed to
make repair actually converge (duplicate/missing coverage, confidence
range) — they follow the same "error forces repair" pattern used by every
other agent's validator in this codebase.
"""
from __future__ import annotations

from .schema import ElementExplanationInput, ElementExplanationResult, ValidationIssue


class ContextualExplanationValidator:
    def validate(
        self,
        results: list[ElementExplanationResult],
        batch: list[ElementExplanationInput],
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        by_id = {item.element_id: item for item in batch}
        seen: set[str] = set()

        for result in results:
            field = f"elements[{result.element_id}]"
            input_item = by_id.get(result.element_id)

            if input_item is None:
                issues.append(ValidationIssue(
                    "unknown_element_id", "error",
                    f"{field} references an element_id not present in the input batch",
                    field,
                ))
                continue  # nothing further to check against a non-existent input

            if result.element_id in seen:
                issues.append(ValidationIssue(
                    "duplicate_element_output", "error",
                    f"{field} is emitted more than once",
                    field,
                ))
            seen.add(result.element_id)

            if result.element_type and result.element_type != input_item.element_type:
                issues.append(ValidationIssue(
                    "element_type_mismatch", "warning",
                    f"{field} element_type {result.element_type!r} does not match "
                    f"input element_type {input_item.element_type!r}",
                    field,
                ))

            # E3 hallucination guard: generic_explanation requires a library
            # excerpt on the INPUT side — the model cannot invent one.
            if result.generic_explanation is not None and not input_item.library_excerpt:
                issues.append(ValidationIssue(
                    "generic_without_library_excerpt", "error",
                    f"{field} has generic_explanation but the input has no library_excerpt",
                    field,
                ))

            has_explanation = bool(
                (result.contextual_explanation or "").strip()
                or (result.generic_explanation or "").strip()
            )
            if has_explanation and not (result.evidence_quote or "").strip():
                issues.append(ValidationIssue(
                    "explanation_missing_evidence_quote", "error",
                    f"{field} has explanation text but no evidence_quote",
                    field,
                ))

            if not (0.0 <= float(result.confidence) <= 1.0):
                issues.append(ValidationIssue(
                    "confidence_out_of_range", "error",
                    f"{field} confidence {result.confidence} out of [0,1]",
                    field,
                ))

            skipped_reason_present = bool((result.skipped_reason or "").strip())
            if not has_explanation and not skipped_reason_present:
                issues.append(ValidationIssue(
                    "empty_result_without_skipped_reason", "error",
                    f"{field} has no explanation and no skipped_reason "
                    "(P4: information must not be silently dropped)",
                    field,
                ))

        for element_id in by_id:
            if element_id not in seen:
                issues.append(ValidationIssue(
                    "missing_element_output", "error",
                    f"elements[{element_id}] has no output entry",
                    f"elements[{element_id}]",
                ))

        return issues
