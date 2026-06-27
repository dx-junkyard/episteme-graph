"""Deterministic checkpoint planner for the iterative-improvement pipeline (#403).

Turns a baseline inventory (see ``inventory.py``) into a list of audit
checkpoints — the "what do we re-read the source for, and why" plan that the
source re-audit stage (#404) will follow.

Every checkpoint emitted here is *deterministic*: it is derived from a concrete
signal already present in the base artifacts (validation findings, unresolved
references, low confidence, missing source backing, non-atomic claims,
granularity findings, review-required status). LLM-suggested checkpoints can be
appended later, but the deterministic core must be reproducible for the same
base artifacts (AC #403).
"""
from __future__ import annotations

import hashlib
from typing import Any

from episteme_graph.agents.component_assembly.split_recommendation import (
    split_is_required as _split_is_required,
)

# Confidence below which an entity is considered low-confidence and worth a
# source re-check.
LOW_CONFIDENCE_THRESHOLD = 0.5

# review_status values that demand a source re-check.
_REVIEW_REQUIRED_STATUSES = {
    "review_required",
    "teacher_review_required",
    "needs_review",
}

_SOURCE_BACKED_OK = {"source_backed"}

CHECKPOINT_STATUS_PLANNED = "planned"


def _checkpoint_id(target_type: str, target_id: str, trigger_reason: str) -> str:
    digest = hashlib.sha1(
        f"{target_type}|{target_id}|{trigger_reason}".encode("utf-8")
    ).hexdigest()
    return f"ckpt_{digest[:12]}"


def _make_checkpoint(
    *,
    target_type: str,
    target_id: str,
    question: str,
    trigger_reason: str,
    severity: str,
    expected_evidence_type: str,
    source_scope: Any,
    verification_strategy: str,
) -> dict:
    return {
        "checkpoint_id": _checkpoint_id(target_type, target_id, trigger_reason),
        "target_type": target_type,
        "target_id": target_id,
        "question": question,
        "trigger_reason": trigger_reason,
        "severity": severity,
        "expected_evidence_type": expected_evidence_type,
        "source_scope": source_scope or [],
        "verification_strategy": verification_strategy,
        "status": CHECKPOINT_STATUS_PLANNED,
    }


def _entity(inventory: dict, bucket: str, ent_id: str) -> dict:
    return (inventory.get("entities", {}).get(bucket, {}) or {}).get(ent_id, {})


def _scope_of(entity: dict) -> list:
    return entity.get("source_locations") or []


def _requires_review(status: Any) -> bool:
    return str(status or "").strip().lower() in _REVIEW_REQUIRED_STATUSES


# ---------------------------------------------------------------------------
# Deterministic checkpoint sources
# ---------------------------------------------------------------------------

def _from_existing_issues(inventory: dict) -> list[dict]:
    out: list[dict] = []
    for issue in inventory.get("existing_issues", []):
        target_id = issue.get("target_id") or issue.get("artifact") or "document"
        # Issue #418: prefer the explicit target_type recorded on the issue; fall
        # back to artifact/id heuristics only when it is absent (legacy issues).
        target_type = issue.get("target_type") or _infer_target_type(
            issue.get("artifact"), target_id
        )
        code = issue.get("code") or "issue"
        out.append(_make_checkpoint(
            target_type=target_type,
            target_id=target_id,
            question=(
                f"既存の検証指摘「{issue.get('message') or code}」が原文根拠で解消可能か確認する"
            ),
            trigger_reason=f"validation:{code}",
            severity=issue.get("severity") or "warning",
            expected_evidence_type="source_text",
            source_scope=[],
            verification_strategy="re_read_reported_location",
        ))
    return out


