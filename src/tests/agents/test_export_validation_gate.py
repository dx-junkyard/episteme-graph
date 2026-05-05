"""Tests for ExportValidationGate (issue #248)."""
from __future__ import annotations

import importlib.util
import sys
import os

import pytest

# Import the gate module directly to avoid pulling in sqlalchemy via __init__.py
_GATE_PATH = os.path.join(
    os.path.dirname(__file__),
    "../../../backend/core/document_pipeline/export_validation_gate.py",
)
_spec = importlib.util.spec_from_file_location("export_validation_gate", _GATE_PATH)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["export_validation_gate"] = _mod
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]

ExportValidationGate = _mod.ExportValidationGate
ExportValidationResult = _mod.ExportValidationResult


# ---------------------------------------------------------------------------
# Minimal stubs
# ---------------------------------------------------------------------------

class _DslNode:
    def __init__(self, node_id: str):
        self.node_id = node_id


class _DslEdge:
    def __init__(self, edge_id: str, from_node_id: str, to_node_id: str):
        self.edge_id = edge_id
        self.from_node_id = from_node_id
        self.to_node_id = to_node_id


class _DslResult:
    def __init__(self, nodes, edges):
        self.nodes = nodes
        self.edges = edges


class _ComponentRecord:
    def __init__(self, component_id: str, evidence_refs: dict | None = None):
        self.component_id = component_id
        self.evidence_refs = evidence_refs or {}


class _ComponentResult:
    def __init__(self, components):
        self.components = components


class _ClaimObject:
    def __init__(self, claim_id: str):
        self.claim_id = claim_id


class _ClaimObjectResult:
    def __init__(self, claims):
        self.claims = claims


class _EvidenceSource:
    def __init__(self, block_id: str):
        self.block_id = block_id
        self.page = None
        self.section_id = None


class _EvidenceRecord:
    def __init__(self, evidence_id: str):
        self.evidence_id = evidence_id
        self.source = _EvidenceSource("blk_001")


class _EvidenceResult:
    def __init__(self, records):
        self.records = records


class _CourseTopic:
    def __init__(self, linked_component_ids=None):
        self.linked_component_ids = linked_component_ids or []


class _CourseMappingResult:
    def __init__(self, topics):
        self.topics = topics


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_artifacts(**kwargs):
    """Create an artifacts dict with optional stage entries."""
    base = {
        "document_structure": {"blocks": [], "sections": []},
        "source_chunking": [{"text": "chunk"}],
        "claim_qualification": {"qualified_spans": []},
        "component_assembly": {"components": [], "validation_issues": []},
    }
    base.update(kwargs)
    return base


def _run_gate(artifacts=None, component_result=None, course_mapping=None,
              claim_objects=None, evidence=None, dsl=None):
    gate = ExportValidationGate()
    return gate.run(
        artifacts=artifacts or _make_artifacts(),
        component_result=component_result,
        course_mapping=course_mapping,
        claim_objects=claim_objects,
        evidence=evidence,
        dsl=dsl,
    )


# ---------------------------------------------------------------------------
# Tests: status determination
# ---------------------------------------------------------------------------

def test_passed_clean_artifacts():
    """No issues in any artifact → passed."""
    result = _run_gate()
    assert result.status == "passed"
    assert result.exportable is True
    assert result.publish_ready is True
    assert result.errors == []
    assert result.warnings == []


def test_passed_with_warnings_soft_error():
    """Errors from soft stages are downgraded to warnings."""
    artifacts = _make_artifacts(
        equation_semantics={
            "validation_issues": [
                {"rule_id": "some_soft_rule", "severity": "error", "message": "soft error"}
            ]
        }
    )
    result = _run_gate(artifacts=artifacts)
    assert result.status == "passed_with_warnings"
    assert result.exportable is True
    assert result.publish_ready is False
    assert len(result.warnings) == 1


def test_failed_validation_hard_error_from_component_assembly():
    """Errors from component_assembly → failed_validation."""
    artifacts = _make_artifacts(
        component_assembly={
            "components": [],
            "validation_issues": [
                {"rule_id": "unresolved_claim_id", "severity": "error", "message": "bad ref"}
            ]
        }
    )
    result = _run_gate(artifacts=artifacts)
    assert result.status == "failed_validation"
    assert result.exportable is False
    assert result.publish_ready is False
    assert result.summary.error_count >= 1


def test_needs_review_summary_only():
    """summary_only_component rule → needs_review."""
    artifacts = _make_artifacts(
        component_assembly={
            "components": [],
            "validation_issues": [
                {"rule_id": "summary_only_component", "severity": "warning", "message": "no refs"}
            ]
        }
    )
    result = _run_gate(artifacts=artifacts)
    assert result.status == "needs_review"
    assert result.exportable is True
    assert result.publish_ready is False
    assert result.summary.review_required_count >= 1


# ---------------------------------------------------------------------------
# Tests: missing required artifact
# ---------------------------------------------------------------------------

def test_missing_required_artifact():
    """Missing document_structure artifact → failed_validation."""
    artifacts = _make_artifacts()
    del artifacts["document_structure"]
    result = _run_gate(artifacts=artifacts)
    assert result.status == "failed_validation"
    codes = [e.code for e in result.errors]
    assert "MISSING_REQUIRED_ARTIFACT" in codes


# ---------------------------------------------------------------------------
# Tests: cross-artifact component ID resolution
# ---------------------------------------------------------------------------

