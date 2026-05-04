"""BlueprintAgent.

Course mapping (course topic + linked components) と Component Assembly result
から narrative blueprint を組み立てる。

design:
- structure-first / deterministic-first.
- Component の type をもとに narrative role に分類し、教材の説明順序を決める。
- LLM enricher は optional フックとして差し込める（rationale 生成用途など）。
- ブラックボックス/展開する式は CourseMapping の blackbox_policy を反映する。
"""
from __future__ import annotations

from typing import Callable, Optional

from episteme_graph.agents.component_assembly.schema import (
    ComponentAssemblyResult,
    ComponentRecord,
)
from episteme_graph.agents.course_mapping.schema import (
    CourseMappingResult,
    CourseTopicMapping,
)

from .schema import (
    AUDIENCE_LEVELS,
    BLUEPRINT_VERSION,
    BlueprintResult,
    NarrativeStep,
    ValidationIssue,
)

_TYPE_TO_ROLE: dict[str, str] = {
    "TheoryComponent": "problem_setup",
    "AssumptionComponent": "problem_setup",
    "RelationComponent": "core_relation",
    "PaperRelationComponent": "core_relation",
    "MethodComponent": "derivation_intuition",
    "CorrectionComponent": "correction_evaluation",
    "UncertaintyComponent": "uncertainty_evaluation",
    "DiagnosticComponent": "diagnostic",
    "ClaimBundleComponent": "result_summary",
}

_ROLE_PRIORITY: list[str] = [
    "problem_setup",
    "core_relation",
    "derivation_intuition",
    "correction_evaluation",
    "uncertainty_evaluation",
    "diagnostic",
    "result_summary",
    "future_work",
]

_ROLE_TO_VISUAL: dict[str, str] = {
    "problem_setup": "contrast_anomaly_and_baseline",
    "core_relation": "equation_map",
    "derivation_intuition": "rate_partition_diagram",
    "correction_evaluation": "rate_partition_diagram",
    "uncertainty_evaluation": "uncertainty_breakdown",
    "diagnostic": "comparison_plot",
    "result_summary": "result_table",
    "future_work": "none",
}


