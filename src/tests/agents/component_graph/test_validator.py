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

    def test_llm_inferred_with_evidence_equations_no_warn(self):
        nodes = [_node("c1"), _node("c2")]
        edge = ComponentGraphEdge(
            edge_id="component_edge_0001",
            source="c1",
            target="c2",
            edge_type="TRANSFORMS",
            support_status="llm_inferred",
            evidence_claims=[],
            evidence_equation_ids=["eq_1"],
            reasoning="equation flow",
            confidence=0.8,
        )
        issues = self.validator.validate(_result(nodes, [edge]))
        assert not any(i.rule_id == "llm_inferred_no_evidence" for i in issues)

    def test_node_missing_label_is_error(self):
        nodes = [ComponentGraphNode("c1", "", "TheoryComponent"), _node("c2")]
        issues = self.validator.validate(_result(nodes, [_edge("c1", "c2")]))
        assert any(i.rule_id == "component_graph_node_missing_label" for i in issues)

    def test_bidirectional_edges_warn(self):
        nodes = [_node("c1"), _node("c2")]
        edges = [_edge("c1", "c2", edge_id="e1"), _edge("c2", "c1", edge_id="e2")]
        issues = self.validator.validate(_result(nodes, edges))
        assert any(i.rule_id == "bidirectional_component_edge_pair" for i in issues)

    def test_related_to_warns_as_generic(self):
        nodes = [_node("c1"), _node("c2")]
        issues = self.validator.validate(_result(nodes, [_edge("c1", "c2", edge_type="RELATED_TO")]))
        assert any(i.rule_id == "component_graph_related_to_edge" for i in issues)


def _theory_node(cid, **kw):
    defaults = dict(
        component_id=cid,
        label=f"Define {cid}",
        component_type="TheoryOperationNode",
        operation="define",
        graph_layer="main",
        linked_equation_ids=["eq_1"],
        linked_derivation_ids=["step_1"],
        linked_claim_ids=["claim_1"],
        linked_evidence_ids=["ev_1"],
        source_backing_status="source_backed",
    )
    defaults.update(kw)
    return ComponentGraphNode(**defaults)


