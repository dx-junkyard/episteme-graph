"""Regressions from the radar autonomous loop, brain W1 (2610.08751, seq 3283/3289).

- IK-0765: result / diagnostic / limitation components (no equations, never split)
  vanished from component_graph's edge candidates because the refiner's children
  filled the head of a 32-item cap.
- IK-0728 / IK-0755: the same (operation, input equations) step was duplicated into
  several parents, ``Unknown specific operation`` reached component names, and the
  LLM's summary was replaced by an English machine sentence with ``(#324)``.
- IK-0792: routing reasons said "low-confidence" while carrying confidence 0.85.
- claim types offered by the claim_qualification prompt (relation /
  diagnostic_claim / correction / uncertainty) were rounded to ``unknown``.
- accepted_claims were all from the result sections; the input was 404KB.
"""
from __future__ import annotations

from episteme_graph.agents.claim_object_builder.builder import ClaimObjectBuilder
from episteme_graph.agents.claim_object_builder.schema import (
    ClaimObjectBuildResult,
    ClaimObjectRecord,
)
from episteme_graph.agents.claim_qualification.schema import (
    CORE_CLAIM_TYPE_FALLBACKS,
    ClaimQualificationResult,
)
from episteme_graph.agents.claim_selection import select_claim_rows
from episteme_graph.agents.component_assembly import component_refiner as refiner_mod
from episteme_graph.agents.component_assembly.component_refiner import ComponentRefiner
from episteme_graph.agents.component_assembly.input_builder import (
    ComponentAssemblyInputBuilder,
)
from episteme_graph.agents.component_assembly.prompt import ComponentAssemblyPromptFactory
from episteme_graph.agents.component_assembly.schema import (
    COMPONENTS_VERSION,
    ComponentAssemblyResult,
    ComponentRecord,
)
from episteme_graph.agents.component_graph.input_builder import (
    ComponentGraphInputBuilder,
    select_components_within_limit,
)
from episteme_graph.agents.derivation_chain.schema import (
    DerivationChainRecord,
    DerivationChainResult,
    DerivationStep,
)


def _component(component_id: str, **kwargs) -> ComponentRecord:
    defaults = dict(
        component_id=component_id,
        component_type="RelationComponent",
        label=f"Theory unit {component_id}",
        summary="ラプラス近似した周辺事後分布を χ² で書き直す。",
        inputs=[],
        outputs=[],
        preconditions=[],
        cautions=[],
        dependencies=[],
        evidence_refs={"claim_ids": [], "equation_ids": []},
        reason="llm",
        confidence=0.8,
        review_notes=[],
        internal_flow=[],
        linked_claim_ids=[],
        review_status="auto_accepted",
        teaching_takeaway="周辺化で迷惑パラメータを消す。",
    )
    defaults.update(kwargs)
    return ComponentRecord(**defaults)


def _result(components) -> ComponentAssemblyResult:
    return ComponentAssemblyResult(
        document_id="doc",
        components_version=COMPONENTS_VERSION,
        cartridge_id=None,
        components=components,
        assembly_hints=[],
        review_notes=[],
        confidence=0.8,
    )


_SHARED_INPUTS = [f"eq_s{i}" for i in range(1, 13)]


def _shared_step_fixture():
    steps = [
        DerivationStep(step_id="s1", input_equation_ids=[], operation="define the posterior",
                       output_equation_ids=["eq_a1"]),
        DerivationStep(step_id="s2", input_equation_ids=["eq_a1"], operation="derive the marginal",
                       output_equation_ids=["eq_a2"]),
        DerivationStep(step_id="s3", input_equation_ids=["eq_a2"], operation="frobnicate quantity",
                       output_equation_ids=["eq_a3"]),
        DerivationStep(step_id="s4", input_equation_ids=[], operation="define the prior",
                       output_equation_ids=["eq_b1"]),
        DerivationStep(step_id="s5", input_equation_ids=["eq_b1"], operation="evaluate the condition",
                       output_equation_ids=["eq_b2"]),
        # One system-level step with a dozen inputs shared by both parents.
        DerivationStep(step_id="s6", input_equation_ids=list(_SHARED_INPUTS),
                       operation="derive the residual", output_equation_ids=["eq_s13"]),
    ]
    chain = DerivationChainRecord(
        derivation_id="deriv_1",
        document_id="doc",
        source_section_ids=["sec_3"],
        steps=steps,
        linked_component_ids=["comp_a", "comp_b"],
    )
    comp_a = _component(
        "comp_a",
        linked_equation_ids=["eq_a1", "eq_a2", "eq_a3", *_SHARED_INPUTS[:6]],
        linked_derivation_ids=["deriv_1"],
    )
    comp_b = _component(
        "comp_b",
        label="Jeffreys prior cancels Laplace term",
        linked_equation_ids=["eq_b1", "eq_b2", *_SHARED_INPUTS, "eq_s13"],
        linked_derivation_ids=["deriv_1"],
    )
    derivations = DerivationChainResult(document_id="doc", cartridge_id=None, chains=[chain])
    return [comp_a, comp_b], derivations


