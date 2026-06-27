"""Step 3 ComponentRefiner formal-contract tests (issue #324).

These exercise the suggested_split-driven refinement path and the formal output
contract (component_refinement_records / component_id_mapping /
component_graph_updates / refinement_validation) plus teaching granularity.
"""
from episteme_graph.agents.component_assembly.component_refiner import (
    ComponentRefiner,
    _teaching_granularity,
)
from episteme_graph.agents.component_assembly.schema import (
    COMPONENTS_VERSION,
    ComponentAssemblyResult,
    ComponentRecord,
)

REFINER = ComponentRefiner()


def _component(**kwargs):
    defaults = dict(
        component_id="comp",
        component_type="RelationComponent",
        label="Neutral theory component",
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


def _result(components):
    return ComponentAssemblyResult(
        document_id="doc",
        components_version=COMPONENTS_VERSION,
        cartridge_id=None,
        components=components,
        assembly_hints=[],
        review_notes=[],
        confidence=0.8,
    )


class _LLMInput:
    def __init__(self, equations=None, claims=None):
        self.equations = equations or []
        self.available_equations = []
        self.available_claims = claims or []
        self.accepted_claims = []


class _RoutingLLM:
    def __init__(self, assignments):
        self.assignments = assignments
        self.calls = []

    def generate(self, messages):
        self.calls.append(messages)
        return {"assignments": list(self.assignments)}


def _split_rec(*suggested, reasons=None):
    return {
        "required": True,
        "reasons": reasons or ["responsibility type has more than one primary value"],
        "suggested_components": list(suggested),
    }


def _suggested(name, responsibility):
    return {"name": name, "responsibility_type": responsibility, "linked_equation_ids": []}


# ---------------------------------------------------------------------------
# 1. No-op case
# ---------------------------------------------------------------------------

def test_noop_when_split_not_required():
    component = _component(
        component_id="comp_keep",
        component_type="ClaimBundleComponent",
        split_recommendation={"required": False, "suggested_components": []},
        linked_claim_ids=["claim_1"],
    )
    refined = REFINER.refine(_result([component]), llm_input=None, derivations=None)

    assert [c.component_id for c in refined.components] == ["comp_keep"]
    refinement = refined.component_refinement
    mapping = refinement["component_id_mapping"][0]
    assert mapping == {
        "original_component_id": "comp_keep",
        "new_component_ids": ["comp_keep"],
        "mapping_type": "unchanged",
    }
    record = refinement["component_refinement_records"][0]
    assert record["refinement_status"] == "unchanged"
    assert refinement["component_graph_updates"]["removed_nodes"] == []
    assert refinement["component_graph_updates"]["rewired_edges"] == []
    assert refinement["refinement_validation"]["unchanged_components"] == ["comp_keep"]


# ---------------------------------------------------------------------------
# 2. Basic split case
# ---------------------------------------------------------------------------

def test_basic_split_into_two_single_responsibility_children():
    component = _component(
        component_id="comp_mix",
        linked_equation_ids=["eq_def", "eq_der"],
        split_recommendation=_split_rec(
            _suggested("Definition of foo", "definition"),
            _suggested("Derivation of bar", "derivation"),
        ),
    )
    llm_input = _LLMInput(equations=[
        {"equation_id": "eq_def", "role": "definition", "plain_text": "def"},
        {"equation_id": "eq_der", "role": "transformation", "plain_text": "der"},
    ])
    refined = REFINER.refine(_result([component]), llm_input=llm_input, derivations=None)

    ids = [c.component_id for c in refined.components]
    assert "comp_mix" not in ids
    assert ids == ["comp_mix__r1", "comp_mix__r2"]
    assert [c.responsibility_type for c in refined.components] == ["definition", "derivation"]
    for child in refined.components:
        assert child.inputs and child.outputs

    mapping = refined.component_refinement["component_id_mapping"][0]
    assert mapping["mapping_type"] == "one_to_many"
    assert mapping["new_component_ids"] == ["comp_mix__r1", "comp_mix__r2"]
    record = refined.component_refinement["component_refinement_records"][0]
    assert record["refinement_status"] == "split"
    assert record["split_required"] is True
    assert record["provenance"]["source_equation_ids"] == ["eq_def", "eq_der"]

    graph = refined.component_refinement["component_graph_updates"]
    assert graph["removed_nodes"] == ["comp_mix"]
    assert set(graph["added_nodes"]) == {"comp_mix__r1", "comp_mix__r2"}
    assert graph["unresolved_edges"] == []

    # split_method / split_reason / candidate mapping provenance are recorded.
    assert record["split_method"] == "rule_based"
    assert record["split_reason"]  # non-empty: carries the Step 1 reasons
    candidate_names = {
        m["new_component_id"] for m in record["split_candidate_mapping"]
    }
    assert candidate_names == {"comp_mix__r1", "comp_mix__r2"}


# ---------------------------------------------------------------------------
# 3. Link redistribution
# ---------------------------------------------------------------------------

def test_links_redistributed_not_duplicated_and_unassigned_reported():
    component = _component(
        component_id="comp_links",
        linked_equation_ids=["eq_def", "eq_der", "eq_orphan"],
        linked_claim_ids=["claim_def", "claim_der"],
        linked_evidence_ids=["ev_def", "ev_der"],
        evidence_refs={"equation_ids": ["eq_def", "eq_der", "eq_orphan"]},
        split_recommendation=_split_rec(
            _suggested("Definition", "definition"),
            _suggested("Derivation", "derivation"),
        ),
    )
    llm_input = _LLMInput(
        equations=[
            {"equation_id": "eq_def", "role": "definition"},
            {"equation_id": "eq_der", "role": "transformation"},
            {"equation_id": "eq_orphan", "role": ""},  # no category → unassigned
        ],
        claims=[
            {"claim_id": "claim_def", "is_atomic": True, "atomicity": "atomic",
             "equation_ids": ["eq_def"], "source_evidence_ids": ["ev_def"], "concepts": ["alpha"]},
            {"claim_id": "claim_der", "is_atomic": True, "atomicity": "atomic",
             "equation_ids": ["eq_der"], "source_evidence_ids": ["ev_der"], "concepts": ["beta"]},
        ],
    )
    refined = REFINER.refine(_result([component]), llm_input=llm_input, derivations=None)

    definition, derivation = refined.components
    assert definition.linked_equation_ids == ["eq_def"]
    assert derivation.linked_equation_ids == ["eq_der"]
    # No equation is duplicated across children.
    assert set(definition.linked_equation_ids) & set(derivation.linked_equation_ids) == set()
    assert definition.linked_claim_ids == ["claim_def"]
    assert derivation.linked_claim_ids == ["claim_der"]
    assert definition.linked_evidence_ids == ["ev_def"]
    assert derivation.linked_evidence_ids == ["ev_der"]

    unassigned = refined.component_refinement["refinement_validation"]["unassigned_links"]
    assert any(u["link_id"] == "eq_orphan" and u["link_type"] == "equation" for u in unassigned)


# ---------------------------------------------------------------------------
# 3a. Responsibility / equation-role consistency (#421)
# ---------------------------------------------------------------------------

def test_equations_routed_to_responsibility_matching_their_role():
    # A definition equation must land in the definition child and a result/
    # constraint equation in the constraint child — driven by the equation ROLE,
    # never round-robin (#421).
    component = _component(
        component_id="comp_roleconsistency",
        linked_equation_ids=["eq_def", "eq_con"],
        split_recommendation=_split_rec(
            _suggested("Definition", "definition"),
            _suggested("Constraint", "constraint"),
        ),
    )
    llm_input = _LLMInput(equations=[
        {"equation_id": "eq_def", "role": "definition"},
        {"equation_id": "eq_con", "role": "constraint"},
    ])
    refined = REFINER.refine(_result([component]), llm_input=llm_input, derivations=None)

    definition = next(c for c in refined.components if c.responsibility_type == "definition")
    constraint = next(c for c in refined.components if c.responsibility_type == "constraint")
    assert definition.linked_equation_ids == ["eq_def"]
    assert constraint.linked_equation_ids == ["eq_con"]
    # The definition equation never leaks into the constraint child and vice versa.
    assert "eq_def" not in constraint.linked_equation_ids
    assert "eq_con" not in definition.linked_equation_ids
    # Role-classified buckets agree with the assignment.
    assert definition.definition_equation_ids == ["eq_def"]
    assert "eq_def" not in (constraint.definition_equation_ids or [])


def test_unrouteable_equation_is_unassigned_not_round_robined():
    # An equation whose role matches none of the suggested responsibilities is
    # left in unassigned_links, never forced onto an arbitrary child (#421).
    component = _component(
        component_id="comp_orphan_role",
        linked_equation_ids=["eq_def", "eq_con", "eq_unknown"],
        split_recommendation=_split_rec(
            _suggested("Definition", "definition"),
            _suggested("Constraint", "constraint"),
        ),
    )
    llm_input = _LLMInput(equations=[
        {"equation_id": "eq_def", "role": "definition"},
        {"equation_id": "eq_con", "role": "constraint"},
        {"equation_id": "eq_unknown", "role": ""},
    ])
    refined = REFINER.refine(_result([component]), llm_input=llm_input, derivations=None)

    for child in refined.components:
        assert "eq_unknown" not in child.linked_equation_ids
    unassigned = refined.component_refinement["refinement_validation"]["unassigned_links"]
    assert any(
        u["link_id"] == "eq_unknown" and u["link_type"] == "equation" for u in unassigned
    )


def test_claim_routed_by_type_when_no_equation_overlap():
    # A claim sharing no equations with any child is routed by its claim TYPE,
    # not dropped onto the first child (#421).
    component = _component(
        component_id="comp_claimtype",
        linked_equation_ids=["eq_def", "eq_con"],
        linked_claim_ids=["claim_typed"],
        split_recommendation=_split_rec(
            _suggested("Definition", "definition"),
            _suggested("Constraint", "constraint"),
        ),
    )
    llm_input = _LLMInput(
        equations=[
            {"equation_id": "eq_def", "role": "definition"},
            {"equation_id": "eq_con", "role": "constraint"},
        ],
        claims=[
            {"claim_id": "claim_typed", "is_atomic": True, "atomicity": "atomic",
             "claim_type": "constraint", "equation_ids": [], "concepts": ["gamma"]},
        ],
    )
    refined = REFINER.refine(_result([component]), llm_input=llm_input, derivations=None)

    constraint = next(c for c in refined.components if c.responsibility_type == "constraint")
    definition = next(c for c in refined.components if c.responsibility_type == "definition")
    assert "claim_typed" in constraint.linked_claim_ids
    assert "claim_typed" not in definition.linked_claim_ids


def test_constraint_equation_not_misrouted_to_application_child():
    # #421 review (P1): constraint and application share the coarse "result"
    # category. With both children present and responsibilities sorted
    # (application < constraint), the first-match rule would mis-route the
    # constraint equation/claim into the application child. The exact role
    # responsibility must keep the constraint equation in the constraint child.
    component = _component(
        component_id="comp_result_pair",
        linked_equation_ids=["eq_con", "eq_ambig"],
        linked_claim_ids=["claim_con", "claim_app"],
        split_recommendation=_split_rec(
            _suggested("Application", "application"),
            _suggested("Constraint", "constraint"),
        ),
    )
    llm_input = _LLMInput(
        equations=[
            {"equation_id": "eq_con", "role": "constraint"},
            {"equation_id": "eq_ambig", "role": "result"},
        ],
        claims=[
            {"claim_id": "claim_con", "is_atomic": True, "atomicity": "atomic",
             "claim_type": "constraint", "equation_ids": [], "concepts": ["c"]},
            {"claim_id": "claim_app", "is_atomic": True, "atomicity": "atomic",
             "claim_type": "application", "equation_ids": [], "concepts": ["a"]},
        ],
    )
    refined = REFINER.refine(_result([component]), llm_input=llm_input, derivations=None)

    constraint = next(c for c in refined.components if c.responsibility_type == "constraint")
    application = next(c for c in refined.components if c.responsibility_type == "application")
    # The constraint equation stays in the constraint child, not application.
    assert "eq_con" in constraint.linked_equation_ids
    assert "eq_con" not in application.linked_equation_ids
    # Each claim is routed by its exact type to the matching child.
    assert "claim_con" in constraint.linked_claim_ids
    assert "claim_app" in application.linked_claim_ids
    assert "claim_con" not in application.linked_claim_ids
    # eq_ambig (bare "result" role) is ambiguous between the two result-category
    # children, so it is left unassigned rather than guessed onto application.
    unassigned = refined.component_refinement["refinement_validation"]["unassigned_links"]
    assert any(
        u["link_id"] == "eq_ambig" and u["link_type"] == "equation" for u in unassigned
    )


def test_evidence_routed_by_equation_provenance_when_no_claim_cites_it():
    # #421 review (P2): an evidence item referenced only by an assigned equation
    # (no claim cites it) must be routed to that equation's child via equation
    # provenance, not dropped as unassigned.
    component = _component(
        component_id="comp_eqprov",
        linked_equation_ids=["eq_def", "eq_der"],
        linked_evidence_ids=["ev_from_eq"],
        evidence_refs={"equation_ids": ["eq_def", "eq_der"], "evidence_ids": ["ev_from_eq"]},
        split_recommendation=_split_rec(
            _suggested("Definition", "definition"),
            _suggested("Derivation", "derivation"),
        ),
    )
    llm_input = _LLMInput(equations=[
        {"equation_id": "eq_def", "role": "definition", "source_evidence_ids": ["ev_from_eq"]},
        {"equation_id": "eq_der", "role": "transformation"},
    ])
    refined = REFINER.refine(_result([component]), llm_input=llm_input, derivations=None)

    definition = next(c for c in refined.components if c.responsibility_type == "definition")
    derivation = next(c for c in refined.components if c.responsibility_type == "derivation")
    # The evidence follows its equation (eq_def → definition child).
    assert "ev_from_eq" in definition.linked_evidence_ids
    assert "ev_from_eq" not in derivation.linked_evidence_ids
    unassigned = refined.component_refinement["refinement_validation"]["unassigned_links"]
    assert not any(u["link_id"] == "ev_from_eq" for u in unassigned)


def test_shared_evidence_is_linked_to_every_explicit_provenance_child():
    component = _component(
        component_id="comp_shared_ev",
        linked_equation_ids=["eq_def", "eq_con"],
        linked_evidence_ids=["ev_shared"],
        evidence_refs={
            "equation_ids": ["eq_def", "eq_con"],
            "evidence_ids": ["ev_shared"],
        },
        split_recommendation=_split_rec(
            _suggested("Definition", "definition"),
            _suggested("Constraint", "constraint"),
        ),
    )
    llm_input = _LLMInput(equations=[
        {"equation_id": "eq_def", "role": "definition",
         "source_evidence_ids": ["ev_shared"]},
        {"equation_id": "eq_con", "role": "constraint",
         "source_evidence_ids": ["ev_shared"]},
    ])

    refined = REFINER.refine(_result([component]), llm_input=llm_input, derivations=None)

    definition = next(c for c in refined.components if c.responsibility_type == "definition")
    constraint = next(c for c in refined.components if c.responsibility_type == "constraint")
    assert definition.linked_evidence_ids == ["ev_shared"]
    assert constraint.linked_evidence_ids == ["ev_shared"]


def test_low_confidence_ambiguous_equation_is_rejudged_by_constrained_llm():
    component = _component(
        component_id="comp_llm_route",
        linked_equation_ids=["eq_def", "eq_result"],
        split_recommendation=_split_rec(
            _suggested("Definition", "definition"),
            _suggested("Constraint", "constraint"),
        ),
    )
    llm_input = _LLMInput(equations=[
        {"equation_id": "eq_def", "role": "definition"},
        {
            "equation_id": "eq_result",
            "role": "result",
            "plain_text": "The parameters must satisfy this consistency condition.",
            "confidence": 0.55,
            "semantic_status": "context_inferred",
            "review_flags": ["low_confidence"],
        },
    ])
    routing_llm = _RoutingLLM([{
        "artifact_type": "equation",
        "artifact_id": "eq_result",
        "selected_responsibility": "constraint",
        "confidence": 0.91,
        "reason": "The equation states a consistency condition.",
    }])

    refined = ComponentRefiner(llm_client=routing_llm).refine(
        _result([component]), llm_input=llm_input, derivations=None
    )

    constraint = next(c for c in refined.components if c.responsibility_type == "constraint")
    assert constraint.linked_equation_ids == ["eq_result"]
    assert len(routing_llm.calls) == 1
    mapping = refined.component_refinement["component_refinement_records"][0][
        "split_candidate_mapping"
    ]
    decision = next(
        d
        for candidate in mapping
        for d in candidate["routing_decisions"]
        if d["artifact_id"] == "eq_result"
    )
    assert decision["method"] == "llm_rejudgement"


def test_low_confidence_or_candidate_outside_llm_answer_stays_unassigned():
    component = _component(
        component_id="comp_llm_reject",
        linked_equation_ids=["eq_def", "eq_con", "eq_result"],
        split_recommendation=_split_rec(
            _suggested("Definition", "definition"),
            _suggested("Constraint", "constraint"),
        ),
    )
    llm_input = _LLMInput(equations=[
        {"equation_id": "eq_def", "role": "definition"},
        {"equation_id": "eq_con", "role": "constraint"},
        {
            "equation_id": "eq_result",
            "role": "result",
            "confidence": 0.4,
            "semantic_status": "unknown",
            "review_flags": ["low_confidence"],
        },
    ])
    routing_llm = _RoutingLLM([{
        "artifact_type": "equation",
        "artifact_id": "eq_result",
        "selected_responsibility": "application",
        "confidence": 0.95,
        "reason": "Invalid candidate for this split.",
    }])

    refined = ComponentRefiner(llm_client=routing_llm).refine(
        _result([component]), llm_input=llm_input, derivations=None
    )

    unassigned = refined.component_refinement["refinement_validation"]["unassigned_links"]
    assert any(
        row["link_type"] == "equation" and row["link_id"] == "eq_result"
        for row in unassigned
    )


def test_equal_equation_overlap_is_rejudged_instead_of_choosing_first_child():
    component = _component(
        component_id="comp_overlap_tie",
        linked_equation_ids=["eq_def", "eq_con"],
        linked_claim_ids=["claim_both"],
        split_recommendation=_split_rec(
            _suggested("Definition", "definition"),
            _suggested("Constraint", "constraint"),
        ),
    )
    llm_input = _LLMInput(
        equations=[
            {"equation_id": "eq_def", "role": "definition"},
            {"equation_id": "eq_con", "role": "constraint"},
        ],
        claims=[{
            "claim_id": "claim_both",
            "claim_type": "constraint",
            "text": "The defined quantity must satisfy the stated condition.",
            "equation_ids": ["eq_def", "eq_con"],
            "confidence": 0.95,
            "support_status": "source_backed",
            "review_status": "auto_accepted",
            "atomicity": "atomic",
            "is_atomic": True,
        }],
    )
    routing_llm = _RoutingLLM([{
        "artifact_type": "claim",
        "artifact_id": "claim_both",
        "selected_responsibility": "constraint",
        "confidence": 0.9,
        "reason": "The proposition asserts a constraint, despite equal equation overlap.",
    }])

    refined = ComponentRefiner(llm_client=routing_llm).refine(
        _result([component]), llm_input=llm_input, derivations=None
    )

    definition = next(c for c in refined.components if c.responsibility_type == "definition")
    constraint = next(c for c in refined.components if c.responsibility_type == "constraint")
    assert "claim_both" not in definition.linked_claim_ids
    assert "claim_both" in constraint.linked_claim_ids


def test_derivation_routed_by_operation_family():
    # With two derivational-capable children, a derivation is routed by the
    # operation family of its steps, not to a fixed "first derivational child".
    from episteme_graph.agents.derivation_chain.schema import (
        DerivationChainRecord,
        DerivationChainResult,
        DerivationStep,
    )

    component = _component(
        component_id="comp_derfamily",
        linked_equation_ids=["eq_sys", "eq_res"],
        linked_derivation_ids=["d_constraint"],
        split_recommendation=_split_rec(
            _suggested("Equation system", "equation_system"),
            _suggested("Constraint", "constraint"),
        ),
    )
    llm_input = _LLMInput(equations=[
        {"equation_id": "eq_sys", "role": "transformation"},
        {"equation_id": "eq_res", "role": "constraint"},
    ])
    derivations = DerivationChainResult(
        document_id="doc",
        cartridge_id=None,
        chains=[
            DerivationChainRecord(
                derivation_id="d_constraint",
                document_id="doc",
                source_section_ids=["s1"],
                steps=[
                    DerivationStep(
                        step_id="s0",
                        input_equation_ids=[],
                        operation="evaluate the constraint condition",
                        output_equation_ids=[],
                        confidence=0.95,
                        review_status="auto_accepted",
                    ),
                ],
                linked_component_ids=[],
                review_status="auto_accepted",
                confidence=0.95,
                review_required=False,
            )
        ],
    )
    refined = REFINER.refine(_result([component]), llm_input=llm_input, derivations=derivations)

    constraint = next(c for c in refined.components if c.responsibility_type == "constraint")
    eqsys = next(c for c in refined.components if c.responsibility_type == "equation_system")
    assert constraint.linked_derivation_ids == ["d_constraint"]
    assert eqsys.linked_derivation_ids == []


# ---------------------------------------------------------------------------
# 3b. Derivation link redistribution (#324 rule 4)
# ---------------------------------------------------------------------------

def test_derivation_links_redistributed_to_derivational_child():
    component = _component(
        component_id="comp_derlinks",
        linked_equation_ids=["eq_def", "eq_der"],
        linked_derivation_ids=["d_a", "d_b"],
        split_recommendation=_split_rec(
            _suggested("Definition", "definition"),
            _suggested("Derivation", "derivation"),
        ),
    )
    llm_input = _LLMInput(equations=[
        {"equation_id": "eq_def", "role": "definition"},
        {"equation_id": "eq_der", "role": "transformation"},
    ])
    routing_llm = _RoutingLLM([
        {
            "artifact_type": "derivation",
            "artifact_id": derivation_id,
            "selected_responsibility": "derivation",
            "confidence": 0.9,
            "reason": "The only derivational responsibility is the derivation child.",
        }
        for derivation_id in ("d_a", "d_b")
    ])
    refined = ComponentRefiner(llm_client=routing_llm).refine(
        _result([component]), llm_input=llm_input, derivations=None
    )

    definition = next(c for c in refined.components if c.responsibility_type == "definition")
    derivation = next(c for c in refined.components if c.responsibility_type == "derivation")
    # Derivation links go to the derivational child only, not copied to both.
    assert derivation.linked_derivation_ids == ["d_a", "d_b"]
    assert definition.linked_derivation_ids == []


def test_unassigned_derivation_link_reported_not_dropped():
    # definition + application: the split happens (both children carry equations),
    # but neither child is derivational, so the derivation link has no home and
    # must be reported instead of silently dropped.
    component = _component(
        component_id="comp_unassign_der",
        linked_equation_ids=["eq_def", "eq_app"],
        linked_derivation_ids=["d_orphan"],
        split_recommendation=_split_rec(
            _suggested("Definition", "definition"),
            _suggested("Application", "application"),
        ),
    )
    llm_input = _LLMInput(equations=[
        {"equation_id": "eq_def", "role": "definition"},
        {"equation_id": "eq_app", "role": "result"},
    ])
    refined = REFINER.refine(_result([component]), llm_input=llm_input, derivations=None)

    # The split still produced two single-responsibility children.
    assert [c.responsibility_type for c in refined.components] == ["definition", "application"]
    # The derivation link was not assigned to any child.
    for child in refined.components:
        assert "d_orphan" not in child.linked_derivation_ids
    unassigned = refined.component_refinement["refinement_validation"]["unassigned_links"]
    assert any(
        u["link_id"] == "d_orphan" and u["link_type"] == "derivation" for u in unassigned
    )


# ---------------------------------------------------------------------------
# 4. Low-confidence equation propagation
# ---------------------------------------------------------------------------

def test_low_confidence_equation_forces_child_review_required():
    component = _component(
        component_id="comp_lowconf",
        linked_equation_ids=["eq_def", "eq_blocked"],
        split_recommendation=_split_rec(
            _suggested("Definition", "definition"),
            _suggested("Derivation", "derivation"),
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
                "allowed_downstream_use": "semantic_hint_only",
            },
        },
    ])
    routing_llm = _RoutingLLM([{
        "artifact_type": "equation",
        "artifact_id": "eq_blocked",
        "selected_responsibility": "derivation",
        "confidence": 0.9,
        "reason": "The equation is a transformation despite its restricted downstream use.",
    }])
    refined = ComponentRefiner(llm_client=routing_llm).refine(
        _result([component]), llm_input=llm_input, derivations=None
    )

    derivation = next(c for c in refined.components if c.responsibility_type == "derivation")
    assert "eq_blocked" in derivation.linked_equation_ids
    assert derivation.review_status == "review_required"
    assert "eq_blocked" in derivation.review_required_equation_ids
    assert "comp_lowconf" in refined.component_refinement["refinement_validation"]["review_required_refinements"]


