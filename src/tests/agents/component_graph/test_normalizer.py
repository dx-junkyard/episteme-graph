from episteme_graph.agents.component_assembly.schema import ComponentAssemblyResult, ComponentRecord
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
        # Generic but equation-backed on both ends → kept in equation_detail
        # as partially_source_backed (issue #361).
        _step("transform", ["eq_f"], ["eq_g"]),
        # Generic without output equations → debug layer.
        _step("relate", ["eq_g"], []),
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


def _components(*records):
    return ComponentAssemblyResult(
        document_id="doc",
        components_version="v1",
        cartridge_id=None,
        components=list(records),
        assembly_hints=[],
        review_notes=[],
        confidence=0.8,
    )


def _component(component_id="comp_eliminate", **kwargs):
    defaults = dict(
        component_id=component_id,
        component_type="RelationComponent",
        label="Eliminate bias parameters",
        summary="Eliminates nuisance bias parameters.",
        inputs=[{"name": "equation system"}],
        outputs=[{"name": "bias-free relation"}],
        preconditions=[],
        cautions=[],
        dependencies=[],
        evidence_refs={"equation_ids": ["eq_c", "eq_d"]},
        reason="component",
        confidence=0.8,
        review_notes=[],
        linked_equation_ids=["eq_c", "eq_d"],
        linked_derivation_ids=["deriv"],
        operation="eliminate",
    )
    defaults.update(kwargs)
    return ComponentRecord(**defaults)


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

    # 7 derivation steps → 5 non-generic steps become main nodes (one per
    # operation family), and every step is preserved in the detail/debug layer.
    assert len(main_nodes) == 5
    assert len(detail_nodes) + len(debug_nodes) == 7
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


def test_detail_nodes_are_listed_in_main_member_ids():
    # Issue #422: parent_component_id now holds a canonical component_assembly
    # ID (not a theory_op graph-node ID). The detail→main relationship is
    # maintained exclusively via member_component_ids on main nodes.
    result = _normalized()
    claimed_by_main: set[str] = set()
    for n in _layer(result, "main"):
        claimed_by_main.update(n.member_component_ids or [])
    for detail in _layer(result, "equation_detail"):
        assert detail.component_id in claimed_by_main, (
            f"detail node {detail.component_id!r} not found in any main node's member_component_ids"
        )


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


def test_main_node_separates_stage_object_and_component_links():
    result = ComponentGraphNormalizer().normalize(
        _empty_graph(),
        _components(_component()),
        _derivations(),
        claim_index={
            "claim_1": {
                "text": "bias parameters are eliminated",
                "evidence_text": "p.4",
                "is_atomic": True,
            },
        },
    )
    eliminate = next(n for n in _layer(result, "main") if n.label == "Elimination")

    assert eliminate.label == "Elimination"
    assert eliminate.theory_object == "bias parameters are eliminated"
    assert eliminate.display_label == "Elimination: bias parameters are eliminated"
    assert eliminate.representative_component_id == "comp_eliminate"
    assert eliminate.linked_component_ids == ["comp_eliminate"]
    assert eliminate.detail_node_ids
    assert eliminate.detail_node_ids == eliminate.member_component_ids
    assert "deriv" in eliminate.supporting_derivation_ids


# --- concept rollup (issue #8) ----------------------------------------------


def test_main_node_rolls_up_component_concepts():
    # The eliminate component links derivation "deriv" (eq_c/eq_d), which lands in
    # the Elimination main node; its concepts/prerequisite_concepts roll up.
    comp = _component(
        concepts=["Bias parameter", "Consistency relation"],
        prerequisite_concepts=["Galaxy bias"],
    )
    result = ComponentGraphNormalizer().normalize(
        _empty_graph(), _components(comp), _derivations()
    )
    eliminate = next(n for n in _layer(result, "main") if n.label == "Elimination")
    assert "Bias parameter" in eliminate.concepts
    assert "Consistency relation" in eliminate.concepts
    assert "Galaxy bias" in eliminate.prerequisite_concepts


