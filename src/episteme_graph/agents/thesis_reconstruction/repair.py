"""Repair helpers for ThesisReconstructionAgent."""
from __future__ import annotations

import logging

from episteme_graph.agents.llm_step import (
    MAX_REPAIR_ATTEMPTS,
    attach_issues,
    run_repair_loop,
)

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
_MAX_REPAIR_ATTEMPTS = MAX_REPAIR_ATTEMPTS


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
        def _on_exhausted(issues: list[ValidationIssue]) -> ThesisReconstructionResult:
            fallback = ThesisReconstructionResult.make_fallback(
                llm_input.document_id, llm_input.cartridge_id, "Repair failed after max attempts"
            )
            fallback.validation_issues = issues
            return fallback

        return run_repair_loop(
            build_messages=lambda raw, issues: prompt_factory.build_repair_messages(
                llm_input, raw, issues, cartridge
            ),
            generate=lambda messages: llm_client.generate(messages),
            parse=lambda raw: _parse_raw(
                raw, llm_input.document_id, llm_input.cartridge_id
            ),
            validate=lambda result: validator.validate(result, cartridge),  # type: ignore[attr-defined]
            on_success=attach_issues,
            on_exhausted=_on_exhausted,
            raw_output=raw_output,
            validation_issues=validation_issues,
            log_label="Thesis reconstruction",
        )


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
