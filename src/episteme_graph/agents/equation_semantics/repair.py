"""Repair helpers for EquationSemanticsAgent.

Issue #245: EquationRecord (新スキーマ) に対応。
"""
from __future__ import annotations

import logging

from .llm_client import EquationSemanticsLLMClient
from .normalizer import EquationNormalizer
from .prompt import EquationSemanticsPromptFactory
from .schema import (
    CartridgeContext,
    DefinedSymbol,
    EquationCandidate,
    EquationConsistency,
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

# Deterministic, source-aware label validation for the final record (#368).
_LABEL_NORMALIZER = EquationNormalizer()


def _resolve_label_and_id(
    raw: dict,
    llm_input: EquationLLMInput,
) -> tuple[str | None, str, dict]:
    """Re-validate the LLM-provided label / equation_id (issue #368).

    ``llm_input.label`` / ``llm_input.equation_id`` are canonical (already
    produced by the source-aware normalizer at input-build time). An LLM may
    propose a different label; it is only adopted if it passes the same
    source-aware validator, and the equation_id is always *derived* — never
    taken verbatim from the LLM. Rejected LLM values are returned as audit
    metadata so the validator can surface them.
    """
    final_label = llm_input.label
    final_eq_id = llm_input.equation_id or _LABEL_NORMALIZER.equation_id_from_label(
        final_label, llm_input.block_id
    )
    audit: dict = {}

    llm_label = raw.get("label")
    if llm_label is not None and str(llm_label).strip() != str(final_label or "").strip():
        norm_label, valid, _rejected = _LABEL_NORMALIZER.validate_label(
            str(llm_label), source_trusted=llm_input.source_is_trusted
        )
        if valid and norm_label:
            final_label = norm_label
            final_eq_id = EquationNormalizer.equation_id_from_label(
                norm_label, llm_input.block_id
            )
        else:
            audit["llm_rejected_label"] = str(llm_label)

    llm_eq_id = raw.get("equation_id")
    if llm_eq_id and str(llm_eq_id) != str(final_eq_id):
        # The LLM equation_id is never trusted verbatim; keep the derived id.
        audit["llm_rejected_equation_id"] = str(llm_eq_id)

    return final_label, final_eq_id, audit


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
        image: dict | None = None,
    ) -> EquationRecord:
        for attempt in range(1, _MAX_REPAIR_ATTEMPTS + 1):
            logger.info("Equation semantics repair attempt %d/%d", attempt, _MAX_REPAIR_ATTEMPTS)
            messages = prompt_factory.build_repair_messages(
                llm_input, raw_output, validation_issues, cartridge
            )
            try:
                raw_output = llm_client.generate(messages, image=image)
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
    vision_meta = raw.get("_vision_ocr") if isinstance(raw, dict) else None
    if not isinstance(vision_meta, dict):
        vision_meta = None

    # --- deterministic, source-aware label / equation_id re-validation (#368) ---
    final_label, final_eq_id, label_audit = _resolve_label_and_id(raw, llm_input)

    # --- source_extraction ---
    extraction_status = llm_input.extraction_status if candidate else "complete"
    needs_review = candidate.needs_math_review if candidate else False
    review_reason = list(candidate.review_reason) if candidate else []
    if llm_input.needs_reconstruction:
        review_reason.extend(_vision_review_reasons(vision_meta))
    bbox: list[float] = []
    page = 0
    if candidate:
        bbox = list(candidate.source_location.get("bbox", []))
        page = candidate.source_location.get("page", 0)

    source_location = {
        "page": page,
        "section_id": llm_input.section_id,
        "block_id": llm_input.block_id,
        "bbox": bbox,
    }
    # Preserve rejected LLM label / equation_id as audit metadata.
    source_location.update(label_audit)

    source_extraction = EquationSourceExtraction(
        raw_text=llm_input.equation_text,
        latex=None if llm_input.needs_reconstruction else llm_input.latex,
        plain_text=None if llm_input.needs_reconstruction else llm_input.plain_text,
        source_location=source_location,
        extraction_source=llm_input.extraction_source,
        extraction_status=extraction_status,
        needs_math_review=needs_review,
        review_reason=review_reason,
    )

    # --- reconstruction ---
    rec_raw = raw.get("reconstruction") or {}
    if llm_input.source_is_trusted:
        reconstruction = EquationReconstruction.make_none()
    elif rec_raw and rec_raw.get("status") not in (None, "none", ""):
        rec_method = list(rec_raw.get("method", []))
        rec_review_reason = list(rec_raw.get("review_reason", []))
        if llm_input.needs_reconstruction:
            for method in _vision_methods(vision_meta):
                if method not in rec_method:
                    rec_method.append(method)
            rec_review_reason.extend(_vision_review_reasons(vision_meta))
        reconstruction = EquationReconstruction(
            latex=rec_raw.get("latex"),
            plain_text=rec_raw.get("plain_text"),
            status=rec_raw.get("status", "inferred_from_context"),
            method=rec_method,
            supporting_refs=list(rec_raw.get("supporting_refs", [])),
            confidence=_safe_float(rec_raw.get("confidence", 0.5)),
            review_required=bool(rec_raw.get("review_required", True)),
            review_reason=_dedupe(rec_review_reason),
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

    semantic_status = raw.get("semantic_status", "unknown")
    if llm_input.needs_reconstruction and semantic_status == "source_backed":
        semantic_status = "reconstruction_based" if reconstruction.status != "none" else "unknown"
    if llm_input.source_is_trusted and semantic_status != "unknown":
        semantic_status = "source_backed"
    review_flags = list(raw.get("review_flags", []))
    if llm_input.needs_reconstruction:
        for flag in ("needs_reconstruction", "reconstruction_only"):
            if flag not in review_flags:
                review_flags.append(flag)
    if llm_input.source_is_trusted:
        review_flags = [
            flag for flag in review_flags
            if flag not in ("needs_reconstruction", "reconstruction_only", "partial_extraction")
        ]

    semantics = EquationSemantics(
        equation_type=raw.get("equation_type", "unknown"),
        secondary_types=list(raw.get("secondary_types", [])),
        semantic_status=semantic_status,
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
        review_flags=review_flags,
    )

    # --- consistency / confidence_policy (deterministic) ---
    equation_consistency = EquationConsistency.derive(
        source_extraction=source_extraction,
        reconstruction=reconstruction,
        label=final_label,
        semantics=semantics,
    )
    confidence_policy = EquationConfidencePolicy.derive(
        source_extraction,
        reconstruction,
        semantics,
        equation_consistency,
    )

    # --- candidate_trace_ids ---
    candidate_trace_ids: list[str] = []
    if candidate:
        candidate_trace_ids = [candidate.candidate_id]

    return EquationRecord(
        equation_id=final_eq_id,
        document_id=llm_input.document_id,
        label=final_label,
        candidate_trace_ids=candidate_trace_ids,
        source_extraction=source_extraction,
        reconstruction=reconstruction,
        semantics=semantics,
        confidence_policy=confidence_policy,
        equation_consistency=equation_consistency,
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
    if llm_input.needs_reconstruction:
        review_reason.append("vision_ocr_unavailable:fallback_record")
    bbox: list[float] = []
    page = 0
    if candidate:
        bbox = list(candidate.source_location.get("bbox", []))
        page = candidate.source_location.get("page", 0)

    source_extraction = EquationSourceExtraction(
        raw_text=llm_input.equation_text,
        latex=None if llm_input.needs_reconstruction else llm_input.latex,
        plain_text=None if llm_input.needs_reconstruction else llm_input.plain_text,
        source_location={
            "page": page,
            "section_id": llm_input.section_id,
            "block_id": llm_input.block_id,
            "bbox": bbox,
        },
        extraction_source=llm_input.extraction_source,
        extraction_status=extraction_status,
        needs_math_review=needs_review,
        review_reason=review_reason,
    )
    if llm_input.needs_reconstruction:
        reconstruction = EquationReconstruction(
            latex=None,
            plain_text=None,
            status="inferred_from_context",
            method=["mandatory_reconstruction_layer", "text_context_fallback"],
            supporting_refs=[llm_input.block_id],
            confidence=0.0,
            review_required=True,
            review_reason=[reason, "vision_ocr_unavailable:fallback_record"],
        )
    else:
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
        review_flags=flags + (["needs_reconstruction", "reconstruction_only"] if llm_input.needs_reconstruction else []),
    )
    equation_consistency = EquationConsistency.derive(
        source_extraction=source_extraction,
        reconstruction=reconstruction,
        label=llm_input.label,
        semantics=semantics,
    )
    confidence_policy = EquationConfidencePolicy.derive(
        source_extraction,
        reconstruction,
        semantics,
        equation_consistency,
    )

    return EquationRecord(
        equation_id=llm_input.equation_id,
        document_id=llm_input.document_id,
        label=llm_input.label,
        candidate_trace_ids=[candidate.candidate_id] if candidate else [],
        source_extraction=source_extraction,
        reconstruction=reconstruction,
        semantics=semantics,
        confidence_policy=confidence_policy,
        equation_consistency=equation_consistency,
    )


def _safe_float(value: object, default: float = 0.5) -> float:
    try:
        return max(0.0, min(1.0, float(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _vision_methods(vision_meta: dict | None) -> list[str]:
    if not vision_meta:
        return ["vision_ocr_not_attempted", "text_context_fallback"]
    if vision_meta.get("used"):
        return ["vision_ocr"]
    return ["vision_ocr_unavailable", "text_context_fallback"]


def _vision_review_reasons(vision_meta: dict | None) -> list[str]:
    if not vision_meta:
        return ["vision_ocr_not_attempted"]
    provider = vision_meta.get("provider") or "unknown_provider"
    model = vision_meta.get("model") or "unknown_model"
    if vision_meta.get("used"):
        return [f"vision_ocr_used_unverified:{provider}:{model}"]
    reason = vision_meta.get("reason") or "unknown_reason"
    return [f"vision_ocr_unavailable:{reason}:{provider}:{model}"]


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    deduped: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped
