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
    def __init__(
        self,
        component_id: str,
        evidence_refs: dict | None = None,
        *,
        linked_claim_ids=None,
        linked_equation_ids=None,
        input_equation_ids=None,
        output_equation_ids=None,
        review_required_equation_ids=None,
        review_status: str = "teacher_review_required",
        component_type: str = "ClaimBundleComponent",
        internal_flow=None,
        label: str = "",
        summary: str = "",
        responsibility_type: str = "",
        primary_operation: str = "",
        teaching_takeaway: str = "",
        source_scope=None,
        assumptions=None,
        split_recommendation=None,
        component_quality=None,
    ):
        self.component_id = component_id
        self.component_type = component_type
        self.label = label
        self.summary = summary
        self.evidence_refs = evidence_refs or {}
        self.linked_claim_ids = linked_claim_ids or []
        self.linked_equation_ids = linked_equation_ids or []
        self.input_equation_ids = input_equation_ids or []
        self.output_equation_ids = output_equation_ids or []
        self.review_required_equation_ids = review_required_equation_ids or []
        self.review_status = review_status
        self.internal_flow = internal_flow or []
        self.responsibility_type = responsibility_type
        self.primary_operation = primary_operation
        self.operation = primary_operation
        self.teaching_takeaway = teaching_takeaway
        self.source_scope = source_scope or {}
        self.assumptions = assumptions or []
        self.split_recommendation = split_recommendation or {}
        self.component_quality = component_quality or {}
        self.inputs = []
        self.outputs = []
        self.preconditions = []
        self.cautions = []


class _ComponentResult:
    def __init__(self, components):
        self.components = components


class _ClaimObject:
    def __init__(
        self,
        claim_id: str,
        support_status: str = "source_backed",
        source_evidence_ids: list | None = None,
        atomicity: str = "atomic",
        claim_type: str = "definition",
    ):
        self.claim_id = claim_id
        self.support_status = support_status
        self.source_evidence_ids = source_evidence_ids or []
        self.atomicity = atomicity
        self.claim_type = claim_type


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


class _ComponentResultWithAlignment:
    def __init__(self, components, derivation_graph_alignment):
        self.components = components
        self.derivation_graph_alignment = derivation_graph_alignment


def test_derivation_graph_alignment_aggregated_into_export(monkeypatch=None):
    """issue #325: Step 4 alignment results are aggregated into export buckets."""
    alignment = {
        "export_validation": {
            "errors": [
                {"code": "dangling_equation_ref", "message": "op references missing eq"}
            ],
            "warnings": [
                {"code": "generic_edge", "message": "RELATED_TO edge"}
            ],
            "review_items": [
                {"code": "review_required_derivation_path", "message": "low confidence eq"}
            ],
            "component_graph_clean": True,
            "operation_graph_clean": False,
            "support_map_renderable": True,
        }
    }
    comp = _ComponentRecord("comp_001")
    result = _run_gate(
        component_result=_ComponentResultWithAlignment([comp], alignment)
    )
    error_codes = [e.code for e in result.errors]
    warning_codes = [w.code for w in result.warnings]
    review_codes = [r.code for r in result.review_items]
    assert "DERIVATION_GRAPH_ALIGNMENT_DANGLING_EQUATION_REF" in error_codes
    assert "DERIVATION_GRAPH_ALIGNMENT_GENERIC_EDGE" in warning_codes
    assert "DERIVATION_GRAPH_ALIGNMENT_REVIEW_REQUIRED_DERIVATION_PATH" in review_codes
    assert result.derivation_graph_alignment["operation_graph_clean"] is False
    assert result.status == "failed_validation"


def test_derivation_graph_alignment_absent_is_clean_default():
    comp = _ComponentRecord("comp_001")
    result = _run_gate(component_result=_ComponentResult([comp]))
    assert result.derivation_graph_alignment == {
        "errors": [],
        "warnings": [],
        "review_items": [],
        "component_graph_clean": True,
        "operation_graph_clean": True,
        "support_map_renderable": True,
    }


def test_relation_component_without_internal_flow_blocks_export():
    comp = _ComponentRecord("comp_001", component_type="RelationComponent", internal_flow=[])
    result = _run_gate(component_result=_ComponentResult([comp]))
    codes = [e.code for e in result.errors]
    assert "COMPONENT_MISSING_INTERNAL_FLOW" in codes


def test_summary_only_component_in_derivation_path_blocks_export():
    """issue #300 criterion #10: summary-only derivation-path component → hard error."""
    comp = _ComponentRecord("comp_sum", component_type="RelationComponent", internal_flow=[])
    result = _run_gate(component_result=_ComponentResult([comp]))
    codes = [e.code for e in result.errors]
    assert "SUMMARY_ONLY_COMPONENT_IN_DERIVATION_PATH" in codes
    assert result.status == "failed_validation"


