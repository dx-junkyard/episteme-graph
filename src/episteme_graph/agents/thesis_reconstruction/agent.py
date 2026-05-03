"""ThesisReconstructionAgent: reconstruct central thesis and support structure."""
from __future__ import annotations

import logging

from episteme_graph.agents.claim_qualification.schema import ClaimQualificationResult
from episteme_graph.agents.equation_semantics.schema import EquationSemanticsResult
from episteme_graph.agents.paper_skeleton.schema import PaperSkeletonResult

from .cartridge_loader import CartridgeLoader
from .input_builder import ThesisReconstructionInputBuilder
from .llm_client import ThesisReconstructionLLMClient
from .prompt import ThesisReconstructionPromptFactory
from .repair import ThesisReconstructionRepairer, _parse_raw
from .schema import CartridgeContext, ThesisReconstructionResult
from .validator import ThesisReconstructionValidator

logger = logging.getLogger(__name__)


class ThesisReconstructionAgent:
    def __init__(
        self,
        cartridge_base_dir: str | None = None,
        llm_model: str | None = None,
    ) -> None:
        self._cartridge_loader = CartridgeLoader(cartridge_base_dir)
        self._input_builder = ThesisReconstructionInputBuilder()
        self._prompt_factory = ThesisReconstructionPromptFactory()
        self._llm_client = ThesisReconstructionLLMClient(model=llm_model)
        self._validator = ThesisReconstructionValidator()
        self._repairer = ThesisReconstructionRepairer()

    def run(
        self,
        skeleton: PaperSkeletonResult,
        qualified_claims: ClaimQualificationResult,
        equations: EquationSemanticsResult | None = None,
        cartridge_id: str | None = None,
        config: dict | None = None,
    ) -> ThesisReconstructionResult:
        cartridge = self._load_cartridge(cartridge_id)
        llm_input = self._input_builder.build(
            skeleton,
            qualified_claims,
            equations=equations,
            cartridge=cartridge,
            config=config,
        )
        messages = self._prompt_factory.build_messages(llm_input, cartridge)
        try:
            raw_output = self._llm_client.generate(messages)
        except Exception as exc:
            logger.error("Thesis reconstruction failed: %s", exc)
            return ThesisReconstructionResult.make_fallback(
                skeleton.document_id, cartridge_id, str(exc)
            )

        result = _parse_raw(raw_output, skeleton.document_id, llm_input.cartridge_id)
        issues = self._validator.validate(result, cartridge)
        if [i for i in issues if i.severity == "error"]:
            result = self._repairer.repair(
                llm_input=llm_input,
                raw_output=raw_output,
                validation_issues=issues,
                cartridge=cartridge,
                llm_client=self._llm_client,
                prompt_factory=self._prompt_factory,
                validator=self._validator,
            )
        else:
            result.validation_issues = issues
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
