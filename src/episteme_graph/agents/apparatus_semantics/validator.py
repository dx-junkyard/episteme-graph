"""Validation for ApparatusSemanticsAgent outputs.

Enforces the controlled vocabularies, confidence ranges, and — the hard
guardrail — that ``source_backing_status`` is never ``'source_backed'``
(design principle #2/#4: vision-derived apparatus identification is
candidate-only, never treated as fully source-backed).
"""
from __future__ import annotations

import re

from episteme_graph.agents.figure_modes import FIGURE_MODES

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


def _has_guidance_input(figure: FigureImageInput) -> bool:
    """Whether ``figure`` carries any teacher-guidance input for this run.

    Mirrors input_builder.build_guidance's non-emptiness test (hint text /
    focus region / focus crop image / focus labels) without depending on the
    normalized dict, so this validator can be called directly against a
    ``FigureImageInput`` alone.
    """
    return bool(
        (figure.guidance_text or "").strip()
        or figure.focus_bbox_rel
        or figure.focus_image_bytes
        or (figure.focus_label_texts or [])
    )


def _inner_label_texts(figure: FigureImageInput) -> list[str]:
    """Distinct, order-preserving in-figure label strings for one figure."""
    texts: list[str] = []
    for label in figure.inner_labels or []:
        if not isinstance(label, dict):
            continue
        text = str(label.get("text", "") or "").strip()
        if text and text not in texts:
            texts.append(text)
    return texts


