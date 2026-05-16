"""Tests for DSLLinking schema helpers."""
import json

from episteme_graph.agents.dsl_linking.schema import DSLEdge, DSLLinkingResult, DSLNode, DSL_VERSION


def test_to_json_round_trip():
    result = DSLLinkingResult(
        "doc",
        DSL_VERSION,
        [DSLNode("n1", "Relation", "sum_rule", "claim", {"claim_ids": ["c1"], "equation_ids": [], "thesis_refs": []}, "reason", 0.8)],
        [DSLEdge("e1", "n1", "n1", "CONTAINS", "summarizes", "?", {"claim_ids": ["c1"], "equation_ids": [], "thesis_refs": []}, "reason", 0.5)],
        [],
        [],
        0.8,
    )
    restored = DSLLinkingResult.from_dict(json.loads(result.to_json()))
    assert restored.nodes[0].node_type == "Relation"
    assert restored.edges[0].core_predicate == "CONTAINS"
