"""Step 5 TheoryBundleBuilder + TeachingOutputMapper tests (issue #326).

Non-LLM 疎通 coverage for the TheoryBundle container and the teaching output
mapping: bundle creation + references, bundle/component separation, course-topic
linkage to refined component IDs, blackbox policy reflecting equation confidence,
review_required propagation, and the Step 5 validation blocks. Also includes a
Step 3 -> 4 -> 5 integration path that feeds real refiner/aligner output into the
bundle stage.
"""
from episteme_graph.agents.component_assembly.component_refiner import ComponentRefiner
from episteme_graph.agents.component_assembly.derivation_graph_aligner import (
    DerivationGraphAligner,
)
from episteme_graph.agents.component_assembly.theory_bundle import (
    TheoryBundleStage,
    TheoryBundleBuilder,
    TeachingOutputMapper,
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

STAGE = TheoryBundleStage()


def _component(**kwargs):
    defaults = dict(
        component_id="comp",
        component_type="RelationComponent",
        label="A reusable theory unit",
        summary="summary",
        inputs=[{"name": "input"}],
        outputs=[{"name": "output"}],
        preconditions=[],
        cautions=[],
        dependencies=[],
        evidence_refs={},
        reason="",
        confidence=0.8,
        review_notes=[],
        review_status="source_backed",
        source_scope={},
    )
    defaults.update(kwargs)
    return ComponentRecord(**defaults)


def _result(components, derivation_graph_alignment=None, document_id="doc1"):
    return ComponentAssemblyResult(
        document_id=document_id,
        components_version=COMPONENTS_VERSION,
        cartridge_id=None,
        components=components,
        assembly_hints=[],
        review_notes=[],
        confidence=0.8,
        derivation_graph_alignment=derivation_graph_alignment or {},
    )


class _LLMInput:
    def __init__(self, equations=None, claim_centered_plan=None):
        self.equations = equations or []
        self.available_equations = []
        self.claim_centered_plan = claim_centered_plan or {}


def _plan(headline_id="claim_head", headline="The central claim of the paper."):
    return {"headline_claim_ids": [headline_id], "headline_claim": headline}


def _eq(equation_id, **policy):
    return {
        "equation_id": equation_id,
        "plain_text": equation_id,
        "confidence_policy": {
            "can_support_claim": policy.get("can_support_claim", True),
            "can_be_used_in_derivation": policy.get("can_be_used_in_derivation", True),
            "can_be_rendered_as_final_formula": policy.get("can_final", True),
            "allowed_downstream_use": policy.get("allowed", "unrestricted"),
        },
    }


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
        document_id="doc1",
        source_section_ids=[],
        steps=steps,
        review_status=kwargs.pop("review_status", "auto_accepted"),
        **kwargs,
    )
    if linked_component_ids is not None:
        chain.linked_component_ids = linked_component_ids
    return chain


def _derivations(*chains):
    return DerivationChainResult(document_id="doc1", cartridge_id=None, chains=list(chains))


# ---------------------------------------------------------------------------
# 1. TheoryBundle creation
# ---------------------------------------------------------------------------


def test_theory_bundle_created_with_refs_and_headline():
    comps = [
        _component(component_id="c1", responsibility_type="model", support_role="theory_base",
                   concepts=["alpha"]),
        _component(component_id="c2", responsibility_type="constraint", support_role="result",
                   concepts=["beta"]),
    ]
    alignment = {
        "theory_component_graph": {"nodes": [
            {"component_id": "c1", "review_status": "source_backed"},
            {"component_id": "c2", "review_status": "source_backed"},
        ], "edges": []},
        "support_map": {"layers": [{"layer_id": "result_layer", "component_ids": ["c2"]}]},
    }
    out = STAGE.run(
        _result(comps, alignment),
        llm_input=_LLMInput(claim_centered_plan=_plan()),
        derivations=_derivations(),
    )
    bundle = out.theory_bundle
    assert bundle["kind"] == "theory_bundle"
    assert bundle["paper_id"] == "doc1"
    assert bundle["bundle_id"] == "doc1:theory_bundle"
    # headline claim attached to the bundle.
    assert bundle["headline_claim_id"] == "claim_head"
    assert bundle["headline_claim"] == "The central claim of the paper."
    # refined components are bundle children (by id reference).
    assert bundle["component_ids"] == ["c1", "c2"]
    # graph / support map / registries reachable by reference handle.
    assert bundle["component_graph_id"] == "doc1:theory_component_graph"
    assert bundle["support_map_id"] == "doc1:support_map"
    assert bundle["evidence_registry_id"] == "doc1:evidence_registry"
    assert bundle["equation_registry_id"] == "doc1:equation_registry"
    assert bundle["derivation_registry_id"] == "doc1:derivation_registry"
    assert bundle["review_status"] == "source_backed"

    v = out.theory_bundle_validation
    assert v["bundle_created"] is True
    assert v["headline_claim_linked"] is True
    assert v["component_refs_valid"] is True
    assert v["support_map_linked"] is True


