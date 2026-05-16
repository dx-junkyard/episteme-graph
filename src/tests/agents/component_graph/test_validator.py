"""Tests for ComponentGraphValidator."""
from __future__ import annotations

import pytest

from episteme_graph.agents.component_graph.schema import (
    GRAPH_SCHEMA_VERSION,
    ComponentGraphEdge,
    ComponentGraphNode,
    ComponentGraphResult,
)
from episteme_graph.agents.component_graph.validator import ComponentGraphValidator


def _node(cid: str) -> ComponentGraphNode:
    return ComponentGraphNode(
        component_id=cid,
        label=f"Label {cid}",
        component_type="TheoryComponent",
    )


def _edge(
    source: str,
    target: str,
    edge_type: str = "REQUIRES",
    edge_id: str = "component_edge_0001",
    confidence: float = 0.8,
) -> ComponentGraphEdge:
    return ComponentGraphEdge(
        edge_id=edge_id,
        source=source,
        target=target,
        edge_type=edge_type,
        support_status="llm_inferred",
        evidence_claims=["claim:b1:s1"],
        reasoning="test",
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


class TestComponentGraphValidator:
    def setup_method(self):
        self.validator = ComponentGraphValidator()

    def test_valid_result(self):
        nodes = [_node("c1"), _node("c2")]
        edges = [_edge("c1", "c2")]
        issues = self.validator.validate(_result(nodes, edges))
        errors = [i for i in issues if i.severity == "error"]
        assert errors == []

    def test_unknown_source(self):
        nodes = [_node("c1"), _node("c2")]
        edges = [_edge("unknown_comp", "c2")]
        issues = self.validator.validate(_result(nodes, edges))
        assert any(i.rule_id == "unknown_source_component" for i in issues)

    def test_unknown_target(self):
        nodes = [_node("c1"), _node("c2")]
        edges = [_edge("c1", "unknown_comp")]
        issues = self.validator.validate(_result(nodes, edges))
        assert any(i.rule_id == "unknown_target_component" for i in issues)

    def test_self_loop(self):
        nodes = [_node("c1"), _node("c2")]
        edges = [_edge("c1", "c1")]
        issues = self.validator.validate(_result(nodes, edges))
        assert any(i.rule_id == "self_loop" for i in issues)

    def test_invalid_edge_type(self):
        nodes = [_node("c1"), _node("c2")]
        edges = [_edge("c1", "c2", edge_type="NONSENSE_TYPE")]
        issues = self.validator.validate(_result(nodes, edges))
        assert any(i.rule_id == "invalid_edge_type" for i in issues)

    def test_duplicate_edge(self):
        nodes = [_node("c1"), _node("c2")]
        edges = [
            _edge("c1", "c2", edge_id="e1"),
            _edge("c1", "c2", edge_id="e2"),
        ]
        issues = self.validator.validate(_result(nodes, edges))
        assert any(i.rule_id == "duplicate_edge" for i in issues)

    def test_missing_source_empty_string(self):
        nodes = [_node("c1"), _node("c2")]
        edges = [_edge("", "c2")]
        issues = self.validator.validate(_result(nodes, edges))
        assert any(i.rule_id == "missing_source" for i in issues)

    def test_missing_target_empty_string(self):
        nodes = [_node("c1"), _node("c2")]
        edges = [_edge("c1", "")]
        issues = self.validator.validate(_result(nodes, edges))
        assert any(i.rule_id == "missing_target" for i in issues)

    def test_confidence_out_of_range(self):
        nodes = [_node("c1"), _node("c2")]
        issues = self.validator.validate(_result(nodes, [], confidence=1.5))
        assert any(i.rule_id == "confidence_out_of_range" for i in issues)

    def test_edge_confidence_out_of_range(self):
        nodes = [_node("c1"), _node("c2")]
        edges = [_edge("c1", "c2", confidence=1.5)]
        issues = self.validator.validate(_result(nodes, edges))
        assert any(i.rule_id == "edge_confidence_out_of_range" for i in issues)

    def test_multiple_valid_edges(self):
        nodes = [_node("c1"), _node("c2"), _node("c3")]
        edges = [
            _edge("c1", "c2", edge_id="e1"),
            _edge("c2", "c3", edge_id="e2", edge_type="ENABLES"),
        ]
        issues = self.validator.validate(_result(nodes, edges))
        errors = [i for i in issues if i.severity == "error"]
        assert errors == []

    def test_llm_inferred_empty_evidence_claims_warns(self):
        """llm_inferred edge with no evidence_claims emits a warning."""
        nodes = [_node("c1"), _node("c2")]
        edge = ComponentGraphEdge(
            edge_id="component_edge_0001",
            source="c1",
            target="c2",
            edge_type="REQUIRES",
            support_status="llm_inferred",
            evidence_claims=[],
            reasoning="",
            confidence=0.8,
        )
        issues = self.validator.validate(_result(nodes, [edge]))
        assert any(i.rule_id == "llm_inferred_no_evidence" for i in issues)
        assert all(i.severity != "error" for i in issues if i.rule_id == "llm_inferred_no_evidence")

    def test_non_llm_inferred_empty_evidence_claims_no_warn(self):
        """io_matched edge with empty evidence_claims does NOT trigger the warning."""
        nodes = [_node("c1"), _node("c2")]
        edge = ComponentGraphEdge(
            edge_id="component_edge_0001",
            source="c1",
            target="c2",
            edge_type="REQUIRES",
            support_status="io_matched",
            evidence_claims=[],
            reasoning="",
            confidence=0.8,
        )
        issues = self.validator.validate(_result(nodes, [edge]))
        assert not any(i.rule_id == "llm_inferred_no_evidence" for i in issues)

    def test_llm_inferred_with_evidence_claims_no_warn(self):
        """llm_inferred edge that has evidence_claims does NOT trigger the warning."""
        nodes = [_node("c1"), _node("c2")]
        edges = [_edge("c1", "c2")]  # _edge has evidence_claims=["claim:b1:s1"]
        issues = self.validator.validate(_result(nodes, edges))
        assert not any(i.rule_id == "llm_inferred_no_evidence" for i in issues)
