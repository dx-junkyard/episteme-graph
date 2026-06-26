"""Repair helpers for DSLLinkingAgent."""
from __future__ import annotations

import logging

from .graph_cleanup import DSLGraphCleanup
from .llm_client import DSLLinkingLLMClient
from .prompt import DSLLinkingPromptFactory
from .schema import (
    DSL_VERSION,
    DSLEdge,
    DSLLLMInput,
    DSLLinkingResult,
    DSLNode,
    ValidationIssue,
)

logger = logging.getLogger(__name__)
_MAX_REPAIR_ATTEMPTS = 2


class DSLLinkingRepairer:
    def __init__(self, cleanup: DSLGraphCleanup | None = None) -> None:
        self._cleanup = cleanup or DSLGraphCleanup()

    def repair(
        self,
        llm_input: DSLLLMInput,
        raw_output: dict,
        validation_issues: list[ValidationIssue],
        llm_client: DSLLinkingLLMClient,
        prompt_factory: DSLLinkingPromptFactory,
        validator: object,
    ) -> DSLLinkingResult:
        if _has_no_nodes_error(validation_issues):
            return make_deterministic_fallback(
                llm_input,
                "LLM DSL linking returned no nodes",
            )

        for attempt in range(1, _MAX_REPAIR_ATTEMPTS + 1):
            logger.info("DSL linking repair attempt %d/%d", attempt, _MAX_REPAIR_ATTEMPTS)
            messages = prompt_factory.build_repair_messages(
                llm_input, raw_output, validation_issues
            )
            try:
                raw_output = llm_client.generate(messages)
            except Exception as exc:
                logger.warning("Repair LLM call failed: %s", exc)
                break
            result = self._cleanup.cleanup(_parse_raw(raw_output, llm_input.document_id))
            remaining = validator.validate(result)  # type: ignore[attr-defined]
            if not [i for i in remaining if i.severity == "error"]:
                result.validation_issues = remaining
                return result
            validation_issues = remaining
        return make_deterministic_fallback(llm_input, "Repair failed after max attempts")


def _has_no_nodes_error(validation_issues: list[ValidationIssue]) -> bool:
    return any(
        issue.severity == "error" and issue.rule_id == "no_nodes"
        for issue in validation_issues
    )


def make_deterministic_fallback(llm_input: DSLLLMInput, reason: str) -> DSLLinkingResult:
    nodes: list[DSLNode] = []
    for idx, claim in enumerate(llm_input.accepted_claims[:12], start=1):
        claim_id = str(claim.get("claim_id") or "")
        text = str(claim.get("text") or claim_id)
        claim_type = str(claim.get("claim_type_candidate") or "")
        nodes.append(DSLNode(
            node_id=f"n_fallback_{idx:03d}",
            node_type=_node_type_for_claim(claim_type),
            node_value=_short_value(text, idx),
            source_kind="claim",
            source_refs={"claim_ids": [claim_id] if claim_id else [], "equation_ids": [], "thesis_refs": []},
            reason=f"Deterministic fallback node from canonical claim {claim_id}: {reason}",
            confidence=0.45,
        ))

    edges: list[DSLEdge] = []
    for idx in range(1, len(nodes)):
        edges.append(DSLEdge(
            edge_id=f"e_fallback_{idx:03d}",
            from_node_id=nodes[idx - 1].node_id,
            to_node_id=nodes[idx].node_id,
            core_predicate="CORRELATES",
            domain_verb="supports",
            polarity="?",
            evidence_refs={
                "claim_ids": (
                    nodes[idx - 1].source_refs.get("claim_ids", [])
                    + nodes[idx].source_refs.get("claim_ids", [])
                ),
                "equation_ids": [],
                "thesis_refs": [],
            },
            reason="Conservative fallback edge preserving source-backed graph connectivity.",
            confidence=0.35,
            edge_type="CORRELATES",
        ))

    if not nodes:
        return DSLLinkingResult.make_fallback(llm_input.document_id, reason)
    result = DSLLinkingResult(
        document_id=llm_input.document_id,
        dsl_version=DSL_VERSION,
        nodes=nodes,
        edges=edges,
        graph_hints=[],
        review_notes=[f"DSL linking used deterministic fallback: {reason}"],
        confidence=0.45,
    )
    result.validation_issues = [ValidationIssue(
        "dsl_linking_deterministic_fallback",
        "warning",
        f"LLM DSL linking failed; emitted {len(nodes)} fallback node(s)",
        "nodes",
    )]
    return result


