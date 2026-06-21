"""Repair helpers for ComponentAssemblyAgent."""
from __future__ import annotations

import logging

from episteme_graph.agents.id_canonicalization import (
    canonicalize_claim_refs,
    claim_aliases_from_accepted_claims,
)

from .llm_client import ComponentAssemblyLLMClient
from .enrichment import enrich_component_assembly
from .overlap_cleanup import ComponentOverlapCleanup
from .prompt import ComponentAssemblyPromptFactory
from .schema import (
    COMPONENTS_VERSION,
    ComponentAssemblyLLMInput,
    ComponentAssemblyResult,
    ComponentRecord,
    ValidationIssue,
    normalize_dependencies,
)

logger = logging.getLogger(__name__)
_MAX_REPAIR_ATTEMPTS = 2

# Atomicity values that disqualify a claim from being a component's primary
# support. Mirrors export_validation_gate._claim_is_non_atomic / input_builder
# so the deterministic fallback never emits a component the export gate rejects
# with NON_ATOMIC_CLAIM_USED_AS_COMPONENT_SUPPORT (issue #345).
_NON_ATOMIC_ATOMICITIES = {
    "composite",
    "split_required",
    "compound",
    "non_atomic",
    "split_pending",
}


def _claim_row_is_non_atomic(row: dict) -> bool:
    """Return True for an available_claims row that must not back a component.

    A row is non-atomic when its ``atomicity`` is one of the disqualifying
    values or ``is_atomic`` is explicitly false. Unknown/missing metadata
    defaults to atomic (the prior behavior) so legacy rows are still usable.
    """
    atomicity = str(row.get("atomicity", "atomic") or "atomic").lower()
    is_atomic = bool(row.get("is_atomic", atomicity == "atomic"))
    return atomicity in _NON_ATOMIC_ATOMICITIES or not is_atomic


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
        diagnostics: dict | None = None,
    ) -> ComponentAssemblyResult:
        # no_components is repaired like any other validation error (#347):
        # the repair prompt explicitly demands a non-empty components array, so
        # an empty initial LLM output gets at least one regeneration attempt
        # before the deterministic fallback kicks in.
        had_no_components = _has_no_components_error(validation_issues)

        for attempt in range(1, _MAX_REPAIR_ATTEMPTS + 1):
            logger.info("Component assembly repair attempt %d/%d", attempt, _MAX_REPAIR_ATTEMPTS)
            messages = prompt_factory.build_repair_messages(
                llm_input, raw_output, validation_issues
            )
            try:
                raw_output = llm_client.generate(messages)
            except Exception as exc:
                logger.warning("Repair LLM call failed: %s", exc)
                if diagnostics is not None:
                    diagnostics[f"repair_attempt_{attempt}_exception"] = str(exc)
                break
            if diagnostics is not None:
                attempt_component_count = (
                    len(raw_output.get("components", []))
                    if isinstance(raw_output.get("components"), list)
                    else 0
                )
                diagnostics[f"repair_attempt_{attempt}_output"] = {
                    "parsed": raw_output,
                    "component_count": attempt_component_count,
                    "raw_text": getattr(llm_client, "last_raw_text", None),
                    "parse_error": getattr(llm_client, "last_parse_error", None),
                }
                diagnostics[f"repair_attempt_{attempt}_component_count"] = attempt_component_count
            raw_output = canonicalize_claim_refs(
                raw_output,
                None,
                claim_aliases_from_accepted_claims(llm_input.accepted_claims),
            )
            result = self._cleanup.cleanup(_parse_raw(raw_output, llm_input.document_id, llm_input.cartridge_id))
            result = enrich_component_assembly(result, llm_input)
            remaining = validator.validate(result, cartridge, llm_input=llm_input)  # type: ignore[attr-defined]
            if diagnostics is not None:
                diagnostics[f"repair_attempt_{attempt}_issues"] = [
                    _issue_dict(issue, llm_input) for issue in remaining
                ]
                diagnostics[f"repair_attempt_{attempt}_error_codes"] = [
                    issue.rule_id for issue in remaining if issue.severity == "error"
                ]
            if not [i for i in remaining if i.severity == "error"]:
                result.validation_issues = remaining
                return result
            validation_issues = remaining
        reason = (
            "LLM component assembly returned no components"
            if had_no_components and _has_no_components_error(validation_issues)
            else "Repair failed after max attempts"
        )
        if diagnostics is not None:
            diagnostics["fallback_reason"] = reason
            diagnostics["original_failure_codes"] = _issue_codes(validation_issues)
        fallback = make_deterministic_fallback(
            llm_input,
            reason,
            validation_issues,
        )
        return fallback


