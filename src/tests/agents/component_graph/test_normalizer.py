from episteme_graph.agents.component_assembly.schema import ComponentAssemblyResult
from episteme_graph.agents.component_graph.normalizer import (
    ComponentGraphNormalizer,
    _edge_backing,
)
from episteme_graph.agents.component_graph.schema import GRAPH_SCHEMA_VERSION, ComponentGraphResult
from episteme_graph.agents.derivation_chain.schema import (
    DerivationChainRecord,
    DerivationChainResult,
    DerivationStep,
)


def _step(operation, inputs, outputs, **kwargs):
    return DerivationStep(
        step_id=f"step_{operation}",
        input_equation_ids=inputs,
        operation=operation,
        output_equation_ids=outputs,
        review_status="teacher_review_required",
        **kwargs,
    )


def _derivations():
    # Domain-neutral chain: a definition feeds a linearization, which feeds an
    # elimination, a derivation, then a diagnostic, ending in a generic step.
    steps = [
        _step("define", ["eq_a"], ["eq_b"]),
        _step("linearize_moment_equations", ["eq_b"], ["eq_c"]),
        _step(
            "eliminate_second_order_parameter",
            ["eq_c"],
            ["eq_d"],
            required_claim_ids=["claim_1"],
            source_evidence_ids=["ev_1"],
            eliminated_symbols=["b2"],
        ),
        _step("derive_consistency_relation", ["eq_d"], ["eq_e"]),
        _step("apply_criterion", ["eq_e"], ["eq_f"]),
        _step("transform", ["eq_f"], ["eq_g"]),
    ]
    return DerivationChainResult(
        document_id="doc",
        cartridge_id=None,
        chains=[
            DerivationChainRecord(
                derivation_id="deriv",
                document_id="doc",
                source_section_ids=[],
                steps=steps,
            )
        ],
    )


def _empty_components():
    return ComponentAssemblyResult(
        document_id="doc",
        components_version="v1",
        cartridge_id=None,
        components=[],
        assembly_hints=[],
        review_notes=[],
        confidence=0.8,
    )


def _empty_graph():
    return ComponentGraphResult(
        document_id="doc",
        graph_schema_version=GRAPH_SCHEMA_VERSION,
        cartridge_id=None,
        nodes=[],
        edges=[],
        review_notes=[],
        confidence=0.0,
    )


def _normalized():
    return ComponentGraphNormalizer().normalize(_empty_graph(), _empty_components(), _derivations())


def test_normalizer_builds_domain_independent_theory_graph():
    result = _normalized()
    main_nodes = [n for n in result.nodes if n.graph_layer == "main"]

    # One theory-operation node per derivation step, all typed and source-linked.
    assert len(main_nodes) == 6
    assert all(n.component_type == "TheoryOperationNode" for n in main_nodes)
    assert all(n.operation for n in main_nodes)

    # No bare generic labels in the main graph (acceptance #9).
    labels = {n.label for n in main_nodes}
    assert "Define" not in labels
    assert "Transform" not in labels
    assert "Relate" not in labels
    assert "Result" not in labels

    # Labels are derived deterministically from operation + equations.
    assert "Define eq_b" in labels
    assert any(l.startswith("Eliminate second order parameter") for l in labels)


def test_normalizer_maps_operations_to_edge_types():
    result = _normalized()
    edge_types = {edge.edge_type for edge in result.edges}
    # define -> defines isn't produced as an edge unless its output is consumed;
    # the consuming operations drive the edge type (acceptance #5).
    assert "solves" not in edge_types  # no solve_* step in this chain
    assert {"linearizes", "eliminates", "derives", "diagnoses"} <= edge_types

    # Every backed edge carries derivation + equation evidence (acceptance #2).
    backed = [e for e in result.edges if e.review_status == "source_backed"]
    assert backed
    for edge in backed:
        assert edge.evidence_equation_ids
        assert edge.evidence_derivation_ids


