"""ComponentGraphAgent: hybrid deterministic/LLM edge-building pipeline (Issue #266).

Builds the `edges` section of component_graph.json by integrating:
  - Deterministic edges from existing component graph (I/O matching, connectors)
  - LLM-inferred edges using 4-material prompt context

Pipeline:
  1. InputBuilder  → extract Materials 1–4 from upstream artifacts
  2. PromptFactory → build LLM messages
  3. LLMClient     → generate edge proposals (JSON)
  4. _parse_raw    → deserialize to ComponentGraphResult
  5. Validator     → check schema / referential integrity
  6. Repairer      → retry on errors
  7. Merger        → combine with existing deterministic edges
"""
from __future__ import annotations

import logging

from episteme_graph.agents.component_assembly.schema import ComponentAssemblyResult
from episteme_graph.agents.derivation_chain.schema import DerivationChainResult
from episteme_graph.agents.dsl_linking.schema import DSLLinkingResult

from .cartridge_loader import CartridgeLoader
from .input_builder import ComponentGraphInputBuilder
from .llm_client import ComponentGraphLLMClient
from .merger import ComponentGraphMerger
from .normalizer import ComponentGraphNormalizer
from .prompt import ComponentGraphPromptFactory
from .repair import ComponentGraphRepairer, _parse_raw
from .schema import CartridgeContext, ComponentGraphResult
from .validator import ComponentGraphValidator

logger = logging.getLogger(__name__)


def _first_text(*values) -> str:
    """Return the first value that is a non-empty string (ignore lists/dicts)."""
    for value in values:
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _build_claim_index(claims: list[dict] | None) -> dict[str, dict]:
    """Build ``claim_id -> metadata`` for atomic-claim preference (issue #306).

    The normalizer uses ``text`` / ``evidence_text`` / ``is_atomic`` / level to
    decide whether a claim is strong (atomic) backing for a theory operation.
    """
    index: dict[str, dict] = {}
    for claim in claims or []:
        if not isinstance(claim, dict):
            continue
        cid = str(claim.get("claim_id") or claim.get("id") or "").strip()
        if not cid:
            continue
        index[cid] = {
            "text": str(claim.get("text") or claim.get("claim_text") or ""),
            "predicate": str(claim.get("predicate") or ""),
            "evidence_text": _first_text(
                claim.get("evidence_text"),
                claim.get("source_span"),
                claim.get("source_text"),
            ),
            "claim_level": str(
                claim.get("claim_level") or claim.get("level") or claim.get("granularity") or ""
            ),
            "is_atomic": claim.get("is_atomic"),
        }
    return index


class ComponentGraphAgent:
    """Build a component dependency graph with LLM-inferred edges."""

    def __init__(
        self,
        cartridge_base_dir: str | None = None,
        llm_model: str | None = None,
    ) -> None:
        self._cartridge_loader = CartridgeLoader(cartridge_base_dir)
        self._input_builder = ComponentGraphInputBuilder()
        self._prompt_factory = ComponentGraphPromptFactory()
        self._llm_client = ComponentGraphLLMClient(model=llm_model)
        self._validator = ComponentGraphValidator()
        self._repairer = ComponentGraphRepairer()
        self._merger = ComponentGraphMerger()
        self._normalizer = ComponentGraphNormalizer()

    def run(
        self,
        components: ComponentAssemblyResult,
        dsl: DSLLinkingResult | None = None,
        derivations: DerivationChainResult | None = None,
        claims: list[dict] | None = None,
        evidence_snippets: list[dict] | None = None,
        existing_graph: dict | None = None,
        cartridge_id: str | None = None,
        config: dict | None = None,
    ) -> ComponentGraphResult:
        """Run the edge-building pipeline.

        Args:
            components: ComponentAssemblyResult (nodes source of truth).
            dsl: DSLLinkingResult for cross-component micro-relation detection.
            derivations: DerivationChainResult for equation/claim chain edges.
            claims: Flat claim dict list (claim_id, text, …) for Material 4.
            evidence_snippets: Evidence snippet dicts for Material 4.
            existing_graph: Existing component_graph.json payload (optional).
                If provided, its edges are merged with the LLM output.
            cartridge_id: Active cartridge ID for vocabulary constraints.
            config: Runtime config overrides (max_components, max_cross_edges, …).

        Returns:
            ComponentGraphResult with merged nodes and edges.
        """
        cartridge = self._load_cartridge(cartridge_id)
        nodes = self._input_builder.build_nodes(components)

        llm_input = self._input_builder.build(
            components=components,
            dsl=dsl,
            derivations=derivations,
            claims=claims,
            evidence_snippets=evidence_snippets,
            cartridge=cartridge,
            config=config,
        )

        if not llm_input.component_ids:
            logger.warning("ComponentGraphAgent: no components to connect; skipping LLM call")
            return ComponentGraphResult.make_fallback(
                components.document_id, cartridge_id, "no components", nodes
            )

        messages = self._prompt_factory.build_messages(llm_input)
        try:
            raw_output = self._llm_client.generate(messages)
        except Exception as exc:
            logger.error("ComponentGraphAgent LLM call failed: %s", exc)
            return ComponentGraphResult.make_fallback(
                components.document_id, cartridge_id, str(exc), nodes
            )

        result = _parse_raw(raw_output, components.document_id, llm_input.cartridge_id, nodes)
        issues = self._validator.validate(result, cartridge, llm_input=llm_input)

        if [i for i in issues if i.severity == "error"]:
            result = self._repairer.repair(
                llm_input=llm_input,
                raw_output=raw_output,
                validation_issues=issues,
                cartridge=cartridge,
                nodes=nodes,
                llm_client=self._llm_client,
                prompt_factory=self._prompt_factory,
                validator=self._validator,
            )
        else:
            result.validation_issues = issues

        if existing_graph:
            result = self._merger.merge(existing_graph, result)

        result = self._normalizer.normalize(
            result, components, derivations, claim_index=_build_claim_index(claims)
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