class ApparatusSemanticsValidator:
    def validate(
        self,
        result: ApparatusSemanticsResult,
        *,
        figures_by_id: dict[str, FigureImageInput] | None = None,
        candidate_ids_by_figure: dict[str, set[str]] | None = None,
    ) -> list[ValidationIssue]:
        """Aggregate validation over the full result.

        ``figures_by_id`` / ``candidate_ids_by_figure`` are optional for
        backward compatibility with context-free callers.  The agent supplies
        both mappings so evidence and label-grounding warnings are preserved in
        the final artifact instead of being used only transiently by the repair
        loop (P4).
        """
        issues: list[ValidationIssue] = []
        for record in result.apparatus_records:
            issues += self.validate_record(
                record,
                figure=(figures_by_id or {}).get(record.figure_id),
                candidate_ids=(candidate_ids_by_figure or {}).get(record.figure_id),
            )
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

        if record.suggested_mode not in FIGURE_MODES:
            issues.append(ValidationIssue(
                rule_id="invalid_suggested_mode",
                severity="error",
                message=f"{fid} has invalid suggested_mode {record.suggested_mode!r}",
                field=f"{fid}.suggested_mode",
            ))
        if not isinstance(record.analysis_profile, dict):
            issues.append(ValidationIssue(
                rule_id="invalid_analysis_profile",
                severity="error",
                message=f"{fid} analysis_profile must be an object",
                field=f"{fid}.analysis_profile",
            ))
        elif record.suggested_mode == "functional_diagram":
            issues.extend(self._validate_functional_profile(record))

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

            # label_ref grounding checks (Phase 1 subtask C). Both require
            # figure context, so both are skipped entirely when figure=None —
            # same gating as the evidence-quote checks above.
            inner_label_texts = _inner_label_texts(figure)
            inner_label_set = set(inner_label_texts)
            inner_label_set_ci = {t.casefold() for t in inner_label_texts}
            covered_ci: set[str] = set()

            for idx, part in enumerate(record.parts):
                label_ref = (part.label_ref or "").strip()
                if not label_ref:
                    continue
                if not inner_label_texts:
                    # No inner_labels for this figure at all (e.g. a raster
                    # image with no PDF text layer) — do not error-loop the
                    # repair cycle over something the LLM cannot verify (P4).
                    issues.append(ValidationIssue(
                        rule_id="label_ref_without_inner_labels",
                        severity="warning",
                        message=(
                            f"{fid} part[{idx}] has label_ref {label_ref!r} but "
                            "figure.inner_labels is empty"
                        ),
                        field=f"{fid}.parts[{idx}].label_ref",
                    ))
                    continue
                if label_ref in inner_label_set:
                    covered_ci.add(label_ref.casefold())
                elif label_ref.casefold() in inner_label_set_ci:
                    covered_ci.add(label_ref.casefold())
                else:
                    issues.append(ValidationIssue(
                        rule_id="part_label_ref_not_in_inner_labels",
                        severity="error",
                        message=(
                            f"{fid} part[{idx}] label_ref {label_ref!r} does not match "
                            "any figure.inner_labels entry"
                        ),
                        field=f"{fid}.parts[{idx}].label_ref",
                    ))

            if inner_label_texts:
                uncovered = [t for t in inner_label_texts if t.casefold() not in covered_ci]
                if uncovered:
                    # Cannot tell apparatus components from parameter/annotation
                    # labels mechanically, so this stays a warning (kept for
                    # human review, P4) rather than an error.
                    shown = uncovered[:10]
                    message = (
                        f"{fid} has {len(uncovered)} in-figure label(s) not referenced "
                        f"by any part: {', '.join(shown)}"
                    )
                    if len(uncovered) > 10:
                        message += f" (+{len(uncovered) - 10} more)"
                    issues.append(ValidationIssue(
                        rule_id="inner_labels_not_covered",
                        severity="warning",
                        message=message,
                        field=f"{fid}.parts",
                    ))

            # Guided re-analysis (guided_figure_reanalysis_design.md §6-5).
            # Both are non-fatal warnings (the record is already
            # review_required regardless) — this flags a prompt-adherence
            # gap for human review rather than blocking delivery.
            guidance_present = _has_guidance_input(figure)
            guidance_note = (record.guidance_note or "").strip()
            if guidance_present and not guidance_note:
                issues.append(ValidationIssue(
                    rule_id="guidance_note_missing",
                    severity="warning",
                    message=(
                        f"{fid} received teacher guidance but guidance_note is empty"
                    ),
                    field=f"{fid}.guidance_note",
                ))
            elif not guidance_present and guidance_note:
                issues.append(ValidationIssue(
                    rule_id="guidance_note_unexpected",
                    severity="warning",
                    message=(
                        f"{fid} has no teacher guidance input but guidance_note is "
                        "non-empty (possible prompt contamination)"
                    ),
                    field=f"{fid}.guidance_note",
                ))

        return issues

    @staticmethod
    def _validate_functional_profile(record: ApparatusRecord) -> list[ValidationIssue]:
        """Validate function/port references without asserting visual guesses as facts."""
        issues: list[ValidationIssue] = []
        fid = record.figure_id
        functions = [
            item for item in (record.analysis_profile.get("functions") or [])
            if isinstance(item, dict)
        ]
        function_ids = [str(item.get("id") or "") for item in functions]
        if any(not value for value in function_ids) or len(function_ids) != len(set(function_ids)):
            issues.append(ValidationIssue(
                rule_id="functional_diagram_invalid_function_ids",
                severity="error",
                message=f"{fid} functional diagram has missing or duplicate function ids",
                field=f"{fid}.analysis_profile.functions",
            ))
            return issues

        by_id = {str(item.get("id")): item for item in functions}
        for index, connection in enumerate(record.analysis_profile.get("connections") or []):
            if not isinstance(connection, dict):
                continue
            source_id = str(connection.get("from_function_id") or "")
            target_id = str(connection.get("to_function_id") or "")
            output_id = str(connection.get("from_output_id") or "")
            input_id = str(connection.get("to_input_id") or "")
            if not source_id or not target_id or not output_id or not input_id:
                issues.append(ValidationIssue(
                    rule_id="functional_connection_missing_endpoint_id",
                    severity="error",
                    message=(
                        f"{fid} connection[{index}] must reference function and port ids "
                        "at both endpoints"
                    ),
                    field=f"{fid}.analysis_profile.connections[{index}]",
                ))
                continue
            if source_id not in by_id or target_id not in by_id:
                issues.append(ValidationIssue(
                    rule_id="functional_connection_unknown_function",
                    severity="error",
                    message=f"{fid} connection[{index}] references an unknown function",
                    field=f"{fid}.analysis_profile.connections[{index}]",
                ))
                continue
            output_ids = {
                str(port.get("id") or "") for port in (by_id[source_id].get("outputs") or [])
                if isinstance(port, dict)
            }
            input_ids = {
                str(port.get("id") or "") for port in (by_id[target_id].get("inputs") or [])
                if isinstance(port, dict)
            }
            if output_id and output_id not in output_ids:
                issues.append(ValidationIssue(
                    rule_id="functional_connection_unknown_output",
                    severity="error",
                    message=f"{fid} connection[{index}] references an unknown output port",
                    field=f"{fid}.analysis_profile.connections[{index}].from_output_id",
                ))
            if input_id and input_id not in input_ids:
                issues.append(ValidationIssue(
                    rule_id="functional_connection_unknown_input",
                    severity="error",
                    message=f"{fid} connection[{index}] references an unknown input port",
                    field=f"{fid}.analysis_profile.connections[{index}].to_input_id",
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
