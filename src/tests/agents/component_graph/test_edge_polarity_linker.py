"""Issue #451: DSL → component-graph edge polarity propagation test.

ドメイン非依存（claim ID 重なり＋DSL polarity フィールドのみ）であることを、
物理・生物の 2 ドメイン fixture で検証する。
"""
from __future__ import annotations

from types import SimpleNamespace

from episteme_graph.agents.component_graph.edge_polarity_linker import (
    link_component_edge_polarity,
)
from episteme_graph.agents.component_graph.schema import (
    ComponentGraphEdge,
    ComponentGraphNode,
    ComponentGraphResult,
)


def _dsl(nodes, edges):
    return SimpleNamespace(
        nodes=[SimpleNamespace(node_id=nid, source_refs={"claim_ids": cl}) for nid, cl in nodes],
        edges=[SimpleNamespace(from_node_id=f, to_node_id=t, polarity=p) for f, t, p in edges],
    )


def _node(cid, out=None, inp=None):
    return ComponentGraphNode(
        component_id=cid, label=cid, component_type="TheoryOperationNode",
        output_claim_ids=out or [], input_claim_ids=inp or [],
    )


def _edge(eid, src, tgt):
    return ComponentGraphEdge(edge_id=eid, source=src, target=tgt, edge_type="derives",
                              support_status="derivation_linked", evidence_claims=[], reasoning="")


def _result(nodes, edges):
    return ComponentGraphResult(document_id="d", graph_schema_version="1", cartridge_id=None,
                                nodes=nodes, edges=edges, review_notes=[], confidence=1.0)


def test_physics_negative_polarity_propagates():
    # DSL: claim cA --(-)--> claim cB  (an inhibitory relation)
    dsl = _dsl([("n1", ["cA"]), ("n2", ["cB"])], [("n1", "n2", "-")])
    nodes = [_node("S", out=["cA"]), _node("T", inp=["cB"])]
    edges = [_edge("e1", "S", "T")]
    result = _result(nodes, edges)
    n = link_component_edge_polarity(dsl, result)
    assert n == 1
    assert edges[0].polarity == "-"


def test_biology_positive_polarity_propagates():
    dsl = _dsl([("n1", ["cGene"]), ("n2", ["cGrowth"])], [("n1", "n2", "+")])
    nodes = [_node("S", out=["cGene"]), _node("T", inp=["cGrowth"])]
    edges = [_edge("e1", "S", "T")]
    result = _result(nodes, edges)
    assert link_component_edge_polarity(dsl, result) == 1
    assert edges[0].polarity == "+"


def test_negative_dominates_over_positive():
    # Two DSL relations across the same component edge: one +, one - → "-" wins.
    dsl = _dsl(
        [("n1", ["cA"]), ("n2", ["cB"]), ("n3", ["cC"]), ("n4", ["cD"])],
        [("n1", "n2", "+"), ("n3", "n4", "-")],
    )
    nodes = [_node("S", out=["cA", "cC"]), _node("T", inp=["cB", "cD"])]
    edges = [_edge("e1", "S", "T")]
    link_component_edge_polarity(dsl, _result(nodes, edges))
    assert edges[0].polarity == "-"


def test_no_overlap_leaves_neutral():
    dsl = _dsl([("n1", ["cX"]), ("n2", ["cY"])], [("n1", "n2", "-")])
    nodes = [_node("S", out=["cA"]), _node("T", inp=["cB"])]  # no overlap with cX/cY
    edges = [_edge("e1", "S", "T")]
    assert link_component_edge_polarity(dsl, _result(nodes, edges)) == 0
    assert edges[0].polarity == ""


def test_existing_polarity_not_overwritten():
    dsl = _dsl([("n1", ["cA"]), ("n2", ["cB"])], [("n1", "n2", "-")])
    nodes = [_node("S", out=["cA"]), _node("T", inp=["cB"])]
    edges = [_edge("e1", "S", "T")]
    edges[0].polarity = "+"
    link_component_edge_polarity(dsl, _result(nodes, edges))
    assert edges[0].polarity == "+"


def test_tolerates_none_and_serializes():
    assert link_component_edge_polarity(None, None) == 0
    dsl = _dsl([("n1", ["cA"]), ("n2", ["cB"])], [("n1", "n2", "-")])
    nodes = [_node("S", out=["cA"]), _node("T", inp=["cB"])]
    edges = [_edge("e1", "S", "T")]
    result = _result(nodes, edges)
    link_component_edge_polarity(dsl, result)
    payload = result.to_graph_payload()
    assert payload["edges"][0]["polarity"] == "-"
