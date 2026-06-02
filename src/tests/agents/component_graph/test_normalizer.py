from episteme_graph.agents.component_assembly.schema import ComponentAssemblyResult
from episteme_graph.agents.component_graph.normalizer import ComponentGraphNormalizer
from episteme_graph.agents.component_graph.schema import GRAPH_SCHEMA_VERSION, ComponentGraphResult
from episteme_graph.agents.derivation_chain.schema import (
    DerivationChainRecord,
    DerivationChainResult,
    DerivationStep,
)


def _step(operation, inputs, outputs):
    return DerivationStep(
        step_id=f"step_{operation}",
        input_equation_ids=inputs,
        operation=operation,
        output_equation_ids=outputs,
        review_status="teacher_review_required",
    )


def _derivations():
    steps = [
        _step("define", ["eq_2_5"], ["eq_2_7"]),
        _step("define", ["eq_2_12"], ["eq_2_14"]),
        _step("define", ["eq_2_16"], ["eq_2_17"]),
        _step("linearize_skewness_bias_dependence", ["eq_3_32"], ["eq_3_34"]),
        _step("linearize_kurtosis_bias_dependence", ["eq_3_33"], ["eq_3_35"]),
        _step("solve_second_order_bias", ["eq_3_35"], ["eq_3_36"]),
        _step("solve_third_order_bias", ["eq_3_35"], ["eq_3_36"]),
        _step("derive_skewness_consistency_relation", ["eq_3_32"], ["eq_3_34"]),
        _step("derive_first_kurtosis_consistency_relation", ["eq_3_33"], ["eq_3_35"]),
        _step("derive_second_kurtosis_consistency_relation", ["eq_3_35"], ["eq_3_36"]),
        _step("apply_criterion", ["eq_3_34", "eq_3_35", "eq_3_36"], ["eq_2_10"]),
    ]
    return DerivationChainResult(
        document_id="doc",
        cartridge_id=None,
        chains=[
            DerivationChainRecord(
                derivation_id="deriv",
                document_id="doc",
                source_section_ids=[],
                steps=steps,
            )
        ],
    )


def _empty_components():
    return ComponentAssemblyResult(
        document_id="doc",
        components_version="v1",
        cartridge_id=None,
        components=[],
        assembly_hints=[],
        review_notes=[],
        confidence=0.8,
    )


def _empty_graph():
    return ComponentGraphResult(
        document_id="doc",
        graph_schema_version=GRAPH_SCHEMA_VERSION,
        cartridge_id=None,
        nodes=[],
        edges=[],
        review_notes=[],
        confidence=0.0,
    )


def test_normalizer_builds_theory_structure_graph_from_derivations():
    result = ComponentGraphNormalizer().normalize(_empty_graph(), _empty_components(), _derivations())

    labels = {node.label for node in result.nodes if node.graph_layer == "main"}
    assert "Eliminate second-order bias" in labels
    assert "Eliminate third-order bias" in labels
    assert "Derive skewness consistency relation" in labels
    assert "Derive first kurtosis consistency relation" in labels
    assert "Derive second kurtosis consistency relation" in labels
    assert "Diagnose Horndeski / DHOST gravity" in labels
    assert "Define" not in labels
    assert "Transform" not in labels

    edge_types = {edge.edge_type for edge in result.edges}
    assert {"defines", "feeds_equation_system", "linearizes", "eliminates_bias", "diagnoses"} <= edge_types

    diagnostic_sources = {
        edge.source for edge in result.edges
        if edge.target == "theory_horndeski_dhost_diagnostic"
    }
    assert "theory_skewness_consistency_relation" in diagnostic_sources
    assert "theory_first_kurtosis_consistency_relation" in diagnostic_sources
    assert "theory_second_kurtosis_consistency_relation" in diagnostic_sources

    second_order = next(node for node in result.nodes if node.component_id == "theory_second_order_bias_elimination")
    assert second_order.eliminated_symbols
    assert second_order.output_equation_ids
