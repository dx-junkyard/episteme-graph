"""Normalize ComponentGraph into theory-structure graph nodes and edges.

The LLM edge pass connects assembled components. That is useful for debugging,
but the published graph should show the paper's theoretical operations:
definitions, equation-system construction, bias eliminations, consistency
relations, and diagnostics. This module builds that graph deterministically from
component equation roles plus derivation-chain operations.
"""
from __future__ import annotations

from dataclasses import replace

from episteme_graph.agents.component_assembly.schema import ComponentAssemblyResult
from episteme_graph.agents.derivation_chain.schema import DerivationChainResult

from .schema import ComponentGraphEdge, ComponentGraphNode, ComponentGraphResult

_GENERIC_LABELS = {"define", "transform", "relate", "result"}


class ComponentGraphNormalizer:
    def normalize(
        self,
        result: ComponentGraphResult,
        components: ComponentAssemblyResult,
        derivations: DerivationChainResult | None = None,
    ) -> ComponentGraphResult:
        theory_nodes, theory_edges = self._build_derivation_theory_graph(
            result.document_id,
            derivations,
        )
        if len(theory_nodes) >= 5:
            return replace(
                result,
                nodes=theory_nodes + self._debug_nodes(result.nodes),
                edges=theory_edges,
                confidence=max(result.confidence, 0.85),
                review_notes=list(result.review_notes or []) + [
                    "GraphNormalizer built the main graph from derivation operations and equation roles."
                ],
            )

        normalized_nodes = [
            self._normalize_component_node(node, comp)
            for node, comp in _join_nodes_to_components(result.nodes, components)
        ]
        return replace(result, nodes=normalized_nodes, edges=self._normalize_edges(result.edges, normalized_nodes))

    # ------------------------------------------------------------------

    def _build_derivation_theory_graph(
        self,
        document_id: str,
        derivations: DerivationChainResult | None,
    ) -> tuple[list[ComponentGraphNode], list[ComponentGraphEdge]]:
        steps = [
            step
            for chain in (getattr(derivations, "chains", []) or [])
            for step in (getattr(chain, "steps", []) or [])
        ]
        if not steps:
            return [], []

        index = _DerivationIndex(steps)
        nodes: list[ComponentGraphNode] = []

        def add(
            component_id: str,
            label: str,
            operation: str,
            theory_object: str,
            eqs: list[str],
            *,
            inputs: list[str] | None = None,
            outputs: list[str] | None = None,
            constraints: list[str] | None = None,
            definitions: list[str] | None = None,
            eliminated: list[str] | None = None,
            retained: list[str] | None = None,
            ops: list[str] | None = None,
        ) -> None:
            nodes.append(ComponentGraphNode(
                component_id=component_id,
                label=label,
                component_type="TheoryOperationNode",
                review_status="teacher_review_required",
                display_order=len(nodes),
                origin="derivation_chain",
                operation=operation,
                theory_object=theory_object,
                graph_layer="main",
                maturity_source="derivation_normalized",
                publish_ready=False,
                input_equation_ids=_ordered_unique(inputs if inputs is not None else eqs[:1]),
                intermediate_equation_ids=[],
                output_equation_ids=_ordered_unique(outputs if outputs is not None else eqs[-1:]),
                definition_equation_ids=_ordered_unique(definitions or []),
                constraint_equation_ids=_ordered_unique(constraints or []),
                review_required_equation_ids=_ordered_unique(eqs),
                eliminated_symbols=_ordered_unique(eliminated or []),
                retained_symbols=_ordered_unique(retained or []),
                derivation_operations=_ordered_unique(ops or []),
            ))

        add(
            "theory_matter_perturbation_kernels",
            "Define matter perturbation kernels",
            "define",
            "matter perturbation kernels",
            index.equations_matching("eq_2_5", "eq_2_7", "eq_2_10", "eq_2_11"),
            definitions=index.equations_matching("eq_2_5", "eq_2_7"),
            ops=index.operations_matching("define", "derive_result"),
        )
        add(
            "theory_galaxy_bias_model",
            "Define galaxy bias model",
            "define",
            "galaxy bias model",
            index.equations_matching("eq_2_12", "eq_2_13", "eq_2_14"),
            definitions=index.equations_matching("eq_2_12", "eq_2_14"),
            ops=index.operations_matching("define", "transform"),
        )
        add(
            "theory_smoothed_observables",
            "Define smoothed skewness/kurtosis observables",
            "define",
            "smoothed skewness/kurtosis observables",
            index.equations_matching("eq_2_16", "eq_2_17", "eq_2_18", "eq_2_19", "eq_2_21"),
            definitions=index.equations_matching("eq_2_17", "eq_2_18", "eq_2_19", "eq_2_21"),
            ops=index.operations_matching("introduce_observable", "define"),
        )
        add(
            "theory_skewness_bias_linear_equations",
            "Build skewness bias-linear equations",
            "linearize",
            "skewness bias-linear moment equations",
            index.equations_matching("eq_3_32", "eq_3_33", "eq_3_34"),
            inputs=index.equations_matching("eq_3_32", "eq_3_33"),
            outputs=index.equations_matching("eq_3_34"),
            ops=index.operations_matching("linearize_skewness_bias_dependence"),
        )
        add(
            "theory_kurtosis_bias_linear_equations",
            "Build kurtosis bias-linear equations",
            "linearize",
            "kurtosis bias-linear moment equations",
            index.equations_matching("eq_3_33", "eq_3_35"),
            inputs=index.equations_matching("eq_3_33"),
            outputs=index.equations_matching("eq_3_35"),
            ops=index.operations_matching("linearize_kurtosis_bias_dependence", "linearize_skewness_bias_dependence"),
        )
        add(
            "theory_second_order_bias_elimination",
            "Eliminate second-order bias",
            "eliminate",
            "second-order galaxy bias",
            index.equations_matching("eq_3_32", "eq_3_35", "eq_3_36"),
            inputs=index.equations_matching("eq_3_32", "eq_3_35"),
            outputs=index.equations_matching("eq_3_36"),
            eliminated=["b2", "beta2", "second-order bias"],
            retained=["b1", "kappa", "lambda", "skewness observables"],
            ops=index.operations_matching("solve_second_order_bias", "substitute_second_order_bias"),
        )
        add(
            "theory_third_order_bias_elimination",
            "Eliminate third-order bias",
            "eliminate",
            "third-order galaxy bias",
            index.equations_matching("eq_3_35", "eq_3_36"),
            inputs=index.equations_matching("eq_3_35", "eq_3_36"),
            outputs=index.equations_matching("eq_3_35", "eq_3_36"),
            eliminated=["b3", "gamma2", "third-order bias"],
            retained=["b1", "kurtosis observables", "gravity parameters"],
            ops=index.operations_matching("solve_third_order_bias", "substitute_third_order_bias"),
        )
        add(
            "theory_skewness_consistency_relation",
            "Derive skewness consistency relation",
            "derive",
            "skewness consistency relation",
            index.equations_matching("eq_3_34"),
            inputs=index.equations_matching("eq_3_32"),
            outputs=index.equations_matching("eq_3_34"),
            constraints=index.equations_matching("eq_3_34"),
            ops=index.operations_matching("derive_skewness_consistency_relation"),
        )
        add(
            "theory_first_kurtosis_consistency_relation",
            "Derive first kurtosis consistency relation",
            "derive",
            "first kurtosis consistency relation",
            index.equations_matching("eq_3_35"),
            inputs=index.equations_matching("eq_3_33"),
            outputs=index.equations_matching("eq_3_35"),
            constraints=index.equations_matching("eq_3_35"),
            ops=index.operations_matching("derive_first_kurtosis_consistency_relation", "linearize_skewness_bias_dependence"),
        )
        add(
            "theory_second_kurtosis_consistency_relation",
            "Derive second kurtosis consistency relation",
            "derive",
            "second kurtosis consistency relation",
            index.equations_matching("eq_3_36"),
            inputs=index.equations_matching("eq_3_35"),
            outputs=index.equations_matching("eq_3_36"),
            constraints=index.equations_matching("eq_3_36"),
            ops=index.operations_matching("derive_second_kurtosis_consistency_relation", "solve_second_order_bias"),
        )
        add(
            "theory_horndeski_dhost_diagnostic",
            "Diagnose Horndeski / DHOST gravity",
            "diagnose",
            "Horndeski / DHOST gravity",
            index.equations_matching("eq_2_10", "eq_3_34", "eq_3_35", "eq_3_36"),
            inputs=index.equations_matching("eq_3_34", "eq_3_35", "eq_3_36"),
            outputs=index.equations_matching("eq_2_10"),
            constraints=index.equations_matching("eq_2_10"),
            ops=index.operations_matching("derive_result", "apply_criterion"),
        )

        nodes = [node for node in nodes if _node_has_evidence(node)]
        node_ids = {n.component_id for n in nodes}
        edge_specs = [
            ("theory_matter_perturbation_kernels", "theory_galaxy_bias_model", "defines"),
            ("theory_galaxy_bias_model", "theory_smoothed_observables", "defines"),
            ("theory_smoothed_observables", "theory_skewness_bias_linear_equations", "feeds_equation_system"),
            ("theory_smoothed_observables", "theory_kurtosis_bias_linear_equations", "feeds_equation_system"),
            ("theory_skewness_bias_linear_equations", "theory_second_order_bias_elimination", "linearizes"),
            ("theory_kurtosis_bias_linear_equations", "theory_third_order_bias_elimination", "linearizes"),
            ("theory_second_order_bias_elimination", "theory_skewness_consistency_relation", "eliminates_bias"),
            ("theory_third_order_bias_elimination", "theory_first_kurtosis_consistency_relation", "eliminates_bias"),
            ("theory_third_order_bias_elimination", "theory_second_kurtosis_consistency_relation", "eliminates_bias"),
            ("theory_skewness_consistency_relation", "theory_horndeski_dhost_diagnostic", "diagnoses"),
            ("theory_first_kurtosis_consistency_relation", "theory_horndeski_dhost_diagnostic", "diagnoses"),
            ("theory_second_kurtosis_consistency_relation", "theory_horndeski_dhost_diagnostic", "diagnoses"),
        ]
        edges = [
            ComponentGraphEdge(
                edge_id=f"theory_edge_{idx:04d}",
                source=source,
                target=target,
                edge_type=edge_type,
                support_status="derivation_linked",
                evidence_claims=[],
                evidence_equation_ids=_edge_equations(nodes, source, target),
                reasoning=f"GraphNormalizer relation: {source} {edge_type} {target}.",
                confidence=0.9,
                review_status="teacher_review_required",
            )
            for idx, (source, target, edge_type) in enumerate(edge_specs, start=1)
            if source in node_ids and target in node_ids
        ]
        return nodes, edges

    def _normalize_component_node(self, node: ComponentGraphNode, comp) -> ComponentGraphNode:
        label = str(node.label or "").strip()
        if label.lower() in _GENERIC_LABELS:
            label = _label_for_component(comp) or label
        return replace(
            node,
            label=label,
            operation=node.operation or str(getattr(comp, "operation", "") or ""),
            theory_object=node.theory_object or _theory_object_for_component(comp),
            graph_layer="debug" if node.maturity_source == "deterministic_fallback" else node.graph_layer,
        )

    def _normalize_edges(
        self,
        edges: list[ComponentGraphEdge],
        nodes: list[ComponentGraphNode],
    ) -> list[ComponentGraphEdge]:
        node_by_id = {node.component_id: node for node in nodes}
        result: list[ComponentGraphEdge] = []
        seen: set[tuple[str, str, str]] = set()
        for edge in edges:
            if edge.source not in node_by_id or edge.target not in node_by_id:
                continue
            edge_type = _semantic_edge_type(edge, node_by_id[edge.source], node_by_id[edge.target])
            key = (edge.source, edge.target, edge_type)
            if key in seen:
                continue
            seen.add(key)
            result.append(replace(edge, edge_type=edge_type))
        return result

    @staticmethod
    def _debug_nodes(nodes: list[ComponentGraphNode]) -> list[ComponentGraphNode]:
        return [
            replace(node, graph_layer="debug", display_order=1000 + idx)
            for idx, node in enumerate(nodes)
            if node.maturity_source == "deterministic_fallback"
        ]


