"""Step 5: TheoryBundleBuilder + TeachingOutputMapper (issue #326).

Represents the whole paper as a ``theory_bundle`` (a container that *references*
the refined components, theory component graph and support map) instead of one
giant component, and maps the refined components into a teaching output
(course topics + minimal blueprint reflection).

This stage is deterministic (non-LLM) and additive. It runs after Step 4
(DerivationGraphAligner) and reads:

- ``result.components``                 -- refined components (Step 3 output)
- ``result.component_refinement``       -- Step 3 id mapping (old -> refined)
- ``result.derivation_graph_alignment`` -- Step 4 theory graph + support map
- ``llm_input``                         -- equations + claim-centered plan

It emits a single ``theory_bundle`` artifact dict on the ComponentAssemblyResult:

    {
      "theory_bundle":  {...},          # paper-level container (refs only)
      "course_mapping": {"topics": []}, # teaching output (refined-component topics)
      "blueprint_updates": {...},       # minimal blueprint reflection
      "theory_bundle_validation":  {...},
      "teaching_output_validation": {...},
    }

Design principles (issue #326 / CLAUDE.md):
- The paper-level / headline claim attaches to the bundle, not a giant component.
- Reusable theory parts remain components; the bundle only references their IDs.
- Course topics link refined component IDs (never pre-split / old IDs).
- low-confidence / blocked equations are surfaced in each topic's
  ``blackbox_policy`` and in ``blueprint_updates.review_required_items``.
- review_required components propagate to the teaching output and bundle status.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

SOURCE_BACKED = "source_backed"
REVIEW_REQUIRED = "review_required"

BUNDLE_KIND = "theory_bundle"


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------


@dataclass
class TheoryBundleResult:
    theory_bundle: dict = field(default_factory=dict)
    course_mapping: dict = field(default_factory=dict)
    blueprint_updates: dict = field(default_factory=dict)
    theory_bundle_validation: dict = field(default_factory=dict)
    teaching_output_validation: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Stage orchestrator
# ---------------------------------------------------------------------------


class TheoryBundleStage:
    """Run TheoryBundleBuilder then TeachingOutputMapper and self-validate."""

    def __init__(self) -> None:
        self._bundle_builder = TheoryBundleBuilder()
        self._teaching_mapper = TeachingOutputMapper()

    def run(self, result, llm_input=None, derivations=None) -> TheoryBundleResult:
        components = list(getattr(result, "components", []) or [])
        eq_index = _equation_index(llm_input)
        document_id = str(getattr(result, "document_id", "") or "")
        headline_claim_id, headline_claim = _headline_claim(llm_input)
        alignment = getattr(result, "derivation_graph_alignment", {}) or {}
        # Step 4 owns the can_be_rendered_as_final_formula / blocked-path downgrade;
        # Step 5 consumes the resulting per-component review status from the theory
        # component graph so the bundle / teaching output stay consistent with it.
        review_override = _alignment_review_status(alignment)

        bundle = self._bundle_builder.build(
            document_id=document_id,
            components=components,
            eq_index=eq_index,
            headline_claim_id=headline_claim_id,
            headline_claim=headline_claim,
            alignment=alignment,
            review_override=review_override,
        )
        teaching = self._teaching_mapper.map(
            components=components,
            eq_index=eq_index,
            headline_claim=headline_claim,
            review_override=review_override,
        )
        blueprint_updates = self._teaching_mapper.blueprint_updates(
            components=components, eq_index=eq_index, review_override=review_override
        )

        bundle_validation = _validate_bundle(bundle, components, alignment)
        teaching_validation = _validate_teaching_output(
            teaching, blueprint_updates, components, eq_index
        )

        return TheoryBundleResult(
            theory_bundle=bundle,
            course_mapping=teaching,
            blueprint_updates=blueprint_updates,
            theory_bundle_validation=bundle_validation,
            teaching_output_validation=teaching_validation,
        )


# ---------------------------------------------------------------------------
# TheoryBundleBuilder
# ---------------------------------------------------------------------------


class TheoryBundleBuilder:
    """Build the paper-level theory bundle container (references only)."""

    def build(
        self,
        *,
        document_id: str,
        components: list,
        eq_index: dict,
        headline_claim_id: str,
        headline_claim: str,
        alignment: dict,
        review_override: dict | None = None,
    ) -> dict:
        component_ids = [c.component_id for c in components]
        review_reasons: list[str] = []
        status = SOURCE_BACKED
        for component in components:
            if _component_review_status(component, review_override) == REVIEW_REQUIRED:
                status = REVIEW_REQUIRED
                _add(review_reasons, f"component {component.component_id} requires review")
            for eq_id in _component_low_confidence_equations(component, eq_index):
                status = REVIEW_REQUIRED
                _add(review_reasons, f"low-confidence equation {eq_id} in bundle")

        support_map = alignment.get("support_map") if isinstance(alignment, dict) else None
        theory_graph = (
            alignment.get("theory_component_graph") if isinstance(alignment, dict) else None
        )

        return {
            "bundle_id": f"{document_id}:{BUNDLE_KIND}",
            "paper_id": document_id,
            "kind": BUNDLE_KIND,
            "title": headline_claim or f"Theory bundle for {document_id}",
            "central_question": headline_claim,
            "headline_claim_id": headline_claim_id,
            "headline_claim": headline_claim,
            "component_ids": component_ids,
            # References to the Step 4 artifacts. The graph/support-map payloads
            # live on result.derivation_graph_alignment; these IDs are the
            # deterministic handles for them.
            "component_graph_id": f"{document_id}:theory_component_graph"
            if theory_graph
            else "",
            "support_map_id": f"{document_id}:support_map" if support_map else "",
            # Registry handles: payloads are reachable from the upstream
            # EvidenceRegistry / EquationSemantics / DerivationChain artifacts
            # (keyed by document_id).
            "evidence_registry_id": f"{document_id}:evidence_registry",
            "equation_registry_id": f"{document_id}:equation_registry",
            "derivation_registry_id": f"{document_id}:derivation_registry",
            "review_status": status,
            "review_reasons": review_reasons,
        }


# ---------------------------------------------------------------------------
# TeachingOutputMapper
# ---------------------------------------------------------------------------


class TeachingOutputMapper:
    """Map refined components into course topics + minimal blueprint reflection."""

    def map(
        self,
        *,
        components: list,
        eq_index: dict,
        headline_claim: str,
        review_override: dict | None = None,
    ) -> dict:
        topics: list[dict] = []
        for component in components:
            topics.append(
                self._topic_for_component(
                    component, eq_index, headline_claim, review_override
                )
            )
        return {"topics": topics}

    def _topic_for_component(
        self, component, eq_index, headline_claim, review_override=None
    ) -> dict:
        cid = component.component_id
        label = str(getattr(component, "label", "") or cid)
        takeaway = str(getattr(component, "teaching_takeaway", "") or "").strip()
        review_status = _component_review_status(component, review_override)

        learning_objectives: list[str] = []
        if takeaway:
            learning_objectives.append(takeaway)
        learning_objectives.append(f"Understand {label}.")

        blackbox_policy = _blackbox_policy_for_component(component, eq_index)
        granularity = dict(getattr(component, "teaching_granularity", {}) or {})

        return {
            "topic_id": f"topic:{cid}",
            "title": label,
            "description": str(getattr(component, "summary", "") or ""),
            "linked_component_ids": [cid],
            "learning_objectives": _ordered_unique(learning_objectives),
            "prerequisite_concepts": list(getattr(component, "prerequisite_concepts", []) or []),
            "introduced_concepts": list(getattr(component, "introduced_concepts", []) or []),
            "blackbox_policy": blackbox_policy,
            "assessment_prompts": [f"Explain {label} and its role in the paper's argument."],
            "expected_misconceptions": _expected_misconceptions(component),
            "visualization_plan": _visualization_plan(component),
            "review_status": review_status,
            "teaching_granularity": granularity,
        }

    def blueprint_updates(
        self, *, components: list, eq_index: dict, review_override: dict | None = None
    ) -> dict:
        linked_component_ids = [c.component_id for c in components]
        review_required_items: list[dict] = []
        for component in components:
            if _component_review_status(component, review_override) == REVIEW_REQUIRED:
                review_required_items.append({
                    "type": "component",
                    "id": component.component_id,
                    "reason": "component is review_required",
                })
            for eq_id in _component_low_confidence_equations(component, eq_index):
                review_required_items.append({
                    "type": "equation",
                    "id": eq_id,
                    "reason": "low-confidence equation must be blackboxed or warned",
                })
        return {
            "linked_component_ids": linked_component_ids,
            "review_required_items": review_required_items,
        }


# ---------------------------------------------------------------------------
# Validation (stage self-check; gate re-validates refs independently)
# ---------------------------------------------------------------------------


def _validate_bundle(bundle: dict, components: list, alignment: dict) -> dict:
    component_ids = {c.component_id for c in components}
    errors: list[dict] = []
    warnings: list[dict] = []
    review_items: list[dict] = []

    bundle_created = bool(bundle.get("component_ids"))
    if not bundle_created:
        warnings.append({
            "code": "theory_bundle_empty",
            "message": "theory bundle has no component references",
        })

    headline_claim_linked = bool(bundle.get("headline_claim_id"))
    if not headline_claim_linked:
        warnings.append({
            "code": "bundle_headline_claim_missing",
            "message": "theory bundle has no headline claim id",
        })

    dangling = [cid for cid in bundle.get("component_ids", []) if cid not in component_ids]
    for cid in dangling:
        errors.append({
            "code": "bundle_dangling_component_ref",
            "message": f"theory bundle references non-existent component '{cid}'",
            "component_id": cid,
        })
    component_refs_valid = not dangling

    support_map = alignment.get("support_map") if isinstance(alignment, dict) else None
    support_map_linked = bool(bundle.get("support_map_id")) and bool(
        (support_map or {}).get("layers")
    )
    if not support_map_linked:
        warnings.append({
            "code": "bundle_support_map_missing",
            "message": "theory bundle is not linked to a renderable support map",
        })

    if bundle.get("review_status") == REVIEW_REQUIRED:
        review_items.append({
            "code": "bundle_review_required",
            "message": "theory bundle requires review before publish-ready export",
            "reasons": list(bundle.get("review_reasons") or []),
        })

    return {
        "errors": errors,
        "warnings": warnings,
        "review_items": review_items,
        "bundle_created": bundle_created,
        "headline_claim_linked": headline_claim_linked,
        "component_refs_valid": component_refs_valid,
        "support_map_linked": support_map_linked,
    }


def _validate_teaching_output(
    teaching: dict, blueprint_updates: dict, components: list, eq_index: dict
) -> dict:
    component_ids = {c.component_id for c in components}
    component_by_id = {c.component_id: c for c in components}
    errors: list[dict] = []
    warnings: list[dict] = []
    review_items: list[dict] = []

    topics = teaching.get("topics", []) or []
    course_topics_link_components = True
    blackbox_ok = True

    for topic in topics:
        linked = topic.get("linked_component_ids") or []
        resolved = [cid for cid in linked if cid in component_ids]
        if not resolved:
            course_topics_link_components = False
            errors.append({
                "code": "topic_links_no_component",
                "message": f"course topic '{topic.get('topic_id')}' links no refined component",
                "topic_id": topic.get("topic_id"),
            })
        for cid in linked:
            if cid not in component_ids:
                course_topics_link_components = False
                errors.append({
                    "code": "topic_dangling_component_ref",
                    "message": (
                        f"course topic '{topic.get('topic_id')}' references "
                        f"non-existent component '{cid}'"
                    ),
                    "topic_id": topic.get("topic_id"),
                    "component_id": cid,
                })

        # blackbox policy must cover every low-confidence equation of its components.
        policy_eq_ids = {
            str(p.get("equation_id"))
            for p in topic.get("blackbox_policy") or []
            if isinstance(p, dict) and p.get("equation_id")
        }
        for cid in resolved:
            for eq_id in _component_low_confidence_equations(component_by_id[cid], eq_index):
                if eq_id not in policy_eq_ids:
                    blackbox_ok = False
                    errors.append({
                        "code": "blackbox_policy_missing_low_confidence_equation",
                        "message": (
                            f"course topic '{topic.get('topic_id')}' does not blackbox "
                            f"low-confidence equation '{eq_id}'"
                        ),
                        "topic_id": topic.get("topic_id"),
                        "equation_id": eq_id,
                    })

        if topic.get("review_status") == REVIEW_REQUIRED:
            review_items.append({
                "code": "topic_review_required",
                "message": (
                    f"course topic '{topic.get('topic_id')}' contains a review_required "
                    f"component"
                ),
                "topic_id": topic.get("topic_id"),
            })

        granularity = topic.get("teaching_granularity") or {}
        if granularity.get("teachable_as_single_unit") is False or granularity.get(
            "split_if_too_dense"
        ):
            warnings.append({
                "code": "topic_too_dense",
                "message": (
                    f"course topic '{topic.get('topic_id')}' is too dense to teach as a "
                    f"single unit"
                ),
                "topic_id": topic.get("topic_id"),
            })

    blueprint_refs_valid = True
    for cid in blueprint_updates.get("linked_component_ids", []) or []:
        if cid not in component_ids:
            blueprint_refs_valid = False
            errors.append({
                "code": "blueprint_dangling_component_ref",
                "message": f"blueprint references non-existent component '{cid}'",
                "component_id": cid,
            })

    return {
        "errors": errors,
        "warnings": warnings,
        "review_items": review_items,
        "course_topics_link_components": course_topics_link_components,
        "blueprint_refs_valid": blueprint_refs_valid,
        "blackbox_policy_respects_confidence": blackbox_ok,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _equation_index(llm_input) -> dict:
    index: dict[str, dict] = {}
    if not llm_input:
        return index
    pools = list(getattr(llm_input, "equations", []) or []) + list(
        getattr(llm_input, "available_equations", []) or []
    )
    for eq in pools:
        eq_id = str(eq.get("equation_id") or "")
        if not eq_id:
            continue
        merged = dict(index.get(eq_id, {}))
        merged.update(eq)
        index[eq_id] = merged
    return index


def _headline_claim(llm_input) -> tuple[str, str]:
    if not llm_input:
        return "", ""
    plan = getattr(llm_input, "claim_centered_plan", {}) or {}
    ids = plan.get("headline_claim_ids") or []
    claim_id = str(ids[0]) if ids else ""
    return claim_id, str(plan.get("headline_claim") or "")


def _alignment_review_status(alignment: dict) -> dict:
    """component_id -> review_status from the Step 4 theory component graph."""
    override: dict[str, str] = {}
    if not isinstance(alignment, dict):
        return override
    graph = alignment.get("theory_component_graph") or {}
    for node in graph.get("nodes", []) or []:
        cid = node.get("component_id")
        status = node.get("review_status")
        if cid and status:
            override[str(cid)] = str(status)
    return override


def _component_review_status(component, review_override: dict | None = None) -> str:
    cid = getattr(component, "component_id", "")
    if review_override and str(review_override.get(cid, "")) == REVIEW_REQUIRED:
        return REVIEW_REQUIRED
    raw = str(getattr(component, "review_status", "") or "")
    if raw in {SOURCE_BACKED, "auto_accepted"}:
        # An otherwise-accepted component is still review_required if its own
        # gate / review-equation fields say so.
        if getattr(component, "review_required_equation_ids", None):
            return REVIEW_REQUIRED
        gate = getattr(component, "confidence_gate", {}) or {}
        if gate.get("blocked_by_equation_ids"):
            return REVIEW_REQUIRED
        return SOURCE_BACKED
    return REVIEW_REQUIRED


def _equation_is_low_confidence(eq: dict) -> bool:
    """True when an equation's confidence policy makes it unsafe to render as-is.

    Mirrors the Step 0 / Step 4 gates: blocked downstream use, cannot support a
    claim, cannot be used in a derivation, or cannot be rendered as a final
    formula. The last one is the can_be_rendered_as_final_formula concern that
    Step 4 owns; Step 5 surfaces it in the teaching blackbox policy.
    """
    if not isinstance(eq, dict):
        return False
    policy = eq.get("confidence_policy")
    if not isinstance(policy, dict):
        return False
    if str(policy.get("allowed_downstream_use") or "") == "blocked":
        return True
    return (
        policy.get("can_support_claim") is False
        or policy.get("can_be_used_in_derivation") is False
        or policy.get("can_be_rendered_as_final_formula") is False
    )


def _component_low_confidence_equations(component, eq_index: dict | None = None) -> list[str]:
    # Step 3 component fields (already-decided low-confidence equations).
    ids: list[str] = list(getattr(component, "review_required_equation_ids", []) or [])
    gate = getattr(component, "confidence_gate", {}) or {}
    ids.extend(gate.get("blocked_by_equation_ids") or [])
    # Plus any linked equation whose confidence policy says it must not be
    # rendered as-is (covers can_be_rendered_as_final_formula=false, #326).
    if eq_index:
        for eq_id in _component_equation_ids(component):
            if _equation_is_low_confidence(eq_index.get(eq_id, {})):
                ids.append(eq_id)
    return _ordered_unique(ids)


def _component_equation_ids(component) -> list[str]:
    ids: list[str] = []
    for field_name in (
        "linked_equation_ids",
        "input_equation_ids",
        "intermediate_equation_ids",
        "output_equation_ids",
        "constraint_equation_ids",
        "definition_equation_ids",
        "review_required_equation_ids",
    ):
        ids.extend(getattr(component, field_name, []) or [])
    refs = getattr(component, "evidence_refs", {}) or {}
    ids.extend(refs.get("equation_ids") or [])
    return _ordered_unique(ids)


def _blackbox_policy_for_component(component, eq_index) -> list[dict]:
    policy: list[dict] = []
    for eq_id in _component_low_confidence_equations(component, eq_index):
        eq = eq_index.get(eq_id, {}) if isinstance(eq_index, dict) else {}
        eq_policy = eq.get("confidence_policy") if isinstance(eq, dict) else {}
        eq_policy = eq_policy if isinstance(eq_policy, dict) else {}
        allowed = str(eq_policy.get("allowed_downstream_use") or "")
        if allowed not in {"blocked", "semantic_hint_only"}:
            allowed = "semantic_hint_only"
        policy.append({
            "equation_id": eq_id,
            "allowed_downstream_use": allowed,
            "reason": "low-confidence equation must not be rendered as a final formula",
        })
    return policy


def _expected_misconceptions(component) -> list[str]:
    items: list[str] = []
    for caution in getattr(component, "cautions", []) or []:
        if isinstance(caution, dict):
            text = str(caution.get("text") or caution.get("name") or "").strip()
        else:
            text = str(caution or "").strip()
        if text:
            items.append(text)
    for cond in getattr(component, "invalid_conditions", []) or []:
        text = str(cond or "").strip()
        if text:
            items.append(f"Assuming validity when: {text}")
    return _ordered_unique(items)


def _visualization_plan(component) -> list[dict]:
    plan: list[dict] = []
    plan.append({"kind": "component", "ref": component.component_id})
    for eq_id in _ordered_unique(
        list(getattr(component, "linked_equation_ids", []) or [])
    ):
        plan.append({"kind": "equation", "ref": eq_id})
    for der_id in _ordered_unique(
        list(getattr(component, "linked_derivation_ids", []) or [])
    ):
        plan.append({"kind": "derivation", "ref": der_id})
    return plan


def _add(items: list[str], value: str) -> None:
    if value and value not in items:
        items.append(value)


def _ordered_unique(values) -> list[str]:
    result: list[str] = []
    for value in values or []:
        text = str(value or "")
        if text and text not in result:
            result.append(text)
    return result
