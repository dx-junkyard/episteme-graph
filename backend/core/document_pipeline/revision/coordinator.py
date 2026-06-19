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
from .inventory import ARTIFACTS_KEY, build_baseline_inventory, get_artifacts
from .operations import apply_operations

# Revision artifact keys (stored under stage_outputs._artifacts of the run).
BASELINE_INVENTORY_KEY = "baseline_inventory"
AUDIT_CHECKPOINTS_KEY = "audit_checkpoints"
AUDIT_RESULTS_KEY = "audit_results"
REVISION_OPERATIONS_KEY = "revision_operations"
CANDIDATE_KEY = "candidate"


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
