"""component_graph の hard error をノード単位の降格で解消して保存する（2026-10-16）。

実測: arXiv 2610.20589（TeX 経路）の 135 ノード中 1 ノードが
``elimination_missing_eliminated_symbols`` を出し、グラフ全体が保存されなかった。
"""
from types import SimpleNamespace

from episteme_graph.agents.component_graph.schema import (
    GRAPH_SCHEMA_VERSION,
    VALIDATION_DEMOTED_REASON,
    ComponentGraphEdge,
    ComponentGraphNode,
    ComponentGraphResult,
)
from episteme_graph.agents.component_graph.validator import ComponentGraphValidator

from core.document_pipeline import orchestrator
from core.document_pipeline.graph_demotion import plan_node_demotion, try_demote_graph


def _graph():
    main = ComponentGraphNode(
        component_id="theory_op_01", label="Equation system", component_type="TheoryOperationNode",
        operation="substitute", graph_layer="main", source_backing_status="source_backed",
        review_status="source_backed", linked_equation_ids=["eq_a"],
        linked_claim_ids=["c1"], member_component_ids=["eq_op_0001", "eq_op_0026"],
    )
    ok = ComponentGraphNode(
        component_id="eq_op_0001", label="Substitute eq_a", component_type="EquationOperationNode",
        operation="substitute", graph_layer="equation_detail", parent_component_id="theory_op_01",
        source_backing_status="source_backed", review_status="source_backed",
        input_equation_ids=["eq_a"], output_equation_ids=["eq_b"], linked_equation_ids=["eq_a", "eq_b"],
        linked_claim_ids=["c1"],
    )
    # 実データの形: eliminate で eliminated_symbols 空・source_backed。
    bad = ComponentGraphNode(
        component_id="eq_op_0026", label="Eliminate eq_tex_b263", component_type="EquationOperationNode",
        operation="eliminate", derivation_operations=["eliminate"], graph_layer="equation_detail",
        parent_component_id="theory_op_01", source_backing_status="source_backed",
        review_status="source_backed", review_reasons=["missing_atomic_claim"],
        input_equation_ids=["eq_tex_b260"], output_equation_ids=["eq_tex_b263"],
        linked_equation_ids=["eq_tex_b260", "eq_tex_b263"], linked_claim_ids=["synth_claim_0026"],
        eliminated_symbols=[], review_reason="Double-zero term added to the 2PN action.",
    )
    edge = ComponentGraphEdge(
        edge_id="e1", source="eq_op_0001", target="eq_op_0026", edge_type="eliminates",
        support_status="derivation_linked", evidence_claims=[], reasoning="",
        evidence_equation_ids=["eq_b"], graph_layer="equation_detail",
    )
    return ComponentGraphResult(
        document_id="doc", graph_schema_version=GRAPH_SCHEMA_VERSION, cartridge_id=None,
        nodes=[main, ok, bad], edges=[edge], review_notes=[], confidence=0.7,
    )


def _gate_errors(result):
    return [
        {"code": i.rule_id, "path": i.field, "artifact": "component_graph", "severity": "error"}
        for i in ComponentGraphValidator().validate(result) if i.severity == "error"
    ]


def test_real_shape_trips_the_hard_error():
    codes = [e["code"] for e in _gate_errors(_graph())]
    assert codes == ["elimination_missing_eliminated_symbols"]


def test_demotion_keeps_every_node_and_passes_revalidation():
    original = _graph()
    demoted, ids = try_demote_graph(original, _gate_errors(original))
    assert ids == ["eq_op_0026"]
    assert len(demoted.nodes) == len(original.nodes)  # P4: 落とさない
    node = next(n for n in demoted.nodes if n.component_id == "eq_op_0026")
    assert node.graph_layer == "debug"
    assert node.source_backing_status == "review_required"
    assert VALIDATION_DEMOTED_REASON in node.review_reasons
    assert "eliminat" not in node.operation
    assert "'eliminate'" in node.review_reason  # 宣言された操作は残す
    assert all(e.graph_layer == "debug" for e in demoted.edges if "eq_op_0026" in (e.source, e.target))
    assert not [i for i in ComponentGraphValidator().validate(demoted) if i.severity == "error"]
    # 入力は mutate しない。
    assert next(n for n in original.nodes if n.component_id == "eq_op_0026").graph_layer == "equation_detail"


def test_non_node_scoped_error_is_not_demoted():
    errs = [{"code": "component_graph_dependency_cycle", "path": "edges[a]", "artifact": "component_graph"}]
    assert plan_node_demotion(errs) is None
    errs = [{"code": "COMPONENT_GRAPH_EDGE_SOURCE_INVALID", "path": "$.edges[0].source"}]
    assert plan_node_demotion(errs) is None


def _ctx(result, errors):
    return SimpleNamespace(
        validation_result_dict={"status": "failed_validation", "errors": errors},
        component_graph_result=result, document_id="doc", material_id="m",
        skip_graph_persist=False, skip_component_persist=False, degraded_stages=[],
        graph_demoted_node_ids=[],
    )


def test_orchestrator_persists_graph_after_node_demotion():
    result = _graph()
    ctx = _ctx(result, _gate_errors(result))
    orchestrator._compute_persist_degradation_flags(ctx)
    assert ctx.skip_graph_persist is False
    assert ctx.graph_demoted_node_ids == ["eq_op_0026"]
    assert ctx.component_graph_result is not result


def test_orchestrator_still_skips_graph_for_unresolvable_errors():
    result = _graph()
    errors = [{"code": "COMPONENT_GRAPH_EDGE_SOURCE_INVALID", "path": "$.edges[0].source",
               "artifact": "component_graph"}]
    ctx = _ctx(result, errors)
    orchestrator._compute_persist_degradation_flags(ctx)
    assert ctx.skip_graph_persist is True
    assert ctx.degraded_stages == ["component_graph"]
    assert ctx.component_graph_result is result
