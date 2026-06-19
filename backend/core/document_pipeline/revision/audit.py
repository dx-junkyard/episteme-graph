"""Checkpoint-driven source re-audit (#404).

For each planned checkpoint, retrieve the relevant slice of the original source
(by section / block / page, plus neighbouring context) and judge whether the
existing artifact is valid / incorrect / incomplete / unsupported / ambiguous.

Hard rules (AC #404):
- This stage NEVER mutates candidate artifacts; it only evaluates and records
  source evidence.
- Source-derived evidence (``source_evidence``) is kept separate from any LLM
  commentary (``analysis_notes``).
- An artifact is never marked ``valid`` without positive source evidence.
- Low-confidence / ambiguous judgments are never auto-confirmed as revision
  targets (``requires_revision`` stays False, ``review_required`` True).
"""
from __future__ import annotations

from typing import Any, Callable

VERDICTS = ("valid", "incorrect", "incomplete", "unsupported", "ambiguous")

# Checkpoint triggers that point at a concrete, source-checkable defect → the
# deterministic audit can mark them as requiring revision.
_DEFECT_TRIGGERS = {
    "unresolved_reference": "incomplete",
    "non_atomic_claim": "incomplete",
    "review_required_missing_evidence": "unsupported",
    "component_missing_claim_backing": "unsupported",
}
# Triggers that are inherently uncertain → ambiguous, human review, never
# auto-confirmed as a revision target by the deterministic path.
_AMBIGUOUS_TRIGGERS = {
    "low_confidence",
    "equation_needs_math_review",
    "edge_not_source_backed",
    "component_granularity",
    "derivation_review",
}

_LOW_CONFIDENCE = 0.5


# ---------------------------------------------------------------------------
# Source retrieval
# ---------------------------------------------------------------------------

def _chunk_matches_location(chunk: dict, loc: dict) -> str | None:
    """Return the match reason if ``chunk`` covers ``loc``, else None."""
    section_id = loc.get("section_id")
    block_id = loc.get("block_id")
    page = loc.get("page")
    if section_id and chunk.get("section_id") == section_id:
        return "section_match"
    if block_id and block_id in (chunk.get("block_ids") or []):
        return "block_match"
    if page is not None:
        ps, pe = chunk.get("page_start"), chunk.get("page_end")
        if ps is not None and pe is not None and ps <= page <= pe:
            return "page_match"
        if ps is not None and pe is None and ps == page:
            return "page_match"
    return None


def retrieve_source_for_checkpoint(
    checkpoint: dict,
    chunk_index: list[dict],
    *,
    context_neighbors: int = 1,
) -> dict:
    """Retrieve the source slices relevant to one checkpoint.

    Returns ``{"retrieved": [...], "scope_used": [...], "retrieval_reason": str}``
    so the audit artifact records *what* was read and *why* (AC #404).
    """
    chunk_index = chunk_index or []
    by_index = {c.get("chunk_index"): c for c in chunk_index if c.get("chunk_index") is not None}
    scope = checkpoint.get("source_scope") or []
    retrieved: dict[Any, dict] = {}
    scope_used: list[dict] = []

    for loc in scope:
        if not isinstance(loc, dict):
            continue
        for chunk in chunk_index:
            reason = _chunk_matches_location(chunk, loc)
            if not reason:
                continue
            scope_used.append({"location": loc, "match_reason": reason})
            idx = chunk.get("chunk_index")
            retrieved[idx] = {
                "chunk_id": chunk.get("chunk_id"),
                "chunk_index": idx,
                "section_id": chunk.get("section_id"),
                "page_start": chunk.get("page_start"),
                "page_end": chunk.get("page_end"),
                "text": chunk.get("text") or "",
                "match_reason": reason,
            }
            # neighbouring context
            for delta in range(1, context_neighbors + 1):
                for nidx in (idx - delta if idx is not None else None,
                             idx + delta if idx is not None else None):
                    if nidx in by_index and nidx not in retrieved:
                        nb = by_index[nidx]
                        retrieved[nidx] = {
                            "chunk_id": nb.get("chunk_id"),
                            "chunk_index": nidx,
                            "section_id": nb.get("section_id"),
                            "page_start": nb.get("page_start"),
                            "page_end": nb.get("page_end"),
                            "text": nb.get("text") or "",
                            "match_reason": "context_neighbor",
                        }

    ordered = [retrieved[k] for k in sorted(retrieved, key=lambda x: (x is None, x))]
    if ordered:
        retrieval_reason = "scope_resolved"
    elif scope:
        retrieval_reason = "scope_unmatched"
    else:
        retrieval_reason = "no_scope"
    return {
        "retrieved": ordered,
        "scope_used": scope_used,
        "retrieval_reason": retrieval_reason,
    }


# ---------------------------------------------------------------------------
# Verdict assembly
# ---------------------------------------------------------------------------

def _empty_result(checkpoint: dict) -> dict:
    return {
        "checkpoint_id": checkpoint.get("checkpoint_id"),
        "target_type": checkpoint.get("target_type"),
        "target_id": checkpoint.get("target_id"),
        "trigger_reason": checkpoint.get("trigger_reason"),
        "verdict": "ambiguous",
        "findings": [],
        "evidence_refs": [],
        "source_locations": [],
        "source_evidence": [],   # source-derived quotes only
        "analysis_notes": [],    # LLM commentary only — kept separate
        "confidence": 0.0,
        "requires_revision": False,
        "review_required": True,
        "audit_method": "deterministic",
    }


