"""Validation for ComponentGraphAgent outputs (Issue #266)."""
from __future__ import annotations

from .schema import (
    VALID_EDGE_TYPES,
    CartridgeContext,
    ComponentGraphEdge,
    ComponentGraphLLMInput,
    ComponentGraphResult,
    ValidationIssue,
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

        valid_types = set(VALID_EDGE_TYPES)
        if cartridge:
            raw = cartridge.relation_types.get("relation_types") or []
            for item in raw:
                if isinstance(item, dict) and item.get("id"):
                    valid_types.add(str(item["id"]).upper())
                elif isinstance(item, str):
                    valid_types.add(item.upper())

        seen_keys: set[tuple[str, str, str]] = set()
        for edge in result.edges:
            issues += self._check_edge(edge, component_ids, valid_types, seen_keys)

        if result.confidence < 0.0 or result.confidence > 1.0:
            issues.append(ValidationIssue(
                "confidence_out_of_range",
                "error",
                "result confidence out of [0, 1]",
                "confidence",
            ))

        return issues

    @staticmethod
    def _check_edge(
        edge: ComponentGraphEdge,
        component_ids: set[str],
        valid_types: set[str],
        seen_keys: set[tuple[str, str, str]],
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

        if edge.confidence < 0.0 or edge.confidence > 1.0:
            issues.append(ValidationIssue(
                "edge_confidence_out_of_range",
                "warning",
                f"{eid}: confidence {edge.confidence} out of [0, 1]",
                f"edges[{eid}].confidence",
            ))

        return issues