# ---------------------------------------------------------------------------
# 5. Composite claim handling
# ---------------------------------------------------------------------------

def test_composite_claim_marks_child_review_required():
    component = _component(
        component_id="comp_composite",
        linked_equation_ids=["eq_def", "eq_der"],
        linked_claim_ids=["claim_atomic", "claim_composite"],
        split_recommendation=_split_rec(
            _suggested("Definition", "definition"),
            _suggested("Derivation", "derivation"),
        ),
    )
    llm_input = _LLMInput(
        equations=[
            {"equation_id": "eq_def", "role": "definition"},
            {"equation_id": "eq_der", "role": "transformation"},
        ],
        claims=[
            {"claim_id": "claim_atomic", "is_atomic": True, "atomicity": "atomic",
             "equation_ids": ["eq_def"], "concepts": ["alpha"]},
            {"claim_id": "claim_composite", "is_atomic": False, "atomicity": "split_pending",
             "equation_ids": ["eq_der"], "concepts": ["beta"]},
        ],
    )
    refined = REFINER.refine(_result([component]), llm_input=llm_input, derivations=None)

    definition = next(c for c in refined.components if c.responsibility_type == "definition")
    derivation = next(c for c in refined.components if c.responsibility_type == "derivation")
    assert definition.review_status == "source_backed"
    assert derivation.review_status == "review_required"
    assert any("composite" in n for n in derivation.review_notes)


