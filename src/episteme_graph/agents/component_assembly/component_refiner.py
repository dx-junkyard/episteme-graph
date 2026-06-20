"""ComponentRefiner — split summary-level components into theory operations.

Issue #300: the LLM tends to produce components that follow the explanation
structure of a paper (section / paragraph / summary units). Such components are
too coarse to be reusable theory parts. A single coarse component (for example a
"solve and eliminate the nuisance parameters" unit) often bundles several
distinct theoretical operations (linearization, parameter solving/elimination,
residual / final-constraint derivations).

``ComponentRefiner`` is a deterministic (non-LLM) post-processing pass run after
the initial components are assembled. It uses the derivation chains — which carry
the real theory structure (one step = one operation, with input/output equations,
eliminated/retained symbols) — to detect over-large components and split them so
that:

    1 component = 1 reusable theory unit

A *reusable theory unit* is a single responsibility unit (definition, model,
observable basis, equation system, derivation, constraint, …).
Generic operations (``transform`` / ``relate`` / ``connect`` / ``support`` /
empty) never spawn a standalone child — they are kept inside the nearest unit's
``internal_flow`` so the catalogue of reusable components stays at the
theory-unit altitude. Component labels/summaries describe the theory object, not
a bare operation name.

The boundary is decided by *theory structure*, not explanation style:
inputs change, outputs change, the operation family changes, symbols change.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field

from ..theory_operations import operation_family as _generic_operation_family
from .equation_role_classifier import EquationRoleClassifier
from .responsibility import CANONICAL_RESPONSIBILITY_TYPES, canonical_responsibility_type
from .schema import (
    ComponentAssemblyLLMInput,
    ComponentAssemblyResult,
    ComponentRecord,
)
from .split_recommendation import (
    normalize_split_recommendation,
    split_is_required,
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

RESPONSIBILITY_TYPES = CANONICAL_RESPONSIBILITY_TYPES

SIZE_THRESHOLDS = {
    "max_linked_equations_soft": 8,
    "max_linked_equations_hard": 15,
    "max_internal_flow_steps_soft": 5,
    "max_component_types_per_component": 1,
    "max_distinct_responsibility_labels": 1,
}


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
    oversized_components: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "split_count": len(self.split_actions),
            "split_actions": [a.to_dict() for a in self.split_actions],
            "warnings": list(self.warnings),
            "oversized_components": list(self.oversized_components),
            "component_size_thresholds": dict(SIZE_THRESHOLDS),
        }


@dataclass
class TheoryOperationCandidate:
    """Derivation-backed operation unit before it becomes a component.

    Equations stay here as supporting detail. ComponentAssembly should expose
    the operation as the reusable unit, not a long list of equation-to-equation
    dependencies.
    """

    operation: str
    steps: list
    input_equation_ids: list[str]
    output_equation_ids: list[str]
    eliminated_symbols: list[str]
    retained_symbols: list[str]


class ComponentRefiner:
    def __init__(self) -> None:
        self._classifier = EquationRoleClassifier()
        # Per-component refinement trace populated during a single refine() call
        # (issue #324). Reset at the start of refine().
        self._traces: dict[str, dict] = {}

    def refine(
        self,
        result: ComponentAssemblyResult,
        llm_input: ComponentAssemblyLLMInput | None = None,
        derivations=None,
    ) -> ComponentAssemblyResult:
        eq_index = _equation_index(llm_input)
        claim_index = _claim_index(llm_input)
        chains = list(getattr(derivations, "chains", []) or [])
        report = RefinementReport()
        self._traces = {}

        originals = {c.component_id: c for c in result.components}
        original_order = [c.component_id for c in result.components]

        refined: list[ComponentRecord] = []
        # Map each split parent id → the id of its first child so that references
        # held elsewhere (dependencies, assembly hints) keep resolving.
        remap: dict[str, str] = {}
        # original_id → ordered list of child component ids (issue #324).
        child_map: dict[str, list[str]] = {}
        for component in result.components:
            children = self._refine_component(
                component, eq_index, chains, report, claim_index
            )
            child_map[component.component_id] = [c.component_id for c in children]
            if children and children[0].component_id != component.component_id:
                remap[component.component_id] = children[0].component_id
            refined.extend(children)

        if remap:
            self._remap_references(refined, result, remap)

        result.components = refined

        # Step 3 (#324): recompute teaching granularity for every refined component.
        for component in refined:
            component.teaching_granularity = _teaching_granularity(component, eq_index)

        existing = result.refinement_report if isinstance(result.refinement_report, dict) else {}
        merged = dict(existing)
        merged.update(report.to_dict())
        result.refinement_report = merged

        result.component_refinement = self._build_refinement_outputs(
            original_order=original_order,
            originals=originals,
            child_map=child_map,
            refined=refined,
            remap=remap,
        )
        return result

    # ------------------------------------------------------------------
    # Step 3 formal output contract (issue #324)
    # ------------------------------------------------------------------

    def _build_refinement_outputs(
        self,
        *,
        original_order: list[str],
        originals: dict[str, ComponentRecord],
        child_map: dict[str, list[str]],
        refined: list[ComponentRecord],
        remap: dict[str, str],
    ) -> dict:
        refined_by_id = {c.component_id: c for c in refined}
        records: list[dict] = []
        id_mapping: list[dict] = []
        split_components: list[str] = []
        unchanged_components: list[str] = []
        failed_refinements: list[str] = []
        review_required_refinements: list[str] = []
        unassigned_links: list[dict] = []
        teaching_warnings: list[dict] = []

        for original_id in original_order:
            parent = originals[original_id]
            child_ids = child_map.get(original_id, [])
            trace = self._traces.get(original_id, {})
            children = [refined_by_id[cid] for cid in child_ids if cid in refined_by_id]

            is_split = len(child_ids) > 1
            is_unchanged = len(child_ids) == 1 and child_ids[0] == original_id
            could_not_split = bool(trace.get("could_not_split"))

            child_review_required = any(
                _is_review_required_status(c.review_status) for c in children
            )
            review_reasons = list(trace.get("review_reasons") or [])
            if child_review_required and "child_requires_review" not in review_reasons:
                review_reasons.append("child_requires_review")

            if not child_ids:
                refinement_status = "failed"
                mapping_type = "failed"
                failed_refinements.append(original_id)
            elif is_split:
                refinement_status = "split"
                mapping_type = "one_to_many"
                split_components.append(original_id)
            elif could_not_split:
                refinement_status = "review_required"
                mapping_type = "unchanged"
                if not review_reasons:
                    review_reasons.append("insufficient_split_plan")
                review_required_refinements.append(original_id)
            elif is_unchanged:
                refinement_status = "unchanged"
                mapping_type = "unchanged"
                unchanged_components.append(original_id)
            else:
                # Single rebuilt child with a new id (e.g. equation bundle).
                refinement_status = "split"
                mapping_type = "one_to_one"
                split_components.append(original_id)

            if child_review_required and original_id not in review_required_refinements:
                review_required_refinements.append(original_id)

            provenance = trace.get("provenance") or {
                "source_claim_ids": list(parent.linked_claim_ids or []),
                "source_equation_ids": _all_equation_ids(parent),
                "source_evidence_ids": list(parent.linked_evidence_ids or []),
                "source_derivation_ids": list(parent.linked_derivation_ids or []),
            }

            records.append({
                "original_component_id": original_id,
                "refinement_status": refinement_status,
                "split_required": split_is_required(parent.split_recommendation),
                "split_into": list(child_ids),
                "split_reason": list(
                    trace.get("split_reasons")
                    or (parent.split_recommendation or {}).get("reasons")
                    or []
                ),
                "split_method": str(trace.get("method") or "rule_based"),
                "split_candidate_mapping": list(trace.get("split_candidate_mapping") or []),
                "provenance": provenance,
                "review_reasons": review_reasons,
            })
            id_mapping.append({
                "original_component_id": original_id,
                "new_component_ids": list(child_ids),
                "mapping_type": mapping_type,
            })

            for entry in trace.get("unassigned") or []:
                unassigned_links.append({"original_component_id": original_id, **entry})

        for component in refined:
            granularity = component.teaching_granularity or {}
            if granularity.get("split_if_too_dense") or not granularity.get(
                "teachable_as_single_unit", True
            ):
                teaching_warnings.append({
                    "component_id": component.component_id,
                    "estimated_slide_count": granularity.get("estimated_slide_count"),
                    "teaching_takeaway_quality": granularity.get("teaching_takeaway_quality"),
                    "reason": "refined component is still too dense to teach as a single unit",
                })

        graph_updates = _component_graph_updates(
            originals=originals,
            refined_by_id=refined_by_id,
            child_map=child_map,
            remap=remap,
        )

        # A component whose split was required (#417) must be accounted for: it is
        # either split, failed, or explicitly review_required. Anything left as a
        # silent "unchanged" while still flagged ``required: true`` is a contract
        # violation and is surfaced so it cannot reach publish unprocessed.
        processed = (
            set(split_components)
            | set(failed_refinements)
            | set(review_required_refinements)
        )
        unprocessed_split_required = [
            original_id
            for original_id in original_order
            if split_is_required(originals[original_id].split_recommendation)
            and original_id not in processed
        ]

        refinement_validation = {
            "split_components": split_components,
            "unchanged_components": unchanged_components,
            "failed_refinements": failed_refinements,
            "review_required_refinements": _ordered_unique(review_required_refinements),
            "unprocessed_split_required": unprocessed_split_required,
            "unassigned_links": unassigned_links,
            "dangling_component_refs": graph_updates["unresolved_edges"],
            "teaching_granularity_warnings": teaching_warnings,
        }

        return {
            "refined_components": [
                {
                    "component_id": c.component_id,
                    "responsibility_type": c.responsibility_type or c.operation,
                    "review_status": c.review_status,
                    "teaching_granularity": dict(c.teaching_granularity or {}),
                }
                for c in refined
            ],
            "component_refinement_records": records,
            "component_id_mapping": id_mapping,
            "component_graph_updates": graph_updates,
            "refinement_validation": refinement_validation,
        }

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
        claim_index: dict[str, dict] | None = None,
    ) -> list[ComponentRecord]:
        claim_index = claim_index or {}
        split = component.split_recommendation if isinstance(component.split_recommendation, dict) else {}
        if split_is_required(split):
            report.oversized_components.append({
                "component_id": component.component_id,
                "label": component.label,
                "reasons": list(split.get("reasons") or []),
                "split_required": True,
                "suggested_components": list(split.get("suggested_components") or []),
            })
            if component.review_status in ("", "auto_accepted", "source_backed"):
                component.review_status = "review_required"
            note = "ComponentRefiner consumed split recommendation"
            if note not in component.review_notes:
                component.review_notes.append(note)

        responsibility_children = self._responsibility_children(component, eq_index, chains, report)
        if responsibility_children:
            return responsibility_children

        # Step 3 priority order (#324): consume Step 1 ``suggested_split`` as the
        # primary split plan before falling back to derivation-driven splitting.
        suggested_children = self._suggested_split_children(
            component, split, eq_index, claim_index, report
        )
        if suggested_children:
            return suggested_children

        groups, generic_steps = self._operation_groups(component, chains)

        if component.component_type not in _DERIVATION_TYPES:
            # Nothing to split: make sure the component still has a single
            # main operation recorded and role-classified equations.
            self._finalize_single(component, eq_index, groups)
            self._mark_could_not_split(component, split)
            return [component]

        # Generic steps never form their own component; fold them into the unit
        # whose equations they touch (else the last unit) so they survive in the
        # internal_flow without polluting the reusable-component catalogue.
        family_steps = {family: list(steps) for family, steps in groups.items()}
        self._absorb_generic_steps(family_steps, generic_steps)
        candidates = self._operation_candidates(family_steps)

        if len(candidates) < 2:
            self._finalize_single(component, eq_index, groups)
            if candidates and _is_equation_dependency_bundle(component):
                rebuilt = self._build_component_from_candidate(
                    parent=component,
                    component_id=component.component_id,
                    candidate=candidates[0],
                    eq_index=eq_index,
                    dependencies=list(component.dependencies),
                    reason=(
                        "Rebuilt equation-heavy component around one theory-operation "
                        "candidate; equations are retained as supporting detail."
                    ),
                )
                report.warnings.append(
                    f"{component.component_id} was normalized from an equation dependency "
                    "bundle into a single theory-operation component."
                )
                return [rebuilt]
            self._mark_could_not_split(component, split)
            return [component]

        children: list[ComponentRecord] = []
        for idx, candidate in enumerate(candidates, start=1):
            child = self._build_component_from_candidate(
                parent=component,
                component_id=f"{component.component_id}__op{idx}",
                candidate=candidate,
                eq_index=eq_index,
                dependencies=[],
                reason=(
                    f"Isolated single theoretical operation '{candidate.operation}' "
                    f"from parent component {component.component_id}."
                ),
            )
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
                "(issue #308/#319)."
            ),
        ))
        return children

    def _mark_could_not_split(self, component: ComponentRecord, split: dict) -> None:
        """Record that a ``split_required`` component could not be decomposed.

        Step 3 (#324) rule 3: when the suggested plan is insufficient and no
        deterministic split is possible, the component is kept unchanged but the
        refinement is flagged ``review_required`` rather than silently accepted.
        """
        if not split_is_required(split):
            return
        trace = self._traces.setdefault(component.component_id, {})
        trace["could_not_split"] = True
        trace.setdefault("method", "rule_based")
        reasons = trace.setdefault("review_reasons", [])
        if "insufficient_split_plan" not in reasons:
            reasons.append("insufficient_split_plan")

    def _suggested_split_children(
        self,
        component: ComponentRecord,
        split: dict,
        eq_index: dict[str, dict],
        claim_index: dict[str, dict],
        report: RefinementReport,
    ) -> list[ComponentRecord]:
        """Split a component using Step 1's ``suggested_split`` plan (#324).

        Links (claims / equations / evidence / derivations) are *reassigned* to
        the most relevant child rather than copied wholesale; concepts are
        recomputed per child; low-confidence equations and composite claims
        downgrade the affected child to ``review_required``. Anything that cannot
        be assigned confidently is recorded as an unassigned link instead of being
        dropped.
        """
        if not split_is_required(split):
            return []
        suggested = [s for s in (split.get("suggested_components") or []) if isinstance(s, dict)]
        # Distinct primary responsibilities are required to justify a split.
        responsibilities = _ordered_unique(
            canonical_responsibility_type(s.get("responsibility_type"))
            for s in suggested
            if canonical_responsibility_type(s.get("responsibility_type")) in RESPONSIBILITY_TYPES
        )
        if len(suggested) < 2 or len(responsibilities) < 2:
            return []

        plan, unassigned = _redistribute_links(component, suggested, eq_index, claim_index)
        assignable = [spec for spec in plan if _spec_has_payload(spec)]
        if len(assignable) < 2:
            return []

        children: list[ComponentRecord] = []
        candidate_mapping: list[dict] = []
        for idx, spec in enumerate(plan, start=1):
            child_id = f"{component.component_id}__r{idx}"
            child = _build_suggested_component(
                component, child_id, spec, eq_index, claim_index
            )
            children.append(child)
            candidate_mapping.append({
                "suggested_component_name": spec.get("name") or child.label,
                "new_component_id": child_id,
                "responsibility_type": spec.get("responsibility_type"),
                "assigned_claim_ids": list(spec.get("claim_ids") or []),
                "assigned_equation_ids": list(spec.get("equation_ids") or []),
                "assigned_evidence_ids": list(spec.get("evidence_ids") or []),
                "assigned_derivation_ids": list(spec.get("derivation_ids") or []),
                "assigned_concepts": list(child.concepts or []),
            })

        # introduced vs reused depends on cross-child ordering (issue #8 / #324).
        _assign_introduced_reused_children(children, component)

        # Sequential dependency so the original derivation/explanation order is kept.
        for prev, nxt in zip(children, children[1:]):
            nxt.dependencies.append({
                "dependency_type": "depends_on",
                "component_refs": [prev.component_id],
                "reason": "Sequential responsibility dependency from ComponentRefiner (#324).",
            })

        review_reasons: list[str] = []
        for child in children:
            if _is_review_required_status(child.review_status):
                for note in child.review_notes:
                    if note not in review_reasons:
                        review_reasons.append(note)

        self._traces[component.component_id] = {
            "method": "rule_based",
            "split_reasons": list(split.get("reasons") or []),
            "split_candidate_mapping": candidate_mapping,
            "unassigned": unassigned,
            "review_reasons": review_reasons,
            "provenance": {
                "source_claim_ids": list(component.linked_claim_ids or []),
                "source_equation_ids": _all_equation_ids(component),
                "source_evidence_ids": list(component.linked_evidence_ids or []),
                "source_derivation_ids": list(component.linked_derivation_ids or []),
            },
        }

        report.split_actions.append(RefinementAction(
            parent_component_id=component.component_id,
            parent_label=component.label,
            child_component_ids=[c.component_id for c in children],
            operations=[c.responsibility_type or c.operation for c in children],
            reason=(
                "Consumed Step 1 suggested_split plan; reassigned links and "
                "recomputed concepts into one single-responsibility component "
                "per suggested child (#324)."
            ),
        ))
        return children

    @staticmethod
    def _operation_candidates(family_steps: dict) -> list[TheoryOperationCandidate]:
        candidates: list[TheoryOperationCandidate] = []
        for family, steps in family_steps.items():
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
            candidates.append(TheoryOperationCandidate(
                operation=family,
                steps=list(steps),
                input_equation_ids=input_eqs,
                output_equation_ids=output_eqs,
                eliminated_symbols=eliminated,
                retained_symbols=retained,
            ))
        return candidates

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
                family = _operation_group_key(operation)
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

    def _build_component_from_candidate(
        self,
        parent: ComponentRecord,
        component_id: str,
        candidate: TheoryOperationCandidate,
        eq_index: dict[str, dict],
        dependencies: list[dict],
        reason: str,
    ) -> ComponentRecord:
        steps = candidate.steps
        input_eqs = list(candidate.input_equation_ids)
        output_eqs = list(candidate.output_equation_ids)
        eliminated = list(candidate.eliminated_symbols)
        retained = list(candidate.retained_symbols)
        all_eqs = _ordered_unique(input_eqs + output_eqs)

        classification = self._classifier.classify(
            all_eqs,
            equation_index=eq_index,
            derivation_steps=steps,
            declared_output_ids=output_eqs,
        )

        raw_operation = candidate.operation
        # ``family`` is the verb-level grouping label used for labels/io/flow;
        # ``broad_family`` is the stable, domain-neutral operation family exposed
        # on the component (and therefore the operation graph) per #396 / #398.
        family = _operation_family(raw_operation)
        broad_family = _generic_operation_family(raw_operation)
        # Label / summary describe the theory object (the parent's theoretical
        # subject) qualified by the reusable unit's family — not a bare operation
        # name like "Transform" / "Relate" (issue #308).
        label = _theory_unit_label(raw_operation, parent, eliminated or retained)
        summary = _theory_unit_summary(label, broad_family, parent, len(steps))

        internal_flow = _build_internal_flow(steps, family)
        review_required = bool(classification.review_required_equation_ids) or any(
            str(getattr(s, "review_status", "")) not in ("", "auto_accepted") for s in steps
        )
        review_status = "teacher_review_required" if review_required else parent.review_status
        blocked_eqs = _blocked_equation_ids(all_eqs, eq_index)
        confidence_gate = _confidence_gate(blocked_eqs)
        if blocked_eqs:
            review_status = "review_required"

        evidence_refs = copy.deepcopy(parent.evidence_refs or {})
        evidence_refs["equation_ids"] = list(all_eqs)
        review_notes = list(parent.review_notes)
        if blocked_eqs and confidence_gate["blocked_reason"] not in review_notes:
            review_notes.append(confidence_gate["blocked_reason"])

        return ComponentRecord(
            component_id=component_id,
            component_type=parent.component_type,
            label=label,
            summary=summary,
            inputs=_operation_io_items("input", family, input_eqs, parent.inputs),
            outputs=_operation_io_items("output", family, output_eqs, parent.outputs),
            preconditions=list(parent.preconditions),
            cautions=list(parent.cautions),
            dependencies=copy.deepcopy(dependencies or []),
            evidence_refs=evidence_refs,
            reason=reason,
            confidence=parent.confidence,
            review_notes=review_notes,
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
            review_required_equation_ids=_ordered_unique(
                classification.review_required_equation_ids + blocked_eqs
            ),
            eliminated_symbols=eliminated,
            retained_symbols=retained,
            equation_confidence_summary=dict(parent.equation_confidence_summary or {}),
            confidence_gate=confidence_gate,
            review_status=review_status,
            teaching_takeaway=parent.teaching_takeaway,
            source_scope=copy.deepcopy(parent.source_scope or {}),
            assumptions=list(parent.assumptions),
            approximations=list(parent.approximations),
            constraints=list(parent.constraints),
            invalid_conditions=list(parent.invalid_conditions),
            responsibility_type=_responsibility_for_operation(raw_operation),
            primary_operation=broad_family,
            secondary_operations=_secondary_operations(steps, broad_family),
            split_recommendation=_no_split_recommendation(),
            **_child_support_fields(parent, _responsibility_for_operation(raw_operation)),
            operation=broad_family,
        )

    def _responsibility_children(
        self,
        component: ComponentRecord,
        eq_index: dict[str, dict],
        chains: list,
        report: RefinementReport,
    ) -> list[ComponentRecord]:
        """Deprecated paper-specific split path (issues #395 / #396 / #397).

        The previous implementation split a component using hard-coded
        bias→observable buckets, which locally optimised the pipeline for one
        paper family. Splitting is now done generically by the granularity
        analyzer's ``suggested_split`` plan (``_suggested_split_children``) and by
        domain-neutral operation-family grouping (``_operation_groups``), so this
        path is disabled.
        """
        return []

    def _finalize_single(
        self,
        component: ComponentRecord,
        eq_index: dict[str, dict],
        groups: dict,
    ) -> None:
        if not component.operation:
            if len(groups) == 1:
                component.operation = _generic_operation_family(next(iter(groups)))
            else:
                component.operation = _generic_operation_family(_infer_operation_from_text(component))
        if not component.responsibility_type:
            component.responsibility_type = _responsibility_for_operation(component.operation)
        if not component.primary_operation:
            component.primary_operation = component.operation
        if not component.split_recommendation:
            component.split_recommendation = _no_split_recommendation()

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
        blocked_eqs = _blocked_equation_ids(all_eqs, eq_index)
        component.confidence_gate = _confidence_gate(blocked_eqs)
        if blocked_eqs:
            component.review_required_equation_ids = _ordered_unique(
                list(component.review_required_equation_ids) + blocked_eqs
            )
            reason = component.confidence_gate["blocked_reason"]
            if reason and reason not in component.review_notes:
                component.review_notes.append(reason)
            component.review_status = "review_required"
            component.publish_ready = False
        elif component.review_required_equation_ids and component.review_status == "auto_accepted":
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


def _operation_group_key(operation: str) -> str:
    """Domain-neutral operation group key (issues #395 / #396).

    Grouping/splitting use a generic operation FAMILY only. Paper-specific
    operation keys (observable / parameter / method names) are never produced by
    core logic; any such terminology must come from a cartridge subtype, not from
    hard-coded branches here.
    """
    return _generic_operation_family(operation)


# Generic operation family → responsibility type (issues #396 / #397). Mirrors
# the granularity analyzer mapping so refined components expose a responsibility
# even when their operation is a broad family name (e.g. transform_representation).
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


def _responsibility_for_operation(operation: str) -> str:
    """Map a raw operation string OR a broad operation family to a responsibility.

    Works for both the raw step operation text ("define the relation") and the
    generic family exposed on refined components ("define_relation") so a refined
    ``ComponentRecord`` never ends up with an empty ``responsibility_type``
    (issue #396).
    """
    text = str(operation or "").lower()
    # Finer raw-text distinction first (only meaningful for raw operation text).
    if "observable" in text:
        return "observable_basis"
    broad = _generic_operation_family(text)
    mapped = _FAMILY_TO_RESPONSIBILITY.get(broad)
    if mapped:
        return mapped
    return canonical_responsibility_type("")


def _responsibility_labels(component: ComponentRecord, eq_index: dict[str, dict]) -> set[str]:
    """Domain-neutral responsibility labels from generic operation families (#396).

    Derived from the component's own operation/responsibility fields and equation
    *roles* — never from paper-specific observable/parameter terms.
    """
    labels: set[str] = set()
    for value in (component.responsibility_type, component.operation, component.primary_operation):
        if value:
            labels.add(canonical_responsibility_type(_responsibility_for_operation(str(value))))
    for op in component.secondary_operations or []:
        labels.add(canonical_responsibility_type(_responsibility_for_operation(str(op))))
    for eq_id in _all_equation_ids(component):
        role = str((eq_index.get(eq_id) or {}).get("role") or (eq_index.get(eq_id) or {}).get("equation_type") or "").lower()
        if "definition" in role:
            labels.add("definition")
        elif "constraint" in role:
            labels.add("constraint")
        elif role in {"relation", "transformation"}:
            labels.add("equation_system")
        elif "result" in role:
            labels.add("derivation")
    labels.discard("")
    return labels


def _responsibility_slices(component: ComponentRecord, eq_index: dict[str, dict]) -> list[dict]:
    """Removed paper-specific responsibility slicing (issues #395 / #397).

    This previously bucketed equations by domain-specific terms (observable and
    parameter names of one paper family). Splitting is now generic; this path is
    disabled and returns no slices.
    """
    return []


def _build_responsibility_component(
    parent: ComponentRecord,
    component_id: str,
    spec: dict,
    eq_index: dict[str, dict],
) -> ComponentRecord:
    eq_ids = list(spec.get("equation_ids") or [])
    responsibility_type = str(spec.get("responsibility_type") or "")
    operation = str(spec.get("operation") or "")
    review_required = _review_required_equation_ids(eq_ids, eq_index)
    review_status = "review_required" if review_required else _source_backed_status(parent.review_status)
    evidence_refs = copy.deepcopy(parent.evidence_refs or {})
    evidence_refs["equation_ids"] = eq_ids
    return ComponentRecord(
        component_id=component_id,
        component_type=parent.component_type,
        label=str(spec.get("label") or parent.label),
        summary=_responsibility_summary(spec, parent),
        inputs=_responsibility_io("input", spec, parent),
        outputs=_responsibility_io("output", spec, parent),
        preconditions=list(parent.preconditions),
        cautions=list(parent.cautions),
        dependencies=[],
        evidence_refs=evidence_refs,
        reason=f"Split from oversized component {parent.component_id} by responsibility.",
        confidence=parent.confidence,
        review_notes=list(parent.review_notes),
        internal_flow=_responsibility_flow(eq_ids, operation),
        linked_claim_ids=list(parent.linked_claim_ids),
        linked_equation_ids=eq_ids,
        linked_evidence_ids=list(parent.linked_evidence_ids),
        linked_derivation_ids=list(parent.linked_derivation_ids),
        linked_dsl_node_ids=list(parent.linked_dsl_node_ids),
        linked_dsl_edge_ids=list(parent.linked_dsl_edge_ids),
        input_equation_ids=eq_ids[:-1] if len(eq_ids) > 1 and responsibility_type in {"equation_system", "derivation", "constraint"} else [],
        intermediate_equation_ids=[],
        output_equation_ids=eq_ids[-1:] if eq_ids and responsibility_type in {"equation_system", "derivation", "constraint", "observable_basis"} else [],
        constraint_equation_ids=[],
        definition_equation_ids=eq_ids if responsibility_type in {"definition", "model", "observation_model", "observable_basis"} else [],
        review_required_equation_ids=review_required,
        eliminated_symbols=list(parent.eliminated_symbols),
        retained_symbols=list(parent.retained_symbols),
        equation_confidence_summary=dict(parent.equation_confidence_summary or {}),
        confidence_gate=_confidence_gate(_blocked_equation_ids(eq_ids, eq_index)),
        review_status=review_status,
        teaching_takeaway=_responsibility_takeaway(spec),
        source_scope=copy.deepcopy(parent.source_scope or {}),
        assumptions=list(parent.assumptions),
        approximations=list(parent.approximations),
        constraints=list(parent.constraints),
        invalid_conditions=list(parent.invalid_conditions),
        responsibility_type=responsibility_type,
        primary_operation=_operation_family(operation),
        secondary_operations=[],
        split_recommendation=_no_split_recommendation(),
        **_child_support_fields(parent, responsibility_type),
        operation=operation,
    )


def _derivation_responsibility_slices(component: ComponentRecord, chains: list) -> list[dict]:
    linked_ids = set(component.linked_derivation_ids or [])
    component_eqs = set(_all_equation_ids(component))
    specs: list[dict] = []
    for chain in chains or []:
        chain_id = getattr(chain, "derivation_id", None)
        linked_components = set(getattr(chain, "linked_component_ids", []) or [])
        chain_linked = (
            (chain_id and chain_id in linked_ids)
            or (component.component_id in linked_components)
        )
        for step in getattr(chain, "steps", []) or []:
            step_eqs = _ordered_unique(
                _step_field(step, "input_equation_ids") + _step_field(step, "output_equation_ids")
            )
            if not chain_linked and not (set(step_eqs) & component_eqs):
                continue
            operation = _operation_group_key(str(getattr(step, "operation", "") or ""))
            label = _explicit_theory_unit_label(operation)
            if not label:
                continue
            specs.append({
                "label": label,
                "responsibility_type": _responsibility_for_operation(operation),
                "operation": _operation_family(operation),
                "equation_ids": step_eqs,
            })
    return specs


def _responsibility_summary(spec: dict, parent: ComponentRecord) -> str:
    label = str(spec.get("label") or parent.label)
    return f"{label} isolated as a reusable {spec.get('responsibility_type')} component."


def _child_support_fields(parent: ComponentRecord, responsibility_type: str) -> dict:
    role = _support_role_for_responsibility(responsibility_type)
    kind = {
        "derivation_core": "derivational",
        "result": "direct",
        "application": "application",
        "limitation": "caveat",
        "theory_base": "prerequisite",
    }.get(role, "indirect")
    distance = {
        "derivation_core": 0,
        "result": 0,
        "observable_bridge": 1,
        "application": 1,
        "limitation": 1,
        "theory_base": 2,
    }.get(role, getattr(parent, "support_distance_to_headline_claim", 1))
    return {
        "support_role": role or getattr(parent, "support_role", ""),
        "supports_claim_ids": list(getattr(parent, "supports_claim_ids", []) or getattr(parent, "linked_claim_ids", []) or []),
        "support_distance_to_headline_claim": distance,
        "support_kind": kind or getattr(parent, "support_kind", ""),
    }


def _support_role_for_responsibility(responsibility_type: str) -> str:
    if responsibility_type == "derivation":
        return "derivation_core"
    if responsibility_type == "constraint":
        return "result"
    if responsibility_type == "application":
        return "application"
    if responsibility_type == "limitation":
        return "limitation"
    if responsibility_type in {"observation_model", "observable_basis", "equation_system"}:
        return "observable_bridge"
    return "theory_base"


def _no_split_recommendation() -> dict:
    return normalize_split_recommendation({"required": False, "suggested_components": []})


def _secondary_operations(steps: list, primary: str) -> list[str]:
    return _ordered_unique(
        _generic_operation_family(str(getattr(step, "operation", "") or ""))
        for step in steps
        if _generic_operation_family(str(getattr(step, "operation", "") or "")) != primary
    )


def _responsibility_takeaway(spec: dict) -> str:
    label = str(spec.get("label") or "component")
    return f"{label} has one clear input/output responsibility."


def _responsibility_io(direction: str, spec: dict, parent: ComponentRecord) -> list[dict]:
    eq_ids = list(spec.get("equation_ids") or [])
    label = str(spec.get("label") or "")
    if eq_ids:
        return [{
            "name": f"{label} {'inputs' if direction == 'input' else 'outputs'}",
            "role": f"responsibility_{direction}",
            "equation_ids": eq_ids if direction == "output" else eq_ids[:1],
        }]
    return list(parent.inputs if direction == "input" else parent.outputs)[:1]


def _responsibility_flow(eq_ids: list[str], operation: str) -> list[dict]:
    if len(eq_ids) < 2:
        return []
    return [
        {"from": src, "relation": operation or "defines", "to": dst}
        for src, dst in zip(eq_ids, eq_ids[1:])
        if src != dst
    ][:SIZE_THRESHOLDS["max_internal_flow_steps_soft"]]


def _review_required_equation_ids(eq_ids: list[str], eq_index: dict[str, dict]) -> list[str]:
    ids: list[str] = []
    for eq_id in eq_ids:
        eq = eq_index.get(eq_id) or {}
        policy = eq.get("confidence_policy") if isinstance(eq.get("confidence_policy"), dict) else {}
        if (
            bool(eq.get("needs_math_review"))
            or bool(eq.get("review_flags"))
            or eq.get("semantic_status") == "reconstruction_based"
            or eq.get("reconstruction_status") not in (None, "", "none")
            or bool(policy.get("must_not_treat_as_source_extracted"))
            or policy.get("can_support_claim") is False
        ):
            ids.append(eq_id)
    return _ordered_unique(ids)


def _source_backed_status(parent_status: str) -> str:
    if parent_status in {"source_backed", "auto_accepted"}:
        return "source_backed"
    if parent_status in {"review_required", "teacher_review_required"}:
        return parent_status
    return "source_backed"


def _component_text(component: ComponentRecord) -> str:
    parts = [
        component.label,
        component.summary,
        component.reason,
        component.teaching_takeaway,
    ]
    for collection in (component.inputs, component.outputs, component.preconditions, component.cautions):
        for item in collection or []:
            if isinstance(item, dict):
                parts.extend(str(item.get(k) or "") for k in ("name", "label", "text", "role"))
    return " ".join(parts).lower()


def _equation_text(eq_id: str, eq_index: dict[str, dict]) -> str:
    eq = eq_index.get(eq_id) or {}
    fields = [
        eq_id,
        eq.get("label"),
        eq.get("latex"),
        eq.get("plain_text"),
        eq.get("role"),
        eq.get("equation_type"),
        eq.get("semantic_status"),
        " ".join(str(v) for v in eq.get("defined_symbols") or []),
        " ".join(str(v) for v in eq.get("used_symbols") or []),
    ]
    source = eq.get("source_location") if isinstance(eq.get("source_location"), dict) else {}
    fields.extend(str(source.get(k) or "") for k in ("section_id", "block_id"))
    return " ".join(str(v) for v in fields if v).lower()


def _responsibility_named_in_text(label: str, text: str) -> bool:
    # Removed paper-specific label matching (issues #395 / #397).
    return False


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
    explicit = _explicit_theory_unit_label(family)
    if explicit:
        return explicit
    verb = _humanize_operation(_operation_family(family), "")
    object_phrase = ", ".join(symbols[:3]) if symbols else str(parent.label or "").strip()
    if not object_phrase:
        return verb or "Theory unit"
    if not verb:
        return object_phrase
    return f"{verb}: {object_phrase}"


def _explicit_theory_unit_label(operation: str) -> str:
    # Removed paper-specific theory-unit labels (issues #395 / #398): core logic
    # must not emit domain-specific operation labels into components or graphs.
    return ""


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


def _operation_io_items(
    direction: str,
    family: str,
    equation_ids: list[str],
    fallback: list[dict],
) -> list[dict]:
    """Return conceptual component I/O with equations as supporting detail."""
    if equation_ids:
        noun = "inputs" if direction == "input" else "outputs"
        return [{
            "name": f"{_humanize_operation(family, '')} {noun}".strip(),
            "role": f"operation_{direction}",
            "equation_ids": list(equation_ids),
        }]
    return list(fallback or [])


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


def _blocked_equation_ids(equation_ids: list[str], eq_index: dict[str, dict]) -> list[str]:
    blocked: list[str] = []
    for eq_id in equation_ids:
        eq = eq_index.get(eq_id) or {}
        policy = eq.get("confidence_policy") if isinstance(eq.get("confidence_policy"), dict) else {}
        allowed_use = str(policy.get("allowed_downstream_use") or policy.get("downstream_allowed_use") or "")
        if allowed_use == "blocked":
            blocked.append(eq_id)
            continue
        if (
            policy.get("can_support_claim") is False
            and policy.get("can_be_used_in_derivation") is False
            and allowed_use in {"", "blocked", "semantic_hint_only"}
        ):
            blocked.append(eq_id)
            continue
        if (
            eq.get("latex") is None
            and eq.get("plain_text") is None
            and eq.get("extraction_status") in ("partial", "fragment_only", "label_only", "missing", "unparsed")
        ):
            blocked.append(eq_id)
    return _ordered_unique(blocked)


def _confidence_gate(blocked_eqs: list[str]) -> dict:
    if not blocked_eqs:
        return {
            "blocked_by_equation_ids": [],
            "blocked_reason": "",
            "downstream_allowed_use": "display_with_warning",
            "allowed_downstream_use": "display_with_warning",
        }
    return {
        "blocked_by_equation_ids": list(blocked_eqs),
        "blocked_reason": "linked equation cannot support claim or derivation",
        "downstream_allowed_use": "semantic_hint_only",
        "allowed_downstream_use": "semantic_hint_only",
    }


def _is_equation_dependency_bundle(component: ComponentRecord) -> bool:
    """Detect components whose public I/O is mostly raw equation references."""
    equation_refs = set(_all_equation_ids(component))
    io_equations = set(_field_equation_ids(component.inputs) + _field_equation_ids(component.outputs))
    equation_like_items = sum(
        1
        for item in list(component.inputs or []) + list(component.outputs or [])
        if _item_is_equation_like(item)
    )
    return (
        len(equation_refs) >= 6
        or len(io_equations) >= 4
        or equation_like_items >= 4
        or len(component.outputs or []) >= 4
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
    return _ordered_unique(ids)


def _item_is_equation_like(item: object) -> bool:
    if not isinstance(item, dict):
        return False
    if item.get("equation_ids") or item.get("equations"):
        return True
    text = str(item.get("name") or item.get("label") or item.get("text") or "")
    return text.startswith("eq_") or text.lower().startswith("equation ")


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


# ---------------------------------------------------------------------------
# Step 3 suggested_split helpers (issue #324)
# ---------------------------------------------------------------------------

# Responsibility groupings used to reassign equations to suggested children.
_DEFINITION_RESPONSIBILITIES = {"definition", "model", "observation_model", "observable_basis"}
_EQUATION_SYSTEM_RESPONSIBILITIES = {"equation_system"}
_DERIVATION_RESPONSIBILITIES = {"derivation"}
_RESULT_RESPONSIBILITIES = {"constraint", "application", "limitation"}

_CATEGORY_TO_RESPONSIBILITIES = {
    "definition": _DEFINITION_RESPONSIBILITIES,
    "equation_system": _EQUATION_SYSTEM_RESPONSIBILITIES | _DERIVATION_RESPONSIBILITIES,
    "derivation": _DERIVATION_RESPONSIBILITIES | _EQUATION_SYSTEM_RESPONSIBILITIES,
    "result": _RESULT_RESPONSIBILITIES,
}

_RESPONSIBILITY_OPERATIONS = {
    "definition": "define",
    "model": "parameterize",
    "observation_model": "parameterize",
    "observable_basis": "observable_definition",
    "equation_system": "linearize",
    "derivation": "derive",
    "constraint": "constrain",
    "application": "diagnose",
    "limitation": "constrain",
}


def _operation_for_responsibility(responsibility: str) -> str:
    return _RESPONSIBILITY_OPERATIONS.get(str(responsibility or ""), str(responsibility or ""))


def _equation_category(eq: dict) -> str:
    role = str(eq.get("role") or eq.get("equation_type") or "").lower()
    if role in {"definition", "equation_definition"}:
        return "definition"
    if role in {"constraint", "condition", "consistency_relation", "result"}:
        return "result"
    if role in {"transformation", "relation"}:
        return "equation_system"
    return ""


def _claim_index(llm_input) -> dict[str, dict]:
    index: dict[str, dict] = {}
    if not llm_input:
        return index
    rows = list(getattr(llm_input, "available_claims", []) or []) + list(
        getattr(llm_input, "accepted_claims", []) or []
    )
    for row in rows:
        if not isinstance(row, dict):
            continue
        cid = str(row.get("claim_id") or "")
        if not cid:
            continue
        atomicity = str(row.get("atomicity", "atomic") or "atomic")
        entry = index.setdefault(cid, {
            "concepts": [str(c) for c in (row.get("concepts") or []) if c],
            "atomicity": atomicity,
            "is_atomic": bool(row.get("is_atomic", atomicity == "atomic")),
            "equation_ids": [str(e) for e in (row.get("equation_ids") or []) if e],
            "evidence_ids": [str(e) for e in (row.get("source_evidence_ids") or []) if e],
        })
    return index


def _claim_is_atomic(cid: str, claim_index: dict[str, dict]) -> bool:
    info = claim_index.get(cid)
    if not info:
        # Unknown claims are treated as atomic so we do not over-flag review.
        return True
    return bool(info.get("is_atomic", True)) and str(info.get("atomicity", "atomic")) == "atomic"


def _equation_symbols(eq: dict) -> list[str]:
    symbols: list[str] = []
    for sym in eq.get("defined_symbols") or []:
        name = sym.get("symbol") if isinstance(sym, dict) else sym
        if name:
            symbols.append(str(name).strip())
    symbols.extend(str(s).strip() for s in (eq.get("used_symbols") or []) if str(s).strip())
    return _ordered_unique(symbols)


def _is_review_required_status(status: object) -> bool:
    return str(status or "") in {"review_required", "teacher_review_required"}


def _match_spec_by_category(specs: list[dict], category: str) -> dict | None:
    if not category:
        return None
    allowed = _CATEGORY_TO_RESPONSIBILITIES.get(category, set())
    for spec in specs:
        if spec["responsibility_type"] in allowed:
            return spec
    return None


def _match_spec_by_equation_overlap(specs: list[dict], claim_eqs: set) -> dict | None:
    if not claim_eqs:
        return None
    best: dict | None = None
    best_n = 0
    for spec in specs:
        overlap = len(set(spec["equation_ids"]) & claim_eqs)
        if overlap > best_n:
            best_n = overlap
            best = spec
    return best


def _spec_has_payload(spec: dict) -> bool:
    return bool(
        spec.get("equation_ids")
        or spec.get("claim_ids")
        or spec.get("evidence_ids")
        or spec.get("derivation_ids")
    )


def _redistribute_links(
    component: ComponentRecord,
    suggested: list[dict],
    eq_index: dict[str, dict],
    claim_index: dict[str, dict],
) -> tuple[list[dict], list[dict]]:
    """Reassign the parent's links across the suggested children (#324 rule 4).

    Returns ``(child_specs, unassigned_links)``. Links that cannot be assigned
    confidently are recorded in ``unassigned_links`` rather than dropped.
    """
    specs: list[dict] = []
    for s in suggested:
        resp = canonical_responsibility_type(s.get("responsibility_type"))
        specs.append({
            "name": s.get("name") or resp.replace("_", " ").title(),
            "responsibility_type": resp,
            "operation": s.get("operation") or _operation_for_responsibility(resp),
            "equation_ids": [],
            "claim_ids": [],
            "evidence_ids": [],
            "derivation_ids": [],
            "review_required": False,
            "review_notes": [],
        })
    unassigned: list[dict] = []

    # Equations → responsibility category.
    for eq_id in _all_equation_ids(component):
        target = _match_spec_by_category(specs, _equation_category(eq_index.get(eq_id) or {}))
        if target is None:
            unassigned.append({
                "link_type": "equation",
                "link_id": eq_id,
                "reason": "no responsibility category match",
            })
            continue
        target["equation_ids"].append(eq_id)

    # Claims → the child sharing the most equations; atomic fall back to first.
    claim_ids = _ordered_unique(
        list(component.linked_claim_ids or [])
        + list((component.evidence_refs or {}).get("claim_ids") or [])
        + list(component.supports_claim_ids or [])
    )
    claim_evidence: dict[str, set] = {}
    for cid in claim_ids:
        info = claim_index.get(cid, {})
        claim_evidence[cid] = set(info.get("evidence_ids") or [])
        target = _match_spec_by_equation_overlap(specs, set(info.get("equation_ids") or []))
        if target is None:
            if _claim_is_atomic(cid, claim_index):
                target = specs[0]
            else:
                unassigned.append({
                    "link_type": "claim",
                    "link_id": cid,
                    "reason": "composite claim without confident target",
                })
                continue
        target["claim_ids"].append(cid)

    # Evidence → the child whose assigned claims reference it.
    evidence_ids = _ordered_unique(
        list(component.linked_evidence_ids or [])
        + list((component.evidence_refs or {}).get("evidence_ids") or [])
    )
    for ev_id in evidence_ids:
        target = next(
            (
                spec
                for spec in specs
                if any(ev_id in claim_evidence.get(cid, set()) for cid in spec["claim_ids"])
            ),
            None,
        )
        if target is None:
            unassigned.append({
                "link_type": "evidence",
                "link_id": ev_id,
                "reason": "no child claim references this evidence",
            })
            continue
        target["evidence_ids"].append(ev_id)

    # Derivations → the first derivational child.
    derivational = [
        spec
        for spec in specs
        if spec["responsibility_type"]
        in (_DERIVATION_RESPONSIBILITIES | _EQUATION_SYSTEM_RESPONSIBILITIES | {"constraint"})
    ]
    for d_id in _ordered_unique(component.linked_derivation_ids or []):
        if derivational:
            derivational[0]["derivation_ids"].append(d_id)
        else:
            unassigned.append({
                "link_type": "derivation",
                "link_id": d_id,
                "reason": "no derivational child to receive derivation",
            })

    # Children supported only by composite claims are downgraded.
    for spec in specs:
        if spec["claim_ids"] and not any(
            _claim_is_atomic(c, claim_index) for c in spec["claim_ids"]
        ):
            spec["review_required"] = True
            note = "supported primarily by composite/non-atomic claim"
            if note not in spec["review_notes"]:
                spec["review_notes"].append(note)

    return specs, unassigned


def _recompute_concepts(
    claim_ids: list[str],
    eq_ids: list[str],
    eq_index: dict[str, dict],
    claim_index: dict[str, dict],
) -> list[str]:
    concepts: list[str] = []
    for cid in claim_ids:
        if _claim_is_atomic(cid, claim_index):
            concepts.extend(claim_index.get(cid, {}).get("concepts") or [])
    for eq_id in eq_ids:
        concepts.extend(_equation_symbols(eq_index.get(eq_id) or {}))
    return _ordered_unique(concepts)


def _assign_introduced_reused_children(
    children: list[ComponentRecord],
    parent: ComponentRecord,
) -> None:
    seen: set[str] = set()
    for child in children:
        introduced: list[str] = []
        reused: list[str] = []
        for concept in child.concepts or []:
            if concept in seen:
                reused.append(concept)
            else:
                introduced.append(concept)
                seen.add(concept)
        child.introduced_concepts = introduced
        child.reused_concepts = reused
        child.prerequisite_concepts = _ordered_unique(
            [c for c in (parent.prerequisite_concepts or []) if c in (child.concepts or [])]
            + reused
        )


def _suggested_io(direction: str, spec: dict, eq_ids: list[str], parent: ComponentRecord) -> list[dict]:
    name = str(spec.get("name") or "")
    if eq_ids:
        return [{
            "name": f"{name} {'inputs' if direction == 'input' else 'outputs'}".strip(),
            "role": f"responsibility_{direction}",
            "equation_ids": list(eq_ids) if direction == "output" else eq_ids[:1],
        }]
    fallback = list(parent.inputs if direction == "input" else parent.outputs)
    if fallback:
        return fallback[:1]
    return [{"name": f"{name} {direction}".strip(), "role": f"responsibility_{direction}"}]


def _suggested_summary(spec: dict, parent: ComponentRecord) -> str:
    return (
        f"{spec.get('name') or parent.label} isolated as a reusable "
        f"{spec.get('responsibility_type')} component (#324)."
    )


def _suggested_takeaway(spec: dict, equation_count: int) -> str:
    name = spec.get("name") or "Component"
    return (
        f"{name}: a single {spec.get('responsibility_type')} responsibility "
        f"covering {equation_count} equation(s)."
    )


def _add_note(notes: list[str], note: str) -> None:
    if note and note not in notes:
        notes.append(note)


def _build_suggested_component(
    parent: ComponentRecord,
    component_id: str,
    spec: dict,
    eq_index: dict[str, dict],
    claim_index: dict[str, dict],
) -> ComponentRecord:
    resp = str(spec.get("responsibility_type") or "")
    operation = str(spec.get("operation") or _operation_for_responsibility(resp))
    eq_ids = list(spec.get("equation_ids") or [])
    claim_ids = list(spec.get("claim_ids") or [])
    evidence_ids = list(spec.get("evidence_ids") or [])
    derivation_ids = list(spec.get("derivation_ids") or [])

    concepts = _recompute_concepts(claim_ids, eq_ids, eq_index, claim_index)

    blocked = _blocked_equation_ids(eq_ids, eq_index)
    review_eqs = _review_required_equation_ids(eq_ids, eq_index)
    atomic_claims = [c for c in claim_ids if _claim_is_atomic(c, claim_index)]
    composite_only = bool(claim_ids) and not atomic_claims
    empty_payload = not (claim_ids or evidence_ids or eq_ids)

    review_notes = list(parent.review_notes)
    review_required = False
    if blocked:
        review_required = True
        _add_note(review_notes, "linked equation cannot support claim or derivation")
    if review_eqs:
        review_required = True
        _add_note(review_notes, "linked equation needs math review")
    if composite_only:
        review_required = True
        _add_note(review_notes, "supported primarily by composite/non-atomic claim")
    if spec.get("review_required"):
        review_required = True
        for note in spec.get("review_notes") or []:
            _add_note(review_notes, note)
    if empty_payload and resp not in {"application", "limitation"}:
        review_required = True
        _add_note(review_notes, "missing primary claim/equation/evidence support")

    # A successfully split child is source_backed unless its own payload (blocked
    # equations, composite-only claims, missing support) triggers review. It does
    # not inherit the parent's review_required, which was set only to flag that the
    # coarse parent needed splitting (#324).
    review_status = "review_required" if review_required else "source_backed"

    evidence_refs = copy.deepcopy(parent.evidence_refs or {})
    evidence_refs["equation_ids"] = eq_ids
    evidence_refs["claim_ids"] = claim_ids

    eqsys_like = {"equation_system", "derivation", "constraint"}
    return ComponentRecord(
        component_id=component_id,
        component_type=parent.component_type,
        label=str(spec.get("name") or parent.label),
        summary=_suggested_summary(spec, parent),
        inputs=_suggested_io("input", spec, eq_ids, parent),
        outputs=_suggested_io("output", spec, eq_ids, parent),
        preconditions=list(parent.preconditions),
        cautions=list(parent.cautions),
        dependencies=[],
        evidence_refs=evidence_refs,
        reason=(
            f"Split from oversized component {parent.component_id} via Step 1 "
            "suggested_split (#324)."
        ),
        confidence=parent.confidence,
        review_notes=review_notes,
        internal_flow=_responsibility_flow(eq_ids, operation),
        linked_claim_ids=claim_ids,
        linked_equation_ids=eq_ids,
        linked_evidence_ids=evidence_ids,
        linked_derivation_ids=derivation_ids,
        linked_dsl_node_ids=list(parent.linked_dsl_node_ids),
        linked_dsl_edge_ids=list(parent.linked_dsl_edge_ids),
        input_equation_ids=eq_ids[:-1] if len(eq_ids) > 1 and resp in eqsys_like else [],
        intermediate_equation_ids=[],
        output_equation_ids=(
            eq_ids[-1:] if eq_ids and resp in (eqsys_like | {"observable_basis"}) else []
        ),
        constraint_equation_ids=[],
        definition_equation_ids=eq_ids if resp in _DEFINITION_RESPONSIBILITIES else [],
        review_required_equation_ids=_ordered_unique(review_eqs + blocked),
        eliminated_symbols=[],
        retained_symbols=[],
        equation_confidence_summary=dict(parent.equation_confidence_summary or {}),
        confidence_gate=_confidence_gate(blocked),
        review_status=review_status,
        teaching_takeaway=_suggested_takeaway(spec, len(eq_ids)),
        source_scope=copy.deepcopy(parent.source_scope or {}),
        assumptions=list(parent.assumptions),
        approximations=list(parent.approximations),
        constraints=list(parent.constraints),
        invalid_conditions=list(parent.invalid_conditions),
        responsibility_type=resp,
        primary_operation=_operation_family(operation),
        secondary_operations=[],
        split_recommendation=_no_split_recommendation(),
        concepts=concepts,
        prerequisite_concepts=[],
        introduced_concepts=[],
        reused_concepts=[],
        **_child_support_fields(parent, resp),
        operation=operation,
    )


def _teaching_granularity(component: ComponentRecord, eq_index: dict[str, dict]) -> dict:
    """Issue 10-style teaching granularity check, run after splitting (#324)."""
    equation_count = len(_all_equation_ids(component))
    concept_count = len(component.concepts or [])
    flow_count = len(component.internal_flow or [])
    secondary_count = len(component.secondary_operations or [])

    too_dense = (
        equation_count > 5
        or concept_count > 6
        or flow_count > 6
        or secondary_count > 2
    )
    estimated = 1
    if equation_count > 3 or concept_count > 4 or flow_count > 4:
        estimated = 2
    if too_dense:
        estimated = max(estimated, 3)

    takeaway = str(component.teaching_takeaway or "").strip()
    if not takeaway or len(takeaway) < 8:
        quality = "weak"
    elif too_dense:
        quality = "too_broad"
    else:
        quality = "good"

    return {
        "estimated_slide_count": estimated,
        "teachable_as_single_unit": not too_dense,
        "split_if_too_dense": too_dense,
        "teaching_takeaway_quality": quality,
    }


def _component_graph_updates(
    *,
    originals: dict[str, ComponentRecord],
    refined_by_id: dict[str, ComponentRecord],
    child_map: dict[str, list[str]],
    remap: dict[str, str],
) -> dict:
    original_ids = set(originals)
    refined_ids = set(refined_by_id)
    removed_nodes: list[str] = []
    added_nodes: list[str] = []
    for original_id, child_ids in child_map.items():
        if original_id not in refined_ids:
            removed_nodes.append(original_id)
        for cid in child_ids:
            if cid not in original_ids:
                added_nodes.append(cid)
    rewired_edges = [{"from": old, "to": new} for old, new in remap.items()]
    unresolved_edges: list[dict] = []
    for component in refined_by_id.values():
        for dep in component.dependencies or []:
            for ref in dep.get("component_refs") or []:
                if ref not in refined_ids:
                    unresolved_edges.append({
                        "component_id": component.component_id,
                        "missing_ref": ref,
                    })
    return {
        "removed_nodes": _ordered_unique(removed_nodes),
        "added_nodes": _ordered_unique(added_nodes),
        "rewired_edges": rewired_edges,
        "unresolved_edges": unresolved_edges,
    }