def test_component_with_valid_claim_id():
    """Component referencing a real claim ID → no cross-ref error."""
    comp = _ComponentRecord("comp_001", {"claim_ids": ["claim_abc"], "evidence_ids": ["ev_001"]})
    comp_result = _ComponentResult([comp])
    claims = _ClaimObjectResult([_ClaimObject("claim_abc")])
    evidence = _EvidenceResult([_EvidenceRecord("ev_001")])

    result = _run_gate(
        component_result=comp_result,
        claim_objects=claims,
        evidence=evidence,
    )
    cross_errors = [e for e in result.errors if "UNRESOLVED_COMPONENT_CLAIM_ID" in e.code]
    assert cross_errors == []


def test_component_with_unresolved_claim_id():
    """Component referencing a non-existent claim ID → cross-ref error."""
    comp = _ComponentRecord("comp_001", {"claim_ids": ["claim_MISSING"]})
    comp_result = _ComponentResult([comp])
    claims = _ClaimObjectResult([_ClaimObject("claim_real")])
    evidence = _EvidenceResult([])

    result = _run_gate(
        component_result=comp_result,
        claim_objects=claims,
        evidence=evidence,
    )
    codes = [e.code for e in result.errors]
    assert "UNRESOLVED_COMPONENT_CLAIM_ID" in codes
    assert result.status == "failed_validation"


def test_component_with_unresolved_evidence_id():
    """Component referencing a non-existent evidence ID → cross-ref error."""
    comp = _ComponentRecord("comp_001", {"evidence_ids": ["ev_MISSING"]})
    comp_result = _ComponentResult([comp])
    claims = _ClaimObjectResult([])
    evidence = _EvidenceResult([_EvidenceRecord("ev_real")])

    result = _run_gate(
        component_result=comp_result,
        claim_objects=claims,
        evidence=evidence,
    )
    codes = [e.code for e in result.errors]
    assert "UNRESOLVED_COMPONENT_EVIDENCE_ID" in codes


def test_summary_only_component_cross_validation():
    """Component with empty claim_ids and evidence_ids → SUMMARY_ONLY_COMPONENT warning."""
    comp = _ComponentRecord("comp_001", {"claim_ids": [], "evidence_ids": []})
    comp_result = _ComponentResult([comp])
    claims = _ClaimObjectResult([_ClaimObject("claim_real")])
    evidence = _EvidenceResult([_EvidenceRecord("ev_real")])

    result = _run_gate(
        component_result=comp_result,
        claim_objects=claims,
        evidence=evidence,
    )
    codes = [w.code for w in result.warnings]
    assert "SUMMARY_ONLY_COMPONENT" in codes


# ---------------------------------------------------------------------------
# Tests: DSL edge validation
# ---------------------------------------------------------------------------

def test_dsl_edges_valid():
    """Valid DSL edges with real node IDs → no error."""
    nodes = [_DslNode("n1"), _DslNode("n2")]
    edges = [_DslEdge("e1", "n1", "n2")]
    dsl = _DslResult(nodes, edges)

    result = _run_gate(dsl=dsl)
    dsl_errors = [e for e in result.errors if "GRAPH_EDGE" in e.code]
    assert dsl_errors == []


def test_dsl_edge_empty_source():
    """DSL edge with empty from_node_id → EMPTY_GRAPH_EDGE_SOURCE error."""
    nodes = [_DslNode("n1")]
    edges = [_DslEdge("e1", "", "n1")]
    dsl = _DslResult(nodes, edges)

    result = _run_gate(dsl=dsl)
    codes = [e.code for e in result.errors]
    assert "EMPTY_GRAPH_EDGE_SOURCE" in codes


def test_dsl_edge_unresolved_target():
    """DSL edge referencing non-existent to_node_id → UNRESOLVED_GRAPH_EDGE_TARGET error."""
    nodes = [_DslNode("n1")]
    edges = [_DslEdge("e1", "n1", "n_MISSING")]
    dsl = _DslResult(nodes, edges)

    result = _run_gate(dsl=dsl)
    codes = [e.code for e in result.errors]
    assert "UNRESOLVED_GRAPH_EDGE_TARGET" in codes


# ---------------------------------------------------------------------------
# Tests: course mapping validation
# ---------------------------------------------------------------------------

def test_course_mapping_valid_component_id():
    """Course topic referencing a real component ID → no error."""
    comp = _ComponentRecord("comp_001")
    comp_result = _ComponentResult([comp])
    course = _CourseMappingResult([_CourseTopic(["comp_001"])])

    result = _run_gate(component_result=comp_result, course_mapping=course)
    codes = [e.code for e in result.errors]
    assert "UNRESOLVED_COMPONENT_ID" not in codes


def test_course_mapping_unresolved_component_id():
    """Course topic referencing a missing component ID → UNRESOLVED_COMPONENT_ID error."""
    comp = _ComponentRecord("comp_real")
    comp_result = _ComponentResult([comp])
    course = _CourseMappingResult([_CourseTopic(["comp_MISSING"])])

    result = _run_gate(component_result=comp_result, course_mapping=course)
    codes = [e.code for e in result.errors]
    assert "UNRESOLVED_COMPONENT_ID" in codes


# ---------------------------------------------------------------------------
# Tests: result structure
# ---------------------------------------------------------------------------

def test_result_to_dict():
    """ExportValidationResult.to_dict() returns a plain dict."""
    result = _run_gate()
    d = result.to_dict()
    assert isinstance(d, dict)
    assert "status" in d
    assert "exportable" in d
    assert "errors" in d
    assert "warnings" in d
    assert "summary" in d