def _has_no_components_error(validation_issues: list[ValidationIssue]) -> bool:
    return any(
        issue.severity == "error" and issue.rule_id == "no_components"
        for issue in validation_issues
    )


def _issue_codes(validation_issues: list[ValidationIssue]) -> list[str]:
    return [issue.rule_id for issue in validation_issues or []]


def _issue_dict(issue: ValidationIssue, llm_input: ComponentAssemblyLLMInput) -> dict:
    data = {
        "code": issue.rule_id,
        "severity": issue.severity,
        "message": issue.message,
        "field": issue.field,
    }
    if issue.target_type:
        data["target_type"] = issue.target_type
    if issue.target_id:
        data["target_id"] = issue.target_id
    allowed = _allowed_values_for_issue(issue.rule_id, llm_input)
    invalid = _invalid_value_from_message(issue.message)
    if invalid:
        data["invalid_value"] = invalid
    if allowed:
        data["allowed_values"] = sorted(allowed)
    return data


def _allowed_values_for_issue(rule_id: str, llm_input: ComponentAssemblyLLMInput) -> set[str]:
    if rule_id == "unresolved_claim_id":
        return _ids(llm_input.available_claims, "claim_id")
    if rule_id == "unresolved_evidence_id":
        return _ids(llm_input.available_evidence, "evidence_id")
    if rule_id == "unresolved_equation_id":
        return _ids(llm_input.available_equations, "equation_id")
    if rule_id == "unresolved_derivation_id":
        return {str(v) for v in llm_input.available_derivation_ids or [] if str(v)}
    if rule_id == "unresolved_dsl_node_id":
        return _ids(llm_input.available_dsl_nodes, "node_id")
    if rule_id == "unresolved_dsl_edge_id":
        return _ids(llm_input.available_dsl_edges, "edge_id")
    return set()


def _ids(items: list[dict], key: str) -> set[str]:
    return {str(item.get(key)) for item in items or [] if item.get(key)}


def _invalid_value_from_message(message: str) -> str | None:
    if "'" not in message:
        return None
    parts = message.split("'")
    return parts[1] if len(parts) >= 3 else None


