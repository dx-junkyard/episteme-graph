"""ContextualExplanationAgent: two-layer explanations for pipeline elements.

Design: ``docs/features/hierarchical_context_explanation_design.md`` §5.1
(Phase 2, migration 056). LLM-first, cartridge-optional. Consumes
already-resolved ``ElementExplanationInput`` objects (the orchestrator — a
later phase — is responsible for resolving upper/lower structure into text;
this agent never resolves opaque ids itself, design principle E4) and emits
per-element ``contextual_explanation`` / ``generic_explanation`` candidates
for a teacher to later confirm.

Batching (design §5.1): multiple elements are packed into one LLM call
(``max_elements_per_call``, default 8) to bound cost. Repair targets only the
elements that still fail validation after the initial call, so a single
stubborn element does not discard already-correct sibling explanations in
the same batch (see ``repair.py``). Elements that still cannot be explained
after repair are kept in the result with ``skipped_reason`` set — never
dropped (P4).
"""
from __future__ import annotations

import logging

from .cartridge_loader import CartridgeContext, CartridgeLoader
from .input_builder import ContextualExplanationInputBuilder
from .llm_client import ContextualExplanationLLMClient
from .prompt import ContextualExplanationPromptFactory
from .repair import ContextualExplanationRepairer, error_element_ids, parse_batch_output
from .schema import (
    ContextualExplanationResult,
    DEFAULT_MAX_ELEMENTS_PER_CALL,
    ElementExplanationInput,
    ElementExplanationResult,
    SKIPPED_REASON_LLM_CALL_FAILED,
    SKIPPED_REASON_REPAIR_FAILED,
    ValidationIssue,
)
from .validator import ContextualExplanationValidator

logger = logging.getLogger(__name__)


class ContextualExplanationAgent:
    """Generate contextual (+ optional generic) explanations for elements."""

    def __init__(
        self,
        cartridge_base_dir: str | None = None,
        llm_model: str | None = None,
        max_elements_per_call: int = DEFAULT_MAX_ELEMENTS_PER_CALL,
    ) -> None:
        self._cartridge_loader = CartridgeLoader(cartridge_base_dir)
        self._input_builder = ContextualExplanationInputBuilder()
        self._prompt_factory = ContextualExplanationPromptFactory(self._input_builder)
        self._llm_client = ContextualExplanationLLMClient(model=llm_model)
        self._validator = ContextualExplanationValidator()
        self._repairer = ContextualExplanationRepairer()
        self._max_elements_per_call = max_elements_per_call

    def run(
        self,
        elements: list[ElementExplanationInput],
        cartridge_id: str | None = None,
        config: dict | None = None,
    ) -> ContextualExplanationResult:
        cartridge = self._load_cartridge(cartridge_id)
        resolved_cartridge_id = cartridge.cartridge_id if cartridge else cartridge_id
        cfg = config or {}
        max_per_call = int(cfg.get("max_elements_per_call") or self._max_elements_per_call)
        if max_per_call < 1:
            max_per_call = DEFAULT_MAX_ELEMENTS_PER_CALL

        if not elements:
            return ContextualExplanationResult(
                elements=[],
                cartridge_id=resolved_cartridge_id,
                review_notes=["no elements provided"],
            )

        batches = self._input_builder.build_batches(elements, max_per_call)
        all_results: list[ElementExplanationResult] = []
        all_issues: list[ValidationIssue] = []
        call_count = 0

        for batch in batches:
            batch_results, batch_issues, calls = self._run_batch(batch, cartridge)
            all_results.extend(batch_results)
            all_issues.extend(batch_issues)
            call_count += calls

        return ContextualExplanationResult(
            elements=all_results,
            cartridge_id=resolved_cartridge_id,
            llm_call_count=call_count,
            validation_issues=all_issues,
        )

    def _run_batch(
        self,
        batch: list[ElementExplanationInput],
        cartridge: CartridgeContext | None,
    ) -> tuple[list[ElementExplanationResult], list[ValidationIssue], int]:
        by_id = {item.element_id: item for item in batch}
        messages = self._prompt_factory.build_messages(batch, cartridge)
        try:
            raw = self._llm_client.generate(messages)
        except Exception as exc:
            logger.exception(
                "Contextual explanation batch generation failed for %d element(s)",
                len(batch),
            )
            fallback = [
                ElementExplanationResult.make_skipped(item, str(exc), SKIPPED_REASON_LLM_CALL_FAILED)
                for item in batch
            ]
            return fallback, [], 1

        results = parse_batch_output(raw, batch)
        issues = self._validator.validate(results, batch)
        pending_ids = {eid for eid in error_element_ids(issues) if eid in by_id}
        finished: dict[str, ElementExplanationResult] = {
            r.element_id: r
            for r in results
            if r.element_id in by_id and r.element_id not in pending_ids
        }
        calls = 1

        if pending_ids:
            logger.warning(
                "Contextual explanation validation requires repair for %d/%d element(s)",
                len(pending_ids), len(batch),
            )
            repaired, repair_calls = self._repairer.repair(
                batch=batch,
                pending_ids=pending_ids,
                raw_output=raw,
                validation_issues=issues,
                cartridge=cartridge,
                llm_client=self._llm_client,
                prompt_factory=self._prompt_factory,
                validator=self._validator,
            )
            calls += repair_calls
            for result in repaired:
                finished[result.element_id] = result

        ordered = [
            finished.get(item.element_id)
            or ElementExplanationResult.make_skipped(
                item, "no output produced", SKIPPED_REASON_REPAIR_FAILED
            )
            for item in batch
        ]
        return ordered, issues, calls

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
