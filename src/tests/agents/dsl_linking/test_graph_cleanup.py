"""Tests for DSLGraphCleanup."""
from episteme_graph.agents.dsl_linking.graph_cleanup import DSLGraphCleanup
from episteme_graph.agents.dsl_linking.schema import DSLEdge, DSLLinkingResult, DSLNode, DSL_VERSION


def _node(node_id, value):
    return DSLNode(node_id, "Relation", value, "claim", {"claim_ids": [node_id], "equation_ids": [], "thesis_refs": []}, "reason", 0.8)


def test_cleanup_dedupes_nodes_and_rewrites_edges():
    result = DSLLinkingResult(
        "doc",
        DSL_VERSION,
        [_node("n1", "sum_rule"), _node("n2", "sum_rule"), _node("n3", "uncertainty")],
        [
            DSLEdge("e1", "n2", "n3", "CORRELATES", "contributes_to_uncertainty", "?", {"claim_ids": [], "equation_ids": [], "thesis_refs": []}, "reason", 0.7),
            DSLEdge("e2", "n1", "n1", "REQUIRES", "assumes", "+", {"claim_ids": [], "equation_ids": [], "thesis_refs": []}, "self", 0.7),
        ],
        [],
        [],
        0.8,
    )
    cleaned = DSLGraphCleanup().cleanup(result)
    assert [n.node_id for n in cleaned.nodes] == ["n1", "n3"]
    assert len(cleaned.edges) == 1
    assert cleaned.edges[0].from_node_id == "n1"
