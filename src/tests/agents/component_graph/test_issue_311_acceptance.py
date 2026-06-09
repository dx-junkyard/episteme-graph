"""End-to-end acceptance checks for issue #311.

Issue #311 is a *verification* issue: it confirms that the TheoryOperationGraph
display fixes (#306 / #308) behave as intended. This module encodes the issue's
10 acceptance criteria as a single, end-to-end regression guard so the behavior
cannot silently regress. It drives the deterministic normalizer over a
domain-neutral derivation chain and asserts the produced graph payload (the JSON
the UI consumes) against each criterion.

Acceptance criteria (issue #311):
  1. main node ``label`` is a short theory-stage name
  2. long claim / reason text lives in ``description``, not ``label``
  3. ``graph_layer`` separates main / equation_detail / debug
  4. (UI) main layer is the default view — covered by admin.js, asserted via the
     payload precondition that main nodes exist and are distinguishable
  5. EquationOperationNode does not appear in the main layer
  6. ``source_backing_status`` is present on every node / edge
  7. ``review_required`` nodes / edges always carry ``review_reasons``
  8. fallback / inferred nodes are never published as confirmed source_backed
  9. non-atomic / equation-only backing is flagged ``missing_atomic_claim``
 10. (process) atomic-claim improvement is tracked in a separate issue (#312)
"""
from episteme_graph.agents.component_assembly.schema import ComponentAssemblyResult
from episteme_graph.agents.component_graph.normalizer import ComponentGraphNormalizer
from episteme_graph.agents.component_graph.schema import (
    GRAPH_SCHEMA_VERSION,
    SOURCE_BACKING_STATUSES,
    THEORY_STAGE_LABELS,
    ComponentGraphResult,
)
from episteme_graph.agents.component_graph.validator import ComponentGraphValidator
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


def _payload():
    """Build the component_graph.json payload the UI consumes."""
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
        _step("transform", ["eq_f"], ["eq_g"]),  # generic → debug layer
    ]
    derivations = DerivationChainResult(
        document_id="doc",
        cartridge_id=None,
        chains=[DerivationChainRecord(
            derivation_id="deriv",
            document_id="doc",
            source_section_ids=[],
            steps=steps,
        )],
    )
    claim_index = {
        "claim_1": {
            "text": "second-order moment vanishes under symmetry",
            "evidence_text": "p.4 derivation",
            "is_atomic": True,
        },
    }
    empty_graph = ComponentGraphResult(
        document_id="doc",
        graph_schema_version=GRAPH_SCHEMA_VERSION,
        cartridge_id=None,
        nodes=[],
        edges=[],
        review_notes=[],
        confidence=0.0,
    )
    empty_components = ComponentAssemblyResult(
        document_id="doc",
        components_version="v1",
        cartridge_id=None,
        components=[],
        assembly_hints=[],
        review_notes=[],
        confidence=0.8,
    )
    result = ComponentGraphNormalizer().normalize(
        empty_graph, empty_components, derivations, claim_index=claim_index
    )
    return result, result.to_graph_payload()


def _layer(payload, layer):
    return [n for n in payload["nodes"] if n["graph_layer"] == layer]


# --- Criterion 1: short main labels ----------------------------------------


def test_main_labels_are_short_stage_names():
    _result, payload = _payload()
    stage_labels = set(THEORY_STAGE_LABELS.values())
    main = _layer(payload, "main")
    assert main
    for node in main:
        label = node["label"]
        assert label in stage_labels, f"main label {label!r} is not a theory-stage name"
        assert "eq_" not in label.lower()  # never an equation-id fallback
        assert len(label) <= 40  # stays short


# --- Criterion 2: long text in description ----------------------------------


def test_long_text_lives_in_description_not_label():
    _result, payload = _payload()
    elimination = next(n for n in _layer(payload, "main") if n["label"] == "Elimination")
    # The atomic-claim sentence enriches description, while the label stays short.
    assert "second-order moment vanishes" in elimination["description"]
    assert "second-order moment vanishes" not in elimination["label"]


# --- Criterion 3 & 5: layer separation --------------------------------------


