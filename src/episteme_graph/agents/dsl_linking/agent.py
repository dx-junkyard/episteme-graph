"""DSLLinkingAgent: connect claims, equations, and thesis into a DSL graph."""
from __future__ import annotations

import logging

from episteme_graph.agents.claim_qualification.schema import ClaimQualificationResult
from episteme_graph.agents.equation_semantics.schema import EquationSemanticsResult
from episteme_graph.agents.thesis_reconstruction.schema import ThesisReconstructionResult
from episteme_graph.agents.claim_selection import selection_issue_payloads
from episteme_graph.agents.id_canonicalization import (
    canonicalize_claim_refs,
    claim_aliases_from_accepted_claims,
)

from .graph_cleanup import DSLGraphCleanup
from .input_builder import DSLLinkingInputBuilder
from .llm_client import DSLLinkingLLMClient
from .prompt import DSLLinkingPromptFactory
from .repair import DSLLinkingRepairer, _parse_raw, make_deterministic_fallback
from .schema import DSLLinkingResult, ValidationIssue
from .validator import DSLLinkingValidator

logger = logging.getLogger(__name__)


class DSLLinkingAgent:
    def __init__(self, llm_model: str | None = None) -> None:
        self._input_builder = DSLLinkingInputBuilder()
        self._prompt_factory = DSLLinkingPromptFactory()
        self._llm_client = DSLLinkingLLMClient(model=llm_model)
        self._cleanup = DSLGraphCleanup()
        self._validator = DSLLinkingValidator()
        self._repairer = DSLLinkingRepairer(cleanup=self._cleanup)

    def run(
        self,
        qualified_claims: ClaimQualificationResult,
        equations: EquationSemanticsResult | None = None,
        thesis: ThesisReconstructionResult | None = None,
        config: dict | None = None,
        claim_objects=None,
    ) -> DSLLinkingResult:
        llm_input = self._input_builder.build(
            qualified_claims,
            equations=equations,
            thesis=thesis,
            config=config,
            claim_objects=claim_objects,
        )
        messages = self._prompt_factory.build_messages(llm_input)
        try:
            raw_output = self._llm_client.generate(messages)
        except Exception as exc:
            logger.error("DSL linking failed: %s", exc)
            result = make_deterministic_fallback(llm_input, str(exc))
            self._record_claim_exclusions(result, llm_input)
            return result

        raw_output = canonicalize_claim_refs(
            raw_output,
            claim_objects,
            claim_aliases_from_accepted_claims(llm_input.accepted_claims),
        )
        result = self._cleanup.cleanup(_parse_raw(raw_output, qualified_claims.document_id))
        issues = self._validator.validate(result)
        if [i for i in issues if i.severity == "error"]:
            result = self._repairer.repair(
                llm_input=llm_input,
                raw_output=raw_output,
                validation_issues=issues,
                llm_client=self._llm_client,
                prompt_factory=self._prompt_factory,
                validator=self._validator,
            )
        else:
            result.validation_issues = issues
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
            for payload in selection_issue_payloads(excluded, stage="dsl_linking")
        ]
