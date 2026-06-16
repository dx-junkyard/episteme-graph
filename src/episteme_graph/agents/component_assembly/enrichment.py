"""Deterministic enrichment for ComponentAssemblyAgent outputs."""
from __future__ import annotations

from .schema import ComponentAssemblyLLMInput, ComponentAssemblyResult, ComponentRecord
from .claim_centered_planner import infer_support_metadata


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
    claim_index = _claim_concept_index(llm_input)
    eq_symbol_index = _equation_symbol_index(eq_index)
    concept_vocab = _concept_vocab(llm_input, claim_index)

    for component in result.components:
        _normalize_component_lists(component)
        _fill_equation_roles(component, eq_index, review_required)
        _flag_equation_role_conflicts(component, eq_index)
        _fill_equation_confidence_summary(component, eq_index, review_required)
        _fill_confidence_gate(component, eq_index)
        _fill_claim_support_metadata(component, llm_input)
        _fill_thesis_support_refs(component, llm_input)
        _fill_concepts(component, claim_index, eq_symbol_index, concept_vocab)
        _propagate_review_status(component)
        _fill_internal_flow(component)

    # introduced vs reused depends on cross-component ordering (issue #8).
    _assign_introduced_reused(result.components)

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


# equation_semantics の equation_type を正本とし、矛盾する component 側 role を
# 検出する対応表 (issue #358)。
_EQUATION_ROLE_CONFLICTS = {
    "definition": ("output_equation_ids",),
    "result": ("definition_equation_ids",),
}


def _flag_equation_role_conflicts(
    component: ComponentRecord,
    eq_index: dict[str, dict],
) -> None:
    """Mark stage-to-stage equation role conflicts on the component (issue #358).

    The conflict is recorded in ``review_notes`` (components carry no
    review_reasons field) and the component is demoted out of auto_accepted so
    a teacher confirms the classification. Links are kept, never dropped.
    """
    conflicts: list[str] = []
    for eq_type, conflicting_fields in _EQUATION_ROLE_CONFLICTS.items():
        for field_name in conflicting_fields:
            for eq_id in getattr(component, field_name, []) or []:
                eq = eq_index.get(str(eq_id)) or {}
                if str(eq.get("role") or "").lower() != eq_type:
                    continue
                conflicts.append(
                    f"equation_role_conflict: {eq_id} listed in {field_name} "
                    f"but equation_semantics types it as {eq_type}"
                )
    if not conflicts:
        return
    for note in conflicts:
        if note not in component.review_notes:
            component.review_notes.append(note)
    if component.review_status == "auto_accepted":
        component.review_status = "teacher_review_required"


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


def _fill_confidence_gate(
    component: ComponentRecord,
    eq_index: dict[str, dict],
) -> None:
    refs = _all_component_equation_ids(component)
    blocked = [
        eq_id for eq_id in refs
        if _equation_blocks_claim_and_derivation(eq_index.get(eq_id) or {})
    ]
    if not blocked:
        component.confidence_gate = {
            "blocked_by_equation_ids": [],
            "blocked_reason": "",
            "downstream_allowed_use": "display_with_warning",
            "allowed_downstream_use": "display_with_warning",
        }
        return
    reason = "linked equation cannot support claim or derivation"
    component.confidence_gate = {
        "blocked_by_equation_ids": blocked,
        "blocked_reason": reason,
        "downstream_allowed_use": "semantic_hint_only",
        "allowed_downstream_use": "semantic_hint_only",
    }
    component.review_required_equation_ids = _unique(
        list(component.review_required_equation_ids or []) + blocked
    )
    if reason not in component.review_notes:
        component.review_notes.append(reason)
    component.review_status = "review_required"
    component.publish_ready = False


def _fill_claim_support_metadata(
    component: ComponentRecord,
    llm_input: ComponentAssemblyLLMInput,
) -> None:
    metadata = infer_support_metadata(component, llm_input.claim_centered_plan)
    component.support_role = metadata["support_role"]
    component.support_kind = metadata["support_kind"]
    component.supports_claim_ids = _unique(metadata["supports_claim_ids"])
    component.support_distance_to_headline_claim = metadata["support_distance_to_headline_claim"]
    if component.supports_claim_ids:
        component.linked_claim_ids = _unique(list(component.linked_claim_ids or []) + component.supports_claim_ids)


