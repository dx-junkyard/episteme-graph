"""Validation for ComponentAssemblyAgent outputs."""
from __future__ import annotations

from .input_builder import ComponentAssemblyInputBuilder
from .schema import (
    ASSEMBLY_HINT_TYPES,
    CORE_COMPONENT_TYPES,
    CORE_DEPENDENCY_TYPES,
    INTERNAL_FLOW_REQUIRED_TYPES,
    CartridgeContext,
    ComponentAssemblyResult,
    ComponentRecord,
    ValidationIssue,
)


class ComponentAssemblyValidator:
    def validate(
        self,
        result: ComponentAssemblyResult,
        cartridge: CartridgeContext | None = None,
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if not result.components:
            return [ValidationIssue("no_components", "error", "components is empty", "components")]

        allowed_components = set(ComponentAssemblyInputBuilder.allowed_component_types(cartridge))
        allowed_dependencies = set(ComponentAssemblyInputBuilder.allowed_dependency_types(cartridge))
        component_ids = {c.component_id for c in result.components}

        for component in result.components:
            issues += self._check_component(component, allowed_components, allowed_dependencies, component_ids)
            issues += self._check_required_fields(component, cartridge)
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
