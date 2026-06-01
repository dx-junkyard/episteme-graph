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
    normalize_dependency_type,
)


def _build_available_id_index(llm_input: ComponentAssemblyLLMInput | None) -> dict:
    """Build a dict of available ID sets from the LLM input for cross-reference validation."""
    if not llm_input:
        return {}
    equation_policy_map = {}
    equation_review_map = {}
    for e in llm_input.available_equations or []:
        eq_id = e.get("equation_id")
        if not eq_id:
            continue
        policy = e.get("confidence_policy") if isinstance(e.get("confidence_policy"), dict) else {}
        equation_policy_map[eq_id] = policy
        review_required = (
            bool(e.get("needs_math_review"))
            or bool(e.get("review_flags"))
            or e.get("semantic_status") == "reconstruction_based"
            or e.get("reconstruction_status") not in (None, "", "none")
            or bool(policy.get("must_not_treat_as_source_extracted"))
            or policy.get("can_support_claim") is False
        )
        equation_review_map[eq_id] = review_required
    return {
        "claim_ids": {c["claim_id"] for c in (llm_input.available_claims or []) if c.get("claim_id")},
        "evidence_ids": {e["evidence_id"] for e in (llm_input.available_evidence or []) if e.get("evidence_id")},
        "equation_ids": {e["equation_id"] for e in (llm_input.available_equations or []) if e.get("equation_id")},
        "dsl_node_ids": {n["node_id"] for n in (llm_input.available_dsl_nodes or []) if n.get("node_id")},
        "dsl_edge_ids": {e["edge_id"] for e in (llm_input.available_dsl_edges or []) if e.get("edge_id")},
        "derivation_ids": set(llm_input.available_derivation_ids or []),
        "_equation_policy_map": equation_policy_map,
        "_equation_review_map": equation_review_map,
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
            issues += self._check_component(component, allowed_components, allowed_dependencies, component_ids, available)
            issues += self._check_required_fields(component, cartridge)
            issues += self._check_operation(component)
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
        available: dict | None = None,
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
        issues += self._check_internal_flow(component, available)
        if not (0.0 <= component.confidence <= 1.0):
            issues.append(ValidationIssue(
                "confidence_out_of_range",
                "error",
                f"{component.component_id} confidence out of range",
                f"components[{component.component_id}].confidence",
            ))
        if component.confidence >= 0.75 and not (
            _has_evidence(component.evidence_refs)
            or component.linked_claim_ids
            or component.linked_evidence_ids
            or _component_equation_refs(component, component.evidence_refs or {})
        ):
            issues.append(ValidationIssue(
                "strong_component_without_evidence",
                "warning",
                f"{component.component_id} is high-confidence but lacks evidence_refs",
                f"components[{component.component_id}].evidence_refs",
            ))
        for idx, dep in enumerate(component.dependencies):
            dep_type = normalize_dependency_type(dep.get("dependency_type"))
            dep["dependency_type"] = dep_type
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
        available: dict | None = None,
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        flow = component.internal_flow or []
        if component.component_type in INTERNAL_FLOW_REQUIRED_TYPES and not flow:
            severity = "error" if component.component_type in {
                "RelationComponent",
                "PaperRelationComponent",
                "MethodComponent",
            } else "warning"
            issues.append(ValidationIssue(
                "component_missing_internal_flow",
                severity,
                f"{component.component_id} ({component.component_type}) "
                "has no internal_flow; reusable components of this type must "
                "expose how inputs are combined into outputs.",
                f"components[{component.component_id}].internal_flow",
            ))

        # Build a union of all known artifact IDs for from/to validation (issue #262)
        all_known_ids: set[str] = set()
        if available:
            for id_set in available.values():
                if isinstance(id_set, set):
                    all_known_ids.update(id_set)

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
            # Check from/to against known artifact IDs (issue #262)
            if all_known_ids:
                for field_name in ("from", "to"):
                    ref_val = step.get(field_name, "")
                    if ref_val and ref_val not in all_known_ids:
                        # Only warn if it looks like an artifact ID (not a human-readable label)
                        if any(ref_val.startswith(pfx) for pfx in
                               ("claim_", "eq_", "ev_", "node_", "edge_", "step_", "deriv")):
                            issues.append(ValidationIssue(
                                "internal_flow_unresolved_ref",
                                "warning",
                                f"{component.component_id}.internal_flow[{idx}].{field_name}={ref_val!r} "
                                "not found in available artifact IDs",
                                f"components[{component.component_id}].internal_flow[{idx}].{field_name}",
                            ))

        # If component has multi-input or multi-output, internal_flow should be present
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
        available: dict,
    ) -> list[ValidationIssue]:
        """Cross-reference evidence_refs and typed linked IDs against known artifact IDs (issue #262)."""
        issues: list[ValidationIssue] = []
        refs = component.evidence_refs or {}

        # Check legacy evidence_refs.claim_ids
        known_claims = available.get("claim_ids", set())
        if known_claims:
            all_claim_ids = list(refs.get("claim_ids") or []) + list(component.linked_claim_ids or [])
            unknown = [cid for cid in all_claim_ids if cid not in known_claims]
            for cid in unknown:
                issues.append(ValidationIssue(
                    "unresolved_claim_id",
                    "error",
                    f"{component.component_id} contains unresolved claim ID: {cid!r}",
                    f"components[{component.component_id}].linked_claim_ids",
                ))

        # Check evidence_ids
        known_evidence = available.get("evidence_ids", set())
        if known_evidence:
            all_ev_ids = list(refs.get("evidence_ids") or []) + list(component.linked_evidence_ids or [])
            unknown = [eid for eid in all_ev_ids if eid not in known_evidence]
            for eid in unknown:
                issues.append(ValidationIssue(
                    "unresolved_evidence_id",
                    "error",
                    f"{component.component_id} contains unresolved evidence ID: {eid!r}",
                    f"components[{component.component_id}].linked_evidence_ids",
                ))

        # Check equation_ids (typed field takes priority)
        known_equations = available.get("equation_ids", set())
        if known_equations:
            all_eq_ids = _component_equation_refs(component, refs)
            unknown = [eqid for eqid in all_eq_ids if eqid not in known_equations]
            for eqid in unknown:
                issues.append(ValidationIssue(
                    "unresolved_equation_id",
                    "error",
                    f"{component.component_id} contains unresolved equation ID: {eqid!r}",
                    f"components[{component.component_id}].linked_equation_ids",
                ))

        # Check derivation IDs (issue #262)
        known_derivations = available.get("derivation_ids", set())
        if known_derivations and component.linked_derivation_ids:
            unknown = [did for did in component.linked_derivation_ids if did not in known_derivations]
            for did in unknown:
                issues.append(ValidationIssue(
                    "unresolved_derivation_id",
                    "error",
                    f"{component.component_id} contains unresolved derivation ID: {did!r}",
                    f"components[{component.component_id}].linked_derivation_ids",
                ))

        # Check DSL node/edge refs
        dsl_refs = refs.get("dsl_refs") or {}
        known_dsl_nodes = available.get("dsl_node_ids", set())
        if known_dsl_nodes:
            all_node_ids = list(dsl_refs.get("node_ids") or []) + list(component.linked_dsl_node_ids or [])
            unknown = [nid for nid in all_node_ids if nid not in known_dsl_nodes]
            for nid in unknown:
                issues.append(ValidationIssue(
                    "unresolved_dsl_node_id",
                    "error",
                    f"{component.component_id} contains unresolved DSL node ID: {nid!r}",
                    f"components[{component.component_id}].linked_dsl_node_ids",
                ))
        known_dsl_edges = available.get("dsl_edge_ids", set())
        if known_dsl_edges:
            all_edge_ids = list(dsl_refs.get("edge_ids") or []) + list(component.linked_dsl_edge_ids or [])
            unknown = [eid for eid in all_edge_ids if eid not in known_dsl_edges]
            for eid in unknown:
                issues.append(ValidationIssue(
                    "unresolved_dsl_edge_id",
                    "error",
                    f"{component.component_id} contains unresolved DSL edge ID: {eid!r}",
                    f"components[{component.component_id}].linked_dsl_edge_ids",
                ))

        # Detect summary-only: no typed claim/evidence links in either legacy or new fields
        has_claim_link = bool(refs.get("claim_ids")) or bool(component.linked_claim_ids)
        has_evidence_link = bool(refs.get("evidence_ids")) or bool(component.linked_evidence_ids)
        if not has_claim_link and not has_evidence_link:
            issues.append(ValidationIssue(
                "summary_only_component",
                "warning",
                f"{component.component_id} has no claim_ids or evidence_ids (summary-only)",
                f"components[{component.component_id}].evidence_refs",
            ))

        # Component type-specific: equation/derivation dependency without linked IDs (issue #262)
        eq_heavy_types = {"RelationComponent", "PaperRelationComponent", "DiagnosticComponent"}
        deriv_types = {"RelationComponent", "MethodComponent"}
        if component.component_type in eq_heavy_types and known_equations:
            all_eq = _component_equation_refs(component, refs)
            if not all_eq:
                issues.append(ValidationIssue(
                    "equation_dependent_component_missing_eq_ids",
                    "warning",
                    f"{component.component_id} ({component.component_type}) has no equation links",
                    f"components[{component.component_id}].linked_equation_ids",
                ))
        if component.component_type in deriv_types and known_derivations:
            if not component.linked_derivation_ids:
                issues.append(ValidationIssue(
                    "derivation_component_missing_derivation_ids",
                    "warning",
                    f"{component.component_id} ({component.component_type}) has no derivation links",
                    f"components[{component.component_id}].linked_derivation_ids",
                ))

        issues += self._check_equation_role_classification(component, refs, available)

        # Propagate review_status from referenced claims to component (issue #262)
        known_claim_reviews = available.get("_claim_review_map", {}) or {}
        if known_claim_reviews:
            all_claim_refs = list(refs.get("claim_ids") or []) + list(component.linked_claim_ids or [])
            for cid in all_claim_refs:
                review = known_claim_reviews.get(cid)
                if review and review != "auto_accepted":
                    if component.review_status == "auto_accepted":
                        issues.append(ValidationIssue(
                            "review_required_claim_in_component",
                            "warning",
                            f"{component.component_id} references claim {cid!r} with review_status={review!r}"
                            " but component.review_status is auto_accepted",
                            f"components[{component.component_id}].review_status",
                        ))
                    break

        return issues

    def _check_equation_role_classification(
        self,
        component: ComponentRecord,
        refs: dict,
        available: dict,
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        known_equations = available.get("equation_ids", set())
        if not known_equations:
            return issues

        all_eq_refs = _component_equation_refs(component, refs)
        has_classified = any((
            component.input_equation_ids,
            component.intermediate_equation_ids,
            component.output_equation_ids,
            component.constraint_equation_ids,
            component.definition_equation_ids,
            component.review_required_equation_ids,
        ))
        deriv_like = component.component_type in {"RelationComponent", "PaperRelationComponent", "MethodComponent"}
        if deriv_like and all_eq_refs and not has_classified:
            issues.append(ValidationIssue(
                "component_equations_not_role_classified",
                "warning",
                f"{component.component_id} links equations but does not classify their component roles",
                f"components[{component.component_id}].input_equation_ids",
            ))
        if deriv_like and all_eq_refs and not component.output_equation_ids:
            issues.append(ValidationIssue(
                "derivation_component_without_output_equations",
                "warning",
                f"{component.component_id} is derivation-like but has no output_equation_ids",
                f"components[{component.component_id}].output_equation_ids",
            ))
        if deriv_like and all_eq_refs and not component.input_equation_ids:
            issues.append(ValidationIssue(
                "derivation_component_without_input_equations",
                "warning",
                f"{component.component_id} is derivation-like but has no input_equation_ids",
                f"components[{component.component_id}].input_equation_ids",
            ))

        policy_map = available.get("_equation_policy_map", {}) or {}
        review_map = available.get("_equation_review_map", {}) or {}
        claim_linked = bool(refs.get("claim_ids") or component.linked_claim_ids)
        for eq_id in all_eq_refs:
            policy = policy_map.get(eq_id, {}) if isinstance(policy_map, dict) else {}
            if claim_linked and policy.get("can_support_claim") is False:
                issues.append(ValidationIssue(
                    "claim_support_uses_non_supporting_equation",
                    "error",
                    f"{component.component_id} links claim evidence to non-claim-supporting equation {eq_id!r}",
                    f"components[{component.component_id}].linked_equation_ids",
                ))
            if review_map.get(eq_id) and eq_id not in component.review_required_equation_ids:
                issues.append(ValidationIssue(
                    "review_required_equation_not_marked_on_component",
                    "warning",
                    f"{component.component_id} references review-required equation {eq_id!r} "
                    "but does not list it in review_required_equation_ids",
                    f"components[{component.component_id}].review_required_equation_ids",
                ))

        for eq_id in component.output_equation_ids:
            policy = policy_map.get(eq_id, {}) if isinstance(policy_map, dict) else {}
            if policy.get("can_support_claim") is False:
                issues.append(ValidationIssue(
                    "component_output_uses_non_supporting_equation",
                    "error",
                    f"{component.component_id} uses non-claim-supporting equation {eq_id!r} as an output",
                    f"components[{component.component_id}].output_equation_ids",
                ))
            if review_map.get(eq_id) and component.review_status == "auto_accepted":
                issues.append(ValidationIssue(
                    "review_required_output_component_auto_accepted",
                    "error",
                    f"{component.component_id} has review-required output equation {eq_id!r} "
                    "but component.review_status is auto_accepted",
                    f"components[{component.component_id}].review_status",
                ))

        text = " ".join([
            component.label.lower(),
            component.summary.lower(),
            component.reason.lower(),
        ])
        if "eliminat" in text or "bias" in text:
            if not component.eliminated_symbols:
                issues.append(ValidationIssue(
                    "bias_elimination_missing_eliminated_symbols",
                    "warning",
                    f"{component.component_id} appears to describe elimination but has no eliminated_symbols",
                    f"components[{component.component_id}].eliminated_symbols",
                ))
            if not component.retained_symbols:
                issues.append(ValidationIssue(
                    "bias_elimination_missing_retained_symbols",
                    "warning",
                    f"{component.component_id} appears to describe elimination but has no retained_symbols",
                    f"components[{component.component_id}].retained_symbols",
                ))
            if (
                ("second" in text or "second-order" in text or "2nd" in text)
                and ("third" in text or "third-order" in text or "3rd" in text)
            ):
                issues.append(ValidationIssue(
                    "bias_elimination_mixes_second_and_third_order",
                    "warning",
                    f"{component.component_id} appears to combine second- and third-order bias elimination; split it",
                    f"components[{component.component_id}]",
                ))
        if "consistency relation" in text and not component.output_equation_ids:
            issues.append(ValidationIssue(
                "consistency_relation_without_output_equations",
                "error",
                f"{component.component_id} outputs a consistency relation but has no output_equation_ids",
                f"components[{component.component_id}].output_equation_ids",
            ))
        return issues

    def _check_operation(self, component: ComponentRecord) -> list[ValidationIssue]:
        """Derivation-path components should declare a single main operation (issue #300)."""
        deriv_like = component.component_type in {
            "RelationComponent",
            "PaperRelationComponent",
            "MethodComponent",
            "CorrectionComponent",
        }
        if not deriv_like:
            return []
        has_equations = bool(_component_equation_refs(component, component.evidence_refs or {}))
        if has_equations and not (component.operation or "").strip():
            return [ValidationIssue(
                "derivation_component_missing_operation",
                "warning",
                f"{component.component_id} ({component.component_type}) has equations "
                "but no declared main operation; theory components should expose one operation",
                f"components[{component.component_id}].operation",
            )]
        return []

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
    return any((refs or {}).get(key) for key in ("claim_ids", "evidence_ids", "equation_ids", "thesis_refs")) or any(
        dsl_refs.get(key) for key in ("node_ids", "edge_ids")
    )


def _component_equation_refs(component: ComponentRecord, refs: dict) -> list[str]:
    values: list[str] = []
    values.extend(refs.get("equation_ids") or [])
    for field_name in (
        "linked_equation_ids",
        "input_equation_ids",
        "intermediate_equation_ids",
        "output_equation_ids",
        "constraint_equation_ids",
        "definition_equation_ids",
        "review_required_equation_ids",
    ):
        values.extend(getattr(component, field_name, []) or [])
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        eq_id = str(value)
        if eq_id and eq_id not in seen:
            seen.add(eq_id)
            result.append(eq_id)
    return result


def _meta_prior_evidence_ratio(component: ComponentRecord) -> float:
    values = []
    for key in ("claim_ids", "thesis_refs"):
        values.extend(str(v).lower() for v in component.evidence_refs.get(key, []) or [])
    if not values:
        return 0.0
    bad = sum(1 for v in values if "prior" in v or "meta" in v)
    return bad / len(values)
