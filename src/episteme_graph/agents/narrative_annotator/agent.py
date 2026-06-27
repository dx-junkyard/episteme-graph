"""NarrativeAnnotator: reader-facing narrative for the main graph (issue #360).

LLM-first, annotation-only: the agent reads the TheoryOperationGraph and adds
``narrative_role`` / ``transition_text`` / ``graph_summary`` as a SEPARATE
artifact. It never modifies the graph itself — that invariant is asserted by
snapshot comparison and reported as a hard error if it ever breaks. All output
is provisional (maturity_source="llm_proposed").
"""
from __future__ import annotations

import logging

from .cartridge_loader import CartridgeContext, CartridgeLoader
from .input_builder import NarrativeInputBuilder
from .llm_client import NarrativeLLMClient
from .prompt import NarrativePromptFactory
from .repair import NarrativeRepairer, _parse_raw
from .schema import NarrativeAnnotationResult, ValidationIssue
from .validator import NarrativeValidator, verify_graph_unchanged

logger = logging.getLogger(__name__)


class NarrativeAnnotator:
    def __init__(
        self,
        cartridge_base_dir: str | None = None,
        llm_model: str | None = None,
    ) -> None:
        self._cartridge_loader = CartridgeLoader(cartridge_base_dir)
        self._input_builder = NarrativeInputBuilder()
        self._prompt_factory = NarrativePromptFactory()
        self._llm_client = NarrativeLLMClient(model=llm_model)
        self._validator = NarrativeValidator()
        self._repairer = NarrativeRepairer()

    def run(
        self,
        graph,
        thesis=None,
        derivations=None,
        cartridge_id: str | None = None,
        config: dict | None = None,
    ) -> NarrativeAnnotationResult:
        cartridge = self._load_cartridge(cartridge_id)
        document_id = str(getattr(graph, "document_id", "") or "")
        graph_snapshot = graph.to_dict() if hasattr(graph, "to_dict") else None

        llm_input = self._input_builder.build(
            graph, thesis=thesis, derivations=derivations,
            cartridge=cartridge, config=config,
        )
        if not llm_input.main_nodes:
            return NarrativeAnnotationResult.make_fallback(
                document_id, cartridge_id, "graph has no main-layer nodes"
            )

        messages = self._prompt_factory.build_messages(llm_input, cartridge)
        try:
            raw_output = self._llm_client.generate(messages)
        except Exception as exc:
            logger.error("Narrative annotation failed: %s", exc)
            return NarrativeAnnotationResult.make_fallback(
                document_id, cartridge_id, str(exc)
            )

        result = _parse_raw(raw_output, llm_input)
        issues = self._validator.validate(result, llm_input)
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

        self._assert_graph_unchanged(graph, graph_snapshot, result)
        return result

    @staticmethod
    def _assert_graph_unchanged(graph, snapshot, result: NarrativeAnnotationResult) -> None:
        if snapshot is None or not hasattr(graph, "to_dict"):
            return
        if verify_graph_unchanged(snapshot, graph.to_dict()):
            return
        result.validation_issues = list(result.validation_issues or []) + [
            ValidationIssue(
                rule_id="graph_mutated_by_annotator",
                severity="error",
                message="annotation must not modify the graph; structure changed",
                field="graph",
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
