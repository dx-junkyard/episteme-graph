"""EquationRoleClassifier — deterministic equation→component-role assignment.

Issue #300: assign equations to component roles based on derivation structure
and equation semantics, rather than dumping every related equation into a single
undifferentiated ``linked_equation_ids`` list.

Role classification rules (from the issue):

| Condition                                                | Role            |
|----------------------------------------------------------|-----------------|
| Equation appears as a derivation input                   | input           |
| Equation appears as a derivation output but not final    | intermediate    |
| Equation is the component's declared result              | output          |
| Equation has semantic kind ``definition``                | definition      |
| Equation has kind ``consistency_relation`` / ``constraint`` | constraint    |
| Equation has ``needs_math_review: true``                 | review_required |

This module is intentionally non-LLM: it only consumes equation semantics and
derivation steps that upstream stages already produced.
"""
from __future__ import annotations

from dataclasses import dataclass, field

_DEFINITION_KINDS = {"definition", "equation_definition", "define"}
_CONSTRAINT_KINDS = {
    "constraint",
    "condition",
    "consistency_relation",
    "consistency",
    "validity_condition",
}


@dataclass
class EquationRoleClassification:
    input_equation_ids: list[str] = field(default_factory=list)
    intermediate_equation_ids: list[str] = field(default_factory=list)
    output_equation_ids: list[str] = field(default_factory=list)
    definition_equation_ids: list[str] = field(default_factory=list)
    constraint_equation_ids: list[str] = field(default_factory=list)
    review_required_equation_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "input_equation_ids": list(self.input_equation_ids),
            "intermediate_equation_ids": list(self.intermediate_equation_ids),
            "output_equation_ids": list(self.output_equation_ids),
            "definition_equation_ids": list(self.definition_equation_ids),
            "constraint_equation_ids": list(self.constraint_equation_ids),
            "review_required_equation_ids": list(self.review_required_equation_ids),
        }


class EquationRoleClassifier:
    """Assign component-relevant equations to theory roles."""

    def classify(
        self,
        equation_ids,
        equation_index: dict[str, dict] | None = None,
        derivation_steps=None,
        declared_output_ids=None,
    ) -> EquationRoleClassification:
        eq_index = equation_index or {}
        relevant = _ordered_unique(equation_ids)
        relevant_set = set(relevant)
        if not relevant:
            return EquationRoleClassification()

        steps = list(derivation_steps or [])
        deriv_inputs: set[str] = set()
        deriv_outputs: set[str] = set()
        for step in steps:
            deriv_inputs.update(_step_field(step, "input_equation_ids"))
            deriv_outputs.update(_step_field(step, "output_equation_ids"))

        # The component's declared result. Fall back to the final derivation
        # step outputs when no explicit result is provided.
        declared = set(_ordered_unique(declared_output_ids))
        if not declared and steps:
            declared = set(_step_field(steps[-1], "output_equation_ids")) & relevant_set

        classification = EquationRoleClassification()
        for eq_id in relevant:
            eq = eq_index.get(eq_id) or {}
            kind = _equation_kind(eq)

            is_output = eq_id in declared
            is_deriv_output = eq_id in deriv_outputs
            is_deriv_input = eq_id in deriv_inputs

            if is_output:
                classification.output_equation_ids.append(eq_id)
            elif is_deriv_output:
                # Produced by a derivation step but not the final result.
                classification.intermediate_equation_ids.append(eq_id)

            if is_deriv_input and not is_output:
                classification.input_equation_ids.append(eq_id)

            if kind in _DEFINITION_KINDS:
                classification.definition_equation_ids.append(eq_id)
            if kind in _CONSTRAINT_KINDS:
                classification.constraint_equation_ids.append(eq_id)

            if _requires_review(eq):
                classification.review_required_equation_ids.append(eq_id)

        # An equation that is neither a derivation input/output nor a declared
        # result, and has no special kind, is still an input by default so that
        # derivation-like components always expose where their math comes from.
        classified = (
            set(classification.input_equation_ids)
            | set(classification.intermediate_equation_ids)
            | set(classification.output_equation_ids)
            | set(classification.definition_equation_ids)
            | set(classification.constraint_equation_ids)
        )
        for eq_id in relevant:
            if eq_id not in classified:
                classification.input_equation_ids.append(eq_id)

        return classification


def _equation_kind(eq: dict) -> str:
    for key in ("role", "equation_type", "semantic_kind", "kind"):
        value = eq.get(key)
        if value:
            return str(value).strip().lower()
    secondary = eq.get("secondary_types") or []
    for value in secondary:
        text = str(value).strip().lower()
        if text in _DEFINITION_KINDS or text in _CONSTRAINT_KINDS:
            return text
    return ""


def _requires_review(eq: dict) -> bool:
    policy = eq.get("confidence_policy") if isinstance(eq.get("confidence_policy"), dict) else {}
    reconstruction = eq.get("reconstruction") if isinstance(eq.get("reconstruction"), dict) else {}
    recon_status = eq.get("reconstruction_status")
    if recon_status in (None, "",) and reconstruction:
        recon_status = reconstruction.get("status")
    return (
        bool(eq.get("needs_math_review"))
        or bool(eq.get("review_flags"))
        or eq.get("semantic_status") == "reconstruction_based"
        or (recon_status not in (None, "", "none"))
        or bool(policy.get("must_not_treat_as_source_extracted"))
        or policy.get("can_support_claim") is False
    )


def _step_field(step, name: str) -> list[str]:
    if isinstance(step, dict):
        raw = step.get(name) or []
    else:
        raw = getattr(step, name, None) or []
    return [str(v) for v in raw if v]


def _ordered_unique(values) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        text = str(value).strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result
