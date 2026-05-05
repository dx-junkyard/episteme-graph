"""Validation for ComponentAssemblyAgent outputs."""
from __future__ import annotations

from .input_builder import ComponentAssemblyInputBuilder
from .schema import (
    ASSEMBLY_HINT_TYPES,
    CORE_COMPONENT_TYPES,
    CORE_DEPENDENCY_TYPES,
    INTERNAL_FLOW_REQUIRED_TYPES,
    CartridgeContext,
    ComponentAssemblyLLMInput,
    ComponentAssemblyResult,
    ComponentRecord,
    ValidationIssue,
)


def _build_available_id_index(llm_input: ComponentAssemblyLLMInput | None) -> dict[str, set[str]]:
    """Build a dict of available ID sets from the LLM input for cross-reference validation."""
    if not llm_input:
        return {}
    return {
        "claim_ids": {c["claim_id"] for c in (llm_input.available_claims or []) if c.get("claim_id")},
        "evidence_ids": {e["evidence_id"] for e in (llm_input.available_evidence or []) if e.get("evidence_id")},
        "equation_ids": {e["equation_id"] for e in (llm_input.available_equations or []) if e.get("equation_id")},
        "dsl_node_ids": {n["node_id"] for n in (llm_input.available_dsl_nodes or []) if n.get("node_id")},
        "dsl_edge_ids": {e["edge_id"] for e in (llm_input.available_dsl_edges or []) if e.get("edge_id")},
        "derivation_ids": set(llm_input.available_derivation_ids or []),
    }


