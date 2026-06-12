"""Build NarrativeAnnotator LLM input (issue #360).

Packages the main-layer graph (nodes / edges), the reconstructed thesis text,
and the teaching takeaways already produced by DerivationChain so the LLM can
mostly quote and arrange existing material instead of inventing new content.
"""
from __future__ import annotations

from .schema import NarrativeLLMInput

_MAX_MAIN_NODES = 16
_MAX_MAIN_EDGES = 24


class NarrativeInputBuilder:
    def build(
        self,
        graph,
        thesis=None,
        derivations=None,
        cartridge=None,
        config: dict | None = None,
    ) -> NarrativeLLMInput:
        cfg = config or {}
        takeaways = self._takeaways_by_derivation(derivations)
        main_nodes = self._main_nodes(
            graph, takeaways, int(cfg.get("max_main_nodes", _MAX_MAIN_NODES))
        )
        main_node_ids = {n["component_id"] for n in main_nodes}
        main_edges = self._main_edges(
            graph, main_node_ids, int(cfg.get("max_main_edges", _MAX_MAIN_EDGES))
        )
        return NarrativeLLMInput(
            document_id=str(getattr(graph, "document_id", "") or ""),
            cartridge_id=cartridge.cartridge_id if cartridge else getattr(graph, "cartridge_id", None),
            central_thesis_text=self._thesis_text(thesis),
            main_nodes=main_nodes,
            main_edges=main_edges,
            normalized_terms=self._normalized_terms(cartridge) if cartridge else None,
        )

    @staticmethod
    def _thesis_text(thesis) -> str:
        central = getattr(thesis, "central_thesis", None)
        if isinstance(central, dict):
            return str(central.get("text") or "")
        return ""

    @staticmethod
    def _takeaways_by_derivation(derivations) -> dict[str, str]:
        takeaways: dict[str, str] = {}
        for chain in getattr(derivations, "chains", []) or []:
            derivation_id = str(getattr(chain, "derivation_id", "") or "")
            takeaway = str(getattr(chain, "teaching_takeaway", "") or "")
            if derivation_id and takeaway:
                takeaways[derivation_id] = takeaway
        return takeaways

    @staticmethod
    def _main_nodes(graph, takeaways: dict[str, str], limit: int) -> list[dict]:
        nodes = []
        for node in getattr(graph, "nodes", []) or []:
            if str(getattr(node, "graph_layer", "main") or "main") != "main":
                continue
            node_takeaways = [
                takeaways[d]
                for d in (getattr(node, "linked_derivation_ids", []) or [])
                if d in takeaways
            ]
            nodes.append({
                "component_id": node.component_id,
                "label": node.label,
                "description": getattr(node, "description", ""),
                "operation": getattr(node, "operation", ""),
                "source_backing_status": getattr(node, "source_backing_status", ""),
                "linked_equation_ids": list(getattr(node, "linked_equation_ids", []) or []),
                "linked_claim_ids": list(getattr(node, "linked_claim_ids", []) or []),
                "teaching_takeaways": node_takeaways,
                "display_order": int(getattr(node, "display_order", 0) or 0),
            })
            if len(nodes) >= limit:
                break
        nodes.sort(key=lambda n: (n["display_order"], n["component_id"]))
        return nodes

    @staticmethod
    def _main_edges(graph, main_node_ids: set[str], limit: int) -> list[dict]:
        edges = []
        for edge in getattr(graph, "edges", []) or []:
            source = str(getattr(edge, "source", "") or "")
            target = str(getattr(edge, "target", "") or "")
            if source not in main_node_ids or target not in main_node_ids:
                continue
            edges.append({
                "edge_id": edge.edge_id,
                "source": source,
                "target": target,
                "edge_type": getattr(edge, "edge_type", ""),
                "reasoning": getattr(edge, "reasoning", ""),
            })
            if len(edges) >= limit:
                break
        return edges

    @staticmethod
    def _normalized_terms(cartridge) -> list[dict]:
        terms: list[dict] = []
        if getattr(cartridge, "aliases", None):
            for canonical, aliases in cartridge.aliases.items():
                terms.append({"canonical": canonical, "aliases": aliases})
        return terms
