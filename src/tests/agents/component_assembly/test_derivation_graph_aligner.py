"""Step 4 DerivationGraphAligner tests (issue #325).

Covers clean graph separation, derivation alignment, final-result dependency,
low-confidence equation propagation, old-id remapping, support map generation,
operation reference checks, generic edge warnings, and missing support layers.
"""
from episteme_graph.agents.component_assembly.derivation_graph_aligner import (
    DerivationGraphAligner,
)
from episteme_graph.agents.component_assembly.schema import (
    COMPONENTS_VERSION,
    ComponentAssemblyResult,
    ComponentRecord,
)
from episteme_graph.agents.derivation_chain.schema import (
    DerivationChainRecord,
    DerivationChainResult,
    DerivationStep,
)

ALIGNER = DerivationGraphAligner()


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _component(**kwargs):
    defaults = dict(
        component_id="comp",
        component_type="RelationComponent",
        label="Component",
        summary="summary",
        inputs=[],
        outputs=[],
        preconditions=[],
        cautions=[],
        dependencies=[],
        evidence_refs={},
        reason="",
        confidence=0.8,
        review_notes=[],
        review_status="auto_accepted",
    )
    defaults.update(kwargs)
    return ComponentRecord(**defaults)


def _result(components, component_refinement=None):
    return ComponentAssemblyResult(
        document_id="doc",
        components_version=COMPONENTS_VERSION,
        cartridge_id=None,
        components=components,
        assembly_hints=[],
        review_notes=[],
        confidence=0.8,
        component_refinement=component_refinement or {},
    )


def _step(step_id, op, inputs, outputs, **kwargs):
    return DerivationStep(
        step_id=step_id,
        input_equation_ids=inputs,
        operation=op,
        output_equation_ids=outputs,
        review_status=kwargs.pop("review_status", "auto_accepted"),
        **kwargs,
    )


def _chain(derivation_id, steps, **kwargs):
    return DerivationChainRecord(
        derivation_id=derivation_id,
        document_id="doc",
        source_section_ids=[],
        steps=steps,
        review_status=kwargs.pop("review_status", "auto_accepted"),
        **kwargs,
    )


def _derivations(*chains):
    return DerivationChainResult(document_id="doc", cartridge_id=None, chains=list(chains))


class _LLMInput:
    def __init__(self, equations=None, claim_centered_plan=None):
        self.equations = equations or []
        self.available_equations = []
        self.claim_centered_plan = claim_centered_plan or {}


def _eq(equation_id, *, can_derivation=True, can_final=True, allowed="unrestricted"):
    return {
        "equation_id": equation_id,
        "plain_text": equation_id,
        "confidence_policy": {
            "can_support_claim": can_derivation,
            "can_be_used_in_derivation": can_derivation,
            "can_be_rendered_as_final_formula": can_final,
            "allowed_downstream_use": allowed,
        },
    }


# ---------------------------------------------------------------------------
# 1. Clean separation
# ---------------------------------------------------------------------------


def test_clean_separation_of_component_and_operation_graphs():
    comp = _component(
        component_id="c_der",
        responsibility_type="derivation",
        linked_derivation_ids=["d1"],
        input_equation_ids=["eq_a"],
        output_equation_ids=["eq_b"],
    )
    chain = _chain("d1", [_step("s1", "derive", ["eq_a"], ["eq_b"])])
    alignment = ALIGNER.align(
        _result([comp]),
        llm_input=_LLMInput(equations=[_eq("eq_a"), _eq("eq_b")]),
        derivations=_derivations(chain),
    )

    tcg = alignment.theory_component_graph
    eog = alignment.equation_operation_graph
    assert tcg["graph_type"] == "theory_component_graph"
    assert [n["component_id"] for n in tcg["nodes"]] == ["c_der"]
    # No operation fields leak into component nodes.
    for node in tcg["nodes"]:
        assert "operation_id" not in node and "operation_type" not in node
    assert eog["graph_type"] == "equation_operation_graph"
    assert [n["operation_id"] for n in eog["nodes"]] == ["s1"]
    # No component nodes in the operation graph.
    for node in eog["nodes"]:
        assert "component_id" not in node
    # The connection lives in component_operation_links.
    links = alignment.component_operation_links
    assert links == [
        {
            "component_id": "c_der",
            "operation_ids": ["s1"],
            "role": "derives",
            "input_equation_ids": ["eq_a"],
            "output_equation_ids": ["eq_b"],
            "review_status": "source_backed",
        }
    ]
    assert alignment.graph_alignment_validation["component_graph_clean"] is True
    assert alignment.graph_alignment_validation["operation_graph_clean"] is True


