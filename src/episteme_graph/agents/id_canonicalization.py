"""Helpers for keeping cross-artifact references on canonical IDs."""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any


def legacy_claim_id_for_span(span: Any) -> str:
    """Return the pre-canonical claim ID used by early agent prompts."""
    block_id = getattr(span, "block_id", None)
    span_id = getattr(span, "span_id", None)
    if block_id and span_id:
        return f"claim:{block_id}:{span_id}"
    if span_id:
        safe = re.sub(r"[^A-Za-z0-9_]+", "_", str(span_id))
        return f"claim_{safe}"
    return ""


def canonical_claim_id_for_span(span: Any, claim_objects: Any | None = None) -> str:
    """Resolve a qualified span to the ClaimObjectBuilder canonical claim_id.

    ClaimObjectBuilder is the source of truth for claim IDs. It preserves
    source_span_ids and section_id, so that pair is the most stable bridge from
    earlier span records to canonical claim objects. If no claim object match is
    available, fall back to the legacy prompt ID to keep standalone tests and
    partial pipeline stages usable.
    """
    lookup = _claim_lookup(claim_objects)
    span_id = str(getattr(span, "span_id", "") or "")
    section_id = str(getattr(span, "section_id", "") or "")
    legacy_id = legacy_claim_id_for_span(span)

    if section_id and span_id:
        match = lookup.get(("section_span", section_id, span_id))
        if match:
            return match
    if span_id:
        match = lookup.get(("span", span_id))
        if match:
            return match
    if legacy_id:
        match = lookup.get(("legacy", legacy_id))
        if match:
            return match
        return legacy_id
    return ""


def canonicalize_claim_refs(
    value: Any,
    claim_objects: Any | None = None,
    extra_aliases: dict[str, str] | None = None,
) -> Any:
    """Recursively canonicalize claim reference lists in artifact-like objects."""
    lookup = _claim_lookup(claim_objects)
    aliases = dict(extra_aliases or {})
    if not lookup and not aliases:
        return value
    if isinstance(value, list):
        return [canonicalize_claim_refs(item, claim_objects, aliases) for item in value]
    if not isinstance(value, dict):
        return value

    out = dict(value)
    for key in ("claim_ids", "linked_claim_ids", "evidence_claims", "required_claim_ids"):
        refs = out.get(key)
        if isinstance(refs, list):
            out[key] = _map_claim_ref_list(refs, lookup, aliases)
    return {
        key: canonicalize_claim_refs(item, claim_objects, aliases)
        for key, item in out.items()
    }


def canonical_claim_id_set(claim_objects: Any | None) -> set[str]:
    """Return the set of final claim IDs from a ClaimObjectBuildResult-like object.

    ClaimObjectBuilder is the single source of truth for claim IDs after the
    claim_object_builder stage (issue #340). Downstream artifacts may only
    reference IDs in this set; anything else is a provisional/unresolved ref.
    """
    ids: set[str] = set()
    for claim in getattr(claim_objects, "claims", []) or []:
        cid = str(getattr(claim, "claim_id", "") or "")
        if cid:
            ids.add(cid)
    return ids


def filter_claim_ref_list(
    refs: Any,
    claim_objects: Any | None,
    extra_aliases: dict[str, str] | None = None,
) -> tuple[list[str], list[str]]:
    """Resolve claim refs to canonical IDs, dropping any that don't resolve (issue #340).

    Each ref is mapped through the canonicalization lookup / aliases. A ref is
    kept (in canonical form) only when it resolves to an ID present in the final
    claim set; otherwise it is reported as dropped. When ``claim_objects`` is
    ``None`` the canonicalization context is unknown, so refs are returned
    unchanged (standalone / partial-pipeline behaviour).

    Returns ``(kept_canonical_ids, dropped_original_refs)``.
    """
    if claim_objects is None:
        return [str(r) for r in (refs or [])], []

    lookup = _claim_lookup(claim_objects)
    aliases = dict(extra_aliases or {})
    final_ids = canonical_claim_id_set(claim_objects)

    kept: list[str] = []
    dropped: list[str] = []
    for ref in refs or []:
        value = str(ref)
        mapped = (
            aliases.get(value)
            or lookup.get(("id", value))
            or lookup.get(("legacy", value))
            or value
        )
        if mapped in final_ids:
            if mapped not in kept:
                kept.append(mapped)
        elif value not in dropped:
            dropped.append(value)
    return kept, dropped


def canonicalize_equation_claim_links(
    equations: Any,
    claim_objects: Any | None,
    extra_aliases: dict[str, str] | None = None,
) -> list[dict]:
    """Rewrite equation ``semantics.linked_claim_ids`` to canonical claim IDs (issue #340).

    Mutates each EquationRecord in place so that ``linked_claim_ids`` only holds
    IDs present in the final claim set. Provisional / unresolved refs are removed
    and reported so the caller can keep them as review metadata.

    Returns a list of ``{"equation_id", "dropped_claim_ids"}`` for equations that
    lost references.
    """
    dropped_report: list[dict] = []
    for eq in getattr(equations, "equations", []) or []:
        sem = getattr(eq, "semantics", None)
        if sem is None:
            continue
        refs = list(getattr(sem, "linked_claim_ids", []) or [])
        if not refs:
            continue
        kept, dropped = filter_claim_ref_list(refs, claim_objects, extra_aliases)
        if kept != refs:
            sem.linked_claim_ids = kept
        if dropped:
            dropped_report.append({
                "equation_id": str(getattr(eq, "equation_id", "") or ""),
                "dropped_claim_ids": dropped,
            })
    return dropped_report


_DERIVATION_STEP_CLAIM_FIELDS = (
    "required_claim_ids",
    "input_claim_ids",
    "output_claim_ids",
    "claim_ids",
)


