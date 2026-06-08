"""Validation for ComponentGraphAgent outputs (Issue #266)."""
from __future__ import annotations

import re

from .schema import (
    GENERIC_EDGE_TYPES,
    GENERIC_OPERATIONS,
    VALID_EDGE_TYPES,
    CartridgeContext,
    ComponentGraphEdge,
    ComponentGraphLLMInput,
    ComponentGraphNode,
    ComponentGraphResult,
    ValidationIssue,
    classify_operation,
)

_GENERIC_LABELS = {"define", "transform", "relate", "result"}
_GENERIC_EDGE_TYPES = {"RELATED_TO", "CORRELATES", "supports", "related_to", "correlates"}
_BIAS_ELIMINATION_NEEDLES = ("bias elimination", "eliminate second", "eliminate third", "second-order bias", "third-order bias")
# Operation edge types whose nodes must expose their output/constraint equations.
_OUTPUT_BEARING_EDGE_TYPES = {"constrains", "derives", "diagnoses"}
# Equation-id-based main labels are not allowed (issue #308): a main node must
# show a theory stage / construction, not "Define eq_2_7" / "Derive result eq_...".
_EQUATION_ID_LABEL_RE = re.compile(
    r"(?:\beq_\w+)|(?:\beq\.?\s*\(?\s*\d)", re.IGNORECASE
)


