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
    span_to_ids: dict[str, list[str]] = defaultdict(list)
    safe_span_to_ids: dict[str, list[str]] = defaultdict(list)
    for claim in claims:
        claim_id = str(getattr(claim, "claim_id", "") or "")
        if not claim_id:
            continue
        lookup[("id", claim_id)] = claim_id
        lookup[("legacy", claim_id)] = claim_id
        section_id = str(getattr(claim, "section_id", "") or "")
        for span_id_raw in getattr(claim, "source_span_ids", []) or []:
            span_id = str(span_id_raw)
            span_to_ids[span_id].append(claim_id)
            if section_id:
                lookup[("section_span", section_id, span_id)] = claim_id
            safe = re.sub(r"[^A-Za-z0-9_]+", "_", span_id)
            safe_span_to_ids[f"claim_{safe}"].append(claim_id)

    for span_id, ids in span_to_ids.items():
        unique = _unique(ids)
        if len(unique) == 1:
            lookup[("span", span_id)] = unique[0]
    for legacy_id, ids in safe_span_to_ids.items():
        unique = _unique(ids)
        if len(unique) == 1:
            lookup[("legacy", legacy_id)] = unique[0]
    return lookup


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
