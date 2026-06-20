"""ComponentGranularityAnalyzer — Step 1 detection and split proposal only."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..theory_operations import classify_operation
from .responsibility import canonical_responsibility_type, canonical_responsibility_types
from .schema import ComponentAssemblyResult, ComponentRecord
from .split_recommendation import normalize_split_recommendation


THRESHOLDS = {
    "max_linked_equations": 8,
    "max_internal_flow_steps": 5,
    "max_source_scope_width": 2,
}


@dataclass
class ComponentQuality:
    component_id: str
    granularity_status: str
    equation_count: int = 0
    claim_count: int = 0
    derivation_step_count: int = 0
    responsibility_count: int = 0
    source_scope_width: int = 0
    split_required: bool = False
    split_reasons: list[str] = field(default_factory=list)
    suggested_split: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "component_id": self.component_id,
            "granularity_status": self.granularity_status,
            "equation_count": self.equation_count,
            "claim_count": self.claim_count,
            "derivation_step_count": self.derivation_step_count,
            "responsibility_count": self.responsibility_count,
            "source_scope_width": self.source_scope_width,
            "split_required": self.split_required,
            "split_reasons": list(self.split_reasons),
            "suggested_split": list(self.suggested_split),
        }


class ComponentGranularityAnalyzer:
    """Annotate components without changing component membership."""

    def analyze(
        self,
        result: ComponentAssemblyResult,
        *,
        derivations=None,
    ) -> ComponentAssemblyResult:
        derivation_index = _component_derivation_step_index(derivations)
        equation_step_index = _equation_derivation_step_index(derivations)
        quality_entries: list[dict] = []
        for component in result.components:
            steps = _derivation_steps_for_component(
                component, derivation_index, equation_step_index
            )
            quality = self._analyze_component(component, steps)
            _apply_quality(component, quality)
            quality_entries.append(quality.to_dict())

        diagnostics = dict(result.diagnostics or {})
        diagnostics["component_granularity"] = {
            "component_quality": quality_entries,
            "thresholds": dict(THRESHOLDS),
        }
        result.diagnostics = diagnostics
        return result

    def _analyze_component(self, component: ComponentRecord, derivation_steps: list[dict]) -> ComponentQuality:
        equation_ids = _component_equation_refs(component)
        claim_ids = _ordered_unique(
            list((component.evidence_refs or {}).get("claim_ids") or [])
            + list(component.linked_claim_ids or [])
        )
        responsibilities = _detected_responsibilities(component)
        source_scope_width = _source_scope_width(component.source_scope or {})
        split_reasons = _split_reasons(
            component=component,
            equation_count=len(equation_ids),
            responsibility_types=responsibilities,
            source_scope_width=source_scope_width,
        )
        split_required = bool(split_reasons)
        status = _granularity_status(component, responsibilities, split_reasons)
        return ComponentQuality(
            component_id=component.component_id,
            granularity_status=status,
            equation_count=len(equation_ids),
            claim_count=len(claim_ids),
            derivation_step_count=len(derivation_steps),
            responsibility_count=len(responsibilities),
            source_scope_width=source_scope_width,
            split_required=split_required,
            split_reasons=split_reasons,
            suggested_split=_suggested_split(component, responsibilities) if split_required else [],
        )


def _apply_quality(component: ComponentRecord, quality: ComponentQuality) -> None:
    component.component_quality = quality.to_dict()
    # Canonical split-recommendation schema (#417): always emit the ``required``
    # key through the shared normalizer so every downstream stage reads it.
    component.split_recommendation = normalize_split_recommendation({
        "required": quality.split_required,
        "reasons": list(quality.split_reasons),
        "suggested_components": list(quality.suggested_split),
    })
    if component.responsibility_type:
        component.responsibility_type = canonical_responsibility_type(component.responsibility_type)


def _split_reasons(
    *,
    component: ComponentRecord,
    equation_count: int,
    responsibility_types: set[str],
    source_scope_width: int,
) -> list[str]:
    reasons: list[str] = []
    if len(responsibility_types) > 1:
        reasons.append("responsibility type has more than one primary value")
    if equation_count > THRESHOLDS["max_linked_equations"]:
        reasons.append("linked equation count exceeds threshold")
    if len(component.internal_flow or []) > THRESHOLDS["max_internal_flow_steps"]:
        reasons.append("internal_flow has too many steps")
    if source_scope_width > THRESHOLDS["max_source_scope_width"]:
        reasons.append("source scope spans multiple conceptual sections")
    if _major_io_transform_count(component) > 1:
        reasons.append("component has more than one major input/output transformation")
    if {"definition", "derivation"} <= responsibility_types:
        reasons.append("component includes both definitions and derivations")
    if {"derivation", "constraint"} <= responsibility_types:
        reasons.append("component includes both derivation and final result")
    if {"model", "derivation"} <= responsibility_types:
        reasons.append("model and derivation are both present")
    if {"observable_basis", "constraint"} <= responsibility_types:
        reasons.append("observable_basis and constraint are both present")
    if {"definition", "application"} <= responsibility_types:
        reasons.append("definition and application are both present")
    return _ordered_unique(reasons)


def _granularity_status(
    component: ComponentRecord,
    responsibility_types: set[str],
    split_reasons: list[str],
) -> str:
    if _has_mixed_responsibility(responsibility_types):
        return "mixed_responsibility"
    if split_reasons:
        return "too_coarse"
    if component.review_required_equation_ids or component.review_status in {"review_required", "teacher_review_required"}:
        return "review_required"
    if not (_component_equation_refs(component) or component.linked_claim_ids or component.internal_flow):
        return "too_fine"
    return "good"


def _has_mixed_responsibility(responsibility_types: set[str]) -> bool:
    if len(responsibility_types) <= 1:
        return False
    mixed_pairs = [
        {"definition", "derivation"},
        {"derivation", "constraint"},
        {"model", "derivation"},
        {"observable_basis", "constraint"},
        {"definition", "application"},
    ]
    return any(pair <= responsibility_types for pair in mixed_pairs) or len(responsibility_types) > 1


# Generic operation family → responsibility type (issues #395 / #396). Domain
# vocabulary plays no part: responsibilities are inferred from the broad,
# domain-neutral operation family produced by the theory-operation framework.
_FAMILY_TO_RESPONSIBILITY = {
    "introduce_entity": "definition",
    "define_relation": "definition",
    "impose_assumption": "model",
    "construct_model": "model",
    "transform_representation": "equation_system",
    "derive_consequence": "derivation",
    "evaluate_condition": "constraint",
    "compare_cases": "application",
    "validate_or_test": "application",
    "apply_to_context": "application",
    "state_limitation": "limitation",
}


def _operation_strings(component: ComponentRecord) -> list[str]:
    """Collect operation-bearing strings from a component (domain-neutral).

    ``responsibility_type`` is intentionally excluded — it is a responsibility
    label, not an operation, and is added directly by the caller.
    """
    values: list[str] = [
        str(component.operation or ""),
        str(component.primary_operation or ""),
    ]
    values.extend(str(op or "") for op in (component.secondary_operations or []))
    for step in component.internal_flow or []:
        if isinstance(step, dict):
            values.append(str(step.get("operation") or step.get("relation") or ""))
    return [v for v in values if v]


def _detected_responsibilities(component: ComponentRecord) -> set[str]:
    """Detect responsibility types from generic operation families only (#395/#396)."""
    values = {canonical_responsibility_type(component.responsibility_type)}
    for op in _operation_strings(component):
        family = classify_operation(op).operation_family
        responsibility = _FAMILY_TO_RESPONSIBILITY.get(family)
        if responsibility:
            values.add(responsibility)
    values.discard("")
    return canonical_responsibility_types(values)


def _suggested_split(component: ComponentRecord, responsibilities: set[str]) -> list[dict]:
    """Build suggested children with *distinct* candidate links (#417).

    Each suggested child must carry its own candidate equations/claims/derivations
    rather than a duplicate of the parent's full link set, so the downstream
    refiner has identifying information to redistribute by responsibility. The
    parent's links are partitioned deterministically across the suggested
    children (round-robin by sorted responsibility order); the ComponentRefiner
    later performs the role-aware reassignment using equation roles.
    """
    ordered = sorted(responsibilities)
    if not ordered:
        return []
    equation_partition = _partition(_component_equation_refs(component), len(ordered))
    claim_partition = _partition(_component_claim_refs(component), len(ordered))
    derivation_partition = _partition(
        list(component.linked_derivation_ids or []), len(ordered)
    )
    evidence_partition = _partition(
        _ordered_unique(
            list(component.linked_evidence_ids or [])
            + list((component.evidence_refs or {}).get("evidence_ids") or [])
        ),
        len(ordered),
    )
    return [
        {
            "name": responsibility.replace("_", " ").title(),
            "responsibility_type": responsibility,
            "linked_equation_ids": equation_partition[idx],
            "candidate_claim_ids": claim_partition[idx],
            "candidate_derivation_ids": derivation_partition[idx],
            "candidate_evidence_ids": evidence_partition[idx],
        }
        for idx, responsibility in enumerate(ordered)
    ]


def _partition(values: list[str], buckets: int) -> list[list[str]]:
    """Split ``values`` into ``buckets`` disjoint lists, deterministically.

    No value appears in more than one bucket, so suggested children never share
    an identical link set (#417).
    """
    out: list[list[str]] = [[] for _ in range(max(buckets, 1))]
    for i, value in enumerate(values):
        out[i % len(out)].append(value)
    return out


def _component_claim_refs(component: ComponentRecord) -> list[str]:
    return _ordered_unique(
        list((component.evidence_refs or {}).get("claim_ids") or [])
        + list(component.linked_claim_ids or [])
    )


def _component_derivation_step_index(derivations) -> dict[str, list]:
    index: dict[str, list] = {}
    for chain in list(getattr(derivations, "chains", []) or []):
        steps = list(getattr(chain, "steps", []) or [])
        for comp_id in getattr(chain, "linked_component_ids", []) or []:
            index.setdefault(str(comp_id), []).extend(steps)
    return index


def _equation_derivation_step_index(derivations) -> dict[str, list]:
    """Index derivation steps by the equation ids they touch (#417).

    When a derivation chain has no ``linked_component_ids`` it is invisible to the
    component→step index, which left ``derivation_step_count`` at 0 and hid the
    derivation structure from the granularity plan. Indexing steps by their
    input/output equations lets a component recover its steps through equation
    overlap instead.
    """
    index: dict[str, list] = {}
    for chain in list(getattr(derivations, "chains", []) or []):
        for step in list(getattr(chain, "steps", []) or []):
            for eid in _step_equation_ids(step):
                index.setdefault(str(eid), []).append(step)
    return index


def _step_equation_ids(step) -> list[str]:
    ids: list[str] = []
    for field_name in ("input_equation_ids", "output_equation_ids", "inputs", "outputs"):
        value = getattr(step, field_name, None)
        if value is None and isinstance(step, dict):
            value = step.get(field_name)
        for item in value or []:
            if item:
                ids.append(str(item))
    return ids


def _derivation_steps_for_component(
    component: ComponentRecord,
    derivation_index: dict[str, list],
    equation_step_index: dict[str, list],
) -> list:
    """Collect a component's derivation steps via explicit link or eq overlap (#417)."""
    steps = list(derivation_index.get(component.component_id, []))
    if steps:
        return steps
    seen: list[int] = []
    overlap_steps: list = []
    for eid in _component_equation_refs(component):
        for step in equation_step_index.get(eid, []):
            marker = id(step)
            if marker not in seen:
                seen.append(marker)
                overlap_steps.append(step)
    return overlap_steps


def _component_equation_refs(component: ComponentRecord) -> list[str]:
    refs = component.evidence_refs or {}
    values: list[str] = list(refs.get("equation_ids") or [])
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
    return _ordered_unique(values)


def _source_scope_width(scope: dict) -> int:
    if not isinstance(scope, dict):
        return 0
    widths: list[int] = []
    for key in ("sections", "section_ids", "pages", "page_ranges", "block_ids"):
        value = scope.get(key)
        if isinstance(value, list):
            widths.append(len(value))
        elif value:
            widths.append(1)
    return max(widths or [0])


def _major_io_transform_count(component: ComponentRecord) -> int:
    output_groups = len([item for item in component.outputs or [] if isinstance(item, dict)])
    relations = {
        str(step.get("relation") or "")
        for step in component.internal_flow or []
        if isinstance(step, dict) and step.get("relation")
    }
    return max(output_groups, len(relations))


def _component_text(component: ComponentRecord) -> str:
    parts = [
        component.label,
        component.summary,
        component.reason,
        component.operation,
        component.primary_operation,
    ]
    for collection in (component.inputs, component.outputs, component.preconditions, component.cautions):
        for item in collection or []:
            if isinstance(item, dict):
                parts.extend(str(item.get(k) or "") for k in ("name", "label", "text", "role"))
    return " ".join(str(v) for v in parts if v).lower()


def _ordered_unique(values) -> list:
    result = []
    seen = set()
    for value in values or []:
        text = str(value)
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result
