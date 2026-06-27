"""SymbolRegistryResult validation (issue #355)."""
from __future__ import annotations

from .schema import (
    DEFINITION_STATUSES,
    MATURITY_SOURCES,
    SYMBOL_KINDS,
    SYMBOL_SCOPES,
    SymbolRegistryResult,
    ValidationIssue,
)


class SymbolRegistryValidator:
    def validate(self, result: SymbolRegistryResult) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        seen_ids: set[str] = set()
        for record in result.records or []:
            field = record.symbol_id or record.canonical_symbol
            if not record.symbol_id:
                issues.append(ValidationIssue(
                    "symbol_id_missing", "error",
                    f"symbol record {record.canonical_symbol!r} has no symbol_id",
                    field,
                ))
            elif record.symbol_id in seen_ids:
                issues.append(ValidationIssue(
                    "symbol_id_duplicate", "error",
                    f"duplicate symbol_id {record.symbol_id!r}",
                    field,
                ))
            else:
                seen_ids.add(record.symbol_id)

            if not record.canonical_symbol:
                issues.append(ValidationIssue(
                    "canonical_symbol_empty", "error",
                    f"symbol {record.symbol_id!r} has empty canonical_symbol",
                    field,
                ))
            if record.kind not in SYMBOL_KINDS:
                issues.append(ValidationIssue(
                    "invalid_symbol_kind", "error",
                    f"symbol {field!r} has invalid kind {record.kind!r}",
                    field,
                ))
            if record.scope not in SYMBOL_SCOPES:
                issues.append(ValidationIssue(
                    "invalid_symbol_scope", "error",
                    f"symbol {field!r} has invalid scope {record.scope!r}",
                    field,
                ))
            if record.definition_status not in DEFINITION_STATUSES:
                issues.append(ValidationIssue(
                    "invalid_definition_status", "error",
                    f"symbol {field!r} has invalid definition_status "
                    f"{record.definition_status!r}",
                    field,
                ))
            if record.maturity_source not in MATURITY_SOURCES:
                issues.append(ValidationIssue(
                    "invalid_maturity_source", "error",
                    f"symbol {field!r} has invalid maturity_source "
                    f"{record.maturity_source!r}",
                    field,
                ))
            if record.definition_status == "redefined" and \
                    "symbol_redefinition" not in (record.review_reasons or []):
                issues.append(ValidationIssue(
                    "redefinition_without_review_reason", "warning",
                    f"redefined symbol {field!r} lacks symbol_redefinition "
                    "review reason",
                    field,
                ))
            if not (0.0 <= float(record.confidence) <= 1.0):
                issues.append(ValidationIssue(
                    "confidence_out_of_bounds", "error",
                    f"symbol {field!r} confidence {record.confidence} out of [0,1]",
                    field,
                ))
        return issues
