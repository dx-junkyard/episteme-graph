"""Tests for ComponentGraphMerger."""
from __future__ import annotations

from episteme_graph.agents.component_graph.merger import ComponentGraphMerger
from episteme_graph.agents.component_graph.schema import (
    GRAPH_SCHEMA_VERSION,
    ComponentGraphEdge,
    ComponentGraphNode,
    ComponentGraphResult,
)


def _node(cid: str) -> ComponentGraphNode:
    return ComponentGraphNode(
        component_id=cid, label=f"L{cid}", component_type="TheoryComponent"
    )


def _edge(source: str, target: str, edge_type: str = "REQUIRES", confidence: float = 0.8) -> ComponentGraphEdge:
    return ComponentGraphEdge(
        edge_id=f"e_{source}_{target}",
        source=source,
        target=target,
        edge_type=edge_type,
        support_status="llm_inferred",
        evidence_claims=[],
        reasoning="",
        confidence=confidence,
    )


def _result(nodes, edges, confidence=0.8) -> ComponentGraphResult:
    return ComponentGraphResult(
        document_id="doc",
        graph_schema_version=GRAPH_SCHEMA_VERSION,
        cartridge_id=None,
        nodes=nodes,
        edges=edges,
        review_notes=[],
        confidence=confidence,
    )


class TestComponentGraphMerger:
    def setup_method(self):
        self.merger = ComponentGraphMerger()

    def test_merge_adds_new_llm_edges(self):
        existing = {"edges": []}
        llm_result = _result(
            [_node("c1"), _node("c2")],
            [_edge("c1", "c2")],
        )
        merged = self.merger.merge(existing, llm_result)
        assert len(merged.edges) == 1
        assert merged.edges[0].source == "c1"

    def test_merge_preserves_existing_edges(self):
        existing = {
            "edges": [
                {
                    "edge_id": "old_edge_001",
                    "source_component_id": "c1",
                    "target_component_id": "c3",
                    "relation": "ENABLES",
                    "support_status": "io_matched",
                    "confidence": 0.75,
                    "evidence": {"reason": "old edge"},
                }
            ]
        }
        llm_result = _result(
            [_node("c1"), _node("c2"), _node("c3")],
            [_edge("c1", "c2")],
        )
        merged = self.merger.merge(existing, llm_result)
        assert len(merged.edges) == 2
        keys = {(e.source, e.target, e.edge_type) for e in merged.edges}
        assert ("c1", "c3", "ENABLES") in keys
        assert ("c1", "c2", "REQUIRES") in keys

    def test_merge_deduplicates_same_key(self):
        existing = {
            "edges": [
                {
                    "source_component_id": "c1",
                    "target_component_id": "c2",
                    "relation": "REQUIRES",
                    "support_status": "io_matched",
                    "confidence": 0.7,
                    "evidence": {},
                }
            ]
        }
        llm_result = _result(
            [_node("c1"), _node("c2")],
            [_edge("c1", "c2", confidence=0.9)],
        )
        merged = self.merger.merge(existing, llm_result)
        assert len(merged.edges) == 1
        # Higher confidence (LLM) should win
        assert merged.edges[0].confidence == 0.9

    def test_merge_reassigns_sequential_ids(self):
        existing = {
            "edges": [
                {
                    "edge_id": "old_001",
                    "source_component_id": "c1",
                    "target_component_id": "c2",
                    "relation": "ENABLES",
                    "support_status": "io_matched",
                    "confidence": 0.7,
                    "evidence": {},
                }
            ]
        }
        llm_result = _result(
            [_node("c1"), _node("c2"), _node("c3")],
            [_edge("c2", "c3")],
        )
        merged = self.merger.merge(existing, llm_result)
        ids = [e.edge_id for e in merged.edges]
        assert ids == ["component_edge_0001", "component_edge_0002"]

    def test_nodes_come_from_llm_result(self):
        existing = {"edges": []}
        llm_result = _result([_node("c1"), _node("c2"), _node("c3")], [])
        merged = self.merger.merge(existing, llm_result)
        assert len(merged.nodes) == 3

    def test_empty_both(self):
        existing = {"edges": []}
        llm_result = _result([], [])
        merged = self.merger.merge(existing, llm_result)
        assert merged.edges == []

    def test_missing_edges_key_in_existing(self):
        existing = {}
        llm_result = _result([_node("c1"), _node("c2")], [_edge("c1", "c2")])
        merged = self.merger.merge(existing, llm_result)
        assert len(merged.edges) == 1
