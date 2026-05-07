"""Validation for EquationSemanticsAgent outputs.

Issue #245: EquationCandidate / EquationRecord の hard error / warning を実装。
"""
from __future__ import annotations

from .schema import (
    ACCEPTANCE_STATUSES,
    CANDIDATE_REVIEW_REASONS,
    DEFINITION_STATUSES,
    EQUATION_TYPES,
    EXTRACTION_STATUSES,
    LINKED_TEXT_RELATIONS,
    RECONSTRUCTION_STATUSES,
    REVIEW_FLAGS,
    SEMANTIC_STATUSES,
    CartridgeContext,
    EquationCandidate,
    EquationRecord,
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

        # --- candidates ---
        for candidate in result.equation_candidates:
            issues += self._check_candidate(candidate)

        # --- equations (accepted) ---
        if not result.equations and not result.equation_candidates:
            return [ValidationIssue(
                rule_id="no_equations",
                severity="error",
                message="No equation candidates or semantics records are present",
                field="equations",
            )]

        for record in result.equations:
            issues += self._check_record(record)
            issues += self._check_reconstruction(record)
            issues += self._check_cartridge_terms(record, cartridge)

        return issues

    # ------------------------------------------------------------------
    # Candidate validation
    # ------------------------------------------------------------------

    def _check_candidate(self, c: EquationCandidate) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        cid = c.candidate_id

        if c.extraction_status not in EXTRACTION_STATUSES:
            issues.append(ValidationIssue(
                rule_id="invalid_extraction_status",
                severity="error",
                message=f"{cid} has invalid extraction_status {c.extraction_status!r}",
                field=f"{cid}.extraction_status",
            ))
        if c.acceptance_status not in ACCEPTANCE_STATUSES:
            issues.append(ValidationIssue(
                rule_id="invalid_acceptance_status",
                severity="error",
                message=f"{cid} has invalid acceptance_status {c.acceptance_status!r}",
                field=f"{cid}.acceptance_status",
            ))

        # Hard errors: fragment / label_only として accepted は禁止
        if c.extraction_status == "fragment_only" and c.acceptance_status == "accepted":
            issues.append(ValidationIssue(
                rule_id="fragment_only_accepted",
                severity="error",
                message=f"{cid} is fragment_only but accepted — must not be treated as equation body",
                field=f"{cid}.acceptance_status",
            ))
        if c.extraction_status == "label_only" and c.acceptance_status == "accepted":
            issues.append(ValidationIssue(
                rule_id="label_only_accepted",
                severity="error",
                message=f"{cid} is label_only but accepted — must not be treated as equation body",
                field=f"{cid}.acceptance_status",
            ))

        for r in c.review_reason:
            if r not in CANDIDATE_REVIEW_REASONS:
                issues.append(ValidationIssue(
                    rule_id="invalid_candidate_review_reason",
                    severity="warning",
                    message=f"{cid} has unknown review_reason {r!r}",
                    field=f"{cid}.review_reason",
                ))

        return issues

    # ------------------------------------------------------------------
    # EquationRecord validation
    # ------------------------------------------------------------------

    def _check_record(self, record: EquationRecord) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        eid = record.equation_id

        if not eid:
            issues.append(ValidationIssue(
                rule_id="missing_equation_id",
                severity="error",
                message="equation_id is empty",
                field="equation_id",
            ))

        # candidate_trace_ids must not be empty for accepted equations
        if not record.candidate_trace_ids:
            issues.append(ValidationIssue(
                rule_id="missing_candidate_trace",
                severity="error",
                message=f"{eid} accepted equation has no candidate_trace_ids",
                field=f"{eid}.candidate_trace_ids",
            ))

        # source_extraction.block_id must exist
        block_id = record.source_extraction.source_location.get("block_id", "")
        if not block_id:
            issues.append(ValidationIssue(
                rule_id="missing_source_block_id",
                severity="error",
                message=f"{eid} source_extraction.source_location.block_id is empty",
                field=f"{eid}.source_extraction.source_location.block_id",
            ))

        # extraction_status validation
        ext_status = record.source_extraction.extraction_status
        if ext_status not in EXTRACTION_STATUSES:
            issues.append(ValidationIssue(
                rule_id="invalid_extraction_status",
                severity="error",
                message=f"{eid} has invalid source_extraction.extraction_status {ext_status!r}",
                field=f"{eid}.source_extraction.extraction_status",
            ))

        # semantics validation
        sem = record.semantics
        if sem.equation_type not in EQUATION_TYPES:
            issues.append(ValidationIssue(
                rule_id="invalid_equation_type",
                severity="error",
                message=f"{eid} has invalid equation_type {sem.equation_type!r}",
                field=f"{eid}.semantics.equation_type",
            ))
        for t in sem.secondary_types:
            if t not in EQUATION_TYPES:
                issues.append(ValidationIssue(
                    rule_id="invalid_equation_type",
                    severity="error",
                    message=f"{eid} has invalid secondary_type {t!r}",
                    field=f"{eid}.semantics.secondary_types",
                ))
        if sem.semantic_status not in SEMANTIC_STATUSES:
            issues.append(ValidationIssue(
                rule_id="invalid_semantic_status",
                severity="error",
                message=f"{eid} has invalid semantic_status {sem.semantic_status!r}",
                field=f"{eid}.semantics.semantic_status",
            ))
        if not (0.0 <= sem.confidence <= 1.0):
            issues.append(ValidationIssue(
                rule_id="confidence_out_of_range",
                severity="error",
                message=f"{eid} semantics.confidence is out of range",
                field=f"{eid}.semantics.confidence",
            ))
        for symbol in sem.defined_symbols:
            if symbol.definition_status not in DEFINITION_STATUSES:
                issues.append(ValidationIssue(
                    rule_id="invalid_definition_status",
                    severity="error",
                    message=f"{eid} has invalid definition_status {symbol.definition_status!r}",
                    field=f"{eid}.semantics.defined_symbols",
                ))
        for span in sem.linked_text_spans:
            relation = span.get("relation")
            if relation not in LINKED_TEXT_RELATIONS:
                issues.append(ValidationIssue(
                    rule_id="invalid_linked_text_relation",
                    severity="error",
                    message=f"{eid} has invalid linked text relation {relation!r}",
                    field=f"{eid}.semantics.linked_text_spans",
                ))
        for flag in sem.review_flags:
            if flag not in REVIEW_FLAGS:
                issues.append(ValidationIssue(
                    rule_id="invalid_review_flag",
                    severity="error",
                    message=f"{eid} has invalid review flag {flag!r}",
                    field=f"{eid}.semantics.review_flags",
                ))
        if not sem.summary.strip():
            issues.append(ValidationIssue(
                rule_id="empty_summary",
                severity="error",
                message=f"{eid} semantics.summary is empty",
                field=f"{eid}.semantics.summary",
            ))

        # Hard error: reconstruction_based が can_support_claim=True は禁止
        if record.confidence_policy.must_not_treat_as_source_extracted and record.confidence_policy.can_support_claim:
            issues.append(ValidationIssue(
                rule_id="reconstruction_only_supporting_claim",
                severity="error",
                message=(
                    f"{eid} must_not_treat_as_source_extracted is True "
                    "but can_support_claim is also True"
                ),
                field=f"{eid}.confidence_policy",
            ))

        # Consistency warnings
        issues += self._check_semantics_consistency(record)

        return issues

    def _check_reconstruction(self, record: EquationRecord) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        eid = record.equation_id
        rec = record.reconstruction

        if rec.status not in RECONSTRUCTION_STATUSES:
            issues.append(ValidationIssue(
                rule_id="invalid_reconstruction_status",
                severity="error",
                message=f"{eid} has invalid reconstruction.status {rec.status!r}",
                field=f"{eid}.reconstruction.status",
            ))

        if record.source_extraction.needs_math_review and rec.status == "none":
            issues.append(ValidationIssue(
                rule_id="missing_mandatory_reconstruction",
                severity="error",
                message=f"{eid} PDF-derived math must pass through reconstruction",
                field=f"{eid}.reconstruction.status",
            ))

        if rec.status != "none":
            # method / supporting_refs must not be empty when reconstructing
            if not rec.method:
                issues.append(ValidationIssue(
                    rule_id="reconstruction_missing_method",
                    severity="error",
                    message=f"{eid} reconstruction.status={rec.status!r} but method is empty",
                    field=f"{eid}.reconstruction.method",
                ))
            if not rec.supporting_refs:
                issues.append(ValidationIssue(
                    rule_id="reconstruction_missing_supporting_refs",
                    severity="error",
                    message=f"{eid} reconstruction.status={rec.status!r} but supporting_refs is empty",
                    field=f"{eid}.reconstruction.supporting_refs",
                ))
            if not (0.0 <= rec.confidence <= 1.0):
                issues.append(ValidationIssue(
                    rule_id="reconstruction_confidence_out_of_range",
                    severity="error",
                    message=f"{eid} reconstruction.confidence is out of range",
                    field=f"{eid}.reconstruction.confidence",
                ))
            # reconstructed content must not contaminate source_extraction fields
            src = record.source_extraction
            if rec.plain_text and src.plain_text and rec.plain_text == src.plain_text and src.extraction_status in ("missing", "label_only", "fragment_only"):
                issues.append(ValidationIssue(
                    rule_id="reconstruction_contaminating_source",
                    severity="error",
                    message=f"{eid} reconstructed plain_text appears in source_extraction field",
                    field=f"{eid}.source_extraction.plain_text",
                ))

        return issues

    def _check_semantics_consistency(self, record: EquationRecord) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        eid = record.equation_id
        sem = record.semantics

        if sem.equation_type == "definition" and not sem.defined_symbols:
            issues.append(ValidationIssue(
                rule_id="definition_without_symbols",
                severity="warning",
                message=f"{eid} is a definition but has no defined_symbols",
                field=f"{eid}.semantics.defined_symbols",
            ))
        if sem.equation_type == "transformation" and not sem.input_equation_ids:
            issues.append(ValidationIssue(
                rule_id="transformation_without_input_equations",
                severity="warning",
                message=f"{eid} is a transformation but has no input_equation_ids",
                field=f"{eid}.semantics.input_equation_ids",
            ))
        if not record.label and "missing_label" not in sem.review_flags:
            issues.append(ValidationIssue(
                rule_id="missing_label_without_review_flag",
                severity="warning",
                message=f"{eid} has no label and no missing_label review flag",
                field=f"{eid}.semantics.review_flags",
            ))
        if sem.confidence < 0.5 and "low_confidence" not in sem.review_flags:
            issues.append(ValidationIssue(
                rule_id="low_confidence_without_review_flag",
                severity="warning",
                message=f"{eid} has low confidence without review flag",
                field=f"{eid}.semantics.review_flags",
            ))

        # extraction_status が complete なのに source_backed でない場合は警告
        src_status = record.source_extraction.extraction_status
        if src_status == "complete" and sem.semantic_status not in ("source_backed", "context_inferred"):
            issues.append(ValidationIssue(
                rule_id="source_complete_but_not_source_backed",
                severity="warning",
                message=f"{eid} extraction is complete but semantic_status is {sem.semantic_status!r}",
                field=f"{eid}.semantics.semantic_status",
            ))
        # extraction が不十分なのに source_backed を主張している場合は警告
        if src_status in ("missing", "label_only", "fragment_only") and sem.semantic_status == "source_backed":
            issues.append(ValidationIssue(
                rule_id="insufficient_extraction_but_source_backed",
                severity="warning",
                message=(
                    f"{eid} extraction_status={src_status!r} "
                    "but semantic_status claims source_backed"
                ),
                field=f"{eid}.semantics.semantic_status",
            ))

        return issues

    def _check_cartridge_terms(
        self,
        record: EquationRecord,
        cartridge: CartridgeContext | None,
    ) -> list[ValidationIssue]:
        if not cartridge or not cartridge.aliases:
            return []
        src = record.source_extraction
        sem = record.semantics
        text = f"{src.raw_text} {src.plain_text or ''} {sem.summary}".lower()
        matched = [
            canonical
            for canonical, aliases in cartridge.aliases.items()
            if canonical.lower() in text
            or any(str(alias).lower() in text for alias in aliases)
        ]
        if matched and not sem.defined_symbols:
            return [ValidationIssue(
                rule_id="cartridge_notation_without_symbols",
                severity="warning",
                message=(
                    f"{record.equation_id} mentions cartridge term(s) {matched[:3]} "
                    "but has no defined_symbols"
                ),
                field=f"{record.equation_id}.semantics.defined_symbols",
            )]
        return []
