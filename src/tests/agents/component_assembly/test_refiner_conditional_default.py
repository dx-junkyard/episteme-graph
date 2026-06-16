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


# ---------------------------------------------------------------------------
# #392: no fixed cap anywhere in the prompt; domain-neutral wording.
# ---------------------------------------------------------------------------


def test_prompt_has_no_fixed_component_cap_anywhere():
    from episteme_graph.agents.component_assembly.prompt import (
        ComponentAssemblyPromptFactory,
    )
    from episteme_graph.agents.component_assembly.schema import ComponentAssemblyLLMInput

    llm_input = ComponentAssemblyLLMInput(
        document_id="doc", cartridge_id=None, accepted_claims=[], available_claims=[],
        available_evidence=[], available_equations=[], thesis_nodes=[], dsl_nodes=[],
        dsl_edges=[], allowed_component_types=["RelationComponent"],
        allowed_dependency_types=["depends_on"], available_derivation_ids=[],
    ) if False else None
    user_content = ComponentAssemblyPromptFactory()._build_user_content.__doc__
    # Inspect the raw constraint strings directly.
    import inspect
    src = inspect.getsource(ComponentAssemblyPromptFactory._build_user_content)
    assert "AT MOST 3" not in src
    assert "AT MOST" not in src


def test_prompt_is_domain_neutral():
    from episteme_graph.agents.component_assembly import prompt as prompt_mod
    import inspect
    text = (prompt_mod._SYSTEM_CONTENT + inspect.getsource(prompt_mod)).lower()
    for term in ("bias", "skewness", "kurtosis", "galaxy", "smoothed"):
        assert term not in text, f"domain-specific term {term!r} leaked into prompt"


def test_three_to_four_component_compression_triggers_refiner():
    # 4 coarse components with 15+ equations must trigger refinement (#392).
    comps = [_component(component_id=f"comp_{i}") for i in range(4)]
    llm_input = _LLMInput(equations=[{"equation_id": f"eq_{i}"} for i in range(15)])
    run, reasons = _refinement_triggered(_result(comps), llm_input, None)
    assert run is True
    assert "few_components_with_many_equations" in reasons


def test_three_components_many_evidence_triggers_refiner():
    comps = [_component(component_id=f"comp_{i}") for i in range(3)]
    llm_input = _LLMInput(evidence=[{"evidence_id": f"ev_{i}"} for i in range(30)])
    run, reasons = _refinement_triggered(_result(comps), llm_input, None)
    assert run is True
    assert "few_components_with_many_evidence" in reasons


def test_component_with_many_equations_triggers_refiner():
    comp = _component(component_quality={"granularity_status": "good", "equation_count": 9})
    run, reasons = _refinement_triggered(_result([comp]), _LLMInput(), None)
    assert run is True
    assert "component_links_many_equations" in reasons
