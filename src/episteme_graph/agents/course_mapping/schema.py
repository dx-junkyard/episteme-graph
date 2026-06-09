"""CourseMapping data models."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field


@dataclass
class CourseTopicMapping:
    title: str
    description: str
    linked_component_ids: list[str] = field(default_factory=list)
    learning_objectives: list[str] = field(default_factory=list)
    prerequisite_concepts: list[str] = field(default_factory=list)
    introduced_concepts: list[str] = field(default_factory=list)
    blackbox_policy: dict = field(default_factory=dict)
    assessment_prompts: list[str] = field(default_factory=list)


@dataclass
class ValidationIssue:
    rule_id: str
    severity: str
    message: str
    field: str | None = None


@dataclass
class CourseMappingResult:
    document_id: str
    cartridge_id: str | None
    topics: list[CourseTopicMapping] = field(default_factory=list)
    validation_issues: list[ValidationIssue] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)
