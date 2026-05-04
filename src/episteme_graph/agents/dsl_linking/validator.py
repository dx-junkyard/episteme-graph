"""Validation for DSLLinkingAgent outputs."""
from __future__ import annotations

import re

from .schema import (
    CONCRETE_NODE_TYPES,
    CORE_PREDICATES,
    GRAPH_HINT_TYPES,
    NODE_TYPES,
    POLARITIES,
    PREDICATE_PREFERRED_SOURCES,
    PREDICATE_PREFERRED_TARGETS,
    PROXY_NODE_TYPES,
    SOURCE_KINDS,
    DSLLinkingResult,
    ValidationIssue,
)

_EVAL_VALUE_PATTERNS = (
    re.compile(r"\b(small|large|negligible|significant|sufficient|adequate)\b", re.IGNORECASE),
    re.compile(r"\b(is|are|was|were)\s+(small|large|negligible|good|bad)\b", re.IGNORECASE),
)


class DSLLinkingValidator:
    def validate(self, result: DSLLinkingResult) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if not result.nodes:
            return [ValidationIssue("no_nodes", "error", "nodes is empty", "nodes")]
        node_ids = {n.node_id for n in result.nodes}
        node_types = {n.node_id: n.node_type for n in result.nodes}
        issues += self._check_nodes(result)
        issues += self._check_edges(result, node_ids, node_types)
        issues += self._check_graph_hints(result, node_ids)
        issues += self._check_confidence(result)
        issues += self._check_graph_shape(result)
        issues += self._check_concrete_node_coverage(result)
        return issues

    def _check_nodes(self, result: DSLLinkingResult) -> list[ValidationIssue]:
        issues = []
        seen_values: dict[str, int] = {}
        for node in result.nodes:
            if node.node_type not in NODE_TYPES:
                issues.append(ValidationIssue(
                    "invalid_node_type",
                    "error",
                    f"{node.node_id} has invalid node_type={node.node_type!r}",
                    f"nodes[{node.node_id}].node_type",
                ))
            if node.source_kind not in SOURCE_KINDS:
                issues.append(ValidationIssue(
                    "invalid_source_kind",
                    "error",
                    f"{node.node_id} has invalid source_kind={node.source_kind!r}",
                    f"nodes[{node.node_id}].source_kind",
                ))
            key = node.node_value.strip().lower()
            seen_values[key] = seen_values.get(key, 0) + 1
            if not (0.0 <= node.confidence <= 1.0):
                issues.append(ValidationIssue(
                    "confidence_out_of_range",
                    "error",
                    f"{node.node_id} confidence out of range",
                    f"nodes[{node.node_id}].confidence",
                ))
        for value, count in seen_values.items():
            if value and count > 3:
                issues.append(ValidationIssue(
                    "excessive_duplicate_node_values",
                    "warning",
                    f"node_value {value!r} appears {count} times",
                    "nodes",
                ))
        return issues

    def _check_edges(
        self,
        result: DSLLinkingResult,
        node_ids: set[str],
        node_types: dict[str, str],
    ) -> list[ValidationIssue]:
        issues = []
        seen_edges: dict[tuple[str, str, str], int] = {}
        for edge in result.edges:
            if edge.from_node_id not in node_ids:
                issues.append(ValidationIssue(
                    "edge_missing_from_node",
                    "error",
                    f"{edge.edge_id} references missing from_node_id={edge.from_node_id}",
                    f"edges[{edge.edge_id}].from_node_id",
                ))
            if edge.to_node_id not in node_ids:
                issues.append(ValidationIssue(
                    "edge_missing_to_node",
                    "error",
                    f"{edge.edge_id} references missing to_node_id={edge.to_node_id}",
                    f"edges[{edge.edge_id}].to_node_id",
                ))
            if edge.from_node_id == edge.to_node_id:
                issues.append(ValidationIssue(
                    "self_loop_edge",
                    "warning",
                    f"{edge.edge_id} is a self-loop",
                    f"edges[{edge.edge_id}]",
                ))
            if edge.core_predicate not in CORE_PREDICATES:
                issues.append(ValidationIssue(
                    "invalid_core_predicate",
                    "error",
                    f"{edge.edge_id} has invalid core_predicate={edge.core_predicate!r}",
                    f"edges[{edge.edge_id}].core_predicate",
                ))
            if edge.polarity not in POLARITIES:
                issues.append(ValidationIssue(
                    "invalid_polarity",
                    "error",
                    f"{edge.edge_id} has invalid polarity={edge.polarity!r}",
                    f"edges[{edge.edge_id}].polarity",
                ))
            if edge.confidence >= 0.75 and not _has_refs(edge.evidence_refs):
                issues.append(ValidationIssue(
                    "strong_edge_without_evidence",
                    "warning",
                    f"{edge.edge_id} is high-confidence but lacks evidence_refs",
                    f"edges[{edge.edge_id}].evidence_refs",
                ))
            if not _has_refs(edge.evidence_refs):
                issues.append(ValidationIssue(
                    "edge_missing_evidence_refs",
                    "warning",
                    f"{edge.edge_id} has no claim/equation/thesis evidence_refs",
                    f"edges[{edge.edge_id}].evidence_refs",
                ))
            issues += self._check_predicate_semantics(edge, node_types)
            if not (0.0 <= edge.confidence <= 1.0):
                issues.append(ValidationIssue(
                    "confidence_out_of_range",
                    "error",
                    f"{edge.edge_id} confidence out of range",
                    f"edges[{edge.edge_id}].confidence",
                ))
            key = (edge.from_node_id, edge.to_node_id, edge.core_predicate)
            seen_edges[key] = seen_edges.get(key, 0) + 1
        for key, count in seen_edges.items():
            if count > 2:
                issues.append(ValidationIssue(
                    "excessive_duplicate_edges",
                    "warning",
                    f"edge tuple {key} appears {count} times",
                    "edges",
                ))
        return issues

    def _check_graph_hints(
        self,
        result: DSLLinkingResult,
        node_ids: set[str],
    ) -> list[ValidationIssue]:
        issues = []
        for idx, hint in enumerate(result.graph_hints or []):
            if hint.get("hint_type") not in GRAPH_HINT_TYPES:
                issues.append(ValidationIssue(
                    "invalid_graph_hint_type",
                    "error",
                    f"graph_hints[{idx}] has invalid hint_type={hint.get('hint_type')!r}",
                    f"graph_hints[{idx}].hint_type",
                ))
            missing = [n for n in hint.get("node_ids", []) if n not in node_ids]
            if missing:
                issues.append(ValidationIssue(
                    "graph_hint_missing_node",
                    "warning",
                    f"graph_hints[{idx}] references missing nodes {missing}",
                    f"graph_hints[{idx}].node_ids",
                ))
        return issues

    def _check_confidence(self, result: DSLLinkingResult) -> list[ValidationIssue]:
        if not (0.0 <= result.confidence <= 1.0):
            return [ValidationIssue(
                "confidence_out_of_range",
                "error",
                "result confidence out of range",
                "confidence",
            )]
        return []

    def _check_graph_shape(self, result: DSLLinkingResult) -> list[ValidationIssue]:
        issues = []
        if len(result.nodes) > 1 and not result.edges:
            issues.append(ValidationIssue(
                "disconnected_graph",
                "warning",
                "graph has multiple nodes but no edges",
                "edges",
            ))
        meta_count = sum(
            1
            for node in result.nodes
            if "prior" in node.node_value.lower() or "meta" in node.node_value.lower()
        )
        if result.nodes and meta_count / len(result.nodes) > 0.5:
            issues.append(ValidationIssue(
                "meta_prior_nodes_dominate",
                "warning",
                "meta/prior-work-like nodes dominate the graph",
                "nodes",
            ))
        proxy_count = sum(1 for n in result.nodes if n.node_type in PROXY_NODE_TYPES)
        if result.nodes and proxy_count / len(result.nodes) > 0.5:
            issues.append(ValidationIssue(
                "proxy_node_dominance",
                "warning",
                f"proxy nodes are {proxy_count}/{len(result.nodes)}; favor concrete typed nodes",
                "nodes",
            ))
        return issues

    def _check_concrete_node_coverage(
        self, result: DSLLinkingResult
    ) -> list[ValidationIssue]:
        if len(result.nodes) < 4:
            return []
        concrete = {n.node_type for n in result.nodes if n.node_type in CONCRETE_NODE_TYPES}
        focus = {
            "EquationRelation",
            "Observable",
            "Approximation",
            "CorrectionSource",
            "UncertaintySource",
        }
        if concrete.isdisjoint(focus):
            return [ValidationIssue(
                "missing_concrete_node_types",
                "warning",
                "graph lacks any of EquationRelation/Observable/Approximation/CorrectionSource/UncertaintySource",
                "nodes",
            )]
        return []

    def _check_predicate_semantics(
        self,
        edge,
        node_types: dict[str, str],
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        from_type = node_types.get(edge.from_node_id)
        to_type = node_types.get(edge.to_node_id)
        if edge.core_predicate == "EQUIVALENT":
            if from_type and to_type and from_type != to_type:
                issues.append(ValidationIssue(
                    "equivalent_predicate_misuse",
                    "warning",
                    f"{edge.edge_id} uses EQUIVALENT across different node_types "
                    f"({from_type} -> {to_type}); reserve EQUIVALENT for genuine equivalence",
                    f"edges[{edge.edge_id}].core_predicate",
                ))
        preferred_targets = PREDICATE_PREFERRED_TARGETS.get(edge.core_predicate)
        if preferred_targets and to_type and to_type in CONCRETE_NODE_TYPES \
                and to_type not in preferred_targets:
            issues.append(ValidationIssue(
                "predicate_target_mismatch",
                "info",
                f"{edge.edge_id} {edge.core_predicate} -> {to_type} is unusual; "
                f"expected target in {sorted(preferred_targets)}",
                f"edges[{edge.edge_id}].core_predicate",
            ))
        preferred_sources = PREDICATE_PREFERRED_SOURCES.get(edge.core_predicate)
        if preferred_sources and from_type and from_type in CONCRETE_NODE_TYPES \
                and from_type not in preferred_sources:
            issues.append(ValidationIssue(
                "predicate_source_mismatch",
                "info",
                f"{edge.edge_id} {from_type} -{edge.core_predicate}-> is unusual; "
                f"expected source in {sorted(preferred_sources)}",
                f"edges[{edge.edge_id}].core_predicate",
            ))
        return issues


def _has_refs(refs: dict) -> bool:
    return any(refs.get(key) for key in ("claim_ids", "equation_ids", "thesis_refs"))
