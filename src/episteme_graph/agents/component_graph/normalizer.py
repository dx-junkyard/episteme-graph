"""Normalize ComponentGraph into a source-backed theory-operation graph.

The LLM edge pass connects assembled components, which is useful for debugging.
The published graph, however, should show the paper's *theory operations*
(definitions, linearizations, eliminations, derivations, diagnostics, …) and
make explicit, for every node and edge:

  * which artifacts (equations / derivations / claims / evidence) back it, and
  * whether it is ``source_backed`` or ``review_required`` (with reasons).

This module builds that graph deterministically from the DerivationChain. It is
domain-independent: node labels and edge types are derived from each step's
``operation`` via :func:`classify_operation`, never from hard-coded paper terms.
"""
from __future__ import annotations

from dataclasses import replace

from episteme_graph.agents.component_assembly.schema import ComponentAssemblyResult
from episteme_graph.agents.derivation_chain.schema import DerivationChainResult

from .schema import (
    ComponentGraphEdge,
    ComponentGraphNode,
    ComponentGraphResult,
    classify_operation,
)

_GENERIC_LABELS = {"define", "transform", "relate", "result"}
# Minimum number of theory-operation nodes required before we publish the
# derivation-derived graph as the main graph. Whenever a DerivationChain yields
# at least one theory-operation node we adopt it (issue #304): even a 1–2 step
# chain carries operation-derived node/edge types that must reach the graph.
# Only an empty derivation set falls back to the component view.
_MIN_THEORY_NODES = 1


