"""ClaimObjectBuilder data models."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

SUPPORT_STATUSES = [
    "source_backed",            # PDF 原文に裏付けがある
    "partially_source_backed",  # 一部のみ裏付けがある (issue #312)
    "derived",                  # 確定済みの他 Claim / Equation から導出された
    "inferred",                 # LLM 推論ベース、teacher review 必須
    "review_required",          # 確定不能。teacher review 必須 (issue #312)
    "external",                 # 引用文献からの主張
    "unknown",
]

# Atomicity vocabulary (issue #312 extends #260):
#   atomic        — 単一の最小命題
#   compound      — split_claims により atomic 子に分割済みの親
#   non_atomic    — 複数命題を含むが分割されていない（確定 claim にしない）
#   split_pending — 分割予定
ATOMICITY_VALUES = ["atomic", "compound", "non_atomic", "split_pending"]

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

# Claim types that represent the paper's main result / central conclusion.
# These must be atomic single propositions (issue #312): a non-atomic
# main-result claim is a paper-level summary and is a hard error downstream.
MAIN_RESULT_CLAIM_TYPES = {
    "result",
    "conclusion",
    "main_result",
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
    # Normalized single-proposition phrasing (issue #312). Mirrors `text` when no
    # separate normalization is available.
    normalized_text: str = ""
    # Atomicity support (issue #260 / #312)
    atomicity: str = "atomic"  # see ATOMICITY_VALUES
    # True only for confirmed single-proposition claims. Non-atomic / compound
    # parents are False so downstream graph nodes do not treat them as strong
    # atomic backing (issue #312, criterion #7).
    is_atomic: bool = True
    # Why a claim is not yet a confirmed atomic claim (issue #312).
    qualification_reason: str | None = None
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
                normalized_text=raw.get("normalized_text", "") or raw.get("text", ""),
                atomicity=raw.get("atomicity", "atomic"),
                is_atomic=bool(raw.get("is_atomic", raw.get("atomicity", "atomic") == "atomic")),
                qualification_reason=raw.get("qualification_reason"),
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
