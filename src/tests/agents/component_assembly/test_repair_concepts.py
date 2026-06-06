"""Concept-field preservation through repair parsing (issue #8)."""
from episteme_graph.agents.component_assembly.repair import _parse_raw


def test_parse_raw_preserves_concept_fields():
    raw = {
        "document_id": "doc",
        "components": [
            {
                "component_id": "comp_1",
                "component_type": "RelationComponent",
                "label": "L",
                "summary": "S",
                "concepts": ["Skewness", "Kurtosis"],
                "prerequisite_concepts": ["Galaxy bias"],
                "introduced_concepts": ["Skewness"],
                "reused_concepts": ["Kurtosis"],
            }
        ],
    }

    result = _parse_raw(raw, "doc", None)
    comp = result.components[0]
    assert comp.concepts == ["Skewness", "Kurtosis"]
    assert comp.prerequisite_concepts == ["Galaxy bias"]
    assert comp.introduced_concepts == ["Skewness"]
    assert comp.reused_concepts == ["Kurtosis"]


def test_parse_raw_defaults_concept_fields_to_empty_lists():
    raw = {"document_id": "doc", "components": [{"component_id": "comp_1"}]}
    comp = _parse_raw(raw, "doc", None).components[0]
    assert comp.concepts == []
    assert comp.prerequisite_concepts == []
    assert comp.introduced_concepts == []
    assert comp.reused_concepts == []
