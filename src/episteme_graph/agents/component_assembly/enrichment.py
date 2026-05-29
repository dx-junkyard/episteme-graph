"""Deterministic enrichment for ComponentAssemblyAgent outputs."""
from __future__ import annotations

from .schema import ComponentAssemblyLLMInput, ComponentAssemblyResult, ComponentRecord


_DERIVATION_TYPES = {"RelationComponent", "PaperRelationComponent", "MethodComponent"}
_INTERNAL_FLOW_REQUIRED_TYPES = {
    "RelationComponent",
    "PaperRelationComponent",
    "CorrectionComponent",
    "DiagnosticComponent",
    "MethodComponent",
}


def enrich_component_assembly(
    result: ComponentAssemblyResult,
    llm_input: ComponentAssemblyLLMInput,
) -> ComponentAssemblyResult:
    """Fill role-specific equation fields and minimal internal_flow.

    The LLM is asked to emit these fields, but export quality should not depend
    entirely on the model following the schema. This pass only uses IDs already
    present in upstream artifacts or component-local inputs/outputs.
    """
    eq_index = _equation_index(llm_input)
    review_required = {
        eq_id for eq_id, eq in eq_index.items()
        if _requires_review(eq)
    }

    for component in result.components:
        _normalize_component_lists(component)
        _fill_equation_roles(component, eq_index, review_required)
        _fill_equation_confidence_summary(component, eq_index, review_required)
        _propagate_review_status(component)
        _fill_internal_flow(component)

    return result


def _normalize_component_lists(component: ComponentRecord) -> None:
    for field_name in (
        "linked_equation_ids",
        "input_equation_ids",
        "intermediate_equation_ids",
        "output_equation_ids",
        "definition_equation_ids",
        "constraint_equation_ids",
        "review_required_equation_ids",
        "linked_claim_ids",
        "linked_evidence_ids",
        "linked_derivation_ids",
        "linked_dsl_node_ids",
        "linked_dsl_edge_ids",
    ):
        setattr(component, field_name, _unique(getattr(component, field_name, []) or []))


def _fill_equation_roles(
    component: ComponentRecord,
    eq_index: dict[str, dict],
    review_required: set[str],
) -> None:
    refs = component.evidence_refs or {}
    input_eqs = _field_equation_ids(component.inputs) + _field_equation_ids(component.preconditions)
    output_eqs = _field_equation_ids(component.outputs)
    caution_eqs = _field_equation_ids(component.cautions)
    linked_eqs = _unique(
        list(refs.get("equation_ids") or [])
        + list(component.linked_equation_ids or [])
        + input_eqs
        + output_eqs
        + caution_eqs
    )

    component.linked_equation_ids = _unique(list(component.linked_equation_ids or []) + linked_eqs)
    component.input_equation_ids = _unique(list(component.input_equation_ids or []) + input_eqs)
    component.output_equation_ids = _unique(list(component.output_equation_ids or []) + output_eqs)
    component.constraint_equation_ids = _unique(list(component.constraint_equation_ids or []) + caution_eqs)

    for eq_id in linked_eqs:
        eq = eq_index.get(eq_id) or {}
        role = str(eq.get("role") or "").lower()
        if eq_id in review_required:
            component.review_required_equation_ids = _append_unique(component.review_required_equation_ids, eq_id)
        if role in {"definition", "equation_definition"}:
            component.definition_equation_ids = _append_unique(component.definition_equation_ids, eq_id)
        elif role in {"constraint", "condition", "consistency_relation"}:
            component.constraint_equation_ids = _append_unique(component.constraint_equation_ids, eq_id)
        elif role in {"result"}:
            component.output_equation_ids = _append_unique(component.output_equation_ids, eq_id)
        elif role in {"transformation"} and eq_id not in component.output_equation_ids:
            component.intermediate_equation_ids = _append_unique(component.intermediate_equation_ids, eq_id)

    if component.component_type in _DERIVATION_TYPES and linked_eqs:
        if not component.output_equation_ids:
            component.output_equation_ids = [linked_eqs[-1]]
        if not component.input_equation_ids:
            component.input_equation_ids = [eq_id for eq_id in linked_eqs if eq_id not in component.output_equation_ids]


