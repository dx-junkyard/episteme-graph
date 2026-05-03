"""Validation for RhetoricalRoleAgent outputs."""
from __future__ import annotations

from .schema import (
    CORE_CLAIM_ROLES,
    EXCLUSION_ROLES,
    ROLE_LABELS,
    CartridgeContext,
    RhetoricalRoleResult,
    ValidationIssue,
)


class RhetoricalRoleValidator:
    def validate(
        self,
        result: RhetoricalRoleResult,
        block_text_by_id: dict[str, str] | None = None,
        cartridge: CartridgeContext | None = None,
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []

        issues += self._check_annotations_present(result)
        issues += self._check_span_content(result, block_text_by_id or {})
        issues += self._check_role_labels(result)
        issues += self._check_candidate_flags(result)
        issues += self._check_confidence_range(result)
        issues += self._check_exclusion_roles(result)
        issues += self._check_cartridge_hints(result, block_text_by_id or {}, cartridge)

        return issues

    def _check_annotations_present(
        self, result: RhetoricalRoleResult
    ) -> list[ValidationIssue]:
        if not result.role_annotations:
            return [ValidationIssue(
                rule_id="no_role_annotations",
                severity="error",
                message="role_annotations is empty",
                field="role_annotations",
            )]
        issues = []
        for annotation in result.role_annotations:
            if not annotation.span_annotations:
                issues.append(ValidationIssue(
                    rule_id="no_span_annotations",
                    severity="error",
                    message=f"block {annotation.block_id} has no span_annotations",
                    field=f"role_annotations[{annotation.block_id}].span_annotations",
                ))
        return issues

    def _check_span_content(
        self,
        result: RhetoricalRoleResult,
        block_text_by_id: dict[str, str],
    ) -> list[ValidationIssue]:
        issues = []
        for annotation in result.role_annotations:
            block_text = block_text_by_id.get(annotation.block_id)
            for span in annotation.span_annotations:
                field = f"role_annotations[{annotation.block_id}].{span.span_id}"
                if span.char_start >= span.char_end:
                    issues.append(ValidationIssue(
                        rule_id="invalid_char_range",
                        severity="error",
                        message=(
                            f"{span.span_id} has invalid range "
                            f"{span.char_start}:{span.char_end}"
                        ),
                        field=field,
                    ))
                    continue
                if block_text is None:
                    continue
                if span.char_start < 0 or span.char_end > len(block_text):
                    issues.append(ValidationIssue(
                        rule_id="char_range_out_of_bounds",
                        severity="error",
                        message=f"{span.span_id} range is outside block text",
                        field=field,
                    ))
                    continue
                extracted = block_text[span.char_start:span.char_end]
                if extracted != span.text:
                    issues.append(ValidationIssue(
                        rule_id="span_text_mismatch",
                        severity="error",
                        message=(
                            f"{span.span_id} text does not match block substring"
                        ),
                        field=field,
                    ))
        return issues

    def _check_role_labels(
        self, result: RhetoricalRoleResult
    ) -> list[ValidationIssue]:
        allowed = set(ROLE_LABELS)
        issues = []
        for annotation in result.role_annotations:
            for span in annotation.span_annotations:
                if not span.role_labels:
                    issues.append(ValidationIssue(
                        rule_id="missing_role_labels",
                        severity="error",
                        message=f"{span.span_id} has no role labels",
                        field=f"role_annotations[{annotation.block_id}].{span.span_id}.role_labels",
                    ))
                for label in span.role_labels:
                    if label not in allowed:
                        issues.append(ValidationIssue(
                            rule_id="invalid_role_label",
                            severity="error",
                            message=f"{span.span_id} has invalid role label '{label}'",
                            field=f"role_annotations[{annotation.block_id}].{span.span_id}.role_labels",
                        ))
        return issues

    def _check_candidate_flags(
        self, result: RhetoricalRoleResult
    ) -> list[ValidationIssue]:
        issues = []
        for annotation in result.role_annotations:
            for span in annotation.span_annotations:
                if span.is_claim_candidate and span.is_reject_candidate:
                    issues.append(ValidationIssue(
                        rule_id="conflicting_candidate_flags",
                        severity="error",
                        message=(
                            f"{span.span_id} is both claim candidate and reject candidate"
                        ),
                        field=f"role_annotations[{annotation.block_id}].{span.span_id}",
                    ))
        return issues

    def _check_confidence_range(
        self, result: RhetoricalRoleResult
    ) -> list[ValidationIssue]:
        issues = []
        for annotation in result.role_annotations:
            for span in annotation.span_annotations:
                if not (0.0 <= span.confidence <= 1.0):
                    issues.append(ValidationIssue(
                        rule_id="confidence_out_of_range",
                        severity="error",
                        message=f"{span.span_id} confidence={span.confidence} is out of range",
                        field=f"role_annotations[{annotation.block_id}].{span.span_id}.confidence",
                    ))
        return issues

    def _check_exclusion_roles(
        self, result: RhetoricalRoleResult
    ) -> list[ValidationIssue]:
        issues = []
        for annotation in result.role_annotations:
            for span in annotation.span_annotations:
                labels = set(span.role_labels)
                if labels & EXCLUSION_ROLES and span.is_claim_candidate and not span.is_reject_candidate:
                    issues.append(ValidationIssue(
                        rule_id="exclusion_role_marked_claim_only",
                        severity="warning",
                        message=(
                            f"{span.span_id} has exclusion role(s) but is marked claim-only"
                        ),
                        field=f"role_annotations[{annotation.block_id}].{span.span_id}",
                    ))
                if labels & CORE_CLAIM_ROLES and span.is_reject_candidate and not (labels & EXCLUSION_ROLES):
                    issues.append(ValidationIssue(
                        rule_id="core_role_marked_reject",
                        severity="warning",
                        message=f"{span.span_id} has only core roles but is rejected",
                        field=f"role_annotations[{annotation.block_id}].{span.span_id}",
                    ))
        return issues

    def _check_cartridge_hints(
        self,
        result: RhetoricalRoleResult,
        block_text_by_id: dict[str, str],
        cartridge: CartridgeContext | None,
    ) -> list[ValidationIssue]:
        if not cartridge or not cartridge.aliases:
            return []

        issues = []
        for annotation in result.role_annotations:
            block_text = block_text_by_id.get(annotation.block_id, "")
            lowered = block_text.lower()
            matched = [
                canonical
                for canonical, aliases in cartridge.aliases.items()
                if canonical.lower() in lowered
                or any(str(alias).lower() in lowered for alias in aliases)
            ]
            if matched:
                has_content_span = any(
                    set(span.role_labels) & CORE_CLAIM_ROLES
                    for span in annotation.span_annotations
                )
                if not has_content_span:
                    issues.append(ValidationIssue(
                        rule_id="cartridge_alias_without_content_role",
                        severity="warning",
                        message=(
                            f"block {annotation.block_id} mentions cartridge term(s) "
                            f"{matched[:3]} but has no core content role"
                        ),
                        field=f"role_annotations[{annotation.block_id}]",
                    ))
        return issues