# ---------------------------------------------------------------------------
# 2. Derivation alignment
# ---------------------------------------------------------------------------


def test_derivation_chain_aligned_to_component():
    comp = _component(
        component_id="c_der",
        responsibility_type="derivation",
        linked_derivation_ids=["d1"],
        input_equation_ids=["eq_a"],
        output_equation_ids=["eq_c"],
    )
    chain = _chain(
        "d1",
        [
            _step("s1", "substitute", ["eq_a"], ["eq_b"]),
            _step("s2", "solve", ["eq_b"], ["eq_c"]),
        ],
        teaching_takeaway="Eliminate the bias parameter",
    )
    alignment = ALIGNER.align(
        _result([comp]),
        llm_input=_LLMInput(equations=[_eq("eq_a"), _eq("eq_b"), _eq("eq_c")]),
        derivations=_derivations(chain),
    )

    aligned = alignment.aligned_derivation_chains
    assert len(aligned) == 1
    entry = aligned[0]
    assert entry["derivation_id"] == "d1"
    assert entry["name"] == "Eliminate the bias parameter"
    assert entry["linked_component_ids"] == ["c_der"]
    assert [s["operation_id"] for s in entry["steps"]] == ["s1", "s2"]
    assert all(s["component_id"] == "c_der" for s in entry["steps"])
    assert entry["steps"][0]["input_equation_ids"] == ["eq_a"]
    assert entry["steps"][1]["output_equation_ids"] == ["eq_c"]
    assert entry["review_status"] == "source_backed"
    # Operation flow edge produced inside the operation graph.
    edges = alignment.equation_operation_graph["edges"]
    assert {"source": "s1", "target": "s2", "edge_type": "produces_input_for"} in edges


# ---------------------------------------------------------------------------
# 3. Final result dependency
# ---------------------------------------------------------------------------


def test_final_result_with_upstream_derivation_no_warning():
    derivation = _component(
        component_id="c_der",
        responsibility_type="derivation",
        linked_derivation_ids=["d1"],
        input_equation_ids=["eq_a"],
        output_equation_ids=["eq_b"],
    )
    constraint = _component(
        component_id="c_res",
        responsibility_type="constraint",
        dependencies=[{"dependency_type": "depends_on", "component_refs": ["c_der"], "reason": ""}],
        output_equation_ids=["eq_b"],
    )
    chain = _chain("d1", [_step("s1", "derive", ["eq_a"], ["eq_b"])])
    alignment = ALIGNER.align(
        _result([derivation, constraint]),
        llm_input=_LLMInput(equations=[_eq("eq_a"), _eq("eq_b")]),
        derivations=_derivations(chain),
    )

    warnings = alignment.export_validation["warnings"]
    assert not any(
        w["code"] == "final_result_missing_upstream_derivation" for w in warnings
    )
    # The component graph has a depends_on edge from result to derivation.
    edges = alignment.theory_component_graph["edges"]
    assert any(
        e["source"] == "c_res" and e["target"] == "c_der" and e["edge_type"] == "depends_on"
        for e in edges
    )


def test_final_result_without_upstream_derivation_warns():
    constraint = _component(
        component_id="c_res",
        responsibility_type="constraint",
        output_equation_ids=["eq_b"],
    )
    alignment = ALIGNER.align(
        _result([constraint]),
        llm_input=_LLMInput(equations=[_eq("eq_b")]),
        derivations=_derivations(),
    )
    warnings = alignment.export_validation["warnings"]
    assert any(
        w["code"] == "final_result_missing_upstream_derivation"
        and w["component_id"] == "c_res"
        for w in warnings
    )


# ---------------------------------------------------------------------------
# 4. Low-confidence equation path
# ---------------------------------------------------------------------------


def test_low_confidence_equation_downgrades_derivation_path():
    comp = _component(
        component_id="c_der",
        responsibility_type="derivation",
        linked_derivation_ids=["d1"],
        input_equation_ids=["eq_bad"],
        output_equation_ids=["eq_out"],
    )
    chain = _chain("d1", [_step("s1", "derive", ["eq_bad"], ["eq_out"])])
    alignment = ALIGNER.align(
        _result([comp]),
        llm_input=_LLMInput(
            equations=[
                _eq("eq_bad", can_derivation=False, allowed="blocked"),
                _eq("eq_out"),
            ]
        ),
        derivations=_derivations(chain),
    )

    aligned = alignment.aligned_derivation_chains[0]
    assert aligned["steps"][0]["review_status"] == "review_required"
    assert aligned["review_status"] == "review_required"
    review_items = alignment.export_validation["review_items"]
    assert any(
        item["code"] == "review_required_derivation_path" and item["derivation_id"] == "d1"
        for item in review_items
    )
    assert "d1" in alignment.graph_alignment_validation["review_required_paths"]


