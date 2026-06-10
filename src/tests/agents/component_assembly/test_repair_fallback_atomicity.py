"""Regression tests for deterministic fallback claim atomicity (issue #345).

The deterministic fallback in ComponentAssemblyRepairer must never build a
component whose primary claim support points at a non-atomic claim
(composite / split_required / compound / non_atomic / split_pending). Otherwise
the final ExportValidationGate rejects the bundle with
``NON_ATOMIC_CLAIM_USED_AS_COMPONENT_SUPPORT``.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from dataclasses import dataclass, field

from episteme_graph.agents.component_assembly.repair import make_deterministic_fallback
from episteme_graph.agents.component_assembly.schema import ComponentAssemblyLLMInput

# Import the gate module directly to avoid pulling in sqlalchemy via __init__.py.
_GATE_PATH = os.path.join(
    os.path.dirname(__file__),
    "../../../../backend/core/document_pipeline/export_validation_gate.py",
)
_spec = importlib.util.spec_from_file_location("export_validation_gate_fallback", _GATE_PATH)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["export_validation_gate_fallback"] = _mod
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]

ExportValidationGate = _mod.ExportValidationGate


def _llm_input(available_claims: list[dict]) -> ComponentAssemblyLLMInput:
    return ComponentAssemblyLLMInput(
        document_id="doc_test",
        cartridge_id=None,
        accepted_claims=[],
        equations=[],
        thesis_nodes=[],
        dsl_nodes=[],
        dsl_edges=[],
        allowed_component_types=["ClaimBundleComponent"],
        allowed_dependency_types=[],
        available_claims=available_claims,
    )


def _available_claim(claim_id: str, *, atomicity: str = "atomic", is_atomic: bool = True) -> dict:
    return {
        "claim_id": claim_id,
        "claim_type": "result",
        "text": f"text for {claim_id}",
        "source_evidence_ids": [f"ev_{claim_id}"],
        "equation_ids": [],
        "support_status": "source_backed",
        "review_status": "source_backed",
        "atomicity": atomicity,
        "is_atomic": is_atomic,
        "split_suggestions": [],
        "concepts": [],
        "concept_assignment_status": "ok",
    }


# Minimal claim_objects / evidence stand-ins for driving the export gate's
# NON_ATOMIC_CLAIM_USED_AS_COMPONENT_SUPPORT check end-to-end.
@dataclass
class _ClaimObj:
    claim_id: str
    atomicity: str = "atomic"
    is_atomic: bool = True
    source_evidence_ids: list[str] = field(default_factory=list)
    claim_type: str = "result"
    support_status: str = "source_backed"


@dataclass
class _Claims:
    claims: list[_ClaimObj]


@dataclass
class _EvidenceRecord:
    evidence_id: str


@dataclass
class _Evidence:
    records: list[_EvidenceRecord]


def test_fallback_uses_only_atomic_children_when_composite_parent_present():
    """A composite parent plus atomic children → fallback uses only the children."""
    available = [
        _available_claim("claim_parent", atomicity="composite", is_atomic=False),
        _available_claim("claim_child_a"),
        _available_claim("claim_child_b"),
    ]
    result = make_deterministic_fallback(_llm_input(available), "Repair failed after max attempts")

    backed = {
        cid
        for component in result.components
        for cid in component.evidence_refs["claim_ids"]
    }
    assert backed == {"claim_child_a", "claim_child_b"}
    # No component refers to the composite parent in any claim-support field.
    for component in result.components:
        assert "claim_parent" not in component.evidence_refs["claim_ids"]
        assert "claim_parent" not in component.linked_claim_ids
        assert "claim_parent" not in component.supports_claim_ids


def test_fallback_skips_when_only_non_atomic_claim_available():
    """Only a non-atomic claim available → no invalid component is created."""
    available = [
        _available_claim("claim_span_001", atomicity="composite", is_atomic=False),
    ]
    result = make_deterministic_fallback(_llm_input(available), "Repair failed after max attempts")

    for component in result.components:
        assert "claim_span_001" not in component.evidence_refs["claim_ids"]
        assert "claim_span_001" not in component.linked_claim_ids
        assert "claim_span_001" not in component.supports_claim_ids

    # And the export gate does not fail because of fallback output.
    claim_objects = _Claims([_ClaimObj("claim_span_001", atomicity="composite", is_atomic=False)])
    evidence = _Evidence([_EvidenceRecord("ev_claim_span_001")])
    gate_result = ExportValidationGate().run(
        artifacts={},
        component_result=result,
        claim_objects=claim_objects,
        evidence=evidence,
    )
    assert not any(
        e.code == "NON_ATOMIC_CLAIM_USED_AS_COMPONENT_SUPPORT" for e in gate_result.errors
    )


def test_export_gate_still_rejects_explicit_non_atomic_component_support():
    """The gate stays strict: an explicit non-atomic claim ref still fails."""
    available = [_available_claim("claim_child_a")]
    result = make_deterministic_fallback(_llm_input(available), "Repair failed after max attempts")
    # Force a component to explicitly cite a non-atomic claim (not via fallback).
    result.components[0].evidence_refs["claim_ids"] = ["claim_bad"]
    result.components[0].linked_claim_ids = ["claim_bad"]
    result.components[0].supports_claim_ids = ["claim_bad"]

    claim_objects = _Claims([_ClaimObj("claim_bad", atomicity="composite", is_atomic=False)])
    evidence = _Evidence([_EvidenceRecord("ev_claim_child_a")])
    gate_result = ExportValidationGate().run(
        artifacts={},
        component_result=result,
        claim_objects=claim_objects,
        evidence=evidence,
    )
    assert any(
        e.code == "NON_ATOMIC_CLAIM_USED_AS_COMPONENT_SUPPORT" for e in gate_result.errors
    )
