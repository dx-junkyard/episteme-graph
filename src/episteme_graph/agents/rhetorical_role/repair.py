"""Repair helpers for RhetoricalRoleAgent.

Offset ownership (2026-09-19)
-----------------------------
The validator requires ``block_text[char_start:char_end] == span.text`` — the
span must be a *verbatim* slice of the block, otherwise every downstream
consumer (claim_qualification / evidence_registry) would quote text the paper
does not contain. That rule stays.

What changed is **who computes the offsets**. Asking an LLM to count characters
inside a ~1,800 character block is a task it fails at systematically: on the
2026-09-15 corpus 65〜98 % of the spans ended as
``reason == "Repair failed after max attempts"`` (role ``unknown`` /
``confidence 0.0`` / ``is_claim_candidate False``), and only short fragments
survived. The LLM's *judgement* (which text is a span, what role it carries) was
usually fine; only the arithmetic was wrong.

So the offsets are now resolved deterministically on our side
(:func:`resolve_span_offsets`): the span text the LLM returned is located inside
the block (exact → whitespace-normalized → head/tail anchor) and
``char_start`` / ``char_end`` are rewritten to the located range. A span whose
text cannot be located at all keeps the LLM's numbers and still fails
validation. The correction is recorded on the span
(:attr:`SpanAnnotation.offset_correction`) so nothing is silently rewritten (P4).
"""
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

#: Reason string stamped on the single fallback span of a block whose repair
#: attempts were exhausted. The agent counts these for ``summary_stats``.
REPAIR_FAILED_REASON = "Repair failed after max attempts"

# ``offset_correction`` markers (kept on the span, never dropped).
OFFSET_CORRECTION_EXACT = "exact_match"
OFFSET_CORRECTION_WHITESPACE = "whitespace_normalized"
OFFSET_CORRECTION_ANCHOR = "head_tail_anchor"

#: Head / tail length used by the anchor fallback.
ANCHOR_LENGTH = 40


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
                llm_input, REPAIR_FAILED_REASON
            ),
            raw_output=raw_output,
            validation_issues=validation_issues,
            log_label="Rhetorical role",
        )


def _normalized_with_index(text: str) -> tuple[str, list[int]]:
    """Whitespace-collapsed text plus, per character, its original index."""
    chars: list[str] = []
    index: list[int] = []
    prev_space = False
    for i, ch in enumerate(text):
        if ch.isspace():
            if prev_space:
                continue
            chars.append(" ")
            index.append(i)
            prev_space = True
        else:
            chars.append(ch)
            index.append(i)
            prev_space = False
    return "".join(chars), index


def _closest_occurrence(haystack: str, needle: str, hint: int) -> int:
    """Start index of the occurrence nearest ``hint`` (``-1`` when absent).

    Deterministic: ties go to the earlier occurrence.
    """
    if not needle:
        return -1
    positions: list[int] = []
    cursor = 0
    while True:
        found = haystack.find(needle, cursor)
        if found < 0:
            break
        positions.append(found)
        cursor = found + 1
    if not positions:
        return -1
    return min(positions, key=lambda pos: (abs(pos - hint), pos))


def resolve_span_offsets(
    block_text: str,
    span_text: str,
    char_start: int,
    char_end: int,
) -> tuple[int, int, str | None]:
    """Locate ``span_text`` inside ``block_text`` and return its real offsets.

    Returns ``(char_start, char_end, correction)``. ``correction`` is ``None``
    when the LLM's own offsets already quote the span verbatim **or** when the
    span text cannot be located (the caller keeps the LLM's numbers and the
    validator reports the mismatch as before — the "must be a verbatim slice"
    rule is not relaxed).

    Three deterministic strategies, in order:

    1. exact substring;
    2. whitespace-normalized substring (line wraps / double spaces in the block);
    3. head/tail anchor — the first and last :data:`ANCHOR_LENGTH` characters of
       the span are located and everything between them is taken, which recovers
       long spans the LLM copied with a typo in the middle.
    """
    text = str(span_text or "")
    block = str(block_text or "")
    if not block or not text:
        return char_start, char_end, None
    if 0 <= char_start < char_end <= len(block) and block[char_start:char_end] == text:
        return char_start, char_end, None

    hint = char_start if char_start > 0 else 0

    found = _closest_occurrence(block, text, hint)
    if found >= 0:
        return found, found + len(text), OFFSET_CORRECTION_EXACT

    norm_block, origin = _normalized_with_index(block)
    norm_span = " ".join(text.split())
    if not norm_span:
        return char_start, char_end, None

    found = _closest_occurrence(norm_block, norm_span, hint)
    if found >= 0:
        end = origin[found + len(norm_span) - 1] + 1
        return origin[found], end, OFFSET_CORRECTION_WHITESPACE

    if len(norm_span) >= 2 * ANCHOR_LENGTH:
        head = norm_span[:ANCHOR_LENGTH]
        tail = norm_span[-ANCHOR_LENGTH:]
        head_at = _closest_occurrence(norm_block, head, hint)
        if head_at >= 0:
            tail_at = norm_block.find(tail, head_at + len(head))
            if tail_at >= 0:
                end_norm = tail_at + len(tail)
                # Guard against an anchor pair that spans half the block.
                if end_norm - head_at <= 3 * len(norm_span) + 80:
                    return (
                        origin[head_at],
                        origin[end_norm - 1] + 1,
                        OFFSET_CORRECTION_ANCHOR,
                    )

    return char_start, char_end, None


def _coerce_offset(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _parse_block_annotation(raw: dict, llm_input: RoleLLMInput) -> BlockRoleAnnotation:
    raw_spans = raw.get("span_annotations", [])
    block_text = llm_input.block_text or ""
    spans: list[SpanAnnotation] = []
    for i, span in enumerate(raw_spans if isinstance(raw_spans, list) else []):
        if not isinstance(span, dict):
            continue
        confidence = span.get("confidence", 0.5)
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = 0.5
        text = str(span.get("text", ""))
        char_start, char_end, correction = resolve_span_offsets(
            block_text,
            text,
            _coerce_offset(span.get("char_start", 0)),
            _coerce_offset(span.get("char_end", 0)),
        )
        if correction is not None:
            # 訂正したオフセットが指す本文を span.text にする。whitespace 正規化・
            # 頭尾アンカーの 2 戦略は「LLM が写した文字列」と「本文のスライス」が
            # 一致しない（改行・二重空白・中間の脱字）ので、そのままでは validator の
            # 逐語比較（``block_text[start:end] == span.text``）を通らず、せっかく
            # 位置を当てても修復失敗に数えられていた（2026-09-19 レビュー R-2）。
            # 検証基準は不変で、LLM の写しの方を本文に揃える（本文が正本）。
            text = block_text[char_start:char_end]
        spans.append(SpanAnnotation(
            span_id=span.get("span_id", f"span_{i+1:03d}"),
            text=text,
            char_start=char_start,
            char_end=char_end,
            role_labels=list(span.get("role_labels", ["unknown"])),
            is_claim_candidate=bool(span.get("is_claim_candidate", False)),
            is_reject_candidate=bool(span.get("is_reject_candidate", False)),
            confidence=max(0.0, min(1.0, confidence)),
            reason=str(span.get("reason", "")),
            offset_correction=correction,
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


def is_repair_failed_annotation(annotation: BlockRoleAnnotation) -> bool:
    """True for the whole-block fallback produced when repair was exhausted."""
    spans = annotation.span_annotations or []
    return len(spans) == 1 and spans[0].reason == REPAIR_FAILED_REASON


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
