"""Tests for deterministic concept enrichment (issue #8)."""
from episteme_graph.agents.component_assembly.enrichment import enrich_component_assembly
from episteme_graph.agents.component_assembly.schema import (
    COMPONENTS_VERSION,
    ComponentAssemblyLLMInput,
    ComponentAssemblyResult,
    ComponentRecord,
)


def _component(component_id, *, support_distance, linked_claim_ids, linked_equation_ids=None):
    return ComponentRecord(
        component_id,
        "RelationComponent",
        f"{component_id} label",
        f"{component_id} summary",
        [], [], [], [], [],
        {"claim_ids": [], "equation_ids": list(linked_equation_ids or []),
         "thesis_refs": [], "dsl_refs": {"node_ids": [], "edge_ids": []}},
        "reason",
        0.8,
        [],
        linked_claim_ids=list(linked_claim_ids),
        linked_equation_ids=list(linked_equation_ids or []),
        support_distance_to_headline_claim=support_distance,
    )


def _llm_input():
    return ComponentAssemblyLLMInput(
        document_id="doc",
        cartridge_id=None,
        accepted_claims=[],
        equations=[
            {"equation_id": "eq_1", "defined_symbols": [{"symbol": "b_1"}], "used_symbols": []},
        ],
        thesis_nodes=[],
        dsl_nodes=[],
        dsl_edges=[],
        allowed_component_types=["RelationComponent"],
        allowed_dependency_types=["requires"],
        available_claims=[
            {"claim_id": "claim_1", "atomicity": "atomic", "is_atomic": True,
             "concepts": ["Skewness", "Kurtosis"]},
            {"claim_id": "claim_2", "atomicity": "atomic", "is_atomic": True,
             "concepts": ["Skewness", "Galaxy bias"]},
            {"claim_id": "claim_composite", "atomicity": "composite", "is_atomic": False,
             "concepts": ["Should be ignored"]},
        ],
    )


def test_enrichment_fills_concepts_from_atomic_claims_and_equations():
    comp_a = _component("comp_a", support_distance=0, linked_claim_ids=["claim_1"], linked_equation_ids=["eq_1"])
    result = ComponentAssemblyResult("doc", COMPONENTS_VERSION, None, [comp_a], [], [], 0.8)

    enrich_component_assembly(result, _llm_input())

    concepts = result.components[0].concepts
    assert "Skewness" in concepts
    assert "Kurtosis" in concepts
    assert "b_1" in concepts  # equation symbol


def test_enrichment_ignores_non_atomic_claim_concepts():
    comp = _component("comp_x", support_distance=0, linked_claim_ids=["claim_composite"])
    result = ComponentAssemblyResult("doc", COMPONENTS_VERSION, None, [comp], [], [], 0.8)

    enrich_component_assembly(result, _llm_input())

    assert "Should be ignored" not in result.components[0].concepts


def test_enrichment_splits_introduced_and_reused_by_support_order():
    comp_a = _component("comp_a", support_distance=0, linked_claim_ids=["claim_1"])
    comp_b = _component("comp_b", support_distance=1, linked_claim_ids=["claim_2"])
    # Provide them out of support order to confirm ordering is by distance.
    result = ComponentAssemblyResult("doc", COMPONENTS_VERSION, None, [comp_b, comp_a], [], [], 0.8)

    enrich_component_assembly(result, _llm_input())

    by_id = {c.component_id: c for c in result.components}
    assert "Skewness" in by_id["comp_a"].introduced_concepts
    assert "Kurtosis" in by_id["comp_a"].introduced_concepts
    # comp_b is farther from the headline; Skewness was already introduced by comp_a.
    assert "Skewness" in by_id["comp_b"].reused_concepts
    assert "Galaxy bias" in by_id["comp_b"].introduced_concepts
