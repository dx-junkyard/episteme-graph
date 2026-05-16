"""Repair helpers for ClaimQualificationAgent."""
from __future__ import annotations

import logging

from .llm_client import ClaimQualificationLLMClient
from .prompt import ClaimQualificationPromptFactory
from .schema import (
    CartridgeContext,
    ClaimQualificationResult,
    QualificationLLMInput,
    QualifiedSpanRecord,
    ValidationIssue,
)

logger = logging.getLogger(__name__)

_MAX_REPAIR_ATTEMPTS = 2


class ClaimQualificationRepairer:
    def repair(
        self,
        llm_input: QualificationLLMInput,
        raw_output: dict,
        validation_issues: list[ValidationIssue],
        cartridge: CartridgeContext | None,
        llm_client: ClaimQualificationLLMClient,
        prompt_factory: ClaimQualificationPromptFactory,
        validator: object,
    ) -> QualifiedSpanRecord:
        for attempt in range(1, _MAX_REPAIR_ATTEMPTS + 1):
            logger.info("Claim qualification repair attempt %d/%d", attempt, _MAX_REPAIR_ATTEMPTS)
            messages = prompt_factory.build_repair_messages(
                llm_input, raw_output, validation_issues, cartridge
            )
            try:
                raw_output = llm_client.generate(messages)
            except Exception as exc:
                logger.warning("Repair LLM call failed: %s", exc)
                break

            record = _parse_record(raw_output, llm_input)
            partial = _single_result(llm_input, record)
            remaining = validator.validate(partial, cartridge)  # type: ignore[attr-defined]
            if not [i for i in remaining if i.severity == "error"]:
                return record
            validation_issues = remaining

        return _fallback_record(llm_input, "Repair failed after max attempts")


def _parse_record(raw: dict, llm_input: QualificationLLMInput) -> QualifiedSpanRecord:
    qualification = raw.get("qualification")
    if not isinstance(qualification, dict):
        qualification = {
            "status": raw.get("status", "deferred"),
            "claim_tier": raw.get("claim_tier", "background"),
            "claim_type_candidate": raw.get("claim_type_candidate", "unknown"),
            "granularity": raw.get("granularity", "too_narrow"),
            "evidence_adequacy": raw.get("evidence_adequacy", "weak"),
            "reviewability": raw.get("reviewability", "moderate"),
        }

    edit_suggestions = raw.get("edit_suggestions")
    if not isinstance(edit_suggestions, dict):
        edit_suggestions = {}
    edit_suggestions = {
        "should_split": bool(edit_suggestions.get("should_split", False)),
        "should_merge_with_prev": bool(edit_suggestions.get("should_merge_with_prev", False)),
        "should_merge_with_next": bool(edit_suggestions.get("should_merge_with_next", False)),
        "normalized_text_hint": str(edit_suggestions.get("normalized_text_hint", "")),
    }

    confidence = raw.get("confidence", 0.5)
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.5

    return QualifiedSpanRecord(
        span_id=raw.get("span_id", llm_input.span_id),
        block_id=raw.get("block_id", llm_input.block_id),
        section_id=raw.get("section_id", llm_input.section_id),
        text=raw.get("text", llm_input.span_text),
        role_labels=list(raw.get("role_labels", llm_input.role_labels)),
        qualification=qualification,
        edit_suggestions=edit_suggestions,
        reason=str(raw.get("reason", "")),
        confidence=max(0.0, min(1.0, confidence)),
    )


def _fallback_record(llm_input: QualificationLLMInput, reason: str) -> QualifiedSpanRecord:
    return QualifiedSpanRecord(
        span_id=llm_input.span_id,
        block_id=llm_input.block_id,
        section_id=llm_input.section_id,
        text=llm_input.span_text,
        role_labels=llm_input.role_labels,
        qualification={
            "status": "deferred",
            "claim_tier": "background",
            "claim_type_candidate": "unknown",
            "granularity": "too_narrow",
            "evidence_adequacy": "weak",
            "reviewability": "poor",
        },
        edit_suggestions={
            "should_split": False,
            "should_merge_with_prev": False,
            "should_merge_with_next": False,
            "normalized_text_hint": "",
        },
        reason=reason,
        confidence=0.0,
    )


def _single_result(
    llm_input: QualificationLLMInput,
    record: QualifiedSpanRecord,
) -> ClaimQualificationResult:
    accepted = [record] if record.qualification.get("status") == "accepted" else []
    rejected = [record.__dict__] if record.qualification.get("status") == "rejected" else []
    deferred = [record.__dict__] if record.qualification.get("status") == "deferred" else []
    return ClaimQualificationResult(
        document_id=llm_input.document_id,
        cartridge_id=llm_input.cartridge_id,
        qualified_spans=accepted,
        rejected_spans=rejected,
        deferred_spans=deferred,
        summary_stats={
            "accepted": len(accepted),
            "rejected": len(rejected),
            "deferred": len(deferred),
            "split_suggested": 1 if record.edit_suggestions.get("should_split") else 0,
            "merge_suggested": 1 if (
                record.edit_suggestions.get("should_merge_with_prev")
                or record.edit_suggestions.get("should_merge_with_next")
            ) else 0,
        },
    )