def test_derivation_component_with_equations_not_summary_only():
    """A derivation-path component carrying equations is not summary-only."""
    comp = _ComponentRecord(
        "comp_ok",
        component_type="RelationComponent",
        linked_equation_ids=["eq_1"],
        internal_flow=[{"from": "eq_1", "relation": "derive", "to": "eq_2"}],
    )
    result = _run_gate(component_result=_ComponentResult([comp]))
    codes = [e.code for e in result.errors]
    assert "SUMMARY_ONLY_COMPONENT_IN_DERIVATION_PATH" not in codes


def test_component_claim_support_rejects_non_supporting_equation():
    artifacts = _make_artifacts(equation_semantics={
        "equations": [{
            "equation_id": "eq_review",
            "needs_math_review": True,
            "confidence_policy": {
                "can_support_claim": False,
                "must_not_treat_as_source_extracted": True,
            },
        }],
    })
    comp = _ComponentRecord(
        "comp_001",
        {"claim_ids": ["claim_abc"], "evidence_ids": ["ev_001"], "equation_ids": ["eq_review"]},
        linked_equation_ids=["eq_review"],
    )
    result = _run_gate(
        artifacts=artifacts,
        component_result=_ComponentResult([comp]),
        claim_objects=_ClaimObjectResult([_ClaimObject("claim_abc", source_evidence_ids=["ev_001"])]),
        evidence=_EvidenceResult([_EvidenceRecord("ev_001")]),
    )
    codes = [e.code for e in result.errors]
    assert "NON_SUPPORTING_EQUATION_USED_FOR_CLAIM_SUPPORT" in codes


def test_review_required_component_allows_marked_non_supporting_equation():
    artifacts = _make_artifacts(equation_semantics={
        "equations": [{
            "equation_id": "eq_review",
            "needs_math_review": True,
            "confidence_policy": {
                "can_support_claim": False,
                "must_not_treat_as_source_extracted": True,
            },
        }],
    })
    comp = _ComponentRecord(
        "comp_001",
        {"claim_ids": ["claim_abc"], "evidence_ids": ["ev_001"], "equation_ids": ["eq_review"]},
        linked_equation_ids=["eq_review"],
        output_equation_ids=["eq_review"],
        review_required_equation_ids=["eq_review"],
        review_status="review_required",
    )
    result = _run_gate(
        artifacts=artifacts,
        component_result=_ComponentResult([comp]),
        claim_objects=_ClaimObjectResult([_ClaimObject("claim_abc", source_evidence_ids=["ev_001"])]),
        evidence=_EvidenceResult([_EvidenceRecord("ev_001")]),
    )
    error_codes = [e.code for e in result.errors]
    warning_codes = [w.code for w in result.warnings]
    assert "NON_SUPPORTING_EQUATION_USED_FOR_CLAIM_SUPPORT" not in error_codes
    assert "NON_SUPPORTING_EQUATION_USED_AS_COMPONENT_OUTPUT" not in error_codes
    assert "NON_SUPPORTING_EQUATION_USED_FOR_CLAIM_SUPPORT" in warning_codes
    assert "NON_SUPPORTING_EQUATION_USED_AS_COMPONENT_OUTPUT" in warning_codes
    assert result.status != "failed_validation"


def test_component_output_rejects_review_required_auto_accepted_equation():
    artifacts = _make_artifacts(equation_semantics={
        "equations": [{
            "equation_id": "eq_review",
            "needs_math_review": True,
            "confidence_policy": {
                "can_support_claim": False,
                "must_not_treat_as_source_extracted": True,
            },
        }],
    })
    comp = _ComponentRecord(
        "comp_001",
        {"claim_ids": [], "evidence_ids": ["ev_001"], "equation_ids": ["eq_review"]},
        output_equation_ids=["eq_review"],
        review_status="auto_accepted",
    )
    result = _run_gate(
        artifacts=artifacts,
        component_result=_ComponentResult([comp]),
        claim_objects=_ClaimObjectResult([]),
        evidence=_EvidenceResult([_EvidenceRecord("ev_001")]),
    )
    codes = [e.code for e in result.errors]
    assert "NON_SUPPORTING_EQUATION_USED_AS_COMPONENT_OUTPUT" in codes
    assert "REVIEW_REQUIRED_OUTPUT_COMPONENT_AUTO_ACCEPTED" in codes