def _fill_thesis_support_refs(
    component: ComponentRecord,
    llm_input: ComponentAssemblyLLMInput,
) -> None:
    """Derive supports_thesis_node_ids from claim/equation overlap (issue #354).

    A component backs a thesis node (central_thesis or support:<section>:<idx>)
    when it shares at least one claim or equation reference with that node. No
    LLM re-judgement: only IDs already present in both artifacts are used.
    """
    nodes = llm_input.thesis_nodes or []
    if not nodes:
        return
    claim_ids = set(_component_claim_ids(component))
    equation_ids = set(_all_component_equation_ids(component))
    refs: list[str] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        thesis_ref = str(node.get("thesis_ref") or "")
        if not thesis_ref:
            continue
        node_claims = {str(v) for v in (node.get("claim_ids") or []) if v}
        node_equations = {str(v) for v in (node.get("equation_ids") or []) if v}
        if (claim_ids & node_claims) or (equation_ids & node_equations):
            refs.append(thesis_ref)
    component.supports_thesis_node_ids = _unique(
        list(component.supports_thesis_node_ids or []) + refs
    )


def _fill_concepts(
    component: ComponentRecord,
    claim_index: dict[str, dict],
    eq_symbol_index: dict[str, list[str]],
    concept_vocab: dict[str, list[str]],
) -> None:
    """Derive component concepts deterministically (issue #8).

    Concepts come from linked atomic-claim concepts, equation symbols, and
    cartridge / claim concept terms mentioned in the component text. Concepts from
    non-atomic claims are not used as confirmed concept backing.
    """
    concepts: list[str] = []
    for cid in _component_claim_ids(component):
        info = claim_index.get(cid)
        if not info:
            continue
        if info.get("is_atomic") and str(info.get("atomicity", "atomic")) == "atomic":
            concepts.extend(info.get("concepts") or [])

    for eq_id in _all_component_equation_ids(component):
        concepts.extend(eq_symbol_index.get(eq_id, []))

    body = _component_text(component)
    for canonical, needles in concept_vocab.items():
        if any(needle in body for needle in needles):
            concepts.append(canonical)
    component.concepts = _unique(concepts)

    prereq_text = _precondition_text(component)
    prerequisites = [
        canonical for canonical, needles in concept_vocab.items()
        if any(needle in prereq_text for needle in needles)
    ]
    component.prerequisite_concepts = _unique(prerequisites)


def _assign_introduced_reused(components: list[ComponentRecord]) -> None:
    """Split each component's concepts into newly introduced vs reused (issue #8).

    Components are walked in support order (closest to the headline claim first,
    then original order); a concept is "introduced" the first time it appears and
    "reused" thereafter.
    """
    order = sorted(
        range(len(components)),
        key=lambda i: (
            int(getattr(components[i], "support_distance_to_headline_claim", 0) or 0),
            i,
        ),
    )
    seen: set[str] = set()
    for i in order:
        component = components[i]
        introduced: list[str] = []
        reused: list[str] = []
        for concept in component.concepts or []:
            if concept in seen:
                reused.append(concept)
            else:
                introduced.append(concept)
                seen.add(concept)
        component.introduced_concepts = introduced
        component.reused_concepts = reused


def _component_claim_ids(component: ComponentRecord) -> list[str]:
    refs = component.evidence_refs or {}
    return _unique(
        list(component.supports_claim_ids or [])
        + list(component.linked_claim_ids or [])
        + list(refs.get("claim_ids") or [])
    )


def _component_text(component: ComponentRecord) -> str:
    return " ".join(
        str(part or "")
        for part in (
            component.label,
            component.summary,
            component.reason,
            getattr(component, "teaching_takeaway", ""),
        )
    ).lower()


