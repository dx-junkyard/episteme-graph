"""Repair helpers for NarrativeAnnotator (issue #360)."""
from __future__ import annotations

import logging

from .schema import (
    EdgeNarrative,
    NARRATIVE_VERSION,
    NarrativeAnnotationResult,
    NarrativeLLMInput,
    NodeNarrative,
)

logger = logging.getLogger(__name__)

_MAX_REPAIR_ATTEMPTS = 2


def _parse_raw(raw: dict, llm_input: NarrativeLLMInput) -> NarrativeAnnotationResult:
    nodes: list[NodeNarrative] = []
    for entry in raw.get("node_narratives") or []:
        if not isinstance(entry, dict):
            continue
        nodes.append(NodeNarrative(
            component_id=str(entry.get("component_id") or ""),
            narrative_role=str(entry.get("narrative_role") or "").strip(),
            reason=str(entry.get("reason") or ""),
            confidence=_clamp(entry.get("confidence")),
        ))
    edges: list[EdgeNarrative] = []
    for entry in raw.get("edge_narratives") or []:
        if not isinstance(entry, dict):
            continue
        edges.append(EdgeNarrative(
            edge_id=str(entry.get("edge_id") or ""),
            transition_text=str(entry.get("transition_text") or "").strip(),
            reason=str(entry.get("reason") or ""),
            confidence=_clamp(entry.get("confidence")),
        ))
    return NarrativeAnnotationResult(
        document_id=llm_input.document_id,
        narrative_version=NARRATIVE_VERSION,
        cartridge_id=llm_input.cartridge_id,
        graph_summary=str(raw.get("graph_summary") or "").strip(),
        node_narratives=nodes,
        edge_narratives=edges,
        review_notes=[str(n) for n in (raw.get("review_notes") or []) if n],
        confidence=_clamp(raw.get("confidence")),
        maturity_source="llm_proposed",
    )


def _clamp(value) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


class NarrativeRepairer:
    def repair(
        self,
        *,
        llm_input: NarrativeLLMInput,
        raw_output: dict,
        validation_issues: list,
        cartridge,
        llm_client,
        prompt_factory,
        validator,
    ) -> NarrativeAnnotationResult:
        for attempt in range(1, _MAX_REPAIR_ATTEMPTS + 1):
            logger.info(
                "Narrative annotation repair attempt %d/%d", attempt, _MAX_REPAIR_ATTEMPTS
            )
            messages = prompt_factory.build_repair_messages(
                llm_input, raw_output, validation_issues, cartridge
            )
            try:
                raw_output = llm_client.generate(messages)
            except Exception as exc:
                logger.warning("Narrative repair LLM call failed: %s", exc)
                break
            result = _parse_raw(raw_output, llm_input)
            remaining = validator.validate(result, llm_input)
            if not [i for i in remaining if i.severity == "error"]:
                result.validation_issues = remaining
                return result
            validation_issues = remaining
        return NarrativeAnnotationResult.make_fallback(
            llm_input.document_id,
            llm_input.cartridge_id,
            "Repair failed after max attempts",
        )