class TestSourceBackingRules:
    def setup_method(self):
        self.validator = ComponentGraphValidator()

    def test_theory_node_without_source_link_is_error(self):
        node = _theory_node(
            "c1",
            linked_equation_ids=[],
            linked_derivation_ids=[],
            linked_claim_ids=[],
            linked_evidence_ids=[],
            source_backing_status="review_required",
            review_reasons=["missing_equation_link"],
        )
        issues = self.validator.validate(_result([node], []))
        assert any(i.rule_id == "theory_node_missing_source_link" for i in issues)

    def test_review_required_node_without_reasons_is_error(self):
        node = _theory_node("c1", source_backing_status="review_required", review_reasons=[])
        issues = self.validator.validate(_result([node], []))
        assert any(i.rule_id == "review_required_node_missing_reasons" for i in issues)

    def test_fallback_node_marked_source_backed_is_error(self):
        node = _theory_node("c1", maturity_source="deterministic_fallback", source_backing_status="source_backed")
        issues = self.validator.validate(_result([node], []))
        assert any(i.rule_id == "fallback_node_marked_source_backed" for i in issues)

    def test_derive_node_without_derivation_link_is_error(self):
        node = _theory_node("c1", operation="derive_relation", linked_derivation_ids=[])
        issues = self.validator.validate(_result([node], []))
        assert any(i.rule_id == "derive_node_missing_derivation_link" for i in issues)

    def test_generic_operation_warns(self):
        node = _theory_node("c1", operation="transform", label="Transform eq_1")
        issues = self.validator.validate(_result([node], []))
        assert any(i.rule_id == "theory_node_generic_operation" for i in issues)

    def test_main_graph_generic_operation_is_error(self):
        # Issue #308: a generic operation must never reach the main graph.
        node = _theory_node("c1", operation="transform", label="Theory basis")
        issues = self.validator.validate(_result([node], []))
        assert any(
            i.rule_id == "main_graph_generic_operation" and i.severity == "error"
            for i in issues
        )

    def test_detail_layer_generic_operation_is_not_error(self):
        # The same generic operation in the equation_detail layer is allowed.
        node = _theory_node(
            "c1",
            operation="transform",
            label="Transform eq_1",
            graph_layer="equation_detail",
            component_type="EquationOperationNode",
        )
        issues = self.validator.validate(_result([node], []))
        assert not any(i.rule_id == "main_graph_generic_operation" for i in issues)

    def test_main_graph_equation_id_label_is_error(self):
        # Issue #308: main labels must be theory stages, not "Define eq_2_7".
        node = _theory_node("c1", label="Define eq_2_7")
        issues = self.validator.validate(_result([node], []))
        assert any(
            i.rule_id == "main_graph_node_equation_id_label" and i.severity == "error"
            for i in issues
        )

    def test_main_graph_derive_result_equation_label_is_error(self):
        node = _theory_node("c1", operation="derive_result", label="Derive result eq_2_10")
        issues = self.validator.validate(_result([node], []))
        assert any(i.rule_id == "main_graph_node_equation_id_label" for i in issues)

    def test_theory_stage_label_is_accepted(self):
        # A clean theory-stage label produces no label/operation errors.
        node = _theory_node("c1", label="Equation system", operation="linearize_moment_equations")
        issues = self.validator.validate(_result([node], []))
        label_errors = [
            i for i in issues
            if i.rule_id in ("main_graph_node_equation_id_label", "main_graph_generic_operation")
        ]
        assert label_errors == []

    def test_equation_detail_label_with_equation_id_is_allowed(self):
        # Equation ids are fine in the detail layer (traceability).
        node = _theory_node(
            "c1",
            label="Define eq_2_7",
            graph_layer="equation_detail",
            component_type="EquationOperationNode",
        )
        issues = self.validator.validate(_result([node], []))
        assert not any(i.rule_id == "main_graph_node_equation_id_label" for i in issues)

    def test_edge_without_evidence_warns(self):
        nodes = [_theory_node("c1"), _theory_node("c2")]
        edge = ComponentGraphEdge(
            edge_id="e1", source="c1", target="c2", edge_type="defines",
            support_status="derivation_linked", evidence_claims=[], reasoning="",
            confidence=0.8, evidence_equation_ids=[], evidence_derivation_ids=[],
        )
        issues = self.validator.validate(_result(nodes, [edge]))
        assert any(i.rule_id == "component_graph_edge_no_evidence" for i in issues)

    def test_fully_backed_theory_node_has_no_errors(self):
        nodes = [_theory_node("c1"), _theory_node("c2")]
        edge = ComponentGraphEdge(
            edge_id="e1", source="c1", target="c2", edge_type="defines",
            support_status="derivation_linked", evidence_claims=[],
            reasoning="flow", confidence=0.9, evidence_equation_ids=["eq_1"],
            evidence_derivation_ids=["step_1"], review_status="source_backed",
        )
        issues = self.validator.validate(_result(nodes, [edge]))
        assert [i for i in issues if i.severity == "error"] == []

    # --- issue #334: claim-only nodes relax equation checks --------------------

    def test_claim_only_derive_node_missing_equations_is_warning(self):
        """A derives node backed by claims should produce a warning, not error."""
        node = _theory_node(
            "c1",
            operation="derive_result",
            linked_equation_ids=[],
            linked_claim_ids=["claim_1"],
            linked_evidence_ids=["ev_1"],
            source_backing_status="partially_source_backed",
            review_reasons=["missing_equation_link"],
        )
        issues = self.validator.validate(_result([node], []))
        eq_issues = [i for i in issues if i.rule_id == "output_node_missing_linked_equations"]
        assert len(eq_issues) == 1
        assert eq_issues[0].severity == "warning"

    def test_claim_only_derive_node_without_claims_is_error(self):
        """A derives node with no equations AND no claims stays an error."""
        node = _theory_node(
            "c1",
            operation="derive_result",
            linked_equation_ids=[],
            linked_claim_ids=[],
            linked_evidence_ids=[],
            source_backing_status="review_required",
            review_reasons=["missing_equation_link", "missing_atomic_claim"],
        )
        issues = self.validator.validate(_result([node], []))
        eq_issues = [i for i in issues if i.rule_id == "output_node_missing_linked_equations"]
        assert len(eq_issues) == 1
        assert eq_issues[0].severity == "error"
