"""Repair helpers for NarrativeAnnotator (issue #360)."""
from __future__ import annotations

import logging

from episteme_graph.agents.llm_step import (
    MAX_REPAIR_ATTEMPTS,
    attach_issues,
    run_repair_loop,
)

from .schema import (
    EdgeNarrative,
    NARRATIVE_VERSION,
    NarrativeAnnotationResult,
    NarrativeLLMInput,
    NodeNarrative,
)

logger = logging.getLogger(__name__)

_MAX_REPAIR_ATTEMPTS = MAX_REPAIR_ATTEMPTS


def _resolve_ref(entry: dict, id_key: str, by_ref: dict[str, str]) -> str:
    """Map the LLM's handle (``N1`` / ``E1``, IK-0753) back to the internal ID.

    An entry that carries the internal ID directly is still accepted; an unknown
    handle is kept as-is so the validator reports it.
    """
    ref = str(entry.get("ref") or "").strip()
    if ref:
        return by_ref.get(ref, ref)
    return str(entry.get(id_key) or "")


def _parse_raw(raw: dict, llm_input: NarrativeLLMInput) -> NarrativeAnnotationResult:
    node_by_ref = {
        str(n.get("ref")): str(n.get("component_id") or "")
        for n in llm_input.main_nodes if n.get("ref")
    }
    edge_by_ref = {
        str(e.get("ref")): str(e.get("edge_id") or "")
        for e in llm_input.main_edges if e.get("ref")
    }
    nodes: list[NodeNarrative] = []
    for entry in raw.get("node_narratives") or []:
        if not isinstance(entry, dict):
            continue
        nodes.append(NodeNarrative(
            component_id=_resolve_ref(entry, "component_id", node_by_ref),
            narrative_role=str(entry.get("narrative_role") or "").strip(),
            reason=str(entry.get("reason") or ""),
            confidence=_clamp(entry.get("confidence")),
        ))
    edges: list[EdgeNarrative] = []
    for entry in raw.get("edge_narratives") or []:
        if not isinstance(entry, dict):
            continue
        edges.append(EdgeNarrative(
            edge_id=_resolve_ref(entry, "edge_id", edge_by_ref),
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
        def _on_exhausted(_issues: list) -> NarrativeAnnotationResult:
            # Unlike most agents the fallback carries no validation_issues.
            return NarrativeAnnotationResult.make_fallback(
                llm_input.document_id,
                llm_input.cartridge_id,
                "Repair failed after max attempts",
            )

        return run_repair_loop(
            build_messages=lambda raw, issues: prompt_factory.build_repair_messages(
                llm_input, raw, issues, cartridge
            ),
            generate=lambda messages: llm_client.generate(messages),
            parse=lambda raw: _parse_raw(raw, llm_input),
            validate=lambda result: validator.validate(result, llm_input),
            on_success=attach_issues,
            on_exhausted=_on_exhausted,
            raw_output=raw_output,
            validation_issues=validation_issues,
            log_label="Narrative annotation",
        )
