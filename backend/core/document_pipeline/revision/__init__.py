"""Iterative-improvement (revision) pipeline (#401).

Audit the adopted run's artifacts, re-read the source per checkpoint, propose
explicit revision operations, and assemble a candidate set of artifacts — all
without mutating the adopted active run or projection tables until accepted.
"""
from __future__ import annotations

from .audit import (
    VERDICTS,
    audit_checkpoint,
    retrieve_source_for_checkpoint,
    run_source_audit,
)
from .checkpoints import LOW_CONFIDENCE_THRESHOLD, plan_checkpoints
from .coordinator import (
    AUDIT_CHECKPOINTS_KEY,
    AUDIT_RESULTS_KEY,
    BASELINE_INVENTORY_KEY,
    audit_revision_run,
    build_revision_plan,
    start_revision_run,
)
from .inventory import ARTIFACTS_KEY, build_baseline_inventory, get_artifacts

__all__ = [
    "ARTIFACTS_KEY",
    "AUDIT_CHECKPOINTS_KEY",
    "AUDIT_RESULTS_KEY",
    "BASELINE_INVENTORY_KEY",
    "LOW_CONFIDENCE_THRESHOLD",
    "VERDICTS",
    "audit_checkpoint",
    "audit_revision_run",
    "build_baseline_inventory",
    "build_revision_plan",
    "get_artifacts",
    "plan_checkpoints",
    "retrieve_source_for_checkpoint",
    "run_source_audit",
    "start_revision_run",
]
