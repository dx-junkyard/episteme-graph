"""Tests for ComponentGraphAgent schema models."""
from __future__ import annotations

import json

import pytest

from episteme_graph.agents.component_graph.schema import (
    GRAPH_SCHEMA_VERSION,
    ComponentGraphEdge,
    ComponentGraphNode,
    ComponentGraphResult,
    ValidationIssue,
)


def _make_node(component_id: str = "comp_001", label: str = "Test Component") -> ComponentGraphNode:
    return ComponentGraphNode(
        component_id=component_id,
        label=label,
        component_type="TheoryComponent",
        review_status="teacher_review_required",
        display_order=0,
        origin="paper",
    )


def _make_edge(
    source: str = "comp_001",
    target: str = "comp_002",
    edge_type: str = "REQUIRES",
) -> ComponentGraphEdge:
    return ComponentGraphEdge(
        edge_id="component_edge_0001",
        source=source,
        target=target,
        edge_type=edge_type,
        support_status="llm_inferred",
        evidence_claims=["claim:b1:s1"],
        reasoning="Test reasoning",
        confidence=0.85,
    )


def _make_result(
    nodes: list[ComponentGraphNode] | None = None,
    edges: list[ComponentGraphEdge] | None = None,
) -> ComponentGraphResult:
    return ComponentGraphResult(
        document_id="doc_test",
        graph_schema_version=GRAPH_SCHEMA_VERSION,
        cartridge_id="particle_physics",
        nodes=nodes or [_make_node("comp_001"), _make_node("comp_002")],
        edges=edges or [_make_edge()],
        review_notes=[],
        confidence=0.85,
    )


class TestComponentGraphNode:
    def test_basic_fields(self):
        node = _make_node()
        assert node.component_id == "comp_001"
        assert node.component_type == "TheoryComponent"

    def test_defaults(self):
        node = ComponentGraphNode(
            component_id="c1",
            label="L",
            component_type="MethodComponent",
        )
        assert node.review_status == "teacher_review_required"
        assert node.display_order == 0
        assert node.origin == "paper"
        assert node.graph_layer == "main"
        assert node.output_equation_ids == []


class TestComponentGraphEdge:
    def test_basic_fields(self):
        edge = _make_edge()
        assert edge.source == "comp_001"
        assert edge.target == "comp_002"
        assert edge.edge_type == "REQUIRES"
        assert edge.confidence == 0.85

    def test_default_confidence(self):
        edge = ComponentGraphEdge(
            edge_id="e1", source="a", target="b",
            edge_type="ENABLES", support_status="llm_inferred",
            evidence_claims=[], reasoning="",
        )
        assert edge.confidence == 0.75


class TestComponentGraphResult:
    def test_to_dict_roundtrip(self):
        result = _make_result()
        d = result.to_dict()
        assert "nodes" in d
        assert "edges" in d
        assert d["document_id"] == "doc_test"

    def test_to_json_valid(self):
        result = _make_result()
        text = result.to_json()
        parsed = json.loads(text)
        assert parsed["document_id"] == "doc_test"
        assert len(parsed["edges"]) == 1

    def test_to_graph_payload(self):
        result = _make_result()
        payload = result.to_graph_payload()
        assert payload["graph_schema_version"] == GRAPH_SCHEMA_VERSION
        assert len(payload["nodes"]) == 2
        assert payload["nodes"][0]["component_id"] == "comp_001"
        assert "output_equation_ids" in payload["nodes"][0]
        edge = payload["edges"][0]
        assert edge["source_component_id"] == "comp_001"
        assert edge["relation"] == "REQUIRES"
        assert "evidence_claims" in edge["evidence"]
        assert "evidence_equation_ids" in edge["evidence"]

    def test_from_dict(self):
        original = _make_result()
        d = original.to_dict()
        restored = ComponentGraphResult.from_dict(d)
        assert restored.document_id == original.document_id
        assert len(restored.nodes) == len(original.nodes)
        assert len(restored.edges) == len(original.edges)

    def test_make_fallback(self):
        fallback = ComponentGraphResult.make_fallback("doc_x", "pp", "test failure")
        assert fallback.document_id == "doc_x"
        assert fallback.edges == []
        assert any(i.rule_id == "component_graph_failed" for i in fallback.validation_issues)

    def test_make_fallback_with_nodes(self):
        nodes = [_make_node("comp_001")]
        fallback = ComponentGraphResult.make_fallback("doc_x", None, "fail", nodes=nodes)
        assert len(fallback.nodes) == 1

    def test_to_graph_payload_edge_type_is_semantic_not_support_status(self):
        # Issue #266: edge_type should be the semantic relation (REQUIRES, TRANSFORMS…)
        # not the support_status (llm_inferred, dependency_declared…)
        result = _make_result(edges=[
            ComponentGraphEdge(
                edge_id="e1",
                source="comp_001",
                target="comp_002",
                edge_type="TRANSFORMS",
                support_status="llm_inferred",
                evidence_claims=[],
                reasoning="test",
                confidence=0.9,
            )
        ])
        payload = result.to_graph_payload()
        edge = payload["edges"][0]
        assert edge["edge_type"] == "TRANSFORMS", (
            "edge_type in payload must be the semantic relation type, not support_status"
        )
        assert edge["support_status"] == "llm_inferred"
        assert edge["relation"] == "TRANSFORMS"