def test_export_validation_lists_equation_consistency_mismatch_candidates():
    artifacts = _make_artifacts(equation_semantics={
        "equations": [{
            "equation_id": "eq_bad",
            "confidence_policy": {
                "can_support_claim": False,
                "can_be_used_in_derivation": False,
                "must_not_treat_as_source_extracted": True,
            },
            "equation_consistency": {
                "raw_text_latex_match": "mismatch",
                "label_location_match": "match",
                "symbol_overlap_score": 0.0,
                "source_span_quality": "clean",
                "review_required": True,
            },
        }],
    })
    result = _run_gate(artifacts=artifacts)
    codes = [e.code for e in result.review_items]
    assert "EQUATION_CONSISTENCY_MISMATCH" in codes
    assert result.status == "needs_review"


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


def test_component_graph_node_missing_label_is_hard_error():
    artifacts = _make_artifacts(component_graph={
        "nodes": [{"component_id": "comp_1", "label": "", "component_type": "RelationComponent"}],
        "edges": [],
    })
    result = _run_gate(artifacts=artifacts)
    assert "COMPONENT_GRAPH_NODE_MISSING_LABEL" in [e.code for e in result.errors]


def test_component_graph_edge_quality_warnings():
    artifacts = _make_artifacts(component_graph={
        "nodes": [
            {"component_id": "comp_1", "label": "A", "component_type": "RelationComponent"},
            {"component_id": "comp_2", "label": "B", "component_type": "RelationComponent"},
        ],
        "edges": [
            {"edge_id": "e1", "source": "comp_1", "target": "comp_2", "edge_type": "RELATED_TO"},
            {"edge_id": "e2", "source": "comp_2", "target": "comp_1", "edge_type": "REQUIRES"},
        ],
    })
    result = _run_gate(artifacts=artifacts)
    codes = {w.code for w in result.warnings}
    assert "COMPONENT_GRAPH_RELATED_TO_EDGE" in codes
    assert "COMPONENT_GRAPH_EDGE_NO_EVIDENCE" in codes
    assert "COMPONENT_GRAPH_BIDIRECTIONAL_EDGE_PAIR" in codes


# ---------------------------------------------------------------------------
# Tests: component granularity quality (issue #4)
# ---------------------------------------------------------------------------

def test_component_quality_good_component_is_reported():
    comp = _ComponentRecord(
        "comp_good",
        {"claim_ids": ["claim_abc"], "evidence_ids": ["ev_001"], "equation_ids": ["eq_1"]},
        linked_claim_ids=["claim_abc"],
        linked_equation_ids=["eq_1"],
        review_status="source_backed",
        responsibility_type="equation_system",
        primary_operation="linearize",
        internal_flow=[{"from": "eq_0", "relation": "linearize", "to": "eq_1"}],
        component_quality={
            "component_id": "comp_good",
            "granularity_status": "good",
            "equation_count": 1,
            "claim_count": 1,
            "derivation_step_count": 0,
            "responsibility_count": 1,
            "source_scope_width": 0,
            "split_required": False,
            "split_reasons": [],
            "suggested_split": [],
        },
    )
    result = _run_gate(
        component_result=_ComponentResult([comp]),
        claim_objects=_ClaimObjectResult([_ClaimObject("claim_abc", source_evidence_ids=["ev_001"])]),
        evidence=_EvidenceResult([_EvidenceRecord("ev_001")]),
    )

    assert result.component_quality[0].granularity_status == "good"
    assert result.component_quality[0].equation_count == 1
    assert result.publish_ready is True


def test_oversized_component_quality_warns_and_blocks_publish_ready():
    comp = _ComponentRecord(
        "comp_big",
        {"claim_ids": ["claim_abc"], "evidence_ids": ["ev_001"], "equation_ids": [f"eq_{i}" for i in range(10)]},
        linked_claim_ids=["claim_abc"],
        linked_equation_ids=[f"eq_{i}" for i in range(10)],
        review_status="source_backed",
        responsibility_type="equation_system",
        primary_operation="linearize",
        internal_flow=[{"from": f"eq_{i}", "relation": "step", "to": f"eq_{i+1}"} for i in range(6)],
        source_scope={"section_ids": ["s1", "s2", "s3"]},
        teaching_takeaway="This is a broad, multi-clause takeaway that covers too many operations.",
        component_quality={
            "component_id": "comp_big",
            "granularity_status": "too_coarse",
            "equation_count": 10,
            "claim_count": 1,
            "derivation_step_count": 0,
            "responsibility_count": 1,
            "source_scope_width": 3,
            "split_required": True,
            "split_reasons": [
                "linked equation count exceeds threshold",
                "source scope spans multiple conceptual sections",
            ],
            "suggested_split": [
                {"name": "Equation System", "responsibility_type": "equation_system"},
            ],
        },
    )
    result = _run_gate(
        component_result=_ComponentResult([comp]),
        claim_objects=_ClaimObjectResult([_ClaimObject("claim_abc", source_evidence_ids=["ev_001"])]),
        evidence=_EvidenceResult([_EvidenceRecord("ev_001")]),
    )

    q = result.component_quality[0]
    assert q.granularity_status == "too_coarse"
    assert q.split_required is True
    assert "linked equation count exceeds threshold" in q.split_reasons
    assert "source scope spans multiple conceptual sections" in q.split_reasons
    assert "COMPONENT_GRANULARITY_ISSUE" in [w.code for w in result.warnings]
    assert result.publish_ready is False


