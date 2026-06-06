"""Step 3 -> Step 4 integration / cross-stage wiring tests (#324 / #325).

The dedicated unit tests in ``test_component_refinement_stage.py`` and
``test_derivation_graph_aligner.py`` exercise each stage in isolation; the
aligner unit tests hand-build the ``component_id_mapping``. These tests instead
run the *real* ComponentRefiner output into the DerivationGraphAligner so the
non-LLM contract between the two stages is exercised end to end:

- Step 3 splits a coarse component and emits ``component_id_mapping``.
- Step 4 consumes that mapping to resolve old (pre-split) component references in
  dependencies and derivation chains to the refined IDs.
- No dangling references / errors survive a clean happy path.
- A low-confidence equation propagates from the refined component all the way to
  the aligned derivation path's review status and export review items.
"""
from episteme_graph.agents.component_assembly.component_refiner import ComponentRefiner
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

REFINER = ComponentRefiner()
ALIGNER = DerivationGraphAligner()


def _component(**kwargs):
    defaults = dict(
        component_id="comp",
        component_type="RelationComponent",
        label="Coarse theory component",
        summary="A coarse bundle of distinct responsibilities.",
        inputs=[{"name": "input"}],
        outputs=[{"name": "output"}],
        preconditions=[],
        cautions=[],
        dependencies=[],
        evidence_refs={},
        reason="coarse",
        confidence=0.8,
        review_notes=[],
        review_status="auto_accepted",
        source_scope={"section_ids": ["s1"]},
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


class _LLMInput:
    def __init__(self, equations=None, claims=None, claim_centered_plan=None):
        self.equations = equations or []
        self.available_equations = []
        self.available_claims = claims or []
        self.accepted_claims = []
        self.claim_centered_plan = claim_centered_plan or {}


def _step(step_id, op, inputs, outputs, **kwargs):
    return DerivationStep(
        step_id=step_id,
        input_equation_ids=inputs,
        operation=op,
        output_equation_ids=outputs,
        review_status=kwargs.pop("review_status", "auto_accepted"),
        **kwargs,
    )


def _chain(derivation_id, steps, linked_component_ids=None, **kwargs):
    chain = DerivationChainRecord(
        derivation_id=derivation_id,
        document_id="doc",
        source_section_ids=[],
        steps=steps,
        review_status=kwargs.pop("review_status", "auto_accepted"),
        **kwargs,
    )
    if linked_component_ids is not None:
        chain.linked_component_ids = linked_component_ids
    return chain


def _derivations(*chains):
    return DerivationChainResult(document_id="doc", cartridge_id=None, chains=list(chains))


def _split_rec(*suggested):
    return {
        "required": True,
        "reasons": ["responsibility type has more than one primary value"],
        "suggested_components": [
            {"name": name, "responsibility_type": resp} for name, resp in suggested
        ],
    }


# ---------------------------------------------------------------------------
# Happy path: refiner split -> aligner consumes mapping, resolves old refs.
# ---------------------------------------------------------------------------


def test_refiner_split_then_aligner_uses_refined_ids_no_dangling():
    # cx is oversized (definition + derivation) and gets split. cb depends on the
    # *old* id cx; a derivation chain is also linked to the old id cx.
    cx = _component(
        component_id="cx",
        linked_equation_ids=["eq_def", "eq_der"],
        linked_derivation_ids=["d1"],
        split_recommendation=_split_rec(
            ("Definition of foo", "definition"),
            ("Derivation of bar", "derivation"),
        ),
    )
    cb = _component(
        component_id="cb",
        component_type="ClaimBundleComponent",
        split_recommendation={"required": False, "suggested_components": []},
        dependencies=[
            {"dependency_type": "depends_on", "component_refs": ["cx"], "reason": "uses cx"}
        ],
    )
    llm_input = _LLMInput(equations=[
        {"equation_id": "eq_def", "role": "definition", "plain_text": "def"},
        {"equation_id": "eq_der", "role": "transformation", "plain_text": "der"},
    ])

    # --- Step 3 ---
    refined = REFINER.refine(_result([cx, cb]), llm_input=llm_input, derivations=None)
    refined_ids = [c.component_id for c in refined.components]
    assert "cx" not in refined_ids
    assert {"cx__r1", "cx__r2", "cb"} == set(refined_ids)
    # Step 3 already rewired the in-graph dependency to the first child.
    cb_refined = next(c for c in refined.components if c.component_id == "cb")
    assert cb_refined.dependencies[0]["component_refs"] == ["cx__r1"]

    # --- Step 4 (consumes Step 3 output, not a hand-built mapping) ---
    chain = _chain(
        "d1",
        [_step("s1", "derive", ["eq_def"], ["eq_der"])],
        linked_component_ids=["cx"],  # still the OLD id
    )
    alignment = ALIGNER.align(refined, llm_input=llm_input, derivations=_derivations(chain))

    # Theory graph contains only refined component nodes.
    node_ids = [n["component_id"] for n in alignment.theory_component_graph["nodes"]]
    assert set(node_ids) == {"cx__r1", "cx__r2", "cb"}
    # The chain's old "cx" reference is resolved to BOTH refined children.
    assert alignment.aligned_derivation_chains[0]["linked_component_ids"] == [
        "cx__r1",
        "cx__r2",
    ]
    # No unresolved / dangling references remain on a clean happy path.
    assert alignment.unresolved_component_refs == []
    assert alignment.graph_alignment_validation["dangling_component_refs"] == []
    assert alignment.export_validation["errors"] == []
    assert alignment.graph_alignment_validation["component_graph_clean"] is True


# ---------------------------------------------------------------------------
# Low-confidence path: blocked equation propagates Step 3 -> Step 4.
# ---------------------------------------------------------------------------


def test_low_confidence_equation_propagates_refiner_to_aligner_path():
    cx = _component(
        component_id="cx",
        linked_equation_ids=["eq_def", "eq_blocked"],
        linked_derivation_ids=["d1"],
        split_recommendation=_split_rec(
            ("Definition", "definition"),
            ("Derivation", "derivation"),
        ),
    )
    llm_input = _LLMInput(equations=[
        {"equation_id": "eq_def", "role": "definition"},
        {
            "equation_id": "eq_blocked",
            "role": "transformation",
            "confidence_policy": {
                "can_support_claim": False,
                "can_be_used_in_derivation": False,
                "allowed_downstream_use": "blocked",
            },
        },
    ])

    refined = REFINER.refine(_result([cx]), llm_input=llm_input, derivations=None)
    derivation_child = next(
        c for c in refined.components if c.responsibility_type == "derivation"
    )
    # Step 3 already downgraded the child that owns the blocked equation.
    assert derivation_child.review_status == "review_required"

    chain = _chain(
        "d1",
        [_step("s1", "derive", ["eq_def"], ["eq_blocked"])],
        linked_component_ids=["cx"],
    )
    alignment = ALIGNER.align(refined, llm_input=llm_input, derivations=_derivations(chain))

    aligned = alignment.aligned_derivation_chains[0]
    # The step using the blocked equation, and the whole chain, are review_required.
    assert aligned["steps"][0]["review_status"] == "review_required"
    assert aligned["review_status"] == "review_required"
    assert "d1" in alignment.graph_alignment_validation["review_required_paths"]
    assert any(
        item["code"] == "review_required_derivation_path" and item["derivation_id"] == "d1"
        for item in alignment.export_validation["review_items"]
    )
    # The refined derivation component node also reflects review_required status.
    der_node = next(
        n
        for n in alignment.theory_component_graph["nodes"]
        if n["component_id"] == derivation_child.component_id
    )
    assert der_node["review_status"] == "review_required"