def _deterministic_verdict(checkpoint: dict, retrieval: dict) -> dict:
    result = _empty_result(checkpoint)
    trigger = checkpoint.get("trigger_reason") or ""
    base_trigger = trigger.split(":", 1)[0]
    retrieved = retrieval.get("retrieved") or []
    result["source_locations"] = [
        {"chunk_id": r.get("chunk_id"), "section_id": r.get("section_id"),
         "page_start": r.get("page_start"), "page_end": r.get("page_end")}
        for r in retrieved
    ]
    result["source_evidence"] = [
        {"chunk_id": r.get("chunk_id"), "text": r.get("text"),
         "match_reason": r.get("match_reason")}
        for r in retrieved if r.get("match_reason") != "context_neighbor"
    ]

    if base_trigger == "validation" or trigger.startswith("validation:"):
        result["verdict"] = "incomplete"
        result["requires_revision"] = True
        result["review_required"] = True
        result["findings"] = [{
            "kind": "existing_validation_issue",
            "detail": "既存検証指摘。原文根拠を再確認のうえ修正候補化する",
        }]
        result["confidence"] = 0.6
        return result

    if trigger in _DEFECT_TRIGGERS:
        result["verdict"] = _DEFECT_TRIGGERS[trigger]
        result["requires_revision"] = True
        result["review_required"] = True
        result["findings"] = [{"kind": trigger, "detail": checkpoint.get("question", "")}]
        # A concrete cross-reference defect is source-checkable; confidence
        # higher when we actually retrieved the span.
        result["confidence"] = 0.6 if result["source_evidence"] else 0.4
        return result

    if trigger in _AMBIGUOUS_TRIGGERS:
        result["verdict"] = "ambiguous"
        result["requires_revision"] = False   # never auto-confirm
        result["review_required"] = True
        result["findings"] = [{"kind": trigger, "detail": checkpoint.get("question", "")}]
        result["confidence"] = 0.3
        return result

    # Unknown trigger: stay conservative — ambiguous, human review.
    result["findings"] = [{"kind": "unclassified_trigger", "detail": trigger}]
    return result


def _coerce_llm_result(checkpoint: dict, retrieval: dict, raw: Any) -> dict:
    """Validate / normalize an LLM audit result, enforcing the safety rules."""
    result = _deterministic_verdict(checkpoint, retrieval)
    if not isinstance(raw, dict):
        return result  # malformed → deterministic fallback

    verdict = str(raw.get("verdict") or "").strip().lower()
    if verdict not in VERDICTS:
        return result

    evidence_refs = [r for r in (raw.get("evidence_refs") or []) if r]
    # Safety rule: never mark valid without positive source evidence.
    if verdict == "valid" and not (evidence_refs or result["source_evidence"]):
        verdict = "ambiguous"

    confidence = float(raw.get("confidence") or 0.0)
    requires_revision = bool(raw.get("requires_revision", False))
    review_required = bool(raw.get("review_required", False))

    # Safety rule: low-confidence / ambiguous is never auto-confirmed as a
    # revision target; force human review instead.
    if verdict == "ambiguous" or confidence < _LOW_CONFIDENCE:
        requires_revision = False
        review_required = True

    result.update({
        "verdict": verdict,
        "findings": list(raw.get("findings") or result["findings"]),
        "evidence_refs": evidence_refs,
        "analysis_notes": list(raw.get("analysis_notes") or raw.get("notes") or []),
        "confidence": confidence,
        "requires_revision": requires_revision,
        "review_required": review_required,
        "audit_method": "llm",
    })
    return result


def audit_checkpoint(
    checkpoint: dict,
    chunk_index: list[dict],
    *,
    llm_client: Callable[[dict, dict], Any] | None = None,
) -> dict:
    """Retrieve source for a checkpoint and produce an audit result."""
    retrieval = retrieve_source_for_checkpoint(checkpoint, chunk_index)
    if llm_client is None:
        return _deterministic_verdict(checkpoint, retrieval)
    try:
        raw = llm_client(checkpoint, retrieval)
    except Exception:
        return _deterministic_verdict(checkpoint, retrieval)
    return _coerce_llm_result(checkpoint, retrieval, raw)


def run_source_audit(
    *,
    checkpoints: list[dict],
    chunk_index: list[dict],
    llm_client: Callable[[dict, dict], Any] | None = None,
) -> dict:
    """Audit every checkpoint; return results + a revision worklist summary."""
    results = [
        audit_checkpoint(cp, chunk_index, llm_client=llm_client)
        for cp in (checkpoints or [])
    ]
    revision_targets = [
        {"checkpoint_id": r["checkpoint_id"], "target_type": r["target_type"],
         "target_id": r["target_id"], "verdict": r["verdict"]}
        for r in results if r.get("requires_revision")
    ]
    review_items = [
        {"checkpoint_id": r["checkpoint_id"], "target_type": r["target_type"],
         "target_id": r["target_id"], "verdict": r["verdict"]}
        for r in results if r.get("review_required") and not r.get("requires_revision")
    ]
    verdict_counts: dict[str, int] = {}
    for r in results:
        verdict_counts[r["verdict"]] = verdict_counts.get(r["verdict"], 0) + 1
    return {
        "audit_results": results,
        "revision_targets": revision_targets,
        "review_items": review_items,
        "verdict_counts": verdict_counts,
    }