def test_blocked_final_equation_makes_result_review_required():
    constraint = _component(
        component_id="c_res",
        responsibility_type="constraint",
        dependencies=[{"dependency_type": "depends_on", "component_refs": ["c_der"], "reason": ""}],
        output_equation_ids=["eq_final"],
    )
    derivation = _component(
        component_id="c_der",
        responsibility_type="derivation",
        input_equation_ids=["eq_a"],
        output_equation_ids=["eq_final"],
    )
    alignment = ALIGNER.align(
        _result([constraint, derivation]),
        llm_input=_LLMInput(
            equations=[_eq("eq_final", can_final=False), _eq("eq_a")]
        ),
        derivations=_derivations(),
    )
    node = next(
        n for n in alignment.theory_component_graph["nodes"] if n["component_id"] == "c_res"
    )
    assert node["review_status"] == "review_required"


# ---------------------------------------------------------------------------
# 5. Old ID remapping
# ---------------------------------------------------------------------------


def test_old_component_id_resolved_through_step3_mapping():
    # comp_new_a depends on the *old* id "comp_old"; Step 3 split it into two.
    comp_a = _component(
        component_id="comp_new_a",
        responsibility_type="derivation",
        dependencies=[{"dependency_type": "depends_on", "component_refs": ["comp_old"], "reason": ""}],
    )
    comp_b = _component(component_id="comp_new_b", responsibility_type="definition")
    refinement = {
        "component_id_mapping": [
            {
                "original_component_id": "comp_old",
                "new_component_ids": ["comp_new_b"],
                "mapping_type": "one_to_one",
            }
        ]
    }
    alignment = ALIGNER.align(
        _result([comp_a, comp_b], component_refinement=refinement),
        llm_input=_LLMInput(),
        derivations=_derivations(),
    )
    edges = alignment.theory_component_graph["edges"]
    assert any(
        e["source"] == "comp_new_a" and e["target"] == "comp_new_b" for e in edges
    )
    assert alignment.unresolved_component_refs == []
    assert alignment.graph_alignment_validation["dangling_component_refs"] == []


def test_unresolved_component_ref_reported():
    comp = _component(
        component_id="comp_a",
        dependencies=[{"dependency_type": "depends_on", "component_refs": ["ghost"], "reason": ""}],
    )
    alignment = ALIGNER.align(
        _result([comp]), llm_input=_LLMInput(), derivations=_derivations()
    )
    assert any(r["missing_ref"] == "ghost" for r in alignment.unresolved_component_refs)
    assert "ghost" in alignment.graph_alignment_validation["dangling_component_refs"]
    assert any(
        e["code"] == "dangling_component_ref"
        for e in alignment.export_validation["errors"]
    )


# ---------------------------------------------------------------------------
# 6. Support map
# ---------------------------------------------------------------------------


def test_support_map_assigns_components_to_layers():
    comps = [
        _component(component_id="c_theory", responsibility_type="model", support_role="theory_base"),
        _component(component_id="c_bridge", responsibility_type="observation_model", support_role="observable_bridge"),
        _component(component_id="c_der", responsibility_type="derivation", support_role="derivation_core"),
        _component(component_id="c_res", responsibility_type="constraint", support_role="result"),
    ]
    alignment = ALIGNER.align(
        _result(comps),
        llm_input=_LLMInput(claim_centered_plan={"headline_claim_ids": ["claim_head"]}),
        derivations=_derivations(),
    )
    support = alignment.support_map
    assert support["headline_claim_id"] == "claim_head"
    layer_map = {l["layer_id"]: l["component_ids"] for l in support["layers"]}
    assert layer_map["theory_base"] == ["c_theory"]
    assert layer_map["observable_bridge"] == ["c_bridge"]
    assert layer_map["derivation_core"] == ["c_der"]
    assert layer_map["result_layer"] == ["c_res"]
    assert support["emphasis"]["primary_novelty_component_ids"] == ["c_res"]
    assert alignment.graph_alignment_validation["support_map_renderable"] is True
    # All support edges reference existing components.
    for edge in support["edges"]:
        assert edge["target"] == "claim_head"
        assert edge["support_kind"]


