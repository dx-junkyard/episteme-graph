"""Tests for the graded generic-operation fallback (issue #361)."""
from episteme_graph.agents.component_assembly.schema import ComponentAssemblyResult
from episteme_graph.agents.component_graph.normalizer import ComponentGraphNormalizer
from episteme_graph.agents.component_graph.validator import ComponentGraphValidator
from episteme_graph.agents.component_graph.schema import (
    ComponentGraphNode,
    ComponentGraphResult,
    GRAPH_SCHEMA_VERSION,
)
from episteme_graph.agents.derivation_chain.agent import DerivationChainAgent
from episteme_graph.agents.derivation_chain.schema import (
    DerivationChainRecord,
    DerivationChainResult,
    DerivationStep,
)


def _step(operation, inputs, outputs, **kwargs):
    return DerivationStep(
        step_id=f"step_{operation}_{len(inputs)}_{len(outputs)}",
        input_equation_ids=inputs,
        operation=operation,
        output_equation_ids=outputs,
        review_status="teacher_review_required",
        **kwargs,
    )


def _derivations(steps):
    return DerivationChainResult(
        document_id="doc",
        cartridge_id=None,
        chains=[DerivationChainRecord(
            derivation_id="deriv",
            document_id="doc",
            source_section_ids=[],
            steps=steps,
        )],
    )


def _components():
    return ComponentAssemblyResult(
        document_id="doc", components_version="v1", cartridge_id=None,
        components=[], assembly_hints=[], review_notes=[], confidence=0.8,
    )


def _normalize(steps):
    return ComponentGraphNormalizer().normalize(
        ComponentGraphResult(
            document_id="doc", graph_schema_version=GRAPH_SCHEMA_VERSION,
            cartridge_id=None, nodes=[], edges=[], review_notes=[],
            confidence=0.8,
        ),
        components=_components(),
        derivations=_derivations(steps),
    )


def _layer(result, layer):
    return [n for n in result.nodes if n.graph_layer == layer]


def test_all_generic_chain_with_io_stays_in_detail_layer():
    """両端 source_backed の generic step は debug に落ちない."""
    result = _normalize([
        _step("transform", ["eq_a"], ["eq_b"]),
        _step("relate", ["eq_b"], ["eq_c"]),
    ])
    detail = _layer(result, "equation_detail")
    assert len(detail) == 2
    assert _layer(result, "debug") == []
    for node in detail:
        assert node.source_backing_status == "partially_source_backed"
        assert "generic_operation" in node.review_reasons


def test_generic_without_equation_backing_falls_to_debug():
    result = _normalize([
        _step("transform", [], ["eq_b"]),
        _step("relate", ["eq_b"], []),
    ])
    assert _layer(result, "equation_detail") == []
    debug = _layer(result, "debug")
    assert len(debug) == 2
    for node in debug:
        assert node.source_backing_status == "inferred"


def test_kept_generic_never_becomes_main_node():
    """generic は main node にしない（#308 の hard error を維持）."""
    result = _normalize([
        _step("define_operator", ["eq_a"], ["eq_b"]),
        _step("transform", ["eq_b"], ["eq_c"]),
    ])
    main_ops = {n.operation for n in _layer(result, "main")}
    assert "transform" not in main_ops
    issues = ComponentGraphValidator().validate(result)
    assert not any(i.rule_id == "main_graph_generic_operation" for i in issues)


def test_validator_reports_empty_main_graph_with_quality_breakdown():
    """main graph 全滅時は generic 率・backing 欠落率を warning で report."""
    nodes = [
        ComponentGraphNode(
            component_id=f"eq_op_{i}", label=f"step {i}",
            component_type="EquationOperationNode",
            graph_layer="debug", operation="transform",
            source_backing_status="inferred",
            review_reasons=["fallback_or_inferred_node"],
        )
        for i in range(3)
    ]
    result = ComponentGraphResult(
        document_id="doc", graph_schema_version=GRAPH_SCHEMA_VERSION,
        cartridge_id=None, nodes=nodes, edges=[], review_notes=[],
        confidence=0.8,
    )
    issues = ComponentGraphValidator().validate(result)
    reports = [i for i in issues if i.rule_id == "main_graph_empty_derivation_quality"]
    assert len(reports) == 1
    assert reports[0].severity == "warning"
    assert "3/3" in reports[0].message


def test_validator_silent_when_main_graph_present():
    result = _normalize([
        _step("define_operator", ["eq_a"], ["eq_b"]),
        _step("transform", ["eq_b"], ["eq_c"]),
    ])
    issues = ComponentGraphValidator().validate(result)
    assert not any(
        i.rule_id == "main_graph_empty_derivation_quality" for i in issues
    )


# ---------------------------------------------------------------------------
# Deterministic generic-operation reclassification (derivation_chain side)
# ---------------------------------------------------------------------------

class _Sem:
    def __init__(self, secondary_types=(), defined=()):
        self.secondary_types = list(secondary_types)
        self.defined_symbols = list(defined)


class _Defined:
    def __init__(self, status="defined"):
        self.definition_status = status


class _Record:
    def __init__(self, secondary_types=(), defined=()):
        self.semantics = _Sem(secondary_types, defined)


def test_refine_generic_operation_uses_secondary_roles():
    refine = DerivationChainAgent._refine_generic_operation
    assert refine("relate", _Record(secondary_types=["result"])) == "derive_result"
    assert refine("transform", _Record(secondary_types=["approximation"])) == "approximate"
    assert refine("relate", _Record(secondary_types=["constraint"])) == "apply_constraint"


def test_refine_generic_operation_uses_defined_symbols():
    refine = DerivationChainAgent._refine_generic_operation
    assert refine("relate", _Record(defined=[_Defined()])) == "apply_definition"


def test_refine_keeps_generic_when_no_signal():
    refine = DerivationChainAgent._refine_generic_operation
    assert refine("relate", _Record()) == "relate"
    assert refine("relate", None) == "relate"


def test_refine_does_not_touch_concrete_operations():
    refine = DerivationChainAgent._refine_generic_operation
    assert refine(
        "eliminate_parameter", _Record(secondary_types=["result"])
    ) == "eliminate_parameter"
