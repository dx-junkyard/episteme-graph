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
    Aggregated, high-level theory operations. Equation-level steps that share a
    derivation and an operation family collapse into a single node so the main
    graph stays small and shows the theory backbone rather than one node per
    equation. Generic operations (``transform`` / ``relate`` / …) never become
    confirmed main nodes.

``equation_detail`` (EquationOperationNode)
    One node per derivation step, preserving the full equation-by-equation
    trace. Each detail node points back at its main node via
    ``parent_component_id``; generic steps land in the ``debug`` layer instead.
"""
from __future__ import annotations

from dataclasses import replace

from episteme_graph.agents.component_assembly.schema import ComponentAssemblyResult
from episteme_graph.agents.derivation_chain.schema import DerivationChainResult

from .schema import (
    EQUATION_OPERATION_NODE,
    GRAPH_LAYER_DEBUG,
    GRAPH_LAYER_EQUATION_DETAIL,
    GRAPH_LAYER_MAIN,
    THEORY_OPERATION_NODE,
    ComponentGraphEdge,
    ComponentGraphNode,
    ComponentGraphResult,
    classify_operation,
    review_status_for_backing,
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
            derivations, claim_index
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
    ) -> tuple[list[ComponentGraphNode], list[ComponentGraphNode], list[ComponentGraphEdge]]:
        records = self._collect_step_records(derivations)
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
        detail_edges = _detail_edges_from_records(records, claim_index)
        return main_nodes, detail_nodes, main_edges + detail_edges

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
                    "detail_id": f"eq_op_{counter:04d}",
                    "order": counter,
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
    """Aggregate non-generic step records into main theory-operation groups.

    Grouping key: ``(derivation_id, operation family)``. Consecutive equation
    steps that share a derivation and an operation family collapse into one
    high-level node; generic operations never form a main node (issue #306).
    """
    groups: list[dict] = []
    index: dict[tuple[str, str], dict] = {}
    order = 0
    for rec in records:
        if rec["is_generic"]:
            continue
        key = (rec["derivation_id"], rec["edge_type"])
        group = index.get(key)
        if group is None:
            order += 1
            group = {
                "node_id": f"theory_op_{order:04d}",
                "order": order,
                "derivation_id": rec["derivation_id"],
                "edge_type": rec["edge_type"],
                "records": [],
            }
            index[key] = group
            groups.append(group)
        group["records"].append(rec)
    return groups


def _main_node_from_group(group: dict, claim_index: dict[str, dict]) -> ComponentGraphNode:
    records = group["records"]
    edge_type = group["edge_type"]

    inputs = _ordered_unique([eq for rec in records for eq in rec["inputs"]])
    outputs = _ordered_unique([eq for rec in records for eq in rec["outputs"]])
    linked_equation_ids = _ordered_unique(inputs + outputs)
    linked_derivation_ids = _ordered_unique(
        [group["derivation_id"]] + [rec["step_id"] for rec in records]
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

    atomic_claim_ids = _atomic_claim_ids(linked_claim_ids, claim_index)
    status, reasons = _node_backing(
        linked_equation_ids=linked_equation_ids,
        linked_derivation_ids=linked_derivation_ids,
        atomic_claim_ids=atomic_claim_ids,
        linked_claim_ids=linked_claim_ids,
        linked_evidence_ids=linked_evidence_ids,
        is_generic=False,
    )

    rep = _representative_record(records)
    label, label_from_eq_only = _build_main_label(
        rep, records, inputs, outputs, eliminated, retained, atomic_claim_ids, claim_index
    )
    # A node whose label could only be built from an equation ID is, by
    # definition, not backed by an atomic claim (acceptance #3 / #9). A
    # source-backed node (it has evidence) keeps its status; only the
    # remaining cases gain the missing_atomic_claim reason.
    if label_from_eq_only and status != "source_backed":
        reasons = _ordered_unique(reasons + ["missing_atomic_claim"])

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
        theory_object=_theory_object(rep, atomic_claim_ids, claim_index),
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
        member_component_ids=[rec["detail_id"] for rec in records],
    )


def _main_edges_from_groups(groups: list[dict]) -> list[ComponentGraphEdge]:
    """High-level theory flow: group output equation feeds another group's input."""
    summaries = []
    for group in groups:
        outputs = {eq for rec in group["records"] for eq in rec["outputs"]}
        inputs = {eq for rec in group["records"] for eq in rec["inputs"]}
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
            "step_ids": step_ids,
            "claim_ids": claim_ids,
            "evidence_ids": evidence_ids,
        })

    edges: list[ComponentGraphEdge] = []
    seen: set[tuple[str, str, str]] = set()
    for tgt in summaries:
        if not tgt["inputs"]:
            continue
        for src in summaries:
            if src["group"]["node_id"] == tgt["group"]["node_id"]:
                continue
            overlap = _ordered_unique([eq for eq in src["outputs"] if eq in tgt["inputs"]])
            if not overlap:
                continue
            edge_type = tgt["group"]["edge_type"]
            key = (src["group"]["node_id"], tgt["group"]["node_id"], edge_type)
            if key in seen:
                continue
            seen.add(key)
            evidence_derivation_ids = _ordered_unique(src["step_ids"] + tgt["step_ids"])
            review_status, review_reasons = _edge_backing(
                evidence_equation_ids=overlap,
                is_generic=False,
                evidence_claims=tgt["claim_ids"],
                evidence_derivation_ids=evidence_derivation_ids,
            )
            edges.append(ComponentGraphEdge(
                edge_id=f"theory_edge_{len(edges) + 1:04d}",
                source=src["group"]["node_id"],
                target=tgt["group"]["node_id"],
                edge_type=edge_type,
                support_status="derivation_linked",
                evidence_claims=[],
                reasoning=(
                    f"Theory flow: {src['group']['edge_type']} output feeds "
                    f"{tgt['group']['edge_type']} ({', '.join(overlap)})."
                ),
                confidence=0.9,
                evidence_equation_ids=overlap,
                review_status=review_status,
                evidence_derivation_ids=evidence_derivation_ids,
                evidence_claim_ids=tgt["claim_ids"],
                source_evidence_ids=tgt["evidence_ids"],
                review_reasons=review_reasons,
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

    return ComponentGraphNode(
        component_id=rec["detail_id"],
        label=_build_detail_label(rec["verb"], step, inputs, outputs),
        component_type=EQUATION_OPERATION_NODE,
        review_status=review_status_for_backing(status),
        display_order=1000 + rec["order"],
        origin="derivation_chain",
        operation=rec["operation"],
        theory_object=str(getattr(step, "reason", "") or "").strip()[:120] or rec["verb"],
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
    )


def _detail_edges_from_records(
    records: list[dict],
    claim_index: dict[str, dict],
) -> list[ComponentGraphEdge]:
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
            key = (source["detail_id"], target["detail_id"], edge_type)
            if key in seen or source["detail_id"] == target["detail_id"]:
                continue
            seen.add(key)
            step = target["step"]
            review_status, review_reasons = _edge_backing(
                evidence_equation_ids=overlap,
                is_generic=target["is_generic"],
                evidence_derivation_ids=[source["step_id"], target["step_id"]],
            )
            edges.append(ComponentGraphEdge(
                edge_id=f"eq_edge_{len(edges) + 1:04d}",
                source=source["detail_id"],
                target=target["detail_id"],
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
                evidence_claim_ids=_ordered_unique(_step_claim_ids(step)),
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
        # Confirmed structure: do not surface informational gaps as reasons.
        reasons = []
    return status, _ordered_unique(reasons)


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
    """Equation-level label: equation IDs are acceptable here (traceability)."""
    obj = (outputs[:1] or inputs[:1] or [""])[0]
    if obj:
        return f"{verb} {obj}".strip()
    reason = str(getattr(step, "reason", "") or "").strip()
    if reason and reason.lower() not in _GENERIC_LABELS:
        return reason[:80]
    return verb


def _representative_record(records: list[dict]) -> dict:
    """Pick the member whose operation carries the most specific object phrase."""
    return max(records, key=lambda r: (len(str(r["operation"] or "")), -r["order"]))


def _build_main_label(
    rep: dict,
    records: list[dict],
    inputs: list[str],
    outputs: list[str],
    eliminated: list[str],
    retained: list[str],
    atomic_claim_ids: list[str],
    claim_index: dict[str, dict],
) -> tuple[str, bool]:
    """Domain-neutral ``operation + theory object`` label for a main node.

    Returns ``(label, from_equation_id_only)``. The object phrase is taken, in
    order, from: atomic claim text → step reason → eliminated/retained symbols →
    equation ID (last resort, which flags the node, acceptance #3).
    """
    verb = str(rep["verb"] or "").strip()
    # 1. atomic claim text
    claim_phrase = _atomic_claim_phrase(atomic_claim_ids, claim_index)
    if claim_phrase:
        return _compose_label(verb, claim_phrase), False
    # 2. step reason (skip bare generic words)
    for rec in [rep] + records:
        reason = str(getattr(rec["step"], "reason", "") or "").strip()
        if reason and reason.lower() not in _GENERIC_LABELS:
            return _compose_label(verb, reason[:80]), False
    # 3. eliminated / retained symbols
    symbols = eliminated or retained
    if symbols:
        return _compose_label(verb, ", ".join(symbols[:3])), False
    # 4. the verb already names a specific operation (multi-word from operation)
    if " " in verb:
        return verb, False
    # 5. equation ID fallback only — flags the node as not atomically backed
    obj = (outputs[:1] or inputs[:1] or [""])[0]
    if obj:
        return f"{verb} {obj}".strip(), True
    return verb, True


def _compose_label(verb: str, phrase: str) -> str:
    verb = verb.strip()
    phrase = phrase.strip()
    if not phrase:
        return verb
    if not verb:
        return phrase
    # Avoid duplicating the verb if the phrase already starts with it.
    if phrase.lower().startswith(verb.lower()):
        return phrase
    return f"{verb} {phrase}"


def _atomic_claim_phrase(atomic_claim_ids: list[str], claim_index: dict[str, dict]) -> str:
    for cid in atomic_claim_ids:
        meta = claim_index.get(cid)
        if not meta:
            continue
        text = str(meta.get("text") or meta.get("predicate") or "").strip()
        if text:
            return text[:80]
    return ""


def _theory_object(rep: dict, atomic_claim_ids: list[str], claim_index: dict[str, dict]) -> str:
    phrase = _atomic_claim_phrase(atomic_claim_ids, claim_index)
    if phrase:
        return phrase[:120]
    reason = str(getattr(rep["step"], "reason", "") or "").strip()
    return reason[:120] or str(rep["verb"] or "")


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
