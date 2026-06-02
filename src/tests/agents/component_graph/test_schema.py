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


class TestClassifyOperation:
    """Domain-independent operation → (verb, edge_type, is_generic) mapping (#302)."""

    @pytest.mark.parametrize("operation,edge_type", [
        ("define_kernel", "defines"),
        ("construct_system", "constructs"),
        ("linearize_moment_equations", "linearizes"),
        ("solve_second_order_bias", "solves"),
        ("substitute_parameter", "substitutes"),
        ("eliminate_bias", "eliminates"),
        ("derive_consistency_relation", "derives"),
        ("constrain_gravity", "constrains"),
        ("diagnose_modified_gravity", "diagnoses"),
        ("compare_models", "compares"),
        ("apply_criterion", "diagnoses"),
        ("introduce_observable", "defines"),
    ])
    def test_concrete_operations_map_to_edge_types(self, operation, edge_type):
        from episteme_graph.agents.component_graph.schema import classify_operation
        verb, mapped, is_generic = classify_operation(operation)
        assert mapped == edge_type
        assert is_generic is False
        assert verb and verb.lower() not in {"define", "transform", "relate", "result"}

    @pytest.mark.parametrize("operation", ["transform", "relate", "connect", "support", "associate", ""])
    def test_generic_operations_flagged(self, operation):
        from episteme_graph.agents.component_graph.schema import classify_operation
        _verb, edge_type, is_generic = classify_operation(operation)
        assert is_generic is True
        assert edge_type == "requires_review"


class TestSourceBackingSerialization:
    """New source-backing fields round-trip through (de)serialization (#302)."""

    def test_node_source_links_in_payload(self):
        node = _make_node("comp_001")
        node.linked_equation_ids = ["eq_1"]
        node.linked_derivation_ids = ["step_1"]
        node.linked_claim_ids = ["claim_1"]
        node.linked_evidence_ids = ["ev_1"]
        node.source_backing_status = "source_backed"
        node.component_type = "TheoryOperationNode"
        payload = _make_result(nodes=[node]).to_graph_payload()
        n = payload["nodes"][0]
        assert n["linked_equation_ids"] == ["eq_1"]
        assert n["linked_derivation_ids"] == ["step_1"]
        assert n["linked_claim_ids"] == ["claim_1"]
        assert n["linked_evidence_ids"] == ["ev_1"]
        assert n["source_backing_status"] == "source_backed"

    def test_edge_evidence_links_in_payload(self):
        edge = ComponentGraphEdge(
            edge_id="e1", source="comp_001", target="comp_002", edge_type="derives",
            support_status="derivation_linked", evidence_claims=[], reasoning="",
            confidence=0.9, evidence_equation_ids=["eq_1"], review_status="source_backed",
            evidence_derivation_ids=["step_1"], evidence_claim_ids=["claim_1"],
            source_evidence_ids=["ev_1"], review_reasons=[],
        )
        payload = _make_result(edges=[edge]).to_graph_payload()
        e = payload["edges"][0]
        assert e["review_status"] == "source_backed"
        assert e["evidence"]["evidence_derivation_ids"] == ["step_1"]
        assert e["evidence"]["evidence_claim_ids"] == ["claim_1"]
        assert e["evidence"]["source_evidence_ids"] == ["ev_1"]

    def test_from_dict_restores_source_backing(self):
        node = _make_node("comp_001")
        node.source_backing_status = "partially_source_backed"
        node.review_reasons = ["missing_evidence_link"]
        node.linked_equation_ids = ["eq_1"]
        restored = ComponentGraphResult.from_dict(_make_result(nodes=[node]).to_dict())
        assert restored.nodes[0].source_backing_status == "partially_source_backed"
        assert restored.nodes[0].review_reasons == ["missing_evidence_link"]
        assert restored.nodes[0].linked_equation_ids == ["eq_1"]