def test_mixed_responsibility_component_quality_emits_suggested_split():
    comp = _ComponentRecord(
        "comp_mixed",
        {"claim_ids": ["claim_abc"], "evidence_ids": ["ev_001"], "equation_ids": ["eq_model", "eq_final"]},
        linked_claim_ids=["claim_abc"],
        linked_equation_ids=["eq_model", "eq_final"],
        review_status="source_backed",
        label="Bias model and consistency relation",
        summary="Defines a local bias model and derives a final consistency relation.",
        responsibility_type="model",
        primary_operation="parameterize",
        component_quality={
            "component_id": "comp_mixed",
            "granularity_status": "mixed_responsibility",
            "equation_count": 2,
            "claim_count": 1,
            "derivation_step_count": 0,
            "responsibility_count": 2,
            "source_scope_width": 0,
            "split_required": True,
            "split_reasons": ["responsibility type has more than one primary value"],
            "suggested_split": [
                {"name": "Model", "responsibility_type": "model"},
                {"name": "Constraint", "responsibility_type": "constraint"},
            ],
        },
    )
    result = _run_gate(
        component_result=_ComponentResult([comp]),
        claim_objects=_ClaimObjectResult([_ClaimObject("claim_abc", source_evidence_ids=["ev_001"])]),
        evidence=_EvidenceResult([_EvidenceRecord("ev_001")]),
    )

    q = result.component_quality[0]
    assert q.granularity_status == "mixed_responsibility"
    assert q.responsibility_count >= 2
    assert q.split_required is True
    assert q.suggested_split
    assert "COMPONENT_GRANULARITY_ISSUE" in [w.code for w in result.warnings]
    assert result.publish_ready is False


def test_component_quality_uses_existing_split_recommendation():
    comp = _ComponentRecord(
        "comp_split",
        {"claim_ids": ["claim_abc"], "evidence_ids": ["ev_001"], "equation_ids": ["eq_1", "eq_2"]},
        linked_claim_ids=["claim_abc"],
        linked_equation_ids=["eq_1", "eq_2"],
        review_status="source_backed",
        responsibility_type="equation_system",
        split_recommendation={
            "required": True,
            "reasons": ["component includes both derivation and final result"],
            "suggested_components": [
                {"name": "Linear equation", "responsibility_type": "equation_system"},
                {"name": "Final relation", "responsibility_type": "constraint"},
            ],
        },
        component_quality={
            "component_id": "comp_split",
            "granularity_status": "too_coarse",
            "equation_count": 2,
            "claim_count": 1,
            "derivation_step_count": 0,
            "responsibility_count": 1,
            "source_scope_width": 0,
            "split_required": True,
            "split_reasons": ["component includes both derivation and final result"],
            "suggested_split": [
                {"name": "Linear equation", "responsibility_type": "equation_system"},
                {"name": "Final relation", "responsibility_type": "constraint"},
            ],
        },
    )
    result = _run_gate(
        component_result=_ComponentResult([comp]),
        claim_objects=_ClaimObjectResult([_ClaimObject("claim_abc", source_evidence_ids=["ev_001"])]),
        evidence=_EvidenceResult([_EvidenceRecord("ev_001")]),
    )

    q = result.component_quality[0]
    assert q.granularity_status == "too_coarse"
    assert q.suggested_split == [
        {"name": "Linear equation", "responsibility_type": "equation_system"},
        {"name": "Final relation", "responsibility_type": "constraint"},
    ]


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


# ---------------------------------------------------------------------------
# Tests: source-backed claim ↔ EvidenceRegistry validation (issue #257)
# ---------------------------------------------------------------------------

def test_source_backed_claim_with_evidence_ids_no_warning():
    """source_backed claim that has source_evidence_ids → no SOURCE_BACKED warning."""
    claims = _ClaimObjectResult([
        _ClaimObject("claim_001", support_status="source_backed",
                     source_evidence_ids=["ev_001"]),
    ])
    evidence = _EvidenceResult([_EvidenceRecord("ev_001")])

    result = _run_gate(claim_objects=claims, evidence=evidence)
    codes = [w.code for w in result.warnings]
    assert "SOURCE_BACKED_CLAIM_NO_EVIDENCE_IDS" not in codes


