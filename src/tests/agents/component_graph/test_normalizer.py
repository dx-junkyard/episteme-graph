from episteme_graph.agents.component_assembly.schema import ComponentAssemblyResult
from episteme_graph.agents.component_graph.normalizer import (
    ComponentGraphNormalizer,
    _claim_is_atomic,
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


def _normalized(claim_index=None):
    return ComponentGraphNormalizer().normalize(
        _empty_graph(), _empty_components(), _derivations(), claim_index=claim_index
    )


def _layer(result, layer):
    return [n for n in result.nodes if n.graph_layer == layer]


# --- layering (acceptance #1, #2, #12) --------------------------------------


def test_main_graph_is_smaller_than_detail_graph():
    result = _normalized()
    main_nodes = _layer(result, "main")
    detail_nodes = _layer(result, "equation_detail")
    debug_nodes = _layer(result, "debug")

    # 6 derivation steps → 5 non-generic steps become main nodes (one per
    # operation family), and every step is preserved in the detail/debug layer.
    assert len(main_nodes) == 5
    assert len(detail_nodes) + len(debug_nodes) == 6
    assert len(main_nodes) < len(detail_nodes) + len(debug_nodes)
    assert all(n.component_type == "TheoryOperationNode" for n in main_nodes)
    assert all(n.component_type == "EquationOperationNode" for n in detail_nodes)


def test_equation_steps_aggregate_into_one_main_node():
    # Two linearization steps in the same chain collapse into a single main node.
    derivations = DerivationChainResult(
        document_id="doc",
        cartridge_id=None,
        chains=[DerivationChainRecord(
            derivation_id="deriv",
            document_id="doc",
            source_section_ids=[],
            steps=[
                _step("define", ["eq_a"], ["eq_b"]),
                _step("linearize_first", ["eq_b"], ["eq_c"]),
                _step("linearize_second", ["eq_c"], ["eq_d"]),
            ],
        )],
    )
    result = ComponentGraphNormalizer().normalize(
        _empty_graph(), _empty_components(), derivations
    )
    main_nodes = _layer(result, "main")
    detail_nodes = _layer(result, "equation_detail")
    assert len(main_nodes) == 2  # defines + linearizes
    assert len(detail_nodes) == 3
    linearizes = next(n for n in main_nodes if n.operation.startswith("linearize"))
    assert len(linearizes.member_component_ids) == 2


def test_steps_from_multiple_derivations_share_a_stage_node():
    # Issue #308: stage aggregation is global — define steps from two different
    # derivations collapse into a single "Theory basis" main node.
    derivations = DerivationChainResult(
        document_id="doc",
        cartridge_id=None,
        chains=[
            DerivationChainRecord(
                derivation_id="deriv_a",
                document_id="doc",
                source_section_ids=[],
                steps=[_step("define_kernel", ["eq_a"], ["eq_b"])],
            ),
            DerivationChainRecord(
                derivation_id="deriv_b",
                document_id="doc",
                source_section_ids=[],
                steps=[_step("define_observable", ["eq_x"], ["eq_y"])],
            ),
        ],
    )
    result = ComponentGraphNormalizer().normalize(
        _empty_graph(), _empty_components(), derivations
    )
    basis_nodes = [n for n in _layer(result, "main") if n.label == "Theory basis"]
    assert len(basis_nodes) == 1
    assert len(basis_nodes[0].member_component_ids) == 2


def test_detail_nodes_point_back_at_main_node():
    result = _normalized()
    main_by_id = {n.component_id: n for n in _layer(result, "main")}
    for detail in _layer(result, "equation_detail"):
        assert detail.parent_component_id in main_by_id
        assert detail.component_id in main_by_id[detail.parent_component_id].member_component_ids


# --- labels (acceptance #3, #4) ---------------------------------------------


def test_main_labels_are_theory_stages_not_equation_ids():
    # Issue #308: main labels are high-level theory stages, never bare generic
    # operations and never equation-id fallbacks ("Define eq_...", etc.).
    result = _normalized()
    labels = {n.label for n in _layer(result, "main")}
    for generic in ("Define", "Transform", "Relate", "Result"):
        assert generic not in labels
    # No main label contains an equation id.
    for label in labels:
        assert "eq_" not in label.lower()
        assert not label.lower().startswith("define eq")
        assert not label.lower().startswith("derive result eq")
    # Labels are the canonical theory-stage backbone.
    assert "Theory basis" in labels  # define step
    assert "Equation system" in labels  # linearize step
    assert "Elimination" in labels  # eliminate step
    assert "Consistency relation" in labels  # derive step
    assert "Diagnostic / application" in labels  # apply_criterion step
    # Labels stay short: the stage name only, never a "Stage: long phrase" form.
    for label in labels:
        assert ":" not in label.replace("Diagnostic / application", "")
        assert len(label) <= 40


def test_main_graph_has_five_to_eight_stage_nodes():
    # Acceptance: the main graph collapses to a handful of theory stages.
    result = _normalized()
    main_nodes = _layer(result, "main")
    assert 1 <= len(main_nodes) <= 8


def test_main_label_stays_short_and_description_holds_claim_text():
    # Issue #308: the atomic-claim phrase enriches the node's *description*, not
    # its label. The label stays a short theory-stage name.
    claim_index = {
        "claim_1": {"text": "second-order moment vanishes", "evidence_text": "p.4", "is_atomic": True},
    }
    result = _normalized(claim_index)
    eliminate = next(n for n in _layer(result, "main") if n.operation.startswith("eliminate"))
    assert eliminate.label == "Elimination"
    assert "second-order moment vanishes" in eliminate.description


# --- generic operations (acceptance #5) -------------------------------------


def test_generic_operations_excluded_from_main_graph():
    result = _normalized()
    main_ops = {n.operation for n in _layer(result, "main")}
    assert "transform" not in main_ops

    # The generic step is kept in the debug layer, flagged as inferred.
    debug = _layer(result, "debug")
    generic = next(n for n in debug if n.operation == "transform")
    assert generic.source_backing_status == "inferred"
    assert "fallback_or_inferred_node" in generic.review_reasons


# --- source backing / review status (acceptance #6, #7, #8, #9) -------------


def test_source_backed_node_is_not_teacher_review_required():
    result = _normalized()
    backed = next(n for n in _layer(result, "main") if n.operation.startswith("eliminate"))
    assert backed.source_backing_status == "source_backed"
    assert backed.review_status == "source_backed"
    assert backed.review_reasons == []
    assert backed.publish_ready is True
    assert backed.linked_claim_ids == ["claim_1"]
    assert backed.linked_evidence_ids == ["ev_1"]


def test_evidence_backed_node_without_atomic_claim_keeps_warning():
    # A step backed by an equation + evidence link but no atomic claim is
    # source_backed (acceptance: backing exists) yet must still warn that no
    # minimal atomic claim supports the node's meaning (issue #306).
    derivations = DerivationChainResult(
        document_id="doc",
        cartridge_id=None,
        chains=[DerivationChainRecord(
            derivation_id="deriv",
            document_id="doc",
            source_section_ids=[],
            steps=[
                _step("derive_result", ["eq_a"], ["eq_b"], source_evidence_ids=["ev_1"]),
            ],
        )],
    )
    result = ComponentGraphNormalizer().normalize(
        _empty_graph(), _empty_components(), derivations
    )
    node = next(n for n in _layer(result, "main"))
    assert node.linked_evidence_ids == ["ev_1"]
    assert node.linked_claim_ids == []
    # Backed by evidence → source_backed, but the atomic-claim gap is retained.
    assert node.source_backing_status == "source_backed"
    assert node.review_status == "source_backed"
    assert node.review_reasons == ["missing_atomic_claim"]


def test_equation_only_node_flags_missing_atomic_claim():
    result = _normalized()
    define = next(n for n in _layer(result, "main") if n.operation == "define")
    # Backed only by equation IDs → partially source-backed, never source_backed.
    assert define.source_backing_status == "partially_source_backed"
    assert "missing_atomic_claim" in define.review_reasons
    assert define.review_status == "teacher_review_required"


def test_review_required_nodes_and_edges_have_reasons():
    result = _normalized()
    for node in result.nodes:
        if node.review_status == "review_required":
            assert node.review_reasons, f"{node.component_id} missing review_reasons"
    for edge in result.edges:
        if edge.review_status == "review_required":
            assert edge.review_reasons, f"{edge.edge_id} missing review_reasons"


def test_no_node_is_uniformly_teacher_review_required():
    result = _normalized()
    statuses = {n.review_status for n in result.nodes}
    # The mapping yields a spread of statuses, not a single default.
    assert "source_backed" in statuses
    assert statuses != {"teacher_review_required"}


# --- edges (acceptance #2, #5) ----------------------------------------------


def test_main_edges_carry_operation_edge_types():
    result = _normalized()
    main_ids = {n.component_id for n in _layer(result, "main")}
    main_edges = [e for e in result.edges if e.source in main_ids and e.target in main_ids]
    edge_types = {e.edge_type for e in main_edges}
    assert {"linearizes", "eliminates", "derives", "diagnoses"} <= edge_types
    assert "solves" not in edge_types
    for edge in main_edges:
        assert edge.evidence_equation_ids
        assert edge.evidence_derivation_ids


def test_generic_step_edge_in_detail_is_review_required():
    result = _normalized()
    debug_ids = {n.component_id for n in _layer(result, "debug")}
    into_generic = [e for e in result.edges if e.target in debug_ids]
    assert into_generic
    assert all(e.review_status == "review_required" for e in into_generic)
    assert all(e.review_reasons for e in into_generic)


# --- atomic claim selection (acceptance #10, #11) ---------------------------


def test_non_atomic_claim_is_not_strong_backing():
    # claim_1 is a long, paper-level claim with empty evidence → not strong.
    claim_index = {
        "claim_1": {
            "text": "x" * 400,
            "evidence_text": "",
            "claim_level": "paper",
            "is_atomic": False,
        },
    }
    result = _normalized(claim_index)
    eliminate = next(n for n in _layer(result, "main") if n.operation.startswith("eliminate"))
    # It still has evidence (ev_1) so it remains source-backed via evidence, but
    # the claim itself must not be treated as the atomic backing.
    assert "claim_1" in eliminate.linked_claim_ids


def test_empty_evidence_claim_does_not_make_node_source_backed():
    derivations = DerivationChainResult(
        document_id="doc",
        cartridge_id=None,
        chains=[DerivationChainRecord(
            derivation_id="deriv",
            document_id="doc",
            source_section_ids=[],
            steps=[
                _step("derive_result", ["eq_a"], ["eq_b"], required_claim_ids=["claim_x"]),
            ],
        )],
    )
    claim_index = {
        "claim_x": {"text": "x" * 400, "evidence_text": "", "claim_level": "paper"},
    }
    result = ComponentGraphNormalizer().normalize(
        _empty_graph(), _empty_components(), derivations, claim_index=claim_index
    )
    node = next(n for n in _layer(result, "main"))
    # No evidence and only a non-atomic claim → not source_backed.
    assert node.source_backing_status != "source_backed"
    assert "missing_atomic_claim" in node.review_reasons


def test_claim_is_atomic_heuristic():
    assert _claim_is_atomic({"text": "short atomic claim", "evidence_text": "p1"})
    assert not _claim_is_atomic({"text": "x" * 400})
    assert not _claim_is_atomic({"text": "ok", "claim_level": "paper"})
    assert not _claim_is_atomic({"text": "ok", "is_atomic": False})


# --- issue #304 regression --------------------------------------------------


def test_edge_backing_treats_derivation_evidence_as_source_backed():
    status, review_status, reasons = _edge_backing(
        evidence_equation_ids=[],
        is_generic=False,
        evidence_claims=[],
        evidence_derivation_ids=["step_1", "step_2"],
    )
    assert status == "source_backed"
    assert review_status == "source_backed"
    assert reasons == []

    status, review_status, reasons = _edge_backing(
        evidence_equation_ids=[],
        is_generic=False,
        evidence_claims=[],
        evidence_derivation_ids=[],
    )
    assert status == "review_required"
    assert review_status == "review_required"
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
    derivations = _short_derivations([
        _step("define", ["eq_a"], ["eq_b"]),
        _step("linearize_moment_equations", ["eq_b"], ["eq_c"]),
    ])
    result = ComponentGraphNormalizer().normalize(
        _empty_graph(), _empty_components(), derivations
    )
    main_nodes = _layer(result, "main")
    assert len(main_nodes) == 2
    edge_types = {edge.edge_type for edge in result.edges}
    assert "linearizes" in edge_types


def test_normalizer_reflects_single_step_derivation_chain():
    derivations = _short_derivations([
        _step("derive_consistency_relation", ["eq_a"], ["eq_b"]),
    ])
    result = ComponentGraphNormalizer().normalize(
        _empty_graph(), _empty_components(), derivations
    )
    main_nodes = _layer(result, "main")
    assert len(main_nodes) == 1
    assert main_nodes[0].operation == "derive_consistency_relation"
