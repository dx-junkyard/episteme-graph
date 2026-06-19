"""Before/after diff report for the iterative-improvement pipeline (#406).

Computes the structural diff, the quality-metric delta, and the semantic change
list between a base run and a candidate run, then assembles the report schema the
accept/reject UI consumes. ``recommendation`` is advisory only and never drives
automatic adoption.
"""
from __future__ import annotations

from typing import Any

from .inventory import build_baseline_inventory
from .validation import gate_error_count

LOW_CONFIDENCE = 0.5

# operation -> semantic change_type
_CHANGE_TYPE = {
    "add_entity": "added",
    "remove_entity": "removed",
    "update_entity": "updated",
    "split_entity": "split",
    "merge_entities": "merged",
    "add_relation": "relation_added",
    "remove_relation": "relation_removed",
    "relink_evidence": "evidence_relinked",
}


# ---------------------------------------------------------------------------
# Quality metrics
# ---------------------------------------------------------------------------

def compute_quality_metrics(artifacts: dict, *, gate_result: dict | None = None) -> dict:
    """Deterministic quality snapshot of one artifact set."""
    inv = build_baseline_inventory(artifacts or {}, base_run_id="")
    entities = inv["entities"]

    summary = (gate_result or {}).get("summary") or {}
    hard_errors = int(summary.get("error_count") or 0) if gate_result else 0
    warnings = int(summary.get("warning_count") or 0) if gate_result else 0
    review_required = int(summary.get("review_required_count") or 0) if gate_result else 0

    # source-backed rate over graph nodes + edges.
    backed = 0
    backable = 0
    for bucket in ("graph_nodes", "graph_edges"):
        for ent in entities.get(bucket, {}).values():
            backable += 1
            if str(ent.get("source_backing_status") or "").lower() == "source_backed":
                backed += 1
    source_backed_rate = round(backed / backable, 4) if backable else 1.0

    low_conf_equations = sum(
        1 for eq in entities.get("equations", {}).values()
        if float(eq.get("confidence") or 0.0) < LOW_CONFIDENCE
    )

    # main claim coverage: components carrying >= 1 linked claim.
    components = entities.get("components", {})
    covered = sum(1 for c in components.values() if c.get("claim_ids"))
    main_claim_coverage = round(covered / len(components), 4) if components else 1.0

    granularity_violations = sum(
        1 for c in components.values()
        if (c.get("split_recommendation") or {}).get("split_required")
        or (c.get("split_recommendation") or {}).get("should_split")
    )

    return {
        "hard_error_count": hard_errors,
        "warning_count": warnings,
        "review_required_count": review_required,
        "unresolved_reference_count": len(inv["unresolved_references"]),
        "source_backed_rate": source_backed_rate,
        "low_confidence_equation_count": low_conf_equations,
        "main_claim_coverage": main_claim_coverage,
        "component_granularity_violation_count": granularity_violations,
        "entity_counts": inv["entity_counts"],
    }


# ---------------------------------------------------------------------------
# Gate finding diff
# ---------------------------------------------------------------------------

def _finding_key(entry: dict) -> tuple:
    return (
        str(entry.get("code") or ""),
        str(entry.get("artifact") or ""),
        str(entry.get("path") or ""),
        str(entry.get("message") or ""),
    )


def _all_findings(gate_result: dict) -> dict[tuple, dict]:
    out: dict[tuple, dict] = {}
    for bucket in ("errors", "warnings", "review_items"):
        for entry in (gate_result or {}).get(bucket, []) or []:
            if isinstance(entry, dict):
                e = dict(entry)
                e["severity"] = bucket.rstrip("s") if bucket != "review_items" else "review_required"
                out[_finding_key(entry)] = e
    return out


