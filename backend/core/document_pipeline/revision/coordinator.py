"""Revision-run coordinator (#402 + #403).

Glue between the run version-management helpers and the deterministic baseline
inventory / checkpoint planner. ``build_revision_plan`` is pure (operates on a
base-run dict); ``start_revision_run`` performs the DB side effects (create a
candidate run, persist inventory + checkpoints as that run's artifacts) without
ever touching projection tables or the adopted active run.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from .. import persistence
from .audit import run_source_audit
from .checkpoints import plan_checkpoints
from .diff import build_diff_report
from .inventory import (
    ARTIFACTS_KEY,
    build_baseline_inventory,
    get_artifacts,
    overlay_projection_state,
)
from .operations import apply_operations
from .validation import run_export_validation

logger = logging.getLogger(__name__)

# Revision pipeline stages — values stored in document_analysis_runs.current_stage
# so the generic run state reflects progress without inspecting artifacts (#412 P1-3).
STAGE_AUDIT = "audit"
STAGE_PROPOSAL = "proposal"
STAGE_CANDIDATE_ASSEMBLY = "candidate_assembly"
STAGE_VALIDATION = "validation"
STAGE_REPORT = "report"
STAGE_COMPLETED = "completed"

# Canonical revision stage order (#414-3).
REVISION_STAGES = (
    STAGE_AUDIT, STAGE_PROPOSAL, STAGE_CANDIDATE_ASSEMBLY,
    STAGE_VALIDATION, STAGE_REPORT, STAGE_COMPLETED,
)

# Revision artifact keys (stored under stage_outputs._artifacts of the run).
BASELINE_INVENTORY_KEY = "baseline_inventory"
AUDIT_CHECKPOINTS_KEY = "audit_checkpoints"
AUDIT_RESULTS_KEY = "audit_results"
REVISION_OPERATIONS_KEY = "revision_operations"
PROPOSED_OPERATIONS_KEY = "proposed_operations"
CANDIDATE_KEY = "candidate"
CANDIDATE_VALIDATION_KEY = "candidate_validation"
DIFF_REPORT_KEY = "diff_report"


def build_revision_plan(
    base_run: dict,
    *,
    document_id: str = "",
    projection_overlay: dict | None = None,
) -> dict:
    """Build inventory + checkpoints from a base run dict (no side effects)."""
    base_run = base_run or {}
    base_run_id = str(base_run.get("id") or "")
    document_id = document_id or str(base_run.get("document_id") or "")
    artifacts = get_artifacts(base_run.get("stage_outputs"))
    inventory = build_baseline_inventory(
        artifacts, base_run_id=base_run_id, document_id=document_id
    )
    inventory = overlay_projection_state(inventory, projection_overlay)
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
    projection_overlay = persistence.load_revision_projection_overlay(
        document_id=document_id
    )

    run_id = persistence.create_revision_run(
        document_id=document_id,
        base_run_id=base_run_id,
        material_id=material_id or base.get("material_id"),
        cartridge_id=cartridge_id or base.get("cartridge_id"),
        created_by=created_by,
        revision_status="preparing",
    )

    plan = build_revision_plan(
        base,
        document_id=document_id,
        projection_overlay=projection_overlay,
    )
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


def _load_chunk_index(document_id: str) -> list[dict]:
    """Trusted source-chunk index from the chunks table (empty on any failure)."""
    try:
        return persistence.load_source_chunk_index(document_id=document_id) or []
    except Exception:
        return []


def _trusted_chunk_ids(chunk_index: list[dict] | None) -> set[str]:
    return {str(c.get("chunk_id")) for c in (chunk_index or []) if c.get("chunk_id")}


def audit_revision_run(
    *,
    run_id: str,
    llm_client: Callable[[dict, dict], Any] | None = None,
    chunk_index: list[dict] | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
) -> dict:
    """Run the source re-audit stage for a candidate run.

    Reads the run's planned checkpoints, re-reads the source per checkpoint, and
    stores audit results as a run artifact. This stage evaluates only — it never
    builds candidate artifacts nor touches the base/active run (AC #404).
    ``progress_callback(completed, total)`` reports per-checkpoint progress (#414-3).
    """
    run = persistence.get_analysis_run(run_id=run_id)
    if not run:
        raise ValueError(f"revision run {run_id} not found")
    if run.get("run_type") != "revision":
        raise ValueError(f"run {run_id} is not a revision run")

    artifacts = get_artifacts(run.get("stage_outputs"))
    checkpoints = artifacts.get(AUDIT_CHECKPOINTS_KEY) or []
    document_id = str(run.get("document_id") or "")

    # Production path: attach a real LLM audit client (#410 P1-6) built from the
    # baseline inventory, when a real LLM key is configured. Tests / no-key
    # environments fall back to the deterministic path (llm_client stays None).
    if llm_client is None:
        from .llm_audit import build_default_audit_client
        inventory = artifacts.get(BASELINE_INVENTORY_KEY) or {}
        llm_client = build_default_audit_client(inventory)

    if chunk_index is None:
        chunk_index = _load_chunk_index(document_id)

    audit = run_source_audit(
        checkpoints=checkpoints,
        chunk_index=chunk_index or [],
        llm_client=llm_client,
        progress_callback=progress_callback,
    )
    persistence.update_revision_status(
        run_id=run_id,
        revision_status="auditing",
        stage_outputs={ARTIFACTS_KEY: {AUDIT_RESULTS_KEY: audit}},
    )
    return {"revision_run_id": run_id, "document_id": document_id, **audit}


def generate_proposals(
    *,
    run_id: str,
    generate: Callable[[list[dict]], str] | None = None,
    chunk_index: list[dict] | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
) -> dict:
    """Generate validated revision-operation proposals from the audit results (#410 P1-6).

    Reads the run's audit_results + baseline_inventory, asks the LLM (when
    configured) for one operation per ``requires_revision`` finding, validates each
    proposal server-side (schema / target / evidence / traceability), and stores
    the result as the ``proposed_operations`` artifact. Never auto-accepts.
    ``source_chunk_ids`` are validated against the trusted chunks index (#414-5);
    ``progress_callback(completed, total)`` reports per-target progress (#414-3).
    """
    run = persistence.get_analysis_run(run_id=run_id)
    if not run or run.get("run_type") != "revision":
        raise ValueError(f"revision run {run_id} not found")
    artifacts = get_artifacts(run.get("stage_outputs"))
    inventory = artifacts.get(BASELINE_INVENTORY_KEY) or {}
    audit_results = (artifacts.get(AUDIT_RESULTS_KEY) or {}).get("audit_results") or []
    if chunk_index is None:
        chunk_index = _load_chunk_index(str(run.get("document_id") or ""))

    from .proposal import propose_operations
    if generate is None:
        from .llm_audit import _default_generate, llm_enabled
        generate = _default_generate if llm_enabled() else None

    result = propose_operations(
        audit_results, inventory, generate=generate,
        known_chunk_ids=_trusted_chunk_ids(chunk_index),
        progress_callback=progress_callback,
    )
    persistence.update_revision_status(
        run_id=run_id, revision_status="auditing",
        stage_outputs={ARTIFACTS_KEY: {PROPOSED_OPERATIONS_KEY: result}},
    )
    return {"revision_run_id": run_id, **result}


class ProposalValidationError(ValueError):
    """Raised before candidate creation when any operation is invalid."""

    def __init__(self, rejected: list[dict]):
        self.rejected = rejected
        super().__init__(f"{len(rejected)} revision operation(s) failed validation")


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
    audit_results = (
        (run_artifacts.get(AUDIT_RESULTS_KEY) or {}).get("audit_results") or []
    )

    # Generated, raw/debug, and user-edited operations all pass through the same
    # fail-closed validator immediately before candidate assembly. The trusted
    # chunks index is supplied so source_chunk_ids are validated against real DB
    # chunks, not the LLM's own output (#414-5).
    from .proposal import validate_proposed_operations
    validated = validate_proposed_operations(
        operations or [],
        base_inventory,
        audit_results=audit_results,
        checkpoints=run_artifacts.get(AUDIT_CHECKPOINTS_KEY) or [],
        known_chunk_ids=_trusted_chunk_ids(_load_chunk_index(str(run.get("document_id") or ""))),
    )
    if validated["rejected"]:
        raise ProposalValidationError(validated["rejected"])

    result = apply_operations(
        base_artifacts, validated["operations"], base_inventory=base_inventory
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
    checkpoints = run_artifacts.get(AUDIT_CHECKPOINTS_KEY) or []
    proposed = run_artifacts.get(PROPOSED_OPERATIONS_KEY) or {}

    gate_base = run_export_validation(base_artifacts)
    gate_candidate = run_export_validation(candidate_artifacts)

    # Stage rollups so the report distinguishes "nothing to improve" from
    # "improvements proposed but not adoptable" (#412 P1-8).
    from .diff import summarize_rejections
    revision_targets = [a for a in audit_results if a.get("requires_revision")]
    manual_review = proposed.get("manual_review") or [
        a for a in audit_results
        if a.get("review_required") and not a.get("requires_revision")
    ]
    rejected_proposals = proposed.get("rejected") or []
    audit_summary = {
        "checkpoints_total": len(checkpoints) or len(audit_results),
        "revision_target_count": len(revision_targets),
        "manual_review_count": len(manual_review),
    }
    proposal_summary = {
        "accepted_count": len(proposed.get("operations") or applied_operations),
        "rejected_count": len(rejected_proposals),
        "rejection_reasons": summarize_rejections(rejected_proposals),
    }
    new_unknown_reference_count = sum(
        len(err.get("details") or [])
        for err in (candidate.get("operation_errors") or [])
        if isinstance(err, dict) and err.get("error") == "new_unknown_references"
    )

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
            for a in revision_targets
        ],
        audit_summary=audit_summary,
        proposal_summary=proposal_summary,
        new_unknown_reference_count=new_unknown_reference_count,
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

    return persistence.accept_revision(
        document_id=str(run.get("document_id")),
        run_id=run_id,
        expected_base_run_id=run.get("base_run_id"),
        changed_by=changed_by,
        comment=comment,
        candidate_artifacts=candidate_artifacts,
    )


def _safe_error(code: str, exc: Exception) -> str:
    """Bounded, structural error summary for persistence/logs.

    Never includes provider response bodies, full source text, or secrets (#412
    P1-5); only the error category and a short, truncated message.
    """
    detail = str(exc).replace("\n", " ").strip()
    if len(detail) > 200:
        detail = detail[:200] + "…"
    return f"{code}: {detail}" if detail else code


def run_revision_pipeline(
    *,
    run_id: str,
    operations: list[dict] | None = None,
    llm_client: Callable[[dict, dict], Any] | None = None,
    generate: Callable[[list[dict]], str] | None = None,
    progress_callback: Callable[[str, int, int], None] | None = None,
    task_id: str | None = None,
) -> dict:
    """Run audit → proposal → candidate_assembly → validation → report end-to-end.

    Drives the revision run's generic state (``status`` / ``current_stage`` /
    ``completed_at``) and emits structured stage logs so progress/outcome can be
    judged from run state + logs alone, without inspecting artifacts (#412 P1-3 /
    P1-5). ``progress_callback(stage, completed, total)`` reports intra-stage
    counts (#414-3); ``task_id`` is included in logs for correlation. Designed to
    run inside a background worker (#412 P0-2) — it never touches the active run
    or projection tables. ``operations`` supplied means the raw/debug override
    path (proposal generation is skipped).
    """
    run = persistence.get_analysis_run(run_id=run_id)
    if not run or run.get("run_type") != "revision":
        raise ValueError(f"revision run {run_id} not found")
    document_id = str(run.get("document_id") or "")
    log_ctx = "run_id=%s document_id=%s task_id=%s" % (run_id, document_id, task_id or "-")

    def _progress(stage: str, completed: int, total: int) -> None:
        if progress_callback:
            progress_callback(stage, completed, total)

    current_stage = STAGE_AUDIT
    try:
        # --- audit ---
        artifacts = get_artifacts(run.get("stage_outputs"))
        checkpoints_total = len(artifacts.get(AUDIT_CHECKPOINTS_KEY) or [])
        persistence.update_revision_status(
            run_id=run_id, revision_status="auditing",
            status="running", current_stage=STAGE_AUDIT,
        )
        logger.info("revision_started %s checkpoints=%d", log_ctx, checkpoints_total)
        _progress(STAGE_AUDIT, 0, checkpoints_total)
        audit = audit_revision_run(
            run_id=run_id, llm_client=llm_client,
            progress_callback=lambda done, total: _progress(STAGE_AUDIT, done, total),
        )
        revision_targets = audit.get("revision_targets") or []
        review_items = audit.get("review_items") or []
        logger.info(
            "revision_audit_completed %s checkpoints=%d completed=%d targets=%d review_items=%d",
            log_ctx, checkpoints_total, checkpoints_total,
            len(revision_targets), len(review_items),
        )

        # --- proposal (skipped for the raw/debug override path) ---
        if operations is None:
            current_stage = STAGE_PROPOSAL
            persistence.update_revision_status(
                run_id=run_id, revision_status="auditing", current_stage=STAGE_PROPOSAL,
            )
            _progress(STAGE_PROPOSAL, 0, len(revision_targets))
            proposals = generate_proposals(
                run_id=run_id, generate=generate,
                progress_callback=lambda done, total: _progress(STAGE_PROPOSAL, done, total),
            )
            ops = proposals.get("operations") or []
            rejected = proposals.get("rejected") or []
            manual_review = proposals.get("manual_review") or []
            logger.info(
                "revision_proposal_completed %s accepted=%d rejected=%d manual_review=%d",
                log_ctx, len(ops), len(rejected), len(manual_review),
            )
            operation_source = "generated"
        else:
            ops = operations
            operation_source = "raw"

        # --- candidate assembly ---
        current_stage = STAGE_CANDIDATE_ASSEMBLY
        persistence.update_revision_status(
            run_id=run_id, revision_status="auditing", current_stage=STAGE_CANDIDATE_ASSEMBLY,
        )
        _progress(STAGE_CANDIDATE_ASSEMBLY, 0, 1)
        candidate = assemble_candidate(run_id=run_id, operations=ops)
        new_unknown = sum(
            len(err.get("details") or [])
            for err in (candidate.get("operation_errors") or [])
            if isinstance(err, dict) and err.get("error") == "new_unknown_references"
        )
        logger.info(
            "revision_candidate_validated %s invalid=%s applied=%d new_unknown_references=%d",
            log_ctx, bool(candidate.get("invalid")),
            len(candidate.get("applied_operations") or []), new_unknown,
        )

        # --- validation (explicit stage) ---
        current_stage = STAGE_VALIDATION
        persistence.update_revision_status(
            run_id=run_id, revision_status="proposed", current_stage=STAGE_VALIDATION,
        )
        _progress(STAGE_VALIDATION, 0, 1)

        # --- report ---
        current_stage = STAGE_REPORT
        persistence.update_revision_status(
            run_id=run_id, revision_status="proposed", current_stage=STAGE_REPORT,
        )
        _progress(STAGE_REPORT, 0, 1)
        report_out = revalidate_and_report(run_id=run_id)
        report = report_out["report"]
        summary = report.get("summary") or {}

        persistence.update_revision_status(
            run_id=run_id, revision_status="proposed",
            status="completed", current_stage=STAGE_COMPLETED,
        )
        _progress(STAGE_COMPLETED, 1, 1)
        logger.info(
            "revision_completed %s recommendation=%s outcome=%s changes=%d candidate_invalid=%s",
            log_ctx, report.get("recommendation"), summary.get("outcome"),
            summary.get("entity_change_count"), summary.get("candidate_invalid"),
        )
        return {
            "revision_run_id": run_id,
            "operation_source": operation_source,
            "audit": {
                "verdict_counts": audit.get("verdict_counts"),
                "revision_targets": audit.get("revision_targets"),
            },
            "candidate_invalid": bool(candidate.get("invalid")),
            "report_summary": summary,
            "stage_summary": report.get("stage_summary"),
            "revision_status": "proposed",
        }
    except ProposalValidationError as exc:
        # Not a worker crash: the audit found targets but no adoptable candidate
        # could be assembled. Persist a failed state with the offending stage.
        persistence.update_revision_status(
            run_id=run_id, revision_status="proposed",
            status="failed", current_stage=STAGE_CANDIDATE_ASSEMBLY,
            error_message=_safe_error("proposal_validation_failed", exc),
        )
        logger.warning(
            "revision_failed %s stage=%s reason=proposal_validation rejected=%d",
            log_ctx, STAGE_CANDIDATE_ASSEMBLY, len(exc.rejected),
        )
        raise
    except Exception as exc:
        persistence.update_revision_status(
            run_id=run_id, revision_status="failed",
            status="failed", current_stage=current_stage,
            error_message=_safe_error(type(exc).__name__, exc),
        )
        logger.exception("revision_failed %s stage=%s", log_ctx, current_stage)
        raise


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
    projection_overlay = persistence.load_revision_projection_overlay(
        document_id=document_id
    )

    run_id = persistence.create_revision_run(
        document_id=document_id,
        base_run_id=str(base["id"]),
        material_id=base.get("material_id"),
        cartridge_id=base.get("cartridge_id"),
        created_by=created_by,
        parent_revision_id=parent_run_id,
        revision_status="preparing",
    )

    plan = build_revision_plan(
        base,
        document_id=document_id,
        projection_overlay=projection_overlay,
    )
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