def _node_type_for_claim(claim_type: str) -> str:
    normalized = claim_type.lower()
    if "assumption" in normalized:
        return "Approximation"
    if "uncertainty" in normalized:
        return "UncertaintySource"
    if "diagnostic" in normalized:
        return "Diagnostic"
    if "definition" in normalized:
        return "Parameter"
    if "result" in normalized:
        return "Result"
    if "derivation" in normalized:
        return "Relation"
    return "ClaimProxy"


def _short_value(text: str, idx: int) -> str:
    compact = " ".join(text.split())
    if not compact:
        return f"fallback_claim_{idx}"
    return compact[:77] + "..." if len(compact) > 80 else compact


def _parse_raw(raw: dict, document_id: str) -> DSLLinkingResult:
    nodes = []
    for idx, node in enumerate(raw.get("nodes", []) if isinstance(raw.get("nodes", []), list) else []):
        if not isinstance(node, dict):
            continue
        nodes.append(DSLNode(
            node_id=str(node.get("node_id", f"n{idx + 1}")),
            node_type=str(node.get("node_type", "ClaimProxy")),
            node_value=str(node.get("node_value", "")),
            source_kind=str(node.get("source_kind", "claim")),
            source_refs=_refs(node.get("source_refs", {})),
            reason=str(node.get("reason", "")),
            confidence=_confidence(node.get("confidence", 0.5)),
        ))

    edges = []
    for idx, edge in enumerate(raw.get("edges", []) if isinstance(raw.get("edges", []), list) else []):
        if not isinstance(edge, dict):
            continue
        core_predicate = str(edge.get("core_predicate", "CONTAINS"))
        edges.append(DSLEdge(
            edge_id=str(edge.get("edge_id", f"e{idx + 1}")),
            from_node_id=str(edge.get("from_node_id", "")),
            to_node_id=str(edge.get("to_node_id", "")),
            core_predicate=core_predicate,
            domain_verb=str(edge.get("domain_verb", "supports")),
            polarity=str(edge.get("polarity", "?")),
            evidence_refs=_refs(edge.get("evidence_refs", {})),
            reason=str(edge.get("reason", "")),
            confidence=_confidence(edge.get("confidence", 0.5)),
            # Issue #441: edge_type normalises to the managed vocab; the raw LLM
            # value is honoured when present, otherwise mirrors core_predicate.
            edge_type=str(edge.get("edge_type") or core_predicate),
        ))

    confidence = _confidence(raw.get("confidence", 0.0))
    return DSLLinkingResult(
        document_id=raw.get("document_id", document_id),
        dsl_version=raw.get("dsl_version", DSL_VERSION),
        nodes=nodes,
        edges=edges,
        graph_hints=list(raw.get("graph_hints", [])),
        review_notes=list(raw.get("review_notes", [])),
        confidence=confidence,
    )


def _refs(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raw = {}
    return {
        "claim_ids": list(raw.get("claim_ids", [])),
        "equation_ids": list(raw.get("equation_ids", [])),
        "thesis_refs": list(raw.get("thesis_refs", [])),
    }


def _confidence(value: object) -> float:
    try:
        val = float(value)
    except (TypeError, ValueError):
        val = 0.0
    return max(0.0, min(1.0, val))
