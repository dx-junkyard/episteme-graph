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


def test_proxy_node_dominance_warning():
    nodes = [
        _node("n1", node_type="ClaimProxy"),
        _node("n2", node_type="ClaimProxy", value="claim2"),
        _node("n3", node_type="ThesisProxy", value="thesis"),
        _node("n4", node_type="Result", value="result"),
    ]
    edges = [_edge(from_node_id="n1", to_node_id="n2")]
    issues = VALIDATOR.validate(_result(nodes=nodes, edges=edges))
    assert any(i.rule_id == "proxy_node_dominance" for i in issues)


def test_concrete_typed_graph_has_no_proxy_dominance():
    nodes = [
        _node("n1", node_type="EquationRelation", value="eq_3_14"),
        _node("n2", node_type="Approximation", value="zero_recoil"),
        _node("n3", node_type="UncertaintySource", value="form_factor"),
        _node("n4", node_type="Observable", value="decay_rate"),
    ]
    edges = [_edge(from_node_id="n1", to_node_id="n2", core_predicate="REQUIRES")]
    issues = VALIDATOR.validate(_result(nodes=nodes, edges=edges))
    assert not any(i.rule_id == "proxy_node_dominance" for i in issues)
    assert not any(i.rule_id == "missing_concrete_node_types" for i in issues)


def test_missing_concrete_node_types_warning():
    nodes = [
        _node("n1", node_type="ClaimProxy", value="a"),
        _node("n2", node_type="ClaimProxy", value="b"),
        _node("n3", node_type="Method", value="m"),
        _node("n4", node_type="Constraint", value="c"),
    ]
    issues = VALIDATOR.validate(_result(nodes=nodes, edges=[]))
    assert any(i.rule_id == "missing_concrete_node_types" for i in issues)


def test_equivalent_predicate_misuse_warning():
    nodes = [
        _node("n1", node_type="CorrectionTerm", value="theoretical_corrections"),
        _node("n2", node_type="Result", value="corrections_are_small"),
    ]
    edge = _edge(
        from_node_id="n1",
        to_node_id="n2",
        core_predicate="EQUIVALENT",
    )
    issues = VALIDATOR.validate(_result(nodes=nodes, edges=[edge]))
    assert any(i.rule_id == "equivalent_predicate_misuse" for i in issues)


def test_equivalent_same_type_is_allowed():
    nodes = [
        _node("n1", node_type="EquationRelation", value="eq_a"),
        _node("n2", node_type="EquationRelation", value="eq_b"),
    ]
    edge = _edge(
        from_node_id="n1",
        to_node_id="n2",
        core_predicate="EQUIVALENT",
    )
    issues = VALIDATOR.validate(_result(nodes=nodes, edges=[edge]))
    assert not any(i.rule_id == "equivalent_predicate_misuse" for i in issues)


def test_edge_missing_evidence_refs_warning():
    nodes = [
        _node("n1", node_type="EquationRelation", value="eq_a"),
        _node("n2", node_type="EquationRelation", value="eq_b"),
    ]
    edge = _edge(
        from_node_id="n1",
        to_node_id="n2",
        core_predicate="TRANSFORMS",
        confidence=0.5,
        evidence_refs={"claim_ids": [], "equation_ids": [], "thesis_refs": []},
    )
    issues = VALIDATOR.validate(_result(nodes=nodes, edges=[edge]))
    assert any(i.rule_id == "edge_missing_evidence_refs" for i in issues)


def test_predicate_target_mismatch_info():
    # MEASURES -> Approximation is an unusual target; expect Observable/Diagnostic/Result
    nodes = [
        _node("n1", node_type="Diagnostic", value="diag"),
        _node("n2", node_type="Approximation", value="approx"),
    ]
    edge = _edge(
        from_node_id="n1",
        to_node_id="n2",
        core_predicate="MEASURES",
    )
    issues = VALIDATOR.validate(_result(nodes=nodes, edges=[edge]))
    assert any(i.rule_id == "predicate_target_mismatch" for i in issues)


def test_predicate_source_mismatch_info():
    # MEASURES typically originates from Diagnostic/Experiment/Method/Observable.
    # An Approximation -> Observable MEASURES edge should flag predicate_source_mismatch.
    nodes = [
        _node("n1", node_type="Approximation", value="approx"),
        _node("n2", node_type="Observable", value="obs"),
    ]
    edge = _edge(
        from_node_id="n1",
        to_node_id="n2",
        core_predicate="MEASURES",
    )
    issues = VALIDATOR.validate(_result(nodes=nodes, edges=[edge]))
    assert any(i.rule_id == "predicate_source_mismatch" for i in issues)


def test_new_node_types_accepted():
    nodes = [
        _node("n1", node_type="EquationRelation", value="eq"),
        _node("n2", node_type="CorrectionSource", value="src"),
        _node("n3", node_type="Experiment", value="exp"),
    ]
    edges = [_edge(from_node_id="n1", to_node_id="n2", core_predicate="CAUSES")]
    issues = VALIDATOR.validate(_result(nodes=nodes, edges=edges))
    assert not any(i.rule_id == "invalid_node_type" for i in issues)
