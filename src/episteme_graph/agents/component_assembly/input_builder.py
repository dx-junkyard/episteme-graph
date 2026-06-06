"""Build ComponentAssemblyAgent LLM input."""
from __future__ import annotations

from episteme_graph.agents.claim_qualification.schema import ClaimQualificationResult
from episteme_graph.agents.dsl_linking.schema import DSLLinkingResult
from episteme_graph.agents.equation_semantics.schema import EquationSemanticsResult
from episteme_graph.agents.thesis_reconstruction.schema import ThesisReconstructionResult
from episteme_graph.agents.id_canonicalization import (
    canonical_claim_id_for_span,
    canonicalize_claim_refs,
    legacy_claim_id_for_span,
)

from .schema import (
    CORE_COMPONENT_TYPES,
    CORE_DEPENDENCY_TYPES,
    CartridgeContext,
    ComponentAssemblyLLMInput,
)

_MAX_CLAIMS = 48
_MAX_EQUATIONS = 24
_MAX_DSL_NODES = 48
_MAX_DSL_EDGES = 64
_MAX_THESIS_NODES = 24


class ComponentAssemblyInputBuilder:
    def build(
        self,
        qualified_claims: ClaimQualificationResult,
        equations: EquationSemanticsResult | None = None,
        thesis: ThesisReconstructionResult | None = None,
        dsl: DSLLinkingResult | None = None,
        cartridge: CartridgeContext | None = None,
        config: dict | None = None,
        claim_objects=None,
        evidence_registry=None,
        derivations=None,
    ) -> ComponentAssemblyLLMInput:
        cfg = config or {}
        accepted_claims = self._accepted_claims(
            qualified_claims,
            int(cfg.get("max_claims", _MAX_CLAIMS)),
            claim_objects=claim_objects,
        )
        claim_aliases = {
            c["legacy_claim_id"]: c["claim_id"]
            for c in accepted_claims
            if c.get("legacy_claim_id") and c.get("claim_id") != c.get("legacy_claim_id")
        }
        return ComponentAssemblyLLMInput(
            document_id=qualified_claims.document_id,
            cartridge_id=cartridge.cartridge_id if cartridge else qualified_claims.cartridge_id,
            accepted_claims=accepted_claims,
            equations=self._equations(equations, int(cfg.get("max_equations", _MAX_EQUATIONS))),
            thesis_nodes=self._thesis_nodes(
                thesis,
                int(cfg.get("max_thesis_nodes", _MAX_THESIS_NODES)),
                claim_objects=claim_objects,
                claim_aliases=claim_aliases,
            ),
            dsl_nodes=self._dsl_nodes(
                dsl,
                int(cfg.get("max_dsl_nodes", _MAX_DSL_NODES)),
                claim_objects=claim_objects,
                claim_aliases=claim_aliases,
            ),
            dsl_edges=self._dsl_edges(
                dsl,
                int(cfg.get("max_dsl_edges", _MAX_DSL_EDGES)),
                claim_objects=claim_objects,
                claim_aliases=claim_aliases,
            ),
            allowed_component_types=self.allowed_component_types(cartridge),
            allowed_dependency_types=self.allowed_dependency_types(cartridge),
            normalized_terms=self._normalized_terms(cartridge) if cartridge else None,
            available_claims=self._available_claims(claim_objects),
            available_evidence=self._available_evidence(evidence_registry),
            available_equations=self._available_equations_index(equations),
            available_dsl_nodes=self._available_dsl_nodes(dsl),
            available_dsl_edges=self._available_dsl_edges(dsl),
            available_derivation_ids=self._available_derivation_ids(derivations),
        )

    @staticmethod
    def allowed_component_types(cartridge: CartridgeContext | None) -> list[str]:
        values = list(CORE_COMPONENT_TYPES)
        if cartridge:
            raw = cartridge.component_types.get("component_types", [])
            for item in raw:
                if isinstance(item, dict) and item.get("id"):
                    values.append(str(item["id"]))
                elif isinstance(item, str):
                    values.append(item)
        return sorted(set(values))

    @staticmethod
    def allowed_dependency_types(cartridge: CartridgeContext | None) -> list[str]:
        values = list(CORE_DEPENDENCY_TYPES)
        if cartridge:
            raw = cartridge.relation_types.get("relation_types", [])
            for item in raw:
                if isinstance(item, dict) and item.get("id"):
                    values.append(str(item["id"]).lower())
                elif isinstance(item, str):
                    values.append(item.lower())
        return sorted(set(values))

    @staticmethod
    def _accepted_claims(
        result: ClaimQualificationResult,
        limit: int,
        claim_objects=None,
    ) -> list[dict]:
        claims = []
        for record in result.qualified_spans[:limit]:
            claims.append({
                "claim_id": canonical_claim_id_for_span(record, claim_objects),
                "legacy_claim_id": legacy_claim_id_for_span(record),
                "span_id": record.span_id,
                "block_id": record.block_id,
                "section_id": record.section_id,
                "text": record.text,
                "role_labels": record.role_labels,
                "claim_tier": record.qualification.get("claim_tier"),
                "claim_type_candidate": record.qualification.get("claim_type_candidate"),
                "reason": record.reason,
                "confidence": record.confidence,
            })
        return claims

    @staticmethod
    def _equations(equations: EquationSemanticsResult | None, limit: int) -> list[dict]:
        if not equations:
            return []
        result = []
        for record in equations.equations[:limit]:
            src = record.source_extraction
            sem = record.semantics
            cp = getattr(record, "confidence_policy", None)
            block_id = src.source_location.get("block_id", "")
            result.append({
                "equation_id": record.equation_id,
                "block_id": block_id,
                "section_id": src.source_location.get("section_id"),
                "label": record.label,
                "latex": src.latex,
                "plain_text": src.plain_text,
                "role": sem.equation_type,
                "secondary_types": list(sem.secondary_types),
                "semantic_status": sem.semantic_status,
                "summary": sem.summary,
                "defined_symbols": [
                    {"symbol": s.symbol, "definition_status": s.definition_status}
                    for s in sem.defined_symbols
                ],
                "used_symbols": list(sem.used_symbols),
                "local_assumptions": list(sem.assumptions),
                "from_equations": list(sem.input_equation_ids),
                "to_equations": list(sem.output_equation_ids),
                "source_evidence_ids": list(sem.source_evidence_ids),
                "linked_claim_ids": list(sem.linked_claim_ids),
                "review_flags": list(sem.review_flags),
                "needs_math_review": bool(src.needs_math_review),
                "extraction_status": src.extraction_status,
                "reconstruction_status": getattr(record.reconstruction, "status", "none"),
                "confidence_policy": {
                    "can_support_claim": bool(getattr(cp, "can_support_claim", False)),
                    "can_be_used_in_derivation": bool(getattr(cp, "can_be_used_in_derivation", False)),
                    "can_be_rendered_as_final_formula": bool(getattr(cp, "can_be_rendered_as_final_formula", False)),
                    "allowed_downstream_use": str(getattr(cp, "allowed_downstream_use", "semantic_hint_only")),
                    "can_be_displayed_in_course": bool(getattr(cp, "can_be_displayed_in_course", True)),
                    "display_requires_note": bool(getattr(cp, "display_requires_note", True)),
                    "must_not_treat_as_source_extracted": bool(getattr(cp, "must_not_treat_as_source_extracted", True)),
                },
                "confidence": sem.confidence,
            })
        return result

    @staticmethod
    def _thesis_nodes(
        thesis: ThesisReconstructionResult | None,
        limit: int,
        claim_objects=None,
        claim_aliases: dict[str, str] | None = None,
    ) -> list[dict]:
        if not thesis:
            return []
        nodes = [{
            "thesis_ref": "central_thesis",
            "text": thesis.central_thesis.get("text", ""),
            "claim_ids": canonicalize_claim_refs(
                {"claim_ids": thesis.central_thesis.get("claim_ids", [])},
                claim_objects,
                claim_aliases,
            ).get("claim_ids", []),
            "equation_ids": thesis.central_thesis.get("equation_ids", []),
            "kind": "central_thesis",
        }]
        for section, entries in (thesis.support_structure or {}).items():
            if not isinstance(entries, list):
                continue
            for idx, entry in enumerate(entries):
                nodes.append({
                    "thesis_ref": f"support:{section}:{idx}",
                    "text": entry.get("text", ""),
                    "claim_ids": canonicalize_claim_refs(
                        {"claim_ids": entry.get("claim_ids", [])},
                        claim_objects,
                        claim_aliases,
                    ).get("claim_ids", []),
                    "equation_ids": entry.get("equation_ids", []),
                    "kind": section,
                    "support_type": entry.get("support_type"),
                })
                if len(nodes) >= limit:
                    return nodes
        return nodes[:limit]

    @staticmethod
    def _dsl_nodes(
        dsl: DSLLinkingResult | None,
        limit: int,
        claim_objects=None,
        claim_aliases: dict[str, str] | None = None,
    ) -> list[dict]:
        if not dsl:
            return []
        return [
            {
                "node_id": node.node_id,
                "node_type": node.node_type,
                "node_value": node.node_value,
                "source_kind": node.source_kind,
                "source_refs": canonicalize_claim_refs(
                    node.source_refs,
                    claim_objects,
                    claim_aliases,
                ),
                "confidence": node.confidence,
            }
            for node in dsl.nodes[:limit]
        ]

    @staticmethod
    def _dsl_edges(
        dsl: DSLLinkingResult | None,
        limit: int,
        claim_objects=None,
        claim_aliases: dict[str, str] | None = None,
    ) -> list[dict]:
        if not dsl:
            return []
        return [
            {
                "edge_id": edge.edge_id,
                "from_node_id": edge.from_node_id,
                "to_node_id": edge.to_node_id,
                "core_predicate": edge.core_predicate,
                "domain_verb": edge.domain_verb,
                "polarity": edge.polarity,
                "evidence_refs": canonicalize_claim_refs(
                    edge.evidence_refs,
                    claim_objects,
                    claim_aliases,
                ),
                "confidence": edge.confidence,
            }
            for edge in dsl.edges[:limit]
        ]

    @staticmethod
    def _normalized_terms(cartridge: CartridgeContext) -> list[dict]:
        terms = []
        if cartridge.aliases:
            for canonical, aliases in cartridge.aliases.items():
                terms.append({"canonical": canonical, "aliases": aliases})
        return terms

    @staticmethod
    def _available_claims(claim_objects) -> list[dict]:
        """Build available claim ID list from ClaimObjectBuildResult."""
        if not claim_objects:
            return []
        result = []
        for claim in getattr(claim_objects, "claims", []) or []:
            result.append({
                "claim_id": claim.claim_id,
                "claim_type": claim.claim_type,
                "text": claim.text[:200],
                "source_evidence_ids": list(claim.source_evidence_ids or []),
                "equation_ids": list(claim.equation_ids or []),
                "support_status": claim.support_status,
                "review_status": claim.review_status,
            })
        return result

    @staticmethod
    def _available_evidence(evidence_registry) -> list[dict]:
        """Build available evidence ID list from EvidenceRegistryResult."""
        if not evidence_registry:
            return []
        result = []
        for record in getattr(evidence_registry, "records", []) or []:
            src = getattr(record, "source", None)
            result.append({
                "evidence_id": record.evidence_id,
                "block_id": src.block_id if src else None,
                "evidence_role": record.evidence_role,
            })
        return result

    @staticmethod
    def _available_equations_index(equations) -> list[dict]:
        """Build available equation ID list from EquationSemanticsResult."""
        if not equations:
            return []
        result = []
        for record in getattr(equations, "equations", []) or []:
            src = record.source_extraction
            sem = record.semantics
            cp = getattr(record, "confidence_policy", None)
            result.append({
                "equation_id": record.equation_id,
                "block_id": src.source_location.get("block_id", ""),
                "role": sem.equation_type,
                "semantic_status": sem.semantic_status,
                "needs_math_review": bool(src.needs_math_review),
                "review_flags": list(sem.review_flags),
                "reconstruction_status": getattr(record.reconstruction, "status", "none"),
                "confidence_policy": {
                    "can_support_claim": bool(getattr(cp, "can_support_claim", False)),
                    "can_be_used_in_derivation": bool(getattr(cp, "can_be_used_in_derivation", False)),
                    "can_be_rendered_as_final_formula": bool(getattr(cp, "can_be_rendered_as_final_formula", False)),
                    "allowed_downstream_use": str(getattr(cp, "allowed_downstream_use", "semantic_hint_only")),
                    "can_be_displayed_in_course": bool(getattr(cp, "can_be_displayed_in_course", True)),
                    "display_requires_note": bool(getattr(cp, "display_requires_note", True)),
                    "must_not_treat_as_source_extracted": bool(getattr(cp, "must_not_treat_as_source_extracted", True)),
                },
                "confidence": sem.confidence,
            })
        return result

    @staticmethod
    def _available_dsl_nodes(dsl) -> list[dict]:
        if not dsl:
            return []
        return [{"node_id": n.node_id} for n in dsl.nodes]

    @staticmethod
    def _available_dsl_edges(dsl) -> list[dict]:
        if not dsl:
            return []
        return [{"edge_id": e.edge_id} for e in dsl.edges]

    @staticmethod
    def _available_derivation_ids(derivations) -> list[str]:
        if not derivations:
            return []
        ids = []
        for chain in getattr(derivations, "chains", []) or []:
            # DerivationChainRecord uses derivation_id, not chain_id (issue #262)
            chain_id = getattr(chain, "derivation_id", None) or getattr(chain, "chain_id", None)
            if chain_id:
                ids.append(chain_id)
            for step in getattr(chain, "steps", []) or []:
                step_id = getattr(step, "step_id", None)
                if step_id:
                    ids.append(step_id)
        return ids