def _fill_equation_confidence_summary(
    component: ComponentRecord,
    eq_index: dict[str, dict],
    review_required: set[str],
) -> None:
    refs = _all_component_equation_ids(component)
    if not refs:
        return
    reconstructed = []
    non_supporting = []
    for eq_id in refs:
        eq = eq_index.get(eq_id) or {}
        policy = eq.get("confidence_policy") if isinstance(eq.get("confidence_policy"), dict) else {}
        if eq.get("semantic_status") == "reconstruction_based" or eq.get("reconstruction_status") not in (None, "", "none"):
            reconstructed.append(eq_id)
        if policy.get("can_support_claim") is False:
            non_supporting.append(eq_id)
    component.equation_confidence_summary = {
        "all_source_backed": not reconstructed,
        "has_review_required": any(eq_id in review_required for eq_id in refs),
        "has_reconstructed_equations": bool(reconstructed),
        "non_supporting_equation_ids": non_supporting,
    }


def _propagate_review_status(component: ComponentRecord) -> None:
    if component.review_required_equation_ids and component.review_status == "auto_accepted":
        component.review_status = "teacher_review_required"


def _fill_internal_flow(component: ComponentRecord) -> None:
    if component.internal_flow or component.component_type not in _INTERNAL_FLOW_REQUIRED_TYPES:
        return
    inputs = component.input_equation_ids or _field_equation_ids(component.inputs) or _field_text_refs(component.inputs)
    outputs = component.output_equation_ids or _field_equation_ids(component.outputs) or _field_text_refs(component.outputs)
    if not inputs or not outputs:
        return
    relation = _flow_relation(component)
    component.internal_flow = [
        {"from": src, "relation": relation, "to": dst}
        for src in inputs[:3]
        for dst in outputs[:2]
        if src != dst
    ][:4]


def _flow_relation(component: ComponentRecord) -> str:
    text = " ".join([component.label, component.summary, component.reason]).lower()
    if "eliminat" in text:
        return "eliminate_parameter"
    if "consistency" in text:
        return "derive_consistency_relation"
    if "diagnos" in text or "forecast" in text:
        return "diagnose_with"
    if "correct" in text:
        return "apply_correction"
    return "derive_from"


def _equation_index(llm_input: ComponentAssemblyLLMInput) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for eq in list(llm_input.equations or []) + list(llm_input.available_equations or []):
        eq_id = str(eq.get("equation_id") or "")
        if not eq_id:
            continue
        existing = index.get(eq_id, {})
        merged = dict(existing)
        merged.update(eq)
        index[eq_id] = merged
    return index


def _requires_review(eq: dict) -> bool:
    policy = eq.get("confidence_policy") if isinstance(eq.get("confidence_policy"), dict) else {}
    return (
        bool(eq.get("needs_math_review"))
        or bool(eq.get("review_flags"))
        or eq.get("semantic_status") == "reconstruction_based"
        or eq.get("reconstruction_status") not in (None, "", "none")
        or bool(policy.get("must_not_treat_as_source_extracted"))
        or policy.get("can_support_claim") is False
    )


def _field_equation_ids(items: list[dict]) -> list[str]:
    ids: list[str] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        for key in ("equation_ids", "equations"):
            raw = item.get(key) or []
            if isinstance(raw, str):
                ids.append(raw)
            elif isinstance(raw, list):
                ids.extend(str(v) for v in raw if v)
    return _unique(ids)


def _field_text_refs(items: list[dict]) -> list[str]:
    refs: list[str] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        refs.append(item.get("name") or item.get("text") or item.get("label") or "")
    return _unique(refs)


def _all_component_equation_ids(component: ComponentRecord) -> list[str]:
    refs = component.evidence_refs or {}
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
    return _unique(values)


def _append_unique(values: list[str], value: str) -> list[str]:
    return _unique(list(values or []) + [value])


def _unique(values: list[object]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result