def diff_findings(gate_base: dict, gate_candidate: dict) -> dict:
    base = _all_findings(gate_base)
    cand = _all_findings(gate_candidate)
    resolved = [base[k] for k in base.keys() - cand.keys()]
    introduced = [cand[k] for k in cand.keys() - base.keys()]
    unchanged = [cand[k] for k in base.keys() & cand.keys()]
    return {
        "resolved_issues": resolved,
        "introduced_issues": introduced,
        "unchanged_review_items": [u for u in unchanged if u.get("severity") == "review_required"],
    }


# ---------------------------------------------------------------------------
# Structural + semantic entity diff
# ---------------------------------------------------------------------------

def _entity_change_from_operation(op: dict) -> dict:
    return {
        "change_type": _CHANGE_TYPE.get(op.get("operation"), op.get("operation")),
        "operation": op.get("operation"),
        "operation_id": op.get("operation_id"),
        "entity_type": op.get("target_type"),
        "entity_id": op.get("target_id"),
        "before": op.get("before_json"),
        "after": op.get("after_json"),
        "reason": op.get("reason"),
        "checkpoint_ids": op.get("checkpoint_ids") or [],
        "evidence_refs": op.get("evidence_refs") or [],
        "source_chunk_ids": op.get("source_chunk_ids") or [],
        "source_locations": op.get("source_locations") or [],
        "confidence": op.get("confidence"),
        "protected": bool(op.get("protected_target")),
        "validation_result": op.get("validation_result") or {},
    }


def compute_structural_summary(
    base_artifacts: dict, candidate_artifacts: dict
) -> dict:
    base_inv = build_baseline_inventory(base_artifacts or {}, base_run_id="")
    cand_inv = build_baseline_inventory(candidate_artifacts or {}, base_run_id="")
    summary: dict[str, dict] = {}
    for bucket in base_inv["entity_counts"]:
        base_ids = set(base_inv["entities"].get(bucket, {}))
        cand_ids = set(cand_inv["entities"].get(bucket, {}))
        summary[bucket] = {
            "added": sorted(cand_ids - base_ids),
            "removed": sorted(base_ids - cand_ids),
            "common": len(base_ids & cand_ids),
        }
    return summary


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------

def _recommendation(quality_after: dict, candidate_invalid: bool,
                    introduced_errors: int, resolved_count: int,
                    requires_confirmation: bool) -> str:
    if candidate_invalid or quality_after.get("hard_error_count", 0) > 0:
        return "reject"
    if introduced_errors > 0 or requires_confirmation:
        return "manual_review"
    if resolved_count > 0:
        return "accept"
    return "manual_review"


def summarize_rejections(rejected: list[dict]) -> list[dict]:
    """Aggregate rejected-proposal reasons by their leading reason code (#412 P1-8)."""
    counts: dict[str, int] = {}
    for item in rejected or []:
        reason = str((item or {}).get("reason") or "unknown")
        code = reason.split(":", 1)[0]
        counts[code] = counts.get(code, 0) + 1
    return [
        {"reason": code, "count": counts[code]}
        for code in sorted(counts, key=lambda c: (-counts[c], c))
    ]


def build_stage_summary(
    *,
    audit_summary: dict | None,
    proposal_summary: dict | None,
    candidate_invalid: bool,
    new_unknown_reference_count: int,
    applied_change_count: int,
) -> dict:
    """Three-stage rollup (audit / candidate generation / candidate evaluation).

    Distinguishes "no improvement found" from "improvements proposed but could not
    be validated/adopted" so a 1-change diff over 59 targets is not misread as
    "nothing to improve" (#412 P1-8).
    """
    audit_summary = audit_summary or {}
    proposal_summary = proposal_summary or {}
    accepted = int(proposal_summary.get("accepted_count") or 0)
    rejected = int(proposal_summary.get("rejected_count") or 0)
    audit_targets = int(audit_summary.get("revision_target_count") or 0)
    if audit_targets == 0:
        outcome = "no_audit_targets"
    elif applied_change_count == 0 and accepted == 0:
        outcome = "proposals_failed"   # targets found, but no adoptable change
    elif candidate_invalid:
        outcome = "candidate_invalid"
    else:
        outcome = "changes_proposed"
    return {
        "audit": {
            "checkpoints_total": int(audit_summary.get("checkpoints_total") or 0),
            "revision_target_count": audit_targets,
            "manual_review_count": int(audit_summary.get("manual_review_count") or 0),
        },
        "candidate_generation": {
            "accepted_count": accepted,
            "rejected_count": rejected,
            "rejection_reasons": list(proposal_summary.get("rejection_reasons") or []),
        },
        "candidate_evaluation": {
            "applied_change_count": applied_change_count,
            "candidate_invalid": bool(candidate_invalid),
            "new_unknown_reference_count": int(new_unknown_reference_count),
        },
        "outcome": outcome,
    }


