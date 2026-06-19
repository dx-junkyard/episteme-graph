"""Revision-run coordinator (#402 + #403).

Glue between the run version-management helpers and the deterministic baseline
inventory / checkpoint planner. ``build_revision_plan`` is pure (operates on a
base-run dict); ``start_revision_run`` performs the DB side effects (create a
candidate run, persist inventory + checkpoints as that run's artifacts) without
ever touching projection tables or the adopted active run.
"""
from __future__ import annotations

from typing import Any, Callable

from .. import persistence
from .audit import run_source_audit
from .checkpoints import plan_checkpoints
from .diff import build_diff_report
from .inventory import ARTIFACTS_KEY, build_baseline_inventory, get_artifacts
from .operations import apply_operations
from .validation import run_export_validation

# Revision artifact keys (stored under stage_outputs._artifacts of the run).
BASELINE_INVENTORY_KEY = "baseline_inventory"
AUDIT_CHECKPOINTS_KEY = "audit_checkpoints"
AUDIT_RESULTS_KEY = "audit_results"
REVISION_OPERATIONS_KEY = "revision_operations"
CANDIDATE_KEY = "candidate"
CANDIDATE_VALIDATION_KEY = "candidate_validation"
DIFF_REPORT_KEY = "diff_report"


def build_revision_plan(base_run: dict, *, document_id: str = "") -> dict:
    """Build inventory + checkpoints from a base run dict (no side effects)."""
    base_run = base_run or {}
    base_run_id = str(base_run.get("id") or "")
    document_id = document_id or str(base_run.get("document_id") or "")
    artifacts = get_artifacts(base_run.get("stage_outputs"))
    inventory = build_baseline_inventory(
        artifacts, base_run_id=base_run_id, document_id=document_id
    )
    checkpoints = plan_checkpoints(inventory)
    return {
        BASELINE_INVENTORY_KEY: inventory,
        AUDIT_CHECKPOINTS_KEY: checkpoints,
    }


def start_revision_run(
    *,
    document_id: str,
    created_by: str | None = None,
    material_id: str | None = None,
    cartridge_id: str | None = None,
    base_run: dict | None = None,
) -> dict:
    """Create a candidate revision run and persist its baseline plan.

    Resolves the adopted (active) run as the base — falling back to the latest
    completed run for legacy documents. Raises ValueError if no adoptable base
    run exists. The candidate run holds the inventory/checkpoints as artifacts
    and never mutates the base run, active pointer, or projection tables.
    """
    base = base_run or persistence.resolve_artifact_run(
        document_id=document_id, material_id=material_id
    )
    if not base or not base.get("id"):
        raise ValueError(
            f"no adoptable base run for document {document_id}; cannot start a revision"
        )
    base_run_id = str(base["id"])

    run_id = persistence.create_revision_run(
        document_id=document_id,
        base_run_id=base_run_id,
        material_id=material_id or base.get("material_id"),
        cartridge_id=cartridge_id or base.get("cartridge_id"),
        created_by=created_by,
        revision_status="preparing",
    )

    plan = build_revision_plan(base, document_id=document_id)
    persistence.update_revision_status(
        run_id=run_id,
        revision_status="preparing",
        stage_outputs={ARTIFACTS_KEY: plan},
    )
    return {
        "revision_run_id": run_id,
        "base_run_id": base_run_id,
        "document_id": document_id,
        **plan,
    }


def audit_revision_run(
    *,
    run_id: str,
    llm_client: Callable[[dict, dict], Any] | None = None,
    chunk_index: list[dict] | None = None,
) -> dict:
    """Run the source re-audit stage for a candidate run.

    Reads the run's planned checkpoints, re-reads the source per checkpoint, and
    stores audit results as a run artifact. This stage evaluates only — it never
    builds candidate artifacts nor touches the base/active run (AC #404).
    """
    run = persistence.get_analysis_run(run_id=run_id)
    if not run:
        raise ValueError(f"revision run {run_id} not found")
    if run.get("run_type") != "revision":
        raise ValueError(f"run {run_id} is not a revision run")

    artifacts = get_artifacts(run.get("stage_outputs"))
    checkpoints = artifacts.get(AUDIT_CHECKPOINTS_KEY) or []
    document_id = str(run.get("document_id") or "")

    if chunk_index is None:
        try:
            chunk_index = persistence.load_source_chunk_index(document_id=document_id)
        except Exception:
            chunk_index = []

    audit = run_source_audit(
        checkpoints=checkpoints,
        chunk_index=chunk_index or [],
        llm_client=llm_client,
    )
    persistence.update_revision_status(
        run_id=run_id,
        revision_status="auditing",
        stage_outputs={ARTIFACTS_KEY: {AUDIT_RESULTS_KEY: audit}},
    )
    return {"revision_run_id": run_id, "document_id": document_id, **audit}