def test_graph_builds_when_components_have_no_concepts():
    # Components without concept tags must not break graph generation; concept
    # fields default to empty lists on every node.
    comp = _component()  # no concepts / prerequisite_concepts
    result = ComponentGraphNormalizer().normalize(
        _empty_graph(), _components(comp), _derivations()
    )
    assert result.nodes
    for node in result.nodes:
        assert isinstance(node.concepts, list)
        assert isinstance(node.prerequisite_concepts, list)
    eliminate = next(n for n in _layer(result, "main") if n.label == "Elimination")
    assert eliminate.concepts == []


# --- generic operations (acceptance #5) -------------------------------------


def test_generic_operations_excluded_from_main_graph():
    result = _normalized()
    main_ops = {n.operation for n in _layer(result, "main")}
    assert "transform" not in main_ops
    assert "relate" not in main_ops

    # Issue #361: a generic step with input AND output equations stays in the
    # equation_detail layer as partially_source_backed (never stronger).
    detail = _layer(result, "equation_detail")
    kept = next(n for n in detail if n.operation == "transform")
    assert kept.source_backing_status == "partially_source_backed"
    assert "generic_operation" in kept.review_reasons
    assert kept.review_status == "teacher_review_required"

    # A generic step without equation backing still lands in debug as inferred.
    debug = _layer(result, "debug")
    generic = next(n for n in debug if n.operation == "relate")
    assert generic.source_backing_status == "inferred"
    assert "fallback_or_inferred_node" in generic.review_reasons


def test_kept_generic_detail_node_has_parent_main_node():
    # Issue #361: the kept-generic step is attached to the nearest preceding
    # non-generic step's main node so the layer linkage invariant holds, but
    # it must not strengthen that main node's backing.
    # Issue #422: parent_component_id now holds a canonical component ID, so
    # the detail→main relationship is checked via member_component_ids.
    result = _normalized()
    detail = _layer(result, "equation_detail")
    kept = next(n for n in detail if n.operation == "transform")
    main_by_id = {n.component_id: n for n in _layer(result, "main")}
    # Find the main node that claims this kept-generic detail node.
    parent = next(
        (n for n in main_by_id.values() if kept.component_id in (n.member_component_ids or [])),
        None,
    )
    assert parent is not None, f"{kept.component_id!r} not in any main node's member_component_ids"
    # eq_g (only produced by the generic step) never backs the main node.
    assert "eq_g" not in parent.linked_equation_ids


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
    generic_ids = {
        n.component_id
        for n in result.nodes
        if n.operation in ("transform", "relate")
    }
    into_generic = [e for e in result.edges if e.target in generic_ids]
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


# --- claim-only derivation chains (issue #334) --------------------------------


def _claim_step(operation, input_claims, output_claims, **kwargs):
    """DerivationStep with claim flow but no equation I/O."""
    return DerivationStep(
        step_id=f"step_{operation}",
        input_equation_ids=[],
        output_equation_ids=[],
        operation=operation,
        input_claim_ids=input_claims,
        output_claim_ids=output_claims,
        review_status="teacher_review_required",
        **kwargs,
    )


def _claim_only_derivations():
    return DerivationChainResult(
        document_id="doc",
        cartridge_id=None,
        chains=[DerivationChainRecord(
            derivation_id="deriv_claim",
            document_id="doc",
            source_section_ids=[],
            steps=[
                _claim_step("define_framework", [], ["claim_a"],
                            required_claim_ids=[]),
                _claim_step("linearize_model", ["claim_a"], ["claim_b"],
                            required_claim_ids=["claim_a"]),
                _claim_step("derive_result", ["claim_b"], ["claim_c"],
                            required_claim_ids=["claim_b"],
                            source_evidence_ids=["ev_1"]),
            ],
        )],
    )


