"""ComponentAssemblyAgent: assemble reusable components from claims, equations, thesis, and DSL."""
from __future__ import annotations

import logging

from episteme_graph.agents.claim_qualification.schema import ClaimQualificationResult
from episteme_graph.agents.dsl_linking.schema import DSLLinkingResult
from episteme_graph.agents.equation_semantics.schema import EquationSemanticsResult
from episteme_graph.agents.thesis_reconstruction.schema import ThesisReconstructionResult

from .cartridge_loader import CartridgeLoader
from .enrichment import enrich_component_assembly
from .input_builder import ComponentAssemblyInputBuilder
from .llm_client import ComponentAssemblyLLMClient
from .overlap_cleanup import ComponentOverlapCleanup
from .prompt import ComponentAssemblyPromptFactory
from .repair import ComponentAssemblyRepairer, _parse_raw
from .schema import CartridgeContext, ComponentAssemblyResult
from .validator import ComponentAssemblyValidator

logger = logging.getLogger(__name__)


class ComponentAssemblyAgent:
    def __init__(
        self,
        cartridge_base_dir: str | None = None,
        llm_model: str | None = None,
    ) -> None:
        self._cartridge_loader = CartridgeLoader(cartridge_base_dir)
        self._input_builder = ComponentAssemblyInputBuilder()
        self._prompt_factory = ComponentAssemblyPromptFactory()
        self._llm_client = ComponentAssemblyLLMClient(model=llm_model)
        self._cleanup = ComponentOverlapCleanup()
        self._validator = ComponentAssemblyValidator()
        self._repairer = ComponentAssemblyRepairer(cleanup=self._cleanup)

    def run(
        self,
        qualified_claims: ClaimQualificationResult,
        equations: EquationSemanticsResult | None = None,
        thesis: ThesisReconstructionResult | None = None,
        dsl: DSLLinkingResult | None = None,
        cartridge_id: str | None = None,
        config: dict | None = None,
        claim_objects=None,
        evidence_registry=None,
        derivations=None,
    ) -> ComponentAssemblyResult:
        cartridge = self._load_cartridge(cartridge_id)
        llm_input = self._input_builder.build(
            qualified_claims,
            equations=equations,
            thesis=thesis,
            dsl=dsl,
            cartridge=cartridge,
            config=config,
            claim_objects=claim_objects,
            evidence_registry=evidence_registry,
            derivations=derivations,
        )
        messages = self._prompt_factory.build_messages(llm_input)
        try:
            raw_output = self._llm_client.generate(messages)
        except Exception as exc:
            logger.error("Component assembly failed: %s", exc)
            return ComponentAssemblyResult.make_fallback(
                qualified_claims.document_id, cartridge_id, str(exc)
            )
        result = self._cleanup.cleanup(
            _parse_raw(raw_output, qualified_claims.document_id, llm_input.cartridge_id)
        )
        result = enrich_component_assembly(result, llm_input)
        issues = self._validator.validate(result, cartridge, llm_input=llm_input)
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
            result = enrich_component_assembly(result, llm_input)
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
