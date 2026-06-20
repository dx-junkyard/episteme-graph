"""Canonical split-recommendation schema and normalization (issue #417).

The component granularity analyzer, ComponentRefiner, and the revision
checkpoint planner all exchange a *split recommendation*: the structured signal
that says "this component bundles more than one reusable theory unit and should
be decomposed".

Historically these stages disagreed on the key name. The granularity analyzer
emits::

    {"required": true, "reasons": [...], "suggested_components": [...]}

while the revision checkpoint planner only read ``split_required`` /
``should_split``. As a result a component with ``required: true`` was never
turned into a revision checkpoint and the split silently went unprocessed.

This module is the single source of truth for the schema. The canonical key is
``required``. ``normalize_split_recommendation`` accepts any of the legacy keys
(``required`` / ``split_required`` / ``should_split``) during the migration
period and always returns the canonical shape so every downstream stage reads
the same thing.
"""
from __future__ import annotations

from typing import Any

# Canonical key. All stages must read split state through this module so the
# canonical key stays authoritative even while legacy keys are still produced.
CANONICAL_SPLIT_KEY = "required"

# Legacy keys accepted on input during the migration period (#417).
_LEGACY_REQUIRED_KEYS = ("required", "split_required", "should_split")


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def split_is_required(recommendation: Any) -> bool:
    """Return whether a (possibly legacy-shaped) recommendation requests a split.

    Reads ``required`` first, then the legacy ``split_required`` / ``should_split``
    aliases, so callers never miss a split signal because of a key mismatch.
    """
    rec = _as_dict(recommendation)
    return any(bool(rec.get(key)) for key in _LEGACY_REQUIRED_KEYS)


def normalize_split_recommendation(recommendation: Any) -> dict:
    """Normalize any split recommendation to the canonical ``required`` schema.

    Output shape::

        {"required": bool, "reasons": list[str], "suggested_components": list[dict]}

    Unknown extra keys are preserved (so richer producers keep their metadata),
    but the canonical fields are always present and authoritative.
    """
    rec = dict(_as_dict(recommendation))
    required = split_is_required(rec)
    reasons = [str(r) for r in _as_list(rec.get("reasons")) if str(r)]
    suggested = [s for s in _as_list(rec.get("suggested_components")) if isinstance(s, dict)]
    # Drop the legacy alias keys so only the canonical key remains authoritative.
    for legacy in ("split_required", "should_split"):
        rec.pop(legacy, None)
    rec[CANONICAL_SPLIT_KEY] = required
    rec["reasons"] = reasons
    rec["suggested_components"] = suggested
    return rec
