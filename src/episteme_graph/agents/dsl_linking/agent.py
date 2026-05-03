"""DSLLinkingAgent: connect claims, equations, and thesis into a DSL graph."""
from __future__ import annotations

import logging

from episteme_graph.agents.claim_qualification.schema import ClaimQualificationResult
from episteme_graph.agents.equation_semantics.schema import EquationSemanticsResult
from episteme_graph.agents.thesis_reconstruction.schema import ThesisReconstructionResult

from .graph_cleanup import DSLGraphCleanup
from .input_builder import DSLLinkingInputBuilder
from .llm_client import DSLLinkingLLMClient
from .prompt import DSLLinkingPromptFactory
from .repair import DSLLinkingRepairer, _parse_raw
from .schema import DSLLinkingResult
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
    ) -> DSLLinkingResult:
        llm_input = self._input_builder.build(
            qualified_claims, equations=equations, thesis=thesis, config=config
        )
        messages = self._prompt_factory.build_messages(llm_input)
        try:
            raw_output = self._llm_client.generate(messages)
        except Exception as exc:
            logger.error("DSL linking failed: %s", exc)
            return DSLLinkingResult.make_fallback(qualified_claims.document_id, str(exc))

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
        return result