class BlueprintAgent:
    """Course mapping + Components → Blueprint narrative arc."""

    def __init__(
        self,
        *,
        rationale_enricher: Optional[
            Callable[[NarrativeStep, list[ComponentRecord]], NarrativeStep]
        ] = None,
    ) -> None:
        self._rationale_enricher = rationale_enricher

    def run(
        self,
        course: CourseMappingResult,
        components: ComponentAssemblyResult,
        *,
        audience_level: str = "graduate_seminar",
        course_id: str | None = None,
    ) -> BlueprintResult:
        component_index = {c.component_id: c for c in components.components}
        issues: list[ValidationIssue] = []
        review_notes: list[str] = []

        if audience_level not in AUDIENCE_LEVELS:
            issues.append(ValidationIssue(
                rule_id="blueprint_invalid_audience_level",
                severity="warning",
                message=f"audience_level {audience_level!r} is not in AUDIENCE_LEVELS",
                field="audience_level",
            ))

        course_topics = course.topics or []
        if not course_topics:
            issues.append(ValidationIssue(
                rule_id="blueprint_missing_course_topics",
                severity="error",
                message="course has no topics; cannot build blueprint",
                field="narrative_arc",
            ))
            return BlueprintResult(
                blueprint_id=f"blueprint_{course.document_id}",
                document_id=course.document_id,
                source_course_id=course_id or course.document_id,
                audience_level=audience_level,
                narrative_arc=[],
                review_notes=review_notes,
                validation_issues=issues,
            )

        ordered_components = self._collect_ordered_components(
            course_topics, component_index, issues
        )

        steps = self._build_steps(
            ordered_components, course_topics[0], component_index
        )
        if self._rationale_enricher:
            try:
                steps = [
                    self._rationale_enricher(step, list(component_index.values()))
                    for step in steps
                ]
            except Exception as exc:
                issues.append(ValidationIssue(
                    rule_id="blueprint_rationale_enricher_failed",
                    severity="warning",
                    message=str(exc),
                    field="narrative_arc",
                ))

        issues += self._validate_arc(steps, course_topics[0])

        return BlueprintResult(
            blueprint_id=f"blueprint_{course.document_id}",
            document_id=course.document_id,
            source_course_id=course_id or course.document_id,
            audience_level=audience_level,
            narrative_arc=steps,
            review_notes=review_notes,
            validation_issues=issues,
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _collect_ordered_components(
        topics: list[CourseTopicMapping],
        component_index: dict[str, ComponentRecord],
        issues: list[ValidationIssue],
    ) -> list[ComponentRecord]:
        seen: set[str] = set()
        ordered: list[ComponentRecord] = []
        for topic in topics:
            for cid in topic.linked_component_ids:
                if cid in seen:
                    continue
                comp = component_index.get(cid)
                if comp is None:
                    issues.append(ValidationIssue(
                        rule_id="blueprint_unknown_component_ref",
                        severity="warning",
                        message=f"course topic references unknown component {cid!r}",
                        field="narrative_arc",
                    ))
                    continue
                seen.add(cid)
                ordered.append(comp)
        # Sort by role priority while preserving insertion order within a role.
        bucket: dict[str, list[ComponentRecord]] = {role: [] for role in _ROLE_PRIORITY}
        leftover: list[ComponentRecord] = []
        for comp in ordered:
            role = _TYPE_TO_ROLE.get(comp.component_type)
            if role is None:
                leftover.append(comp)
                continue
            bucket[role].append(comp)
        result: list[ComponentRecord] = []
        for role in _ROLE_PRIORITY:
            result.extend(bucket[role])
        result.extend(leftover)
        return result

    @staticmethod
    def _build_steps(
        components: list[ComponentRecord],
        first_topic: CourseTopicMapping,
        component_index: dict[str, ComponentRecord],
    ) -> list[NarrativeStep]:
        blackbox_policy = first_topic.blackbox_policy or {}
        blackbox_equations = list(blackbox_policy.get("blackbox_equations", []) or [])
        expand_equations = list(blackbox_policy.get("expand_equations", []) or [])
        steps: list[NarrativeStep] = []
        for idx, comp in enumerate(components, start=1):
            role = _TYPE_TO_ROLE.get(comp.component_type, "result_summary")
            visual = _ROLE_TO_VISUAL.get(role, "none")
            rationale = comp.summary or comp.label or comp.component_id
            steps.append(NarrativeStep(
                step=idx,
                role=role,
                linked_component_ids=[comp.component_id],
                visual_strategy=visual,
                rationale=rationale,
                blackbox_equations=blackbox_equations,
                expand_equations=expand_equations,
            ))
        return steps

    @staticmethod
    def _validate_arc(
        steps: list[NarrativeStep],
        first_topic: CourseTopicMapping,
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if not steps:
            issues.append(ValidationIssue(
                rule_id="blueprint_empty_arc",
                severity="error",
                message="narrative_arc is empty",
                field="narrative_arc",
            ))
            return issues
        roles = {s.role for s in steps}
        if "core_relation" not in roles and "derivation_intuition" not in roles:
            issues.append(ValidationIssue(
                rule_id="blueprint_missing_core_relation",
                severity="warning",
                message="narrative_arc has no core_relation or derivation_intuition step",
                field="narrative_arc",
            ))
        if "result_summary" not in roles:
            issues.append(ValidationIssue(
                rule_id="blueprint_missing_result_summary",
                severity="info",
                message="narrative_arc has no result_summary step",
                field="narrative_arc",
            ))
        if not first_topic.learning_objectives:
            issues.append(ValidationIssue(
                rule_id="blueprint_topic_missing_learning_objectives",
                severity="warning",
                message="course topic has no learning_objectives",
                field="narrative_arc",
            ))
        return issues
