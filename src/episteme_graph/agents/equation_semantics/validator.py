"""Validation for EquationSemanticsAgent outputs."""
from __future__ import annotations

from .schema import (
    DEFINITION_STATUSES,
    EQUATION_ROLES,
    LINKED_TEXT_RELATIONS,
    REVIEW_FLAGS,
    CartridgeContext,
    EquationSemanticsRecord,
    EquationSemanticsResult,
    ValidationIssue,
)


class EquationSemanticsValidator:
    def validate(
        self,
        result: EquationSemanticsResult,
        cartridge: CartridgeContext | None = None,
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if not result.equations:
            return [ValidationIssue(
                rule_id="no_equations",
                severity="error",
                message="No equation semantics records are present",
                field="equations",
            )]
        for record in result.equations:
            issues += self._check_record(record)
            issues += self._check_consistency(record)
            issues += self._check_cartridge_terms(record, cartridge)
        return issues

    def _check_record(self, record: EquationSemanticsRecord) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if not record.equation_id:
            issues.append(ValidationIssue(
                rule_id="missing_equation_id",
                severity="error",
                message="equation_id is empty",
                field=f"{record.block_id}.equation_id",
            ))
        primary = record.equation_role.primary
        if primary not in EQUATION_ROLES:
            issues.append(ValidationIssue(
                rule_id="invalid_equation_role",
                severity="error",
                message=f"{record.equation_id} has invalid primary role {primary!r}",
                field=f"{record.equation_id}.equation_role.primary",
            ))
        for role in record.equation_role.secondary:
            if role not in EQUATION_ROLES:
                issues.append(ValidationIssue(
                    rule_id="invalid_equation_role",
                    severity="error",
                    message=f"{record.equation_id} has invalid secondary role {role!r}",
                    field=f"{record.equation_id}.equation_role.secondary",
                ))
        if not (0.0 <= record.equation_role.confidence <= 1.0):
            issues.append(ValidationIssue(
                rule_id="confidence_out_of_range",
                severity="error",
                message=f"{record.equation_id} confidence is out of range",
                field=f"{record.equation_id}.equation_role.confidence",
            ))
        for symbol in record.defined_symbols:
            if symbol.definition_status not in DEFINITION_STATUSES:
                issues.append(ValidationIssue(
                    rule_id="invalid_definition_status",
                    severity="error",
                    message=(
                        f"{record.equation_id} has invalid definition_status "
                        f"{symbol.definition_status!r}"
                    ),
                    field=f"{record.equation_id}.defined_symbols",
                ))
        for span in record.derivation_links.linked_text_spans:
            relation = span.get("relation")
            if relation not in LINKED_TEXT_RELATIONS:
                issues.append(ValidationIssue(
                    rule_id="invalid_linked_text_relation",
                    severity="error",
                    message=f"{record.equation_id} has invalid linked text relation {relation!r}",
                    field=f"{record.equation_id}.derivation_links.linked_text_spans",
                ))
        for flag in record.review_flags:
            if flag not in REVIEW_FLAGS:
                issues.append(ValidationIssue(
                    rule_id="invalid_review_flag",
                    severity="error",
                    message=f"{record.equation_id} has invalid review flag {flag!r}",
                    field=f"{record.equation_id}.review_flags",
                ))
        if not record.summary.strip():
            issues.append(ValidationIssue(
                rule_id="empty_summary",
                severity="error",
                message=f"{record.equation_id} summary is empty",
                field=f"{record.equation_id}.summary",
            ))
        return issues

    def _check_consistency(self, record: EquationSemanticsRecord) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        primary = record.equation_role.primary
        if primary == "equation_definition" and not record.defined_symbols:
            issues.append(ValidationIssue(
                rule_id="definition_without_symbols",
                severity="warning",
                message=f"{record.equation_id} is a definition but has no defined_symbols",
                field=f"{record.equation_id}.defined_symbols",
            ))
        if primary == "equation_transformation" and not record.derivation_links.from_equations:
            issues.append(ValidationIssue(
                rule_id="transformation_without_from_equations",
                severity="warning",
                message=f"{record.equation_id} is a transformation but has no from_equations",
                field=f"{record.equation_id}.derivation_links.from_equations",
            ))
        if not record.label and "missing_label" not in record.review_flags:
            issues.append(ValidationIssue(
                rule_id="missing_label_without_review_flag",
                severity="warning",
                message=f"{record.equation_id} has no label and no missing_label review flag",
                field=f"{record.equation_id}.review_flags",
            ))
        if record.equation_role.confidence < 0.5 and "low_confidence" not in record.review_flags:
            issues.append(ValidationIssue(
                rule_id="low_confidence_without_review_flag",
                severity="warning",
                message=f"{record.equation_id} has low confidence without review flag",
                field=f"{record.equation_id}.review_flags",
            ))
        if primary == "equation_result" and len(record.summary.split()) < 4:
            issues.append(ValidationIssue(
                rule_id="result_summary_too_vague",
                severity="warning",
                message=f"{record.equation_id} result summary appears too vague",
                field=f"{record.equation_id}.summary",
            ))
        return issues

    def _check_cartridge_terms(
        self,
        record: EquationSemanticsRecord,
        cartridge: CartridgeContext | None,
    ) -> list[ValidationIssue]:
        if not cartridge or not cartridge.aliases:
            return []
        text = f"{record.text} {record.plain_text or ''} {record.summary}".lower()
        matched = [
            canonical
            for canonical, aliases in cartridge.aliases.items()
            if canonical.lower() in text
            or any(str(alias).lower() in text for alias in aliases)
        ]
        if matched and not record.defined_symbols:
            return [ValidationIssue(
                rule_id="cartridge_notation_without_symbols",
                severity="warning",
                message=(
                    f"{record.equation_id} mentions cartridge term(s) {matched[:3]} "
                    "but has no defined_symbols"
                ),
                field=f"{record.equation_id}.defined_symbols",
            )]
        return []