class _DerivationIndex:
    def __init__(self, steps) -> None:
        self.steps = list(steps or [])

    def equations_matching(self, *eq_ids: str) -> list[str]:
        wanted = set(eq_ids)
        result: list[str] = []
        for step in self.steps:
            for eq_id in list(getattr(step, "input_equation_ids", []) or []) + list(getattr(step, "output_equation_ids", []) or []):
                if eq_id in wanted:
                    result.append(eq_id)
        return _ordered_unique(result)

    def operations_matching(self, *operations: str) -> list[str]:
        wanted = set(operations)
        return _ordered_unique(
            str(getattr(step, "operation", "") or "")
            for step in self.steps
            if str(getattr(step, "operation", "") or "") in wanted
        )


def _join_nodes_to_components(nodes: list[ComponentGraphNode], components: ComponentAssemblyResult) -> list[tuple[ComponentGraphNode, object]]:
    comp_by_id = {c.component_id: c for c in components.components}
    return [(node, comp_by_id.get(node.component_id)) for node in nodes]


def _label_for_component(comp) -> str:
    if comp is None:
        return ""
    op = str(getattr(comp, "operation", "") or "").lower()
    theory_object = _theory_object_for_component(comp)
    if op and theory_object:
        return f"{_verb_for_operation(op)} {theory_object}"
    if theory_object:
        return theory_object[:1].upper() + theory_object[1:]
    return ""