def make_deterministic_fallback(
    llm_input: ComponentAssemblyLLMInput,
    reason: str,
    validation_issues: list[ValidationIssue] | None = None,
) -> ComponentAssemblyResult:
    """Build source-backed review components without another LLM call.

    The export bundle should not contain an empty components array when the
    upstream claim/evidence artifacts are available. These fallback components
    are intentionally conservative: one source-backed claim bundle per canonical
    claim, low confidence, and teacher review required.
    """
    components: list[ComponentRecord] = []
    original_failure_codes = _issue_codes(validation_issues or [])
    # Only atomic available claims may back a fallback component. Non-atomic
    # parents (composite / split_required / ...) are dropped so the fallback
    # never points a component's primary claim support at a non-atomic claim;
    # any atomic children appear as their own available_claims rows and are
    # kept (issue #345). accepted_claims is already non-atomic-filtered upstream.
    atomic_available_claims = [
        c
        for c in llm_input.available_claims or []
        if c.get("claim_id") and not _claim_row_is_non_atomic(c)
    ]
    evidence_by_claim = {
        str(c.get("claim_id")): list(c.get("source_evidence_ids") or [])
        for c in atomic_available_claims
    }
    equations_by_claim = {
        str(c.get("claim_id")): list(c.get("equation_ids") or [])
        for c in atomic_available_claims
    }
    atomic_accepted_claims = [
        c
        for c in llm_input.accepted_claims or []
        if c.get("claim_id") and not _claim_row_is_non_atomic(c)
    ]
    accepted_by_claim = {
        str(c.get("claim_id")): c
        for c in atomic_accepted_claims
    }

    claim_ids = _ordered_unique(
        [str(c.get("claim_id")) for c in atomic_available_claims]
        + [str(c.get("claim_id")) for c in atomic_accepted_claims]
    )
    for idx, claim_id in enumerate(claim_ids[:12], start=1):
        accepted = accepted_by_claim.get(claim_id, {})
        text = str(accepted.get("text") or _available_claim_text(llm_input, claim_id) or claim_id)
        evidence_ids = evidence_by_claim.get(claim_id, [])
        equation_ids = equations_by_claim.get(claim_id, [])
        component_text = _component_text(text, equation_ids)
        component_id = f"comp_fallback_{idx:03d}"
        components.append(ComponentRecord(
            component_id=component_id,
            component_type="ClaimBundleComponent",
            label=_short_label(component_text, idx),
            summary=component_text,
            inputs=[],
            outputs=[{
                "name": _short_label(component_text, idx),
                "claim_ids": [claim_id],
                "equation_ids": equation_ids,
            }],
            preconditions=[],
            cautions=[{
                "text": "Generated by deterministic fallback after LLM component assembly failed; teacher review required.",
                "claim_ids": [claim_id],
                "equation_ids": [],
            }],
            dependencies=[],
            evidence_refs={
                "claim_ids": [claim_id],
                "evidence_ids": evidence_ids,
                "equation_ids": equation_ids,
                "thesis_refs": [],
                "dsl_refs": {"node_ids": [], "edge_ids": []},
            },
            reason=f"Deterministic fallback component built from canonical claim {claim_id}: {reason}",
            confidence=0.45,
            review_notes=[f"ComponentAssemblyAgent fallback: {reason}"],
            linked_claim_ids=[claim_id],
            linked_equation_ids=equation_ids,
            output_equation_ids=equation_ids,
            linked_evidence_ids=evidence_ids,
            review_status="teacher_review_required",
            teaching_takeaway=component_text,
            support_role="theory_base",
            supports_claim_ids=[claim_id],
            support_distance_to_headline_claim=2,
            support_kind="prerequisite",
            concepts=list(accepted.get("concepts") or []),
            prerequisite_concepts=[],
            introduced_concepts=list(accepted.get("concepts") or []),
            reused_concepts=[],
            source_scope={
                "fallback": True,
                "source": "claim_object_builder",
                "claim_id": claim_id,
                "fallback_reason": reason,
                "original_failure_codes": original_failure_codes,
            },
            operation="summarize_claim",
            maturity_source="deterministic_fallback",
            publish_ready=False,
            fallback_reason=reason,
            original_failure_codes=original_failure_codes,
        ))

    logger.warning(
        "Component assembly deterministic fallback: document=%s reason=%r "
        "original_failure_codes=%s atomic_available_claims=%d atomic_accepted_claims=%d "
        "fallback_components=%d",
        llm_input.document_id,
        reason,
        original_failure_codes,
        len(atomic_available_claims),
        len(atomic_accepted_claims),
        len(components),
    )

    if components:
        result = ComponentAssemblyResult(
            document_id=llm_input.document_id,
            components_version=COMPONENTS_VERSION,
            cartridge_id=llm_input.cartridge_id,
            components=components,
            assembly_hints=[],
            review_notes=[f"Component assembly used deterministic fallback: {reason}"],
            confidence=0.45,
        )
        result.validation_issues = [
            ValidationIssue(
                "component_assembly_deterministic_fallback",
                "warning",
                f"LLM component assembly failed; emitted {len(components)} claim-bundle fallback component(s)",
                "components",
            )
        ]
        return result

    fallback = ComponentAssemblyResult.make_fallback(
        llm_input.document_id,
        llm_input.cartridge_id,
        reason,
    )
    fallback.validation_issues = validation_issues or []
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
            dependencies=normalize_dependencies(item.get("dependencies", [])),
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
            support_role=str(item.get("support_role", "")),
            supports_claim_ids=list(item.get("supports_claim_ids", [])),
            support_distance_to_headline_claim=int(item.get("support_distance_to_headline_claim", 0) or 0),
            support_kind=str(item.get("support_kind", "")),
            concepts=list(item.get("concepts", []) or []),
            prerequisite_concepts=list(item.get("prerequisite_concepts", []) or []),
            introduced_concepts=list(item.get("introduced_concepts", []) or []),
            reused_concepts=list(item.get("reused_concepts", []) or []),
            operation=str(item.get("operation", "")),
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


def _available_claim_text(llm_input: ComponentAssemblyLLMInput, claim_id: str) -> str:
    for claim in llm_input.available_claims or []:
        if str(claim.get("claim_id") or "") == claim_id:
            return str(claim.get("text") or "")
    return ""


def _component_text(text: str, equation_ids: list[str]) -> str:
    if equation_ids:
        return text
    return text.replace("consistency relation", "consistency claim").replace(
        "Consistency relation", "Consistency claim"
    )


def _short_label(text: str, idx: int) -> str:
    words = " ".join(text.split())
    if not words:
        return f"Fallback claim component {idx}"
    return words[:77] + "..." if len(words) > 80 else words


def _ordered_unique(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


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