def test_source_backed_claim_without_evidence_ids_is_hard_error():
    """source_backed claim with empty source_evidence_ids → hard error (#312)."""
    claims = _ClaimObjectResult([
        _ClaimObject("claim_002", support_status="source_backed",
                     source_evidence_ids=[]),
    ])
    evidence = _EvidenceResult([_EvidenceRecord("ev_001")])

    result = _run_gate(claim_objects=claims, evidence=evidence)
    codes = [e.code for e in result.errors]
    assert "SOURCE_BACKED_CLAIM_NO_EVIDENCE_IDS" in codes
    assert result.status == "failed_validation"


def test_source_backed_claim_with_unresolved_evidence_id_is_hard_error():
    """source_backed claim referencing a missing evidence_id → hard error (#312)."""
    claims = _ClaimObjectResult([
        _ClaimObject("claim_003", support_status="source_backed",
                     source_evidence_ids=["ev_MISSING"]),
    ])
    evidence = _EvidenceResult([_EvidenceRecord("ev_real")])

    result = _run_gate(claim_objects=claims, evidence=evidence)
    codes = [e.code for e in result.errors]
    assert "SOURCE_BACKED_CLAIM_UNRESOLVED_EVIDENCE_ID" in codes
    assert result.status == "failed_validation"


def test_non_source_backed_claim_no_evidence_ids_is_silent():
    """inferred / domain_inferred claims are not required to have evidence_ids."""
    claims = _ClaimObjectResult([
        _ClaimObject("claim_004", support_status="inferred",
                     source_evidence_ids=[]),
    ])
    evidence = _EvidenceResult([])

    result = _run_gate(claim_objects=claims, evidence=evidence)
    codes = [w.code for w in result.warnings]
    assert "SOURCE_BACKED_CLAIM_NO_EVIDENCE_IDS" not in codes


# ---------------------------------------------------------------------------
# Tests: claim atomicity reporting (issue #312)
# ---------------------------------------------------------------------------

def test_non_atomic_main_result_claim_is_hard_error():
    """A non-atomic main-result claim blocks export."""
    claims = _ClaimObjectResult([
        _ClaimObject("claim_001", atomicity="split_required", claim_type="result"),
    ])
    result = _run_gate(claim_objects=claims)
    review_codes = [r.code for r in result.review_items]
    assert "SPLIT_PENDING_CLAIM_NEEDS_CONFIRMATION" in review_codes
    assert "NON_ATOMIC_MAIN_RESULT_CLAIM" not in [e.code for e in result.errors]
    assert result.status == "needs_review"


def test_legacy_non_atomic_main_result_claim_is_hard_error():
    claims = _ClaimObjectResult([
        _ClaimObject("claim_001", atomicity="non_atomic", claim_type="result"),
    ])
    result = _run_gate(claim_objects=claims)
    assert "NON_ATOMIC_MAIN_RESULT_CLAIM" in [e.code for e in result.errors]
    assert result.status == "failed_validation"


def test_non_atomic_non_main_claim_needs_review():
    """A non-atomic non-main claim is flagged for review, not a hard error."""
    claims = _ClaimObjectResult([
        _ClaimObject("claim_001", atomicity="non_atomic", claim_type="method_choice"),
    ])
    result = _run_gate(claim_objects=claims)
    review_codes = [r.code for r in result.review_items]
    assert "NON_ATOMIC_CLAIM_NEEDS_SPLIT" in review_codes
    assert result.status == "needs_review"


def test_atomic_claim_produces_no_atomicity_issue():
    """Atomic claims do not trigger atomicity reporting."""
    claims = _ClaimObjectResult([
        _ClaimObject("claim_001", atomicity="atomic", claim_type="result"),
    ])
    result = _run_gate(claim_objects=claims)
    all_codes = [e.code for e in result.errors] + [r.code for r in result.review_items]
    assert "NON_ATOMIC_MAIN_RESULT_CLAIM" not in all_codes
    assert "NON_ATOMIC_CLAIM_NEEDS_SPLIT" not in all_codes


def test_split_pending_claim_needs_confirmation():
    """Deterministic-split suggestions are surfaced for review (issue #317)."""
    claims = _ClaimObjectResult([
        _ClaimObject("claim_001_sub01", atomicity="split_required", claim_type="result"),
    ])
    result = _run_gate(claim_objects=claims)
    review_codes = [r.code for r in result.review_items]
    assert "SPLIT_PENDING_CLAIM_NEEDS_CONFIRMATION" in review_codes
    # split_pending must not be treated as a non-atomic hard error
    assert "NON_ATOMIC_MAIN_RESULT_CLAIM" not in [e.code for e in result.errors]