class ComponentGraphValidator:
    def validate(
        self,
        result: ComponentGraphResult,
        cartridge: CartridgeContext | None = None,
        llm_input: ComponentGraphLLMInput | None = None,
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []

        component_ids = set(n.component_id for n in result.nodes)
        if llm_input:
            component_ids = set(llm_input.component_ids)

        for node in result.nodes:
            if not node.label:
                issues.append(ValidationIssue(
                    "component_graph_node_missing_label",
                    "error",
                    f"node {node.component_id!r} has empty label",
                    f"nodes[{node.component_id}].label",
                ))
            is_main = str(getattr(node, "graph_layer", "main") or "main") == "main"
            if not node.component_type:
                issues.append(ValidationIssue(
                    "component_graph_node_missing_component_type",
                    "error" if is_main else "warning",
                    f"node {node.component_id!r} has empty component_type",
                    f"nodes[{node.component_id}].component_type",
                ))
            label = str(node.label or "").strip()
            if str(getattr(node, "graph_layer", "main") or "main") == "main" and label.lower() in _GENERIC_LABELS:
                issues.append(ValidationIssue(
                    "component_graph_node_label_generic",
                    "error",
                    f"main graph node {node.component_id!r} has generic label {label!r}",
                    f"nodes[{node.component_id}].label",
                ))
            # Issue #308: a main node label must be a theory stage / construction,
            # never an equation-id fallback ("Define eq_2_7", "Derive result eq_...").
            if is_main and _EQUATION_ID_LABEL_RE.search(label):
                issues.append(ValidationIssue(
                    "main_graph_node_equation_id_label",
                    "error",
                    f"main graph node {node.component_id!r} has equation-id label {label!r}",
                    f"nodes[{node.component_id}].label",
                ))
            if (
                str(getattr(node, "graph_layer", "main") or "main") == "main"
                and getattr(node, "maturity_source", "") == "deterministic_fallback"
            ):
                issues.append(ValidationIssue(
                    "main_graph_contains_fallback_component",
                    "error",
                    f"main graph contains fallback component {node.component_id!r}",
                    f"nodes[{node.component_id}]",
                ))
            if getattr(node, "linked_equation_ids", []) and not _has_equation_roles(node):
                issues.append(ValidationIssue(
                    "component_graph_node_missing_equation_roles",
                    "warning",
                    f"node {node.component_id!r} has linked equations but no equation role classification",
                    f"nodes[{node.component_id}]",
                ))
            operation = str(getattr(node, "operation", "") or "").strip()
            if not operation and _looks_like_theory_operation_node(node):
                issues.append(ValidationIssue(
                    "component_graph_node_missing_operation",
                    "error",
                    f"node {node.component_id!r} has no operation",
                    f"nodes[{node.component_id}].operation",
                ))
            if operation == "constrain" and not getattr(node, "output_equation_ids", []):
                issues.append(ValidationIssue(
                    "constraint_component_missing_output_equations",
                    "error",
                    f"constraint node {node.component_id!r} has no output_equation_ids",
                    f"nodes[{node.component_id}].output_equation_ids",
                ))
            label_text = label.lower()
            if any(needle in label_text for needle in _BIAS_ELIMINATION_NEEDLES) and not getattr(node, "eliminated_symbols", []):
                issues.append(ValidationIssue(
                    "bias_elimination_missing_eliminated_symbols",
                    "error",
                    f"bias elimination node {node.component_id!r} has no eliminated_symbols",
                    f"nodes[{node.component_id}].eliminated_symbols",
                ))
            issues += self._check_node_source_backing(node, is_main)

        valid_types = set(VALID_EDGE_TYPES)
        if cartridge:
            raw = cartridge.relation_types.get("relation_types") or []
            for item in raw:
                if isinstance(item, dict) and item.get("id"):
                    valid_types.add(str(item["id"]).upper())
                elif isinstance(item, str):
                    valid_types.add(item.upper())

        seen_keys: set[tuple[str, str, str]] = set()
        seen_pairs: set[tuple[str, str]] = set()
        for edge in result.edges:
            issues += self._check_edge(edge, component_ids, valid_types, seen_keys, seen_pairs)

        if result.confidence < 0.0 or result.confidence > 1.0:
            issues.append(ValidationIssue(
                "confidence_out_of_range",
                "error",
                "result confidence out of [0, 1]",
                "confidence",
            ))

        return issues

    @staticmethod
    def _check_node_source_backing(node: ComponentGraphNode, is_main: bool) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        cid = node.component_id
        backing = str(getattr(node, "source_backing_status", "") or "")
        reasons = list(getattr(node, "review_reasons", []) or [])
        maturity = str(getattr(node, "maturity_source", "") or "")
        operation = str(getattr(node, "operation", "") or "")
        _verb, edge_type, is_generic_op = classify_operation(operation) if operation else ("", "", False)

        linked = _collect_source_links(node)
        is_theory_op = str(getattr(node, "component_type", "") or "") == "TheoryOperationNode"

        # Hard error: every theory-operation node must carry a source link
        # (acceptance criterion #1).
        if is_main and is_theory_op and not linked:
            issues.append(ValidationIssue(
                "theory_node_missing_source_link",
                "error",
                f"node {cid!r} has no equation/derivation/claim/evidence link",
                f"nodes[{cid}]",
            ))

        # Hard error: review_required node must explain why.
        if backing == "review_required" and not reasons:
            issues.append(ValidationIssue(
                "review_required_node_missing_reasons",
                "error",
                f"node {cid!r} is review_required but has empty review_reasons",
                f"nodes[{cid}].review_reasons",
            ))

        # Hard error: fallback/inferred nodes must not be marked source_backed.
        if backing == "source_backed" and maturity == "deterministic_fallback":
            issues.append(ValidationIssue(
                "fallback_node_marked_source_backed",
                "error",
                f"fallback node {cid!r} is marked source_backed",
                f"nodes[{cid}].source_backing_status",
            ))

        # Result/constraint/derive nodes should expose equation links.  For
        # claim-only derivation chains the node may legitimately have no
        # equations; downgrade to warning when claims or evidence back it
        # (issue #334).
        if operation and edge_type in _OUTPUT_BEARING_EDGE_TYPES and is_main:
            if not getattr(node, "linked_equation_ids", []):
                has_claim_backing = bool(
                    getattr(node, "linked_claim_ids", [])
                    or getattr(node, "linked_evidence_ids", [])
                )
                severity = "warning" if has_claim_backing else "error"
                issues.append(ValidationIssue(
                    "output_node_missing_linked_equations",
                    severity,
                    f"{edge_type} node {cid!r} has no linked_equation_ids",
                    f"nodes[{cid}].linked_equation_ids",
                ))
            if edge_type == "derives" and not getattr(node, "linked_derivation_ids", []):
                issues.append(ValidationIssue(
                    "derive_node_missing_derivation_link",
                    "error",
                    f"derive node {cid!r} has no linked_derivation_ids",
                    f"nodes[{cid}].linked_derivation_ids",
                ))

        # Warning: review_required node visible in the main graph.
        if is_main and backing == "review_required":
            issues.append(ValidationIssue(
                "review_required_node_in_main_graph",
                "warning",
                f"review_required node {cid!r} appears in main graph ({', '.join(reasons)})",
                f"nodes[{cid}]",
            ))

        # Warning: node has no claim backing.
        if not getattr(node, "linked_claim_ids", []):
            issues.append(ValidationIssue(
                "theory_node_missing_claim_link",
                "warning",
                f"node {cid!r} has no linked_claim_ids (prefer atomic claims)",
                f"nodes[{cid}].linked_claim_ids",
            ))

        # Warning: generic operation cannot be published as a concrete operation.
        if operation and (operation.strip().lower() in GENERIC_OPERATIONS or is_generic_op):
            issues.append(ValidationIssue(
                "theory_node_generic_operation",
                "warning",
                f"node {cid!r} operation {operation!r} is too generic for the main graph",
                f"nodes[{cid}].operation",
            ))
            # Issue #308: generic operations must never reach the main graph —
            # they belong in the equation_detail / debug layer. A main-layer
            # TheoryOperationNode carrying one is a hard error.
            if is_main:
                issues.append(ValidationIssue(
                    "main_graph_generic_operation",
                    "error",
                    f"main graph node {cid!r} has generic operation {operation!r}",
                    f"nodes[{cid}].operation",
                ))

        return issues

    @staticmethod
    def _check_edge(
        edge: ComponentGraphEdge,
        component_ids: set[str],
        valid_types: set[str],
        seen_keys: set[tuple[str, str, str]],
        seen_pairs: set[tuple[str, str]],
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        eid = edge.edge_id

        if not edge.source:
            issues.append(ValidationIssue(
                "missing_source", "error", f"{eid}: source is empty", f"edges[{eid}].source"
            ))
        elif edge.source not in component_ids:
            issues.append(ValidationIssue(
                "unknown_source_component",
                "error",
                f"{eid}: source {edge.source!r} not in component list",
                f"edges[{eid}].source",
            ))

        if not edge.target:
            issues.append(ValidationIssue(
                "missing_target", "error", f"{eid}: target is empty", f"edges[{eid}].target"
            ))
        elif edge.target not in component_ids:
            issues.append(ValidationIssue(
                "unknown_target_component",
                "error",
                f"{eid}: target {edge.target!r} not in component list",
                f"edges[{eid}].target",
            ))

        if edge.source and edge.target and edge.source == edge.target:
            issues.append(ValidationIssue(
                "self_loop",
                "error",
                f"{eid}: source == target ({edge.source!r})",
                f"edges[{eid}]",
            ))

        if edge.edge_type not in valid_types:
            issues.append(ValidationIssue(
                "invalid_edge_type",
                "warning",
                f"{eid}: edge_type {edge.edge_type!r} not in valid vocabulary",
                f"edges[{eid}].edge_type",
            ))

        key = (edge.source, edge.target, edge.edge_type)
        if key in seen_keys:
            issues.append(ValidationIssue(
                "duplicate_edge",
                "warning",
                f"Duplicate edge ({edge.source} → {edge.target}, {edge.edge_type})",
                f"edges[{eid}]",
            ))
        else:
            seen_keys.add(key)

        pair = (edge.source, edge.target)
        reverse_pair = (edge.target, edge.source)
        if edge.source and edge.target and reverse_pair in seen_pairs:
            issues.append(ValidationIssue(
                "bidirectional_component_edge_pair",
                "warning",
                f"{eid}: reverse edge already exists for {edge.source} <-> {edge.target}",
                f"edges[{eid}]",
            ))
        seen_pairs.add(pair)

        if edge.edge_type in _GENERIC_EDGE_TYPES:
            issues.append(ValidationIssue(
                "component_graph_related_to_edge",
                "warning",
                f"{eid}: {edge.edge_type} is too generic for publish-ready graph structure",
                f"edges[{eid}].edge_type",
            ))

        if not isinstance(edge.evidence_claims, list):
            issues.append(ValidationIssue(
                "invalid_evidence_claims",
                "warning",
                f"{eid}: evidence_claims must be a list",
                f"edges[{eid}].evidence_claims",
            ))
        elif edge.support_status == "llm_inferred" and len(edge.evidence_claims) == 0 and len(edge.evidence_equation_ids) == 0:
            issues.append(ValidationIssue(
                "llm_inferred_no_evidence",
                "warning",
                f"{eid}: llm_inferred edge has no claim or equation evidence; teacher_review_required",
                f"edges[{eid}].evidence_claims",
            ))
        if not isinstance(edge.evidence_equation_ids, list):
            issues.append(ValidationIssue(
                "invalid_evidence_equation_ids",
                "warning",
                f"{eid}: evidence_equation_ids must be a list",
                f"edges[{eid}].evidence_equation_ids",
            ))
        elif (
            len(edge.evidence_claims or []) == 0
            and len(edge.evidence_equation_ids or []) == 0
            and len(getattr(edge, "evidence_derivation_ids", []) or []) == 0
        ):
            issues.append(ValidationIssue(
                "component_graph_edge_no_evidence",
                "warning",
                f"{eid}: edge has no equation or derivation evidence",
                f"edges[{eid}].evidence_equation_ids",
            ))

        if (
            str(getattr(edge, "review_status", "") or "") == "review_required"
            and not list(getattr(edge, "review_reasons", []) or [])
        ):
            issues.append(ValidationIssue(
                "review_required_edge_missing_reasons",
                "warning",
                f"{eid}: review_required edge has empty review_reasons",
                f"edges[{eid}].review_reasons",
            ))

        if edge.confidence < 0.0 or edge.confidence > 1.0:
            issues.append(ValidationIssue(
                "edge_confidence_out_of_range",
                "warning",
                f"{eid}: confidence {edge.confidence} out of [0, 1]",
                f"edges[{eid}].confidence",
            ))

        return issues


def _collect_source_links(node: ComponentGraphNode) -> list[str]:
    links: list[str] = []
    for field_name in (
        "linked_equation_ids",
        "linked_derivation_ids",
        "linked_claim_ids",
        "linked_evidence_ids",
    ):
        links.extend(getattr(node, field_name, []) or [])
    return links


def _has_equation_roles(node: ComponentGraphNode) -> bool:
    return any(
        getattr(node, field_name, []) for field_name in (
            "input_equation_ids",
            "intermediate_equation_ids",
            "output_equation_ids",
            "definition_equation_ids",
            "constraint_equation_ids",
            "review_required_equation_ids",
        )
    )


def _looks_like_theory_operation_node(node: ComponentGraphNode) -> bool:
    text = " ".join([
        str(node.label or ""),
        str(node.component_type or ""),
        str(getattr(node, "theory_object", "") or ""),
    ]).lower()
    return any(
        needle in text for needle in (
            "equation",
            "relation",
            "bias",
            "diagnostic",
            "observable",
            "kernel",
            "constraint",
            "derive",
            "eliminate",
        )
    )
