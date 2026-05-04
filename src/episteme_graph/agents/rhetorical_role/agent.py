"""RhetoricalRoleAgent: chunk/span rhetorical role labeling.

Design: LLM-first / cartridge-aware. This agent consumes DocumentStructureAgent
and PaperSkeletonAgent outputs and emits role-labeled span annotations for
downstream claim extraction.
"""
from __future__ import annotations

import logging

from episteme_graph.agents.document_structure.schema import DocumentStructureResult
from episteme_graph.agents.paper_skeleton.schema import PaperSkeletonResult

from .cartridge_loader import CartridgeLoader
from .input_builder import RhetoricalRoleInputBuilder
from .llm_client import RhetoricalRoleLLMClient
from .prompt import RhetoricalRolePromptFactory
from .repair import RhetoricalRoleRepairer, _fallback_annotation, _parse_block_annotation
from .schema import (
    BlockRoleAnnotation,
    CartridgeContext,
    RhetoricalRoleResult,
)
from .validator import RhetoricalRoleValidator

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
    ) -> RhetoricalRoleResult:
        cartridge = self._load_cartridge(cartridge_id)
        llm_inputs = self._input_builder.build(
            structure, skeleton, cartridge=cartridge, config=config
        )
        if not llm_inputs:
            return RhetoricalRoleResult.make_fallback(
                structure.document_id, cartridge_id, None, "No target blocks for role labeling"
            )

        annotations: list[BlockRoleAnnotation] = []
        block_text_by_id = {i.block_id: i.block_text for i in llm_inputs}

        total = len(llm_inputs)
        for idx, llm_input in enumerate(llm_inputs, start=1):
            messages = self._prompt_factory.build_messages(llm_input, cartridge)
            try:
                raw_output = self._llm_client.generate(messages)
            except Exception as exc:
                logger.exception(
                    "LLM role labeling failed for document=%s block_id=%s cartridge=%s",
                    structure.document_id,
                    llm_input.block_id,
                    llm_input.cartridge_id,
                )
                annotations.append(_fallback_annotation(llm_input, str(exc)))
                if progress_callback:
                    progress_callback(idx, total)
                continue

            annotation = _parse_block_annotation(raw_output, llm_input)
            partial = self._build_result(
                document_id=structure.document_id,
                cartridge_id=llm_input.cartridge_id,
                annotations=[annotation],
            )
            issues = self._validator.validate(
                partial, {llm_input.block_id: llm_input.block_text}, cartridge
            )
            errors = [i for i in issues if i.severity == "error"]
            if errors:
                logger.warning(
                    "Rhetorical role validation repair required for document=%s block_id=%s errors=%d",
                    structure.document_id,
                    llm_input.block_id,
                    len(errors),
                )
                annotation = self._repairer.repair(
                    llm_input=llm_input,
                    raw_output=raw_output,
                    validation_issues=issues,
                    cartridge=cartridge,
                    llm_client=self._llm_client,
                    prompt_factory=self._prompt_factory,
                    validator=self._validator,
                )
            annotations.append(annotation)
            if progress_callback:
                progress_callback(idx, total)

        result = self._build_result(
            document_id=structure.document_id,
            cartridge_id=cartridge.cartridge_id if cartridge else cartridge_id,
            annotations=annotations,
        )
        result.validation_issues = self._validator.validate(
            result, block_text_by_id, cartridge
        )
        return result

    def _load_cartridge(self, cartridge_id: str | None) -> CartridgeContext | None:
        if not cartridge_id:
            return None
        try:
            return self._cartridge_loader.load(cartridge_id)
        except FileNotFoundError:
            logger.warning(
                "Cartridge '%s' not found; proceeding without cartridge", cartridge_id
            )
            return None

    @staticmethod
    def _build_result(
        document_id: str,
        cartridge_id: str | None,
        annotations: list[BlockRoleAnnotation],
    ) -> RhetoricalRoleResult:
        claim_count = sum(
            1
            for annotation in annotations
            for span in annotation.span_annotations
            if span.is_claim_candidate
        )
        reject_count = sum(
            1
            for annotation in annotations
            for span in annotation.span_annotations
            if span.is_reject_candidate
        )
        return RhetoricalRoleResult(
            document_id=document_id,
            cartridge_id=cartridge_id,
            role_annotations=annotations,
            summary_stats={
                "claim_candidate_spans": claim_count,
                "reject_candidate_spans": reject_count,
            },
        )
