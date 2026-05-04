"""BlueprintAgent data models."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field


BLUEPRINT_VERSION = "v1"

NARRATIVE_ROLES = [
    "problem_setup",
    "core_relation",
    "derivation_intuition",
    "correction_evaluation",
    "uncertainty_evaluation",
    "diagnostic",
    "result_summary",
    "future_work",
]

VISUAL_STRATEGIES = [
    "contrast_anomaly_and_baseline",
    "equation_map",
    "rate_partition_diagram",
    "uncertainty_breakdown",
    "comparison_plot",
    "schematic",
    "flow_diagram",
    "result_table",
    "none",
]

AUDIENCE_LEVELS = [
    "undergraduate",
    "graduate_seminar",
    "specialist_review",
    "general_audience",
]


@dataclass
class NarrativeStep:
    step: int
    role: str
    linked_component_ids: list[str] = field(default_factory=list)
    visual_strategy: str = "none"
    rationale: str = ""
    blackbox_equations: list[str] = field(default_factory=list)
    expand_equations: list[str] = field(default_factory=list)


@dataclass
class ValidationIssue:
    rule_id: str
    severity: str
    message: str
    field: str | None = None


@dataclass
class BlueprintResult:
    blueprint_id: str
    document_id: str
    source_course_id: str
    audience_level: str
    narrative_arc: list[NarrativeStep] = field(default_factory=list)
    review_notes: list[str] = field(default_factory=list)
    validation_issues: list[ValidationIssue] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_dict(cls, d: dict) -> "BlueprintResult":
        steps = [NarrativeStep(**s) for s in d.get("narrative_arc", [])]
        issues = [ValidationIssue(**i) for i in d.get("validation_issues", [])]
        return cls(
            blueprint_id=d["blueprint_id"],
            document_id=d["document_id"],
            source_course_id=d["source_course_id"],
            audience_level=d.get("audience_level", "graduate_seminar"),
            narrative_arc=steps,
            review_notes=list(d.get("review_notes", [])),
            validation_issues=issues,
        )