class ComponentAssemblyValidator:
    def validate(
        self,
        result: ComponentAssemblyResult,
        cartridge: CartridgeContext | None = None,
        llm_input: ComponentAssemblyLLMInput | None = None,
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if not result.components:
            return [ValidationIssue("no_components", "error", "components is empty", "components")]

        allowed_components = set(ComponentAssemblyInputBuilder.allowed_component_types(cartridge))
        allowed_dependencies = set(ComponentAssemblyInputBuilder.allowed_dependency_types(cartridge))
        component_ids = {c.component_id for c in result.components}
        available = _build_available_id_index(llm_input)

        for component in result.components:
            issues += self._check_component(component, allowed_components, allowed_dependencies, component_ids)
            issues += self._check_required_fields(component, cartridge)
            if available:
                issues += self._check_id_references(component, available)
        issues += self._check_hints(result, component_ids)
        issues += self._check_duplicates(result)
        if not (0.0 <= result.confidence <= 1.0):
            issues.append(ValidationIssue("confidence_out_of_range", "error", "result confidence out of range", "confidence"))
        return issues

    def _check_component(
        self,
        component: ComponentRecord,
        allowed_components: set[str],
        allowed_dependencies: set[str],
        component_ids: set[str],
    ) -> list[ValidationIssue]:
        issues = []
        if component.component_type not in allowed_components:
            issues.append(ValidationIssue(
                "invalid_component_type",
                "error",
                f"{component.component_id} has invalid component_type={component.component_type!r}",
                f"components[{component.component_id}].component_type",
            ))
        for field in ("inputs", "outputs", "preconditions", "cautions", "dependencies", "internal_flow"):
            if not isinstance(getattr(component, field, None), list):
                issues.append(ValidationIssue(
                    "invalid_component_field",
                    "error",
                    f"{component.component_id}.{field} must be a list",
                    f"components[{component.component_id}].{field}",
                ))
        issues += self._check_internal_flow(component)
        if not (0.0 <= component.confidence <= 1.0):
            issues.append(ValidationIssue(
                "confidence_out_of_range",
                "error",
                f"{component.component_id} confidence out of range",
                f"components[{component.component_id}].confidence",
            ))
        if component.confidence >= 0.75 and not _has_evidence(component.evidence_refs):
            issues.append(ValidationIssue(
                "strong_component_without_evidence",
                "warning",
                f"{component.component_id} is high-confidence but lacks evidence_refs",
                f"components[{component.component_id}].evidence_refs",
            ))
        for idx, dep in enumerate(component.dependencies):
            dep_type = dep.get("dependency_type")
            if dep_type not in allowed_dependencies:
                issues.append(ValidationIssue(
                    "invalid_dependency_type",
                    "error",
                    f"{component.component_id}.dependencies[{idx}] has invalid dependency_type={dep_type!r}",
                    f"components[{component.component_id}].dependencies[{idx}].dependency_type",
                ))
            missing = [ref for ref in dep.get("component_refs", []) if ref not in component_ids]
            if missing:
                issues.append(ValidationIssue(
                    "dependency_missing_component",
                    "warning",
                    f"{component.component_id}.dependencies[{idx}] references missing components {missing}",
                    f"components[{component.component_id}].dependencies[{idx}].component_refs",
                ))
        if _meta_prior_evidence_ratio(component) > 0.5:
            issues.append(ValidationIssue(
                "meta_prior_evidence_dominates",
                "warning",
                f"{component.component_id} appears dominated by meta/prior-work evidence",
                f"components[{component.component_id}].evidence_refs",
            ))
        return issues

    def _check_internal_flow(
        self,
        component: ComponentRecord,
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        flow = component.internal_flow or []
        if component.component_type in INTERNAL_FLOW_REQUIRED_TYPES and not flow:
            issues.append(ValidationIssue(
                "component_missing_internal_flow",
                "warning",
                f"{component.component_id} ({component.component_type}) "
                "has no internal_flow; reusable components of this type must "
                "expose how inputs are combined into outputs.",
                f"components[{component.component_id}].internal_flow",
            ))
        for idx, step in enumerate(flow):
            if not isinstance(step, dict):
                issues.append(ValidationIssue(
                    "internal_flow_invalid_entry",
                    "error",
                    f"{component.component_id}.internal_flow[{idx}] is not an object",
                    f"components[{component.component_id}].internal_flow[{idx}]",
                ))
                continue
            missing = [k for k in ("from", "relation", "to") if not step.get(k)]
            if missing:
                issues.append(ValidationIssue(
                    "internal_flow_incomplete_step",
                    "error",
                    f"{component.component_id}.internal_flow[{idx}] is missing {missing}",
                    f"components[{component.component_id}].internal_flow[{idx}]",
                ))
        # If component has multi-input or multi-output, internal_flow should be present
        # to explain how they relate.
        if not flow and (
            len(component.inputs) >= 2 or len(component.outputs) >= 2
        ) and component.component_type not in ("ClaimBundleComponent",):
            issues.append(ValidationIssue(
                "component_multi_io_without_flow",
                "warning",
                f"{component.component_id} has multiple inputs/outputs but no internal_flow",
                f"components[{component.component_id}].internal_flow",
            ))
        return issues

    def _check_id_references(
        self,
        component: ComponentRecord,
        available: dict[str, set[str]],
    ) -> list[ValidationIssue]:
        """Cross-reference evidence_refs against known artifact IDs."""
        issues: list[ValidationIssue] = []
        refs = component.evidence_refs or {}

        # Check claim_ids
        known_claims = available.get("claim_ids", set())
        if known_claims:
            unknown = [cid for cid in (refs.get("claim_ids") or []) if cid not in known_claims]
            for cid in unknown:
                issues.append(ValidationIssue(
                    "unresolved_claim_id",
                    "error",
                    f"{component.component_id}.evidence_refs.claim_ids contains unresolved ID: {cid!r}",
                    f"components[{component.component_id}].evidence_refs.claim_ids",
                ))

        # Check evidence_ids
        known_evidence = available.get("evidence_ids", set())
        if known_evidence:
            unknown = [eid for eid in (refs.get("evidence_ids") or []) if eid not in known_evidence]
            for eid in unknown:
                issues.append(ValidationIssue(
                    "unresolved_evidence_id",
                    "error",
                    f"{component.component_id}.evidence_refs.evidence_ids contains unresolved ID: {eid!r}",
                    f"components[{component.component_id}].evidence_refs.evidence_ids",
                ))

        # Check equation_ids
        known_equations = available.get("equation_ids", set())
        if known_equations:
            unknown = [eqid for eqid in (refs.get("equation_ids") or []) if eqid not in known_equations]
            for eqid in unknown:
                issues.append(ValidationIssue(
                    "unresolved_equation_id",
                    "error",
                    f"{component.component_id}.evidence_refs.equation_ids contains unresolved ID: {eqid!r}",
                    f"components[{component.component_id}].evidence_refs.equation_ids",
                ))

        # Check DSL node/edge refs
        dsl_refs = refs.get("dsl_refs") or {}
        known_dsl_nodes = available.get("dsl_node_ids", set())
        if known_dsl_nodes:
            unknown = [nid for nid in (dsl_refs.get("node_ids") or []) if nid not in known_dsl_nodes]
            for nid in unknown:
                issues.append(ValidationIssue(
                    "unresolved_dsl_node_id",
                    "error",
                    f"{component.component_id}.evidence_refs.dsl_refs.node_ids contains unresolved ID: {nid!r}",
                    f"components[{component.component_id}].evidence_refs.dsl_refs.node_ids",
                ))
        known_dsl_edges = available.get("dsl_edge_ids", set())
        if known_dsl_edges:
            unknown = [eid for eid in (dsl_refs.get("edge_ids") or []) if eid not in known_dsl_edges]
            for eid in unknown:
                issues.append(ValidationIssue(
                    "unresolved_dsl_edge_id",
                    "error",
                    f"{component.component_id}.evidence_refs.dsl_refs.edge_ids contains unresolved ID: {eid!r}",
                    f"components[{component.component_id}].evidence_refs.dsl_refs.edge_ids",
                ))

        # Detect summary-only component: no claim_ids and no evidence_ids
        has_claim_link = bool(refs.get("claim_ids"))
        has_evidence_link = bool(refs.get("evidence_ids"))
        if not has_claim_link and not has_evidence_link:
            issues.append(ValidationIssue(
                "summary_only_component",
                "warning",
                f"{component.component_id} has no claim_ids or evidence_ids in evidence_refs (summary-only)",
                f"components[{component.component_id}].evidence_refs",
            ))

        # Propagate review_status: if any referenced claim has non-auto review, flag component
        known_claim_reviews = available.get("_claim_review_map", {})
        if known_claim_reviews:
            for cid in refs.get("claim_ids") or []:
                review = known_claim_reviews.get(cid)
                if review and review != "auto_accepted":
                    comp_review = getattr(component, "review_notes", []) or []
                    if not any("teacher_review" in n for n in comp_review):
                        issues.append(ValidationIssue(
                            "review_required_claim_in_component",
                            "warning",
                            f"{component.component_id} references claim {cid!r} with review_status={review!r}",
                            f"components[{component.component_id}].evidence_refs.claim_ids",
                        ))
                        break

        return issues

    def _check_required_fields(
        self,
        component: ComponentRecord,
        cartridge: CartridgeContext | None,
    ) -> list[ValidationIssue]:
        required = _required_fields_for_type(component.component_type, cartridge)
        issues = []
        for field in required:
            value = getattr(component, field, None)
            if not value:
                issues.append(ValidationIssue(
                    "missing_required_component_field",
                    "error",
                    f"{component.component_id} is missing required field {field}",
                    f"components[{component.component_id}].{field}",
                ))
        if component.component_type in ("RelationComponent", "PaperRelationComponent") and not component.outputs:
            issues.append(ValidationIssue(
                "relation_component_without_output",
                "warning",
                f"{component.component_id} relation component has no outputs",
                f"components[{component.component_id}].outputs",
            ))
        return issues

    def _check_hints(
        self,
        result: ComponentAssemblyResult,
        component_ids: set[str],
    ) -> list[ValidationIssue]:
        issues = []
        for idx, hint in enumerate(result.assembly_hints or []):
            if hint.get("hint_type") not in ASSEMBLY_HINT_TYPES:
                issues.append(ValidationIssue(
                    "invalid_assembly_hint_type",
                    "error",
                    f"assembly_hints[{idx}] has invalid hint_type={hint.get('hint_type')!r}",
                    f"assembly_hints[{idx}].hint_type",
                ))
            missing = [ref for ref in hint.get("component_ids", []) if ref not in component_ids]
            if missing:
                issues.append(ValidationIssue(
                    "assembly_hint_missing_component",
                    "warning",
                    f"assembly_hints[{idx}] references missing components {missing}",
                    f"assembly_hints[{idx}].component_ids",
                ))
        return issues

    def _check_duplicates(self, result: ComponentAssemblyResult) -> list[ValidationIssue]:
        seen: dict[tuple[str, str], int] = {}
        for component in result.components:
            key = (component.component_type, component.label.strip().lower())
            seen[key] = seen.get(key, 0) + 1
        if any(count > 2 for count in seen.values()):
            return [ValidationIssue(
                "excessive_duplicate_components",
                "warning",
                "duplicate component labels/types appear excessive",
                "components",
            )]
        return []


def _required_fields_for_type(
    component_type: str,
    cartridge: CartridgeContext | None,
) -> list[str]:
    if not cartridge:
        return []
    for item in cartridge.component_types.get("component_types", []):
        if isinstance(item, dict) and item.get("id") == component_type:
            fields = item.get("required_fields", [])
            return list(fields) if isinstance(fields, list) else []
    return []


def _has_evidence(refs: dict) -> bool:
    dsl_refs = (refs or {}).get("dsl_refs", {}) or {}
    return any((refs or {}).get(key) for key in ("claim_ids", "equation_ids", "thesis_refs")) or any(
        dsl_refs.get(key) for key in ("node_ids", "edge_ids")
    )


def _meta_prior_evidence_ratio(component: ComponentRecord) -> float:
    values = []
    for key in ("claim_ids", "thesis_refs"):
        values.extend(str(v).lower() for v in component.evidence_refs.get(key, []) or [])
    if not values:
        return 0.0
    bad = sum(1 for v in values if "prior" in v or "meta" in v)
    return bad / len(values)
