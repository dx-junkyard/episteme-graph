"""Repair helpers for ComponentAssemblyAgent."""
from __future__ import annotations

import logging

from .llm_client import ComponentAssemblyLLMClient
from .overlap_cleanup import ComponentOverlapCleanup
from .prompt import ComponentAssemblyPromptFactory
from .schema import (
    COMPONENTS_VERSION,
    ComponentAssemblyLLMInput,
    ComponentAssemblyResult,
    ComponentRecord,
    ValidationIssue,
)

logger = logging.getLogger(__name__)
_MAX_REPAIR_ATTEMPTS = 2


class ComponentAssemblyRepairer:
    def __init__(self, cleanup: ComponentOverlapCleanup | None = None) -> None:
        self._cleanup = cleanup or ComponentOverlapCleanup()

    def repair(
        self,
        llm_input: ComponentAssemblyLLMInput,
        raw_output: dict,
        validation_issues: list[ValidationIssue],
        cartridge: object | None,
        llm_client: ComponentAssemblyLLMClient,
        prompt_factory: ComponentAssemblyPromptFactory,
        validator: object,
    ) -> ComponentAssemblyResult:
        for attempt in range(1, _MAX_REPAIR_ATTEMPTS + 1):
            logger.info("Component assembly repair attempt %d/%d", attempt, _MAX_REPAIR_ATTEMPTS)
            messages = prompt_factory.build_repair_messages(
                llm_input, raw_output, validation_issues
            )
            try:
                raw_output = llm_client.generate(messages)
            except Exception as exc:
                logger.warning("Repair LLM call failed: %s", exc)
                break
            result = self._cleanup.cleanup(_parse_raw(raw_output, llm_input.document_id, llm_input.cartridge_id))
            remaining = validator.validate(result, cartridge, llm_input=llm_input)  # type: ignore[attr-defined]
            if not [i for i in remaining if i.severity == "error"]:
                result.validation_issues = remaining
                return result
            validation_issues = remaining
        fallback = ComponentAssemblyResult.make_fallback(
            llm_input.document_id, llm_input.cartridge_id, "Repair failed after max attempts"
        )
        fallback.validation_issues = validation_issues
        return fallback


def _parse_raw(
    raw: dict,
    document_id: str,
    cartridge_id: str | None,
) -> ComponentAssemblyResult:
    components = []
    for idx, item in enumerate(raw.get("components", []) if isinstance(raw.get("components", []), list) else []):
        if not isinstance(item, dict):
            continue
        components.append(ComponentRecord(
            component_id=str(item.get("component_id", f"comp_{idx + 1:03d}")),
            component_type=str(item.get("component_type", "ClaimBundleComponent")),
            label=str(item.get("label", "")),
            summary=str(item.get("summary", "")),
            inputs=list(item.get("inputs", [])),
            outputs=list(item.get("outputs", [])),
            preconditions=list(item.get("preconditions", [])),
            cautions=list(item.get("cautions", [])),
            dependencies=list(item.get("dependencies", [])),
            evidence_refs=_evidence_refs(item.get("evidence_refs", {})),
            reason=str(item.get("reason", "")),
            confidence=_confidence(item.get("confidence", 0.5)),
            review_notes=list(item.get("review_notes", [])),
            internal_flow=_internal_flow(item.get("internal_flow", [])),
            linked_claim_ids=list(item.get("linked_claim_ids", [])),
            linked_equation_ids=list(item.get("linked_equation_ids", [])),
            linked_evidence_ids=list(item.get("linked_evidence_ids", [])),
            linked_derivation_ids=list(item.get("linked_derivation_ids", [])),
            linked_dsl_node_ids=list(item.get("linked_dsl_node_ids", [])),
            linked_dsl_edge_ids=list(item.get("linked_dsl_edge_ids", [])),
            input_equation_ids=list(item.get("input_equation_ids", [])),
            intermediate_equation_ids=list(item.get("intermediate_equation_ids", [])),
            output_equation_ids=list(item.get("output_equation_ids", [])),
            constraint_equation_ids=list(item.get("constraint_equation_ids", [])),
            definition_equation_ids=list(item.get("definition_equation_ids", [])),
            review_required_equation_ids=list(item.get("review_required_equation_ids", [])),
            eliminated_symbols=list(item.get("eliminated_symbols", [])),
            retained_symbols=list(item.get("retained_symbols", [])),
            equation_confidence_summary=(
                item.get("equation_confidence_summary", {})
                if isinstance(item.get("equation_confidence_summary", {}), dict)
                else {}
            ),
            review_status=str(item.get("review_status", "teacher_review_required")),
            teaching_takeaway=str(item.get("teaching_takeaway", "")),
            source_scope=item.get("source_scope", {}) if isinstance(item.get("source_scope", {}), dict) else {},
            assumptions=list(item.get("assumptions", [])),
            approximations=list(item.get("approximations", [])),
        ))
    return ComponentAssemblyResult(
        document_id=raw.get("document_id", document_id),
        components_version=raw.get("components_version", COMPONENTS_VERSION),
        cartridge_id=raw.get("cartridge_id", cartridge_id),
        components=components,
        assembly_hints=list(raw.get("assembly_hints", [])),
        review_notes=list(raw.get("review_notes", [])),
        confidence=_confidence(raw.get("confidence", 0.0)),
    )


def _evidence_refs(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raw = {}
    dsl_refs = raw.get("dsl_refs", {}) if isinstance(raw.get("dsl_refs", {}), dict) else {}
    return {
        "claim_ids": list(raw.get("claim_ids", [])),
        "evidence_ids": list(raw.get("evidence_ids", [])),
        "equation_ids": list(raw.get("equation_ids", [])),
        "thesis_refs": list(raw.get("thesis_refs", [])),
        "dsl_refs": {
            "node_ids": list(dsl_refs.get("node_ids", [])),
            "edge_ids": list(dsl_refs.get("edge_ids", [])),
        },
    }


def _internal_flow(raw: object) -> list[dict]:
    if not isinstance(raw, list):
        return []
    flow: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        flow.append({
            "from": str(item.get("from", "")),
            "relation": str(item.get("relation", "")),
            "to": str(item.get("to", "")),
        })
    return flow


def _confidence(value: object) -> float:
    try:
        val = float(value)
    except (TypeError, ValueError):
        val = 0.0
    return max(0.0, min(1.0, val))