def canonicalize_derivation_claim_refs(
    derivations: Any,
    claim_objects: Any | None,
    extra_aliases: dict[str, str] | None = None,
) -> list[dict]:
    """Rewrite derivation step claim refs to canonical claim IDs (issue #340).

    Mutates each DerivationStep in place so that its claim reference fields only
    hold IDs present in the final claim set. Provisional / unresolved refs are
    removed and reported.

    Returns a list of ``{"derivation_id", "step_id", "dropped_claim_ids"}`` for
    steps that lost references.
    """
    dropped_report: list[dict] = []
    for chain in getattr(derivations, "chains", []) or []:
        for step in getattr(chain, "steps", []) or []:
            step_dropped: list[str] = []
            for field_name in _DERIVATION_STEP_CLAIM_FIELDS:
                if not hasattr(step, field_name):
                    continue
                refs = list(getattr(step, field_name) or [])
                if not refs:
                    continue
                kept, dropped = filter_claim_ref_list(refs, claim_objects, extra_aliases)
                if kept != refs:
                    setattr(step, field_name, kept)
                for ref in dropped:
                    if ref not in step_dropped:
                        step_dropped.append(ref)
            if step_dropped:
                dropped_report.append({
                    "derivation_id": str(getattr(chain, "derivation_id", "") or ""),
                    "step_id": str(getattr(step, "step_id", "") or ""),
                    "dropped_claim_ids": step_dropped,
                })
    return dropped_report


def claim_aliases_from_accepted_claims(accepted_claims: list[dict]) -> dict[str, str]:
    """Build legacy/ref aliases from LLM input claims."""
    aliases: dict[str, str] = {}
    for claim in accepted_claims or []:
        canonical = str(claim.get("claim_id") or "")
        legacy = str(claim.get("legacy_claim_id") or "")
        if canonical and legacy and canonical != legacy:
            aliases[legacy] = canonical
    return aliases


def _claim_lookup(claim_objects: Any | None) -> dict[tuple[str, str] | tuple[str, str, str], str]:
    claims = list(getattr(claim_objects, "claims", []) or [])
    if not claims:
        return {}

    lookup: dict[tuple[str, str] | tuple[str, str, str], str] = {}
    span_claims: dict[str, list[Any]] = defaultdict(list)
    section_span_claims: dict[tuple[str, str], list[Any]] = defaultdict(list)
    safe_span_claims: dict[str, list[Any]] = defaultdict(list)
    for claim in claims:
        claim_id = str(getattr(claim, "claim_id", "") or "")
        if not claim_id:
            continue
        lookup[("id", claim_id)] = claim_id
        lookup[("legacy", claim_id)] = claim_id
        section_id = str(getattr(claim, "section_id", "") or "")
        for span_id_raw in getattr(claim, "source_span_ids", []) or []:
            span_id = str(span_id_raw)
            span_claims[span_id].append(claim)
            if section_id:
                section_span_claims[(section_id, span_id)].append(claim)
            safe = re.sub(r"[^A-Za-z0-9_]+", "_", span_id)
            safe_span_claims[f"claim_{safe}"].append(claim)

    for span_id, group in span_claims.items():
        resolved = _resolve_span_claim_id(group)
        if resolved:
            lookup[("span", span_id)] = resolved
    for (section_id, span_id), group in section_span_claims.items():
        resolved = _resolve_span_claim_id(group)
        if resolved:
            lookup[("section_span", section_id, span_id)] = resolved
    for legacy_id, group in safe_span_claims.items():
        resolved = _resolve_span_claim_id(group)
        if resolved and ("legacy", legacy_id) not in lookup:
            lookup[("legacy", legacy_id)] = resolved
    return lookup


def _resolve_span_claim_id(group: list[Any]) -> str:
    """Pick the canonical claim ID for a span shared by several claim objects.

    A span rewritten into atomic claims keeps its span_id on both the composite
    parent and each child (issue #317), so the parent — the record representing
    the whole span — is canonical. The parent is identified by hierarchy links
    (children carry ``parent_claim_id`` / parents list ``subclaim_ids``) or,
    failing that, as the single non-atomic record among atomic ones. Returns ""
    when the span is genuinely ambiguous (multiple unrelated claims).
    """
    ids = _unique([str(getattr(c, "claim_id", "") or "") for c in group])
    if len(ids) == 1:
        return ids[0]
    child_ids = set()
    for c in group:
        child_ids.update(str(v) for v in getattr(c, "subclaim_ids", None) or [])
    roots = _unique([
        str(getattr(c, "claim_id", "") or "")
        for c in group
        if not getattr(c, "parent_claim_id", None)
        and str(getattr(c, "claim_id", "") or "") not in child_ids
    ])
    if len(roots) == 1:
        return roots[0]
    non_atomic = _unique([
        str(getattr(c, "claim_id", "") or "")
        for c in group
        if not _claim_obj_is_atomic(c)
    ])
    if len(non_atomic) == 1 and len(non_atomic) < len(ids):
        return non_atomic[0]
    return ""


def _claim_obj_is_atomic(claim: Any) -> bool:
    atomicity = str(getattr(claim, "atomicity", "atomic") or "atomic").lower()
    return bool(getattr(claim, "is_atomic", atomicity == "atomic")) and atomicity == "atomic"


def _map_claim_ref_list(
    refs: list[Any],
    lookup: dict[tuple[str, str] | tuple[str, str, str], str],
    aliases: dict[str, str],
) -> list[str]:
    result: list[str] = []
    for ref in refs:
        value = str(ref)
        mapped = aliases.get(value) or lookup.get(("id", value)) or lookup.get(("legacy", value)) or value
        if mapped not in result:
            result.append(mapped)
    return result


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result