def _from_unresolved_references(inventory: dict) -> list[dict]:
    out: list[dict] = []
    for ref in inventory.get("unresolved_references", []):
        out.append(_make_checkpoint(
            target_type=ref.get("source_type") or "unknown",
            target_id=ref.get("source_id") or "",
            question=(
                f"{ref.get('ref_field')} が指す {ref.get('target_type')} "
                f"'{ref.get('target_id')}' が存在しない。参照を再解決できるか確認する"
            ),
            trigger_reason="unresolved_reference",
            severity="error",
            expected_evidence_type="cross_reference",
            source_scope=_scope_of(_entity_for_ref(inventory, ref)),
            verification_strategy="resolve_reference",
        ))
    return out


def _entity_for_ref(inventory: dict, ref: dict) -> dict:
    bucket_map = {
        "claim": "claims", "equation": "equations", "evidence": "evidence",
        "derivation": "derivations", "component": "components",
        "graph_node": "graph_nodes", "graph_edge": "graph_edges", "thesis": "thesis",
    }
    bucket = bucket_map.get(ref.get("source_type"), "")
    return _entity(inventory, bucket, ref.get("source_id")) if bucket else {}


def _from_claims(inventory: dict) -> list[dict]:
    out: list[dict] = []
    for cid, claim in (inventory.get("entities", {}).get("claims", {}) or {}).items():
        scope = _scope_of(claim)
        if not claim.get("is_atomic", True):
            out.append(_make_checkpoint(
                target_type="claim", target_id=cid,
                question="この claim は複数命題を含む。原文の限定条件・否定・不確実性を保持して atomic に再構成できるか確認する",
                trigger_reason="non_atomic_claim",
                severity="warning", expected_evidence_type="source_text",
                source_scope=scope, verification_strategy="re_read_claim_span",
            ))
        if float(claim.get("confidence") or 0.0) < LOW_CONFIDENCE_THRESHOLD:
            out.append(_make_checkpoint(
                target_type="claim", target_id=cid,
                question="低 confidence の claim が原文と整合し、限定条件を保持しているか確認する",
                trigger_reason="low_confidence",
                severity="warning", expected_evidence_type="source_text",
                source_scope=scope, verification_strategy="re_read_claim_span",
            ))
        if _requires_review(claim.get("review_status")) and not claim.get("evidence_ids"):
            out.append(_make_checkpoint(
                target_type="claim", target_id=cid,
                question="review_required の claim に evidence link が無い。原文から根拠を特定できるか確認する",
                trigger_reason="review_required_missing_evidence",
                severity="warning", expected_evidence_type="evidence_quote",
                source_scope=scope, verification_strategy="find_evidence",
            ))
    return out


def _from_equations(inventory: dict) -> list[dict]:
    out: list[dict] = []
    for eid, eq in (inventory.get("entities", {}).get("equations", {}) or {}).items():
        needs = (
            _requires_review(eq.get("review_status"))
            or float(eq.get("confidence") or 0.0) < LOW_CONFIDENCE_THRESHOLD
            or not (eq.get("latex") or "").strip()
        )
        if needs:
            out.append(_make_checkpoint(
                target_type="equation", target_id=eid,
                question="数式の記号・添字・符号・近似条件が原文と一致するか、raw text / latex / label を突き合わせて確認する",
                trigger_reason="equation_needs_math_review",
                severity="warning", expected_evidence_type="equation_source",
                source_scope=eq.get("source_locations") or [],
                verification_strategy="compare_equation_raw_latex",
            ))
    return out


def _from_derivations(inventory: dict) -> list[dict]:
    out: list[dict] = []
    for did, der in (inventory.get("entities", {}).get("derivations", {}) or {}).items():
        if _requires_review(der.get("review_status")) or der.get("step_count", 0) == 0:
            out.append(_make_checkpoint(
                target_type="derivation", target_id=did,
                question="導出チェーンの仮定・途中式に欠落が無いか、原文の式間関係を再確認する",
                trigger_reason="derivation_review",
                severity="warning", expected_evidence_type="equation_source",
                source_scope=_scope_of(der),
                verification_strategy="re_read_derivation_steps",
            ))
    return out