def test_claim_only_chain_produces_main_edges():
    result = ComponentGraphNormalizer().normalize(
        _empty_graph(), _empty_components(), _claim_only_derivations()
    )
    main_nodes = _layer(result, "main")
    main_ids = {n.component_id for n in main_nodes}
    main_edges = [e for e in result.edges if e.source in main_ids and e.target in main_ids]
    assert len(main_nodes) >= 2
    assert len(main_edges) >= 1, "claim-only chain must produce at least one main edge"


def test_claim_only_chain_produces_detail_edges():
    result = ComponentGraphNormalizer().normalize(
        _empty_graph(), _empty_components(), _claim_only_derivations()
    )
    detail_ids = {n.component_id for n in _layer(result, "equation_detail")}
    detail_edges = [e for e in result.edges if e.source in detail_ids and e.target in detail_ids]
    assert len(detail_edges) >= 1, "claim-only chain must produce detail edges"


def test_claim_only_chain_edges_carry_evidence():
    result = ComponentGraphNormalizer().normalize(
        _empty_graph(), _empty_components(), _claim_only_derivations()
    )
    main_ids = {n.component_id for n in _layer(result, "main")}
    main_edges = [e for e in result.edges if e.source in main_ids and e.target in main_ids]
    for edge in main_edges:
        assert edge.evidence_derivation_ids, f"{edge.edge_id} missing derivation evidence"
        assert edge.evidence_claim_ids, f"{edge.edge_id} missing claim evidence"


def test_claim_only_chain_nodes_have_linked_claims():
    result = ComponentGraphNormalizer().normalize(
        _empty_graph(), _empty_components(), _claim_only_derivations()
    )
    for node in _layer(result, "main"):
        assert node.linked_claim_ids, f"{node.component_id} missing linked_claim_ids"
        assert node.linked_derivation_ids, f"{node.component_id} missing linked_derivation_ids"


def test_claim_only_chain_node_not_review_required():
    result = ComponentGraphNormalizer().normalize(
        _empty_graph(), _empty_components(), _claim_only_derivations()
    )
    for node in _layer(result, "main"):
        assert node.source_backing_status != "review_required", (
            f"claim-only node {node.component_id} should be partially_source_backed "
            f"(has derivation_ids), not review_required"
        )


def test_fallback_sequential_edges_when_no_overlap():
    steps = [
        _claim_step("define_framework", [], ["claim_x"]),
        _claim_step("derive_result", ["claim_y"], ["claim_z"]),
    ]
    derivations = _short_derivations(steps)
    result = ComponentGraphNormalizer().normalize(
        _empty_graph(), _empty_components(), derivations
    )
    main_ids = {n.component_id for n in _layer(result, "main")}
    main_edges = [e for e in result.edges if e.source in main_ids and e.target in main_ids]
    assert len(main_edges) >= 1, "fallback sequential edges should connect main nodes"
    for edge in main_edges:
        assert edge.review_status == "review_required"
        assert edge.review_reasons


def test_mixed_equation_and_claim_chain_prefers_equations():
    steps = [
        _step("define", ["eq_a"], ["eq_b"]),
        DerivationStep(
            step_id="step_derive",
            input_equation_ids=["eq_b"],
            output_equation_ids=["eq_c"],
            operation="derive_result",
            input_claim_ids=["claim_a"],
            output_claim_ids=["claim_b"],
            review_status="teacher_review_required",
        ),
    ]
    derivations = _short_derivations(steps)
    result = ComponentGraphNormalizer().normalize(
        _empty_graph(), _empty_components(), derivations
    )
    main_ids = {n.component_id for n in _layer(result, "main")}
    main_edges = [e for e in result.edges if e.source in main_ids and e.target in main_ids]
    assert len(main_edges) >= 1
    for edge in main_edges:
        assert edge.evidence_equation_ids, "equation evidence should be preferred when available"


# --- detail node labels never use step.reason (issue #337) --------------------