def test_normalizer_flags_generic_operations_for_review():
    result = _normalized()
    generic = next(n for n in result.nodes if n.operation == "transform")
    assert generic.source_backing_status == "inferred"
    assert "fallback_or_inferred_node" in generic.review_reasons

    # The edge into the generic node is review_required with explicit reasons.
    generic_edges = [e for e in result.edges if e.target == generic.component_id]
    assert generic_edges
    assert all(e.review_status == "review_required" for e in generic_edges)
    assert all(e.review_reasons for e in generic_edges)


def test_normalizer_promotes_fully_backed_nodes():
    result = _normalized()
    # The eliminate step carries a claim + evidence link → source_backed.
    backed = next(n for n in result.nodes if n.operation == "eliminate_second_order_parameter")
    assert backed.source_backing_status == "source_backed"
    assert backed.review_reasons == []
    assert backed.linked_claim_ids == ["claim_1"]
    assert backed.linked_evidence_ids == ["ev_1"]
    assert backed.linked_equation_ids
    assert backed.linked_derivation_ids
    assert backed.eliminated_symbols == ["b2"]


def test_normalizer_review_required_nodes_have_reasons():
    result = _normalized()
    for node in result.nodes:
        if node.source_backing_status == "review_required":
            assert node.review_reasons, f"{node.component_id} missing review_reasons"


def test_normalizer_diagnostic_backed_by_upstream_results():
    result = _normalized()
    diagnostic = next(n for n in result.nodes if n.operation == "apply_criterion")
    incoming = {e.source for e in result.edges if e.target == diagnostic.component_id}
    # The diagnostic node is fed by the upstream derivation node (acceptance #7).
    derive_node = next(n for n in result.nodes if n.operation == "derive_consistency_relation")
    assert derive_node.component_id in incoming


# --- issue #304 regression tests --------------------------------------------


def test_edge_backing_treats_derivation_evidence_as_source_backed():
    """An edge with only evidence_derivation_ids is source-backed (issue #304)."""
    status, reasons = _edge_backing(
        evidence_equation_ids=[],
        is_generic=False,
        evidence_claims=[],
        evidence_derivation_ids=["step_1", "step_2"],
    )
    assert status == "source_backed"
    assert reasons == []

    # No backing at all is still review_required with a reason.
    status, reasons = _edge_backing(
        evidence_equation_ids=[],
        is_generic=False,
        evidence_claims=[],
        evidence_derivation_ids=[],
    )
    assert status == "review_required"
    assert reasons


def _short_derivations(steps):
    return DerivationChainResult(
        document_id="doc",
        cartridge_id=None,
        chains=[
            DerivationChainRecord(
                derivation_id="deriv",
                document_id="doc",
                source_section_ids=[],
                steps=steps,
            )
        ],
    )


def test_normalizer_reflects_two_step_derivation_chain():
    """A 1–2 step chain still produces operation-derived nodes/edges (issue #304)."""
    derivations = _short_derivations([
        _step("define", ["eq_a"], ["eq_b"]),
        _step("linearize_moment_equations", ["eq_b"], ["eq_c"]),
    ])
    result = ComponentGraphNormalizer().normalize(
        _empty_graph(), _empty_components(), derivations
    )
    main_nodes = [n for n in result.nodes if n.graph_layer == "main"]
    # Both derivation steps surface as main theory-operation nodes.
    assert len(main_nodes) == 2
    assert all(n.component_type == "TheoryOperationNode" for n in main_nodes)

    # The operation-derived edge type reaches the graph instead of being
    # dropped back to the component fallback view.
    edge_types = {edge.edge_type for edge in result.edges}
    assert "linearizes" in edge_types


def test_normalizer_reflects_single_step_derivation_chain():
    derivations = _short_derivations([
        _step("derive_consistency_relation", ["eq_a"], ["eq_b"]),
    ])
    result = ComponentGraphNormalizer().normalize(
        _empty_graph(), _empty_components(), derivations
    )
    main_nodes = [n for n in result.nodes if n.graph_layer == "main"]
    assert len(main_nodes) == 1
    assert main_nodes[0].operation == "derive_consistency_relation"
