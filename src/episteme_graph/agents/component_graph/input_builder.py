"""Build LLM input for ComponentGraphAgent (Issue #266).

Generates the 4 prompt-context materials defined in the issue:
  Material 1: Component I/O and declared dependencies
  Material 2: Cross-component DSL micro-relations
  Material 3: Derivation chains spanning multiple components
  Material 4: Evidence text for referenced claims / evidence IDs
"""
from __future__ import annotations

from episteme_graph.agents.component_assembly.schema import ComponentAssemblyResult
from episteme_graph.agents.derivation_chain.schema import DerivationChainResult
from episteme_graph.agents.dsl_linking.schema import DSLLinkingResult

from .schema import (
    VALID_EDGE_TYPES,
    CartridgeContext,
    ComponentGraphLLMInput,
    ComponentGraphNode,
)

_MAX_COMPONENTS = 32
_MAX_CROSS_EDGES = 48
_MAX_DERIVATIONS = 16
_MAX_CLAIM_TEXTS = 64


class ComponentGraphInputBuilder:
    def build(
        self,
        components: ComponentAssemblyResult,
        dsl: DSLLinkingResult | None = None,
        derivations: DerivationChainResult | None = None,
        claims: list[dict] | None = None,
        evidence_snippets: list[dict] | None = None,
        cartridge: CartridgeContext | None = None,
        config: dict | None = None,
    ) -> ComponentGraphLLMInput:
        cfg = config or {}
        max_comps = int(cfg.get("max_components", _MAX_COMPONENTS))
        max_cross = int(cfg.get("max_cross_edges", _MAX_CROSS_EDGES))
        max_derivs = int(cfg.get("max_derivations", _MAX_DERIVATIONS))
        max_claims = int(cfg.get("max_claim_texts", _MAX_CLAIM_TEXTS))

        comp_list = components.components[:max_comps]
        component_ids = [c.component_id for c in comp_list]

        # Reverse map: dsl_node_id → component_id (for cross-edge detection)
        node_to_comp: dict[str, str] = {}
        for comp in comp_list:
            for nid in getattr(comp, "linked_dsl_node_ids", []) or []:
                node_to_comp[nid] = comp.component_id

        material1 = self._build_material1(comp_list)
        material2 = self._build_material2(dsl, node_to_comp, max_cross)
        material3 = self._build_material3(derivations, set(component_ids), max_derivs)
        claim_texts, evidence_texts = self._build_material4(
            material1, material2, material3, claims, evidence_snippets, max_claims
        )
        valid_edge_types = self._valid_edge_types(cartridge)

        return ComponentGraphLLMInput(
            document_id=components.document_id,
            cartridge_id=cartridge.cartridge_id if cartridge else components.cartridge_id,
            components=material1,
            cross_component_dsl_edges=material2,
            multi_component_derivations=material3,
            claim_texts=claim_texts,
            evidence_texts=evidence_texts,
            valid_edge_types=valid_edge_types,
            component_ids=component_ids,
        )

    def build_nodes(self, components: ComponentAssemblyResult) -> list[ComponentGraphNode]:
        nodes = []
        for idx, comp in enumerate(components.components):
            label = str(getattr(comp, "label", "") or "").strip()
            if not label:
                label = str(getattr(comp, "summary", "") or "").strip()[:80] or comp.component_id
            component_type = str(getattr(comp, "component_type", "") or "").strip() or "UnknownComponent"
            nodes.append(ComponentGraphNode(
                component_id=comp.component_id,
                label=label,
                component_type=component_type,
                review_status=getattr(comp, "review_status", "teacher_review_required"),
                display_order=idx,
                origin="paper",
            ))
        return nodes

    # ------------------------------------------------------------------ #
    # Material builders
    # ------------------------------------------------------------------ #

    @staticmethod
    def _build_material1(comp_list) -> list[dict]:
        result = []
        for comp in comp_list:
            result.append({
                "component_id": comp.component_id,
                "name": comp.label,
                "summary": comp.summary,
                "inputs": list(comp.inputs or []),
                "outputs": list(comp.outputs or []),
                "input_equation_ids": list(getattr(comp, "input_equation_ids", []) or []),
                "intermediate_equation_ids": list(getattr(comp, "intermediate_equation_ids", []) or []),
                "output_equation_ids": list(getattr(comp, "output_equation_ids", []) or []),
                "constraint_equation_ids": list(getattr(comp, "constraint_equation_ids", []) or []),
                "definition_equation_ids": list(getattr(comp, "definition_equation_ids", []) or []),
                "review_required_equation_ids": list(getattr(comp, "review_required_equation_ids", []) or []),
                "eliminated_symbols": list(getattr(comp, "eliminated_symbols", []) or []),
                "retained_symbols": list(getattr(comp, "retained_symbols", []) or []),
                "equation_confidence_summary": dict(getattr(comp, "equation_confidence_summary", {}) or {}),
                "dependencies": [
                    {
                        "dependency_type": d.get("dependency_type") if isinstance(d, dict) else "",
                        "component_refs": d.get("component_refs", []) if isinstance(d, dict) else [],
                        "reason": d.get("reason", "") if isinstance(d, dict) else "",
                    }
                    for d in (comp.dependencies or [])
                ],
                "linked_claim_ids": list(getattr(comp, "linked_claim_ids", []) or []),
                "linked_equation_ids": list(getattr(comp, "linked_equation_ids", []) or []),
                "linked_dsl_node_ids": list(getattr(comp, "linked_dsl_node_ids", []) or []),
                "linked_dsl_edge_ids": list(getattr(comp, "linked_dsl_edge_ids", []) or []),
                "linked_derivation_ids": list(getattr(comp, "linked_derivation_ids", []) or []),
            })
        return result

    @staticmethod
    def _build_material2(
        dsl: DSLLinkingResult | None,
        node_to_comp: dict[str, str],
        limit: int,
    ) -> list[dict]:
        if not dsl:
            return []
        cross_edges = []
        for edge in dsl.edges:
            src_comp = node_to_comp.get(edge.from_node_id)
            tgt_comp = node_to_comp.get(edge.to_node_id)
            if not src_comp or not tgt_comp or src_comp == tgt_comp:
                continue
            cross_edges.append({
                "edge_id": edge.edge_id,
                "from_node_id": edge.from_node_id,
                "from_component_id": src_comp,
                "to_node_id": edge.to_node_id,
                "to_component_id": tgt_comp,
                "core_predicate": edge.core_predicate,
                "domain_verb": edge.domain_verb,
                "polarity": edge.polarity,
                "evidence_refs": edge.evidence_refs,
                "confidence": edge.confidence,
            })
            if len(cross_edges) >= limit:
                break
        return cross_edges

    @staticmethod
    def _build_material3(
        derivations: DerivationChainResult | None,
        component_id_set: set[str],
        limit: int,
    ) -> list[dict]:
        if not derivations:
            return []
        result = []
        for chain in derivations.chains:
            linked = [
                cid for cid in (chain.linked_component_ids or [])
                if cid in component_id_set
            ]
            if len(linked) < 2:
                continue
            step_summaries = [
                {
                    "step_id": s.step_id,
                    "operation": s.operation,
                    "input_equation_ids": list(s.input_equation_ids or []),
                    "output_equation_ids": list(s.output_equation_ids or []),
                    "required_claim_ids": list(s.required_claim_ids or []),
                    "assumption_ids": list(s.assumption_ids or []),
                    "source_evidence_ids": list(s.source_evidence_ids or []),
                }
                for s in chain.steps[:8]
            ]
            result.append({
                "derivation_id": chain.derivation_id,
                "linked_component_ids": linked,
                "chain_type": chain.chain_type,
                "steps": step_summaries,
            })
            if len(result) >= limit:
                break
        return result

    @staticmethod
    def _build_material4(
        material1: list[dict],
        material2: list[dict],
        material3: list[dict],
        claims: list[dict] | None,
        evidence_snippets: list[dict] | None,
        limit: int,
    ) -> tuple[dict[str, str], dict[str, str]]:
        needed_claim_ids: set[str] = set()
        needed_evidence_ids: set[str] = set()

        for comp in material1:
            needed_claim_ids.update(comp.get("linked_claim_ids") or [])

        for edge in material2:
            refs = edge.get("evidence_refs") or {}
            if isinstance(refs, dict):
                needed_claim_ids.update(refs.get("claim_ids") or [])

        for chain in material3:
            for step in chain.get("steps") or []:
                needed_claim_ids.update(step.get("required_claim_ids") or [])
                needed_evidence_ids.update(step.get("source_evidence_ids") or [])

        claim_texts: dict[str, str] = {}
        if claims:
            for claim in claims:
                cid = claim.get("claim_id") or claim.get("id")
                if cid and cid in needed_claim_ids and cid not in claim_texts:
                    text = claim.get("text") or claim.get("claim_text") or ""
                    claim_texts[cid] = str(text)[:300]
                if len(claim_texts) >= limit:
                    break

        evidence_texts: dict[str, str] = {}
        if evidence_snippets:
            for snippet in evidence_snippets:
                eid = snippet.get("evidence_id") or snippet.get("id")
                if eid and eid in needed_evidence_ids and eid not in evidence_texts:
                    text = snippet.get("evidence_text") or snippet.get("text") or ""
                    evidence_texts[eid] = str(text)[:300]

        return claim_texts, evidence_texts

    @staticmethod
    def _valid_edge_types(cartridge: CartridgeContext | None) -> list[str]:
        types = list(VALID_EDGE_TYPES)
        if cartridge:
            raw = cartridge.relation_types.get("relation_types") or []
            for item in raw:
                if isinstance(item, dict) and item.get("id"):
                    types.append(str(item["id"]).upper())
                elif isinstance(item, str):
                    types.append(item.upper())
        return sorted(set(types))