def build_diff_report(
    *,
    base_run_id: str,
    candidate_run_id: str,
    base_artifacts: dict,
    candidate_artifacts: dict,
    applied_operations: list[dict],
    gate_base: dict,
    gate_candidate: dict,
    candidate_invalid: bool = False,
    protected_changes: list[dict] | None = None,
    requires_confirmation: bool = False,
    id_mapping: dict | None = None,
    audit_verifications: list[dict] | None = None,
    audit_summary: dict | None = None,
    proposal_summary: dict | None = None,
    new_unknown_reference_count: int = 0,
) -> dict:
    """Assemble the before/after diff report (schema per #406, sections per #412)."""
    quality_before = compute_quality_metrics(base_artifacts, gate_result=gate_base)
    quality_after = compute_quality_metrics(candidate_artifacts, gate_result=gate_candidate)

    finding_diff = diff_findings(gate_base, gate_candidate)
    introduced_errors = sum(
        1 for f in finding_diff["introduced_issues"] if f.get("severity") == "error"
    )

    entity_changes = [
        _entity_change_from_operation(op)
        for op in (applied_operations or [])
        if (op.get("validation_result") or {}).get("status") == "applied"
    ]
    structural_summary = compute_structural_summary(base_artifacts, candidate_artifacts)
    protected_changes = protected_changes or []

    hard_error_count = quality_after.get("hard_error_count", 0)
    acceptable = (
        not candidate_invalid
        and hard_error_count == 0
    )

    recommendation = _recommendation(
        quality_after, candidate_invalid, introduced_errors,
        len(finding_diff["resolved_issues"]), requires_confirmation,
    )

    stage_summary = build_stage_summary(
        audit_summary=audit_summary,
        proposal_summary=proposal_summary,
        candidate_invalid=candidate_invalid,
        new_unknown_reference_count=new_unknown_reference_count,
        applied_change_count=len(entity_changes),
    )

    return {
        "base_run_id": base_run_id,
        "candidate_run_id": candidate_run_id,
        "summary": {
            "entity_change_count": len(entity_changes),
            "resolved_issue_count": len(finding_diff["resolved_issues"]),
            "introduced_issue_count": len(finding_diff["introduced_issues"]),
            "introduced_error_count": introduced_errors,
            "protected_change_count": len(protected_changes),
            "candidate_invalid": bool(candidate_invalid),
            "hard_error_count": hard_error_count,
            "acceptable": acceptable,
            "requires_confirmation": bool(requires_confirmation),
            "outcome": stage_summary["outcome"],
        },
        "stage_summary": stage_summary,
        "entity_changes": entity_changes,
        "structural_summary": structural_summary,
        "id_mapping": id_mapping or {},
        "quality_before": quality_before,
        "quality_after": quality_after,
        "resolved_issues": finding_diff["resolved_issues"],
        "introduced_issues": finding_diff["introduced_issues"],
        "unchanged_review_items": finding_diff["unchanged_review_items"],
        "protected_changes": protected_changes,
        "audit_verifications": audit_verifications or [],
        "recommendation": recommendation,
    }
