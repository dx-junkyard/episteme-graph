"""RhetoricalRoleAgent: chunk/span rhetorical role labeling.

Design: LLM-first / cartridge-aware. This agent consumes DocumentStructureAgent
and PaperSkeletonAgent outputs and emits role-labeled span annotations for
downstream claim extraction.
"""
from __future__ import annotations

import logging

from episteme_graph.agents.document_structure.schema import DocumentStructureResult
from episteme_graph.agents.paper_skeleton.schema import PaperSkeletonResult

from episteme_graph.agents.cartridge_loader import load_cartridge_or_none

from .cartridge_loader import CartridgeLoader
from .input_builder import RhetoricalRoleInputBuilder
from .llm_client import RhetoricalRoleLLMClient
from .prompt import RhetoricalRolePromptFactory
from .repair import (
    RhetoricalRoleRepairer,
    _fallback_annotation,
    _parse_block_annotation,
    is_repair_failed_annotation,
    parse_window_answer,
)
from .schema import (
    BlockRoleAnnotation,
    CartridgeContext,
    RhetoricalRoleResult,
    RoleLLMInput,
    ValidationIssue,
)
from .validator import RhetoricalRoleValidator
from .window import (
    OUTCOME_LLM_ERROR,
    OUTCOME_OK,
    OUTCOME_REPAIR_FAILED,
    OUTCOME_REPAIRED,
    annotation_from_dict,
    block_keys,
    build_checkpoint,
    checkpoint_entry,
    group_windows,
    resolve_window_config,
    restore_checkpoint,
)

logger = logging.getLogger(__name__)