def test_graph_layer_separates_main_detail_debug():
    _result, payload = _payload()
    main = _layer(payload, "main")
    detail = _layer(payload, "equation_detail")
    debug = _layer(payload, "debug")
    assert main and detail and debug
    # Every node carries an explicit, valid graph_layer.
    valid_layers = {"main", "equation_detail", "debug"}
    assert all(n["graph_layer"] in valid_layers for n in payload["nodes"])
    # TheoryOperationNode → main; EquationOperationNode → never main (criterion 5).
    assert all(n["component_type"] == "TheoryOperationNode" for n in main)
    assert all(n["component_type"] == "EquationOperationNode" for n in detail)
    assert all(n["component_type"] != "EquationOperationNode" or n["graph_layer"] != "main"
               for n in payload["nodes"])
    # The main graph is a small backbone, smaller than the detailed trace.
    assert len(main) <= len(detail) + len(debug)


# --- Criterion 4: main layer is distinguishable (UI default) ----------------


def test_main_layer_nodes_link_to_their_detail_members():
    # The UI defaults to the main layer; that only works if main nodes reference
    # the detail nodes they aggregate so the toggle can drill down.
    _result, payload = _payload()
    main_by_id = {n["component_id"]: n for n in _layer(payload, "main")}
    detail = _layer(payload, "equation_detail")
    assert detail
    for node in detail:
        assert node["parent_component_id"] in main_by_id
        assert node["component_id"] in main_by_id[node["parent_component_id"]]["member_component_ids"]


# --- Criterion 6: source_backing_status everywhere --------------------------


def test_every_node_and_edge_has_source_backing_status():
    _result, payload = _payload()
    for node in payload["nodes"]:
        assert node["source_backing_status"] in SOURCE_BACKING_STATUSES
    for edge in payload["edges"]:
        # Criterion 6: edges expose the same backing vocabulary as nodes.
        assert edge["source_backing_status"] in SOURCE_BACKING_STATUSES
        assert edge["review_status"] in ("source_backed", "review_required")


# --- Criterion 7: review_required has reasons -------------------------------


def test_review_required_nodes_and_edges_always_have_reasons():
    _result, payload = _payload()
    for node in payload["nodes"]:
        if node["source_backing_status"] == "review_required":
            assert node["review_reasons"], f"{node['component_id']} missing review_reasons"
    for edge in payload["edges"]:
        if edge["review_status"] == "review_required":
            assert edge["review_reasons"], f"{edge['edge_id']} missing review_reasons"


# --- Criterion 8: fallback / inferred never confirmed -----------------------


def test_inferred_nodes_are_not_confirmed_source_backed():
    _result, payload = _payload()
    debug = _layer(payload, "debug")
    assert debug  # the generic "transform" step lands here
    for node in debug:
        assert node["source_backing_status"] != "source_backed"
        assert node["source_backing_status"] in ("inferred", "review_required")
        assert "fallback_or_inferred_node" in node["review_reasons"]


# --- Criterion 9: non-atomic / equation-only flagged ------------------------


def test_equation_only_node_is_flagged_missing_atomic_claim():
    _result, payload = _payload()
    define = next(n for n in _layer(payload, "main") if n["operation"] == "define")
    # Backed only by equation IDs → partially source-backed, with the atomic gap.
    assert define["source_backing_status"] == "partially_source_backed"
    assert "missing_atomic_claim" in define["review_reasons"]


def test_atomic_claim_backed_node_is_source_backed_without_warning():
    _result, payload = _payload()
    elimination = next(n for n in _layer(payload, "main") if n["label"] == "Elimination")
    # Equation + atomic claim + evidence → confirmed structure, no residual gap.
    assert elimination["source_backing_status"] == "source_backed"
    assert elimination["review_reasons"] == []
    assert elimination["linked_claim_ids"] == ["claim_1"]
    assert elimination["linked_evidence_ids"] == ["ev_1"]


# --- Whole-graph: validator stays clean -------------------------------------


def test_generated_graph_passes_validation_without_errors():
    result, _payload_dict = _payload()
    issues = ComponentGraphValidator().validate(result)
    errors = [i for i in issues if i.severity == "error"]
    assert errors == [], [f"{i.rule_id}: {i.message}" for i in errors]
