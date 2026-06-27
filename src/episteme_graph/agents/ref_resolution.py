"""Shared cross-artifact reference resolution (issue #443).

Single source of truth for how a reference string is normalized and resolved to
a canonical ID, and for whether a reference is dangling. The rules here are
domain-agnostic and never branch on ID name form / contained tokens:

  * ``normalize_ref`` absorbs a single leading scope/document prefix
    (``scope:bare`` → ``bare``) so a prefixed referent and a bare referrer (or
    vice-versa) compare equal. A genuinely structural id with more than one
    separator (e.g. a legacy ``claim:block:span``) is left untouched.
  * ``RefResolver`` maps a reference through an explicit alias map and the
    normalized known-id set. ``is_dangling`` is *pure set membership* — it never
    inspects whether the id "looks legacy".
  * ``CANONICAL_FIELD_MAP`` records, per concept, the one canonical field name
    and its tolerated aliases; ``detect_field_alias_conflicts`` flags an object
    that emits the same concept under multiple keys with *conflicting* values.
"""
from __future__ import annotations

from typing import Any, Iterable

# A single leading ``scope:`` prefix is a scope/document qualifier and is
# absorbed during normalization. Two or more separators indicate a structural
# id (e.g. the legacy ``claim:block:span`` form) and are left intact.
_SCOPE_SEPARATOR = ":"


def normalize_ref(ref: Any) -> str:
    """Normalize an ID expression so prefixed/bare forms compare equal (#443).

    Trims surrounding whitespace and strips a single leading ``scope:`` prefix.
    Domain-agnostic: it does not look at which tokens the id contains.
    """
    text = str(ref or "").strip()
    if not text:
        return ""
    if text.count(_SCOPE_SEPARATOR) == 1:
        prefix, _, rest = text.partition(_SCOPE_SEPARATOR)
        if prefix and rest:
            return rest
    return text


def id_set(ids: Iterable[Any]) -> set[str]:
    """Return the normalized canonical-id set for a collection of ids (#443)."""
    out: set[str] = set()
    for value in ids or []:
        normalized = normalize_ref(value)
        if normalized:
            out.add(normalized)
    return out


class RefResolver:
    """Resolve and membership-test references against a known canonical id set.

    ``alias_map`` maps a known alias (any form) to its canonical id; it is applied
    after normalization. Both the alias keys and the known ids are normalized so
    prefix/scope differences are absorbed on both sides.
    """

    def __init__(
        self,
        known_ids: Iterable[Any] = (),
        alias_map: dict[Any, Any] | None = None,
    ) -> None:
        self._known = id_set(known_ids)
        self._alias: dict[str, str] = {}
        for raw_alias, canonical in (alias_map or {}).items():
            alias_key = normalize_ref(raw_alias)
            canonical_id = normalize_ref(canonical)
            if alias_key and canonical_id:
                self._alias[alias_key] = canonical_id

    def resolve(self, ref: Any) -> str:
        """Return the canonical id for ``ref`` (alias map then normalization)."""
        normalized = normalize_ref(ref)
        return self._alias.get(normalized, normalized)

    def is_known(self, ref: Any) -> bool:
        """Membership only — no name-pattern inspection (#443)."""
        if not self._known:
            # No known set supplied: cannot judge membership; treat as known so
            # standalone / partial contexts do not raise false dangling refs.
            return True
        return self.resolve(ref) in self._known

    def is_dangling(self, ref: Any) -> bool:
        return not self.is_known(ref)

    @property
    def known(self) -> set[str]:
        return set(self._known)


# ---------------------------------------------------------------------------
# Canonical field names (issue #443): one name per concept + tolerated aliases.
# ---------------------------------------------------------------------------

# concept -> {"canonical": <name>, "aliases": [<alias>, ...]}
# ``type`` is the historical serialization alias of ``responsibility_type`` (the
# reusable-responsibility classification), NOT of the cartridge ``component_type``
# — they are distinct concepts and must not be conflated.
CANONICAL_FIELD_MAP: dict[str, dict[str, Any]] = {
    "component_responsibility": {"canonical": "responsibility_type", "aliases": ["type"]},
    "component_name": {"canonical": "name", "aliases": ["label"]},
    "teaching_takeaway": {"canonical": "teaching_takeaway", "aliases": ["teacher_notes"]},
}


def _non_empty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, tuple, set)):
        return bool(value)
    return True


def detect_field_alias_conflicts(
    obj: dict,
    field_map: dict[str, dict[str, Any]] | None = None,
) -> list[dict]:
    """Detect same-concept fields emitted under conflicting alias names (#443).

    A conflict is reported only when both a canonical field and one of its
    aliases are present with *different* non-empty values — that is the harmful
    case where a consumer reading one name sees a different (or "empty") value
    than a consumer reading the other. Equal mirrors are tolerated.

    Returns a list of ``{"concept", "canonical", "alias", "canonical_value",
    "alias_value"}`` dicts.
    """
    field_map = field_map or CANONICAL_FIELD_MAP
    if not isinstance(obj, dict):
        return []
    conflicts: list[dict] = []
    for concept, spec in field_map.items():
        canonical = spec.get("canonical")
        if canonical not in obj:
            continue
        canonical_value = obj.get(canonical)
        for alias in spec.get("aliases") or []:
            if alias not in obj:
                continue
            alias_value = obj.get(alias)
            if not (_non_empty(canonical_value) and _non_empty(alias_value)):
                continue
            if canonical_value != alias_value:
                conflicts.append({
                    "concept": concept,
                    "canonical": canonical,
                    "alias": alias,
                    "canonical_value": canonical_value,
                    "alias_value": alias_value,
                })
    return conflicts