def _from_components(inventory: dict) -> list[dict]:
    out: list[dict] = []
    for cid, comp in (inventory.get("entities", {}).get("components", {}) or {}).items():
        split = comp.get("split_recommendation") or {}
        # Canonical key is ``required`` (#417). Read through the shared helper so
        # legacy ``split_required`` / ``should_split`` shapes are still honored
        # and a ``required: true`` component is never silently skipped.
        if _split_is_required(split):
            out.append(_make_checkpoint(
                target_type="component", target_id=cid,
                question="component が単一責務・適切な粒度か（分割推奨あり）。input/output が claim/equation と整合するか確認する",
                trigger_reason="component_granularity",
                severity="warning", expected_evidence_type="source_text",
                source_scope=_scope_of(comp),
                verification_strategy="re_read_component_scope",
            ))
        if _requires_review(comp.get("review_status")) and not comp.get("claim_ids"):
            out.append(_make_checkpoint(
                target_type="component", target_id=cid,
                question="review_required の component に支持 claim が無い。main claim を支える根拠が原文にあるか確認する",
                trigger_reason="component_missing_claim_backing",
                severity="warning", expected_evidence_type="source_text",
                source_scope=_scope_of(comp),
                verification_strategy="find_supporting_claim",
            ))
    return out


def _from_graph(inventory: dict) -> list[dict]:
    out: list[dict] = []
    for eid, edge in (inventory.get("entities", {}).get("graph_edges", {}) or {}).items():
        backing = str(edge.get("source_backing_status") or "").strip().lower()
        if backing and backing not in _SOURCE_BACKED_OK:
            out.append(_make_checkpoint(
                target_type="graph_edge", target_id=eid,
                question=f"graph edge ({edge.get('source')}→{edge.get('target')}) に十分な source backing があるか確認する",
                trigger_reason="edge_not_source_backed",
                severity="warning", expected_evidence_type="equation_or_claim",
                source_scope=[],
                verification_strategy="verify_edge_backing",
            ))
    return out


def _infer_target_type(artifact: Any, target_id: str) -> str:
    artifact = str(artifact or "")
    if "claim" in artifact:
        return "claim"
    if "equation" in artifact:
        return "equation"
    if "evidence" in artifact:
        return "evidence"
    if "derivation" in artifact:
        return "derivation"
    if "component_graph" in artifact:
        return "graph_edge"
    if "component" in artifact:
        return "component"
    # Fall back to a prefix heuristic on the id.
    tid = str(target_id or "")
    for prefix, ttype in (("clm", "claim"), ("eq", "equation"), ("ev", "evidence"),
                          ("der", "derivation"), ("cmp", "component"), ("comp", "component")):
        if tid.startswith(prefix):
            return ttype
    return "document"


_SEVERITY_ORDER = {"error": 0, "warning": 1, "review_required": 2, "info": 3}


def plan_checkpoints(inventory: dict) -> list[dict]:
    """Generate deterministic audit checkpoints from a baseline inventory."""
    checkpoints: list[dict] = []
    checkpoints += _from_existing_issues(inventory)
    checkpoints += _from_unresolved_references(inventory)
    checkpoints += _from_claims(inventory)
    checkpoints += _from_equations(inventory)
    checkpoints += _from_derivations(inventory)
    checkpoints += _from_components(inventory)
    checkpoints += _from_graph(inventory)

    # De-duplicate by checkpoint_id (same target+trigger collapses), keeping the
    # first (most severe source) occurrence. Then sort deterministically by
    # (severity, target_type, target_id, trigger_reason) so output is stable.
    seen: set[str] = set()
    deduped: list[dict] = []
    for cp in checkpoints:
        if cp["checkpoint_id"] in seen:
            continue
        seen.add(cp["checkpoint_id"])
        deduped.append(cp)

    deduped.sort(key=lambda c: (
        _SEVERITY_ORDER.get(c.get("severity"), 9),
        c.get("target_type", ""),
        c.get("target_id", ""),
        c.get("trigger_reason", ""),
    ))
    return deduped
