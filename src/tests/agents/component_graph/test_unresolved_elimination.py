"""消去対象の記号が無い eliminate step（2026-10-16 実測 arXiv 2610.20589 TeX 経路）。

derivation_chain の step が ``operation="eliminate"`` を宣言しながら
``eliminated_symbols=[]`` だったため、ComponentGraphValidator の hard error
``elimination_missing_eliminated_symbols`` に当たり、135 ノードのグラフ全体が保存
されなかった。normalizer は記号を推測で補わず、generic step として残す。
"""
from episteme_graph.agents.component_assembly.schema import ComponentAssemblyResult
from episteme_graph.agents.component_graph.normalizer import ComponentGraphNormalizer
from episteme_graph.agents.component_graph.schema import (
    ELIMINATION_TARGET_UNRESOLVED_REASON,
    GRAPH_SCHEMA_VERSION,
    ComponentGraphResult,
)
from episteme_graph.agents.component_graph.validator import ComponentGraphValidator
from episteme_graph.agents.derivation_chain.schema import (
    DerivationChainRecord,
    DerivationChainResult,
    DerivationStep,
)


def _result(steps):
    derivations = DerivationChainResult(
        document_id="doc",
        cartridge_id=None,
        chains=[DerivationChainRecord(
            derivation_id="derivation_eq_tex_b266",
            document_id="doc",
            source_section_ids=["sec_14"],
            steps=steps,
        )],
    )
    components = ComponentAssemblyResult(
        document_id="doc", components_version="v1", cartridge_id=None,
        components=[], assembly_hints=[], review_notes=[], confidence=0.8,
    )
    empty = ComponentGraphResult(
        document_id="doc", graph_schema_version=GRAPH_SCHEMA_VERSION, cartridge_id=None,
        nodes=[], edges=[], review_notes=[], confidence=0.0,
    )
    return ComponentGraphNormalizer().normalize(empty, components, derivations)


def _real_shape_steps(eliminated):
    # 実データの形: eliminate step は eliminated_symbols / retained_symbols 空。
    return [
        DerivationStep(
            step_id="step_001",
            input_equation_ids=["eq_tex_b260"],
            operation="eliminate",
            output_equation_ids=["eq_tex_b263"],
            review_status="teacher_review_required",
            required_claim_ids=["synth_claim_0026", "synth_claim_0027"],
            source_evidence_ids=["ev_0212", "ev_0213"],
            eliminated_symbols=eliminated,
        ),
        DerivationStep(
            step_id="step_002",
            input_equation_ids=["eq_tex_b263"],
            operation="apply_definition",
            output_equation_ids=["eq_tex_b266"],
            review_status="teacher_review_required",
            source_evidence_ids=["ev_0213", "ev_0214"],
        ),
    ]


def test_unresolved_elimination_is_kept_as_generic_detail_step():
    result = _result(_real_shape_steps([]))
    node = next(n for n in result.nodes if n.component_id == "eq_op_0001")
    assert "eliminat" not in node.operation
    assert not any("eliminat" in op for op in node.derivation_operations)
    assert node.graph_layer == "equation_detail"  # 式 backing があるので残す
    assert node.source_backing_status == "partially_source_backed"
    assert ELIMINATION_TARGET_UNRESOLVED_REASON in node.review_reasons
    assert "generic_operation" in node.review_reasons
    # 宣言された操作は落とさない（P4）。
    assert "'eliminate'" in node.review_reason
    # main に elimination stage を作らない（#308: generic は main にしない）。
    assert not any(
        n.graph_layer == "main" and "eliminat" in n.operation for n in result.nodes
    )


def test_unresolved_elimination_no_longer_trips_hard_error():
    result = _result(_real_shape_steps([]))
    issues = ComponentGraphValidator().validate(result)
    assert not [i for i in issues if i.rule_id == "elimination_missing_eliminated_symbols"]
    assert not [i for i in issues if i.severity == "error"]


def test_named_elimination_still_eliminates():
    result = _result(_real_shape_steps(["a_i"]))
    node = next(n for n in result.nodes if n.component_id == "eq_op_0001")
    assert node.operation == "eliminate"
    assert node.eliminated_symbols == ["a_i"]
    assert ELIMINATION_TARGET_UNRESOLVED_REASON not in node.review_reasons
