"""Merge LLM-inferred edges with existing deterministic edges (Issue #266).

Combines:
  - Deterministic edges from the stored component_graph (input/output matching,
    explicit connectors), already stored in theory_component_graphs.graph_json.
  - LLM-inferred edges from ComponentGraphResult.

Deduplicates by (source, target, edge_type) key, preferring higher-confidence edges.
"""
from __future__ import annotations

import logging

from .schema import (
    GRAPH_SCHEMA_VERSION,
    ComponentGraphEdge,
    ComponentGraphNode,
    ComponentGraphResult,
)

logger = logging.getLogger(__name__)


class ComponentGraphMerger:
    def merge(
        self,
        existing_graph: dict,
        llm_result: ComponentGraphResult,
    ) -> ComponentGraphResult:
        """Merge existing graph edges with LLM result edges.

        Args:
            existing_graph: The stored component_graph.json payload with "nodes"
                and "edges" (from _row_to_component_graph or _build_component_graph_payload).
            llm_result: The ComponentGraphResult produced by the LLM pipeline.

        Returns:
            A merged ComponentGraphResult whose nodes come from llm_result (which
            carries the authoritative component list) and whose edges combine both
            sources, deduplicated by (source, target, edge_type).
        """
        existing_edges = self._parse_existing_edges(existing_graph)
        merged = self._dedup_merge(existing_edges, llm_result.edges)

        return ComponentGraphResult(
            document_id=llm_result.document_id,
            graph_schema_version=GRAPH_SCHEMA_VERSION,
            cartridge_id=llm_result.cartridge_id,
            nodes=llm_result.nodes,
            edges=merged,
            review_notes=llm_result.review_notes,
            confidence=llm_result.confidence,
            validation_issues=llm_result.validation_issues,
        )

    @staticmethod
    def _parse_existing_edges(graph: dict) -> list[ComponentGraphEdge]:
        edges: list[ComponentGraphEdge] = []
        for idx, e in enumerate(graph.get("edges") or []):
            if not isinstance(e, dict):
                continue
            source = str(
                e.get("source") or e.get("source_component_id") or e.get("from") or ""
            ).strip()
            target = str(
                e.get("target") or e.get("target_component_id") or e.get("to") or ""
            ).strip()
            if not source or not target:
                continue
            edge_type = str(
                e.get("edge_type") or e.get("relation") or e.get("type") or "RELATED_TO"
            ).strip().upper()
            support_status = str(e.get("support_status") or "io_matched")
            evidence_obj = e.get("evidence") or {}
            evidence_claims: list[str] = []
            if isinstance(evidence_obj, dict):
                raw = evidence_obj.get("evidence_claims") or []
                if isinstance(raw, list):
                    evidence_claims = [str(c) for c in raw]
            reasoning = ""
            if isinstance(evidence_obj, dict):
                reasoning = str(evidence_obj.get("reason") or "")
            edges.append(ComponentGraphEdge(
                edge_id=str(e.get("edge_id") or f"existing_edge_{idx + 1:04d}"),
                source=source,
                target=target,
                edge_type=edge_type,
                support_status=support_status,
                evidence_claims=evidence_claims,
                reasoning=reasoning,
                confidence=float(e.get("confidence") or 0.7),
            ))
        return edges

    @staticmethod
    def _dedup_merge(
        existing: list[ComponentGraphEdge],
        llm_edges: list[ComponentGraphEdge],
    ) -> list[ComponentGraphEdge]:
        # key: (source, target, edge_type) → best edge
        index: dict[tuple[str, str, str], ComponentGraphEdge] = {}

        for edge in existing:
            key = (edge.source, edge.target, edge.edge_type)
            current = index.get(key)
            if current is None or edge.confidence > current.confidence:
                index[key] = edge

        for edge in llm_edges:
            key = (edge.source, edge.target, edge.edge_type)
            current = index.get(key)
            if current is None or edge.confidence > current.confidence:
                index[key] = edge

        # Reassign sequential IDs and preserve insertion order (existing first, then new LLM edges)
        seen: set[tuple[str, str, str]] = set()
        ordered: list[ComponentGraphEdge] = []
        for source_list in (existing, llm_edges):
            for edge in source_list:
                key = (edge.source, edge.target, edge.edge_type)
                if key not in seen:
                    seen.add(key)
                    ordered.append(index[key])

        result: list[ComponentGraphEdge] = []
        for seq, edge in enumerate(ordered, start=1):
            result.append(ComponentGraphEdge(
                edge_id=f"component_edge_{seq:04d}",
                source=edge.source,
                target=edge.target,
                edge_type=edge.edge_type,
                support_status=edge.support_status,
                evidence_claims=edge.evidence_claims,
                reasoning=edge.reasoning,
                confidence=edge.confidence,
            ))
        return result