def test_detail_node_label_never_uses_reason():
    """Issue #337: detail node labels must never be step.reason text."""
    reason_text = (
        "The span is content-bearing and reviewable, but it is not a minimal "
        "reusable claim for the theory operation graph"
    )
    steps = [
        DerivationStep(
            step_id="step_with_reason",
            input_equation_ids=[],
            output_equation_ids=[],
            operation="apply_definition",
            input_claim_ids=["claim_a"],
            output_claim_ids=["claim_b"],
            reason=reason_text,
            review_status="teacher_review_required",
        ),
    ]
    derivations = _short_derivations(steps)
    result = ComponentGraphNormalizer().normalize(
        _empty_graph(), _empty_components(), derivations
    )
    detail_nodes = _layer(result, "equation_detail")
    assert len(detail_nodes) >= 1
    for node in detail_nodes:
        assert reason_text[:40] not in node.label, (
            f"detail node label must not contain reason text: {node.label}"
        )
        assert reason_text[:40] not in node.display_label, (
            f"detail node display_label must not contain reason text: {node.display_label}"
        )


def test_detail_node_reason_goes_to_review_reason():
    """Issue #337: step.reason is stored in review_reason, not label/description."""
    reason_text = "Applies the EPR definition of physical reality"
    steps = [
        DerivationStep(
            step_id="step_apply",
            input_equation_ids=[],
            output_equation_ids=[],
            operation="apply_definition",
            input_claim_ids=["claim_a"],
            output_claim_ids=["claim_b"],
            reason=reason_text,
            review_status="teacher_review_required",
        ),
    ]
    derivations = _short_derivations(steps)
    result = ComponentGraphNormalizer().normalize(
        _empty_graph(), _empty_components(), derivations
    )
    detail_nodes = _layer(result, "equation_detail")
    assert len(detail_nodes) >= 1
    node = detail_nodes[0]
    assert node.review_reason == reason_text
    assert node.description == ""
    assert reason_text not in node.theory_object


def test_claim_only_detail_label_uses_verb():
    """Issue #337: claim-only steps use the operation verb as label."""
    steps = [
        _claim_step("infer_conclusion", ["claim_a"], ["claim_b"]),
    ]
    derivations = _short_derivations(steps)
    result = ComponentGraphNormalizer().normalize(
        _empty_graph(), _empty_components(), derivations
    )
    detail_nodes = _layer(result, "equation_detail")
    assert len(detail_nodes) >= 1
    node = detail_nodes[0]
    assert node.label == "Infer conclusion"
    assert node.display_label == "Infer conclusion"


def test_equation_detail_label_still_uses_equation_id():
    """Issue #337: equation steps still use equation IDs in detail labels."""
    result = _normalized()
    detail_nodes = _layer(result, "equation_detail")
    eq_nodes = [n for n in detail_nodes if n.input_equation_ids or n.output_equation_ids]
    assert len(eq_nodes) >= 1
    for node in eq_nodes:
        has_eq_ref = any(
            eq_id in node.label for eq_id in node.input_equation_ids + node.output_equation_ids
        )
        assert has_eq_ref, (
            f"equation detail node should reference equation IDs in label: {node.label}"
        )


def test_detail_node_empty_reason_gives_empty_review_reason():
    """Issue #337 edge case: empty reason produces empty review_reason."""
    steps = [
        DerivationStep(
            step_id="step_no_reason",
            input_equation_ids=[],
            output_equation_ids=[],
            operation="define_framework",
            input_claim_ids=[],
            output_claim_ids=["claim_x"],
            reason="",
            review_status="teacher_review_required",
        ),
    ]
    derivations = _short_derivations(steps)
    result = ComponentGraphNormalizer().normalize(
        _empty_graph(), _empty_components(), derivations
    )
    detail_nodes = _layer(result, "equation_detail")
    assert len(detail_nodes) >= 1
    assert detail_nodes[0].review_reason == ""
    assert detail_nodes[0].description == ""
    assert detail_nodes[0].label == "Define framework"


