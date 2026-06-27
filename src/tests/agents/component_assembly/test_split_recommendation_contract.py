"""Split-recommendation contract regression tests (#417).

The granularity analyzer, ComponentRefiner, and the revision checkpoint planner
must agree on a single split-recommendation schema whose canonical key is
``required``. Legacy ``split_required`` / ``should_split`` keys must still be
honored during the migration period.
"""
from episteme_graph.agents.component_assembly.granularity_analyzer import (
    ComponentGranularityAnalyzer,
)
from episteme_graph.agents.component_assembly.component_refiner import ComponentRefiner
from episteme_graph.agents.component_assembly.schema import (
    COMPONENTS_VERSION,
    ComponentAssemblyResult,
    ComponentRecord,
)
from episteme_graph.agents.component_assembly.split_recommendation import (
    CANONICAL_SPLIT_KEY,
    normalize_split_recommendation,
    split_is_required,
)


def _component(**kwargs):
    defaults = dict(
        component_id="comp_1",
        component_type="RelationComponent",
        label="component",
        summary="summary",
        inputs=[{"name": "input"}],
        outputs=[{"name": "output"}],
        preconditions=[],
        cautions=[],
        dependencies=[],
        evidence_refs={},
        reason="reason",
        confidence=0.8,
        review_notes=[],
        responsibility_type="equation_system",
        primary_operation="linearize",
    )
    defaults.update(kwargs)
    return ComponentRecord(**defaults)


def _result(components):
    return ComponentAssemblyResult("doc", COMPONENTS_VERSION, None, components, [], [], 0.8)


# ---------------------------------------------------------------------------
# normalize / split_is_required
# ---------------------------------------------------------------------------

def test_split_is_required_reads_all_legacy_keys():
    assert split_is_required({"required": True}) is True
    assert split_is_required({"split_required": True}) is True
    assert split_is_required({"should_split": True}) is True
    assert split_is_required({"required": False}) is False
    assert split_is_required({}) is False
    assert split_is_required(None) is False


def test_normalize_emits_canonical_key_and_drops_legacy_aliases():
    norm = normalize_split_recommendation({"split_required": True, "reasons": ["x"]})
    assert norm[CANONICAL_SPLIT_KEY] is True
    assert "split_required" not in norm
    assert "should_split" not in norm
    assert norm["reasons"] == ["x"]
    assert norm["suggested_components"] == []


def test_normalize_should_split_alias():
    norm = normalize_split_recommendation({"should_split": True})
    assert norm["required"] is True


# ---------------------------------------------------------------------------
# Analyzer emits canonical schema
# ---------------------------------------------------------------------------

def test_analyzer_emits_canonical_required_key():
    component = _component(
        component_id="comp_big",
        linked_equation_ids=[f"eq_{i}" for i in range(10)],
        evidence_refs={"equation_ids": [f"eq_{i}" for i in range(10)]},
    )
    analyzed = ComponentGranularityAnalyzer().analyze(_result([component]))
    rec = analyzed.components[0].split_recommendation
    assert "required" in rec
    assert "split_required" not in rec
    assert rec["required"] is True


def test_suggested_children_do_not_share_identical_equation_sets():
    # A mixed-responsibility component must hand each suggested child *distinct*
    # candidate equations (#417), not a duplicate of the whole set.
    component = _component(
        component_id="comp_mix",
        responsibility_type="constraint",
        primary_operation="construct model",
        secondary_operations=["derive consequence"],
        linked_equation_ids=["eq_1", "eq_2", "eq_3", "eq_4"],
        internal_flow=[{"from": "eq_0", "relation": "derive", "to": "eq_1"}],
    )
    analyzed = ComponentGranularityAnalyzer().analyze(_result([component]))
    suggested = analyzed.components[0].split_recommendation["suggested_components"]
    assert len(suggested) >= 2
    seen: set[str] = set()
    for child in suggested:
        eqs = set(child.get("linked_equation_ids") or [])
        # No equation is duplicated across children.
        assert not (eqs & seen), "suggested children share equations"
        seen |= eqs


# ---------------------------------------------------------------------------
# derivation_step_count via equation overlap
# ---------------------------------------------------------------------------

class _Step:
    def __init__(self, inputs, outputs, operation="derive"):
        self.input_equation_ids = inputs
        self.output_equation_ids = outputs
        self.operation = operation


class _Chain:
    def __init__(self, steps, linked_component_ids=None):
        self.steps = steps
        self.linked_component_ids = linked_component_ids or []
        self.derivation_id = "der_1"


class _Derivations:
    def __init__(self, chains):
        self.chains = chains


def test_derivation_step_count_uses_equation_overlap_when_link_empty():
    # A derivation chain with no linked_component_ids still contributes steps to
    # a component that shares its equations (#417).
    component = _component(
        component_id="comp_eq",
        linked_equation_ids=["eq_1", "eq_2"],
        evidence_refs={"equation_ids": ["eq_1", "eq_2"]},
    )
    derivations = _Derivations([
        _Chain([
            _Step(["eq_1"], ["eq_2"]),
            _Step(["eq_2"], ["eq_3"]),
        ], linked_component_ids=[]),
    ])
    analyzed = ComponentGranularityAnalyzer().analyze(
        _result([component]), derivations=derivations
    )
    quality = analyzed.components[0].component_quality
    assert quality["derivation_step_count"] == 2


# ---------------------------------------------------------------------------
# refiner: required:true never silently unprocessed
# ---------------------------------------------------------------------------

class _LLMInput:
    def __init__(self, equations=None):
        self.equations = equations or []
        self.available_equations = []
        self.available_claims = []
        self.accepted_claims = []


def test_required_split_is_never_silently_unprocessed():
    component = _component(
        component_id="comp_req",
        component_type="ClaimBundleComponent",
        linked_claim_ids=["claim_1"],
        split_recommendation={
            "required": True,
            "reasons": ["responsibility type has more than one primary value"],
            # Only one usable responsibility → cannot actually split.
            "suggested_components": [
                {"name": "Only def", "responsibility_type": "definition"}
            ],
        },
    )
    refined = ComponentRefiner().refine(
        _result([component]), llm_input=_LLMInput(), derivations=None
    )
    validation = refined.component_refinement["refinement_validation"]
    # It must be accounted for (review_required), not silently left unprocessed.
    assert validation["unprocessed_split_required"] == []
    assert "comp_req" in validation["review_required_refinements"]


def test_legacy_split_required_key_still_drives_refiner():
    component = _component(
        component_id="comp_legacy",
        linked_equation_ids=["eq_def", "eq_der"],
        split_recommendation={
            "split_required": True,  # legacy key only
            "reasons": ["mixed"],
            "suggested_components": [
                {"name": "Def", "responsibility_type": "definition"},
                {"name": "Der", "responsibility_type": "derivation"},
            ],
        },
    )
    llm_input = _LLMInput(equations=[
        {"equation_id": "eq_def", "role": "definition"},
        {"equation_id": "eq_der", "role": "transformation"},
    ])
    refined = ComponentRefiner().refine(
        _result([component]), llm_input=llm_input, derivations=None
    )
    ids = [c.component_id for c in refined.components]
    assert "comp_legacy" not in ids  # it was split despite using the legacy key
