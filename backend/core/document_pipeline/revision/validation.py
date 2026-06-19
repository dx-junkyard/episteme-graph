"""Candidate re-validation for the iterative-improvement pipeline (#406 / #410 P1-2).

Re-applies the existing ``ExportValidationGate`` to the candidate **with the same
inputs the initial pipeline uses**: the agent Result objects are reconstructed
from the candidate artifacts (ComponentAssemblyResult / ClaimObjectBuildResult /
EvidenceRegistryResult / DSLLinkingResult) so the cross-artifact checks
(reference integrity, source-backed evidence, claim atomicity, component quality,
DSL edges, …) actually run.

Fail-closed contract (#410 P1-2): if reconstruction of a *present* artifact, the
gate import, or the gate run fails, validation returns a ``failed_validation``
result carrying a hard error — never ``passed_with_warnings`` — so a candidate
whose validation could not complete can never become acceptable.
"""
from __future__ import annotations

from typing import Any


def _failed_gate_result(note: str) -> dict:
    """Fail-closed gate result: a hard error so the candidate is never acceptable."""
    return {
        "status": "failed_validation",
        "exportable": False,
        "publish_ready": False,
        "errors": [{
            "code": "CANDIDATE_VALIDATION_FAILED",
            "message": note,
            "artifact": "candidate",
            "path": "",
            "source_stage": "export_validation",
        }],
        "warnings": [],
        "review_items": [],
        "summary": {"error_count": 1, "warning_count": 0, "review_required_count": 0},
    }


def _reconstruct_inputs(candidate_artifacts: dict):
    """Reconstruct the gate's Result inputs from candidate artifact dicts.

    Only *present* artifacts are reconstructed; an absent artifact yields None.
    A present-but-unparseable artifact raises (→ fail-closed upstream), because a
    candidate that cannot be deserialized is an integrity problem, not a pass.
    """
    component_result = claim_objects = evidence = dsl = None

    ca = candidate_artifacts.get("component_assembly")
    if ca:
        from episteme_graph.agents.component_assembly.schema import ComponentAssemblyResult
        component_result = ComponentAssemblyResult.from_dict(ca)

    cob = candidate_artifacts.get("claim_object_builder")
    if cob:
        from episteme_graph.agents.claim_object_builder.schema import ClaimObjectBuildResult
        claim_objects = ClaimObjectBuildResult.from_dict(cob)

    ev = candidate_artifacts.get("evidence_registry")
    if ev:
        from episteme_graph.agents.evidence_registry.schema import EvidenceRegistryResult
        evidence = EvidenceRegistryResult.from_dict(ev)

    dl = candidate_artifacts.get("dsl_linking")
    if dl:
        from episteme_graph.agents.dsl_linking.schema import DSLLinkingResult
        dsl = DSLLinkingResult.from_dict(dl)

    return component_result, claim_objects, evidence, dsl


def run_export_validation(candidate_artifacts: dict) -> dict:
    """Run the ExportValidationGate against the candidate with full inputs.

    Always returns a gate-result dict. Reconstruction / import / runtime failures
    yield a fail-closed ``failed_validation`` result (hard error), never a silent
    pass — so report generation can never mark an unvalidated candidate
    acceptable, and it still cannot disturb the active run.
    """
    candidate_artifacts = dict(candidate_artifacts or {})
    try:
        component_result, claim_objects, evidence, dsl = _reconstruct_inputs(candidate_artifacts)
    except Exception as exc:
        return _failed_gate_result(f"candidate artifact reconstruction failed: {exc}")

    try:
        from ..export_validation_gate import ExportValidationGate
    except Exception as exc:  # pragma: no cover - import guard
        return _failed_gate_result(f"export validation gate import failed: {exc}")

    try:
        gate = ExportValidationGate()
        result = gate.run(
            artifacts=candidate_artifacts,
            component_result=component_result,
            claim_objects=claim_objects,
            evidence=evidence,
            dsl=dsl,
        )
        return result.to_dict() if hasattr(result, "to_dict") else dict(result)
    except Exception as exc:
        return _failed_gate_result(f"export validation gate raised: {exc}")


def gate_error_count(gate_result: Any) -> int:
    summary = (gate_result or {}).get("summary") or {}
    try:
        return int(summary.get("error_count") or 0)
    except (TypeError, ValueError):
        return len((gate_result or {}).get("errors") or [])


def candidate_has_hard_errors(gate_result: Any) -> bool:
    return gate_error_count(gate_result) > 0