def test_detail_node_theory_object_is_verb_not_reason():
    """Issue #337: theory_object should be the verb, not step.reason."""
    reason = "Long extraction reason that should not appear in theory_object"
    steps = [
        _claim_step("flag_limitation", ["claim_a"], ["claim_b"],
                     reason=reason),
    ]
    derivations = _short_derivations(steps)
    result = ComponentGraphNormalizer().normalize(
        _empty_graph(), _empty_components(), derivations
    )
    detail_nodes = _layer(result, "equation_detail")
    assert len(detail_nodes) >= 1
    node = detail_nodes[0]
    assert node.theory_object == "Flag limitation"
    assert reason not in node.theory_object


def test_detail_node_has_visual_label():
    """Issue #337: detail nodes carry a visual_label with verb and I/O flow."""
    steps = [
        _claim_step("infer_conclusion", ["claim_a"], ["claim_b"]),
    ]
    derivations = _short_derivations(steps)
    result = ComponentGraphNormalizer().normalize(
        _empty_graph(), _empty_components(), derivations
    )
    detail_nodes = _layer(result, "equation_detail")
    assert len(detail_nodes) >= 1
    vl = detail_nodes[0].visual_label
    assert vl.startswith("Infer conclusion"), f"visual_label should start with verb: {vl}"
    assert "claim_a" in vl, f"visual_label should show input claim: {vl}"


def test_main_node_visual_label_is_stage_name():
    """Issue #337: main nodes carry a short visual_label (theory stage name)."""
    result = _normalized()
    main_nodes = _layer(result, "main")
    assert len(main_nodes) >= 1
    for node in main_nodes:
        assert node.visual_label, f"{node.component_id} missing visual_label"
        assert len(node.visual_label) <= 30, (
            f"visual_label too long: {node.visual_label}"
        )


def test_detail_node_has_claim_io():
    """Issue #337: detail nodes store input/output claim IDs separately."""
    steps = [
        _claim_step("apply_definition", ["claim_a"], ["claim_b"],
                     required_claim_ids=["claim_pre"]),
    ]
    derivations = _short_derivations(steps)
    result = ComponentGraphNormalizer().normalize(
        _empty_graph(), _empty_components(), derivations
    )
    detail_nodes = _layer(result, "equation_detail")
    assert len(detail_nodes) >= 1
    node = detail_nodes[0]
    assert "claim_a" in node.input_claim_ids
    assert "claim_pre" in node.required_claim_ids
    assert "claim_b" in node.output_claim_ids


def test_main_node_has_aggregated_claim_io():
    """Issue #337: main nodes aggregate claim I/O from group records."""
    result = ComponentGraphNormalizer().normalize(
        _empty_graph(), _empty_components(), _claim_only_derivations()
    )
    main_nodes = _layer(result, "main")
    assert len(main_nodes) >= 1
    has_output = any(node.output_claim_ids for node in main_nodes)
    assert has_output, "at least one main node should have output_claim_ids"


def test_main_node_description_excludes_extraction_reason():
    """Issue #337: main node description never contains step extraction reasons."""
    reason = "The span is content-bearing and reviewable, but it is not minimal"
    steps = [
        DerivationStep(
            step_id="step_def",
            input_equation_ids=["eq_a"],
            output_equation_ids=["eq_b"],
            operation="define",
            reason=reason,
            review_status="teacher_review_required",
        ),
    ]
    derivations = _short_derivations(steps)
    result = ComponentGraphNormalizer().normalize(
        _empty_graph(), _empty_components(), derivations
    )
    main_nodes = _layer(result, "main")
    assert len(main_nodes) >= 1
    for node in main_nodes:
        assert reason not in (node.description or ""), (
            f"main description should not contain extraction reason"
        )
        assert reason not in (node.display_label or ""), (
            f"main display_label should not contain extraction reason"
        )


# --- required_claim_ids separation (issue #337 round 3) ----------------------


