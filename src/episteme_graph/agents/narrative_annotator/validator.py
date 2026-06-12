"""Validation for NarrativeAnnotator outputs (issue #360)."""
from __future__ import annotations

from .schema import (
    MATURITY_SOURCES,
    MAX_GRAPH_SUMMARY_CHARS,
    MAX_NARRATIVE_ROLE_CHARS,
    MAX_TRANSITION_TEXT_CHARS,
    NarrativeAnnotationResult,
    NarrativeLLMInput,
    ValidationIssue,
)


def verify_graph_unchanged(before: dict, after: dict) -> bool:
    """The annotator must never modify the graph (issue #360 hard guarantee)."""
    return before == after


class NarrativeValidator:
    def validate(
        self,
        result: NarrativeAnnotationResult,
        llm_input: NarrativeLLMInput | None = None,
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        known_node_ids = {
            str(n.get("component_id") or "")
            for n in (llm_input.main_nodes if llm_input else [])
        }
        known_edge_ids = {
            str(e.get("edge_id") or "")
            for e in (llm_input.main_edges if llm_input else [])
        }

        if result.maturity_source not in MATURITY_SOURCES:
            issues.append(ValidationIssue(
                "invalid_maturity_source", "error",
                f"maturity_source {result.maturity_source!r} is not allowed",
                "maturity_source",
            ))
        if len(result.graph_summary or "") > MAX_GRAPH_SUMMARY_CHARS:
            issues.append(ValidationIssue(
                "graph_summary_too_long", "error",
                f"graph_summary exceeds {MAX_GRAPH_SUMMARY_CHARS} characters",
                "graph_summary",
            ))
        if not (0.0 <= float(result.confidence) <= 1.0):
            issues.append(ValidationIssue(
                "confidence_out_of_range", "error",
                f"confidence {result.confidence} out of [0,1]",
                "confidence",
            ))

        seen_nodes: set[str] = set()
        for narrative in result.node_narratives or []:
            field = f"node_narratives[{narrative.component_id}]"
            if known_node_ids and narrative.component_id not in known_node_ids:
                issues.append(ValidationIssue(
                    "unknown_component_id", "error",
                    f"{field} references unknown main node",
                    field,
                ))
            if narrative.component_id in seen_nodes:
                issues.append(ValidationIssue(
                    "duplicate_node_narrative", "error",
                    f"{field} is annotated more than once",
                    field,
                ))
            seen_nodes.add(narrative.component_id)
            if not str(narrative.narrative_role or "").strip():
                issues.append(ValidationIssue(
                    "empty_narrative_role", "error",
                    f"{field} has empty narrative_role",
                    field,
                ))
            if len(narrative.narrative_role or "") > MAX_NARRATIVE_ROLE_CHARS:
                issues.append(ValidationIssue(
                    "narrative_role_too_long", "error",
                    f"{field} exceeds {MAX_NARRATIVE_ROLE_CHARS} characters",
                    field,
                ))
            if not (0.0 <= float(narrative.confidence) <= 1.0):
                issues.append(ValidationIssue(
                    "confidence_out_of_range", "error",
                    f"{field} confidence out of [0,1]",
                    field,
                ))

        seen_edges: set[str] = set()
        for narrative in result.edge_narratives or []:
            field = f"edge_narratives[{narrative.edge_id}]"
            if known_edge_ids and narrative.edge_id not in known_edge_ids:
                issues.append(ValidationIssue(
                    "unknown_edge_id", "error",
                    f"{field} references unknown main edge",
                    field,
                ))
            if narrative.edge_id in seen_edges:
                issues.append(ValidationIssue(
                    "duplicate_edge_narrative", "error",
                    f"{field} is annotated more than once",
                    field,
                ))
            seen_edges.add(narrative.edge_id)
            if not str(narrative.transition_text or "").strip():
                issues.append(ValidationIssue(
                    "empty_transition_text", "error",
                    f"{field} has empty transition_text",
                    field,
                ))
            if len(narrative.transition_text or "") > MAX_TRANSITION_TEXT_CHARS:
                issues.append(ValidationIssue(
                    "transition_text_too_long", "error",
                    f"{field} exceeds {MAX_TRANSITION_TEXT_CHARS} characters",
                    field,
                ))

        # Coverage warnings (not hard errors): unannotated nodes / edges keep
        # the artifact usable but visible for review.
        for node_id in sorted(known_node_ids - seen_nodes):
            if node_id:
                issues.append(ValidationIssue(
                    "node_without_narrative", "warning",
                    f"main node {node_id!r} has no narrative_role",
                    f"node_narratives[{node_id}]",
                ))
        for edge_id in sorted(known_edge_ids - seen_edges):
            if edge_id:
                issues.append(ValidationIssue(
                    "edge_without_narrative", "warning",
                    f"main edge {edge_id!r} has no transition_text",
                    f"edge_narratives[{edge_id}]",
                ))
        return issues
