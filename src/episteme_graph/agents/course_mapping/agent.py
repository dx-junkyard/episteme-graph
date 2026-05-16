"""CourseMappingAgent.

ComponentAssemblyResult / ClaimObjectBuildResult を受け取り、
component を course topic へグルーピングして CourseMappingResult を生成する。

Quality checks:
- linked_component_ids が空にならない
- learning_objectives がある
- prerequisite_concepts がある
- blackbox_policy がある
"""
from __future__ import annotations

from typing import Iterable, Optional

from .schema import (
    CourseMappingResult,
    CourseTopicMapping,
    ValidationIssue,
)


class CourseMappingAgent:
    """Map components to course topics.

    Default policy: 1 component = 1 topic (既存トピックから linked_component_ids が
    空のままになる問題を回避するため、Component を確実に topic に紐付ける)。

    クラスタ単位で topic を構成したい場合は cluster_strategy を渡す。
    """

    def __init__(
        self,
        cluster_strategy: Optional[str] = None,
    ) -> None:
        self._cluster_strategy = cluster_strategy or "one_component_per_topic"

    def run(
        self,
        document_id: str,
        components: Iterable[object],
        *,
        claims: Iterable[object] | None = None,
        cartridge_id: str | None = None,
        existing_course_info: dict | None = None,
    ) -> CourseMappingResult:
        components = list(components)
        claims_list = list(claims or [])
        topics: list[CourseTopicMapping] = []
        issues: list[ValidationIssue] = []

        if not components:
            issues.append(ValidationIssue(
                rule_id="course_mapping_no_components",
                severity="error",
                message="No components provided; cannot build course topics.",
            ))
            return CourseMappingResult(
                document_id=document_id,
                cartridge_id=cartridge_id,
                topics=[],
                validation_issues=issues,
            )

        claim_index = self._build_claim_index(claims_list)

        if self._cluster_strategy == "one_component_per_topic":
            for comp in components:
                topic = self._build_topic_for_component(comp, claim_index)
                topics.append(topic)
        else:
            # Future: clustering by component_type / shared evidence
            for comp in components:
                topics.append(self._build_topic_for_component(comp, claim_index))

        # Quality checks
        for topic in topics:
            if not topic.linked_component_ids:
                issues.append(ValidationIssue(
                    rule_id="course_topic_missing_components",
                    severity="error",
                    message=f"topic '{topic.title}' has empty linked_component_ids",
                    field=topic.title,
                ))
            if not topic.learning_objectives:
                issues.append(ValidationIssue(
                    rule_id="course_topic_missing_objectives",
                    severity="warning",
                    message=f"topic '{topic.title}' has no learning_objectives",
                    field=topic.title,
                ))
            if not topic.prerequisite_concepts:
                issues.append(ValidationIssue(
                    rule_id="course_topic_missing_prerequisites",
                    severity="warning",
                    message=f"topic '{topic.title}' has no prerequisite_concepts",
                    field=topic.title,
                ))
            if not topic.blackbox_policy:
                issues.append(ValidationIssue(
                    rule_id="course_topic_missing_blackbox_policy",
                    severity="warning",
                    message=f"topic '{topic.title}' has no blackbox_policy",
                    field=topic.title,
                ))

        return CourseMappingResult(
            document_id=document_id,
            cartridge_id=cartridge_id,
            topics=topics,
            validation_issues=issues,
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _build_claim_index(claims: list[object]) -> dict[str, object]:
        idx: dict[str, object] = {}
        for c in claims:
            cid = getattr(c, "claim_id", None) or (c.get("claim_id") if isinstance(c, dict) else None)
            if cid:
                idx[cid] = c
        return idx

    def _build_topic_for_component(
        self,
        component: object,
        claim_index: dict[str, object],
    ) -> CourseTopicMapping:
        def _attr(obj, name, default=None):
            if isinstance(obj, dict):
                return obj.get(name, default)
            return getattr(obj, name, default)

        comp_id = _attr(component, "component_id") or ""
        label = _attr(component, "label") or ""
        summary = _attr(component, "summary") or ""
        comp_type = _attr(component, "component_type") or ""

        # Extract preconditions as prerequisite concepts
        preconditions = _attr(component, "preconditions") or []
        prerequisite_concepts: list[str] = []
        for p in preconditions:
            if isinstance(p, dict):
                cond = p.get("condition") or p.get("text")
                if cond:
                    prerequisite_concepts.append(str(cond))
            elif isinstance(p, str):
                prerequisite_concepts.append(p)

        # Extract evidence claims for assessment material
        evidence_refs = _attr(component, "evidence_refs") or {}
        evidence_claim_ids: list[str] = []
        if isinstance(evidence_refs, dict):
            evidence_claim_ids = list(evidence_refs.get("claim_ids", []) or [])
        evidence_claims_attr = _attr(component, "evidence_claims")
        if evidence_claims_attr:
            evidence_claim_ids.extend(list(evidence_claims_attr))

        # Build learning objectives based on component summary and outputs
        outputs = _attr(component, "outputs") or []
        objectives: list[str] = []
        if summary:
            objectives.append(f"{label}の概要を説明できる" if label else "このコンポーネントの概要を説明できる")
        for o in outputs:
            if isinstance(o, dict):
                name = o.get("name") or o.get("text")
                if name:
                    objectives.append(f"{name}の役割を説明できる")
        if not objectives:
            objectives.append("このコンポーネントが扱う主題を説明できる")

        # Blackbox policy: derived from component_type
        blackbox_policy = self._default_blackbox_policy(comp_type, component)

        # Assessment prompts: lightweight question stems based on objectives
        assessment_prompts = [f"{obj}か？" for obj in objectives[:3]]

        return CourseTopicMapping(
            title=label or (comp_id or "Topic"),
            description=summary or label or "",
            linked_component_ids=[comp_id] if comp_id else [],
            learning_objectives=objectives,
            prerequisite_concepts=prerequisite_concepts,
            blackbox_policy=blackbox_policy,
            assessment_prompts=assessment_prompts,
        )

    @staticmethod
    def _default_blackbox_policy(component_type: str, component: object) -> dict:
        if isinstance(component, dict):
            equation_ids = component.get("equation_ids") or []
        else:
            equation_ids = getattr(component, "equation_ids", None) or []
        # Heuristic: theory-style components show derivations; method/diagnostic blackbox most steps.
        if component_type in {"TheoryComponent", "RelationComponent", "PaperRelationComponent"}:
            depth = "medium"
            expand = list(equation_ids[-2:])
            blackbox = list(equation_ids[:-2]) if len(equation_ids) > 2 else []
        elif component_type in {"MethodComponent", "DiagnosticComponent"}:
            depth = "shallow"
            expand = []
            blackbox = list(equation_ids)
        else:
            depth = "medium"
            expand = list(equation_ids[-1:])
            blackbox = list(equation_ids[:-1]) if len(equation_ids) > 1 else []
        return {
            "show_derivation_depth": depth,
            "blackbox_equations": blackbox,
            "expand_equations": expand,
        }