def test_detail_node_separates_required_claim_ids():
    """Issue #337: required_claim_ids are stored separately from input_claim_ids."""
    steps = [
        _claim_step("apply_definition", ["claim_in"], ["claim_out"],
                     required_claim_ids=["claim_req"]),
    ]
    derivations = _short_derivations(steps)
    result = ComponentGraphNormalizer().normalize(
        _empty_graph(), _empty_components(), derivations
    )
    detail_nodes = _layer(result, "equation_detail")
    assert len(detail_nodes) >= 1
    node = detail_nodes[0]
    assert "claim_in" in node.input_claim_ids
    assert "claim_req" not in node.input_claim_ids, (
        "required_claim_ids should not be merged into input_claim_ids"
    )
    assert "claim_req" in node.required_claim_ids
    assert "claim_out" in node.output_claim_ids


def test_main_node_aggregates_required_claim_ids():
    """Issue #337: main nodes aggregate required_claim_ids from group records."""
    steps = [
        _claim_step("define_framework", [], ["claim_a"],
                     required_claim_ids=["claim_pre"]),
        _claim_step("derive_result", ["claim_a"], ["claim_b"]),
    ]
    derivations = _short_derivations(steps)
    result = ComponentGraphNormalizer().normalize(
        _empty_graph(), _empty_components(), derivations
    )
    main_nodes = _layer(result, "main")
    has_required = any(node.required_claim_ids for node in main_nodes)
    assert has_required, "at least one main node should have required_claim_ids"


# --- visual_label never contains reason (regression, issue #337 round 3) ------


def test_visual_label_never_contains_reason():
    """Regression: visual_label must not contain step.reason text."""
    reason = "Long extraction reason that must not leak into visual_label"
    steps = [
        DerivationStep(
            step_id="step_def",
            input_equation_ids=["eq_a"],
            output_equation_ids=["eq_b"],
            operation="define",
            reason=reason,
            review_status="teacher_review_required",
        ),
    ]
    derivations = _short_derivations(steps)
    result = ComponentGraphNormalizer().normalize(
        _empty_graph(), _empty_components(), derivations
    )
    for node in result.nodes:
        assert reason not in (node.visual_label or ""), (
            f"visual_label should never contain extraction reason: {node.visual_label}"
        )


# --- detail visual_label shows I/O flow (issue #337 round 3) -----------------


def test_detail_visual_label_shows_equation_flow():
    """Issue #337: detail visual_label includes equation I/O flow."""
    steps = [
        _step("define", ["eq_a"], ["eq_b"]),
    ]
    derivations = _short_derivations(steps)
    result = ComponentGraphNormalizer().normalize(
        _empty_graph(), _empty_components(), derivations
    )
    detail_nodes = _layer(result, "equation_detail")
    assert len(detail_nodes) >= 1
    vl = detail_nodes[0].visual_label
    assert "eq_a" in vl, f"visual_label should reference input equation: {vl}"
    assert "eq_b" in vl, f"visual_label should reference output equation: {vl}"
    assert "→" in vl, f"visual_label should contain arrow for flow: {vl}"


def test_detail_visual_label_shows_claim_flow():
    """Issue #337: claim-only detail visual_label includes claim I/O flow."""
    steps = [
        _claim_step("infer_conclusion", ["claim_x"], ["claim_y"]),
    ]
    derivations = _short_derivations(steps)
    result = ComponentGraphNormalizer().normalize(
        _empty_graph(), _empty_components(), derivations
    )
    detail_nodes = _layer(result, "equation_detail")
    assert len(detail_nodes) >= 1
    vl = detail_nodes[0].visual_label
    assert "claim_x" in vl, f"visual_label should reference input claim: {vl}"
    assert "claim_y" in vl, f"visual_label should reference output claim: {vl}"


# --- to_graph_payload includes new fields (issue #337 round 3) ----------------