# ---------------------------------------------------------------------------
# 6. Concept recomputation
# ---------------------------------------------------------------------------

def test_concepts_recomputed_per_child_not_copied_wholesale():
    component = _component(
        component_id="comp_concepts",
        linked_equation_ids=["eq_def", "eq_der"],
        linked_claim_ids=["claim_def", "claim_der"],
        concepts=["alpha", "beta", "gamma"],
        prerequisite_concepts=["alpha"],
        split_recommendation=_split_rec(
            _suggested("Definition", "definition"),
            _suggested("Derivation", "derivation"),
        ),
    )
    llm_input = _LLMInput(
        equations=[
            {"equation_id": "eq_def", "role": "definition"},
            {"equation_id": "eq_der", "role": "transformation"},
        ],
        claims=[
            {"claim_id": "claim_def", "is_atomic": True, "atomicity": "atomic",
             "equation_ids": ["eq_def"], "concepts": ["alpha"]},
            {"claim_id": "claim_der", "is_atomic": True, "atomicity": "atomic",
             "equation_ids": ["eq_der"], "concepts": ["beta"]},
        ],
    )
    refined = REFINER.refine(_result([component]), llm_input=llm_input, derivations=None)

    definition, derivation = refined.components
    assert definition.concepts == ["alpha"]
    assert derivation.concepts == ["beta"]
    # gamma (a broad parent-only concept) is not copied onto either child.
    assert "gamma" not in definition.concepts + derivation.concepts
    # introduced vs reused recomputed by child order.
    assert definition.introduced_concepts == ["alpha"]
    assert derivation.introduced_concepts == ["beta"]
    # prerequisite recomputed: alpha is a parent prerequisite present in the definition child.
    assert definition.prerequisite_concepts == ["alpha"]


