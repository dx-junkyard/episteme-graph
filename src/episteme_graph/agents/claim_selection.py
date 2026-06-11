"""Shared claim input selection policy for downstream LLM input builders (issue #356).

thesis_reconstruction / dsl_linking / component_assembly each cap how many
claims they expose to the LLM. Before this module each builder truncated its
own list in document order, so the visible claim set differed per stage and
claims silently disappeared. This module centralises the ordering policy and
makes every drop explicit:

- importance order: claim_tier (paper_core first) → confidence (desc) →
  original document order as the stable tie-break
- claims cut by the limit are returned as ``excluded`` entries
  ({claim_id, span_id, excluded_at_stage, reason, referenced_by_thesis}) so
  agents can persist them on their results instead of dropping silently.

Selected rows keep their original relative (document) order — only membership
is decided by importance — so prompts remain stable for the LLM.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Lower value = selected first. Unknown tiers rank with background.
TIER_PRIORITY = {
    "paper_core": 0,
    "paper_supporting": 1,
    "background": 2,
    "prior_work": 3,
    "meta": 4,
}
_DEFAULT_TIER_PRIORITY = TIER_PRIORITY["background"]

EXCLUSION_REASON_LIMIT = "max_claims_exceeded"

# Validation rule ids surfaced by agents when the limit drops claims.
RULE_CLAIM_INPUT_LIMIT_EXCEEDED = "claim_input_limit_exceeded"
RULE_THESIS_REFERENCED_CLAIM_EXCLUDED = "thesis_referenced_claim_excluded"


@dataclass
class ClaimSelection:
    selected: list[dict]
    excluded: list[dict] = field(default_factory=list)


def select_claim_rows(
    rows: list[dict],
    limit: int,
    *,
    stage: str,
    thesis_claim_ids: set[str] | None = None,
) -> ClaimSelection:
    """Apply the shared importance-based limit to claim input rows.

    ``rows`` are the fully built claim input dicts (must carry ``claim_id``;
    ``claim_tier`` and ``confidence`` are used for ranking when present).
    """
    rows = list(rows or [])
    if limit <= 0 or len(rows) <= limit:
        return ClaimSelection(selected=rows)

    def _priority(index: int) -> tuple:
        row = rows[index]
        tier = str(row.get("claim_tier") or "")
        try:
            confidence = float(row.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        return (
            TIER_PRIORITY.get(tier, _DEFAULT_TIER_PRIORITY),
            -confidence,
            index,
        )

    ranked = sorted(range(len(rows)), key=_priority)
    keep = set(ranked[:limit])
    selected = [rows[i] for i in range(len(rows)) if i in keep]

    thesis_ids = {str(v) for v in (thesis_claim_ids or set()) if v}
    excluded: list[dict] = []
    for i in range(len(rows)):
        if i in keep:
            continue
        row = rows[i]
        claim_id = str(row.get("claim_id") or "")
        excluded.append({
            "claim_id": claim_id,
            "span_id": str(row.get("span_id") or ""),
            "claim_tier": str(row.get("claim_tier") or ""),
            "excluded_at_stage": stage,
            "reason": EXCLUSION_REASON_LIMIT,
            "referenced_by_thesis": bool(claim_id and claim_id in thesis_ids),
        })
    return ClaimSelection(selected=selected, excluded=excluded)


def thesis_claim_id_set(thesis) -> set[str]:
    """Claim ids referenced by central_thesis / support_structure (issue #356)."""
    ids: set[str] = set()
    if thesis is None:
        return ids
    central = getattr(thesis, "central_thesis", None)
    if isinstance(central, dict):
        ids.update(str(v) for v in (central.get("claim_ids") or []) if v)
    support = getattr(thesis, "support_structure", None)
    if isinstance(support, dict):
        for entries in support.values():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if isinstance(entry, dict):
                    ids.update(str(v) for v in (entry.get("claim_ids") or []) if v)
    return ids


def selection_issue_payloads(excluded: list[dict], *, stage: str) -> list[dict]:
    """Warning payloads ({rule_id, severity, message, field}) for dropped claims.

    Agents wrap these in their module-local ValidationIssue dataclass so the
    drops show up in export_validation review metadata (issue #356).
    """
    excluded = [e for e in (excluded or []) if isinstance(e, dict)]
    if not excluded:
        return []
    payloads = [{
        "rule_id": RULE_CLAIM_INPUT_LIMIT_EXCEEDED,
        "severity": "warning",
        "message": (
            f"{stage} input limit dropped {len(excluded)} claim(s): "
            + ", ".join(
                e.get("claim_id") or e.get("span_id") or "?"
                for e in excluded[:8]
            )
            + ("…" if len(excluded) > 8 else "")
        ),
        "field": "accepted_claims",
    }]
    thesis_referenced = [
        e for e in excluded if e.get("referenced_by_thesis")
    ]
    if thesis_referenced:
        payloads.append({
            "rule_id": RULE_THESIS_REFERENCED_CLAIM_EXCLUDED,
            "severity": "warning",
            "message": (
                f"{stage} input limit dropped thesis-referenced claim(s): "
                + ", ".join(
                    e.get("claim_id") or "?" for e in thesis_referenced[:8]
                )
                + ("…" if len(thesis_referenced) > 8 else "")
            ),
            "field": "accepted_claims",
        })
    return payloads
