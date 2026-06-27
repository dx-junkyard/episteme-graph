"""Issue #443: shared reference resolution by set membership (no name patterns),
ID normalization, and canonical-field alias-conflict detection.

Multi-domain fixtures use atypical-but-legitimate IDs and reference the canonical
field map rather than hardcoding field names.
"""
from __future__ import annotations

import pytest

from episteme_graph.agents.ref_resolution import (
    CANONICAL_FIELD_MAP,
    RefResolver,
    detect_field_alias_conflicts,
    id_set,
    normalize_ref,
)


def test_normalize_absorbs_single_scope_prefix():
    assert normalize_ref("docA:node_5") == "node_5"
    assert normalize_ref("sec_3:eq_1") == "eq_1"
    assert normalize_ref("  node_5  ") == "node_5"
    # bare ids unchanged
    assert normalize_ref("comp_001") == "comp_001"
    # structural multi-separator ids are NOT mangled
    assert normalize_ref("claim:block:span") == "claim:block:span"


@pytest.mark.parametrize("known,ref", [
    (["node_5"], "docA:node_5"),       # prefixed referrer, bare referent
    (["docA:node_5"], "node_5"),       # bare referrer, prefixed referent
    (["comp_001"], "comp_001"),        # atypical-but-legitimate id (legacy-looking)
    (["claim_span_x"], "claim_span_x"),
])
def test_membership_is_form_independent(known, ref):
    resolver = RefResolver(known)
    assert resolver.is_known(ref)
    assert not resolver.is_dangling(ref)


def test_alias_map_resolution():
    resolver = RefResolver(["canonical_1"], alias_map={"legacy_1": "canonical_1"})
    assert resolver.resolve("legacy_1") == "canonical_1"
    assert resolver.is_known("legacy_1")
    assert resolver.is_dangling("not_a_thing")


def test_dangling_only_when_not_in_set():
    resolver = RefResolver(["a", "b"])
    assert resolver.is_dangling("c")
    assert not resolver.is_dangling("a")


def test_empty_known_set_treats_all_as_known():
    # Standalone / partial context: cannot judge membership → never false-dangling.
    resolver = RefResolver([])
    assert resolver.is_known("anything")


def test_id_set_normalizes():
    assert id_set(["docA:x", "y", " z "]) == {"x", "y", "z"}


# --------------------------------------------------------------------------- #
# Field alias conflicts
# --------------------------------------------------------------------------- #

def test_no_conflict_when_alias_mirrors_canonical():
    # name == label, type == responsibility_type → mirrors, not conflicts.
    obj = {
        "responsibility_type": "derivation", "type": "derivation",
        "name": "Closure relation", "label": "Closure relation",
    }
    assert detect_field_alias_conflicts(obj) == []


def test_conflict_when_alias_differs():
    obj = {"responsibility_type": "derivation", "type": "definition"}
    conflicts = detect_field_alias_conflicts(obj)
    assert len(conflicts) == 1
    assert conflicts[0]["concept"] == "component_responsibility"
    assert conflicts[0]["canonical"] == "responsibility_type"
    assert conflicts[0]["alias"] == "type"


def test_no_conflict_when_only_one_alias_present():
    assert detect_field_alias_conflicts({"name": "X"}) == []
    assert detect_field_alias_conflicts({"label": "X"}) == []


def test_canonical_field_map_has_no_self_alias():
    # The canonical name must not also appear in its own alias list.
    for concept, spec in CANONICAL_FIELD_MAP.items():
        assert spec["canonical"] not in (spec.get("aliases") or []), concept
