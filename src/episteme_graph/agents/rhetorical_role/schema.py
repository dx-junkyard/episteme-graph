"""RhetoricalRoleAgent data models.

The output is a reviewable intermediate layer between paper-level skeleton
hypotheses and downstream claim extraction. All dataclasses are JSON
serializable.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from episteme_graph.agents.cartridge_context import CartridgeContext

ROLE_LABELS = [
    "definition",
    "observable_definition",
    "equation_definition",
    "relation",
    "equation_relation",
    "equation_transformation",
    "assumption",
    "approximation",
    "derivation_step",
    "correction",
    "uncertainty",
    "limitation",
    "result",
    "diagnostic_claim",
    "method_choice",
    "prior_work",
    "figure_narration",
    "table_narration",
    "section_meta",
    "meta_discourse",
    "citation_context",
    "background_general",
    "unknown",
]

CORE_CLAIM_ROLES = {
    "definition",
    "observable_definition",
    "equation_definition",
    "relation",
    "equation_relation",
    "equation_transformation",
    "assumption",
    "approximation",
    "derivation_step",
    "correction",
    "uncertainty",
    "limitation",
    "result",
    "diagnostic_claim",
    "method_choice",
}

EXCLUSION_ROLES = {
    "prior_work",
    "figure_narration",
    "table_narration",
    "section_meta",
    "meta_discourse",
    "citation_context",
    "background_general",
}


@dataclass
class RoleLLMInput:
    document_id: str
    cartridge_id: str | None
    block_id: str
    section_id: str | None
    section_title: str | None
    backbone_block_type: str | None
    headline_claim: str | None
    block_text: str
    prev_text: str | None
    next_text: str | None
    normalized_terms: list[dict] | None = None


@dataclass
class SpanAnnotation:
    span_id: str
    text: str
    char_start: int
    char_end: int
    role_labels: list[str]
    is_claim_candidate: bool
    is_reject_candidate: bool
    confidence: float
    reason: str


@dataclass
class BlockRoleAnnotation:
    block_id: str
    section_id: str | None
    backbone_block_type: str | None
    span_annotations: list[SpanAnnotation]


@dataclass
class ValidationIssue:
    rule_id: str
    severity: str
    message: str
    field: str | None = None


@dataclass
class RhetoricalRoleResult:
    document_id: str
    cartridge_id: str | None
    role_annotations: list[BlockRoleAnnotation]
    summary_stats: dict
    validation_issues: list[ValidationIssue] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_dict(cls, d: dict) -> "RhetoricalRoleResult":
        annotations = []
        for a in d.get("role_annotations", []):
            spans = [SpanAnnotation(**s) for s in a.get("span_annotations", [])]
            annotations.append(BlockRoleAnnotation(
                block_id=a["block_id"],
                section_id=a.get("section_id"),
                backbone_block_type=a.get("backbone_block_type"),
                span_annotations=spans,
            ))
        issues = [ValidationIssue(**i) for i in d.get("validation_issues", [])]
        return cls(
            document_id=d["document_id"],
            cartridge_id=d.get("cartridge_id"),
            role_annotations=annotations,
            summary_stats=d.get("summary_stats", {}),
            validation_issues=issues,
        )

    @classmethod
    def make_fallback(
        cls,
        document_id: str,
        cartridge_id: str | None,
        block_id: str | None,
        reason: str,
    ) -> "RhetoricalRoleResult":
        issues = [ValidationIssue(
            rule_id="role_generation_failed",
            severity="error",
            message=reason,
            field=block_id,
        )]
        return cls(
            document_id=document_id,
            cartridge_id=cartridge_id,
            role_annotations=[],
            summary_stats={"claim_candidate_spans": 0, "reject_candidate_spans": 0},
            validation_issues=issues,
        )
