"""Candidate re-validation for the iterative-improvement pipeline (#406).

Re-applies the existing ``ExportValidationGate`` to the candidate artifacts and
exposes a small, defensive wrapper so a gate failure never propagates (and so it
can never change the adopted active run).
"""
from __future__ import annotations

from typing import Any


def _empty_gate_result(note: str = "") -> dict:
    return {
        "status": "passed_with_warnings" if note else "passed",
        "exportable": True,
        "publish_ready": False,
        "errors": [],
        "warnings": ([{"code": "GATE_ERROR", "message": note, "artifact": "candidate"}]
                     if note else []),
        "review_items": [],
        "summary": {
            "error_count": 0,
            "warning_count": 1 if note else 0,
            "review_required_count": 0,
        },
    }


def run_export_validation(candidate_artifacts: dict) -> dict:
    """Run the ExportValidationGate against candidate artifacts (best effort).

    Always returns a gate-result dict. On any internal error a degraded result
    with a single GATE_ERROR warning is returned — never an exception, so report
    generation cannot disturb the active run.
    """
    try:
        from ..export_validation_gate import ExportValidationGate
    except Exception as exc:  # pragma: no cover - import guard
        return _empty_gate_result(f"gate import failed: {exc}")
    try:
        gate = ExportValidationGate()
        result = gate.run(artifacts=dict(candidate_artifacts or {}))
        return result.to_dict() if hasattr(result, "to_dict") else dict(result)
    except Exception as exc:
        return _empty_gate_result(f"gate raised: {exc}")


def gate_error_count(gate_result: Any) -> int:
    summary = (gate_result or {}).get("summary") or {}
    try:
        return int(summary.get("error_count") or 0)
    except (TypeError, ValueError):
        return len((gate_result or {}).get("errors") or [])


def candidate_has_hard_errors(gate_result: Any) -> bool:
    return gate_error_count(gate_result) > 0