def test_component_using_split_required_claim_as_support_blocks_publish():
    comp = _ComponentRecord(
        "comp_bad_claim",
        {"claim_ids": ["claim_split"], "evidence_ids": [], "equation_ids": []},
        linked_claim_ids=["claim_split"],
    )
    claims = _ClaimObjectResult([
        _ClaimObject("claim_split", atomicity="split_required", claim_type="main_result"),
    ])
    result = _run_gate(component_result=_ComponentResult([comp]), claim_objects=claims, evidence=_EvidenceResult([]))
    assert "NON_ATOMIC_CLAIM_USED_AS_COMPONENT_SUPPORT" in [e.code for e in result.errors]


# ---------------------------------------------------------------------------
# Tests: concept coverage reporting (issue #8)
# ---------------------------------------------------------------------------

def test_concept_validation_block_always_present():
    result = _run_gate()
    block = result.concept_validation
    for key in (
        "missing_concepts",
        "empty_concepts_on_main_claims",
        "empty_concepts_on_main_components",
        "concepts_on_composite_claims",
        "concept_role_mismatch",
        "concepts_from_low_confidence_sources",
    ):
        assert key in block
        assert block[key] == []


def test_main_claim_with_empty_concepts_is_reported():
    claim = _ClaimObject("claim_main", claim_type="result")
    claim.concepts = []
    claim.concept_assignment_status = "review_required"
    result = _run_gate(claim_objects=_ClaimObjectResult([claim]))
    assert "claim_main" in result.concept_validation["empty_concepts_on_main_claims"]
    assert "claim_main" in result.concept_validation["missing_concepts"]
    assert "MAIN_CLAIM_INSUFFICIENT_CONCEPTS" in [w.code for w in result.warnings]


def test_main_component_with_too_few_concepts_is_reported():
    comp = _ComponentRecord("comp_thin", {"claim_ids": [], "evidence_ids": []})
    comp.concepts = ["Skewness"]
    result = _run_gate(component_result=_ComponentResult([comp]))
    assert "comp_thin" in result.concept_validation["empty_concepts_on_main_components"]
    assert "MAIN_COMPONENT_INSUFFICIENT_CONCEPTS" in [w.code for w in result.warnings]


def test_source_backed_concepts_on_composite_claim_flagged_for_review():
    claim = _ClaimObject("claim_comp", atomicity="composite", claim_type="definition")
    claim.concepts = ["Skewness", "Kurtosis"]
    claim.concept_assignment_status = "source_backed"
    result = _run_gate(claim_objects=_ClaimObjectResult([claim]))
    assert "claim_comp" in result.concept_validation["concepts_on_composite_claims"]
    assert "CONCEPTS_ON_COMPOSITE_CLAIM" in [r.code for r in result.review_items]


def test_low_confidence_source_concepts_flagged_for_review():
    claim = _ClaimObject("claim_low", atomicity="atomic", claim_type="definition")
    claim.concepts = ["Skewness", "Kurtosis"]
    claim.concept_assignment_status = "review_required"
    result = _run_gate(claim_objects=_ClaimObjectResult([claim]))
    assert "claim_low" in result.concept_validation["concepts_from_low_confidence_sources"]
    assert "CONCEPTS_FROM_LOW_CONFIDENCE_SOURCE" in [r.code for r in result.review_items]


def _role_mismatch_reasons(result, comp_id):
    return [
        e["reason"]
        for e in result.concept_validation["concept_role_mismatch"]
        if e["component_id"] == comp_id
    ]


def test_derivation_component_without_math_concept_is_role_mismatch():
    comp = _ComponentRecord(
        "comp_deriv",
        {"claim_ids": [], "evidence_ids": []},
        primary_operation="eliminate_bias",
    )
    comp.concepts = ["Galaxy survey", "Gravity model"]  # named, but no math/procedural
    result = _run_gate(component_result=_ComponentResult([comp]))
    reasons = _role_mismatch_reasons(result, "comp_deriv")
    assert any("mathematical or procedural" in r for r in reasons)
    assert "COMPONENT_CONCEPT_ROLE_MISMATCH" in [w.code for w in result.warnings]


def test_observable_component_without_named_concept_is_role_mismatch():
    comp = _ComponentRecord(
        "comp_obs",
        {"claim_ids": [], "evidence_ids": []},
        component_type="ObservableComponent",
    )
    comp.concepts = ["b_1", "derive"]  # only symbol + procedural
    result = _run_gate(component_result=_ComponentResult([comp]))
    reasons = _role_mismatch_reasons(result, "comp_obs")
    assert any("observable-name" in r for r in reasons)


def test_comparison_component_without_theory_class_is_role_mismatch():
    comp = _ComponentRecord(
        "comp_cmp",
        {"claim_ids": [], "evidence_ids": []},
        component_type="ComparisonComponent",
    )
    comp.concepts = ["b_1", "solve"]  # only symbol + procedural
    result = _run_gate(component_result=_ComponentResult([comp]))
    reasons = _role_mismatch_reasons(result, "comp_cmp")
    assert any("theory-class" in r for r in reasons)