# --------------------------------------------------------------------- IK-0728


def test_shared_step_becomes_a_child_of_one_parent_only():
    components, derivations = _shared_step_fixture()
    result = ComponentRefiner().refine(_result(components), derivations=derivations)
    holders = [
        c.component_id for c in result.components
        if set(_SHARED_INPUTS) & set(c.input_equation_ids + c.linked_equation_ids)
        and "eq_s13" in (c.linked_equation_ids or [])
    ]
    # Before the fix both comp_a and comp_b got a "Derive: …" child with the
    # 12 shared inputs; now only the owner (most shared equations) has it.
    assert len(holders) == 1
    assert holders[0].startswith("comp_b")


def test_unknown_operation_never_names_a_child():
    components, derivations = _shared_step_fixture()
    result = ComponentRefiner().refine(_result(components), derivations=derivations)
    for component in result.components:
        assert "unknown specific operation" not in component.label.lower()
        assert component.operation != "unknown_specific_operation"


# --------------------------------------------------------------------- IK-0755


def test_split_children_keep_the_llm_summary_and_put_machine_text_in_reason():
    components, derivations = _shared_step_fixture()
    parents = {c.component_id: c.summary for c in components}
    result = ComponentRefiner().refine(_result(components), derivations=derivations)
    children = [c for c in result.components if "__op" in c.component_id]
    assert children, "fixture must split"
    for child in children:
        parent_id = child.component_id.split("__", 1)[0]
        assert child.summary == parents[parent_id]
        for machine in ("(#324)", "equation step(s)", "Reusable theory unit"):
            assert machine not in child.summary
            assert machine not in child.teaching_takeaway
        assert "Reusable theory unit" in child.reason


# --------------------------------------------------------------------- IK-0792


def test_rejudge_reason_names_the_failed_check_not_low_confidence():
    eq = {
        "confidence": 0.85,
        "semantic_status": "source_backed",
        "review_flags": ["missing_label"],
        "needs_math_review": False,
        "reconstruction_status": "none",
    }
    assert not refiner_mod._equation_is_high_confidence(eq)
    reason = refiner_mod._rejudge_reason(
        "equation semantics", refiner_mod._equation_rejudge_checks(eq)
    )
    assert "low-confidence" not in reason
    assert "missing_label" in reason
    assert "confidence below" not in reason

    claim = {"confidence": 0.9, "support_status": "source_backed",
             "review_status": "teacher_review_required"}
    claim_reason = refiner_mod._rejudge_reason(
        "claim classification", refiner_mod._claim_rejudge_checks(claim)
    )
    assert "teacher_review_required" in claim_reason
    assert "confidence below" not in claim_reason


# --------------------------------------------------------------------- IK-0765


def test_graph_input_keeps_every_original_parent_within_the_cap():
    comps = []
    for p in range(1, 13):  # 12 split parents → 32 children
        n = 3 if p <= 8 else 2
        comps += [_component(f"comp_{p:03d}__op{i}") for i in range(1, n + 1)]
    assert len(comps) == 32
    comps += [_component(f"comp_{p:03d}", component_type="ClaimBundleComponent") for p in range(13, 19)]

    selected, dropped = select_components_within_limit(comps, 32)
    parents = {c.component_id.split("__", 1)[0] for c in selected}
    assert parents == {f"comp_{p:03d}" for p in range(1, 19)}
    assert len(selected) == 32 and len(dropped) == 6
    # original order kept
    order = [c.component_id for c in comps]
    assert [c.component_id for c in selected] == [cid for cid in order if cid not in dropped]

    builder = ComponentGraphInputBuilder()
    llm_input = builder.build(_result(comps))
    assert "comp_018" in llm_input.component_ids
    coverage = builder.last_component_coverage
    assert coverage["truncated"] == 6
    assert coverage["reasons"] == ["max_components"]
    assert sorted(coverage["details"]["dropped_component_ids"]) == sorted(dropped)