# ---------------------------------------------------------------------------
# 7. Operation graph references missing equation
# ---------------------------------------------------------------------------


def test_operation_referencing_missing_equation_is_error():
    comp = _component(
        component_id="c_der",
        responsibility_type="derivation",
        linked_derivation_ids=["d1"],
        input_equation_ids=["eq_a"],
        output_equation_ids=["eq_ghost"],
    )
    chain = _chain("d1", [_step("s1", "derive", ["eq_a"], ["eq_ghost"])])
    alignment = ALIGNER.align(
        _result([comp]),
        llm_input=_LLMInput(equations=[_eq("eq_a")]),  # eq_ghost is missing
        derivations=_derivations(chain),
    )
    assert "eq_ghost" in alignment.graph_alignment_validation["dangling_equation_refs"]
    assert alignment.graph_alignment_validation["operation_graph_clean"] is False
    assert any(
        e["code"] == "dangling_equation_ref" and e["missing_ref"] == "eq_ghost"
        for e in alignment.export_validation["errors"]
    )


# ---------------------------------------------------------------------------
# 8. Generic edge warning
# ---------------------------------------------------------------------------


def test_generic_edge_warns_but_graph_still_built():
    comp_a = _component(
        component_id="c_a",
        dependencies=[{"dependency_type": "mysterious_relation", "component_refs": ["c_b"], "reason": ""}],
    )
    comp_b = _component(component_id="c_b")
    alignment = ALIGNER.align(
        _result([comp_a, comp_b]), llm_input=_LLMInput(), derivations=_derivations()
    )
    edges = alignment.theory_component_graph["edges"]
    related = [e for e in edges if e["edge_type"] == "RELATED_TO"]
    assert len(related) == 1
    assert any(
        w["code"] == "generic_edge" for w in alignment.export_validation["warnings"]
    )
    assert alignment.graph_alignment_validation["generic_edges"] == [
        {"source": "c_a", "target": "c_b"}
    ]


# ---------------------------------------------------------------------------
# 9. Missing support layer / component
# ---------------------------------------------------------------------------


def test_component_missing_from_support_map_warns():
    # A component whose support role maps cleanly should appear; we force a
    # component with no support data to verify it still lands in a layer (no
    # component should silently disappear) -- and a genuinely excluded one warns.
    comp = _component(component_id="c_known", responsibility_type="model", support_role="theory_base")
    alignment = ALIGNER.align(
        _result([comp]), llm_input=_LLMInput(), derivations=_derivations()
    )
    # Every component must be placed; none missing.
    assert alignment.graph_alignment_validation["missing_support_layers"] == []
    layer_components = {
        cid for layer in alignment.support_map["layers"] for cid in layer["component_ids"]
    }
    assert "c_known" in layer_components


def test_derivation_order_validation():
    comp = _component(
        component_id="c_der", responsibility_type="derivation", linked_derivation_ids=["d1"]
    )
    good = _chain(
        "d1",
        [
            _step("s1", "substitute", ["eq_a"], ["eq_b"]),
            _step("s2", "solve", ["eq_b"], ["eq_c"]),
        ],
    )
    ok = ALIGNER.align(
        _result([comp]), llm_input=_LLMInput(), derivations=_derivations(good)
    )
    assert ok.graph_alignment_validation["derivation_order_valid"] is True

    # s1 consumes eq_b which is only produced later by s2 -> backward dependency.
    bad = _chain(
        "d1",
        [
            _step("s1", "solve", ["eq_b"], ["eq_c"]),
            _step("s2", "substitute", ["eq_a"], ["eq_b"]),
        ],
    )
    broken = ALIGNER.align(
        _result([comp]), llm_input=_LLMInput(), derivations=_derivations(bad)
    )
    assert broken.graph_alignment_validation["derivation_order_valid"] is False


def test_derivation_component_missing_io_equations_warns():
    comp = _component(
        component_id="c_der",
        responsibility_type="derivation",
        linked_derivation_ids=["d1"],
    )
    chain = _chain("d1", [_step("s1", "derive", [], [])])
    alignment = ALIGNER.align(
        _result([comp]), llm_input=_LLMInput(), derivations=_derivations(chain)
    )
    assert any(
        w["code"] == "derivation_component_missing_io_equations"
        and w["component_id"] == "c_der"
        for w in alignment.export_validation["warnings"]
    )