def assemble_candidate(
    *,
    run_id: str,
    operations: list[dict],
    base_run: dict | None = None,
) -> dict:
    """Apply explicit revision operations and persist the candidate artifacts.

    Base artifacts are read immutably from the run's ``base_run_id``. The
    candidate is stored only on the revision run; projection tables and the
    active run are never touched. An invalid candidate (failed op or new unknown
    reference) is persisted but marked invalid so it can never be adopted.
    """
    run = persistence.get_analysis_run(run_id=run_id)
    if not run:
        raise ValueError(f"revision run {run_id} not found")
    if run.get("run_type") != "revision":
        raise ValueError(f"run {run_id} is not a revision run")
    base_run_id = run.get("base_run_id")
    if not base_run_id:
        raise ValueError(f"revision run {run_id} has no base_run_id")

    base = base_run or persistence.get_analysis_run(run_id=str(base_run_id))
    if not base:
        raise ValueError(f"base run {base_run_id} not found")
    base_artifacts = get_artifacts(base.get("stage_outputs"))

    run_artifacts = get_artifacts(run.get("stage_outputs"))
    base_inventory = run_artifacts.get(BASELINE_INVENTORY_KEY) or {}

    result = apply_operations(
        base_artifacts, operations or [], base_inventory=base_inventory
    )

    persistence.update_revision_status(
        run_id=run_id,
        revision_status="proposed",
        stage_outputs={ARTIFACTS_KEY: {
            REVISION_OPERATIONS_KEY: result["applied_operations"],
            CANDIDATE_KEY: {
                "candidate_artifacts": result["candidate_artifacts"],
                "id_mapping": result["id_mapping"],
                "operation_errors": result["operation_errors"],
                "protected_changes": result["protected_changes"],
                "dropped_edges": result["dropped_edges"],
                "carried_unresolved_references": result["carried_unresolved_references"],
                "invalid": result["invalid"],
                "requires_confirmation": result["requires_confirmation"],
            },
        }},
    )
    return {"revision_run_id": run_id, "base_run_id": str(base_run_id), **result}


def revalidate_and_report(*, run_id: str, base_run: dict | None = None) -> dict:
    """Re-validate the candidate and build the before/after diff report (#406).

    Applies the existing ExportValidationGate to the candidate artifacts, diffs
    quality + findings against the base run, and stores the report as a run
    artifact. Never touches the active run or projections; a gate failure is
    captured as a degraded result rather than raised, so it cannot disturb the
    adopted run.
    """
    run = persistence.get_analysis_run(run_id=run_id)
    if not run:
        raise ValueError(f"revision run {run_id} not found")
    if run.get("run_type") != "revision":
        raise ValueError(f"run {run_id} is not a revision run")
    base_run_id = run.get("base_run_id")
    base = base_run or persistence.get_analysis_run(run_id=str(base_run_id))
    if not base:
        raise ValueError(f"base run {base_run_id} not found")

    base_artifacts = get_artifacts(base.get("stage_outputs"))
    run_artifacts = get_artifacts(run.get("stage_outputs"))
    candidate = run_artifacts.get(CANDIDATE_KEY) or {}
    candidate_artifacts = candidate.get("candidate_artifacts") or {}
    applied_operations = run_artifacts.get(REVISION_OPERATIONS_KEY) or []
    audit_results = (run_artifacts.get(AUDIT_RESULTS_KEY) or {}).get("audit_results") or []

    gate_base = run_export_validation(base_artifacts)
    gate_candidate = run_export_validation(candidate_artifacts)

    report = build_diff_report(
        base_run_id=str(base_run_id or ""),
        candidate_run_id=str(run_id),
        base_artifacts=base_artifacts,
        candidate_artifacts=candidate_artifacts,
        applied_operations=applied_operations,
        gate_base=gate_base,
        gate_candidate=gate_candidate,
        candidate_invalid=bool(candidate.get("invalid")),
        protected_changes=candidate.get("protected_changes") or [],
        requires_confirmation=bool(candidate.get("requires_confirmation")),
        id_mapping=candidate.get("id_mapping") or {},
        audit_verifications=[
            {"checkpoint_id": a.get("checkpoint_id"), "target_id": a.get("target_id"),
             "verdict": a.get("verdict"), "source_locations": a.get("source_locations") or []}
            for a in audit_results if a.get("requires_revision")
        ],
    )

    persistence.update_revision_status(
        run_id=run_id,
        revision_status="proposed",
        stage_outputs={ARTIFACTS_KEY: {
            CANDIDATE_VALIDATION_KEY: gate_candidate,
            DIFF_REPORT_KEY: report,
        }},
    )
    return {"revision_run_id": run_id, "report": report, "candidate_validation": gate_candidate}


