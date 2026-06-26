"""Issue #440: components must carry a specific name, type, role_in_thesis,
and teaching_takeaway.

Two unrelated label/responsibility sets verify behavior without domain asserts.
"""
from __future__ import annotations

import pytest

from episteme_graph.agents.component_assembly.enrichment import (
    _fill_role_in_thesis,
    _fill_teaching_takeaway,
)
from episteme_graph.agents.component_assembly.schema import (
    ComponentAssemblyResult,
    ComponentRecord,
    is_generic_component_name,
)
from episteme_graph.agents.component_assembly.validator import ComponentAssemblyValidator


def _component(component_id, label, *, responsibility="derivation", support_role="derivation_core",
               role_in_thesis="", teaching_takeaway=""):
    return ComponentRecord(
        component_id=component_id, component_type="RelationComponent", label=label,
        summary="s", inputs=[], outputs=[], preconditions=[], cautions=[], dependencies=[],
        evidence_refs={}, reason="r", confidence=0.6, review_notes=[],
        responsibility_type=responsibility, support_role=support_role,
        role_in_thesis=role_in_thesis, teaching_takeaway=teaching_takeaway,
    )


def test_is_generic_component_name():
    assert is_generic_component_name("transform")
    assert is_generic_component_name("Relation")
    assert is_generic_component_name("")
    assert not is_generic_component_name("Reynolds stress closure")
    assert not is_generic_component_name("Michaelis-Menten rate law")


@pytest.mark.parametrize("support_role,expected_nonempty", [
    ("result", True), ("derivation_core", True), ("observable_bridge", True),
    ("theory_base", True), ("application", True), ("limitation", True), ("", True),
])
def test_role_in_thesis_filled_for_every_support_role(support_role, expected_nonempty):
    c = _component("comp_1", "Some concrete name", support_role=support_role)
    _fill_role_in_thesis(c)
    assert bool(c.role_in_thesis.strip()) == expected_nonempty


def test_teaching_takeaway_always_non_empty():
    c = _component("comp_1", "Concrete subject closure", teaching_takeaway="")
    _fill_teaching_takeaway(c)
    assert c.teaching_takeaway.strip()


def test_existing_values_are_not_overwritten():
    c = _component("comp_1", "X", role_in_thesis="custom role", teaching_takeaway="custom takeaway")
    _fill_role_in_thesis(c)
    _fill_teaching_takeaway(c)
    assert c.role_in_thesis == "custom role"
    assert c.teaching_takeaway == "custom takeaway"


def test_to_dict_projects_name_and_type():
    c = _component("comp_1", "Concrete name", responsibility="derivation")
    result = ComponentAssemblyResult("doc", "v1", None, [c], [], [], 0.7)
    payload = result.to_dict()["components"][0]
    assert payload["name"] == "Concrete name"
    assert payload["type"]  # non-empty classification


_GENERIC_DOMAINS = {
    "domain_a": "transform",      # bare operation
    "domain_b": "relation",       # bare responsibility
}


@pytest.mark.parametrize("domain", sorted(_GENERIC_DOMAINS))
def test_validator_flags_generic_name(domain):
    label = _GENERIC_DOMAINS[domain]
    c = _component("comp_1", label, role_in_thesis="States a result.",
                   teaching_takeaway="t")
    result = ComponentAssemblyResult("doc", "v1", None, [c], [], [], 0.6)
    issues = ComponentAssemblyValidator().validate(result, None)
    codes = {i.rule_id for i in issues}
    assert "COMPONENT_NAME_GENERIC" in codes


def test_validator_does_not_flag_concrete_name_with_metadata():
    c = _component("comp_1", "Closure relation for stress",
                   role_in_thesis="Derives the core relation.",
                   teaching_takeaway="One responsibility.")
    result = ComponentAssemblyResult("doc", "v1", None, [c], [], [], 0.6)
    issues = ComponentAssemblyValidator().validate(result, None)
    codes = {i.rule_id for i in issues}
    assert "COMPONENT_NAME_GENERIC" not in codes
    assert "COMPONENT_ROLE_IN_THESIS_EMPTY" not in codes
    assert "COMPONENT_TEACHING_TAKEAWAY_EMPTY" not in codes
