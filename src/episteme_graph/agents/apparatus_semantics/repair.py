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

from .llm_client import ApparatusSemanticsLLMClient
from .prompt import ApparatusSemanticsPromptFactory
from .schema import (
    REVIEW_STATUS_DEFAULT,
    ApparatusConnection,
    ApparatusPart,
    ApparatusRecord,
    FigureImageInput,
    LibraryCandidate,
    ValidationIssue,
)

logger = logging.getLogger(__name__)

_MAX_REPAIR_ATTEMPTS = 2
_WHITESPACE_RE = re.compile(r"\s+")


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
    )


def _fallback_record(
    figure: FigureImageInput,
    reason: str,
    *,
    repair_failed: bool = False,
) -> ApparatusRecord:
    """A minimal reviewable record for a figure the agent could not process
    (no image / LLM call failed / repair exhausted). Never dropped (P4)."""
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
        image_payload: dict,
        inner_label_hints: list[str] | None = None,
        abbreviations: dict | None = None,
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
            )
            try:
                raw_output = llm_client.generate(messages, images=[image_payload])
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
