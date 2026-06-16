"""Tests for issue #384 (no hard component cap) and #385 (conditional default-on refiner)."""

from __future__ import annotations

from episteme_graph.agents.component_assembly.agent import _refinement_triggered
from episteme_graph.agents.component_assembly.prompt import _SYSTEM_CONTENT
from episteme_graph.agents.component_assembly.schema import (
    ComponentAssemblyResult,
    ComponentRecord,
)


def _component(**overrides) -> ComponentRecord:
    base = dict(
        component_id="comp_1",
        component_type="MethodComponent",
        label="c",
        summary="s",
        inputs=[],
        outputs=[],
        preconditions=[],
        cautions=[],
        dependencies=[],
        evidence_refs={},
        reason="",
        confidence=0.5,
        review_notes=[],
    )
    base.update(overrides)
    return ComponentRecord(**base)


def _result(components) -> ComponentAssemblyResult:
    return ComponentAssemblyResult(
        document_id="doc_1",
        components_version="0.1.0",
        cartridge_id=None,
        components=components,
        assembly_hints=[],
        review_notes=[],
        confidence=0.5,
    )


class _LLMInput:
    def __init__(self, equations=None, evidence=None):
        self.available_equations = equations or []
        self.available_evidence = evidence or []


class _Chain:
    def __init__(self, linked=None):
        self.linked_component_ids = linked or []


class _Derivations:
    def __init__(self, chains):
        self.chains = chains


# ---------------------------------------------------------------------------
# #384: the hard "AT MOST 6 components" cap is gone from the prompt.
# ---------------------------------------------------------------------------


def test_prompt_has_no_hard_component_cap():
    assert "AT MOST 6" not in _SYSTEM_CONTENT
    assert "ADAPTIVE granularity" in _SYSTEM_CONTENT


# ---------------------------------------------------------------------------
# #385: refiner auto-triggers on quality signals.
# ---------------------------------------------------------------------------


def test_split_recommendation_triggers_refiner():
    comp = _component(split_recommendation={"required": True, "reasons": ["x"]})
    run, reasons = _refinement_triggered(_result([comp]), _LLMInput(), None)
    assert run is True
    assert "component_split_recommended" in reasons


def test_mixed_responsibility_triggers_refiner():
    comp = _component(component_quality={"granularity_status": "mixed_responsibility"})
    run, reasons = _refinement_triggered(_result([comp]), _LLMInput(), None)
    assert run is True
    assert "coarse_or_mixed_responsibility_component" in reasons


def test_few_components_many_equations_triggers_refiner():
    comp = _component()
    llm_input = _LLMInput(equations=[{"equation_id": f"eq_{i}"} for i in range(8)])
    run, reasons = _refinement_triggered(_result([comp]), llm_input, None)
    assert run is True
    assert "few_components_with_many_artifacts" in reasons


def test_unlinked_derivation_chains_trigger_refiner():
    comp = _component()
    derivations = _Derivations([_Chain(linked=[])])
    run, reasons = _refinement_triggered(_result([comp, _component(component_id="comp_2")]), _LLMInput(), derivations)
    assert run is True
    assert "derivation_chains_unlinked_to_components" in reasons


def test_clean_components_do_not_trigger_refiner():
    comp = _component(
        component_quality={"granularity_status": "good", "responsibility_count": 1},
        split_recommendation={"required": False},
    )
    comp2 = _component(
        component_id="comp_2",
        component_quality={"granularity_status": "good", "responsibility_count": 1},
        split_recommendation={"required": False},
    )
    comp3 = _component(
        component_id="comp_3",
        component_quality={"granularity_status": "good", "responsibility_count": 1},
        split_recommendation={"required": False},
    )
    derivations = _Derivations([_Chain(linked=["comp_1"])])
    run, reasons = _refinement_triggered(_result([comp, comp2, comp3]), _LLMInput(), derivations)
    assert run is False
    assert reasons == []