def test_concepts_recomputed_from_equation_symbols():
    # No claims at all: child concepts must be derived from the equations'
    # defined/used symbols, redistributed per responsibility.
    component = _component(
        component_id="comp_eqsym",
        linked_equation_ids=["eq_def", "eq_der"],
        concepts=["broad_parent_concept"],
        split_recommendation=_split_rec(
            _suggested("Definition", "definition"),
            _suggested("Derivation", "derivation"),
        ),
    )
    llm_input = _LLMInput(equations=[
        {"equation_id": "eq_def", "role": "definition", "defined_symbols": [{"symbol": "sigma"}]},
        {"equation_id": "eq_der", "role": "transformation", "defined_symbols": [{"symbol": "b_2"}],
         "used_symbols": ["delta"]},
    ])
    refined = REFINER.refine(_result([component]), llm_input=llm_input, derivations=None)

    definition = next(c for c in refined.components if c.responsibility_type == "definition")
    derivation = next(c for c in refined.components if c.responsibility_type == "derivation")
    assert definition.concepts == ["sigma"]
    assert set(derivation.concepts) == {"b_2", "delta"}
    # The broad parent-only concept is not copied onto either child.
    assert "broad_parent_concept" not in definition.concepts + derivation.concepts


def test_non_atomic_claim_concepts_not_treated_as_confirmed():
    # A composite (non-atomic) claim's concepts must NOT become a child's
    # confirmed concepts; only atomic-claim and equation-symbol concepts do.
    component = _component(
        component_id="comp_lowconf_concept",
        linked_equation_ids=["eq_def", "eq_der"],
        linked_claim_ids=["claim_atomic", "claim_composite"],
        split_recommendation=_split_rec(
            _suggested("Definition", "definition"),
            _suggested("Derivation", "derivation"),
        ),
    )
    llm_input = _LLMInput(
        equations=[
            {"equation_id": "eq_def", "role": "definition"},
            {"equation_id": "eq_der", "role": "transformation"},
        ],
        claims=[
            {"claim_id": "claim_atomic", "is_atomic": True, "atomicity": "atomic",
             "equation_ids": ["eq_def"], "concepts": ["confirmed_concept"]},
            {"claim_id": "claim_composite", "is_atomic": False, "atomicity": "split_pending",
             "equation_ids": ["eq_der"], "concepts": ["unconfirmed_concept"]},
        ],
    )
    refined = REFINER.refine(_result([component]), llm_input=llm_input, derivations=None)

    definition = next(c for c in refined.components if c.responsibility_type == "definition")
    derivation = next(c for c in refined.components if c.responsibility_type == "derivation")
    assert "confirmed_concept" in definition.concepts
    # The composite claim was assigned to the derivation child, but its concept
    # is not promoted to a confirmed concept there.
    assert "claim_composite" in derivation.linked_claim_ids
    assert "unconfirmed_concept" not in derivation.concepts


