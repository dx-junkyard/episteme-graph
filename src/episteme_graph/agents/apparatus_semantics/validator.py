"""Validation for ApparatusSemanticsAgent outputs.

Enforces the controlled vocabularies, confidence ranges, and — the hard
guardrail — that ``source_backing_status`` is never ``'source_backed'``
(design principle #2/#4: vision-derived apparatus identification is
candidate-only, never treated as fully source-backed).
"""
from __future__ import annotations

import re

from .schema import (
    MATCH_STATUSES,
    REVIEW_STATUSES,
    SOURCE_BACKING_STATUSES,
    ApparatusRecord,
    ApparatusSemanticsResult,
    FigureImageInput,
    ValidationIssue,
)

_WHITESPACE_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", (text or "").strip().lower())


def _source_text(figure: FigureImageInput) -> str:
    parts = [figure.caption_text or ""]
    parts.extend(figure.nearby_text or [])
    return " ".join(parts)


class ApparatusSemanticsValidator:
    def validate(self, result: ApparatusSemanticsResult) -> list[ValidationIssue]:
        """Aggregate, context-free validation over the full result.

        This is the *second*, coarser pass run once at the end of
        ``agent.run()`` — the figure/candidate-aware checks (evidence quote
        entailment, matched_library_entry_id membership) already ran per
        figure via :meth:`validate_record` with context, inline in the
        generate → validate → repair loop.
        """
        issues: list[ValidationIssue] = []
        for record in result.apparatus_records:
            issues += self.validate_record(record)
        issues += self._check_duplicate_matches(result.apparatus_records)
        return issues

    def validate_record(
        self,
        record: ApparatusRecord,
        *,
        figure: FigureImageInput | None = None,
        candidate_ids: set[str] | None = None,
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        fid = record.figure_id

        if record.match_status not in MATCH_STATUSES:
            issues.append(ValidationIssue(
                rule_id="invalid_match_status",
                severity="error",
                message=f"{fid} has invalid match_status {record.match_status!r}",
                field=f"{fid}.match_status",
            ))

        # Hard guardrail: never accept 'source_backed' even though it is part
        # of the shared TheoryOperationGraph vocabulary (design principle #2/#4).
        if record.source_backing_status == "source_backed":
            issues.append(ValidationIssue(
                rule_id="apparatus_must_not_be_source_backed",
                severity="error",
                message=(
                    f"{fid} source_backing_status is 'source_backed'; vision-derived "
                    "apparatus identification must never be fully source-backed"
                ),
                field=f"{fid}.source_backing_status",
            ))
        elif record.source_backing_status not in SOURCE_BACKING_STATUSES:
            issues.append(ValidationIssue(
                rule_id="invalid_source_backing_status",
                severity="error",
                message=f"{fid} has invalid source_backing_status {record.source_backing_status!r}",
                field=f"{fid}.source_backing_status",
            ))

        if record.review_status not in REVIEW_STATUSES:
            issues.append(ValidationIssue(
                rule_id="invalid_review_status",
                severity="error",
                message=(
                    f"{fid} review_status must be one of {REVIEW_STATUSES}, "
                    f"got {record.review_status!r}"
                ),
                field=f"{fid}.review_status",
            ))

        if not (0.0 <= record.confidence <= 1.0):
            issues.append(ValidationIssue(
                rule_id="confidence_out_of_range",
                severity="error",
                message=f"{fid} confidence is out of range",
                field=f"{fid}.confidence",
            ))

        if record.match_status == "matched":
            if not record.matched_library_entry_id:
                issues.append(ValidationIssue(
                    rule_id="matched_without_entry_id",
                    severity="error",
                    message=f"{fid} match_status is 'matched' but matched_library_entry_id is empty",
                    field=f"{fid}.matched_library_entry_id",
                ))
            elif candidate_ids is not None and record.matched_library_entry_id not in candidate_ids:
                issues.append(ValidationIssue(
                    rule_id="matched_entry_id_not_in_candidates",
                    severity="error",
                    message=(
                        f"{fid} matched_library_entry_id "
                        f"{record.matched_library_entry_id!r} is not among the retrieved candidates"
                    ),
                    field=f"{fid}.matched_library_entry_id",
                ))
        elif record.matched_library_entry_id:
            issues.append(ValidationIssue(
                rule_id="unexpected_matched_entry_id",
                severity="warning",
                message=(
                    f"{fid} match_status={record.match_status!r} but matched_library_entry_id is set"
                ),
                field=f"{fid}.matched_library_entry_id",
            ))

        if record.match_status != "unknown" and not record.apparatus_name_candidate.strip():
            issues.append(ValidationIssue(
                rule_id="empty_apparatus_name_candidate",
                severity="error",
                message=f"{fid} has no apparatus_name_candidate",
                field=f"{fid}.apparatus_name_candidate",
            ))

        for idx, part in enumerate(record.parts):
            if not (0.0 <= part.confidence <= 1.0):
                issues.append(ValidationIssue(
                    rule_id="part_confidence_out_of_range",
                    severity="error",
                    message=f"{fid} part[{idx}] confidence is out of range",
                    field=f"{fid}.parts[{idx}].confidence",
                ))
            if not part.name.strip():
                issues.append(ValidationIssue(
                    rule_id="part_missing_name",
                    severity="error",
                    message=f"{fid} part[{idx}] has empty name",
                    field=f"{fid}.parts[{idx}].name",
                ))

        part_names = {p.name for p in record.parts if p.name}
        for idx, conn in enumerate(record.connections):
            if not (0.0 <= conn.confidence <= 1.0):
                issues.append(ValidationIssue(
                    rule_id="connection_confidence_out_of_range",
                    severity="error",
                    message=f"{fid} connection[{idx}] confidence is out of range",
                    field=f"{fid}.connections[{idx}].confidence",
                ))
            if part_names and (conn.from_part not in part_names or conn.to_part not in part_names):
                issues.append(ValidationIssue(
                    rule_id="connection_references_unknown_part",
                    severity="warning",
                    message=f"{fid} connection[{idx}] references a part not present in parts[]",
                    field=f"{fid}.connections[{idx}]",
                ))

        # Evidence-based check (design principle #4): evidence_quote should be
        # a verbatim excerpt of the caption/nearby text actually shown to the
        # model. Non-fatal (warning) — an LLM may lightly normalize
        # whitespace/punctuation when quoting; this does not block delivery,
        # only flags the record for human review (it is already
        # review_required regardless).
        if figure is not None:
            source_text = _normalize(_source_text(figure))
            if record.evidence_quote and _normalize(record.evidence_quote) not in source_text:
                issues.append(ValidationIssue(
                    rule_id="evidence_quote_not_verbatim",
                    severity="warning",
                    message=f"{fid} evidence_quote does not appear verbatim in caption/nearby text",
                    field=f"{fid}.evidence_quote",
                ))
            for idx, part in enumerate(record.parts):
                if part.evidence_quote and _normalize(part.evidence_quote) not in source_text:
                    issues.append(ValidationIssue(
                        rule_id="part_evidence_quote_not_verbatim",
                        severity="warning",
                        message=(
                            f"{fid} part[{idx}] evidence_quote does not appear "
                            "verbatim in caption/nearby text"
                        ),
                        field=f"{fid}.parts[{idx}].evidence_quote",
                    ))

        return issues

    @staticmethod
    def _check_duplicate_matches(records: list[ApparatusRecord]) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        seen: dict[str, str] = {}
        for record in records:
            if record.match_status != "matched" or not record.matched_library_entry_id:
                continue
            prior = seen.get(record.matched_library_entry_id)
            if prior and prior != record.figure_id:
                issues.append(ValidationIssue(
                    rule_id="duplicate_library_entry_match",
                    severity="info",
                    message=(
                        f"figures {prior} and {record.figure_id} both matched "
                        f"library entry {record.matched_library_entry_id}"
                    ),
                    field=record.matched_library_entry_id,
                ))
            else:
                seen[record.matched_library_entry_id] = record.figure_id
        return issues
