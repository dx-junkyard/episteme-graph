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
    CANDIDATE_KEY,
    CANDIDATE_VALIDATION_KEY,
    DIFF_REPORT_KEY,
    REVISION_OPERATIONS_KEY,
    assemble_candidate,
    audit_revision_run,
    build_revision_plan,
    revalidate_and_report,
    start_revision_run,
)
from .diff import build_diff_report, compute_quality_metrics
from .inventory import ARTIFACTS_KEY, build_baseline_inventory, get_artifacts
from .operations import OPERATIONS, apply_operations, make_operation
from .validation import candidate_has_hard_errors, run_export_validation

__all__ = [
    "ARTIFACTS_KEY",
    "AUDIT_CHECKPOINTS_KEY",
    "AUDIT_RESULTS_KEY",
    "BASELINE_INVENTORY_KEY",
    "CANDIDATE_KEY",
    "CANDIDATE_VALIDATION_KEY",
    "DIFF_REPORT_KEY",
    "LOW_CONFIDENCE_THRESHOLD",
    "OPERATIONS",
    "REVISION_OPERATIONS_KEY",
    "VERDICTS",
    "apply_operations",
    "assemble_candidate",
    "audit_checkpoint",
    "audit_revision_run",
    "build_baseline_inventory",
    "build_diff_report",
    "build_revision_plan",
    "candidate_has_hard_errors",
    "compute_quality_metrics",
    "get_artifacts",
    "make_operation",
    "plan_checkpoints",
    "retrieve_source_for_checkpoint",
    "revalidate_and_report",
    "run_export_validation",
    "run_source_audit",
    "start_revision_run",
]