class RhetoricalRoleAgent:
    """Generate role-labeled spans from structured paper blocks."""

    def __init__(
        self,
        cartridge_base_dir: str | None = None,
        llm_model: str | None = None,
    ) -> None:
        self._cartridge_loader = CartridgeLoader(cartridge_base_dir)
        self._input_builder = RhetoricalRoleInputBuilder()
        self._prompt_factory = RhetoricalRolePromptFactory()
        self._llm_client = RhetoricalRoleLLMClient(model=llm_model)
        self._validator = RhetoricalRoleValidator()
        self._repairer = RhetoricalRoleRepairer()

    def run(
        self,
        structure: DocumentStructureResult,
        skeleton: PaperSkeletonResult,
        cartridge_id: str | None = None,
        config: dict | None = None,
        progress_callback=None,
        checkpoint: dict | None = None,
        checkpoint_callback=None,
    ) -> RhetoricalRoleResult:
        """Label every target block.

        ``checkpoint`` / ``checkpoint_callback`` (IK-0710): the agent hands its
        per-block state to ``checkpoint_callback`` after every window, and a
        state passed back as ``checkpoint`` skips the blocks it already answered
        (matched by block fingerprint). The format is owned by ``window.py``.
        """
        cartridge = self._load_cartridge(cartridge_id)
        # P0-1: 入力と一緒に「取りこぼしの量」（母集合 / 処理数 / 打ち切り / 理由）を受け取り、
        # summary_stats["coverage"] として stage_outputs まで運ぶ（F-18）。
        llm_inputs, coverage = self._input_builder.build_with_coverage(
            structure, skeleton, cartridge=cartridge, config=config
        )
        if not llm_inputs:
            fallback = RhetoricalRoleResult.make_fallback(
                structure.document_id, cartridge_id, None, "No target blocks for role labeling"
            )
            # 対象ゼロも「見た」ことの記録として残す（population=0 の報告）。
            fallback.summary_stats["coverage"] = coverage
            return fallback

        block_text_by_id = {i.block_id: i.block_text for i in llm_inputs}
        result_cartridge_id = cartridge.cartridge_id if cartridge else cartridge_id
        blocks_per_call, window_max_chars = resolve_window_config(config)
        client = _CountingClient(self._llm_client)

        # IK-0710: blocks already answered in a checkpoint of this stage are not
        # re-asked. ``entries`` is the full per-block state (restored + new) and is
        # handed back through ``checkpoint_callback`` after every window.
        keys = block_keys(llm_inputs)
        key_of = {id(i): k for i, k in zip(llm_inputs, keys)}
        entries: dict[str, dict] = restore_checkpoint(
            checkpoint, llm_inputs, keys, result_cartridge_id
        )
        resumed_blocks = len(entries)
        pending = [i for i in llm_inputs if key_of[id(i)] not in entries]

        windows = group_windows(pending, blocks_per_call, window_max_chars)
        window_calls = 0
        window_fallback_windows = 0
        window_fallback_blocks = 0
        total = len(llm_inputs)
        for window in windows:
            if len(window) == 1:
                annotation, outcome = self._judge_single_block(
                    window[0], cartridge, client, structure.document_id
                )
                entries[key_of[id(window[0])]] = checkpoint_entry(window[0], annotation, outcome)
            else:
                window_calls += 1
                judged = self._judge_window(window, cartridge, client, structure.document_id)
                fallback = [i for i in window if str(i.block_id) not in judged]
                if fallback:
                    window_fallback_windows += 1
                    window_fallback_blocks += len(fallback)
                for llm_input in window:
                    if str(llm_input.block_id) in judged:
                        annotation, outcome = judged[str(llm_input.block_id)]
                    else:
                        # The window did not answer this block: ask it alone through
                        # the single-block path (never drop a block — P4).
                        annotation, outcome = self._judge_single_block(
                            llm_input, cartridge, client, structure.document_id
                        )
                    entries[key_of[id(llm_input)]] = checkpoint_entry(llm_input, annotation, outcome)
            if checkpoint_callback:
                try:
                    checkpoint_callback(build_checkpoint(result_cartridge_id, entries))
                except Exception:
                    logger.warning("rhetorical_role checkpoint_callback raised", exc_info=True)
            if progress_callback:
                progress_callback(len(entries), total)

        annotations: list[BlockRoleAnnotation] = []
        outcomes: list[str] = []
        for key in keys:
            entry = entries[key]
            annotations.append(annotation_from_dict(entry["annotation"]))
            outcomes.append(str(entry.get("outcome") or OUTCOME_OK))

        result = self._build_result(
            document_id=structure.document_id,
            cartridge_id=result_cartridge_id,
            annotations=annotations,
            coverage=coverage,
            extra_stats={
                "blocks_processed": total,
                # Repair losses are counted per block: a block that ends on the
                # whole-block fallback contributes exactly one ``unknown`` span,
                # which the coverage report (population/processed of *blocks*)
                # cannot express — it counts the block as processed, and it was.
                "llm_error_blocks": outcomes.count(OUTCOME_LLM_ERROR),
                "repaired_blocks": outcomes.count(OUTCOME_REPAIRED),
                "repair_failed_blocks": outcomes.count(OUTCOME_REPAIR_FAILED),
                # IK-0699: request unit and its cost in this run.
                "blocks_per_call": blocks_per_call,
                "window_max_chars": window_max_chars,
                "windows": window_calls,
                "window_fallback_windows": window_fallback_windows,
                "window_fallback_blocks": window_fallback_blocks,
                "llm_calls": client.calls,
                # IK-0710: blocks taken from the checkpoint of an interrupted run.
                "resumed_blocks": resumed_blocks,
            },
        )
        result.validation_issues = self._validator.validate(
            result, block_text_by_id, cartridge
        )
        return result

    # ------------------------------------------------------------------
    # single block (the original path; also the window fallback)
    # ------------------------------------------------------------------

    def _judge_single_block(
        self,
        llm_input: RoleLLMInput,
        cartridge: CartridgeContext | None,
        client: "_CountingClient",
        document_id: str,
    ) -> tuple[BlockRoleAnnotation, str]:
        messages = self._prompt_factory.build_messages(llm_input, cartridge)
        try:
            raw_output = client.generate(messages)
        except Exception as exc:
            logger.exception(
                "LLM role labeling failed for document=%s block_id=%s cartridge=%s",
                document_id,
                llm_input.block_id,
                llm_input.cartridge_id,
            )
            return _fallback_annotation(llm_input, str(exc)), OUTCOME_LLM_ERROR

        annotation = _parse_block_annotation(raw_output, llm_input)
        issues = self._validate_block(llm_input, annotation, cartridge)
        if not [i for i in issues if i.severity == "error"]:
            return annotation, OUTCOME_OK
        logger.warning(
            "Rhetorical role validation repair required for document=%s block_id=%s errors=%d",
            document_id,
            llm_input.block_id,
            len([i for i in issues if i.severity == "error"]),
        )
        annotation = self._repairer.repair(
            llm_input=llm_input,
            raw_output=raw_output,
            validation_issues=issues,
            cartridge=cartridge,
            llm_client=client,
            prompt_factory=self._prompt_factory,
            validator=self._validator,
        )
        if is_repair_failed_annotation(annotation):
            return annotation, OUTCOME_REPAIR_FAILED
        return annotation, OUTCOME_REPAIRED

    def _validate_block(
        self,
        llm_input: RoleLLMInput,
        annotation: BlockRoleAnnotation,
        cartridge: CartridgeContext | None,
    ) -> list[ValidationIssue]:
        partial = self._build_result(
            document_id=llm_input.document_id,
            cartridge_id=llm_input.cartridge_id,
            annotations=[annotation],
        )
        return self._validator.validate(
            partial, {llm_input.block_id: llm_input.block_text}, cartridge
        )

    # ------------------------------------------------------------------
    # window (IK-0699)
    # ------------------------------------------------------------------

    def _judge_window(
        self,
        window: list[RoleLLMInput],
        cartridge: CartridgeContext | None,
        client: "_CountingClient",
        document_id: str,
    ) -> dict[str, tuple[BlockRoleAnnotation, str]]:
        """Judge a window; return the blocks it answered validly.

        Blocks missing from the answer or still invalid after
        :data:`WINDOW_REPAIR_ATTEMPTS` window repairs are absent from the
        returned dict — the caller re-asks them through the single-block path.
        A repair only re-asks the blocks still unresolved.
        """
        validate_block = lambda llm_input, annotation: self._validate_block(  # noqa: E731
            llm_input, annotation, cartridge
        )
        messages = self._prompt_factory.build_window_messages(window, cartridge)
        try:
            raw_output = client.generate(messages)
        except Exception:
            logger.warning(
                "LLM window role labeling failed for document=%s blocks=%s; "
                "falling back to single-block judgement",
                document_id,
                [i.block_id for i in window],
                exc_info=True,
            )
            return {}
        accepted, unresolved, issues = parse_window_answer(
            raw_output, window, validate_block, outcome=OUTCOME_OK
        )
        if not unresolved:
            return accepted
        logger.warning(
            "Rhetorical role window repair required for document=%s blocks=%s",
            document_id,
            [i.block_id for i in unresolved],
        )
        accepted.update(self._repairer.repair_window(
            unresolved=unresolved,
            raw_output=raw_output,
            validation_issues=issues,
            cartridge=cartridge,
            llm_client=client,
            prompt_factory=self._prompt_factory,
            validate_block=validate_block,
        ))
        return accepted

    def _load_cartridge(self, cartridge_id: str | None) -> CartridgeContext | None:
        if not cartridge_id:
            return None
        return load_cartridge_or_none(self._cartridge_loader, cartridge_id)

    @staticmethod
    def _build_result(
        document_id: str,
        cartridge_id: str | None,
        annotations: list[BlockRoleAnnotation],
        coverage: dict | None = None,
        extra_stats: dict | None = None,
    ) -> RhetoricalRoleResult:
        spans = [span for a in annotations for span in a.span_annotations]
        claim_count = sum(1 for span in spans if span.is_claim_candidate)
        reject_count = sum(1 for span in spans if span.is_reject_candidate)
        summary_stats: dict = {
            "claim_candidate_spans": claim_count,
            "reject_candidate_spans": reject_count,
        }
        if extra_stats is not None:
            summary_stats.update(extra_stats)
            summary_stats["total_spans"] = len(spans)
            summary_stats["unknown_role_spans"] = sum(
                1 for span in spans if list(span.role_labels) == ["unknown"]
            )
            summary_stats["offset_corrected_spans"] = sum(
                1 for span in spans if span.offset_correction
            )
        if coverage is not None:
            summary_stats["coverage"] = coverage
        return RhetoricalRoleResult(
            document_id=document_id,
            cartridge_id=cartridge_id,
            role_annotations=annotations,
            summary_stats=summary_stats,
        )


class _CountingClient:
    """Proxy that counts the LLM calls this run made (``summary_stats.llm_calls``).

    Counts at the call site so the meter stays meaningful even when tests patch
    ``generate``; the provider client's own ``calls`` meter is untouched.
    """

    def __init__(self, inner) -> None:
        self._inner = inner
        self.calls = 0

    def generate(self, messages):
        self.calls += 1
        return self._inner.generate(messages)