# ---------------------------------------------------------------------------
# 7. Teaching granularity
# ---------------------------------------------------------------------------

def test_teaching_granularity_flags_dense_child():
    dense_eqs = [f"eq_def_{i}" for i in range(6)]
    component = _component(
        component_id="comp_dense",
        linked_equation_ids=dense_eqs + ["eq_der"],
        split_recommendation=_split_rec(
            _suggested("Big definitions", "definition"),
            _suggested("Derivation", "derivation"),
        ),
    )
    llm_input = _LLMInput(equations=(
        [{"equation_id": e, "role": "definition"} for e in dense_eqs]
        + [{"equation_id": "eq_der", "role": "transformation"}]
    ))
    refined = REFINER.refine(_result([component]), llm_input=llm_input, derivations=None)

    dense = next(c for c in refined.components if c.responsibility_type == "definition")
    assert dense.teaching_granularity["teachable_as_single_unit"] is False
    assert dense.teaching_granularity["split_if_too_dense"] is True
    warnings = refined.component_refinement["refinement_validation"]["teaching_granularity_warnings"]
    assert any(w["component_id"] == dense.component_id for w in warnings)


def test_teaching_granularity_good_for_compact_unit():
    component = _component(linked_equation_ids=[], teaching_takeaway="A compact, specific takeaway sentence.")
    granularity = _teaching_granularity(component, {})
    assert granularity["teachable_as_single_unit"] is True
    assert granularity["split_if_too_dense"] is False
    assert granularity["teaching_takeaway_quality"] == "good"
    assert granularity["estimated_slide_count"] == 1


