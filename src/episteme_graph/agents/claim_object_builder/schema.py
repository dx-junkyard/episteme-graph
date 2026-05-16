"""ClaimObjectBuilder data models."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

SUPPORT_STATUSES = [
    "source_backed",     # PDF 原文に裏付けがある
    "derived",           # 確定済みの他 Claim / Equation から導出された
    "inferred",          # LLM 推論ベース、teacher review 必須
    "external",          # 引用文献からの主張
    "unknown",
]

REVIEW_STATUSES = [
    "auto_accepted",
    "teacher_review_required",
    "rejected",
]

# Domain-neutral claim type ontology (issue #260)
CLAIM_TYPE_ONTOLOGY = [
    "definition",
    "criterion",
    "assumption",
    "approximation",
    "setup",
    "observable_definition",
    "operator_relation",
    "equation_definition",
    "equation_relation",
    "equation_transformation",
    "measurement_or_update",
    "causal_or_dependency_claim",
    "incompatibility_or_constraint",
    "comparison",
    "result",
    "conclusion",
    "limitation",
    "method_choice",
    "background",
    "prior_work",
    "meta",
    "unknown",
]

EQUATION_CLAIM_TYPES = {
    "equation_definition",
    "equation_relation",
    "equation_transformation",
    "derivation_step",
    "operator_relation",
    "measurement_or_update",
}


@dataclass
class ClaimConcept:
    name: str
    normalized: str
    concept_type: str = "unknown"
    role: str = "unknown"


@dataclass
class ClaimObjectRecord:
    claim_id: str
    document_id: str
    claim_type: str
    text: str
    source_evidence_ids: list[str]
    source_span_ids: list[str]
    concepts: list[ClaimConcept]
    equation_ids: list[str] = field(default_factory=list)
    figure_ids: list[str] = field(default_factory=list)
    table_ids: list[str] = field(default_factory=list)
    support_status: str = "source_backed"
    review_status: str = "teacher_review_required"
    review_note: str = ""
    section_id: str | None = None
    confidence: float = 0.0
    # Atomicity support (issue #260)
    atomicity: str = "atomic"  # "atomic" | "compound" | "split_pending"
    parent_claim_id: str | None = None
    subclaim_ids: list[str] = field(default_factory=list)


@dataclass
class ValidationIssue:
    rule_id: str
    severity: str
    message: str
    field: str | None = None


@dataclass
class ClaimObjectBuildResult:
    document_id: str
    cartridge_id: str | None
    claims: list[ClaimObjectRecord] = field(default_factory=list)
    validation_issues: list[ValidationIssue] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def index_by_id(self) -> dict[str, ClaimObjectRecord]:
        return {c.claim_id: c for c in self.claims}

    @classmethod
    def from_dict(cls, d: dict) -> "ClaimObjectBuildResult":
        claims = []
        for raw in d.get("claims", []):
            concepts = [
                ClaimConcept(
                    name=c.get("name", ""),
                    normalized=c.get("normalized", ""),
                    concept_type=c.get("concept_type", "unknown"),
                    role=c.get("role", "unknown"),
                )
                for c in raw.get("concepts", [])
            ]
            claims.append(ClaimObjectRecord(
                claim_id=raw["claim_id"],
                document_id=raw["document_id"],
                claim_type=raw["claim_type"],
                text=raw.get("text", ""),
                source_evidence_ids=list(raw.get("source_evidence_ids", [])),
                source_span_ids=list(raw.get("source_span_ids", [])),
                concepts=concepts,
                equation_ids=list(raw.get("equation_ids", [])),
                figure_ids=list(raw.get("figure_ids", [])),
                table_ids=list(raw.get("table_ids", [])),
                support_status=raw.get("support_status", "source_backed"),
                review_status=raw.get("review_status", "teacher_review_required"),
                review_note=raw.get("review_note", ""),
                section_id=raw.get("section_id"),
                confidence=float(raw.get("confidence", 0.0)),
                atomicity=raw.get("atomicity", "atomic"),
                parent_claim_id=raw.get("parent_claim_id"),
                subclaim_ids=list(raw.get("subclaim_ids", [])),
            ))
        issues = [ValidationIssue(**i) for i in d.get("validation_issues", [])]
        return cls(
            document_id=d["document_id"],
            cartridge_id=d.get("cartridge_id"),
            claims=claims,
            validation_issues=issues,
        )