def _theory_object_for_component(comp) -> str:
    text = " ".join([
        str(getattr(comp, "label", "") or ""),
        str(getattr(comp, "summary", "") or ""),
        str(getattr(comp, "reason", "") or ""),
        " ".join(getattr(comp, "linked_equation_ids", []) or []),
        " ".join(getattr(comp, "output_equation_ids", []) or []),
    ]).lower()
    if "horndeski" in text or "dhost" in text or "eq_2_10" in text:
        return "Horndeski / DHOST gravity"
    if "eq_3_34" in text or "skewness" in text:
        return "skewness consistency relation"
    if "eq_3_35" in text or "eq_3_36" in text or "kurtosis" in text:
        return "kurtosis consistency relation"
    if "bias" in text or "eq_2_12" in text or "eq_2_13" in text:
        return "galaxy bias model"
    if "observable" in text or "eq_2_17" in text or "eq_2_18" in text:
        return "smoothed skewness/kurtosis observables"
    if "kernel" in text or "eq_2_5" in text:
        return "matter perturbation kernels"
    return ""


def _verb_for_operation(operation: str) -> str:
    if operation in ("define", "observable_definition"):
        return "Define"
    if operation == "linearize":
        return "Build"
    if operation == "eliminate":
        return "Eliminate"
    if operation == "derive":
        return "Derive"
    if operation == "diagnose":
        return "Diagnose"
    if operation == "constrain":
        return "Constrain"
    return operation.replace("_", " ").capitalize()


