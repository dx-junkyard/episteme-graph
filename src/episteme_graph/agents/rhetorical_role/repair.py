"""Repair helpers for RhetoricalRoleAgent."""
from __future__ import annotations

import logging

from episteme_graph.agents.llm_step import MAX_REPAIR_ATTEMPTS, run_repair_loop

from .llm_client import RhetoricalRoleLLMClient
from .prompt import RhetoricalRolePromptFactory
from .schema import (
    BlockRoleAnnotation,
    CartridgeContext,
    RoleLLMInput,
    RhetoricalRoleResult,
    SpanAnnotation,
    ValidationIssue,
)

logger = logging.getLogger(__name__)

_MAX_REPAIR_ATTEMPTS = MAX_REPAIR_ATTEMPTS


class RhetoricalRoleRepairer:
    def repair(
        self,
        llm_input: RoleLLMInput,
        raw_output: dict,
        validation_issues: list[ValidationIssue],
        cartridge: CartridgeContext | None,
        llm_client: RhetoricalRoleLLMClient,
        prompt_factory: RhetoricalRolePromptFactory,
        validator: object,
    ) -> BlockRoleAnnotation:
        def _validate(annotation: BlockRoleAnnotation) -> list[ValidationIssue]:
            # The validator works on a whole result, so wrap the single block.
            partial = _single_result(llm_input, annotation)
            return validator.validate(  # type: ignore[attr-defined]
                partial, {llm_input.block_id: llm_input.block_text}, cartridge
            )

        return run_repair_loop(
            build_messages=lambda raw, issues: prompt_factory.build_repair_messages(
                llm_input, raw, issues, cartridge
            ),
            generate=lambda messages: llm_client.generate(messages),
            parse=lambda raw: _parse_block_annotation(raw, llm_input),
            validate=_validate,
            # Success yields the bare annotation (no validation_issues field).
            on_success=lambda annotation, _remaining: annotation,
            on_exhausted=lambda _issues: _fallback_annotation(
                llm_input, "Repair failed after max attempts"
            ),
            raw_output=raw_output,
            validation_issues=validation_issues,
            log_label="Rhetorical role",
        )


def _parse_block_annotation(raw: dict, llm_input: RoleLLMInput) -> BlockRoleAnnotation:
    raw_spans = raw.get("span_annotations", [])
    spans: list[SpanAnnotation] = []
    for i, span in enumerate(raw_spans if isinstance(raw_spans, list) else []):
        if not isinstance(span, dict):
            continue
        confidence = span.get("confidence", 0.5)
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = 0.5
        spans.append(SpanAnnotation(
            span_id=span.get("span_id", f"span_{i+1:03d}"),
            text=str(span.get("text", "")),
            char_start=int(span.get("char_start", 0)),
            char_end=int(span.get("char_end", 0)),
            role_labels=list(span.get("role_labels", ["unknown"])),
            is_claim_candidate=bool(span.get("is_claim_candidate", False)),
            is_reject_candidate=bool(span.get("is_reject_candidate", False)),
            confidence=max(0.0, min(1.0, confidence)),
            reason=str(span.get("reason", "")),
        ))

    return BlockRoleAnnotation(
        block_id=raw.get("block_id", llm_input.block_id),
        section_id=raw.get("section_id", llm_input.section_id),
        backbone_block_type=raw.get("backbone_block_type", llm_input.backbone_block_type),
        span_annotations=spans,
    )


def _fallback_annotation(llm_input: RoleLLMInput, reason: str) -> BlockRoleAnnotation:
    text = llm_input.block_text
    return BlockRoleAnnotation(
        block_id=llm_input.block_id,
        section_id=llm_input.section_id,
        backbone_block_type=llm_input.backbone_block_type,
        span_annotations=[SpanAnnotation(
            span_id="span_001",
            text=text,
            char_start=0,
            char_end=len(text),
            role_labels=["unknown"],
            is_claim_candidate=False,
            is_reject_candidate=False,
            confidence=0.0,
            reason=reason,
        )],
    )


def _single_result(
    llm_input: RoleLLMInput,
    annotation: BlockRoleAnnotation,
) -> RhetoricalRoleResult:
    claim_count = sum(1 for s in annotation.span_annotations if s.is_claim_candidate)
    reject_count = sum(1 for s in annotation.span_annotations if s.is_reject_candidate)
    return RhetoricalRoleResult(
        document_id=llm_input.document_id,
        cartridge_id=llm_input.cartridge_id,
        role_annotations=[annotation],
        summary_stats={
            "claim_candidate_spans": claim_count,
            "reject_candidate_spans": reject_count,
        },
    )
