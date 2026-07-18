"""Repair helpers for ApparatusSemanticsAgent.

``_parse_record`` deterministically builds an ``ApparatusRecord`` from raw LLM
JSON: the LLM is trusted only for identification-level fields (name candidate /
match status / parts / connections / evidence / reason / confidence).
``source_backing_status`` and ``review_status`` are always derived here, never
taken from the LLM (design principle #2/#4 — the model never gets to assert
its own confirmation status).

``ApparatusSemanticsRepairer`` re-prompts the LLM up to
``_MAX_REPAIR_ATTEMPTS`` times when validation finds hard errors. If repair is
exhausted, the figure is kept as a reviewable ``unknown`` record with
``repair_failed=True`` rather than being dropped (P4, "情報を落とさない").
"""
from __future__ import annotations

import logging
import re

from episteme_graph.agents.figure_modes import analysis_profile_for_record

from .llm_client import ApparatusSemanticsLLMClient
from .prompt import ApparatusSemanticsPromptFactory
from .schema import (
    REVIEW_STATUS_DEFAULT,
    AlignmentItem,
    AlternativeHypothesis,
    ApparatusConnection,
    ApparatusPart,
    ApparatusRecord,
    ContextHypothesis,
    ExpectedElement,
    ExpectedRelation,
    FigureImageInput,
    LibraryCandidate,
    ObservedConnection,
    ObservedElement,
    ValidationIssue,
    VerificationTask,
    VisualObservationSet,
)

logger = logging.getLogger(__name__)

_MAX_REPAIR_ATTEMPTS = 2
_WHITESPACE_RE = re.compile(r"\s+")

