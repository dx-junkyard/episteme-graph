"""Revision-run coordinator (#402 + #403).

Glue between the run version-management helpers and the deterministic baseline
inventory / checkpoint planner. ``build_revision_plan`` is pure (operates on a
base-run dict); ``start_revision_run`` performs the DB side effects (create a
candidate run, persist inventory + checkpoints as that run's artifacts) without
ever touching projection tables or the adopted active run.
"""
from __future__ import annotations

from typing import Any

from .. import persistence
from .checkpoints import plan_checkpoints
from .inventory import ARTIFACTS_KEY, build_baseline_inventory, get_artifacts

# Revision artifact keys (stored under stage_outputs._artifacts of the run).
BASELINE_INVENTORY_KEY = "baseline_inventory"
AUDIT_CHECKPOINTS_KEY = "audit_checkpoints"


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
