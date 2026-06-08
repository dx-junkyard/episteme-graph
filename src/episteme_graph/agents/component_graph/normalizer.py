"""Normalize ComponentGraph into a layered, source-backed theory graph.

The LLM edge pass connects assembled components, which is useful for debugging.
The published graph, however, should show the paper's *theory operations*
(definitions, linearizations, eliminations, derivations, diagnostics, …) and
make explicit, for every node and edge:

  * which artifacts (equations / derivations / claims / evidence) back it, and
  * whether it is ``source_backed`` or ``review_required`` (with reasons).

This module builds that graph deterministically from the DerivationChain. It is
domain-independent: node labels and edge types are derived from each step's
``operation`` via :func:`classify_operation`, never from hard-coded paper terms.

Two layers are produced (issue #306):

``main`` (TheoryOperationNode)
    Aggregated, high-level theory operations. Equation-level steps collapse into
    a single node per *theory stage* (``theory_basis`` → ``observation_model`` →
    … → ``diagnostic_application``, issue #308) so the main graph stays small
    (5–8 nodes) and shows the theory backbone rather than one node per equation
    or operation. The stage is derived domain-neutrally from each step's
    operation edge_type family. Main labels are short stage names and never bare
    equation IDs; any longer atomic-claim / reason phrase is kept in the node's
    ``description`` (shown in the UI detail pane) rather than crammed into the
    label. Generic operations (``transform`` / ``relate`` / …) never become
    confirmed main nodes.

``equation_detail`` (EquationOperationNode)
    One node per derivation step, preserving the full equation-by-equation
    trace. Each detail node points back at its main node via
    ``parent_component_id``; generic steps land in the ``debug`` layer instead.
"""
from __future__ import annotations

import re
from dataclasses import replace

from episteme_graph.agents.component_assembly.schema import ComponentAssemblyResult
from episteme_graph.agents.derivation_chain.schema import DerivationChainResult

from .schema import (
    EQUATION_OPERATION_NODE,
    GRAPH_LAYER_DEBUG,
    GRAPH_LAYER_EQUATION_DETAIL,
    GRAPH_LAYER_MAIN,
    THEORY_OPERATION_NODE,
    THEORY_STAGES,
    ComponentGraphEdge,
    ComponentGraphNode,
    ComponentGraphResult,
    classify_operation,
    review_status_for_backing,
    stage_for_edge_type,
    theory_stage_label,
)

_GENERIC_LABELS = {"define", "transform", "relate", "result", "operation"}
# Minimum number of theory-operation nodes (across both layers) required before
# we publish the derivation-derived graph. Whenever a DerivationChain yields at
# least one operation node we adopt it (issue #304); only an empty derivation
# set falls back to the component view.
_MIN_THEORY_NODES = 1


