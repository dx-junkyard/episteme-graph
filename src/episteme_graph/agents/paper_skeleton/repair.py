"""PaperSkeletonRepairer: validation 失敗時に LLM 再試行を行う。

Repair 戦略:
1. validation issues を repair prompt に含めて再呼び出し
2. 再試行後も error が残る場合は fallback result を返す
3. warning のみであれば partial result として受け入れる
"""
from __future__ import annotations

import logging

from .llm_client import PaperSkeletonLLMClient
from .prompt import PaperSkeletonPromptFactory
from .schema import (
    CartridgeContext,
    LogicalBlock,
    PaperSkeletonResult,
    SKELETON_VERSION,
    ValidationIssue,
    SkeletonLLMInput,
)

logger = logging.getLogger(__name__)

_MAX_REPAIR_ATTEMPTS = 2


class PaperSkeletonRepairer:
    def repair(
        self,
        llm_input: SkeletonLLMInput,
        raw_output: dict,
        validation_issues: list[ValidationIssue],
        cartridge: CartridgeContext | None,
        llm_client: PaperSkeletonLLMClient,
        prompt_factory: PaperSkeletonPromptFactory,
        validator: object,
    ) -> PaperSkeletonResult:
        """validation_issues を含む repair prompt で再試行する。"""
        for attempt in range(1, _MAX_REPAIR_ATTEMPTS + 1):
            logger.info("Repair attempt %d/%d", attempt, _MAX_REPAIR_ATTEMPTS)
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

            errors = [i for i in remaining if i.severity == "error"]
            if not errors:
                result.validation_issues = remaining
                return result

            validation_issues = remaining

        # Exhausted retries — return fallback
        logger.warning(
            "Repair exhausted for %s; returning fallback result", llm_input.document_id
        )
        fallback = PaperSkeletonResult.make_fallback(
            llm_input.document_id,
            llm_input.cartridge_id,
            "Repair failed after max attempts",
        )
        fallback.validation_issues = validation_issues
        return fallback


def _parse_raw(raw: dict, document_id: str, cartridge_id: str | None) -> PaperSkeletonResult:
    """raw LLM dict → PaperSkeletonResult。欠落フィールドは空値で補填する。"""
    def _entry(key: str) -> dict:
        val = raw.get(key, {})
        if not isinstance(val, dict):
            return {"text": str(val), "evidence_block_ids": [], "reason": "", "confidence": 0.5}
        return val

    logical_blocks_raw = raw.get("logical_blocks", [])
    logical_blocks: list[LogicalBlock] = []
    for i, lb in enumerate(logical_blocks_raw if isinstance(logical_blocks_raw, list) else []):
        if not isinstance(lb, dict):
            continue
        logical_blocks.append(LogicalBlock(
            block_id=lb.get("block_id", f"logic_{i+1}"),
            block_type=lb.get("block_type", "meta"),
            label=lb.get("label", ""),
            section_ids=lb.get("section_ids", []),
            evidence_block_ids=lb.get("evidence_block_ids", []),
            summary=lb.get("summary", ""),
            reason=lb.get("reason", ""),
            confidence=float(lb.get("confidence", 0.5)),
        ))

    confidence = raw.get("confidence", 0.0)
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    return PaperSkeletonResult(
        document_id=raw.get("document_id", document_id),
        skeleton_version=raw.get("skeleton_version", SKELETON_VERSION),
        cartridge_id=raw.get("cartridge_id", cartridge_id),
        paper_goal=_entry("paper_goal"),
        central_question=_entry("central_question"),
        headline_claim=_entry("headline_claim"),
        supporting_subclaims=raw.get("supporting_subclaims", []),
        logical_blocks=logical_blocks,
        excluded_regions=raw.get("excluded_regions", []),
        review_notes=raw.get("review_notes", []),
        confidence=confidence,
    )