class AcceptBlockedError(RuntimeError):
    """Raised when a candidate cannot be accepted (hard errors / unconfirmed protected)."""


def accept_revision(
    *,
    document_id: str,
    run_id: str,
    changed_by: str | None = None,
    comment: str = "",
    confirm_protected: bool = False,
) -> dict:
    """Validate-then-accept a candidate revision (#407).

    Refuses candidates with hard errors / invalid status (AcceptBlockedError) and
    candidates with unconfirmed protected changes. Delegates the atomic state
    switch to ``persistence.accept_revision`` (raises RevisionConflictError → 409).
    """
    run = persistence.get_analysis_run(run_id=run_id)
    if not run:
        raise ValueError(f"revision run {run_id} not found")
    if run.get("run_type") != "revision":
        raise ValueError(f"run {run_id} is not a revision run")
    if document_id and str(run.get("document_id")) != str(document_id):
        raise ValueError("revision run does not belong to this document")

    run_artifacts = get_artifacts(run.get("stage_outputs"))
    report = run_artifacts.get(DIFF_REPORT_KEY)
    if not report:
        raise AcceptBlockedError("candidate has not been validated; run report first")
    summary = report.get("summary") or {}
    if not summary.get("acceptable", False) or summary.get("hard_error_count", 0) > 0:
        raise AcceptBlockedError(
            f"candidate has {summary.get('hard_error_count', 0)} hard error(s) or is invalid"
        )
    if summary.get("protected_change_count", 0) > 0 and not confirm_protected:
        raise AcceptBlockedError(
            "candidate changes teacher-approved / manually-edited items; explicit confirmation required"
        )

    candidate = run_artifacts.get(CANDIDATE_KEY) or {}
    candidate_artifacts = candidate.get("candidate_artifacts") or {}
    graph_payload = candidate_artifacts.get("component_graph")

    return persistence.accept_revision(
        document_id=str(run.get("document_id")),
        run_id=run_id,
        expected_base_run_id=run.get("base_run_id"),
        changed_by=changed_by,
        comment=comment,
        graph_payload=graph_payload,
    )


def reject_revision(*, run_id: str, changed_by: str | None = None, comment: str = "") -> dict:
    """Reject a candidate revision (active run + projections unchanged)."""
    return persistence.reject_revision(run_id=run_id, changed_by=changed_by, comment=comment)


def revise_revision_run(
    *,
    document_id: str,
    parent_run_id: str,
    created_by: str | None = None,
    comment: str = "",
) -> dict:
    """Create a child revision from a proposed candidate (#407 revise).

    The new revision keeps the adopted active run as its ``base_run_id`` and the
    current candidate as ``parent_revision_id``. A user comment is carried into
    the new plan as an extra manual checkpoint.
    """
    parent = persistence.get_analysis_run(run_id=parent_run_id)
    if not parent or parent.get("run_type") != "revision":
        raise ValueError(f"parent revision run {parent_run_id} not found")

    base = persistence.resolve_artifact_run(document_id=document_id)
    if not base or not base.get("id"):
        raise ValueError(f"no adoptable base run for document {document_id}")

    run_id = persistence.create_revision_run(
        document_id=document_id,
        base_run_id=str(base["id"]),
        material_id=base.get("material_id"),
        cartridge_id=base.get("cartridge_id"),
        created_by=created_by,
        parent_revision_id=parent_run_id,
        revision_status="preparing",
    )

    plan = build_revision_plan(base, document_id=document_id)
    if comment:
        plan[AUDIT_CHECKPOINTS_KEY] = [
            {
                "checkpoint_id": f"ckpt_user_{parent_run_id[:8]}",
                "target_type": "document", "target_id": document_id,
                "question": comment, "trigger_reason": "user_comment",
                "severity": "review_required", "expected_evidence_type": "source_text",
                "source_scope": [], "verification_strategy": "user_directed",
                "status": "planned",
            },
            *plan.get(AUDIT_CHECKPOINTS_KEY, []),
        ]

    persistence.update_revision_status(
        run_id=run_id, revision_status="preparing",
        stage_outputs={ARTIFACTS_KEY: plan},
    )
    return {
        "revision_run_id": run_id,
        "base_run_id": str(base["id"]),
        "parent_revision_id": parent_run_id,
        "document_id": document_id,
        **plan,
    }
