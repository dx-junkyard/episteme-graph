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
    PROPOSED_OPERATIONS_KEY,
    REVISION_OPERATIONS_KEY,
    AcceptBlockedError,
    ProposalValidationError,
    accept_revision,
    assemble_candidate,
    audit_revision_run,
    build_revision_plan,
    generate_proposals,
    reject_revision,
    revalidate_and_report,
    revise_revision_run,
    run_revision_pipeline,
    start_revision_run,
)
from .llm_audit import LLMAuditClient, build_default_audit_client, llm_enabled
from .proposal import (
    propose_operations,
    validate_proposed_operation,
    validate_proposed_operations,
)
from .diff import build_diff_report, compute_quality_metrics
from .inventory import (
    ARTIFACTS_KEY,
    build_baseline_inventory,
    get_artifacts,
    overlay_projection_state,
)
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
    "PROPOSED_OPERATIONS_KEY",
    "LLMAuditClient",
    "AcceptBlockedError",
    "ProposalValidationError",
    "accept_revision",
    "build_default_audit_client",
    "generate_proposals",
    "llm_enabled",
    "propose_operations",
    "validate_proposed_operation",
    "validate_proposed_operations",
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
    "overlay_projection_state",
    "make_operation",
    "plan_checkpoints",
    "reject_revision",
    "retrieve_source_for_checkpoint",
    "revalidate_and_report",
    "revise_revision_run",
    "run_export_validation",
    "run_revision_pipeline",
    "run_source_audit",
    "start_revision_run",
]
