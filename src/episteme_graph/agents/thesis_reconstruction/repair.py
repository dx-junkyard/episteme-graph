"""Repair helpers for ThesisReconstructionAgent."""
from __future__ import annotations

import logging

from .llm_client import ThesisReconstructionLLMClient
from .prompt import ThesisReconstructionPromptFactory
from .schema import (
    SUPPORT_SECTIONS,
    THESIS_VERSION,
    CartridgeContext,
    ThesisLLMInput,
    ThesisReconstructionResult,
    ValidationIssue,
)

logger = logging.getLogger(__name__)
_MAX_REPAIR_ATTEMPTS = 2


class ThesisReconstructionRepairer:
    def repair(
        self,
        llm_input: ThesisLLMInput,
        raw_output: dict,
        validation_issues: list[ValidationIssue],
        cartridge: CartridgeContext | None,
        llm_client: ThesisReconstructionLLMClient,
        prompt_factory: ThesisReconstructionPromptFactory,
        validator: object,
    ) -> ThesisReconstructionResult:
        for attempt in range(1, _MAX_REPAIR_ATTEMPTS + 1):
            logger.info("Thesis reconstruction repair attempt %d/%d", attempt, _MAX_REPAIR_ATTEMPTS)
            messages = prompt_factory.build_repair_messages(
                llm_input, raw_output, validation_issues, cartridge
            )
            try:
                raw_output = llm_client.generate(messages)
            except Exception as exc:
                logger.warning("Repair LLM call failed: %s", exc)
                break
            result = _parse_raw(raw_output, llm_input.document_id, llm_input.cartridge_id)
            remaining = validator.validate(result, cartridge)  # type: ignore[attr-defined]
            if not [i for i in remaining if i.severity == "error"]:
                result.validation_issues = remaining
                return result
            validation_issues = remaining
        fallback = ThesisReconstructionResult.make_fallback(
            llm_input.document_id, llm_input.cartridge_id, "Repair failed after max attempts"
        )
        fallback.validation_issues = validation_issues
        return fallback


def _parse_raw(
    raw: dict,
    document_id: str,
    cartridge_id: str | None,
) -> ThesisReconstructionResult:
    confidence = raw.get("confidence", 0.0)
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    support = raw.get("support_structure", {})
    if not isinstance(support, dict):
        support = {}
    for section in SUPPORT_SECTIONS:
        support.setdefault(section, [])

    thesis = raw.get("central_thesis", {})
    if not isinstance(thesis, dict):
        thesis = {
            "text": str(thesis),
            "claim_ids": [],
            "equation_ids": [],
            "evidence_block_ids": [],
            "reason": "",
            "confidence": 0.5,
        }

    return ThesisReconstructionResult(
        document_id=raw.get("document_id", document_id),
        thesis_version=raw.get("thesis_version", THESIS_VERSION),
        cartridge_id=raw.get("cartridge_id", cartridge_id),
        central_thesis=thesis,
        alternative_theses=list(raw.get("alternative_theses", [])),
        support_structure=support,
        excluded_from_core=list(raw.get("excluded_from_core", [])),
        thesis_graph_hints=list(raw.get("thesis_graph_hints", [])),
        review_notes=list(raw.get("review_notes", [])),
        confidence=confidence,
    )
