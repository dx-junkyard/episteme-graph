"""Repair helpers for EquationSemanticsAgent.

Issue #245: EquationRecord (新スキーマ) に対応。
"""
from __future__ import annotations

import logging

from .llm_client import EquationSemanticsLLMClient
from .prompt import EquationSemanticsPromptFactory
from .schema import (
    CartridgeContext,
    DefinedSymbol,
    EquationCandidate,
    EquationConfidencePolicy,
    EquationLLMInput,
    EquationRecord,
    EquationReconstruction,
    EquationSemantics,
    EquationSemanticsResult,
    EquationSourceExtraction,
    ValidationIssue,
)

logger = logging.getLogger(__name__)

_MAX_REPAIR_ATTEMPTS = 2


class EquationSemanticsRepairer:
    def repair(
        self,
        llm_input: EquationLLMInput,
        candidate: EquationCandidate | None,
        raw_output: dict,
        validation_issues: list[ValidationIssue],
        cartridge: CartridgeContext | None,
        llm_client: EquationSemanticsLLMClient,
        prompt_factory: EquationSemanticsPromptFactory,
        validator: object,
    ) -> EquationRecord:
        for attempt in range(1, _MAX_REPAIR_ATTEMPTS + 1):
            logger.info("Equation semantics repair attempt %d/%d", attempt, _MAX_REPAIR_ATTEMPTS)
            messages = prompt_factory.build_repair_messages(
                llm_input, raw_output, validation_issues, cartridge
            )
            try:
                raw_output = llm_client.generate(messages)
            except Exception as exc:
                logger.warning("Repair LLM call failed: %s", exc)
                break

            record = _parse_record(raw_output, llm_input, candidate)
            partial = EquationSemanticsResult(
                document_id=llm_input.document_id,
                cartridge_id=llm_input.cartridge_id,
                equation_candidates=[],
                equations=[record],
            )
            remaining = validator.validate(partial, cartridge)  # type: ignore[attr-defined]
            if not [i for i in remaining if i.severity == "error"]:
                return record
            validation_issues = remaining

        return _fallback_record(llm_input, candidate, "Repair failed after max attempts")


def _parse_record(
    raw: dict,
    llm_input: EquationLLMInput,
    candidate: EquationCandidate | None,
) -> EquationRecord:
    """LLM 生出力 + llm_input + candidate から EquationRecord を構築する。"""
    # --- source_extraction ---
    extraction_status = llm_input.extraction_status if candidate else "complete"
    needs_review = candidate.needs_math_review if candidate else False
    review_reason = list(candidate.review_reason) if candidate else []
    bbox: list[float] = []
    page = 0
    if candidate:
        bbox = list(candidate.source_location.get("bbox", []))
        page = candidate.source_location.get("page", 0)

    source_extraction = EquationSourceExtraction(
        raw_text=llm_input.equation_text,
        latex=llm_input.latex,
        plain_text=llm_input.plain_text,
        source_location={
            "page": page,
            "section_id": llm_input.section_id,
            "block_id": llm_input.block_id,
            "bbox": bbox,
        },
        extraction_source="pdf_text_layer",
        extraction_status=extraction_status,
        needs_math_review=needs_review,
        review_reason=review_reason,
    )

    # --- reconstruction ---
    rec_raw = raw.get("reconstruction") or {}
    if rec_raw and rec_raw.get("status") not in (None, "none", ""):
        reconstruction = EquationReconstruction(
            latex=rec_raw.get("latex"),
            plain_text=rec_raw.get("plain_text"),
            status=rec_raw.get("status", "inferred_from_context"),
            method=list(rec_raw.get("method", [])),
            supporting_refs=list(rec_raw.get("supporting_refs", [])),
            confidence=_safe_float(rec_raw.get("confidence", 0.5)),
            review_required=bool(rec_raw.get("review_required", True)),
            review_reason=list(rec_raw.get("review_reason", [])),
        )
    else:
        reconstruction = EquationReconstruction.make_none()

    # --- semantics ---
    confidence = _safe_float(raw.get("confidence", 0.5))
    defined_symbols = []
    for raw_symbol in raw.get("defined_symbols", []):
        if not isinstance(raw_symbol, dict):
            continue
        defined_symbols.append(DefinedSymbol(
            symbol=str(raw_symbol.get("symbol", "")),
            definition_status=raw_symbol.get("definition_status", "unknown"),
            evidence_text=raw_symbol.get("evidence_text"),
        ))

    links_raw = raw.get("linked_text_spans", [])
    if not isinstance(links_raw, list):
        links_raw = []

    semantics = EquationSemantics(
        equation_type=raw.get("equation_type", "unknown"),
        secondary_types=list(raw.get("secondary_types", [])),
        semantic_status=raw.get("semantic_status", "unknown"),
        confidence=confidence,
        reason=str(raw.get("reason", "")),
        defined_symbols=defined_symbols,
        used_symbols=list(raw.get("used_symbols", [])),
        assumptions=list(raw.get("assumptions", [])),
        input_equation_ids=list(raw.get("input_equation_ids", [])),
        output_equation_ids=list(raw.get("output_equation_ids", [])),
        linked_text_spans=links_raw,
        source_evidence_ids=[],
        linked_claim_ids=[],
        summary=str(raw.get("summary", "")),
        review_flags=list(raw.get("review_flags", [])),
    )

    # --- confidence_policy (deterministic) ---
    confidence_policy = EquationConfidencePolicy.derive(source_extraction, reconstruction, semantics)

    # --- candidate_trace_ids ---
    candidate_trace_ids: list[str] = []
    if candidate:
        candidate_trace_ids = [candidate.candidate_id]

    return EquationRecord(
        equation_id=raw.get("equation_id", llm_input.equation_id),
        document_id=llm_input.document_id,
        label=raw.get("label", llm_input.label),
        candidate_trace_ids=candidate_trace_ids,
        source_extraction=source_extraction,
        reconstruction=reconstruction,
        semantics=semantics,
        confidence_policy=confidence_policy,
    )


