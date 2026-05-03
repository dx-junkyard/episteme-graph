"""Deterministic cleanup for DSL graphs."""
from __future__ import annotations

from .schema import DSLEdge, DSLLinkingResult, DSLNode


class DSLGraphCleanup:
    def cleanup(self, result: DSLLinkingResult) -> DSLLinkingResult:
        nodes, id_map = self._dedupe_nodes(result.nodes)
        edges = self._dedupe_edges(result.edges, id_map, {n.node_id for n in nodes})
        result.nodes = nodes
        result.edges = edges
        return result

    @staticmethod
    def _dedupe_nodes(nodes: list[DSLNode]) -> tuple[list[DSLNode], dict[str, str]]:
        seen: dict[tuple[str, str], DSLNode] = {}
        id_map: dict[str, str] = {}
        output: list[DSLNode] = []
        for node in nodes:
            key = (node.node_type, node.node_value.strip().lower())
            if key in seen:
                canonical = seen[key]
                id_map[node.node_id] = canonical.node_id
                canonical.source_refs = _merge_refs(canonical.source_refs, node.source_refs)
                canonical.confidence = max(canonical.confidence, node.confidence)
            else:
                seen[key] = node
                id_map[node.node_id] = node.node_id
                output.append(node)
        return output, id_map

    @staticmethod
    def _dedupe_edges(
        edges: list[DSLEdge],
        id_map: dict[str, str],
        node_ids: set[str],
    ) -> list[DSLEdge]:
        seen: dict[tuple[str, str, str, str], DSLEdge] = {}
        output: list[DSLEdge] = []
        for edge in edges:
            edge.from_node_id = id_map.get(edge.from_node_id, edge.from_node_id)
            edge.to_node_id = id_map.get(edge.to_node_id, edge.to_node_id)
            if edge.from_node_id == edge.to_node_id:
                continue
            if edge.from_node_id not in node_ids or edge.to_node_id not in node_ids:
                continue
            key = (
                edge.from_node_id,
                edge.to_node_id,
                edge.core_predicate,
                edge.domain_verb,
            )
            if key in seen:
                canonical = seen[key]
                canonical.evidence_refs = _merge_refs(canonical.evidence_refs, edge.evidence_refs)
                canonical.confidence = max(canonical.confidence, edge.confidence)
            else:
                seen[key] = edge
                output.append(edge)
        for idx, edge in enumerate(output, start=1):
            if not edge.edge_id:
                edge.edge_id = f"e{idx}"
        return output


def _merge_refs(a: dict, b: dict) -> dict:
    merged = dict(a or {})
    for key in ("claim_ids", "equation_ids", "thesis_refs"):
        values = []
        values.extend(merged.get(key, []) or [])
        values.extend((b or {}).get(key, []) or [])
        merged[key] = sorted({str(v) for v in values})
    return merged
