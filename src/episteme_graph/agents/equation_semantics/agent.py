"""EquationSemanticsAgent: recover local semantic roles for equation blocks.

Issue #245: candidates → EquationAcceptanceGate → LLM semantics の3段パイプライン。
"""
from __future__ import annotations

import logging

from episteme_graph.agents.document_structure.schema import DocumentStructureResult
from episteme_graph.agents.paper_skeleton.schema import PaperSkeletonResult
from episteme_graph.agents.rhetorical_role.schema import RhetoricalRoleResult

from .acceptance_gate import EquationAcceptanceGate
from .cartridge_loader import CartridgeLoader
from .input_builder import EquationSemanticsInputBuilder
from .llm_client import EquationSemanticsLLMClient
from .prompt import EquationSemanticsPromptFactory
from .repair import EquationSemanticsRepairer, _fallback_record, _parse_record
from .schema import (
    CartridgeContext,
    EquationCandidate,
    EquationRecord,
    EquationSemanticsResult,
)
from .validator import EquationSemanticsValidator

logger = logging.getLogger(__name__)


class EquationSemanticsAgent:
    def __init__(
        self,
        cartridge_base_dir: str | None = None,
        llm_model: str | None = None,
    ) -> None:
        self._cartridge_loader = CartridgeLoader(cartridge_base_dir)
        self._input_builder = EquationSemanticsInputBuilder()
        self._acceptance_gate = EquationAcceptanceGate()
        self._prompt_factory = EquationSemanticsPromptFactory()
        self._llm_client = EquationSemanticsLLMClient(model=llm_model)
        self._validator = EquationSemanticsValidator()
        self._repairer = EquationSemanticsRepairer()

    def run(
        self,
        structure: DocumentStructureResult,
        skeleton: PaperSkeletonResult | None = None,
        roles: RhetoricalRoleResult | None = None,
        cartridge_id: str | None = None,
        config: dict | None = None,
        progress_callback=None,
    ) -> EquationSemanticsResult:
        cartridge = self._load_cartridge(cartridge_id)

        # Step 1: 候補生成 (全 equation_block から EquationCandidate を作る)
        candidates = self._input_builder.build_candidates(
            structure, skeleton=skeleton, roles=roles, cartridge=cartridge, config=config
        )
        if not candidates:
            return EquationSemanticsResult.make_fallback(
                structure.document_id, cartridge_id, None, "No equation blocks for semantics"
            )

        # Step 2: Acceptance gate (accepted / rejected / needs_merge / context_only を分類)
        classified_candidates = self._acceptance_gate.process(candidates)
        accepted = [c for c in classified_candidates if c.acceptance_status == "accepted"]

        if not accepted:
            logger.info(
                "document=%s: all %d equation candidates were rejected by acceptance gate",
                structure.document_id,
                len(classified_candidates),
            )
            result = EquationSemanticsResult(
                document_id=structure.document_id,
                cartridge_id=cartridge.cartridge_id if cartridge else cartridge_id,
                equation_candidates=classified_candidates,
                equations=[],
            )
            result.validation_issues = self._validator.validate(result, cartridge)
            return result

        # Step 3: LLM 入力構築 (accepted candidates のみ)
        llm_inputs = self._input_builder.build_llm_inputs(
            structure, accepted, skeleton=skeleton, roles=roles, cartridge=cartridge
        )

        # Step 4: LLM semantics analysis
        candidate_by_block_id = {c.source_location.get("block_id"): c for c in accepted}
        records: list[EquationRecord] = []
        total = len(llm_inputs)

        for idx, llm_input in enumerate(llm_inputs, start=1):
            candidate = candidate_by_block_id.get(llm_input.block_id)
            messages = self._prompt_factory.build_messages(llm_input, cartridge)
            try:
                raw_output = self._llm_client.generate(messages)
            except Exception as exc:
                logger.exception(
                    "Equation semantics failed for document=%s block_id=%s",
                    structure.document_id,
                    llm_input.block_id,
                )
                records.append(_fallback_record(llm_input, candidate, str(exc)))
                if progress_callback:
                    progress_callback(idx, total)
                continue

            record = _parse_record(raw_output, llm_input, candidate)
            partial = EquationSemanticsResult(
                document_id=structure.document_id,
                cartridge_id=llm_input.cartridge_id,
                equation_candidates=[],
                equations=[record],
            )
            issues = self._validator.validate(partial, cartridge)
            if [i for i in issues if i.severity == "error"]:
                logger.warning(
                    "Equation semantics validation repair for document=%s block_id=%s errors=%d",
                    structure.document_id,
                    llm_input.block_id,
                    len([i for i in issues if i.severity == "error"]),
                )
                record = self._repairer.repair(
                    llm_input=llm_input,
                    candidate=candidate,
                    raw_output=raw_output,
                    validation_issues=issues,
                    cartridge=cartridge,
                    llm_client=self._llm_client,
                    prompt_factory=self._prompt_factory,
                    validator=self._validator,
                )
            # accepted_equation_id を candidate に書き込む
            if candidate:
                candidate.accepted_equation_id = record.equation_id
            records.append(record)
            if progress_callback:
                progress_callback(idx, total)

        result = EquationSemanticsResult(
            document_id=structure.document_id,
            cartridge_id=cartridge.cartridge_id if cartridge else cartridge_id,
            equation_candidates=classified_candidates,
            equations=records,
        )
        result.validation_issues = self._validator.validate(result, cartridge)
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