class ComponentGraphNormalizer:
    def normalize(
        self,
        result: ComponentGraphResult,
        components: ComponentAssemblyResult,
        derivations: DerivationChainResult | None = None,
    ) -> ComponentGraphResult:
        theory_nodes, theory_edges = self._build_theory_graph(
            result.document_id,
            derivations,
        )
        if len(theory_nodes) >= _MIN_THEORY_NODES:
            return replace(
                result,
                nodes=theory_nodes + self._debug_nodes(result.nodes),
                edges=theory_edges,
                confidence=max(result.confidence, 0.85),
                review_notes=list(result.review_notes or []) + [
                    "GraphNormalizer built the main graph from derivation operations "
                    "and equation roles."
                ],
            )

        normalized_nodes = [
            self._normalize_component_node(node, comp)
            for node, comp in _join_nodes_to_components(result.nodes, components)
        ]
        return replace(
            result,
            nodes=normalized_nodes,
            edges=self._normalize_edges(result.edges, normalized_nodes),
        )

    # ------------------------------------------------------------------ #
    # Derivation → theory-operation graph (domain-independent)
    # ------------------------------------------------------------------ #

    def _build_theory_graph(
        self,
        document_id: str,
        derivations: DerivationChainResult | None,
    ) -> tuple[list[ComponentGraphNode], list[ComponentGraphEdge]]:
        records = self._collect_step_records(derivations)
        if not records:
            return [], []

        nodes = [self._node_from_record(rec) for rec in records]
        edges = self._edges_from_records(records)
        return nodes, edges

    @staticmethod
    def _collect_step_records(derivations: DerivationChainResult | None) -> list[dict]:
        records: list[dict] = []
        counter = 0
        for chain in getattr(derivations, "chains", []) or []:
            derivation_id = str(getattr(chain, "derivation_id", "") or "")
            for step in getattr(chain, "steps", []) or []:
                counter += 1
                operation = str(getattr(step, "operation", "") or "")
                verb, edge_type, is_generic = classify_operation(operation)
                step_id = str(getattr(step, "step_id", "") or f"step_{counter}")
                records.append({
                    "node_id": f"theory_op_{counter:04d}",
                    "step": step,
                    "step_id": step_id,
                    "derivation_id": derivation_id,
                    "operation": operation,
                    "verb": verb,
                    "edge_type": edge_type,
                    "is_generic": is_generic,
                    "inputs": _ordered_unique(getattr(step, "input_equation_ids", []) or []),
                    "outputs": _ordered_unique(getattr(step, "output_equation_ids", []) or []),
                })
        return records

    @staticmethod
    def _node_from_record(rec: dict) -> ComponentGraphNode:
        step = rec["step"]
        inputs = rec["inputs"]
        outputs = rec["outputs"]
        edge_type = rec["edge_type"]

        linked_equation_ids = _ordered_unique(inputs + outputs)
        linked_derivation_ids = _ordered_unique([rec["derivation_id"], rec["step_id"]])
        linked_claim_ids = _ordered_unique(
            list(getattr(step, "required_claim_ids", []) or [])
            + list(getattr(step, "input_claim_ids", []) or [])
            + list(getattr(step, "output_claim_ids", []) or [])
        )
        linked_evidence_ids = _ordered_unique(getattr(step, "source_evidence_ids", []) or [])

        status, reasons = _node_backing(
            linked_equation_ids=linked_equation_ids,
            linked_derivation_ids=linked_derivation_ids,
            linked_claim_ids=linked_claim_ids,
            linked_evidence_ids=linked_evidence_ids,
            is_generic=rec["is_generic"],
        )

        definitions = outputs if edge_type == "defines" else []
        constraints = outputs if edge_type in ("constrains", "derives", "diagnoses") else []

        return ComponentGraphNode(
            component_id=rec["node_id"],
            label=_build_label(rec["verb"], step, inputs, outputs),
            component_type="TheoryOperationNode",
            review_status="teacher_review_required",
            display_order=int(rec["node_id"].rsplit("_", 1)[-1]),
            origin="derivation_chain",
            operation=rec["operation"],
            theory_object=str(getattr(step, "reason", "") or "").strip()[:120] or rec["verb"],
            graph_layer="main",
            maturity_source="derivation_normalized",
            publish_ready=False,
            input_equation_ids=inputs,
            intermediate_equation_ids=[],
            output_equation_ids=outputs,
            definition_equation_ids=_ordered_unique(definitions),
            constraint_equation_ids=_ordered_unique(constraints),
            review_required_equation_ids=[],
            eliminated_symbols=_ordered_unique(getattr(step, "eliminated_symbols", []) or []),
            retained_symbols=_ordered_unique(getattr(step, "retained_symbols", []) or []),
            derivation_operations=[rec["operation"]] if rec["operation"] else [],
            linked_equation_ids=linked_equation_ids,
            linked_derivation_ids=linked_derivation_ids,
            linked_claim_ids=linked_claim_ids,
            linked_evidence_ids=linked_evidence_ids,
            source_backing_status=status,
            review_reasons=reasons,
        )

    @staticmethod
    def _edges_from_records(records: list[dict]) -> list[ComponentGraphEdge]:
        edges: list[ComponentGraphEdge] = []
        seen: set[tuple[str, str, str]] = set()
        for j, target in enumerate(records):
            target_inputs = set(target["inputs"])
            if not target_inputs:
                continue
            for source in records[:j]:
                overlap = [eq for eq in source["outputs"] if eq in target_inputs]
                if not overlap:
                    continue
                edge_type = target["edge_type"]
                key = (source["node_id"], target["node_id"], edge_type)
                if key in seen or source["node_id"] == target["node_id"]:
                    continue
                seen.add(key)
                step = target["step"]
                review_status, review_reasons = _edge_backing(
                    evidence_equation_ids=overlap,
                    is_generic=target["is_generic"],
                    evidence_derivation_ids=[source["step_id"], target["step_id"]],
                )
                edges.append(ComponentGraphEdge(
                    edge_id=f"theory_edge_{len(edges) + 1:04d}",
                    source=source["node_id"],
                    target=target["node_id"],
                    edge_type=edge_type,
                    support_status="derivation_linked",
                    evidence_claims=[],
                    reasoning=(
                        f"Derivation data flow: {source['operation'] or 'step'} output "
                        f"feeds {target['operation'] or 'step'} ({', '.join(overlap)})."
                    ),
                    confidence=0.9,
                    evidence_equation_ids=_ordered_unique(overlap),
                    review_status=review_status,
                    evidence_derivation_ids=_ordered_unique([
                        source["step_id"], target["step_id"]
                    ]),
                    evidence_claim_ids=_ordered_unique(
                        getattr(step, "required_claim_ids", []) or []
                    ),
                    source_evidence_ids=_ordered_unique(
                        getattr(step, "source_evidence_ids", []) or []
                    ),
                    review_reasons=review_reasons,
                ))
        return edges

    # ------------------------------------------------------------------ #
    # Component-graph fallback path
    # ------------------------------------------------------------------ #

    def _normalize_component_node(self, node: ComponentGraphNode, comp) -> ComponentGraphNode:
        label = str(node.label or "").strip()
        if label.lower() in _GENERIC_LABELS:
            label = _label_for_component(comp) or label
        linked_equation_ids = _ordered_unique(
            list(node.input_equation_ids)
            + list(node.intermediate_equation_ids)
            + list(node.output_equation_ids)
            + list(node.definition_equation_ids)
            + list(node.constraint_equation_ids)
        )
        linked_claim_ids = _ordered_unique(getattr(comp, "linked_claim_ids", []) or [])
        linked_evidence_ids = _ordered_unique(getattr(comp, "linked_evidence_ids", []) or [])
        is_fallback = node.maturity_source == "deterministic_fallback"
        status, reasons = _node_backing(
            linked_equation_ids=linked_equation_ids,
            linked_derivation_ids=node.derivation_operations,
            linked_claim_ids=linked_claim_ids,
            linked_evidence_ids=linked_evidence_ids,
            is_generic=is_fallback,
        )
        return replace(
            node,
            label=label,
            operation=node.operation or str(getattr(comp, "operation", "") or ""),
            theory_object=node.theory_object or str(getattr(comp, "summary", "") or "")[:120],
            graph_layer="debug" if is_fallback else node.graph_layer,
            linked_equation_ids=linked_equation_ids,
            linked_claim_ids=linked_claim_ids,
            linked_evidence_ids=linked_evidence_ids,
            linked_derivation_ids=node.linked_derivation_ids,
            source_backing_status=status,
            review_reasons=reasons,
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
            review_status, review_reasons = _edge_backing(
                evidence_equation_ids=edge.evidence_equation_ids,
                is_generic=edge_type.lower() in ("requires_review", "transforms", "related_to"),
                evidence_claims=edge.evidence_claims,
                evidence_derivation_ids=edge.evidence_derivation_ids,
            )
            result.append(replace(
                edge,
                edge_type=edge_type,
                review_status=review_status,
                review_reasons=review_reasons,
            ))
        return result

    @staticmethod
    def _debug_nodes(nodes: list[ComponentGraphNode]) -> list[ComponentGraphNode]:
        return [
            replace(
                node,
                graph_layer="debug",
                display_order=1000 + idx,
                source_backing_status="inferred",
                review_reasons=_ordered_unique(
                    list(node.review_reasons) + ["fallback_or_inferred_node"]
                ),
            )
            for idx, node in enumerate(nodes)
            if node.maturity_source == "deterministic_fallback"
        ]


# ---------------------------------------------------------------------- #
# Backing computation
# ---------------------------------------------------------------------- #

def _node_backing(
    *,
    linked_equation_ids: list[str],
    linked_derivation_ids: list[str],
    linked_claim_ids: list[str],
    linked_evidence_ids: list[str],
    is_generic: bool,
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if not linked_equation_ids:
        reasons.append("missing_equation_link")
    if not linked_claim_ids:
        reasons.append("missing_atomic_claim")
    if not linked_evidence_ids:
        reasons.append("missing_evidence_link")
    if not linked_derivation_ids:
        reasons.append("missing_derivation_link")
    if is_generic:
        reasons.append("fallback_or_inferred_node")

    has_equation = bool(linked_equation_ids)
    has_strong = bool(linked_claim_ids or linked_evidence_ids)

    if is_generic:
        status = "inferred"
    elif has_equation and has_strong:
        status = "source_backed"
    elif has_equation or linked_derivation_ids:
        status = "partially_source_backed"
    else:
        status = "review_required"

    if status == "source_backed":
        # Confirmed structure: do not surface informational gaps as reasons.
        reasons = []
    return status, reasons


def _edge_backing(
    *,
    evidence_equation_ids: list[str],
    is_generic: bool,
    evidence_claims: list[str] | None = None,
    evidence_derivation_ids: list[str] | None = None,
) -> tuple[str, list[str]]:
    # A derivation-backed edge is just as source-backed as an equation- or
    # claim-backed one (issue #304): equation OR claim OR derivation evidence
    # all count as backing.
    has_evidence = (
        bool(evidence_equation_ids)
        or bool(evidence_claims)
        or bool(evidence_derivation_ids)
    )
    if is_generic:
        return "review_required", ["edge_not_source_backed", "fallback_or_inferred_node"]
    if has_evidence:
        return "source_backed", []
    return "review_required", ["edge_not_source_backed"]


# ---------------------------------------------------------------------- #
# Label / component helpers
# ---------------------------------------------------------------------- #

def _build_label(verb: str, step, inputs: list[str], outputs: list[str]) -> str:
    obj = (outputs[:1] or inputs[:1] or [""])[0]
    if obj:
        return f"{verb} {obj}".strip()
    reason = str(getattr(step, "reason", "") or "").strip()
    if reason and reason.lower() not in _GENERIC_LABELS:
        return reason[:80]
    return verb


def _join_nodes_to_components(
    nodes: list[ComponentGraphNode],
    components: ComponentAssemblyResult,
) -> list[tuple[ComponentGraphNode, object]]:
    comp_by_id = {c.component_id: c for c in components.components}
    return [(node, comp_by_id.get(node.component_id)) for node in nodes]


def _label_for_component(comp) -> str:
    if comp is None:
        return ""
    op = str(getattr(comp, "operation", "") or "")
    if op:
        verb, _edge_type, _generic = classify_operation(op)
        outputs = list(getattr(comp, "output_equation_ids", []) or [])
        if outputs:
            return f"{verb} {outputs[0]}".strip()
        return verb
    summary = str(getattr(comp, "summary", "") or "").strip()
    if summary:
        return summary[:80]
    return ""


def _semantic_edge_type(
    edge: ComponentGraphEdge,
    source: ComponentGraphNode,
    target: ComponentGraphNode,
) -> str:
    tgt_op = str(target.operation or "")
    if tgt_op:
        _verb, edge_type, _generic = classify_operation(tgt_op)
        return edge_type
    return edge.edge_type


def _ordered_unique(values) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result
