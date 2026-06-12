"""Repair helpers for ClaimQualificationAgent."""
from __future__ import annotations

import logging

from .context_lint import apply_context_lint
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

    atomic_claims = _normalize_atomic_claims(raw, edit_suggestions, llm_input)
    # Deterministic context-dependency lint (issue #357): demote accepted
    # atomic claims whose text still leads with unresolved anaphora, and fill
    # the structured context_refs list.
    apply_context_lint(atomic_claims)

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
        atomic_claims=atomic_claims,
    )


def _normalize_atomic_claims(
    raw: dict,
    edit_suggestions: dict,
    llm_input: QualificationLLMInput,
) -> list[dict]:
    """Normalize LLM atomic-rewrite output (issue #317).

    Reads the rich ``atomic_claims`` field; for backward compatibility, falls back
    to legacy ``edit_suggestions.split_claims`` ({text, claim_type}) entries.
    Every entry is coerced to the full atomic-claim shape so the downstream
    ClaimObjectBuilder can treat them as confirmed LLM-rewritten candidates.
    """
    entries = raw.get("atomic_claims")
    legacy = False
    if not isinstance(entries, list) or not entries:
        # Backward compat: older prompt emitted edit_suggestions.split_claims.
        entries = (raw.get("edit_suggestions") or {}).get("split_claims")
        legacy = True
    if not isinstance(entries, list):
        return []

    normalized: list[dict] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        text = str(entry.get("text", "")).strip()
        if not text:
            continue
        atomicity = str(entry.get("atomicity", "atomic")).strip().lower()
        if atomicity not in ("atomic", "non_atomic"):
            atomicity = "atomic"
        status = str(entry.get("status", "")).strip().lower()
        if status not in ("accepted", "review_required"):
            status = "review_required" if atomicity == "non_atomic" else "accepted"
        try:
            conf = float(entry.get("confidence", llm_input and 0.0))
        except (TypeError, ValueError):
            conf = 0.0
        normalized.append({
            "text": text,
            "normalized_text": str(entry.get("normalized_text", "") or text).strip(),
            "claim_type_candidate": str(
                entry.get("claim_type_candidate")
                or entry.get("claim_type")
                or ""
            ).strip(),
            "atomicity": atomicity,
            "status": status,
            "source_span_id": str(entry.get("source_span_id") or llm_input.span_id),
            "evidence_quote": str(entry.get("evidence_quote", "") or ""),
            "qualification_reason": str(
                entry.get("qualification_reason", "")
                or ("legacy split_claims candidate" if legacy else "")
            ),
            # Structured intra-document references (issue #357); filled by the
            # context lint after normalization.
            "context_refs": list(entry.get("context_refs") or []),
            "confidence": max(0.0, min(1.0, conf)),
        })
    return normalized


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
