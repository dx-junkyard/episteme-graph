"""Tests for the ExportValidationGate thesis coverage report (issue #354)."""
from __future__ import annotations

import importlib.util
import os
import sys

# Import the gate module directly to avoid pulling in sqlalchemy via __init__.py
_GATE_PATH = os.path.join(
    os.path.dirname(__file__),
    "../../../backend/core/document_pipeline/export_validation_gate.py",
)
_spec = importlib.util.spec_from_file_location("export_validation_gate_tc", _GATE_PATH)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["export_validation_gate_tc"] = _mod
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]

ExportValidationGate = _mod.ExportValidationGate


# ---------------------------------------------------------------------------
# Minimal stubs
# ---------------------------------------------------------------------------

class _ClaimObject:
    def __init__(self, claim_id: str):
        self.claim_id = claim_id
        self.support_status = "source_backed"
        self.source_evidence_ids = ["ev_001"]
        self.atomicity = "atomic"
        self.claim_type = "definition"


class _ClaimObjectResult:
    def __init__(self, claims):
        self.claims = claims


class _EvidenceRecord:
    def __init__(self, evidence_id: str):
        self.evidence_id = evidence_id


class _EvidenceResult:
    def __init__(self, records):
        self.records = records


class _ComponentRecord:
    def __init__(self, component_id, *, linked_claim_ids=None, linked_equation_ids=None):
        self.component_id = component_id
        self.component_type = "ClaimBundleComponent"
        self.label = component_id
        self.summary = ""
        self.evidence_refs = {"claim_ids": list(linked_claim_ids or []),
                              "evidence_ids": ["ev_001"]}
        self.linked_claim_ids = list(linked_claim_ids or [])
        self.linked_equation_ids = list(linked_equation_ids or [])
        self.review_status = "teacher_review_required"
        self.internal_flow = []
        self.maturity_source = "llm_proposed"
        self.fallback_reason = ""
        self.inputs = []
        self.outputs = []
        self.preconditions = []
        self.cautions = []


class _ComponentResult:
    def __init__(self, components):
        self.components = components


def _thesis_artifact(*, claim_ids=("claim_main",), equation_ids=("eq_final",),
                     assumptions=None):
    support = {section: [] for section in (
        "direct_supports", "assumptions", "derivation_core", "correction_sources",
        "uncertainty_sources", "diagnostic_consequences", "future_requirements",
    )}
    if assumptions:
        support["assumptions"] = list(assumptions)
    return {
        "document_id": "doc",
        "central_thesis": {
            "text": "central thesis",
            "claim_ids": list(claim_ids),
            "equation_ids": list(equation_ids),
            "evidence_block_ids": [],
            "reason": "r",
            "confidence": 0.9,
        },
        "support_structure": support,
        "validation_issues": [],
    }


def _graph_artifact(*, main_claim_ids=("claim_main",), main_equation_ids=(),
                    member_equation_ids=("eq_final",)):
    """Main node backed by claims plus an equation_detail member node."""
    return {
        "nodes": [
            {
                "component_id": "main_1",
                "label": "Theory basis",
                "component_type": "TheoryOperationNode",
                "graph_layer": "main",
                "source_backing_status": "source_backed",
                "linked_claim_ids": list(main_claim_ids),
                "linked_equation_ids": list(main_equation_ids),
                "member_component_ids": ["detail_1"],
            },
            {
                "component_id": "detail_1",
                "label": "derive eq",
                "component_type": "EquationOperationNode",
                "graph_layer": "equation_detail",
                "source_backing_status": "source_backed",
                "parent_component_id": "main_1",
                "linked_claim_ids": [],
                "linked_equation_ids": list(member_equation_ids),
            },
        ],
        "edges": [],
    }


def _make_artifacts(**kwargs):
    base = {
        "document_structure": {"blocks": [], "sections": []},
        "source_chunking": [{"text": "chunk"}],
        "claim_qualification": {"qualified_spans": []},
        "component_assembly": {"components": [], "validation_issues": []},
        "equation_semantics": {"equations": [{"equation_id": "eq_final"}]},
    }
    base.update(kwargs)
    return base