# ---------------------------------------------------------------------------
# 2. Bundle vs component separation
# ---------------------------------------------------------------------------


def test_bundle_references_components_and_is_not_a_giant_component():
    comps = [
        _component(component_id="c1", responsibility_type="model"),
        _component(component_id="c2", responsibility_type="derivation"),
        _component(component_id="c3", responsibility_type="constraint"),
    ]
    out = STAGE.run(
        _result(comps),
        llm_input=_LLMInput(claim_centered_plan=_plan()),
        derivations=_derivations(),
    )
    bundle = out.theory_bundle
    # The bundle is a container that references component IDs; the reusable parts
    # remain as separate components (no giant component swallowing the paper).
    assert bundle["component_ids"] == ["c1", "c2", "c3"]
    assert "inputs" not in bundle and "internal_flow" not in bundle
    # The paper-level headline claim lives on the bundle, not duplicated as a
    # component field.
    assert bundle["headline_claim_id"] == "claim_head"


# ---------------------------------------------------------------------------
# 3. Course mapping linkage (teaching output)
# ---------------------------------------------------------------------------


def test_course_topics_link_refined_components_with_concepts():
    comps = [
        _component(
            component_id="c_def",
            responsibility_type="definition",
            prerequisite_concepts=["alpha"],
            introduced_concepts=["sigma"],
            teaching_takeaway="Sigma is the smoothed moment.",
        ),
    ]
    out = STAGE.run(
        _result(comps),
        llm_input=_LLMInput(claim_centered_plan=_plan()),
        derivations=_derivations(),
    )
    topics = out.course_mapping["topics"]
    assert len(topics) == 1
    topic = topics[0]
    assert topic["topic_id"] == "topic:c_def"
    assert topic["linked_component_ids"] == ["c_def"]
    assert topic["prerequisite_concepts"] == ["alpha"]
    assert topic["introduced_concepts"] == ["sigma"]
    assert "Sigma is the smoothed moment." in topic["learning_objectives"]
    assert topic["assessment_prompts"]
    # visualization plan references the actual component.
    assert {"kind": "component", "ref": "c_def"} in topic["visualization_plan"]
    assert out.teaching_output_validation["course_topics_link_components"] is True
    assert out.teaching_output_validation["blueprint_refs_valid"] is True


# ---------------------------------------------------------------------------
# 4. Low-confidence equation -> blackbox policy
# ---------------------------------------------------------------------------


def test_low_confidence_equation_reflected_in_blackbox_policy_and_review():
    comp = _component(
        component_id="c_res",
        responsibility_type="constraint",
        support_role="result",
        linked_equation_ids=["eq_final"],
        output_equation_ids=["eq_final"],
    )
    # eq_final must not be rendered as a final formula.
    llm_input = _LLMInput(
        equations=[_eq("eq_final", can_final=False, allowed="display_with_warning")],
        claim_centered_plan=_plan(),
    )
    out = STAGE.run(_result([comp]), llm_input=llm_input, derivations=_derivations())

    topic = out.course_mapping["topics"][0]
    policy_eqs = [p["equation_id"] for p in topic["blackbox_policy"]]
    assert "eq_final" in policy_eqs
    # the bundle is downgraded because it carries a low-confidence equation.
    assert out.theory_bundle["review_status"] == "review_required"
    assert any("eq_final" in r for r in out.theory_bundle["review_reasons"])
    # blackbox policy is self-consistent with the low-confidence equation set.
    assert out.teaching_output_validation["blackbox_policy_respects_confidence"] is True
    # equation also surfaced for the blueprint as a review-required item.
    bp_items = out.blueprint_updates["review_required_items"]
    assert any(i["type"] == "equation" and i["id"] == "eq_final" for i in bp_items)


# ---------------------------------------------------------------------------
# 5. review_required component -> teaching output
# ---------------------------------------------------------------------------


def test_review_required_component_propagates_to_topic_and_blueprint():
    comp = _component(component_id="c_rev", responsibility_type="model",
                      review_status="review_required")
    out = STAGE.run(
        _result([comp]),
        llm_input=_LLMInput(claim_centered_plan=_plan()),
        derivations=_derivations(),
    )
    topic = out.course_mapping["topics"][0]
    assert topic["review_status"] == "review_required"
    assert any(
        r["code"] == "topic_review_required" for r in out.teaching_output_validation["review_items"]
    )
    assert any(
        i["type"] == "component" and i["id"] == "c_rev"
        for i in out.blueprint_updates["review_required_items"]
    )
    assert out.theory_bundle["review_status"] == "review_required"


