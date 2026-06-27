"""ThesisReconstructionAgent: reconstruct central thesis and support structure."""
from __future__ import annotations

import logging

from episteme_graph.agents.claim_qualification.schema import ClaimQualificationResult
from episteme_graph.agents.equation_semantics.schema import EquationSemanticsResult
from episteme_graph.agents.paper_skeleton.schema import PaperSkeletonResult
from episteme_graph.agents.claim_selection import selection_issue_payloads
from episteme_graph.agents.id_canonicalization import (
    canonicalize_claim_refs,
    claim_aliases_from_accepted_claims,
)

from .cartridge_loader import CartridgeLoader
from .input_builder import ThesisReconstructionInputBuilder
from .llm_client import ThesisReconstructionLLMClient
from .prompt import ThesisReconstructionPromptFactory
from .repair import ThesisReconstructionRepairer, _parse_raw
from .schema import CartridgeContext, ThesisReconstructionResult, ValidationIssue
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
        claim_objects=None,
    ) -> ThesisReconstructionResult:
        cartridge = self._load_cartridge(cartridge_id)
        llm_input = self._input_builder.build(
            skeleton,
            qualified_claims,
            equations=equations,
            cartridge=cartridge,
            config=config,
            claim_objects=claim_objects,
        )
        messages = self._prompt_factory.build_messages(llm_input, cartridge)
        try:
            raw_output = self._llm_client.generate(messages)
        except Exception as exc:
            logger.error("Thesis reconstruction failed: %s", exc)
            result = ThesisReconstructionResult.make_fallback(
                skeleton.document_id, cartridge_id, str(exc)
            )
            result.finalize_traversal_fields(
                central_question_hint=llm_input.central_question,
                headline_claim_hint=llm_input.headline_claim,
            )
            self._record_claim_exclusions(result, llm_input)
            return result

        raw_output = canonicalize_claim_refs(
            raw_output,
            claim_objects,
            claim_aliases_from_accepted_claims(llm_input.accepted_claims),
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
        # Issue #442: populate the traversal-anchor description fields
        # deterministically (anchor_node_ids are linked after DSL linking).
        result.finalize_traversal_fields(
            central_question_hint=llm_input.central_question,
            headline_claim_hint=llm_input.headline_claim,
        )
        self._record_claim_exclusions(result, llm_input)
        return result

    @staticmethod
    def _record_claim_exclusions(result, llm_input) -> None:
        """Persist limit-dropped claims and surface them as warnings (#356)."""
        excluded = list(getattr(llm_input, "excluded_from_pipeline_input", []) or [])
        if not excluded:
            return
        result.excluded_from_pipeline_input = excluded
        result.validation_issues = list(result.validation_issues or []) + [
            ValidationIssue(**payload)
            for payload in selection_issue_payloads(
                excluded, stage="thesis_reconstruction"
            )
        ]

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
