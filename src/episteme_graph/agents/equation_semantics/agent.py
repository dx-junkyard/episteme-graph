"""EquationSemanticsAgent: recover local semantic roles for equation blocks."""
from __future__ import annotations

import logging

from episteme_graph.agents.document_structure.schema import DocumentStructureResult
from episteme_graph.agents.paper_skeleton.schema import PaperSkeletonResult
from episteme_graph.agents.rhetorical_role.schema import RhetoricalRoleResult

from .cartridge_loader import CartridgeLoader
from .input_builder import EquationSemanticsInputBuilder
from .llm_client import EquationSemanticsLLMClient
from .prompt import EquationSemanticsPromptFactory
from .repair import EquationSemanticsRepairer, _fallback_record, _parse_record
from .schema import CartridgeContext, EquationSemanticsRecord, EquationSemanticsResult
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
        llm_inputs = self._input_builder.build(
            structure, skeleton=skeleton, roles=roles, cartridge=cartridge, config=config
        )
        if not llm_inputs:
            return EquationSemanticsResult.make_fallback(
                structure.document_id, cartridge_id, None, "No equation blocks for semantics"
            )

        records: list[EquationSemanticsRecord] = []
        total = len(llm_inputs)
        for idx, llm_input in enumerate(llm_inputs, start=1):
            messages = self._prompt_factory.build_messages(llm_input, cartridge)
            try:
                raw_output = self._llm_client.generate(messages)
            except Exception as exc:
                logger.exception(
                    "Equation semantics failed for document=%s block_id=%s cartridge=%s",
                    structure.document_id,
                    llm_input.block_id,
                    llm_input.cartridge_id,
                )
                records.append(_fallback_record(llm_input, str(exc)))
                if progress_callback:
                    progress_callback(idx, total)
                continue

            record = _parse_record(raw_output, llm_input)
            partial = EquationSemanticsResult(
                document_id=structure.document_id,
                cartridge_id=llm_input.cartridge_id,
                equations=[record],
            )
            issues = self._validator.validate(partial, cartridge)
            if [i for i in issues if i.severity == "error"]:
                logger.warning(
                    "Equation semantics validation repair required for document=%s block_id=%s errors=%d",
                    structure.document_id,
                    llm_input.block_id,
                    len([i for i in issues if i.severity == "error"]),
                )
                record = self._repairer.repair(
                    llm_input=llm_input,
                    raw_output=raw_output,
                    validation_issues=issues,
                    cartridge=cartridge,
                    llm_client=self._llm_client,
                    prompt_factory=self._prompt_factory,
                    validator=self._validator,
                )
            records.append(record)
            if progress_callback:
                progress_callback(idx, total)

        result = EquationSemanticsResult(
            document_id=structure.document_id,
            cartridge_id=cartridge.cartridge_id if cartridge else cartridge_id,
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
