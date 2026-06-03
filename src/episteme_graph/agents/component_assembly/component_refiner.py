"""ComponentRefiner — split summary-level components into theory operations.

Issue #300: the LLM tends to produce components that follow the explanation
structure of a paper (section / paragraph / summary units). Such components are
too coarse to be reusable theory parts. A single component like
``Linear elimination of nonlinear bias parameters`` actually bundles several
distinct theoretical operations (linearization, second/third-order bias solving,
consistency-relation derivations).

``ComponentRefiner`` is a deterministic (non-LLM) post-processing pass run after
the initial components are assembled. It uses the derivation chains — which carry
the real theory structure (one step = one operation, with input/output equations,
eliminated/retained symbols) — to detect over-large components and split them so
that:

    1 component = 1 reusable theory unit

A *reusable theory unit* is a theory-operation **family** (linearize / solve /
eliminate / derive / …), not a single equation step (issue #308). Several
equation steps of the same family collapse into one component and are preserved
in its ``internal_flow``. Generic operations (``transform`` / ``relate`` /
``connect`` / ``support`` / empty) never spawn a standalone child — they are kept
inside the nearest unit's ``internal_flow`` so the catalogue of reusable
components stays at the theory-unit altitude. Component labels/summaries describe
the theory object, not a bare operation name.

The boundary is decided by *theory structure*, not explanation style:
inputs change, outputs change, the operation family changes, symbols change.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field

from .equation_role_classifier import EquationRoleClassifier
from .schema import (
    ComponentAssemblyLLMInput,
    ComponentAssemblyResult,
    ComponentRecord,
)

# Component types that live on the main derivation path and are therefore
# candidates for theory-operation refinement.
_DERIVATION_TYPES = {
    "RelationComponent",
    "PaperRelationComponent",
    "MethodComponent",
    "CorrectionComponent",
}

# Generic operations that do not name a reusable theory unit (issue #308). Steps
# carrying these never form a standalone child component; they are folded into
# the nearest unit's internal_flow instead.
_GENERIC_OPERATIONS = {"transform", "relate", "connect", "support", "associate", ""}


@dataclass
class RefinementAction:
    parent_component_id: str
    parent_label: str
    child_component_ids: list[str]
    operations: list[str]
    reason: str

    def to_dict(self) -> dict:
        return {
            "parent_component_id": self.parent_component_id,
            "parent_label": self.parent_label,
            "child_component_ids": list(self.child_component_ids),
            "operations": list(self.operations),
            "reason": self.reason,
        }


@dataclass
class RefinementReport:
    split_actions: list[RefinementAction] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "split_count": len(self.split_actions),
            "split_actions": [a.to_dict() for a in self.split_actions],
            "warnings": list(self.warnings),
        }


class ComponentRefiner:
    def __init__(self) -> None:
        self._classifier = EquationRoleClassifier()

    def refine(
        self,
        result: ComponentAssemblyResult,
        llm_input: ComponentAssemblyLLMInput | None = None,
        derivations=None,
    ) -> ComponentAssemblyResult:
        eq_index = _equation_index(llm_input)
        chains = list(getattr(derivations, "chains", []) or [])
        report = RefinementReport()

        refined: list[ComponentRecord] = []
        # Map each split parent id → the id of its first child so that references
        # held elsewhere (dependencies, assembly hints) keep resolving.
        remap: dict[str, str] = {}
        for component in result.components:
            children = self._refine_component(component, eq_index, chains, report)
            if children and children[0].component_id != component.component_id:
                remap[component.component_id] = children[0].component_id
            refined.extend(children)

        if remap:
            self._remap_references(refined, result, remap)

        result.components = refined
        existing = result.refinement_report if isinstance(result.refinement_report, dict) else {}
        merged = dict(existing)
        merged.update(report.to_dict())
        result.refinement_report = merged
        return result

    def _remap_references(
        self,
        components: list[ComponentRecord],
        result: ComponentAssemblyResult,
        remap: dict[str, str],
    ) -> None:
        child_ids = {c.component_id for c in components}
        for component in components:
            for dep in component.dependencies:
                refs = dep.get("component_refs") or []
                dep["component_refs"] = [
                    remap.get(ref, ref) for ref in refs
                    if remap.get(ref, ref) != component.component_id
                ]
        for hint in result.assembly_hints or []:
            if not isinstance(hint, dict):
                continue
            refs = hint.get("component_ids") or []
            hint["component_ids"] = _ordered_unique(
                remap.get(ref, ref) for ref in refs if remap.get(ref, ref) in child_ids
            )

    # ------------------------------------------------------------------

    def _refine_component(
        self,
        component: ComponentRecord,
        eq_index: dict[str, dict],
        chains: list,
        report: RefinementReport,
    ) -> list[ComponentRecord]:
        groups, generic_steps = self._operation_groups(component, chains)

        if component.component_type not in _DERIVATION_TYPES or len(groups) < 2:
            # Nothing to split: make sure the component still has a single
            # main operation recorded and role-classified equations.
            self._finalize_single(component, eq_index, groups)
            return [component]

        # Generic steps never form their own component; fold them into the unit
        # whose equations they touch (else the last unit) so they survive in the
        # internal_flow without polluting the reusable-component catalogue.
        family_steps = {family: list(steps) for family, steps in groups.items()}
        self._absorb_generic_steps(family_steps, generic_steps)

        children: list[ComponentRecord] = []
        for idx, (family, steps) in enumerate(family_steps.items(), start=1):
            child = self._build_child(component, idx, family, steps, eq_index)
            children.append(child)

        # Chain the children sequentially so the derivation order is preserved.
        for prev, nxt in zip(children, children[1:]):
            nxt.dependencies.append({
                "dependency_type": "depends_on",
                "component_refs": [prev.component_id],
                "reason": "Sequential theory-unit dependency from ComponentRefiner.",
            })

        report.split_actions.append(RefinementAction(
            parent_component_id=component.component_id,
            parent_label=component.label,
            child_component_ids=[c.component_id for c in children],
            operations=[c.operation for c in children],
            reason=(
                f"Component bundled {len(family_steps)} distinct reusable theory "
                "units; split into one component per theory-operation family "
                "(issue #308)."
            ),
        ))
        return children

    def _operation_groups(self, component: ComponentRecord, chains: list) -> tuple[dict, list]:
        """Group derivation steps relevant to the component by theory-unit family.

        Returns ``(family_groups, generic_steps)``. Steps are bucketed by their
        operation *family* (issue #308) so several equation steps of the same
        family form one reusable unit; order is preserved (first-seen family
        first). Generic-operation steps are collected separately and never become
        their own family.
        """
        component_eqs = set(_all_equation_ids(component))
        linked_ids = set(component.linked_derivation_ids or [])
        groups: dict[str, list] = {}
        generic_steps: list = []
        for chain in chains:
            chain_id = getattr(chain, "derivation_id", None)
            linked_components = set(getattr(chain, "linked_component_ids", []) or [])
            chain_linked = (
                (chain_id and chain_id in linked_ids)
                or (component.component_id in linked_components)
            )
            for step in getattr(chain, "steps", []) or []:
                step_eqs = set(_step_field(step, "input_equation_ids")) | set(
                    _step_field(step, "output_equation_ids")
                )
                if not chain_linked and not (step_eqs & component_eqs):
                    continue
                operation = str(getattr(step, "operation", "") or "").strip()
                if not operation:
                    continue
                if _is_generic_operation(operation):
                    generic_steps.append(step)
                    continue
                family = _operation_family(operation)
                groups.setdefault(family, []).append(step)
        return groups, generic_steps

    @staticmethod
    def _absorb_generic_steps(family_steps: dict, generic_steps: list) -> None:
        """Fold generic steps into the family unit they share equations with."""
        if not generic_steps or not family_steps:
            return
        family_eqs = {
            family: {
                eid
                for step in steps
                for eid in _step_field(step, "input_equation_ids") + _step_field(step, "output_equation_ids")
            }
            for family, steps in family_steps.items()
        }
        families = list(family_steps.keys())
        for step in generic_steps:
            eqs = set(
                _step_field(step, "input_equation_ids")
                + _step_field(step, "output_equation_ids")
            )
            target = next(
                (family for family in families if eqs & family_eqs[family]),
                families[-1],
            )
            family_steps[target].append(step)

    def _build_child(
        self,
        parent: ComponentRecord,
        idx: int,
        operation: str,
        steps: list,
        eq_index: dict[str, dict],
    ) -> ComponentRecord:
        input_eqs = _ordered_unique(
            eid for step in steps for eid in _step_field(step, "input_equation_ids")
        )
        output_eqs = _ordered_unique(
            eid for step in steps for eid in _step_field(step, "output_equation_ids")
        )
        eliminated = _ordered_unique(
            s for step in steps for s in _step_field(step, "eliminated_symbols")
        )
        retained = _ordered_unique(
            s for step in steps for s in _step_field(step, "retained_symbols")
        )
        all_eqs = _ordered_unique(input_eqs + output_eqs)

        classification = self._classifier.classify(
            all_eqs,
            equation_index=eq_index,
            derivation_steps=steps,
            declared_output_ids=output_eqs,
        )

        family = _operation_family(operation)
        # Label / summary describe the theory object (the parent's theoretical
        # subject) qualified by the reusable unit's family — not a bare operation
        # name like "Transform" / "Relate" (issue #308).
        label = _theory_unit_label(family, parent, eliminated or retained)
        summary = _theory_unit_summary(label, family, parent, len(steps))

        internal_flow = _build_internal_flow(steps, family)
        review_required = bool(classification.review_required_equation_ids) or any(
            str(getattr(s, "review_status", "")) not in ("", "auto_accepted") for s in steps
        )
        review_status = "teacher_review_required" if review_required else parent.review_status

        evidence_refs = copy.deepcopy(parent.evidence_refs or {})
        evidence_refs["equation_ids"] = list(all_eqs)

        return ComponentRecord(
            component_id=f"{parent.component_id}__op{idx}",
            component_type=parent.component_type,
            label=label,
            summary=summary,
            inputs=[{"name": eid, "equation_ids": [eid]} for eid in input_eqs] or list(parent.inputs),
            outputs=[{"name": eid, "equation_ids": [eid]} for eid in output_eqs] or list(parent.outputs),
            preconditions=list(parent.preconditions),
            cautions=list(parent.cautions),
            dependencies=[],
            evidence_refs=evidence_refs,
            reason=(
                f"Isolated single theoretical operation '{operation}' from parent "
                f"component {parent.component_id} (issue #300)."
            ),
            confidence=parent.confidence,
            review_notes=list(parent.review_notes),
            internal_flow=internal_flow,
            linked_claim_ids=list(parent.linked_claim_ids),
            linked_equation_ids=list(all_eqs),
            linked_evidence_ids=list(parent.linked_evidence_ids),
            linked_derivation_ids=list(parent.linked_derivation_ids),
            linked_dsl_node_ids=list(parent.linked_dsl_node_ids),
            linked_dsl_edge_ids=list(parent.linked_dsl_edge_ids),
            input_equation_ids=classification.input_equation_ids,
            intermediate_equation_ids=classification.intermediate_equation_ids,
            output_equation_ids=classification.output_equation_ids,
            constraint_equation_ids=classification.constraint_equation_ids,
            definition_equation_ids=classification.definition_equation_ids,
            review_required_equation_ids=classification.review_required_equation_ids,
            eliminated_symbols=eliminated,
            retained_symbols=retained,
            equation_confidence_summary=dict(parent.equation_confidence_summary or {}),
            review_status=review_status,
            teaching_takeaway=parent.teaching_takeaway,
            source_scope=copy.deepcopy(parent.source_scope or {}),
            assumptions=list(parent.assumptions),
            approximations=list(parent.approximations),
            operation=family,
        )

    def _finalize_single(
        self,
        component: ComponentRecord,
        eq_index: dict[str, dict],
        groups: dict,
    ) -> None:
        if not component.operation:
            if len(groups) == 1:
                component.operation = _operation_family(next(iter(groups)))
            else:
                component.operation = _infer_operation_from_text(component)

        all_eqs = _all_equation_ids(component)
        if not all_eqs:
            return
        steps = [s for steps in groups.values() for s in steps]
        classification = self._classifier.classify(
            all_eqs,
            equation_index=eq_index,
            derivation_steps=steps,
            declared_output_ids=component.output_equation_ids,
        )
        component.input_equation_ids = _ordered_unique(
            list(component.input_equation_ids) + classification.input_equation_ids
        )
        component.intermediate_equation_ids = _ordered_unique(
            list(component.intermediate_equation_ids) + classification.intermediate_equation_ids
        )
        component.output_equation_ids = _ordered_unique(
            list(component.output_equation_ids) + classification.output_equation_ids
        )
        component.definition_equation_ids = _ordered_unique(
            list(component.definition_equation_ids) + classification.definition_equation_ids
        )
        component.constraint_equation_ids = _ordered_unique(
            list(component.constraint_equation_ids) + classification.constraint_equation_ids
        )
        component.review_required_equation_ids = _ordered_unique(
            list(component.review_required_equation_ids) + classification.review_required_equation_ids
        )
        if component.review_required_equation_ids and component.review_status == "auto_accepted":
            component.review_status = "teacher_review_required"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Map derivation operation strings to a coarse theory-operation family.
_OPERATION_FAMILY_RULES = [
    ("lineariz", "linearize"),
    ("eliminat", "eliminate"),
    ("substitut", "substitute"),
    ("solv", "solve"),
    ("consistency", "derive"),
    ("deriv", "derive"),
    ("approximat", "approximate"),
    ("constrain", "constrain"),
    ("constraint", "constrain"),
    ("criterion", "constrain"),
    ("diagnos", "diagnose"),
    ("forecast", "forecast"),
    ("compar", "compare"),
    ("parameter", "parameterize"),
    ("observable", "observable_definition"),
    ("normaliz", "normalize"),
    ("assum", "assume"),
    ("defin", "define"),
]


def _operation_family(operation: str) -> str:
    text = str(operation or "").lower()
    for needle, family in _OPERATION_FAMILY_RULES:
        if needle in text:
            return family
    return text or "derive"


def _infer_operation_from_text(component: ComponentRecord) -> str:
    text = " ".join([
        str(component.label or ""),
        str(component.summary or ""),
        str(component.reason or ""),
    ]).lower()
    for needle, family in _OPERATION_FAMILY_RULES:
        if needle in text:
            return family
    return ""


def _humanize_operation(operation: str, parent_label: str) -> str:
    words = str(operation or "").replace("_", " ").strip()
    if not words:
        return parent_label
    return words[:1].upper() + words[1:]


def _is_generic_operation(operation: str) -> bool:
    """True if an operation is too generic to name a reusable theory unit."""
    return str(operation or "").strip().lower() in _GENERIC_OPERATIONS


def _theory_unit_label(family: str, parent: ComponentRecord, symbols: list[str]) -> str:
    """Theory-object-centric label for a refined reusable unit (issue #308).

    The label leads with the parent's theoretical subject (its label) and
    qualifies it with the reusable unit's family, so it never collapses to a bare
    operation name. When the unit carries distinguishing symbols they are used as
    the more specific theory object.
    """
    verb = _humanize_operation(family, "")
    object_phrase = ", ".join(symbols[:3]) if symbols else str(parent.label or "").strip()
    if not object_phrase:
        return verb or "Theory unit"
    if not verb:
        return object_phrase
    return f"{verb}: {object_phrase}"


def _theory_unit_summary(label: str, family: str, parent: ComponentRecord, step_count: int) -> str:
    subject = str(parent.label or "").strip() or "the parent theory unit"
    return (
        f"Reusable theory unit ({family}) within {subject}; "
        f"covers {step_count} equation step(s)."
    )


def _build_internal_flow(steps: list, family: str) -> list[dict]:
    flow: list[dict] = []
    seen: set[tuple] = set()
    for step in steps:
        relation = str(getattr(step, "operation", "") or family)
        inputs = _step_field(step, "input_equation_ids")
        outputs = _step_field(step, "output_equation_ids")
        for src in inputs:
            for dst in outputs:
                if src == dst:
                    continue
                key = (src, relation, dst)
                if key in seen:
                    continue
                seen.add(key)
                flow.append({"from": src, "relation": relation, "to": dst})
    return flow


def _equation_index(llm_input: ComponentAssemblyLLMInput | None) -> dict[str, dict]:
    index: dict[str, dict] = {}
    if not llm_input:
        return index
    for eq in list(llm_input.equations or []) + list(llm_input.available_equations or []):
        eq_id = str(eq.get("equation_id") or "")
        if not eq_id:
            continue
        merged = dict(index.get(eq_id, {}))
        merged.update(eq)
        index[eq_id] = merged
    return index


def _all_equation_ids(component: ComponentRecord) -> list[str]:
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