def test_well_tagged_components_have_no_role_mismatch():
    comp = _ComponentRecord(
        "comp_ok",
        {"claim_ids": [], "evidence_ids": []},
        primary_operation="eliminate_bias",
    )
    comp.concepts = ["b_1", "consistency relation"]  # symbol + named
    result = _run_gate(component_result=_ComponentResult([comp]))
    assert result.concept_validation["concept_role_mismatch"] == []


# ---------------------------------------------------------------------------
# Tests: component refinement reporting (issue #324)
# ---------------------------------------------------------------------------

class _RefinedComponentResult:
    def __init__(self, refinement_validation):
        self.components = []
        self.component_refinement = {"refinement_validation": refinement_validation}


def test_component_refinement_absent_is_empty_report():
    result = _run_gate(component_result=_ComponentResult([]))
    assert result.component_refinement_validation["split_components"] == []
    assert result.component_refinement_validation["unassigned_links"] == []


def test_component_refinement_unassigned_link_warns():
    comp_result = _RefinedComponentResult({
        "split_components": ["comp_a"],
        "unchanged_components": [],
        "failed_refinements": [],
        "review_required_refinements": [],
        "unassigned_links": [{"original_component_id": "comp_a", "link_type": "equation", "link_id": "eq_x"}],
        "dangling_component_refs": [],
        "teaching_granularity_warnings": [],
    })
    result = _run_gate(component_result=comp_result)
    assert result.component_refinement_validation["split_components"] == ["comp_a"]
    assert any(w.code == "COMPONENT_REFINEMENT_UNASSIGNED_LINK" for w in result.warnings)


def test_component_refinement_dangling_ref_is_hard_error():
    comp_result = _RefinedComponentResult({
        "split_components": [],
        "unchanged_components": [],
        "failed_refinements": [],
        "review_required_refinements": [],
        "unassigned_links": [],
        "dangling_component_refs": [{"component_id": "comp_b", "missing_ref": "comp_gone"}],
        "teaching_granularity_warnings": [],
    })
    result = _run_gate(component_result=comp_result)
    assert result.status == "failed_validation"
    assert any(e.code == "COMPONENT_REFINEMENT_DANGLING_REF" for e in result.errors)


def test_component_refinement_review_required_and_teaching_warning():
    comp_result = _RefinedComponentResult({
        "split_components": [],
        "unchanged_components": [],
        "failed_refinements": [],
        "review_required_refinements": ["comp_c"],
        "unassigned_links": [],
        "dangling_component_refs": [],
        "teaching_granularity_warnings": [{"component_id": "comp_c__r1"}],
    })
    result = _run_gate(component_result=comp_result)
    assert any(r.code == "COMPONENT_REFINEMENT_REVIEW_REQUIRED" for r in result.review_items)
    assert any(w.code == "COMPONENT_REFINEMENT_TEACHING_GRANULARITY" for w in result.warnings)


# ---------------------------------------------------------------------------
# Step 5: theory bundle + teaching output (issue #326)
# ---------------------------------------------------------------------------


class _BundleComponentResult:
    """component_result carrying a Step 5 theory_bundle artifact block."""

    def __init__(self, components, theory_bundle):
        self.components = components
        self.theory_bundle = theory_bundle


def _bundle_block(*, bundle=None, course_mapping=None, blueprint_updates=None,
                  theory_bundle_validation=None, teaching_output_validation=None):
    return {
        "theory_bundle": bundle or {},
        "course_mapping": course_mapping or {"topics": []},
        "blueprint_updates": blueprint_updates or {"linked_component_ids": [], "review_required_items": []},
        "theory_bundle_validation": theory_bundle_validation or {},
        "teaching_output_validation": teaching_output_validation or {},
    }


def test_theory_bundle_absent_is_empty_default():
    result = _run_gate(component_result=_ComponentResult([]))
    assert result.theory_bundle_validation["bundle_created"] is False
    assert result.teaching_output_validation["course_topics_link_components"] is True


