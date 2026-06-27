"""Issue #441: DSL edges must carry non-null edge_type and non-empty evidence_refs.

Multi-domain fixtures (two unrelated claim/equation id sets) verify behavior
without asserting any domain-specific verb or id.
"""
from __future__ import annotations

import pytest

from episteme_graph.agents.dsl_linking.graph_cleanup import DSLGraphCleanup
from episteme_graph.agents.dsl_linking.schema import (
    EDGE_TYPE_VOCAB,
    DSLEdge,
    DSLLinkingResult,
    DSLNode,
)


def _node(node_id, claim_ids=(), equation_ids=()):
    return DSLNode(
        node_id=node_id, node_type="Relation", node_value=f"v_{node_id}",
        source_kind="claim", source_refs={
            "claim_ids": list(claim_ids), "equation_ids": list(equation_ids),
            "thesis_refs": [],
        },
        reason="r", confidence=0.8,
    )


_DOMAINS = {
    "domain_a": {"claims": ["claim_a1", "claim_a2"], "equations": ["eq_a1"]},
    "domain_b": {"claims": ["c_alpha", "c_beta"], "equations": ["E_one"]},
}


@pytest.mark.parametrize("domain", sorted(_DOMAINS))
def test_cleanup_fills_edge_type_and_evidence(domain):
    spec = _DOMAINS[domain]
    n1 = _node("n1", claim_ids=[spec["claims"][0]], equation_ids=spec["equations"])
    n2 = _node("n2", claim_ids=[spec["claims"][1]])
    # edge_type empty + evidence_refs empty → cleanup must fill both.
    edge = DSLEdge(
        edge_id="e1", from_node_id="n1", to_node_id="n2",
        core_predicate="REQUIRES", domain_verb="assumes", polarity="+",
        evidence_refs={"claim_ids": [], "equation_ids": [], "thesis_refs": []},
        reason="r", confidence=0.8, edge_type="",
    )
    result = DSLLinkingResult("doc", "v1", [n1, n2], [edge], [], [], 0.8)
    cleaned = DSLGraphCleanup().cleanup(result)
    out_edge = cleaned.edges[0]

    assert out_edge.edge_type in EDGE_TYPE_VOCAB
    assert out_edge.edge_type == "REQUIRES"  # mirrors core_predicate (structural, not domain)
    # evidence inherited from the endpoint nodes' source_refs
    refs = out_edge.evidence_refs
    assert refs["claim_ids"] or refs["equation_ids"]
    assert spec["claims"][0] in refs["claim_ids"]


def test_domain_verb_is_preserved_as_subtype():
    n1, n2 = _node("n1", claim_ids=["c1"]), _node("n2", claim_ids=["c2"])
    edge = DSLEdge("e1", "n1", "n2", "TRANSFORMS", "linearizes", "+",
                   {"claim_ids": ["c1"], "equation_ids": [], "thesis_refs": []},
                   "r", 0.9, edge_type="")
    result = DSLLinkingResult("doc", "v1", [n1, n2], [edge], [], [], 0.9)
    cleaned = DSLGraphCleanup().cleanup(result)
    assert cleaned.edges[0].edge_type == "TRANSFORMS"
    assert cleaned.edges[0].domain_verb == "linearizes"  # raw verb kept as subtype