def _semantic_edge_type(edge: ComponentGraphEdge, source: ComponentGraphNode, target: ComponentGraphNode) -> str:
    src_op = str(source.operation or "").lower()
    tgt_op = str(target.operation or "").lower()
    target_label = str(target.label or "").lower()
    if tgt_op == "diagnose" or "diagnos" in target_label or "horndeski" in target_label or "dhost" in target_label:
        return "diagnoses"
    if tgt_op == "derive" or "consistency relation" in target_label:
        return "derives"
    if tgt_op == "eliminate" or "bias elimination" in target_label:
        return "eliminates_bias"
    if tgt_op == "linearize" or "equation system" in target_label:
        return "feeds_equation_system"
    if src_op == "define":
        return "defines"
    return edge.edge_type


def _node_has_evidence(node: ComponentGraphNode) -> bool:
    return any([
        node.input_equation_ids,
        node.output_equation_ids,
        node.definition_equation_ids,
        node.constraint_equation_ids,
        node.derivation_operations,
    ])


def _edge_equations(nodes: list[ComponentGraphNode], source: str, target: str) -> list[str]:
    by_id = {node.component_id: node for node in nodes}
    values: list[str] = []
    for node_id in (source, target):
        node = by_id.get(node_id)
        if not node:
            continue
        values.extend(node.input_equation_ids)
        values.extend(node.output_equation_ids)
        values.extend(node.constraint_equation_ids)
        values.extend(node.definition_equation_ids)
    return _ordered_unique(values)


def _ordered_unique(values) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result
