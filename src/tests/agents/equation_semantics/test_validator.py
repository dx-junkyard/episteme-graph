"""Tests for EquationSemanticsValidator."""
from episteme_graph.agents.equation_semantics.schema import (
    CartridgeContext,
    DefinedSymbol,
    DerivationLinks,
    EquationRolePrediction,
    EquationSemanticsRecord,
    EquationSemanticsResult,
)
from episteme_graph.agents.equation_semantics.validator import EquationSemanticsValidator

VALIDATOR = EquationSemanticsValidator()


def _record(**kwargs):
    defaults = dict(
        equation_id="eq_1_1",
        block_id="e1",
        section_id="sec_1",
        section_title="Formulation",
        label="1.1",
        text="N = a + b",
        latex=None,
        plain_text="N = a + b",
        equation_role=EquationRolePrediction("equation_definition", [], 0.9, "definition"),
        defined_symbols=[DefinedSymbol("N", "defined", "where N is defined as")],
        local_assumptions=[],
        derivation_links=DerivationLinks([], [], []),
        summary="Definition of prefactor N.",
        review_flags=[],
    )
    defaults.update(kwargs)
    return EquationSemanticsRecord(**defaults)


def _result(record=None):
    return EquationSemanticsResult("doc", None, [record or _record()])


def test_valid_record_has_no_errors():
    issues = VALIDATOR.validate(_result())
    assert not [i for i in issues if i.severity == "error"]


def test_invalid_role_is_error():
    role = EquationRolePrediction("bad", [], 0.9, "bad")
    issues = VALIDATOR.validate(_result(_record(equation_role=role)))
    assert any(i.rule_id == "invalid_equation_role" for i in issues)


def test_invalid_secondary_role_is_error():
    role = EquationRolePrediction("equation_relation", ["bad"], 0.9, "bad")
    issues = VALIDATOR.validate(_result(_record(equation_role=role)))
    assert any(i.rule_id == "invalid_equation_role" for i in issues)


def test_confidence_out_of_range_is_error():
    role = EquationRolePrediction("equation_relation", [], 1.5, "bad")
    issues = VALIDATOR.validate(_result(_record(equation_role=role)))
    assert any(i.rule_id == "confidence_out_of_range" for i in issues)


def test_invalid_definition_status_is_error():
    record = _record(defined_symbols=[DefinedSymbol("N", "bad", None)])
    issues = VALIDATOR.validate(_result(record))
    assert any(i.rule_id == "invalid_definition_status" for i in issues)


def test_invalid_linked_text_relation_is_error():
    links = DerivationLinks([], [], [{"span_id": "s1", "relation": "bad"}])
    issues = VALIDATOR.validate(_result(_record(derivation_links=links)))
    assert any(i.rule_id == "invalid_linked_text_relation" for i in issues)


def test_empty_summary_is_error():
    issues = VALIDATOR.validate(_result(_record(summary="")))
    assert any(i.rule_id == "empty_summary" for i in issues)


def test_definition_without_symbols_is_warning():
    issues = VALIDATOR.validate(_result(_record(defined_symbols=[])))
    assert any(i.rule_id == "definition_without_symbols" for i in issues)


def test_transformation_without_from_equations_is_warning():
    role = EquationRolePrediction("equation_transformation", [], 0.9, "transform")
    issues = VALIDATOR.validate(_result(_record(equation_role=role, defined_symbols=[])))
    assert any(i.rule_id == "transformation_without_from_equations" for i in issues)


def test_missing_label_requires_review_flag_warning():
    issues = VALIDATOR.validate(_result(_record(label=None, review_flags=[])))
    assert any(i.rule_id == "missing_label_without_review_flag" for i in issues)


def test_cartridge_notation_without_symbols_warning():
    cartridge = CartridgeContext(
        cartridge_id="test",
        ontology={},
        validation_rules={},
        aliases={"R Lambda c": ["RΛc"]},
    )
    role = EquationRolePrediction("equation_relation", [], 0.9, "relation")
    record = _record(
        text="RΛc = RD + RD*",
        plain_text="R Lambda c = RD + RD*",
        equation_role=role,
        defined_symbols=[],
        summary="Relation for R Lambda c.",
    )
    issues = VALIDATOR.validate(_result(record), cartridge)
    assert any(i.rule_id == "cartridge_notation_without_symbols" for i in issues)