def _fallback_record(
    llm_input: EquationLLMInput,
    candidate: EquationCandidate | None,
    reason: str,
) -> EquationRecord:
    flags: list[str] = ["ambiguous_role", "low_confidence"]
    if not llm_input.label:
        flags.append("missing_label")
    if not llm_input.prev_texts and not llm_input.next_texts:
        flags.append("broken_context")

    extraction_status = llm_input.extraction_status if candidate else "complete"
    needs_review = candidate.needs_math_review if candidate else False
    review_reason = list(candidate.review_reason) if candidate else []
    bbox: list[float] = []
    page = 0
    if candidate:
        bbox = list(candidate.source_location.get("bbox", []))
        page = candidate.source_location.get("page", 0)

    source_extraction = EquationSourceExtraction(
        raw_text=llm_input.equation_text,
        latex=llm_input.latex,
        plain_text=llm_input.plain_text,
        source_location={
            "page": page,
            "section_id": llm_input.section_id,
            "block_id": llm_input.block_id,
            "bbox": bbox,
        },
        extraction_source="pdf_text_layer",
        extraction_status=extraction_status,
        needs_math_review=needs_review,
        review_reason=review_reason,
    )
    reconstruction = EquationReconstruction.make_none()
    semantics = EquationSemantics(
        equation_type="unknown",
        secondary_types=[],
        semantic_status="unknown",
        confidence=0.0,
        reason=reason,
        defined_symbols=[],
        used_symbols=[],
        assumptions=[],
        input_equation_ids=[],
        output_equation_ids=[],
        linked_text_spans=[],
        source_evidence_ids=[],
        linked_claim_ids=[],
        summary="Equation semantics could not be inferred.",
        review_flags=flags,
    )
    confidence_policy = EquationConfidencePolicy.derive(source_extraction, reconstruction, semantics)

    return EquationRecord(
        equation_id=llm_input.equation_id,
        document_id=llm_input.document_id,
        label=llm_input.label,
        candidate_trace_ids=[candidate.candidate_id] if candidate else [],
        source_extraction=source_extraction,
        reconstruction=reconstruction,
        semantics=semantics,
        confidence_policy=confidence_policy,
    )


def _safe_float(value: object, default: float = 0.5) -> float:
    try:
        return max(0.0, min(1.0, float(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