def _run_gate(artifacts, *, component_result=None, claim_objects=None):
    gate = ExportValidationGate()
    return gate.run(
        artifacts=artifacts,
        component_result=component_result,
        course_mapping=None,
        claim_objects=claim_objects,
        evidence=_EvidenceResult([_EvidenceRecord("ev_001")]),
        dsl=None,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_fully_backed_thesis_has_full_coverage():
    """全参照が main graph に到達する fixture で coverage = 1.0."""
    artifacts = _make_artifacts(
        thesis_reconstruction=_thesis_artifact(),
        component_graph=_graph_artifact(),
    )
    result = _run_gate(
        artifacts,
        claim_objects=_ClaimObjectResult([_ClaimObject("claim_main")]),
    )
    coverage = result.thesis_coverage
    assert coverage["thesis_present"] is True
    assert coverage["coverage_by_section"]["central_thesis"] == 1.0
    assert coverage["unreachable_refs"] == []
    assert coverage["total_refs"] == 2
    assert coverage["reachable_refs"] == 2
    assert not any(
        r.code == "THESIS_SUPPORT_UNREACHABLE" for r in result.review_items
    )


def test_dropped_claim_is_reported_unreachable():
    """thesis 参照 claim が claims.json から脱落 → missing_claim で report."""
    artifacts = _make_artifacts(
        thesis_reconstruction=_thesis_artifact(claim_ids=("claim_dropped",)),
        component_graph=_graph_artifact(),
    )
    result = _run_gate(
        artifacts,
        claim_objects=_ClaimObjectResult([_ClaimObject("claim_main")]),
    )
    unreachable = result.thesis_coverage["unreachable_refs"]
    assert any(
        u["ref_id"] == "claim_dropped"
        and u["reason"] == "missing_claim"
        and u["review_reasons"] == ["thesis_support_unreachable"]
        for u in unreachable
    )
    assert any(
        r.code == "THESIS_SUPPORT_UNREACHABLE" for r in result.review_items
    )
    assert result.thesis_coverage["coverage_by_section"]["central_thesis"] == 0.5


def test_existing_claim_not_linked_to_main_graph_is_unreachable():
    """claims.json には存在するが main graph に link されない claim."""
    artifacts = _make_artifacts(
        thesis_reconstruction=_thesis_artifact(
            claim_ids=("claim_orphan",), equation_ids=(),
        ),
        component_graph=_graph_artifact(main_claim_ids=("claim_main",)),
    )
    result = _run_gate(
        artifacts,
        claim_objects=_ClaimObjectResult(
            [_ClaimObject("claim_main"), _ClaimObject("claim_orphan")]
        ),
    )
    unreachable = result.thesis_coverage["unreachable_refs"]
    assert any(
        u["ref_id"] == "claim_orphan" and u["reason"] == "not_linked_to_main_graph"
        for u in unreachable
    )


def test_support_section_coverage_is_reported_per_section():
    artifacts = _make_artifacts(
        thesis_reconstruction=_thesis_artifact(
            assumptions=[{
                "text": "assumption",
                "claim_ids": ["claim_assume"],
                "equation_ids": [],
                "support_type": "assumption_support",
                "reason": "r",
                "confidence": 0.8,
            }],
        ),
        component_graph=_graph_artifact(
            main_claim_ids=("claim_main", "claim_assume"),
        ),
    )
    result = _run_gate(
        artifacts,
        claim_objects=_ClaimObjectResult(
            [_ClaimObject("claim_main"), _ClaimObject("claim_assume")]
        ),
    )
    coverage = result.thesis_coverage["coverage_by_section"]
    assert coverage["central_thesis"] == 1.0
    assert coverage["assumptions"] == 1.0


def test_thesis_absent_degrades_to_empty_report():
    """thesis 不在 → thesis_present=False、review item なし."""
    result = _run_gate(_make_artifacts())
    assert result.thesis_coverage["thesis_present"] is False
    assert result.thesis_coverage["coverage_by_section"] == {}
    assert not any(
        r.code == "THESIS_SUPPORT_UNREACHABLE" for r in result.review_items
    )


def test_unreachable_thesis_refs_never_produce_hard_errors():
    """カバレッジ欠落は review item のみで hard error を出さない."""
    artifacts = _make_artifacts(
        thesis_reconstruction=_thesis_artifact(claim_ids=("claim_dropped",)),
        component_graph=_graph_artifact(),
    )
    result = _run_gate(
        artifacts,
        claim_objects=_ClaimObjectResult([_ClaimObject("claim_main")]),
    )
    assert not any(
        e.code == "THESIS_SUPPORT_UNREACHABLE" for e in result.errors
    )
    assert result.exportable is True
    assert result.status == "needs_review"


def test_components_stand_in_when_component_graph_absent():
    """component_graph artifact が無い場合は components に fallback する."""
    artifacts = _make_artifacts(thesis_reconstruction=_thesis_artifact())
    component_result = _ComponentResult([
        _ComponentRecord(
            "comp_1",
            linked_claim_ids=["claim_main"],
            linked_equation_ids=["eq_final"],
        ),
    ])
    result = _run_gate(
        artifacts,
        component_result=component_result,
        claim_objects=_ClaimObjectResult([_ClaimObject("claim_main")]),
    )
    assert result.thesis_coverage["coverage_by_section"]["central_thesis"] == 1.0
    assert result.thesis_coverage["unreachable_refs"] == []


def test_equation_detail_member_backing_counts_for_main_node():
    """main node 自身ではなく member detail node が式を持つ場合も到達扱い."""
    artifacts = _make_artifacts(
        thesis_reconstruction=_thesis_artifact(claim_ids=()),
        component_graph=_graph_artifact(
            main_equation_ids=(), member_equation_ids=("eq_final",),
        ),
    )
    result = _run_gate(
        artifacts,
        claim_objects=_ClaimObjectResult([_ClaimObject("claim_main")]),
    )
    assert result.thesis_coverage["coverage_by_section"]["central_thesis"] == 1.0


def test_thesis_coverage_in_result_dict():
    result = _run_gate(_make_artifacts(thesis_reconstruction=_thesis_artifact()))
    d = result.to_dict()
    assert "thesis_coverage" in d
    assert d["thesis_coverage"]["thesis_present"] is True
