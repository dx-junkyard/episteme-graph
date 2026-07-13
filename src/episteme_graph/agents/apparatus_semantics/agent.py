"""ApparatusSemanticsAgent: identify experimental apparatus/instruments in figures.

Design doc: docs/features/image_pipeline_knowledge_library_design.md §5.

LLM-first, vision-enabled, candidate-only (design principle #2 — "画像の意味
解釈は candidate 止まり、確定は人間"): the agent never asserts a confirmed
apparatus identification. Cartridge-aware but not cartridge-dependent (works
with ``cartridge_id=None``), and library-aware but not library-dependent
(``library_candidates`` empty/None degrades to novel/unknown identification —
design principle #5).
"""
from __future__ import annotations

import logging

from .cartridge_loader import CartridgeLoader
from .input_builder import ApparatusSemanticsInputBuilder
from .llm_client import ApparatusSemanticsLLMClient
from .prompt import ApparatusSemanticsPromptFactory
from .repair import ApparatusSemanticsRepairer, _fallback_record, _parse_record
from .schema import (
    ApparatusSemanticsResult,
    CartridgeContext,
    FigureImageInput,
    LibraryCandidate,
)
from .validator import ApparatusSemanticsValidator

logger = logging.getLogger(__name__)


class ApparatusSemanticsAgent:
    def __init__(
        self,
        cartridge_id: str | None = None,
        llm_client: ApparatusSemanticsLLMClient | None = None,
        cartridge_loader: CartridgeLoader | None = None,
    ) -> None:
        self._default_cartridge_id = cartridge_id
        self._cartridge_loader = cartridge_loader or CartridgeLoader()
        self._input_builder = ApparatusSemanticsInputBuilder()
        self._prompt_factory = ApparatusSemanticsPromptFactory()
        self._llm_client = llm_client or ApparatusSemanticsLLMClient()
        self._validator = ApparatusSemanticsValidator()
        self._repairer = ApparatusSemanticsRepairer()

    def run(
        self,
        *,
        document_id: str,
        figures: list[FigureImageInput],
        library_candidates: dict[str, list[LibraryCandidate]] | None = None,
        cartridge_id: str | None = None,
    ) -> ApparatusSemanticsResult:
        resolved_cartridge_id = cartridge_id or self._default_cartridge_id
        cartridge = self._load_cartridge(resolved_cartridge_id)
        library_candidates = library_candidates or {}

        records = []
        for figure in figures:
            candidates = list(library_candidates.get(figure.figure_id) or [])

            # Image unavailable → never call the LLM; keep a reviewable
            # 'unknown' record instead of dropping the figure (P4).
            if not figure.image_bytes:
                records.append(_fallback_record(figure, "image_unavailable"))
                continue

            image_payload = self._input_builder.build_image_payload(figure)
            candidate_briefs = self._input_builder.build_candidate_briefs(candidates)
            nearby_text = self._input_builder.build_nearby_text(figure)
            cartridge_hints = self._input_builder.build_cartridge_hints(cartridge)
            messages = self._prompt_factory.build_messages(
                figure, candidate_briefs, nearby_text, cartridge_hints,
            )

            try:
                raw_output = self._llm_client.generate(messages, images=[image_payload])
            except Exception as exc:
                logger.exception(
                    "apparatus_semantics LLM call failed document=%s figure=%s",
                    document_id, figure.figure_id,
                )
                records.append(_fallback_record(figure, f"llm_call_failed: {exc}"))
                continue

            candidate_ids = {c.entry_id for c in candidates}
            record = _parse_record(raw_output, figure, candidates)
            issues = self._validator.validate_record(
                record, figure=figure, candidate_ids=candidate_ids,
            )
            if [i for i in issues if i.severity == "error"]:
                logger.warning(
                    "apparatus_semantics validation repair document=%s figure=%s errors=%d",
                    document_id, figure.figure_id,
                    len([i for i in issues if i.severity == "error"]),
                )
                record = self._repairer.repair(
                    figure=figure,
                    candidates=candidates,
                    candidate_briefs=candidate_briefs,
                    nearby_text=nearby_text,
                    cartridge_hints=cartridge_hints,
                    raw_output=raw_output,
                    validation_issues=issues,
                    llm_client=self._llm_client,
                    prompt_factory=self._prompt_factory,
                    validator=self._validator,
                    image_payload=image_payload,
                )
            records.append(record)

        result = ApparatusSemanticsResult(
            document_id=document_id,
            cartridge_id=cartridge.cartridge_id if cartridge else resolved_cartridge_id,
            apparatus_records=records,
        )
        result.validation_issues = self._validator.validate(result)
        return result

    def _load_cartridge(self, cartridge_id: str | None) -> CartridgeContext | None:
        if not cartridge_id:
            return None
        try:
            return self._cartridge_loader.load(cartridge_id)
        except FileNotFoundError:
            logger.warning(
                "Cartridge '%s' not found; proceeding without cartridge (apparatus_semantics)",
                cartridge_id,
            )
            return None
