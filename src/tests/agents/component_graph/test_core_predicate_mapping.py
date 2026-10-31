"""DSL core_predicate → component 辺 edge_type の写像（IK-0791・X1 seq 3289）。"""
from __future__ import annotations

import dataclasses

import pytest

from episteme_graph.agents.component_graph import prompt as prompt_module
from episteme_graph.agents.component_graph.input_builder import ComponentGraphInputBuilder
from episteme_graph.agents.component_graph.schema import (
    CORE_PREDICATE_NO_SPECIFIC_EDGE_TYPE,
    CORE_PREDICATE_TO_EDGE_TYPE,
    LLM_RELATION_EDGE_TYPES,
    VALID_EDGE_TYPES,
    edge_type_for_core_predicate,
)
from episteme_graph.agents.dsl_linking.schema import CORE_PREDICATES
from tests.agents.component_graph.test_input_builder import (
    _make_assembly,
    _make_component,
    _make_dsl,
    _make_dsl_edge,
    _make_dsl_node,
)


def test_mapping_table_is_closed_over_both_vocabularies():
    assert set(CORE_PREDICATE_TO_EDGE_TYPE) <= set(CORE_PREDICATES)
    assert set(LLM_RELATION_EDGE_TYPES) <= set(VALID_EDGE_TYPES)
    for spec in CORE_PREDICATE_TO_EDGE_TYPE.values():
        assert spec["edge_type"] in LLM_RELATION_EDGE_TYPES
    # 全 10 述語が写る（無いものは generic で落とさない）。
    for pred in CORE_PREDICATES:
        assert edge_type_for_core_predicate(pred)["edge_type"] in LLM_RELATION_EDGE_TYPES


def test_prompt_defines_exactly_the_llm_relation_vocabulary():
    rules = prompt_module._SYSTEM_CONTENT.split("Edge inference rules:")[1].split("Inference strategy")[0]
    defined = [line.split(":")[0].split(". ", 1)[1] for line in rules.strip().splitlines()]
    assert defined == LLM_RELATION_EDGE_TYPES


@pytest.mark.parametrize("pred", ["CONTAINS", "CORRELATES", "CAUSES", "EQUIVALENT", "unknown_pred"])
def test_predicates_without_a_specific_edge_type_fall_back_to_generic(pred):
    mapped = edge_type_for_core_predicate(pred)
    assert mapped == {
        "edge_type": "RELATED_TO", "reverse": False, "generic": True,
        "mapping_note": CORE_PREDICATE_NO_SPECIFIC_EDGE_TYPE,
    }


def test_inhibits_is_a_correction_not_a_contradiction():
    assert edge_type_for_core_predicate("INHIBITS")["edge_type"] == "QUALIFIES"


def _input_for(predicate):
    comp_a = _make_component("comp_003", "Jeffreys prior", linked_dsl_node_ids=["n1"])
    comp_b = _make_component("comp_004", "Laplace term", linked_dsl_node_ids=["n2"])
    edge = dataclasses.replace(_make_dsl_edge("e1", "n1", "n2"), core_predicate=predicate,
                               domain_verb="corrects")
    dsl = _make_dsl([_make_dsl_node("n1"), _make_dsl_node("n2")], [edge])
    return ComponentGraphInputBuilder().build(_make_assembly([comp_a, comp_b]), dsl=dsl)


def test_material2_offers_the_mapped_edge_type_3289():
    cross = _input_for("INHIBITS").cross_component_dsl_edges[0]
    assert cross["core_predicate"] == "INHIBITS"
    assert cross["suggested_edge_type"] == "QUALIFIES"
    assert (cross["suggested_source_component_id"], cross["suggested_target_component_id"]) == (
        "comp_003", "comp_004")
    assert "mapping_note" not in cross


def test_material2_reverses_measures_and_keeps_generic_note(caplog):
    measured = _input_for("MEASURES").cross_component_dsl_edges[0]
    assert measured["suggested_edge_type"] == "CHECKED_BY"
    assert measured["suggested_source_component_id"] == "comp_004"
    with caplog.at_level("WARNING"):
        contains = _input_for("CONTAINS").cross_component_dsl_edges[0]
    assert contains["suggested_edge_type"] == "RELATED_TO"
    assert contains["mapping_note"] == CORE_PREDICATE_NO_SPECIFIC_EDGE_TYPE
    assert "no specific component edge_type" in caplog.text


def test_llm_is_not_offered_undefined_operation_types():
    types = _input_for("REQUIRES").valid_edge_types
    assert sorted(types) == sorted(LLM_RELATION_EDGE_TYPES)
    assert "diagnoses" not in types and "feeds" not in types