def test_theory_bundle_clean_passes_and_reports_booleans():
    comp = _ComponentRecord("c1", review_status="source_backed")
    block = _bundle_block(
        bundle={
            "component_ids": ["c1"],
            "headline_claim_id": "claim_head",
            "support_map_id": "doc:support_map",
            "review_status": "source_backed",
        },
        course_mapping={"topics": [{
            "topic_id": "topic:c1",
            "linked_component_ids": ["c1"],
            "blackbox_policy": [],
            "review_status": "source_backed",
        }]},
        blueprint_updates={"linked_component_ids": ["c1"], "review_required_items": []},
        theory_bundle_validation={
            "bundle_created": True, "headline_claim_linked": True, "support_map_linked": True,
        },
        teaching_output_validation={
            "course_topics_link_components": True, "blueprint_refs_valid": True,
            "blackbox_policy_respects_confidence": True,
        },
    )
    result = _run_gate(component_result=_BundleComponentResult([comp], block))
    assert result.theory_bundle_validation["component_refs_valid"] is True
    assert result.theory_bundle_validation["headline_claim_linked"] is True
    assert result.theory_bundle_validation["support_map_linked"] is True
    assert result.teaching_output_validation["course_topics_link_components"] is True
    assert result.teaching_output_validation["blackbox_policy_respects_confidence"] is True
    assert not any(e.code.startswith("THEORY_BUNDLE") for e in result.errors)


def test_theory_bundle_dangling_component_ref_is_hard_error():
    comp = _ComponentRecord("c1")
    block = _bundle_block(bundle={
        "component_ids": ["c1", "ghost"],
        "headline_claim_id": "claim_head",
        "support_map_id": "doc:support_map",
        "review_status": "source_backed",
    })
    result = _run_gate(component_result=_BundleComponentResult([comp], block))
    assert result.status == "failed_validation"
    assert result.theory_bundle_validation["component_refs_valid"] is False
    assert any(e.code == "THEORY_BUNDLE_DANGLING_COMPONENT_REF" for e in result.errors)


def test_teaching_output_dangling_topic_ref_is_hard_error():
    comp = _ComponentRecord("c1")
    block = _bundle_block(
        bundle={"component_ids": ["c1"], "headline_claim_id": "h",
                "support_map_id": "s", "review_status": "source_backed"},
        course_mapping={"topics": [{
            "topic_id": "topic:ghost",
            "linked_component_ids": ["ghost"],
            "blackbox_policy": [],
        }]},
    )
    result = _run_gate(component_result=_BundleComponentResult([comp], block))
    assert result.status == "failed_validation"
    assert result.teaching_output_validation["course_topics_link_components"] is False
    assert any(e.code == "TEACHING_OUTPUT_DANGLING_COMPONENT_REF" for e in result.errors)


def test_teaching_output_blackbox_policy_must_respect_equation_confidence():
    # c1 carries a low-confidence equation but the topic does not blackbox it.
    comp = _ComponentRecord("c1", review_required_equation_ids=["eq_bad"])
    block = _bundle_block(
        bundle={"component_ids": ["c1"], "headline_claim_id": "h",
                "support_map_id": "s", "review_status": "review_required"},
        course_mapping={"topics": [{
            "topic_id": "topic:c1",
            "linked_component_ids": ["c1"],
            "blackbox_policy": [],  # missing eq_bad
            "review_status": "review_required",
        }]},
        teaching_output_validation={"blackbox_policy_respects_confidence": True},
    )
    result = _run_gate(component_result=_BundleComponentResult([comp], block))
    assert result.teaching_output_validation["blackbox_policy_respects_confidence"] is False
    assert any(
        e.code == "TEACHING_OUTPUT_BLACKBOX_POLICY_IGNORES_CONFIDENCE" for e in result.errors
    )


def test_theory_bundle_review_required_blocks_publish_ready():
    comp = _ComponentRecord("c1")
    block = _bundle_block(
        bundle={"component_ids": ["c1"], "headline_claim_id": "h",
                "support_map_id": "s", "review_status": "review_required"},
        course_mapping={"topics": [{
            "topic_id": "topic:c1", "linked_component_ids": ["c1"],
            "blackbox_policy": [], "review_status": "review_required",
        }]},
    )
    result = _run_gate(component_result=_BundleComponentResult([comp], block))
    assert result.publish_ready is False
    assert any(r.code == "THEORY_BUNDLE_REVIEW_REQUIRED" for r in result.review_items)
    assert any(r.code == "TEACHING_OUTPUT_TOPIC_REVIEW_REQUIRED" for r in result.review_items)


def test_teaching_output_blueprint_dangling_ref_is_hard_error():
    comp = _ComponentRecord("c1")
    block = _bundle_block(
        bundle={"component_ids": ["c1"], "headline_claim_id": "h",
                "support_map_id": "s", "review_status": "source_backed"},
        course_mapping={"topics": [{
            "topic_id": "topic:c1", "linked_component_ids": ["c1"], "blackbox_policy": [],
        }]},
        blueprint_updates={"linked_component_ids": ["ghost"], "review_required_items": []},
    )
    result = _run_gate(component_result=_BundleComponentResult([comp], block))
    assert result.teaching_output_validation["blueprint_refs_valid"] is False
    assert any(e.code == "TEACHING_OUTPUT_BLUEPRINT_DANGLING_REF" for e in result.errors)
