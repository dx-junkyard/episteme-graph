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
    ALIGNMENT_SEVERITIES,
    ALIGNMENT_STATUSES,
    CONVERGENCE_STATUSES,
    HYPOTHESIS_STATUSES,
    MATCH_STATUSES,
    REVIEW_STATUSES,
    SOURCE_BACKING_STATUSES,
    AlignmentItem,
    ApparatusRecord,
    ApparatusSemanticsResult,
    ContextHypothesis,
    FigureImageInput,
    IterativeAnalysisRecord,
    ValidationIssue,
    VisualObservationSet,
)

# task_findings[].outcome vocabulary (verification step) — deliberately a
# subset of VERIFICATION_TASK_STATUSES (no "open": a finding always reports a
# concluded outcome, never a still-pending one).
_VERIFICATION_OUTCOMES = ("resolved", "refuted", "unresolved")

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


def _alignment_visual_support_is_traceable(
    item: AlignmentItem,
    *,
    observations: VisualObservationSet | None,
    figure: FigureImageInput | None,
) -> bool:
    """Whether ``item``'s claimed visual backing can actually be traced to a
    known observation or a real in-figure label, rather than resting on
    free-text ``visual_evidence`` alone (gap #3, contextual_figure_analysis_
    iterative_verification.md: a "ghost part" backed only by a descriptive
    sentence like "a box is visible" must not count as visual evidence).

    - ``observations`` provided: an ``observation_refs`` entry must resolve to
      a real ``observation_id``, OR ``label_ref`` must match a real
      ``figure.inner_labels`` entry (case-insensitive) when ``figure`` is
      known — degrades to a non-empty check when ``figure`` is ``None``
      (nothing to verify label_ref against).
    - ``observations`` is ``None``: degrades to a structural existence check
      (non-empty ``observation_refs`` or non-empty ``label_ref``) — there is
      no observation set to verify against either way.
    """
    obs_refs = [str(r) for r in (item.observation_refs or []) if str(r).strip()]
    label_ref = (item.label_ref or "").strip()

    if observations is None:
        return bool(obs_refs) or bool(label_ref)

    observation_id_set = {e.observation_id for e in observations.elements if e.observation_id}
    obs_backed = any(r in observation_id_set for r in obs_refs)
    if figure is not None:
        inner_label_ci = {t.casefold() for t in _inner_label_texts(figure)}
        label_backed = bool(label_ref) and (
            not inner_label_ci or label_ref.casefold() in inner_label_ci
        )
    else:
        label_backed = bool(label_ref)
    return obs_backed or label_backed


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

    # ------------------------------------------------------------------
    # Iterative contextual-analysis pipeline
    # (docs/features/contextual_figure_analysis_iterative_verification.md)
    #
    # Wave 1 scaffolding: these methods validate the intermediate/terminal
    # artifacts of the hypothesis -> observation -> alignment -> verification
    # loop. No caller in this wave invokes them yet — the state machine that
    # drives the loop lives in a later wave.
    # ------------------------------------------------------------------

    def validate_hypothesis(
        self,
        hypothesis: ContextHypothesis,
        *,
        figure: FigureImageInput | None = None,
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []

        if not (0.0 <= hypothesis.confidence <= 1.0):
            issues.append(ValidationIssue(
                rule_id="hypothesis_confidence_out_of_range",
                severity="error",
                message="context_hypothesis confidence is out of range",
                field="context_hypothesis.confidence",
            ))

        element_ids: list[str] = []
        for idx, element in enumerate(hypothesis.expected_elements):
            if not (0.0 <= element.confidence <= 1.0):
                issues.append(ValidationIssue(
                    rule_id="expected_element_confidence_out_of_range",
                    severity="error",
                    message=f"expected_elements[{idx}] confidence is out of range",
                    field=f"context_hypothesis.expected_elements[{idx}].confidence",
                ))
            if not element.element_id.strip():
                issues.append(ValidationIssue(
                    rule_id="expected_element_missing_id",
                    severity="error",
                    message=f"expected_elements[{idx}] has no element_id",
                    field=f"context_hypothesis.expected_elements[{idx}].element_id",
                ))
            else:
                element_ids.append(element.element_id)

        duplicate_ids = sorted({eid for eid in element_ids if element_ids.count(eid) > 1})
        if duplicate_ids:
            issues.append(ValidationIssue(
                rule_id="expected_element_duplicate_id",
                severity="error",
                message=f"expected_elements has duplicate element_id(s): {duplicate_ids}",
                field="context_hypothesis.expected_elements",
            ))

        if figure is not None:
            source_text = _normalize(_source_text(figure))
            for idx, element in enumerate(hypothesis.expected_elements):
                if element.evidence_quote and _normalize(element.evidence_quote) not in source_text:
                    issues.append(ValidationIssue(
                        rule_id="expected_element_evidence_not_verbatim",
                        severity="warning",
                        message=(
                            f"expected_elements[{idx}] evidence_quote does not appear "
                            "verbatim in caption/nearby text"
                        ),
                        field=f"context_hypothesis.expected_elements[{idx}].evidence_quote",
                    ))

        element_id_set = set(element_ids)
        for idx, relation in enumerate(hypothesis.expected_relations):
            if not (0.0 <= relation.confidence <= 1.0):
                issues.append(ValidationIssue(
                    rule_id="expected_relation_confidence_out_of_range",
                    severity="error",
                    message=f"expected_relations[{idx}] confidence is out of range",
                    field=f"context_hypothesis.expected_relations[{idx}].confidence",
                ))
            if element_id_set and (
                relation.from_element_id not in element_id_set
                or relation.to_element_id not in element_id_set
            ):
                issues.append(ValidationIssue(
                    rule_id="expected_relation_unknown_element",
                    severity="warning",
                    message=(
                        f"expected_relations[{idx}] references an element_id not present "
                        "in expected_elements"
                    ),
                    field=f"context_hypothesis.expected_relations[{idx}]",
                ))

        return issues

    def validate_observations(
        self, observations: VisualObservationSet,
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []

        if not (0.0 <= observations.confidence <= 1.0):
            issues.append(ValidationIssue(
                rule_id="observations_confidence_out_of_range",
                severity="error",
                message="visual_observations confidence is out of range",
                field="visual_observations.confidence",
            ))

        if observations.visual_mode_guess not in FIGURE_MODES:
            issues.append(ValidationIssue(
                rule_id="invalid_visual_mode_guess",
                severity="error",
                message=(
                    f"visual_observations has invalid visual_mode_guess "
                    f"{observations.visual_mode_guess!r}"
                ),
                field="visual_observations.visual_mode_guess",
            ))

        observation_ids: list[str] = []
        for idx, element in enumerate(observations.elements):
            if not element.observation_id.strip():
                issues.append(ValidationIssue(
                    rule_id="observed_element_missing_id",
                    severity="error",
                    message=f"elements[{idx}] has no observation_id",
                    field=f"visual_observations.elements[{idx}].observation_id",
                ))
            else:
                observation_ids.append(element.observation_id)

        duplicate_ids = sorted({oid for oid in observation_ids if observation_ids.count(oid) > 1})
        if duplicate_ids:
            issues.append(ValidationIssue(
                rule_id="observed_element_duplicate_id",
                severity="error",
                message=f"elements has duplicate observation_id(s): {duplicate_ids}",
                field="visual_observations.elements",
            ))

        observation_id_set = set(observation_ids)
        for idx, connection in enumerate(observations.connections):
            if observation_id_set and (
                connection.from_observation_id not in observation_id_set
                or connection.to_observation_id not in observation_id_set
            ):
                issues.append(ValidationIssue(
                    rule_id="observed_connection_unknown_observation",
                    severity="warning",
                    message=(
                        f"connections[{idx}] references an observation_id not present "
                        "in elements"
                    ),
                    field=f"visual_observations.connections[{idx}]",
                ))

        return issues

    def validate_alignment(
        self,
        record: ApparatusRecord,
        iterative_parts: dict,
        *,
        figure: FigureImageInput | None = None,
        candidate_ids: set[str] | None = None,
        observations: VisualObservationSet | None = None,
    ) -> list[ValidationIssue]:
        """Validate the alignment step's output: the final ``ApparatusRecord``
        (via ``validate_record``, unchanged) plus the new alignment-only
        pieces, and — the key structural guardrail — that every ``parts[]``
        entry has a supporting alignment item (``part_without_visual_support``).
        """
        issues = list(self.validate_record(record, figure=figure, candidate_ids=candidate_ids))

        iterative_parts = iterative_parts if isinstance(iterative_parts, dict) else {}
        alignment_items = list(iterative_parts.get("alignment_items") or [])
        alternative_hypotheses = list(iterative_parts.get("alternative_hypotheses") or [])
        verification_tasks = list(iterative_parts.get("verification_tasks") or [])

        inner_label_texts = _inner_label_texts(figure) if figure is not None else []
        inner_label_set_ci = {t.casefold() for t in inner_label_texts}

        observation_id_set: set[str] | None = None
        if observations is not None:
            observation_id_set = {e.observation_id for e in observations.elements if e.observation_id}

        source_text = _normalize(_source_text(figure)) if figure is not None else None

        fid = record.figure_id
        supporting_items_by_label: dict[str, str] = {}
        supporting_items_by_name: dict[str, str] = {}

        for idx, item in enumerate(alignment_items):
            prefix = f"{fid}.alignment_items[{idx}]"

            if item.status not in ALIGNMENT_STATUSES:
                issues.append(ValidationIssue(
                    rule_id="invalid_alignment_status",
                    severity="error",
                    message=f"{prefix} has invalid status {item.status!r}",
                    field=f"{prefix}.status",
                ))

            if item.severity not in ALIGNMENT_SEVERITIES:
                issues.append(ValidationIssue(
                    rule_id="invalid_alignment_severity",
                    severity="warning",
                    message=f"{prefix} has invalid severity {item.severity!r}",
                    field=f"{prefix}.severity",
                ))

            has_visual_refs = bool(item.observation_refs) or bool((item.label_ref or "").strip())
            if item.status == "text_only" and has_visual_refs:
                issues.append(ValidationIssue(
                    rule_id="text_only_with_visual_refs",
                    severity="error",
                    message=(
                        f"{prefix} is status='text_only' but carries "
                        "observation_refs/label_ref"
                    ),
                    field=f"{prefix}.status",
                ))

            if (
                item.status in ("text_only", "contradicted", "supported_by_both")
                and not item.text_evidence.strip()
            ):
                issues.append(ValidationIssue(
                    rule_id="alignment_missing_text_evidence",
                    severity="error",
                    message=(
                        f"{prefix} status={item.status!r} requires non-empty text_evidence"
                    ),
                    field=f"{prefix}.text_evidence",
                ))

            if item.status in ("visual_only", "supported_by_both", "contradicted") and not (
                item.observation_refs or (item.label_ref or "").strip() or item.visual_evidence.strip()
            ):
                issues.append(ValidationIssue(
                    rule_id="alignment_missing_visual_evidence",
                    severity="error",
                    message=(
                        f"{prefix} status={item.status!r} requires observation_refs, "
                        "label_ref, or visual_evidence"
                    ),
                    field=prefix,
                ))

            # Hard guardrail (gap #3): free-text visual_evidence alone must
            # never count as "visual support" for visual_only/supported_by_both
            # — it must be traceable to a real observation_id or a real
            # in-figure label. "contradicted" is exempt: a contradiction can
            # legitimately be "the image looks different from what the text
            # says" without pinning an observation_id.
            if item.status in ("visual_only", "supported_by_both") and not (
                _alignment_visual_support_is_traceable(item, observations=observations, figure=figure)
            ):
                issues.append(ValidationIssue(
                    rule_id="alignment_visual_support_untraceable",
                    severity="error",
                    message=(
                        f"{prefix} status={item.status!r} has no traceable visual evidence "
                        "— observation_refs must resolve to a known observation_id, or "
                        "label_ref must match a real figure.inner_labels entry; a free-text "
                        "visual_evidence description alone is not sufficient"
                    ),
                    field=prefix,
                ))

            if observation_id_set is not None:
                unknown_refs = [
                    ref for ref in item.observation_refs if ref not in observation_id_set
                ]
                if unknown_refs:
                    issues.append(ValidationIssue(
                        rule_id="alignment_unknown_observation_ref",
                        severity="warning",
                        message=f"{prefix} references unknown observation_ref(s): {unknown_refs}",
                        field=f"{prefix}.observation_refs",
                    ))

            label_ref = (item.label_ref or "").strip()
            if label_ref and figure is not None:
                if not inner_label_texts:
                    issues.append(ValidationIssue(
                        rule_id="alignment_label_ref_not_in_inner_labels",
                        severity="error",
                        message=(
                            f"{prefix} has label_ref {label_ref!r} but "
                            "figure.inner_labels is empty"
                        ),
                        field=f"{prefix}.label_ref",
                    ))
                elif label_ref.casefold() not in inner_label_set_ci:
                    issues.append(ValidationIssue(
                        rule_id="alignment_label_ref_not_in_inner_labels",
                        severity="error",
                        message=(
                            f"{prefix} label_ref {label_ref!r} does not match any "
                            "figure.inner_labels entry"
                        ),
                        field=f"{prefix}.label_ref",
                    ))

            if (
                source_text is not None
                and item.text_evidence
                and _normalize(item.text_evidence) not in source_text
            ):
                issues.append(ValidationIssue(
                    rule_id="alignment_text_evidence_not_verbatim",
                    severity="warning",
                    message=(
                        f"{prefix} text_evidence does not appear verbatim in "
                        "caption/nearby text"
                    ),
                    field=f"{prefix}.text_evidence",
                ))

            if item.status in ("supported_by_both", "visual_only"):
                if label_ref:
                    supporting_items_by_label[label_ref.casefold()] = item.status
                if item.label.strip():
                    supporting_items_by_name[item.label.strip().casefold()] = item.status

        for idx, part in enumerate(record.parts):
            part_label_ref = (part.label_ref or "").strip().casefold()
            part_name = (part.name or "").strip().casefold()
            supported = (
                bool(part_label_ref) and part_label_ref in supporting_items_by_label
            ) or (
                bool(part_name) and part_name in supporting_items_by_name
            )
            if not supported:
                issues.append(ValidationIssue(
                    rule_id="part_without_visual_support",
                    severity="error",
                    message=(
                        f"{fid} part[{idx}] {part.name!r} has no supported_by_both/"
                        "visual_only alignment item backing it"
                    ),
                    field=f"{fid}.parts[{idx}]",
                ))

        for idx, task in enumerate(verification_tasks):
            if not (
                task.question.strip()
                and task.success_condition.strip()
                and task.refutation_condition.strip()
            ):
                issues.append(ValidationIssue(
                    rule_id="verification_task_incomplete",
                    severity="warning",
                    message=(
                        f"verification_tasks[{idx}] is missing question/"
                        "success_condition/refutation_condition"
                    ),
                    field=f"verification_tasks[{idx}]",
                ))

        for idx, hyp in enumerate(alternative_hypotheses):
            if hyp.status not in HYPOTHESIS_STATUSES:
                issues.append(ValidationIssue(
                    rule_id="invalid_hypothesis_status",
                    severity="error",
                    message=f"alternative_hypotheses[{idx}] has invalid status {hyp.status!r}",
                    field=f"alternative_hypotheses[{idx}].status",
                ))

        return issues

    def validate_verification_output(
        self,
        parsed: dict,
        *,
        known_task_ids: set[str] | None = None,
        observations: VisualObservationSet | None = None,
        figure: FigureImageInput | None = None,
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        parsed = parsed if isinstance(parsed, dict) else {}

        for idx, finding in enumerate(parsed.get("task_findings") or []):
            if not isinstance(finding, dict):
                continue
            task_id = str(finding.get("task_id", "") or "")
            outcome = str(finding.get("outcome", "") or "")
            if known_task_ids is not None and task_id not in known_task_ids:
                issues.append(ValidationIssue(
                    rule_id="verification_unknown_task",
                    severity="warning",
                    message=f"task_findings[{idx}] references unknown task_id {task_id!r}",
                    field=f"task_findings[{idx}].task_id",
                ))
            if outcome not in _VERIFICATION_OUTCOMES:
                issues.append(ValidationIssue(
                    rule_id="invalid_verification_outcome",
                    severity="error",
                    message=f"task_findings[{idx}] has invalid outcome {outcome!r}",
                    field=f"task_findings[{idx}].outcome",
                ))

        inner_label_texts = _inner_label_texts(figure) if figure is not None else []
        inner_label_set_ci = {t.casefold() for t in inner_label_texts}
        observation_id_set: set[str] | None = None
        if observations is not None:
            observation_id_set = {
                e.observation_id for e in observations.elements if e.observation_id
            }

        record_deltas = parsed.get("record_deltas")
        record_deltas = record_deltas if isinstance(record_deltas, dict) else {}
        for idx, entry in enumerate(record_deltas.get("parts_to_add") or []):
            if not isinstance(entry, dict):
                continue
            observation_refs = [
                str(r) for r in (entry.get("observation_refs") or []) if str(r).strip()
            ]
            label_ref = str(entry.get("label_ref", "") or "").strip()

            has_observation_backing = bool(observation_refs) and (
                observation_id_set is None
                or any(ref in observation_id_set for ref in observation_refs)
            )
            has_label_backing = bool(label_ref) and (
                not inner_label_texts or label_ref.casefold() in inner_label_set_ci
            )
            if not (has_observation_backing or has_label_backing):
                issues.append(ValidationIssue(
                    rule_id="delta_part_without_visual_support",
                    severity="error",
                    message=(
                        f"record_deltas.parts_to_add[{idx}] has no observation_refs "
                        "or label_ref grounding it"
                    ),
                    field=f"record_deltas.parts_to_add[{idx}]",
                ))

        return issues

    def validate_iterative_record(
        self, ia: IterativeAnalysisRecord,
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []

        if ia.convergence_status not in CONVERGENCE_STATUSES:
            issues.append(ValidationIssue(
                rule_id="invalid_convergence_status",
                severity="error",
                message=(
                    f"iterative_analysis has invalid convergence_status "
                    f"{ia.convergence_status!r}"
                ),
                field="iterative_analysis.convergence_status",
            ))

        if (
            ia.convergence_status != "converged"
            and not ia.review_questions
            and not ia.unresolved_conflicts
        ):
            issues.append(ValidationIssue(
                rule_id="nonconverged_without_review_questions",
                severity="warning",
                message=(
                    "iterative_analysis did not converge but has neither "
                    "review_questions nor unresolved_conflicts"
                ),
                field="iterative_analysis",
            ))

        for idx, iteration in enumerate(ia.verification_iterations):
            if not iteration.executed_task_ids:
                issues.append(ValidationIssue(
                    rule_id="iteration_without_tasks",
                    severity="error",
                    message=f"verification_iterations[{idx}] has no executed_task_ids",
                    field=f"iterative_analysis.verification_iterations[{idx}].executed_task_ids",
                ))

        return issues
