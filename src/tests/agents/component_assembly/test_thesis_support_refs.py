"""Tests for deterministic supports_thesis_node_ids enrichment (issue #354)."""
from episteme_graph.agents.component_assembly.enrichment import enrich_component_assembly
from episteme_graph.agents.component_assembly.schema import (
    COMPONENTS_VERSION,
    ComponentAssemblyLLMInput,
    ComponentAssemblyResult,
    ComponentRecord,
)


def _component(component_id, *, linked_claim_ids=None, linked_equation_ids=None):
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
        linked_claim_ids=list(linked_claim_ids or []),
        linked_equation_ids=list(linked_equation_ids or []),
    )


def _llm_input(thesis_nodes):
    return ComponentAssemblyLLMInput(
        document_id="doc",
        cartridge_id=None,
        accepted_claims=[],
        equations=[],
        thesis_nodes=thesis_nodes,
        dsl_nodes=[],
        dsl_edges=[],
        allowed_component_types=["RelationComponent"],
        allowed_dependency_types=["requires"],
    )


_THESIS_NODES = [
    {
        "thesis_ref": "central_thesis",
        "text": "central",
        "claim_ids": ["claim_main"],
        "equation_ids": ["eq_final"],
        "kind": "central_thesis",
    },
    {
        "thesis_ref": "support:assumptions:0",
        "text": "assumption",
        "claim_ids": ["claim_assume"],
        "equation_ids": [],
        "kind": "support",
    },
]


def _result(components):
    return ComponentAssemblyResult(
        "doc", COMPONENTS_VERSION, None, components, [], [], 0.8
    )


def test_claim_overlap_links_component_to_thesis_node():
    comp = _component("comp_a", linked_claim_ids=["claim_main"])
    enrich_component_assembly(_result([comp]), _llm_input(_THESIS_NODES))
    assert comp.supports_thesis_node_ids == ["central_thesis"]


def test_equation_overlap_links_component_to_thesis_node():
    comp = _component("comp_b", linked_equation_ids=["eq_final"])
    enrich_component_assembly(_result([comp]), _llm_input(_THESIS_NODES))
    assert comp.supports_thesis_node_ids == ["central_thesis"]


def test_component_can_back_multiple_thesis_nodes():
    comp = _component(
        "comp_c",
        linked_claim_ids=["claim_main", "claim_assume"],
    )
    enrich_component_assembly(_result([comp]), _llm_input(_THESIS_NODES))
    assert comp.supports_thesis_node_ids == [
        "central_thesis",
        "support:assumptions:0",
    ]


def test_no_overlap_leaves_thesis_refs_empty():
    comp = _component("comp_d", linked_claim_ids=["claim_other"])
    enrich_component_assembly(_result([comp]), _llm_input(_THESIS_NODES))
    assert comp.supports_thesis_node_ids == []


def test_no_thesis_nodes_leaves_field_untouched():
    comp = _component("comp_e", linked_claim_ids=["claim_main"])
    enrich_component_assembly(_result([comp]), _llm_input([]))
    assert comp.supports_thesis_node_ids == []


def test_thesis_support_refs_are_deterministic():
    def build():
        comp = _component(
            "comp_f",
            linked_claim_ids=["claim_assume", "claim_main"],
            linked_equation_ids=["eq_final"],
        )
        enrich_component_assembly(_result([comp]), _llm_input(_THESIS_NODES))
        return comp.supports_thesis_node_ids

    first = build()
    second = build()
    assert first == second


def test_enrichment_is_idempotent_no_duplicates():
    comp = _component("comp_g", linked_claim_ids=["claim_main"])
    result = _result([comp])
    llm_input = _llm_input(_THESIS_NODES)
    enrich_component_assembly(result, llm_input)
    enrich_component_assembly(result, llm_input)
    assert comp.supports_thesis_node_ids == ["central_thesis"]


def test_supports_thesis_node_ids_round_trips_via_dict():
    comp = _component("comp_h", linked_claim_ids=["claim_main"])
    result = _result([comp])
    enrich_component_assembly(result, _llm_input(_THESIS_NODES))
    reloaded = ComponentAssemblyResult.from_dict(result.to_dict())
    assert reloaded.components[0].supports_thesis_node_ids == ["central_thesis"]