def test_graph_payload_includes_required_claim_ids():
    """Issue #337: to_graph_payload() includes required_claim_ids for each node."""
    steps = [
        _claim_step("apply_definition", ["claim_in"], ["claim_out"],
                     required_claim_ids=["claim_req"]),
    ]
    derivations = _short_derivations(steps)
    result = ComponentGraphNormalizer().normalize(
        _empty_graph(), _empty_components(), derivations
    )
    payload = result.to_graph_payload()
    for node_dict in payload["nodes"]:
        assert "required_claim_ids" in node_dict, (
            f"payload node missing required_claim_ids: {node_dict['component_id']}"
        )
    detail_nodes = [n for n in payload["nodes"] if n.get("graph_layer") == "equation_detail"]
    assert len(detail_nodes) >= 1
    node_with_req = [n for n in detail_nodes if "claim_req" in n.get("required_claim_ids", [])]
    assert node_with_req, "payload should contain detail node with claim_req in required_claim_ids"


def test_graph_payload_includes_visual_label_and_review_reason():
    """Issue #337: to_graph_payload() includes visual_label and review_reason."""
    reason = "Some extraction note"
    steps = [
        DerivationStep(
            step_id="step_def",
            input_equation_ids=["eq_a"],
            output_equation_ids=["eq_b"],
            operation="define",
            reason=reason,
            review_status="teacher_review_required",
        ),
    ]
    derivations = _short_derivations(steps)
    result = ComponentGraphNormalizer().normalize(
        _empty_graph(), _empty_components(), derivations
    )
    payload = result.to_graph_payload()
    for node_dict in payload["nodes"]:
        assert "visual_label" in node_dict
        assert "review_reason" in node_dict
    detail_nodes = [n for n in payload["nodes"] if n.get("graph_layer") == "equation_detail"]
    assert any(n["review_reason"] == reason for n in detail_nodes)


def _chain_of(*operations):
    """1 derivation = 与えた operation 列（各 step は入出力の式を1本ずつ持つ）。"""
    steps = [
        _step(operation, [f"eq_{i}"], [f"eq_{i + 1}"])
        for i, operation in enumerate(operations)
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


def test_apply_equation_lands_on_a_canonical_stage_not_a_pseudo_stage():
    """回帰: 非 generic な ``apply_equation`` が main で "Transforms" になっていた。

    ``_group_records`` は stage の引けない edge_type を「edge_type 自身」を擬似 stage に
    フォールバックさせるため、``transforms`` が stage 写像から漏れていた間、main node の
    ラベルが ``THEORY_STAGE_LABELS`` の外（"Transforms"）になり #308 の
    「main ラベルは正準 stage ラベルそのもの」に違反していた。
    """
    from episteme_graph.agents.component_graph.schema import THEORY_STAGE_LABELS

    canonical = set(THEORY_STAGE_LABELS.values())
    for operation in ("apply_equation", "apply_measurement_or_update"):
        result = ComponentGraphNormalizer().normalize(
            _empty_graph(), _empty_components(), _chain_of("define", operation)
        )
        main_labels = {n.label for n in _layer(result, "main")}
        assert main_labels, f"{operation}: main node が作られていない"
        assert main_labels <= canonical, (
            f"{operation}: 正準 stage ラベル外の main ラベル "
            f"{sorted(main_labels - canonical)}"
        )
        assert "Transforms" not in main_labels


def test_generic_operations_never_reach_the_main_layer():
    """generic operation は main node にしない（式 backing があっても detail 止まり）。"""
    result = ComponentGraphNormalizer().normalize(
        _empty_graph(), _empty_components(), _chain_of("transform", "relate", "associate")
    )
    assert _layer(result, "main") == []
    for node in result.nodes:
        assert node.graph_layer in ("equation_detail", "debug")
        # fallback / inferred を確定扱いにしない
        if node.source_backing_status == "inferred":
            assert node.review_status == "review_required"
            assert node.review_reasons, f"{node.component_id}: review_reasons が空"