def _precondition_text(component: ComponentRecord) -> str:
    parts: list[str] = []
    for item in list(component.preconditions or []) + list(component.inputs or []):
        if isinstance(item, dict):
            parts.append(str(item.get("condition") or item.get("name") or item.get("text") or ""))
        elif isinstance(item, str):
            parts.append(item)
    return " ".join(parts).lower()


def _claim_concept_index(llm_input: ComponentAssemblyLLMInput) -> dict[str, dict]:
    index: dict[str, dict] = {}
    rows = list(llm_input.available_claims or []) + list(llm_input.accepted_claims or [])
    for row in rows:
        if not isinstance(row, dict):
            continue
        cid = str(row.get("claim_id") or "")
        if not cid:
            continue
        concepts = [str(c) for c in (row.get("concepts") or []) if c]
        atomicity = str(row.get("atomicity", "atomic") or "atomic")
        entry = index.setdefault(cid, {
            "concepts": [],
            "atomicity": atomicity,
            "is_atomic": bool(row.get("is_atomic", atomicity == "atomic")),
            "concept_assignment_status": str(
                row.get("concept_assignment_status", "review_required") or "review_required"
            ),
        })
        if concepts and not entry["concepts"]:
            entry["concepts"] = concepts
    return index


def _equation_symbol_index(eq_index: dict[str, dict]) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for eq_id, eq in eq_index.items():
        symbols: list[str] = []
        for sym in eq.get("defined_symbols") or []:
            if isinstance(sym, dict):
                name = str(sym.get("symbol") or "").strip()
            else:
                name = str(sym or "").strip()
            if name:
                symbols.append(name)
        symbols.extend(str(s).strip() for s in (eq.get("used_symbols") or []) if str(s).strip())
        if symbols:
            index[eq_id] = _unique(symbols)
    return index


def _concept_vocab(
    llm_input: ComponentAssemblyLLMInput,
    claim_index: dict[str, dict],
) -> dict[str, list[str]]:
    """Map a canonical concept name to the lowercased needles that imply it."""
    vocab: dict[str, list[str]] = {}
    for term in llm_input.normalized_terms or []:
        if not isinstance(term, dict):
            continue
        canonical = str(term.get("canonical") or "").strip()
        if not canonical:
            continue
        needles = [canonical.lower()] + [str(a).lower() for a in (term.get("aliases") or []) if a]
        vocab[canonical] = _unique(needles)
    for info in claim_index.values():
        for name in info.get("concepts") or []:
            canonical = str(name).strip()
            if canonical and canonical not in vocab:
                vocab[canonical] = [canonical.lower()]
    return vocab


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
    if "consistency" in text or "constraint" in text or "residual" in text:
        return "derive_constraint"
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
        or _equation_consistency_requires_review(eq)
        or eq.get("semantic_status") == "reconstruction_based"
        or eq.get("reconstruction_status") not in (None, "", "none")
        or bool(policy.get("must_not_treat_as_source_extracted"))
        or policy.get("can_support_claim") is False
    )


def _equation_blocks_claim_and_derivation(eq: dict) -> bool:
    policy = eq.get("confidence_policy") if isinstance(eq.get("confidence_policy"), dict) else {}
    allowed_use = str(policy.get("allowed_downstream_use") or policy.get("downstream_allowed_use") or "")
    if allowed_use == "blocked":
        return True
    if (
        policy.get("can_support_claim") is False
        and policy.get("can_be_used_in_derivation") is False
        and allowed_use in {"", "blocked", "semantic_hint_only"}
    ):
        return True
    return (
        eq.get("latex") is None
        and eq.get("plain_text") is None
        and eq.get("extraction_status") in ("partial", "fragment_only", "label_only", "missing", "unparsed")
    )


def _equation_consistency_requires_review(eq: dict) -> bool:
    consistency = eq.get("equation_consistency") if isinstance(eq.get("equation_consistency"), dict) else {}
    return (
        bool(consistency.get("review_required"))
        or consistency.get("raw_text_latex_match") == "mismatch"
        or consistency.get("label_location_match") == "mismatch"
        or consistency.get("source_span_quality") == "corrupted"
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
