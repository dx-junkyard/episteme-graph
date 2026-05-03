"""Tests for DSLLinkingValidator."""
from episteme_graph.agents.dsl_linking.schema import DSLEdge, DSLLinkingResult, DSLNode, DSL_VERSION
from episteme_graph.agents.dsl_linking.validator import DSLLinkingValidator

VALIDATOR = DSLLinkingValidator()


def _node(node_id="n1", node_type="Relation", source_kind="claim", confidence=0.8, value="sum_rule"):
    return DSLNode(
        node_id,
        node_type,
        value,
        source_kind,
        {"claim_ids": ["c1"], "equation_ids": [], "thesis_refs": []},
        "reason",
        confidence,
    )


def _edge(**kwargs):
    defaults = dict(
        edge_id="e1",
        from_node_id="n1",
        to_node_id="n2",
        core_predicate="REQUIRES",
        domain_verb="assumes",
        polarity="+",
        evidence_refs={"claim_ids": ["c1"], "equation_ids": [], "thesis_refs": []},
        reason="reason",
        confidence=0.8,
    )
    defaults.update(kwargs)
    return DSLEdge(**defaults)


def _result(nodes=None, edges=None, confidence=0.8):
    return DSLLinkingResult(
        "doc",
        DSL_VERSION,
        [_node("n1"), _node("n2", "Approximation", value="limit")] if nodes is None else nodes,
        edges if edges is not None else [_edge()],
        [],
        [],
        confidence,
    )


def test_valid_graph_has_no_errors():
    assert not [i for i in VALIDATOR.validate(_result()) if i.severity == "error"]


def test_no_nodes_is_error():
    assert any(i.rule_id == "no_nodes" for i in VALIDATOR.validate(_result(nodes=[], edges=[])))


def test_invalid_node_type_is_error():
    assert any(i.rule_id == "invalid_node_type" for i in VALIDATOR.validate(_result(nodes=[_node(node_type="Bad")])))


def test_invalid_source_kind_is_error():
    assert any(i.rule_id == "invalid_source_kind" for i in VALIDATOR.validate(_result(nodes=[_node(source_kind="bad")])))


def test_edge_missing_node_is_error():
    assert any(i.rule_id == "edge_missing_to_node" for i in VALIDATOR.validate(_result(edges=[_edge(to_node_id="missing")])))


def test_invalid_core_predicate_is_error():
    assert any(i.rule_id == "invalid_core_predicate" for i in VALIDATOR.validate(_result(edges=[_edge(core_predicate="BAD")])))


def test_invalid_polarity_is_error():
    assert any(i.rule_id == "invalid_polarity" for i in VALIDATOR.validate(_result(edges=[_edge(polarity="maybe")])))


def test_strong_edge_without_evidence_is_warning():
    edge = _edge(evidence_refs={"claim_ids": [], "equation_ids": [], "thesis_refs": []})
    assert any(i.rule_id == "strong_edge_without_evidence" for i in VALIDATOR.validate(_result(edges=[edge])))


def test_invalid_graph_hint_is_error():
    result = _result()
    result.graph_hints = [{"hint_type": "bad", "node_ids": ["n1"]}]
    assert any(i.rule_id == "invalid_graph_hint_type" for i in VALIDATOR.validate(result))


def test_disconnected_graph_warning():
    assert any(i.rule_id == "disconnected_graph" for i in VALIDATOR.validate(_result(edges=[])))
