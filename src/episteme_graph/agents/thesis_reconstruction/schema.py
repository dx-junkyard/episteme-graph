"""ThesisReconstructionAgent data models."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

THESIS_VERSION = "v1"

SUPPORT_TYPES = [
    "problem_support",
    "derivation_support",
    "assumption_support",
    "correction_support",
    "uncertainty_support",
    "diagnostic_support",
    "future_requirement_support",
    "result_support",
]

GRAPH_RELATIONS = [
    "supported_by",
    "requires",
    "qualified_by",
    "limited_by",
    "diagnosed_by",
    "motivated_by",
    "made_applicable_by",
]

SUPPORT_SECTIONS = [
    "direct_supports",
    "assumptions",
    "derivation_core",
    "correction_sources",
    "uncertainty_sources",
    "diagnostic_consequences",
    "future_requirements",
]


@dataclass
class CartridgeContext:
    cartridge_id: str
    ontology: dict
    validation_rules: dict
    aliases: dict | None = None
    notation_patterns: list | None = None
    normalization_rules: list | None = None
    extraction_hints: list | None = None


@dataclass
class ThesisLLMInput:
    document_id: str
    cartridge_id: str | None
    paper_goal: str | None
    central_question: str | None
    headline_claim: str | None
    logical_blocks: list[dict]
    accepted_claims: list[dict]
    major_equations: list[dict]
    excluded_regions: list[dict]
    normalized_terms: list[dict] | None = None


@dataclass
class ThesisNode:
    text: str
    claim_ids: list[str]
    equation_ids: list[str]
    evidence_block_ids: list[str]
    reason: str
    confidence: float


@dataclass
class SupportEntry:
    text: str
    claim_ids: list[str]
    equation_ids: list[str]
    support_type: str
    reason: str
    confidence: float


@dataclass
class ValidationIssue:
    rule_id: str
    severity: str
    message: str
    field: str | None = None


@dataclass
class ThesisReconstructionResult:
    document_id: str
    thesis_version: str
    cartridge_id: str | None
    central_thesis: dict
    alternative_theses: list[dict]
    support_structure: dict
    excluded_from_core: list[dict]
    thesis_graph_hints: list[dict]
    review_notes: list[str]
    confidence: float
    validation_issues: list[ValidationIssue] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_dict(cls, d: dict) -> "ThesisReconstructionResult":
        issues = [ValidationIssue(**i) for i in d.get("validation_issues", [])]
        return cls(
            document_id=d["document_id"],
            thesis_version=d.get("thesis_version", THESIS_VERSION),
            cartridge_id=d.get("cartridge_id"),
            central_thesis=d.get("central_thesis", {}),
            alternative_theses=d.get("alternative_theses", []),
            support_structure=d.get("support_structure", {}),
            excluded_from_core=d.get("excluded_from_core", []),
            thesis_graph_hints=d.get("thesis_graph_hints", []),
            review_notes=d.get("review_notes", []),
            confidence=float(d.get("confidence", 0.0)),
            validation_issues=issues,
        )

    @classmethod
    def make_fallback(
        cls,
        document_id: str,
        cartridge_id: str | None,
        reason: str,
    ) -> "ThesisReconstructionResult":
        return cls(
            document_id=document_id,
            thesis_version=THESIS_VERSION,
            cartridge_id=cartridge_id,
            central_thesis={
                "text": "",
                "claim_ids": [],
                "equation_ids": [],
                "evidence_block_ids": [],
                "reason": reason,
                "confidence": 0.0,
            },
            alternative_theses=[],
            support_structure={section: [] for section in SUPPORT_SECTIONS},
            excluded_from_core=[],
            thesis_graph_hints=[],
            review_notes=[f"Thesis reconstruction failed: {reason}"],
            confidence=0.0,
            validation_issues=[ValidationIssue(
                rule_id="thesis_reconstruction_failed",
                severity="error",
                message=reason,
                field="central_thesis",
            )],
        )