# ---------------------------------------------------------------------------
# 8. Graph rewiring
# ---------------------------------------------------------------------------

def test_graph_rewires_dependent_edges_to_first_child():
    parent = _component(
        component_id="comp_parent",
        linked_equation_ids=["eq_def", "eq_der"],
        split_recommendation=_split_rec(
            _suggested("Definition", "definition"),
            _suggested("Derivation", "derivation"),
        ),
    )
    dependent = _component(
        component_id="comp_dependent",
        component_type="ClaimBundleComponent",
        split_recommendation={"required": False, "suggested_components": []},
        dependencies=[{
            "dependency_type": "depends_on",
            "component_refs": ["comp_parent"],
            "reason": "uses parent result",
        }],
    )
    llm_input = _LLMInput(equations=[
        {"equation_id": "eq_def", "role": "definition"},
        {"equation_id": "eq_der", "role": "transformation"},
    ])
    refined = REFINER.refine(_result([parent, dependent]), llm_input=llm_input, derivations=None)

    dep = next(c for c in refined.components if c.component_id == "comp_dependent")
    assert dep.dependencies[0]["component_refs"] == ["comp_parent__r1"]

    graph = refined.component_refinement["component_graph_updates"]
    assert {"from": "comp_parent", "to": "comp_parent__r1"} in graph["rewired_edges"]
    assert "comp_parent" in graph["removed_nodes"]
    assert graph["unresolved_edges"] == []


def test_split_required_but_unsplittable_marks_review_required():
    # Only one usable suggested responsibility → cannot split → review_required.
    component = _component(
        component_id="comp_single_resp",
        component_type="ClaimBundleComponent",
        linked_equation_ids=["eq_def"],
        split_recommendation=_split_rec(_suggested("Only definition", "definition")),
    )
    llm_input = _LLMInput(equations=[{"equation_id": "eq_def", "role": "definition"}])
    refined = REFINER.refine(_result([component]), llm_input=llm_input, derivations=None)

    assert [c.component_id for c in refined.components] == ["comp_single_resp"]
    record = refined.component_refinement["component_refinement_records"][0]
    assert record["refinement_status"] == "review_required"
    assert "insufficient_split_plan" in record["review_reasons"]
    assert "comp_single_resp" in refined.component_refinement["refinement_validation"]["review_required_refinements"]