# Mirrors input_builder._MAX_GUIDANCE_TEXT_CHARS — guidance_note is an LLM
# output field (not a re-echo of the teacher's hint), but is capped the same
# way defensively so a runaway response cannot bloat the stored artifact.
_MAX_GUIDANCE_NOTE_CHARS = 2000


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return max(0.0, min(1.0, float(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _normalize(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", (text or "").strip().lower())


def _source_text(figure: FigureImageInput) -> str:
    parts = [figure.caption_text or ""]
    parts.extend(figure.nearby_text or [])
    return " ".join(parts)


def _parse_record(
    raw: dict,
    figure: FigureImageInput,
    candidates: list[LibraryCandidate],
) -> ApparatusRecord:
    """Deterministically build an ``ApparatusRecord`` from raw LLM JSON."""
    raw = raw if isinstance(raw, dict) else {}

    match_status = str(raw.get("match_status") or "unknown").strip()

    parts: list[ApparatusPart] = []
    for p in raw.get("parts", []) or []:
        if not isinstance(p, dict):
            continue
        # label_ref is the only new field trusted from the LLM here (it names
        # which in-figure label the part refers to). bbox / expanded_name are
        # intentionally NOT read from raw LLM output — they are spatial /
        # dictionary-lookup facts, deterministically attached downstream by
        # agent.py::_attach_label_grounding (design principle #2/#4).
        label_ref = str(p.get("label_ref") or "").strip() or None
        parts.append(ApparatusPart(
            name=str(p.get("name", "") or ""),
            role=str(p.get("role", "") or ""),
            label_ref=label_ref,
            evidence_quote=str(p.get("evidence_quote", "") or ""),
            reason=str(p.get("reason", "") or ""),
            confidence=_safe_float(p.get("confidence", 0.0)),
        ))

    connections: list[ApparatusConnection] = []
    for c in raw.get("connections", []) or []:
        if not isinstance(c, dict):
            continue
        connections.append(ApparatusConnection(
            from_part=str(c.get("from_part", "") or ""),
            to_part=str(c.get("to_part", "") or ""),
            relation=str(c.get("relation", "") or ""),
            reason=str(c.get("reason", "") or ""),
            confidence=_safe_float(c.get("confidence", 0.0)),
        ))

    evidence_quote = str(raw.get("evidence_quote", "") or "")
    source_text = _normalize(_source_text(figure))
    # source_backing_status is derived, never trusted from the LLM (#2/#4):
    # a verbatim caption/nearby-text quote earns 'partially_source_backed',
    # otherwise the record is purely 'inferred'. 'source_backed' is never
    # reachable from this function.
    source_backing_status = (
        "partially_source_backed"
        if evidence_quote and _normalize(evidence_quote) in source_text
        else "inferred"
    )

    matched_entry_id = raw.get("matched_library_entry_id") or None
    matched_version_no = None
    if matched_entry_id:
        matched_version_no = next(
            (c.version_no for c in candidates if c.entry_id == matched_entry_id),
            raw.get("matched_library_version_no"),
        )

    mode_payload = analysis_profile_for_record(
        {
            **raw,
            "parts": [
                {
                    "name": part.name,
                    "role": part.role,
                    "label_ref": part.label_ref,
                    "evidence_quote": part.evidence_quote,
                    "reason": part.reason,
                    "confidence": part.confidence,
                }
                for part in parts
            ],
            "connections": [
                {
                    "from_part": connection.from_part,
                    "to_part": connection.to_part,
                    "relation": connection.relation,
                    "reason": connection.reason,
                    "confidence": connection.confidence,
                }
                for connection in connections
            ],
            "figure_record": figure.figure_record or {},
        },
        caption_text=figure.caption_text,
    )

    raw_suggested_mode = str(
        raw.get("suggested_mode") or raw.get("figure_mode_candidate") or ""
    ).strip()

    return ApparatusRecord(
        figure_id=figure.figure_id,
        figure_key=figure.figure_key,
        apparatus_name_candidate=str(raw.get("apparatus_name_candidate", "") or ""),
        matched_library_entry_id=matched_entry_id,
        matched_library_version_no=matched_version_no,
        match_status=match_status,
        parts=parts,
        connections=connections,
        evidence_quote=evidence_quote,
        reason=str(raw.get("reason", "") or ""),
        confidence=_safe_float(raw.get("confidence", 0.0)),
        source_backing_status=source_backing_status,
        review_status=REVIEW_STATUS_DEFAULT,
        repair_failed=False,
        suggested_mode=raw_suggested_mode or mode_payload["suggested_mode"],
        mode_reason=mode_payload["mode_reason"],
        analysis_profile=mode_payload["analysis_profile"],
        guidance_note=str(raw.get("guidance_note", "") or "").strip()[:_MAX_GUIDANCE_NOTE_CHARS],
    )


# ---------------------------------------------------------------------------
# Iterative contextual-analysis pipeline parsing
# (docs/features/contextual_figure_analysis_iterative_verification.md)
#
# These functions deterministically build the Wave 1 dataclasses from raw LLM
# JSON for each of the four pipeline steps. Like ``_parse_record`` above, they
# are defensive (type mismatches degrade to defaults, confidence is clamped to
# 0..1) but deliberately do NOT enforce the controlled vocabularies
# (status/severity/etc.) — an out-of-vocabulary value is kept as-is so
# validator.py can report it as an explicit issue rather than silently
# discarding it (design principle #4, "情報を落とさない"). No caller in this
# wave invokes these yet — the state machine lives in a later wave.
# ---------------------------------------------------------------------------

def parse_hypothesis(raw: dict) -> ContextHypothesis:
    """Deterministically build a ``ContextHypothesis`` from raw LLM JSON."""
    raw = raw if isinstance(raw, dict) else {}

    expected_elements: list[ExpectedElement] = []
    for item in raw.get("expected_elements", []) or []:
        if not isinstance(item, dict):
            continue
        expected_elements.append(ExpectedElement(
            element_id=str(item.get("element_id", "") or ""),
            name=str(item.get("name", "") or ""),
            evidence_quote=str(item.get("evidence_quote", "") or ""),
            expected_labels=[str(x) for x in (item.get("expected_labels") or [])],
            importance=str(item.get("importance", "primary") or "primary"),
            confidence=_safe_float(item.get("confidence", 0.0)),
        ))

    expected_relations: list[ExpectedRelation] = []
    for item in raw.get("expected_relations", []) or []:
        if not isinstance(item, dict):
            continue
        expected_relations.append(ExpectedRelation(
            relation_id=str(item.get("relation_id", "") or ""),
            from_element_id=str(item.get("from_element_id", "") or ""),
            to_element_id=str(item.get("to_element_id", "") or ""),
            relation=str(item.get("relation", "") or ""),
            direction=str(item.get("direction", "") or ""),
            evidence_quote=str(item.get("evidence_quote", "") or ""),
            confidence=_safe_float(item.get("confidence", 0.0)),
        ))

    return ContextHypothesis(
        role_in_paper=str(raw.get("role_in_paper", "") or ""),
        overall_subject=str(raw.get("overall_subject", "") or ""),
        expected_elements=expected_elements,
        expected_relations=expected_relations,
        expected_visual_cues=[str(x) for x in (raw.get("expected_visual_cues") or [])],
        unstated_points=[str(x) for x in (raw.get("unstated_points") or [])],
        falsification_conditions=[str(x) for x in (raw.get("falsification_conditions") or [])],
        evidence_quotes=[str(x) for x in (raw.get("evidence_quotes") or [])],
        reason=str(raw.get("reason", "") or ""),
        confidence=_safe_float(raw.get("confidence", 0.0)),
    )


def parse_observations(raw: dict) -> VisualObservationSet:
    """Deterministically build a ``VisualObservationSet`` from raw LLM JSON."""
    raw = raw if isinstance(raw, dict) else {}

    elements: list[ObservedElement] = []
    for item in raw.get("elements", []) or []:
        if not isinstance(item, dict):
            continue
        elements.append(ObservedElement(
            observation_id=str(item.get("observation_id", "") or ""),
            kind=str(item.get("kind", "other") or "other"),
            description=str(item.get("description", "") or ""),
            label_text=str(item.get("label_text", "") or ""),
            region_hint=str(item.get("region_hint", "") or ""),
        ))

    connections: list[ObservedConnection] = []
    for item in raw.get("connections", []) or []:
        if not isinstance(item, dict):
            continue
        connections.append(ObservedConnection(
            from_observation_id=str(item.get("from_observation_id", "") or ""),
            to_observation_id=str(item.get("to_observation_id", "") or ""),
            connector=str(item.get("connector", "") or ""),
            direction=str(item.get("direction", "") or ""),
            description=str(item.get("description", "") or ""),
        ))

    return VisualObservationSet(
        panels=[dict(p) for p in (raw.get("panels") or []) if isinstance(p, dict)],
        elements=elements,
        connections=connections,
        ocr_labels=[str(x) for x in (raw.get("ocr_labels") or [])],
        repeated_motifs=[dict(m) for m in (raw.get("repeated_motifs") or []) if isinstance(m, dict)],
        unreadable_regions=[
            dict(r) for r in (raw.get("unreadable_regions") or []) if isinstance(r, dict)
        ],
        undecidable_elements=[
            dict(u) for u in (raw.get("undecidable_elements") or []) if isinstance(u, dict)
        ],
        visual_mode_guess=str(raw.get("visual_mode_guess", "unknown") or "unknown"),
        reason=str(raw.get("reason", "") or ""),
        confidence=_safe_float(raw.get("confidence", 0.0)),
    )


def _parse_alignment_item(item: dict) -> AlignmentItem:
    item = item if isinstance(item, dict) else {}
    return AlignmentItem(
        item_id=str(item.get("item_id", "") or ""),
        item_kind=str(item.get("item_kind", "") or ""),
        label=str(item.get("label", "") or ""),
        # status/severity are intentionally NOT clamped to their controlled
        # vocabulary here — an out-of-vocabulary value is kept as-is so
        # validator.py can report it explicitly (design principle #4).
        status=str(item.get("status", "") or ""),
        expected_ref=str(item.get("expected_ref", "") or ""),
        observation_refs=[str(x) for x in (item.get("observation_refs") or [])],
        label_ref=str(item.get("label_ref", "") or ""),
        text_evidence=str(item.get("text_evidence", "") or ""),
        visual_evidence=str(item.get("visual_evidence", "") or ""),
        severity=str(item.get("severity", "medium") or "medium"),
        reason=str(item.get("reason", "") or ""),
        confidence=_safe_float(item.get("confidence", 0.0)),
    )


def _parse_alternative_hypothesis(item: dict) -> AlternativeHypothesis:
    item = item if isinstance(item, dict) else {}
    return AlternativeHypothesis(
        hypothesis_id=str(item.get("hypothesis_id", "") or ""),
        description=str(item.get("description", "") or ""),
        supporting_evidence=[str(x) for x in (item.get("supporting_evidence") or [])],
        counter_evidence=[str(x) for x in (item.get("counter_evidence") or [])],
        unverified_conditions=[str(x) for x in (item.get("unverified_conditions") or [])],
        status=str(item.get("status", "active") or "active"),
        confidence=_safe_float(item.get("confidence", 0.0)),
    )


def _parse_verification_task(item: dict) -> VerificationTask:
    item = item if isinstance(item, dict) else {}
    focus_bbox_rel = item.get("focus_bbox_rel")
    return VerificationTask(
        task_id=str(item.get("task_id", "") or ""),
        question=str(item.get("question", "") or ""),
        target_item_ids=[str(x) for x in (item.get("target_item_ids") or [])],
        region_hint=str(item.get("region_hint", "") or ""),
        focus_bbox_rel=(
            list(focus_bbox_rel) if isinstance(focus_bbox_rel, list) and focus_bbox_rel else None
        ),
        success_condition=str(item.get("success_condition", "") or ""),
        refutation_condition=str(item.get("refutation_condition", "") or ""),
        status=str(item.get("status", "open") or "open"),
    )


def parse_alignment_output(
    raw: dict,
    figure: FigureImageInput,
    candidates: list[LibraryCandidate],
) -> tuple[ApparatusRecord, dict]:
    """Split raw alignment-step LLM JSON into the final ``ApparatusRecord``
    (built the same way ``_parse_record`` builds the one-shot record — reused
    directly here, design principle #2/#4 discipline unchanged) plus the new
    iterative-only pieces (alignment_items / alternative_hypotheses /
    verification_tasks / review_questions).
    """
    raw = raw if isinstance(raw, dict) else {}
    record = _parse_record(raw, figure, candidates)

    alignment_items = [
        _parse_alignment_item(item) for item in raw.get("alignment_items", []) or []
        if isinstance(item, dict)
    ]
    alternative_hypotheses = [
        _parse_alternative_hypothesis(item)
        for item in raw.get("alternative_hypotheses", []) or []
        if isinstance(item, dict)
    ]
    verification_tasks = [
        _parse_verification_task(item) for item in raw.get("verification_tasks", []) or []
        if isinstance(item, dict)
    ]
    review_questions = [
        dict(item) for item in raw.get("review_questions", []) or [] if isinstance(item, dict)
    ]

    return record, {
        "alignment_items": alignment_items,
        "alternative_hypotheses": alternative_hypotheses,
        "verification_tasks": verification_tasks,
        "review_questions": review_questions,
    }


def parse_verification_output(raw: dict) -> dict:
    """Deterministically parse a gap-driven re-verification response.

    ``task_findings`` / ``hypothesis_updates`` / ``record_deltas`` are kept as
    plain (defensively-typed) dicts — they describe deltas against state this
    module does not own, so validator.py/the (later-wave) engine interpret
    them against the current alignment/observation state.
    """
    raw = raw if isinstance(raw, dict) else {}

    task_findings = [
        dict(item) for item in raw.get("task_findings", []) or [] if isinstance(item, dict)
    ]
    updated_alignment_items = [
        _parse_alignment_item(item) for item in raw.get("updated_alignment_items", []) or []
        if isinstance(item, dict)
    ]
    new_alignment_items = [
        _parse_alignment_item(item) for item in raw.get("new_alignment_items", []) or []
        if isinstance(item, dict)
    ]
    new_verification_tasks = [
        _parse_verification_task(item) for item in raw.get("new_verification_tasks", []) or []
        if isinstance(item, dict)
    ]
    hypothesis_updates = [
        dict(item) for item in raw.get("hypothesis_updates", []) or [] if isinstance(item, dict)
    ]
    record_deltas = raw.get("record_deltas")
    record_deltas = dict(record_deltas) if isinstance(record_deltas, dict) else {}

    return {
        "task_findings": task_findings,
        "updated_alignment_items": updated_alignment_items,
        "new_alignment_items": new_alignment_items,
        "new_verification_tasks": new_verification_tasks,
        "hypothesis_updates": hypothesis_updates,
        "record_deltas": record_deltas,
        "notes": str(raw.get("notes", "") or ""),
    }


def _fallback_record(
    figure: FigureImageInput,
    reason: str,
    *,
    repair_failed: bool = False,
) -> ApparatusRecord:
    """A minimal reviewable record for a figure the agent could not process
    (no image / LLM call failed / repair exhausted). Never dropped (P4).

    ``guidance_note`` is left at its dataclass default ("") — there is no LLM
    output to draw one from on this path, guided or not.
    """
    return ApparatusRecord(
        figure_id=figure.figure_id,
        figure_key=figure.figure_key,
        apparatus_name_candidate="",
        matched_library_entry_id=None,
        matched_library_version_no=None,
        match_status="unknown",
        parts=[],
        connections=[],
        evidence_quote="",
        reason=reason,
        confidence=0.0,
        source_backing_status="inferred",
        review_status=REVIEW_STATUS_DEFAULT,
        repair_failed=repair_failed,
        suggested_mode="unknown",
        mode_reason=reason,
        analysis_profile={"summary": "", "reason": reason},
    )


class ApparatusSemanticsRepairer:
    def repair(
        self,
        *,
        figure: FigureImageInput,
        candidates: list[LibraryCandidate],
        candidate_briefs: list[dict],
        nearby_text: list[str],
        cartridge_hints: dict,
        raw_output: dict,
        validation_issues: list[ValidationIssue],
        llm_client: ApparatusSemanticsLLMClient,
        prompt_factory: ApparatusSemanticsPromptFactory,
        validator: object,
        image_payloads: list[dict],
        inner_label_hints: list[str] | None = None,
        abbreviations: dict | None = None,
        guidance: dict | None = None,
    ) -> ApparatusRecord:
        candidate_ids = {c.entry_id for c in candidates}
        for attempt in range(1, _MAX_REPAIR_ATTEMPTS + 1):
            logger.info(
                "apparatus_semantics repair attempt %d/%d figure=%s",
                attempt, _MAX_REPAIR_ATTEMPTS, figure.figure_id,
            )
            messages = prompt_factory.build_repair_messages(
                figure, candidate_briefs, nearby_text, cartridge_hints,
                raw_output, validation_issues,
                inner_label_hints=inner_label_hints, abbreviations=abbreviations,
                guidance=guidance,
            )
            try:
                raw_output = llm_client.generate(messages, images=image_payloads)
            except Exception as exc:
                logger.warning(
                    "apparatus_semantics repair LLM call failed figure=%s: %s",
                    figure.figure_id, exc,
                )
                break

            record = _parse_record(raw_output, figure, candidates)
            remaining = validator.validate_record(  # type: ignore[attr-defined]
                record, figure=figure, candidate_ids=candidate_ids,
            )
            if not [i for i in remaining if i.severity == "error"]:
                return record
            validation_issues = remaining

        return _fallback_record(
            figure, "repair_failed_after_max_attempts", repair_failed=True,
        )
