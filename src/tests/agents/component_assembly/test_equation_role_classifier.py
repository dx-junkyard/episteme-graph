"""Tests for EquationRoleClassifier (issue #300)."""
from episteme_graph.agents.component_assembly.equation_role_classifier import (
    EquationRoleClassifier,
)

CLASSIFIER = EquationRoleClassifier()


def _step(inputs, outputs, operation="derive"):
    return {
        "input_equation_ids": inputs,
        "output_equation_ids": outputs,
        "operation": operation,
    }


def test_input_and_output_roles_from_derivation():
    steps = [_step(["eq_in"], ["eq_out"])]
    result = CLASSIFIER.classify(
        ["eq_in", "eq_out"],
        equation_index={},
        derivation_steps=steps,
        declared_output_ids=["eq_out"],
    )
    assert result.input_equation_ids == ["eq_in"]
    assert result.output_equation_ids == ["eq_out"]
    assert result.intermediate_equation_ids == []


def test_intermediate_role_for_non_final_output():
    steps = [
        _step(["eq_in"], ["eq_mid"]),
        _step(["eq_mid"], ["eq_out"]),
    ]
    result = CLASSIFIER.classify(
        ["eq_in", "eq_mid", "eq_out"],
        equation_index={},
        derivation_steps=steps,
        declared_output_ids=["eq_out"],
    )
    assert "eq_mid" in result.intermediate_equation_ids
    assert "eq_out" in result.output_equation_ids
    assert "eq_in" in result.input_equation_ids


def test_definition_kind_classified():
    index = {"eq_def": {"role": "definition"}}
    result = CLASSIFIER.classify(["eq_def"], equation_index=index)
    assert result.definition_equation_ids == ["eq_def"]


def test_consistency_relation_is_constraint():
    index = {"eq_c": {"role": "consistency_relation"}}
    result = CLASSIFIER.classify(["eq_c"], equation_index=index)
    assert result.constraint_equation_ids == ["eq_c"]


def test_needs_math_review_marks_review_required():
    index = {"eq_r": {"role": "result", "needs_math_review": True}}
    result = CLASSIFIER.classify(
        ["eq_r"], equation_index=index, declared_output_ids=["eq_r"]
    )
    assert result.review_required_equation_ids == ["eq_r"]
    assert result.output_equation_ids == ["eq_r"]


def test_can_support_claim_false_requires_review():
    index = {"eq_r": {"confidence_policy": {"can_support_claim": False}}}
    result = CLASSIFIER.classify(["eq_r"], equation_index=index)
    assert "eq_r" in result.review_required_equation_ids


def test_unstructured_equation_defaults_to_input():
    result = CLASSIFIER.classify(["eq_x"], equation_index={})
    assert result.input_equation_ids == ["eq_x"]


def test_empty_input_returns_empty_classification():
    result = CLASSIFIER.classify([], equation_index={})
    assert result.to_dict() == {
        "input_equation_ids": [],
        "intermediate_equation_ids": [],
        "output_equation_ids": [],
        "definition_equation_ids": [],
        "constraint_equation_ids": [],
        "review_required_equation_ids": [],
    }
