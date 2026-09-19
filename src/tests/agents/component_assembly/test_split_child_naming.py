"""分割された子 component の名前に親の意味が残ること（2026-09-19）。

``_suggested_split`` は子を責務名だけ（``Definition`` / ``Application`` /
``Equation System``）で名づけていた。親の意味ある label（「Kernel normalization
constraint」等）は ``refinement_report.split_actions[].parent_label`` にしか残らず、
course_mapping の topic 題名に機械名が並び、``Definition`` が 2 件重複する論文も
あった（F-5 残存）。
"""
from __future__ import annotations

from episteme_graph.agents.component_assembly.component_refiner import (
    ComponentRefiner,
    _suggested_child_label,
)
from episteme_graph.agents.component_assembly.granularity_analyzer import (
    ComponentGranularityAnalyzer,
    suggested_child_name,
)
from episteme_graph.agents.component_assembly.schema import (
    COMPONENTS_VERSION,
    ComponentAssemblyResult,
    ComponentRecord,
)

REFINER = ComponentRefiner()
ANALYZER = ComponentGranularityAnalyzer()

# 責務名だけの機械名（これが単独で label になってはいけない）。
_BARE_RESPONSIBILITY_LABELS = {
    "definition", "application", "constraint", "derivation", "equation system",
    "model", "observation model", "observable basis", "limitation",
}


def _component(**kwargs) -> ComponentRecord:
    defaults = dict(
        component_id="comp_1",
        component_type="RelationComponent",
        label="Kernel normalization constraint",
        summary="A coarse bundle.",
        inputs=[],
        outputs=[],
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


def _split_rec(*suggested) -> dict:
    return {
        "required": True,
        "reasons": ["responsibility type has more than one primary value"],
        "suggested_components": [
            {"name": name, "responsibility_type": resp} for name, resp in suggested
        ],
    }


class _LLMInput:
    def __init__(self, equations=None):
        self.equations = equations or []
        self.available_equations = []
        self.available_claims = []
        self.accepted_claims = []
        self.claim_centered_plan = {}


# ---------------------------------------------------------------------------
# suggested_child_name / granularity analyzer
# ---------------------------------------------------------------------------


def test_suggested_child_name_keeps_the_parent_theory_object():
    assert suggested_child_name("Kernel normalization constraint", "constraint") == (
        "Kernel normalization constraint — constraint"
    )
    assert suggested_child_name("Limber reduction step", "equation_system") == (
        "Limber reduction step — equation system"
    )


def test_suggested_child_name_degrades_safely():
    assert suggested_child_name("", "definition") == "Definition"
    assert suggested_child_name("Parent", "") == "Parent"


def test_analyzer_suggested_split_names_are_not_bare_responsibilities():
    component = _component(
        label="FAR sample selection",
        evidence_refs={"claim_ids": ["claim_1"],
                       "equation_ids": [f"eq_{i}" for i in range(10)]},
        linked_equation_ids=[f"eq_{i}" for i in range(10)],
        definition_equation_ids=["eq_0"],
        input_equation_ids=["eq_1"],
        intermediate_equation_ids=["eq_2"],
        output_equation_ids=["eq_3"],
        constraint_equation_ids=["eq_4"],
        internal_flow=[{"from": "eq_0", "relation": "define", "to": "eq_1"}],
    )
    ANALYZER.analyze(_result([component]))
    suggested = component.split_recommendation.get("suggested_components") or []
    assert suggested, "この構成なら分割候補が出る"
    for spec in suggested:
        name = str(spec["name"])
        assert name.lower() not in _BARE_RESPONSIBILITY_LABELS, name
        assert name.startswith("FAR sample selection"), name


# ---------------------------------------------------------------------------
# refiner
# ---------------------------------------------------------------------------


def test_split_children_carry_the_parent_label():
    parent = _component(
        component_id="cx",
        label="Kernel normalization constraint",
        linked_equation_ids=["eq_def", "eq_der"],
        split_recommendation=_split_rec(
            ("Definition", "definition"),
            ("Derivation", "derivation"),
        ),
    )
    llm_input = _LLMInput(equations=[
        {"equation_id": "eq_def", "role": "definition", "plain_text": "def"},
        {"equation_id": "eq_der", "role": "transformation", "plain_text": "der"},
    ])
    result = REFINER.refine(_result([parent]), llm_input=llm_input, derivations=None)

    labels = [c.label for c in result.components]
    assert labels == [
        "Kernel normalization constraint — definition",
        "Kernel normalization constraint — derivation",
    ]
    for child in result.components:
        assert child.label.lower() not in _BARE_RESPONSIBILITY_LABELS
        # 親の label は summary にも残る（どの理論対象の一部かが読める）。
        assert "Kernel normalization constraint" in child.summary


def test_children_of_different_parents_do_not_share_a_label():
    """同じ責務でも親が違えば label は違う（stable_key の衝突も起きない）。"""
    llm_input = _LLMInput(equations=[
        {"equation_id": "eq_def", "role": "definition", "plain_text": "def"},
        {"equation_id": "eq_der", "role": "transformation", "plain_text": "der"},
    ])
    parents = [
        _component(
            component_id="cx",
            label="Kernel normalization constraint",
            linked_equation_ids=["eq_def", "eq_der"],
            split_recommendation=_split_rec(("Definition", "definition"),
                                            ("Derivation", "derivation")),
        ),
        _component(
            component_id="cy",
            label="Limber reduction step",
            linked_equation_ids=["eq_def", "eq_der"],
            split_recommendation=_split_rec(("Definition", "definition"),
                                            ("Derivation", "derivation")),
        ),
    ]
    result = REFINER.refine(_result(parents), llm_input=llm_input, derivations=None)
    labels = [c.label for c in result.components]
    assert len(labels) == len(set(labels)), labels


def test_a_meaningful_suggested_name_is_kept_as_is():
    parent = _component(label="Parent unit")
    assert _suggested_child_label(
        {"name": "Definition of the window function", "responsibility_type": "definition"},
        parent,
    ) == "Definition of the window function"


def test_a_missing_suggested_name_falls_back_to_the_parent_and_role():
    parent = _component(label="Parent unit")
    assert _suggested_child_label(
        {"responsibility_type": "constraint"}, parent
    ) == "Parent unit — constraint"
