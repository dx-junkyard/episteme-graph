"""Repair helpers for ComponentGraphAgent (Issue #266)."""
from __future__ import annotations

import logging

from episteme_graph.agents.llm_step import (
    MAX_REPAIR_ATTEMPTS,
    attach_issues,
    run_repair_loop,
)

from .llm_client import ComponentGraphLLMClient
from .prompt import ComponentGraphPromptFactory
from .schema import (
    GRAPH_SCHEMA_VERSION,
    CartridgeContext,
    ComponentGraphEdge,
    ComponentGraphLLMInput,
    ComponentGraphNode,
    ComponentGraphResult,
    ValidationIssue,
)

logger = logging.getLogger(__name__)
_MAX_REPAIR_ATTEMPTS = MAX_REPAIR_ATTEMPTS


class ComponentGraphRepairer:
    def repair(
        self,
        llm_input: ComponentGraphLLMInput,
        raw_output: dict,
        validation_issues: list[ValidationIssue],
        cartridge: CartridgeContext | None,
        nodes: list[ComponentGraphNode],
        llm_client: ComponentGraphLLMClient,
        prompt_factory: ComponentGraphPromptFactory,
        validator: object,
    ) -> ComponentGraphResult:
        def _on_exhausted(issues: list[ValidationIssue]) -> ComponentGraphResult:
            fallback = ComponentGraphResult.make_fallback(
                llm_input.document_id,
                llm_input.cartridge_id,
                "Repair failed after max attempts",
                nodes,
            )
            fallback.validation_issues = issues
            return fallback

        return run_repair_loop(
            build_messages=lambda raw, issues: prompt_factory.build_repair_messages(
                llm_input, raw, issues
            ),
            generate=lambda messages: llm_client.generate(messages),
            parse=lambda raw: _parse_raw(
                raw, llm_input.document_id, llm_input.cartridge_id, nodes
            ),
            validate=lambda result: validator.validate(  # type: ignore[attr-defined]
                result, cartridge, llm_input=llm_input
            ),
            on_success=attach_issues,
            on_exhausted=_on_exhausted,
            raw_output=raw_output,
            validation_issues=validation_issues,
            log_label="ComponentGraph",
        )


def _parse_raw(
    raw: dict,
    document_id: str,
    cartridge_id: str | None,
    nodes: list[ComponentGraphNode],
) -> ComponentGraphResult:
    edges: list[ComponentGraphEdge] = []
    seen_keys: set[tuple[str, str, str]] = set()
    for idx, item in enumerate(raw.get("edges", []) if isinstance(raw.get("edges"), list) else []):
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "").strip()
        target = str(item.get("target") or "").strip()
        edge_type = str(item.get("edge_type") or "RELATED_TO").strip().upper()
        key = (source, target, edge_type)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        evidence_claims = item.get("evidence_claims") or []
        if not isinstance(evidence_claims, list):
            evidence_claims = []
        evidence_equation_ids = item.get("evidence_equation_ids") or []
        if not isinstance(evidence_equation_ids, list):
            evidence_equation_ids = []
        edges.append(ComponentGraphEdge(
            edge_id=str(item.get("edge_id") or f"component_edge_{idx + 1:04d}"),
            source=source,
            target=target,
            edge_type=edge_type,
            support_status=str(item.get("support_status") or "llm_inferred"),
            evidence_claims=[str(c) for c in evidence_claims],
            reasoning=str(item.get("reasoning") or ""),
            confidence=_confidence(item.get("confidence", 0.75)),
            evidence_equation_ids=[str(e) for e in evidence_equation_ids],
            review_status=str(item.get("review_status") or "teacher_review_required"),
        ))

    confidence_raw = raw.get("confidence")
    confidence = _confidence(confidence_raw) if confidence_raw is not None else (
        sum(e.confidence for e in edges) / max(len(edges), 1) if edges else 0.0
    )

    return ComponentGraphResult(
        document_id=document_id,
        graph_schema_version=GRAPH_SCHEMA_VERSION,
        cartridge_id=cartridge_id,
        nodes=nodes,
        edges=edges,
        review_notes=list(raw.get("review_notes", [])),
        confidence=confidence,
    )


def _confidence(value: object) -> float:
    try:
        val = float(value)
    except (TypeError, ValueError):
        val = 0.75
    return max(0.0, min(1.0, val))