# ---------------------------------------------------------------------------
# 6. Missing headline / support map -> warnings (still creates bundle)
# ---------------------------------------------------------------------------


def test_missing_headline_and_support_map_warn_but_bundle_created():
    comp = _component(component_id="c1", responsibility_type="model")
    out = STAGE.run(
        _result([comp]),  # no alignment -> no support map
        llm_input=_LLMInput(claim_centered_plan={}),  # no headline
        derivations=_derivations(),
    )
    v = out.theory_bundle_validation
    assert v["bundle_created"] is True
    assert v["headline_claim_linked"] is False
    assert v["support_map_linked"] is False
    assert any(w["code"] == "bundle_headline_claim_missing" for w in v["warnings"])
    assert any(w["code"] == "bundle_support_map_missing" for w in v["warnings"])


# ---------------------------------------------------------------------------
# 7. Builder/mapper unit-level: dangling detection in validation
# ---------------------------------------------------------------------------


def test_validation_flags_dangling_topic_component_ref():
    # A teaching output that references a component not in the refined set must be
    # reported (defensive check via the stage validator).
    from episteme_graph.agents.component_assembly.theory_bundle import (
        _validate_teaching_output,
    )
    comp = _component(component_id="c1", responsibility_type="model")
    teaching = {"topics": [{
        "topic_id": "topic:ghost",
        "linked_component_ids": ["ghost"],
        "blackbox_policy": [],
    }]}
    blueprint = {"linked_component_ids": ["c1"], "review_required_items": []}
    validation = _validate_teaching_output(teaching, blueprint, [comp], {})
    assert validation["course_topics_link_components"] is False
    assert any(e["code"] == "topic_dangling_component_ref" for e in validation["errors"])


# ---------------------------------------------------------------------------
# 8. Integration: Step 3 -> 4 -> 5 end to end
# ---------------------------------------------------------------------------


def test_step3_4_5_integration_bundle_separation_and_refined_ids():
    cx = _component(
        component_id="cx",
        label="Coarse bundle",
        linked_equation_ids=["eq_def", "eq_final"],
        linked_claim_ids=["claim_head"],
        linked_derivation_ids=["d1"],
        teaching_takeaway="The bias-free consistency relation removes b2.",
        review_status="auto_accepted",
        split_recommendation={
            "required": True,
            "reasons": ["mix"],
            "suggested_components": [
                {"name": "Definition of moments", "responsibility_type": "definition"},
                {"name": "Consistency relation", "responsibility_type": "constraint"},
            ],
        },
    )
    llm_input = _LLMInput(
        equations=[
            {"equation_id": "eq_def", "role": "definition"},
            {
                "equation_id": "eq_final",
                "role": "result",
                "confidence": 0.95,
                "semantic_status": "source_backed",
                "confidence_policy": {
                    "can_support_claim": True,
                    "can_be_used_in_derivation": True,
                    "can_be_rendered_as_final_formula": False,
                    "allowed_downstream_use": "display_with_warning",
                },
            },
        ],
        claim_centered_plan=_plan(headline="Bias-free consistency relation holds"),
    )

    result = _result([cx])
    # Step 3
    result = ComponentRefiner().refine(result, llm_input=llm_input, derivations=None)
    refined_ids = [c.component_id for c in result.components]
    assert "cx" not in refined_ids  # split happened
    # Step 4 (chain references the OLD id "cx")
    chain = _chain("d1", [_step("s1", "derive", ["eq_def"], ["eq_final"])],
                   linked_component_ids=["cx"])
    alignment = DerivationGraphAligner().align(
        result, llm_input=llm_input, derivations=_derivations(chain)
    )
    result.derivation_graph_alignment = alignment.to_dict()
    # Step 5
    out = STAGE.run(result, llm_input=llm_input, derivations=_derivations(chain))

    bundle = out.theory_bundle
    # Bundle references the REFINED component IDs, never the old "cx".
    assert bundle["component_ids"] == refined_ids
    assert "cx" not in bundle["component_ids"]
    # Headline claim is on the bundle; reusable parts remain as components.
    assert bundle["headline_claim_id"] == "claim_head"
    # Every course topic links a refined component id (and no old id).
    topic_links = [
        cid for t in out.course_mapping["topics"] for cid in t["linked_component_ids"]
    ]
    assert set(topic_links) == set(refined_ids)
    assert "cx" not in topic_links
    # The constraint child renders eq_final (can_be_rendered_as_final_formula=false):
    # Step 4 marked it review_required and Step 5 reflects it.
    constraint_topic = next(
        t for t in out.course_mapping["topics"]
        if any("eq_final" in p["equation_id"] for p in t["blackbox_policy"])
    )
    assert constraint_topic["review_status"] == "review_required"
    assert bundle["review_status"] == "review_required"
