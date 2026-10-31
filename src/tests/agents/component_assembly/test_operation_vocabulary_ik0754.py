"""IK-0754: component_assembly is told the operation vocabulary component_graph enforces.

The prompt presents ``component_graph.schema``'s operation tables as a closed list
(the same objects ``classify_operation`` reads — no copy), and the validator warns
(never errors) when a component's operation falls outside it.
"""
from __future__ import annotations

from episteme_graph.agents.component_assembly import prompt as prompt_mod
from episteme_graph.agents.component_assembly.prompt import (
    ComponentAssemblyPromptFactory,
    operation_vocabulary_text,
)
from episteme_graph.agents.component_assembly.schema import (
    COMPONENTS_VERSION,
    ComponentAssemblyLLMInput,
    ComponentAssemblyResult,
    ComponentRecord,
)
from episteme_graph.agents.component_assembly.validator import ComponentAssemblyValidator
from episteme_graph.agents.component_graph import schema as graph_schema


def _llm_input() -> ComponentAssemblyLLMInput:
    return ComponentAssemblyLLMInput(
        document_id="doc",
        cartridge_id=None,
        accepted_claims=[],
        equations=[],
        thesis_nodes=[],
        dsl_nodes=[],
        dsl_edges=[],
        allowed_component_types=["RelationComponent"],
        allowed_dependency_types=["depends_on"],
    )


def _component(operation: str, primary_operation: str = "") -> ComponentRecord:
    return ComponentRecord(
        component_id="comp_001",
        component_type="RelationComponent",
        label="Unit",
        summary="s",
        inputs=[],
        outputs=[],
        preconditions=[],
        cautions=[],
        dependencies=[],
        evidence_refs={"claim_ids": [], "equation_ids": []},
        reason="r",
        confidence=0.8,
        review_notes=[],
        operation=operation,
        primary_operation=primary_operation,
    )


def _result(component) -> ComponentAssemblyResult:
    return ComponentAssemblyResult(
        document_id="doc", components_version=COMPONENTS_VERSION, cartridge_id=None,
        components=[component], assembly_hints=[], review_notes=[], confidence=0.8,
    )


def test_exported_tables_are_the_classifier_tables_not_copies():
    assert graph_schema.OPERATION_PREFIX_FAMILIES is graph_schema._OPERATION_PREFIX_MAP
    assert graph_schema.OPERATION_FULL_NAMES is graph_schema._OPERATION_FULL_MAP


def test_prompt_lists_every_operation_family_and_full_name():
    content = ComponentAssemblyPromptFactory().build_messages(_llm_input())[1]["content"]
    assert "## Allowed Operations" in content
    assert operation_vocabulary_text() in content
    for prefix, (_verb, edge_type) in graph_schema.OPERATION_PREFIX_FAMILIES.items():
        assert f"- {prefix} -> {edge_type}" in content
    for name, (_verb, edge_type) in graph_schema.OPERATION_FULL_NAMES.items():
        assert f"- {name} -> {edge_type}" in content
    for generic in graph_schema.GENERIC_OPERATIONS:
        if generic:
            assert generic in content


def test_prompt_no_longer_offers_open_ended_operation_examples():
    # The old schema hint ended with "forecast/compare/..." — an open list.
    assert "forecast/compare/..." not in prompt_mod._SYSTEM_CONTENT
    assert "forecast/compare/..." not in str(prompt_mod._OUTPUT_SCHEMA)


def test_every_listed_form_is_accepted_by_the_classifier():
    for prefix in graph_schema.OPERATION_PREFIX_FAMILIES:
        assert graph_schema.classify_operation(f"{prefix}_something")[2] is False
    for name in graph_schema.OPERATION_FULL_NAMES:
        assert graph_schema.classify_operation(name)[2] is False


def test_validator_warns_on_operation_outside_vocabulary_without_error():
    for word in ("limit", "parameterize", "verify", "couple", "qualify", "classify", "forecast"):
        issues = ComponentAssemblyValidator().validate(_result(_component(word)))
        hits = [i for i in issues if i.rule_id == "operation_outside_graph_vocabulary"]
        assert hits, word
        assert all(i.severity == "warning" for i in hits)


def test_validator_checks_primary_operation_too():
    issues = ComponentAssemblyValidator().validate(
        _result(_component("solve_linear_system", primary_operation="verify"))
    )
    fields = {i.field for i in issues if i.rule_id == "operation_outside_graph_vocabulary"}
    assert fields == {"components[comp_001].primary_operation"}


def test_validator_is_quiet_for_vocabulary_operations():
    for word in ("solve_linear_system", "derive_result", "eliminate_parameter", "define"):
        issues = ComponentAssemblyValidator().validate(_result(_component(word)))
        assert not [i for i in issues if i.rule_id == "operation_outside_graph_vocabulary"]