class ComponentGraphNormalizer:
    def normalize(
        self,
        result: ComponentGraphResult,
        components: ComponentAssemblyResult,
        derivations: DerivationChainResult | None = None,
        claim_index: dict[str, dict] | None = None,
    ) -> ComponentGraphResult:
        """Build the layered theory graph.

        Args:
            result: edge-pass output (LLM/component nodes used as a fallback).
            components: assembled components (fallback node source).
            derivations: derivation chains driving the main/detail layers.
            claim_index: optional ``claim_id -> {text, evidence_text, is_atomic}``
                metadata. When present, only atomic claims with non-empty
                evidence count as strong backing (issue #306, acceptance #10/#11).
        """
        claim_index = claim_index or {}
        main_nodes, detail_nodes, edges = self._build_theory_graph(
            derivations, claim_index, components
        )
        theory_nodes = main_nodes + detail_nodes
        if len(theory_nodes) >= _MIN_THEORY_NODES:
            return replace(
                result,
                nodes=theory_nodes + self._debug_nodes(result.nodes),
                edges=edges,
                confidence=max(result.confidence, 0.85),
                review_notes=list(result.review_notes or []) + [
                    "GraphNormalizer built a layered graph: aggregated theory "
                    "operations (main) over equation-level steps (equation_detail)."
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
    # Derivation → layered theory graph (domain-independent)
    # ------------------------------------------------------------------ #

    def _build_theory_graph(
        self,
        derivations: DerivationChainResult | None,
        claim_index: dict[str, dict],
        components: ComponentAssemblyResult | None,
    ) -> tuple[list[ComponentGraphNode], list[ComponentGraphNode], list[ComponentGraphEdge]]:
        component_records = _component_support_records(components)
        records = self._collect_step_records(derivations, component_records)
        if not records:
            return [], [], []

        groups = _group_records(records)
        main_nodes = [
            _main_node_from_group(group, claim_index) for group in groups
        ]
        parent_by_record = {
            rec["detail_id"]: group["node_id"]
            for group in groups
            for rec in group["records"]
        }
        detail_nodes = [
            _detail_node_from_record(rec, parent_by_record.get(rec["detail_id"], ""), claim_index)
            for rec in records
        ]
        main_edges = _main_edges_from_groups(groups)
        if not main_edges and len(groups) >= 2:
            main_edges = _fallback_sequential_edges(groups)
        detail_edges = _detail_edges_from_records(records, claim_index)
        return main_nodes, detail_nodes, main_edges + detail_edges

    @staticmethod
    def _collect_step_records(
        derivations: DerivationChainResult | None,
        component_records: list[dict],
    ) -> list[dict]:
        records: list[dict] = []
        counter = 0
        for chain in getattr(derivations, "chains", []) or []:
            derivation_id = str(getattr(chain, "derivation_id", "") or "")
            chain_component_ids = _ordered_unique(getattr(chain, "linked_component_ids", []) or [])
            for step in getattr(chain, "steps", []) or []:
                counter += 1
                operation = str(getattr(step, "operation", "") or "")
                verb, edge_type, is_generic = classify_operation(operation)
                step_id = str(getattr(step, "step_id", "") or f"step_{counter}")
                records.append({
                    "detail_id": f"eq_op_{counter:04d}",
                    "order": counter,
                    "step": step,
                    "step_id": step_id,
                    "derivation_id": derivation_id,
                    "linked_component_ids": _linked_components_for_step(
                        step=step,
                        derivation_id=derivation_id,
                        chain_component_ids=chain_component_ids,
                        component_records=component_records,
                    ),
                    "component_records": component_records,
                    "operation": operation,
                    "verb": verb,
                    "edge_type": edge_type,
                    "is_generic": is_generic,
                    "inputs": _ordered_unique(getattr(step, "input_equation_ids", []) or []),
                    "outputs": _ordered_unique(getattr(step, "output_equation_ids", []) or []),
                    "input_claims": _ordered_unique(getattr(step, "input_claim_ids", []) or []),
                    "required_claims": _ordered_unique(getattr(step, "required_claim_ids", []) or []),
                    "output_claims": _ordered_unique(getattr(step, "output_claim_ids", []) or []),
                })
        return records

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
            atomic_claim_ids=linked_claim_ids,
            linked_claim_ids=linked_claim_ids,
            linked_evidence_ids=linked_evidence_ids,
            is_generic=is_fallback,
        )
        return replace(
            node,
            label=label,
            operation=node.operation or str(getattr(comp, "operation", "") or ""),
            theory_object=node.theory_object or str(getattr(comp, "summary", "") or "")[:120],
            display_label=node.display_label or _display_label(
                label,
                node.theory_object or str(getattr(comp, "summary", "") or "")[:120],
            ),
            representative_component_id=node.representative_component_id or node.component_id,
            linked_component_ids=node.linked_component_ids or [node.component_id],
            supporting_derivation_ids=node.supporting_derivation_ids or list(node.linked_derivation_ids),
            graph_layer=GRAPH_LAYER_DEBUG if is_fallback else node.graph_layer,
            review_status=review_status_for_backing(status),
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
            source_backing_status, review_status, review_reasons = _edge_backing(
                evidence_equation_ids=edge.evidence_equation_ids,
                is_generic=edge_type.lower() in ("requires_review", "transforms", "related_to"),
                evidence_claims=edge.evidence_claims,
                evidence_derivation_ids=edge.evidence_derivation_ids,
            )
            result.append(replace(
                edge,
                edge_type=edge_type,
                source_backing_status=source_backing_status,
                review_status=review_status,
                review_reasons=review_reasons,
            ))
        return result

    @staticmethod
    def _debug_nodes(nodes: list[ComponentGraphNode]) -> list[ComponentGraphNode]:
        return [
            replace(
                node,
                graph_layer=GRAPH_LAYER_DEBUG,
                display_order=2000 + idx,
                review_status=review_status_for_backing("inferred"),
                source_backing_status="inferred",
                review_reasons=_ordered_unique(
                    list(node.review_reasons) + ["fallback_or_inferred_node"]
                ),
            )
            for idx, node in enumerate(nodes)
            if node.maturity_source == "deterministic_fallback"
        ]


# ---------------------------------------------------------------------- #
# Grouping (equation steps → aggregated main operations)
# ---------------------------------------------------------------------- #

def _group_records(records: list[dict]) -> list[dict]:
    """Aggregate non-generic step records into main theory-stage groups.

    Grouping key: the *theory stage* derived from each step's operation edge_type
    family (issue #308). Steps from every derivation that share a stage collapse
    into one high-level node, so the main graph reads as a backbone of 5–8 stages
    rather than one node per equation/operation. Generic operations never form a
    main node (issue #306/#308); unmapped non-generic edge types fall back to the
    edge type itself as a pseudo-stage so no concrete operation is dropped.
    """
    index: dict[str, dict] = {}
    groups: list[dict] = []
    for rec in records:
        if rec["is_generic"]:
            continue
        stage = stage_for_edge_type(rec["edge_type"]) or rec["edge_type"]
        group = index.get(stage)
        if group is None:
            group = {"stage": stage, "records": []}
            index[stage] = group
            groups.append(group)
        group["records"].append(rec)

    # Order groups by the canonical theory-stage progression so the main graph
    # reads top-to-bottom (basis → … → diagnostic). Unknown stages sort last but
    # keep first-seen order among themselves.
    def _stage_rank(group: dict) -> tuple[int, int]:
        stage = group["stage"]
        canonical = THEORY_STAGES.index(stage) if stage in THEORY_STAGES else len(THEORY_STAGES)
        first_order = min(rec["order"] for rec in group["records"])
        return (canonical, first_order)

    groups.sort(key=_stage_rank)
    for order, group in enumerate(groups, start=1):
        group["order"] = order
        group["node_id"] = f"theory_op_{order:04d}"
        # Representative record drives the node's operation and the edge_type used
        # for definition/constraint roles and main edges.
        rep = _representative_record(group["records"])
        group["rep"] = rep
        group["edge_type"] = rep["edge_type"]
    return groups


def _main_node_from_group(group: dict, claim_index: dict[str, dict]) -> ComponentGraphNode:
    records = group["records"]
    edge_type = group["edge_type"]
    stage = group["stage"]

    inputs = _ordered_unique([eq for rec in records for eq in rec["inputs"]])
    outputs = _ordered_unique([eq for rec in records for eq in rec["outputs"]])
    linked_equation_ids = _ordered_unique(inputs + outputs)
    linked_derivation_ids = _ordered_unique(
        [rec["derivation_id"] for rec in records] + [rec["step_id"] for rec in records]
    )
    linked_claim_ids = _ordered_unique(
        [cid for rec in records for cid in _step_claim_ids(rec["step"])]
    )
    linked_evidence_ids = _ordered_unique(
        [eid for rec in records for eid in (getattr(rec["step"], "source_evidence_ids", []) or [])]
    )
    eliminated = _ordered_unique(
        [s for rec in records for s in (getattr(rec["step"], "eliminated_symbols", []) or [])]
    )
    retained = _ordered_unique(
        [s for rec in records for s in (getattr(rec["step"], "retained_symbols", []) or [])]
    )
    operations = _ordered_unique([rec["operation"] for rec in records if rec["operation"]])
    linked_component_ids = _ordered_unique(
        cid for rec in records for cid in rec.get("linked_component_ids", [])
    )
    support = _support_metadata_for_components(linked_component_ids, records)
    concept_meta = _concepts_for_components(linked_component_ids, records)
    detail_node_ids = [rec["detail_id"] for rec in records]

    atomic_claim_ids = _atomic_claim_ids(linked_claim_ids, claim_index)
    status, reasons = _node_backing(
        linked_equation_ids=linked_equation_ids,
        linked_derivation_ids=linked_derivation_ids,
        atomic_claim_ids=atomic_claim_ids,
        linked_claim_ids=linked_claim_ids,
        linked_evidence_ids=linked_evidence_ids,
        is_generic=False,
    )

    # Claim I/O: aggregate from group records (issue #337).
    group_input_claim_ids = _ordered_unique(
        cid for rec in records for cid in rec.get("input_claims", [])
    )
    group_required_claim_ids = _ordered_unique(
        cid for rec in records for cid in rec.get("required_claims", [])
    )
    group_output_claim_ids = _ordered_unique(
        cid for rec in records for cid in rec.get("output_claims", [])
    )
    # Collect step reasons as review notes (not for node label/description).
    group_review_reasons = [
        str(getattr(rec["step"], "reason", "") or "").strip()
        for rec in records
    ]
    group_review_reason = "; ".join(filter(None, group_review_reasons))[:240]

    rep = group.get("rep") or _representative_record(records)
    label = theory_stage_label(stage)
    theory_object = _theory_object(rep, atomic_claim_ids, claim_index, records)
    description = _build_main_description(records, atomic_claim_ids, claim_index)

    definitions = outputs if edge_type == "defines" else []
    constraints = outputs if edge_type in ("constrains", "derives", "diagnoses") else []

    return ComponentGraphNode(
        component_id=group["node_id"],
        label=label,
        component_type=THEORY_OPERATION_NODE,
        review_status=review_status_for_backing(status),
        display_order=group["order"],
        origin="derivation_chain",
        operation=rep["operation"],
        theory_object=theory_object,
        display_label=_display_label(label, theory_object),
        representative_component_id=linked_component_ids[0] if linked_component_ids else "",
        linked_component_ids=linked_component_ids,
        detail_node_ids=detail_node_ids,
        supporting_derivation_ids=linked_derivation_ids,
        description=description,
        graph_layer=GRAPH_LAYER_MAIN,
        maturity_source="derivation_aggregated",
        publish_ready=status == "source_backed",
        input_equation_ids=inputs,
        intermediate_equation_ids=[],
        output_equation_ids=outputs,
        definition_equation_ids=_ordered_unique(definitions),
        constraint_equation_ids=_ordered_unique(constraints),
        review_required_equation_ids=[],
        eliminated_symbols=eliminated,
        retained_symbols=retained,
        derivation_operations=operations,
        linked_equation_ids=linked_equation_ids,
        linked_derivation_ids=linked_derivation_ids,
        linked_claim_ids=linked_claim_ids,
        linked_evidence_ids=linked_evidence_ids,
        source_backing_status=status,
        review_reasons=reasons,
        member_component_ids=detail_node_ids,
        support_role=support["support_role"],
        supports_claim_ids=support["supports_claim_ids"],
        support_distance_to_headline_claim=support["support_distance_to_headline_claim"],
        support_kind=support["support_kind"],
        concepts=concept_meta["concepts"],
        prerequisite_concepts=concept_meta["prerequisite_concepts"],
        visual_label=label,
        input_claim_ids=group_input_claim_ids,
        output_claim_ids=group_output_claim_ids,
        required_claim_ids=group_required_claim_ids,
        review_reason=group_review_reason,
    )


def _main_edges_from_groups(groups: list[dict]) -> list[ComponentGraphEdge]:
    """High-level theory flow: group output feeds another group's input.

    Edges are created from equation overlap (output equations matching input
    equations) or, when no equation overlap exists, from claim overlap (output
    claims matching input/required claims).  This ensures claim-only derivation
    chains still produce a connected graph (issue #334).
    """
    summaries = []
    for group in groups:
        outputs = {eq for rec in group["records"] for eq in rec["outputs"]}
        inputs = {eq for rec in group["records"] for eq in rec["inputs"]}
        output_claims = {cid for rec in group["records"] for cid in rec.get("output_claims", [])}
        input_claims = {cid for rec in group["records"] for cid in rec.get("input_claims", [])}
        input_claims.update(cid for rec in group["records"] for cid in rec.get("required_claims", []))
        step_ids = [rec["step_id"] for rec in group["records"]]
        claim_ids = _ordered_unique(
            [cid for rec in group["records"] for cid in _step_claim_ids(rec["step"])]
        )
        evidence_ids = _ordered_unique(
            [eid for rec in group["records"]
             for eid in (getattr(rec["step"], "source_evidence_ids", []) or [])]
        )
        summaries.append({
            "group": group,
            "outputs": outputs,
            "inputs": inputs,
            "output_claims": output_claims,
            "input_claims": input_claims,
            "step_ids": step_ids,
            "claim_ids": claim_ids,
            "evidence_ids": evidence_ids,
        })

    edges: list[ComponentGraphEdge] = []
    seen: set[tuple[str, str, str]] = set()
    for tgt in summaries:
        if not tgt["inputs"] and not tgt["input_claims"]:
            continue
        for src in summaries:
            if src["group"]["node_id"] == tgt["group"]["node_id"]:
                continue
            eq_overlap = _ordered_unique([eq for eq in src["outputs"] if eq in tgt["inputs"]])
            claim_overlap = _ordered_unique(
                [cid for cid in src["output_claims"] if cid in tgt["input_claims"]]
            )
            if not eq_overlap and not claim_overlap:
                continue
            edge_type = tgt["group"]["edge_type"]
            key = (src["group"]["node_id"], tgt["group"]["node_id"], edge_type)
            if key in seen:
                continue
            seen.add(key)
            evidence_derivation_ids = _ordered_unique(src["step_ids"] + tgt["step_ids"])
            source_backing_status, review_status, review_reasons = _edge_backing(
                evidence_equation_ids=eq_overlap,
                is_generic=False,
                evidence_claims=tgt["claim_ids"],
                evidence_derivation_ids=evidence_derivation_ids,
            )
            if eq_overlap:
                reasoning = (
                    f"Theory flow: {theory_stage_label(src['group']['stage'])} output feeds "
                    f"{theory_stage_label(tgt['group']['stage'])} ({', '.join(eq_overlap)})."
                )
            else:
                reasoning = (
                    f"Claim flow: {theory_stage_label(src['group']['stage'])} output feeds "
                    f"{theory_stage_label(tgt['group']['stage'])} ({', '.join(claim_overlap[:3])})."
                )
            edges.append(ComponentGraphEdge(
                edge_id=f"theory_edge_{len(edges) + 1:04d}",
                source=src["group"]["node_id"],
                target=tgt["group"]["node_id"],
                edge_type=edge_type,
                support_status="derivation_linked",
                evidence_claims=[],
                reasoning=reasoning,
                confidence=0.9,
                evidence_equation_ids=eq_overlap,
                source_backing_status=source_backing_status,
                review_status=review_status,
                evidence_derivation_ids=evidence_derivation_ids,
                evidence_claim_ids=_ordered_unique(claim_overlap + tgt["claim_ids"]),
                source_evidence_ids=tgt["evidence_ids"],
                review_reasons=review_reasons,
            ))
    return edges


def _fallback_sequential_edges(groups: list[dict]) -> list[ComponentGraphEdge]:
    """Sequential stage edges when neither equation nor claim flow produces edges.

    Connects consecutive main nodes in stage order.  Marked ``review_required``
    because the connection is inferred from ordering, not from data overlap.
    """
    edges: list[ComponentGraphEdge] = []
    for i in range(len(groups) - 1):
        src = groups[i]
        tgt = groups[i + 1]
        edge_type = tgt["edge_type"]
        evidence_derivation_ids = _ordered_unique(
            [rec["step_id"] for rec in src["records"]]
            + [rec["step_id"] for rec in tgt["records"]]
        )
        claim_ids = _ordered_unique(
            [cid for rec in tgt["records"] for cid in _step_claim_ids(rec["step"])]
        )
        evidence_ids = _ordered_unique(
            [eid for rec in tgt["records"]
             for eid in (getattr(rec["step"], "source_evidence_ids", []) or [])]
        )
        edges.append(ComponentGraphEdge(
            edge_id=f"theory_edge_{len(edges) + 1:04d}",
            source=src["node_id"],
            target=tgt["node_id"],
            edge_type=edge_type,
            support_status="derivation_linked",
            evidence_claims=[],
            reasoning=(
                f"Sequential stage flow: {theory_stage_label(src['stage'])} → "
                f"{theory_stage_label(tgt['stage'])}."
            ),
            confidence=0.7,
            evidence_equation_ids=[],
            source_backing_status="review_required",
            review_status="review_required",
            evidence_derivation_ids=evidence_derivation_ids,
            evidence_claim_ids=claim_ids,
            source_evidence_ids=evidence_ids,
            review_reasons=["edge_not_source_backed"],
        ))
    return edges


# ---------------------------------------------------------------------- #
# Equation-detail layer (one node / edge per derivation step)
# ---------------------------------------------------------------------- #

def _detail_node_from_record(
    rec: dict,
    parent_id: str,
    claim_index: dict[str, dict],
) -> ComponentGraphNode:
    step = rec["step"]
    inputs = rec["inputs"]
    outputs = rec["outputs"]
    edge_type = rec["edge_type"]

    linked_equation_ids = _ordered_unique(inputs + outputs)
    linked_derivation_ids = _ordered_unique([rec["derivation_id"], rec["step_id"]])
    linked_claim_ids = _ordered_unique(_step_claim_ids(step))
    linked_evidence_ids = _ordered_unique(getattr(step, "source_evidence_ids", []) or [])
    atomic_claim_ids = _atomic_claim_ids(linked_claim_ids, claim_index)

    status, reasons = _node_backing(
        linked_equation_ids=linked_equation_ids,
        linked_derivation_ids=linked_derivation_ids,
        atomic_claim_ids=atomic_claim_ids,
        linked_claim_ids=linked_claim_ids,
        linked_evidence_ids=linked_evidence_ids,
        is_generic=rec["is_generic"],
    )

    definitions = outputs if edge_type == "defines" else []
    constraints = outputs if edge_type in ("constrains", "derives", "diagnoses") else []
    # Generic / inferred steps are kept for traceability but never confirmed.
    layer = GRAPH_LAYER_DEBUG if rec["is_generic"] else GRAPH_LAYER_EQUATION_DETAIL

    detail_label = _build_detail_label(rec["verb"], step, inputs, outputs)
    step_reason = str(getattr(step, "reason", "") or "").strip()
    step_input_claims = _ordered_unique(getattr(step, "input_claim_ids", []) or [])
    step_required_claims = _ordered_unique(getattr(step, "required_claim_ids", []) or [])
    step_output_claims = _ordered_unique(getattr(step, "output_claim_ids", []) or [])
    visual = _build_detail_visual_label(rec["verb"], inputs, outputs,
                                        step_input_claims, step_output_claims)

    return ComponentGraphNode(
        component_id=rec["detail_id"],
        label=detail_label,
        component_type=EQUATION_OPERATION_NODE,
        review_status=review_status_for_backing(status),
        display_order=1000 + rec["order"],
        origin="derivation_chain",
        operation=rec["operation"],
        theory_object=rec["verb"],
        display_label=detail_label,
        description="",
        linked_component_ids=list(rec.get("linked_component_ids", [])),
        supporting_derivation_ids=linked_derivation_ids,
        graph_layer=layer,
        maturity_source="derivation_step",
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
        parent_component_id=parent_id,
        visual_label=visual,
        input_claim_ids=step_input_claims,
        output_claim_ids=step_output_claims,
        required_claim_ids=step_required_claims,
        review_reason=step_reason[:240],
        **_support_metadata_for_components(list(rec.get("linked_component_ids", [])), [rec]),
        **_concepts_for_components(list(rec.get("linked_component_ids", [])), [rec]),
    )


def _detail_edges_from_records(
    records: list[dict],
    claim_index: dict[str, dict],
) -> list[ComponentGraphEdge]:
    edges: list[ComponentGraphEdge] = []
    seen: set[tuple[str, str, str]] = set()
    for j, target in enumerate(records):
        target_inputs = set(target["inputs"])
        target_input_claims = set(target.get("input_claims", []))
        target_input_claims.update(target.get("required_claims", []))
        if not target_inputs and not target_input_claims:
            continue
        for source in records[:j]:
            eq_overlap = [eq for eq in source["outputs"] if eq in target_inputs]
            claim_overlap = [
                cid for cid in source.get("output_claims", [])
                if cid in target_input_claims
            ]
            if not eq_overlap and not claim_overlap:
                continue
            edge_type = target["edge_type"]
            key = (source["detail_id"], target["detail_id"], edge_type)
            if key in seen or source["detail_id"] == target["detail_id"]:
                continue
            seen.add(key)
            step = target["step"]
            source_backing_status, review_status, review_reasons = _edge_backing(
                evidence_equation_ids=eq_overlap,
                is_generic=target["is_generic"],
                evidence_derivation_ids=[source["step_id"], target["step_id"]],
            )
            if eq_overlap:
                reasoning = (
                    f"Derivation data flow: {source['operation'] or 'step'} output "
                    f"feeds {target['operation'] or 'step'} ({', '.join(eq_overlap)})."
                )
            else:
                reasoning = (
                    f"Claim flow: {source['operation'] or 'step'} output "
                    f"feeds {target['operation'] or 'step'} ({', '.join(claim_overlap[:3])})."
                )
            edges.append(ComponentGraphEdge(
                edge_id=f"eq_edge_{len(edges) + 1:04d}",
                source=source["detail_id"],
                target=target["detail_id"],
                edge_type=edge_type,
                support_status="derivation_linked",
                evidence_claims=[],
                reasoning=reasoning,
                confidence=0.9,
                evidence_equation_ids=_ordered_unique(eq_overlap),
                source_backing_status=source_backing_status,
                review_status=review_status,
                evidence_derivation_ids=_ordered_unique([
                    source["step_id"], target["step_id"]
                ]),
                evidence_claim_ids=_ordered_unique(claim_overlap + _step_claim_ids(step)),
                source_evidence_ids=_ordered_unique(
                    getattr(step, "source_evidence_ids", []) or []
                ),
                review_reasons=review_reasons,
            ))
    return edges


# ---------------------------------------------------------------------- #
# Backing computation
# ---------------------------------------------------------------------- #

def _node_backing(
    *,
    linked_equation_ids: list[str],
    linked_derivation_ids: list[str],
    atomic_claim_ids: list[str],
    linked_claim_ids: list[str],
    linked_evidence_ids: list[str],
    is_generic: bool,
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if not linked_equation_ids:
        reasons.append("missing_equation_link")
    # Atomic claims are preferred backing; a non-atomic / empty-evidence claim
    # does not remove the gap (issue #306, acceptance #10/#11).
    if not atomic_claim_ids:
        reasons.append("missing_atomic_claim")
    if not linked_evidence_ids:
        reasons.append("missing_evidence_link")
    if not linked_derivation_ids:
        reasons.append("missing_derivation_link")
    if is_generic:
        reasons.append("fallback_or_inferred_node")

    has_equation = bool(linked_equation_ids)
    has_strong = bool(atomic_claim_ids or linked_evidence_ids)

    if is_generic:
        status = "inferred"
    elif has_equation and has_strong:
        status = "source_backed"
    elif has_equation or linked_derivation_ids:
        status = "partially_source_backed"
    else:
        status = "review_required"

    if status == "source_backed":
        # Confirmed structure: drop informational gaps. Keep only the
        # missing_atomic_claim warning (issue #306): equations / evidence back
        # the node, but no minimal atomic claim directly supports its meaning,
        # so reviewers should still be told the atomic backing is absent.
        reasons = [] if atomic_claim_ids else ["missing_atomic_claim"]
    return status, _ordered_unique(reasons)


def _edge_backing(
    *,
    evidence_equation_ids: list[str],
    is_generic: bool,
    evidence_claims: list[str] | None = None,
    evidence_derivation_ids: list[str] | None = None,
) -> tuple[str, str, list[str]]:
    """Return ``(source_backing_status, review_status, review_reasons)`` for an edge.

    ``source_backing_status`` uses the same vocabulary as nodes (issue #311
    criterion 6); ``review_status`` is derived from it via
    ``review_status_for_backing`` so the two fields never disagree.
    """
    # A derivation-backed edge is just as source-backed as an equation- or
    # claim-backed one (issue #304): equation OR claim OR derivation evidence
    # all count as backing.
    has_evidence = (
        bool(evidence_equation_ids)
        or bool(evidence_claims)
        or bool(evidence_derivation_ids)
    )
    if is_generic:
        status = "inferred"
        reasons = ["edge_not_source_backed", "fallback_or_inferred_node"]
    elif has_evidence:
        status = "source_backed"
        reasons = []
    else:
        status = "review_required"
        reasons = ["edge_not_source_backed"]
    return status, review_status_for_backing(status), reasons


# ---------------------------------------------------------------------- #
# Claim helpers (atomic-claim preference, issue #306)
# ---------------------------------------------------------------------- #

def _step_claim_ids(step) -> list[str]:
    return (
        list(getattr(step, "required_claim_ids", []) or [])
        + list(getattr(step, "input_claim_ids", []) or [])
        + list(getattr(step, "output_claim_ids", []) or [])
    )


def _atomic_claim_ids(claim_ids: list[str], claim_index: dict[str, dict]) -> list[str]:
    """Return the subset of claim IDs that count as strong (atomic) backing.

    When no metadata exists for a claim we keep it (unknown but usable, so
    derivation-linked claims still back a node, issue #304). When metadata is
    present, only atomic claims with non-empty evidence text qualify.
    """
    if not claim_ids:
        return []
    strong: list[str] = []
    for cid in claim_ids:
        meta = claim_index.get(cid)
        if meta is None:
            strong.append(cid)
            continue
        if _claim_is_atomic(meta) and str(meta.get("evidence_text") or "").strip():
            strong.append(cid)
    return _ordered_unique(strong)


def _claim_is_atomic(meta: dict) -> bool:
    level = str(meta.get("claim_level") or meta.get("level") or meta.get("granularity") or "").lower()
    if level in ("paper", "paper_level", "document", "global"):
        return False
    if meta.get("is_atomic") is False:
        return False
    text = str(meta.get("text") or "").strip()
    # Long, multi-sentence claims tend to mix background/method/result and are
    # not atomic backing for a single theory operation.
    return bool(text) and len(text) <= 240


# ---------------------------------------------------------------------- #
# Label / component helpers
# ---------------------------------------------------------------------- #

def _build_detail_label(verb: str, step, inputs: list[str], outputs: list[str]) -> str:
    """Detail-level label: equation IDs are acceptable here (traceability).

    Never uses step.reason as a label — reason is review/extraction metadata
    and belongs in the node's ``description``, not in its graph label (#337).
    """
    obj = (outputs[:1] or inputs[:1] or [""])[0]
    if obj:
        return f"{verb} {obj}".strip()
    return verb


def _build_detail_visual_label(
    verb: str,
    inputs: list[str],
    outputs: list[str],
    input_claims: list[str],
    output_claims: list[str],
) -> str:
    """Short graph-canvas label for detail nodes, showing I/O flow (#337).

    Format: ``"verb\\ninput → output"`` (capped at 30 chars per line).
    Falls back to verb only when no I/O identifiers are available.
    """
    src = (inputs[:1] or input_claims[:1] or [""])[0]
    dst = (outputs[:1] or output_claims[:1] or [""])[0]
    if src and dst:
        flow = f"{src} → {dst}"
        return f"{verb}\n{flow[:30]}"
    if src or dst:
        return f"{verb}\n{(src or dst)[:30]}"
    return verb


def _representative_record(records: list[dict]) -> dict:
    """Pick the member whose operation carries the most specific object phrase."""
    return max(records, key=lambda r: (len(str(r["operation"] or "")), -r["order"]))


def _build_main_description(
    records: list[dict],
    atomic_claim_ids: list[str],
    claim_index: dict[str, dict],
) -> str:
    """Reader-facing explanation for a main node (issue #308, #337).

    Returns only genuinely reader-friendly content: the atomic-claim text that
    backs the node. Step reasons (extraction/review metadata) are NOT included
    here — they live in ``review_reason`` and are shown separately in the UI.
    Returns ``""`` when no reader-friendly phrase is available.
    """
    claim_phrase = _atomic_claim_phrase(atomic_claim_ids, claim_index, limit=240)
    if claim_phrase:
        return claim_phrase
    return ""


def _looks_like_equation_id(text: str) -> bool:
    """True if a phrase is dominated by equation-id tokens (e.g. ``eq_2_7``).

    Used to avoid enriching a main label with text that is really just an
    equation reference (acceptance: main labels are never equation-id based).
    """
    stripped = str(text or "").strip()
    if not stripped:
        return False
    return bool(re.fullmatch(r"(eq[_\.\s]*\(?\s*[\dA-Za-z\.\)]+\s*[, ]*)+", stripped, re.IGNORECASE))


def _atomic_claim_phrase(
    atomic_claim_ids: list[str],
    claim_index: dict[str, dict],
    limit: int = 80,
) -> str:
    for cid in atomic_claim_ids:
        meta = claim_index.get(cid)
        if not meta:
            continue
        text = str(meta.get("text") or meta.get("predicate") or "").strip()
        if text:
            return text[:limit]
    return ""


def _theory_object(
    rep: dict,
    atomic_claim_ids: list[str],
    claim_index: dict[str, dict],
    records: list[dict] | None = None,
) -> str:
    phrase = _atomic_claim_phrase(atomic_claim_ids, claim_index)
    if phrase and not _looks_like_equation_id(phrase):
        return phrase[:120]
    records = records or [rep]
    symbols = _ordered_unique(
        s
        for rec in records
        for s in (
            list(getattr(rec["step"], "eliminated_symbols", []) or [])
            + list(getattr(rec["step"], "retained_symbols", []) or [])
        )
    )
    if symbols:
        return ", ".join(symbols[:3])[:120]
    # Never fall back to step.reason here (#337) — reason is extraction metadata,
    # not a theory-object phrase. It lives in review_reason instead.
    operation = str(rep.get("operation") or rep.get("verb") or "")
    return operation.replace("_", " ")[:120]


def _display_label(label: str, theory_object: str) -> str:
    stage = str(label or "").strip()
    obj = str(theory_object or "").strip()
    if not stage:
        return obj
    if not obj or _looks_like_equation_id(obj):
        return stage
    if obj.lower() == stage.lower():
        return stage
    return f"{stage}: {obj}"


def _component_support_records(components: ComponentAssemblyResult | None) -> list[dict]:
    records: list[dict] = []
    for comp in getattr(components, "components", []) or []:
        component_id = str(getattr(comp, "component_id", "") or "")
        if not component_id:
            continue
        eq_ids = _ordered_unique(
            list(getattr(comp, "linked_equation_ids", []) or [])
            + list(getattr(comp, "input_equation_ids", []) or [])
            + list(getattr(comp, "intermediate_equation_ids", []) or [])
            + list(getattr(comp, "output_equation_ids", []) or [])
            + list(getattr(comp, "definition_equation_ids", []) or [])
            + list(getattr(comp, "constraint_equation_ids", []) or [])
        )
        records.append({
            "component_id": component_id,
            "linked_derivation_ids": _ordered_unique(
                getattr(comp, "linked_derivation_ids", []) or []
            ),
            "linked_equation_ids": eq_ids,
            "operation": str(getattr(comp, "operation", "") or ""),
            "support_role": str(getattr(comp, "support_role", "") or ""),
            "supports_claim_ids": list(getattr(comp, "supports_claim_ids", []) or []),
            "support_distance_to_headline_claim": int(getattr(comp, "support_distance_to_headline_claim", 0) or 0),
            "support_kind": str(getattr(comp, "support_kind", "") or ""),
            "concepts": list(getattr(comp, "concepts", []) or []),
            "prerequisite_concepts": list(getattr(comp, "prerequisite_concepts", []) or []),
        })
    return records


def _support_metadata_for_components(component_ids: list[str], records: list[dict]) -> dict:
    support_records = []
    wanted = set(component_ids or [])
    for rec in records or []:
        for comp in rec.get("component_records", []) or []:
            if not wanted or comp.get("component_id") in wanted:
                support_records.append(comp)
    if not support_records:
        support_records = [
            rec for rec in records or []
            if rec.get("support_role") or rec.get("supports_claim_ids") or rec.get("support_kind")
        ]
    role_order = ["derivation_core", "result", "observable_bridge", "application", "limitation", "theory_base"]
    selected = ""
    for role in role_order:
        if any(rec.get("support_role") == role for rec in support_records):
            selected = role
            break
    if not selected:
        selected = ""
    claim_ids = _ordered_unique(
        cid for rec in support_records for cid in (rec.get("supports_claim_ids") or [])
    )
    distances = [
        int(rec.get("support_distance_to_headline_claim") or 0)
        for rec in support_records
        if rec.get("support_distance_to_headline_claim") is not None
    ]
    kind = next((rec.get("support_kind") for rec in support_records if rec.get("support_role") == selected and rec.get("support_kind")), "")
    return {
        "support_role": selected,
        "supports_claim_ids": claim_ids,
        "support_distance_to_headline_claim": min(distances or [0]),
        "support_kind": kind,
    }


def _concepts_for_components(component_ids: list[str], records: list[dict]) -> dict:
    """Roll up component concepts onto a graph node (issue #8)."""
    support_records = []
    wanted = set(component_ids or [])
    for rec in records or []:
        for comp in rec.get("component_records", []) or []:
            if not wanted or comp.get("component_id") in wanted:
                support_records.append(comp)
    if not support_records:
        support_records = [
            rec for rec in records or []
            if rec.get("concepts") or rec.get("prerequisite_concepts")
        ]
    concepts = _ordered_unique(
        c for rec in support_records for c in (rec.get("concepts") or [])
    )
    prerequisite_concepts = _ordered_unique(
        c for rec in support_records for c in (rec.get("prerequisite_concepts") or [])
    )
    return {
        "concepts": concepts,
        "prerequisite_concepts": prerequisite_concepts,
    }


def _linked_components_for_step(
    *,
    step,
    derivation_id: str,
    chain_component_ids: list[str],
    component_records: list[dict],
) -> list[str]:
    step_id = str(getattr(step, "step_id", "") or "")
    step_eqs = set(
        _ordered_unique(
            list(getattr(step, "input_equation_ids", []) or [])
            + list(getattr(step, "output_equation_ids", []) or [])
        )
    )
    linked = list(chain_component_ids)
    for rec in component_records:
        derivation_ids = set(rec["linked_derivation_ids"])
        equation_ids = set(rec["linked_equation_ids"])
        if (
            derivation_id in derivation_ids
            or step_id in derivation_ids
            or bool(step_eqs & equation_ids)
        ):
            linked.append(rec["component_id"])
    return _ordered_unique(linked)


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