def test_refiner_keeps_equation_free_result_components():
    components, derivations = _shared_step_fixture()
    results = [
        _component(f"comp_{i:03d}", component_type=t, label=f"Result {i}")
        for i, t in ((13, "ClaimBundleComponent"), (15, "DiagnosticComponent"),
                     (17, "UncertaintyComponent"))
    ]
    out = ComponentRefiner().refine(_result(components + results), derivations=derivations)
    ids = {c.component_id for c in out.components}
    assert {"comp_013", "comp_015", "comp_017"} <= ids


# ---------------------------------------------------------- claim type vocabulary


def test_every_prompt_claim_type_survives_claim_object_normalization():
    for value in CORE_CLAIM_TYPE_FALLBACKS:
        normalized = ClaimObjectBuilder._normalize_claim_type(value)
        if value == "unknown":
            continue
        assert normalized != "unknown", value
    for value in ("relation", "diagnostic_claim", "correction", "uncertainty"):
        assert ClaimObjectBuilder._normalize_claim_type(value) == value


# ------------------------------------------------------------ section stratification


def _row(cid, section, tier):
    return {"claim_id": cid, "section_id": section, "claim_tier": tier,
            "confidence": 0.9, "text": f"claim {cid}"}


def test_accepted_claim_selection_is_stratified_by_section():
    rows = [_row(f"f{i}", "sec_3", "paper_supporting") for i in range(10)]
    rows += [_row(f"r{s}_{i}", f"sec_{s}", "paper_core") for s in range(7, 17) for i in range(10)]
    flat = select_claim_rows(rows, 48, stage="t")
    assert not any(r["section_id"] == "sec_3" for r in flat.selected)  # the old bias
    strat = select_claim_rows(rows, 48, stage="t", section_key_of=lambda r: r["section_id"])
    assert len(strat.selected) == 48
    assert any(r["section_id"] == "sec_3" for r in strat.selected)
    assert len(strat.excluded) == len(rows) - 48


# --------------------------------------------------------------------- input budget


def _claim_object(i, section):
    return ClaimObjectRecord(
        claim_id=f"claim_{i:04d}",
        document_id="doc",
        claim_type="result",
        text=("A long atomic claim sentence about the analysis result " * 4)[:200],
        source_evidence_ids=[f"ev_{i}"],
        source_span_ids=[f"span_{i}"],
        concepts=[],
        section_id=section,
        atomicity="atomic",
        is_atomic=True,
    )


def test_input_budget_thins_displayed_claims_and_reports_coverage():
    objects = [_claim_object(i, f"sec_{i % 5}") for i in range(400)]
    claim_objects = ClaimObjectBuildResult("doc", None, claims=objects)
    qualified = ClaimQualificationResult("doc", None, [], [], [], {})
    llm_input = ComponentAssemblyInputBuilder().build(
        qualified, claim_objects=claim_objects, config={"max_input_chars": 60_000}
    )
    assert len(llm_input.available_claims) == 400  # whitelist untouched
    shown = llm_input.prompt_available_claims
    assert shown is not None and 0 < len(shown) < 400
    assert {c["claim_id"] for c in llm_input.accepted_claims}.isdisjoint(
        {c["claim_id"] for c in shown}
    )
    coverage = llm_input.input_coverage
    assert coverage["reasons"] == ["max_input_chars"]
    assert coverage["truncated"] > 0
    content = ComponentAssemblyPromptFactory().build_messages(llm_input)[-1]["content"]
    assert "available_claims_note" in content
    assert len(content) < 120_000


def test_input_under_budget_is_unchanged():
    objects = [_claim_object(i, "sec_1") for i in range(3)]
    llm_input = ComponentAssemblyInputBuilder().build(
        ClaimQualificationResult("doc", None, [], [], [], {}),
        claim_objects=ClaimObjectBuildResult("doc", None, claims=objects),
    )
    assert llm_input.prompt_available_claims is None
    assert llm_input.input_coverage == {}
