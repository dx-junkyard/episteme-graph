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

# Atomicity vocabulary (issue #359: single source of truth):
#   atomic         — single minimal proposition
#   composite      — parent claim split into atomic children
#   split_required — multiple propositions, not usable as component backing
# Legacy aliases are converted to the canonical values at read time via
# normalize_atomicity(); freshly built artifacts only ever emit canonical values.
CANONICAL_ATOMICITY_VALUES = [
    "atomic",
    "composite",
    "split_required",
]

# legacy value (older artifacts / ClaimQualification vocabulary) → canonical.
ATOMICITY_ALIASES = {
    "compound": "composite",
    "non_atomic": "split_required",
    "split_pending": "split_required",
}

# Accepted on read / export validation: canonical + legacy aliases.
ATOMICITY_VALUES = CANONICAL_ATOMICITY_VALUES + list(ATOMICITY_ALIASES)


def normalize_atomicity(value: object) -> str:
    """Map any accepted atomicity value to the canonical vocabulary (issue #359)."""
    raw = str(value or "atomic").strip().lower()
    if raw in CANONICAL_ATOMICITY_VALUES:
        return raw
    return ATOMICITY_ALIASES.get(raw, "atomic")


REVIEW_STATUSES = [
    "auto_accepted",
    "teacher_review_required",
    "rejected",
]

# ClaimQualificationAgent's evidence_adequacy is a qualification-stage
# intermediate signal; ClaimObjectRecord.support_status is the canonical
# downstream vocabulary (issue #359). This is the single mapping between the
# two — do not re-derive it inline.
EVIDENCE_ADEQUACY_TO_SUPPORT_STATUS = {
    "sufficient": "source_backed",
    "weak": "partially_source_backed",
    "broken": "review_required",
}


def derive_support_status(has_evidence: bool, evidence_adequacy: object = None) -> str:
    """Canonical support_status from qualification-stage evidence signals.

    Without an evidence link the claim is at most ``inferred`` regardless of
    adequacy. With evidence, adequacy caps the strength: sufficient →
    source_backed / weak → partially_source_backed / broken → review_required.
    Missing adequacy keeps the historical default (source_backed).
    """
    if not has_evidence:
        return "inferred"
    key = str(evidence_adequacy or "").strip().lower()
    return EVIDENCE_ADEQUACY_TO_SUPPORT_STATUS.get(key, "source_backed")

# Concept assignment status vocabulary (issue #8):
#   source_backed    — atomic, source-backed claim with concepts from the cartridge
#   inferred         — concepts only from domain fallbacks, not cartridge ontology
#   tentative        — composite / split_required (non-atomic) claim; do not confirm
#   review_required  — low-confidence / review_required source; teacher review needed
CONCEPT_ASSIGNMENT_STATUSES = [
    "source_backed",
    "inferred",
    "tentative",
    "review_required",
]

# Domain-neutral claim type ontology (issue #260, extended for #312).
# The #312 required vocabulary (problem_statement / method / structural_property /
# derivation_result / main_result / interpretation / limitation) is included so
# those candidates normalize to themselves instead of collapsing to "unknown".
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
    # issue #312 required claim types
    "problem_statement",
    "method_motivation",
    "theory_encoding",
    "method",
    "structural_property",
    "derivation_result",
    "main_result",
    "interpretation",
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
    # Equation links demoted to inferred (#358): the equation does not link
    # this claim back, so downstream stages must not consume the link as a
    # confirmed one. The id is kept here, never dropped.
    inferred_equation_ids: list[str] = field(default_factory=list)
    figure_ids: list[str] = field(default_factory=list)
    table_ids: list[str] = field(default_factory=list)
    support_status: str = "source_backed"
    review_status: str = "teacher_review_required"
    review_note: str = ""
    section_id: str | None = None
    # Human-readable section title (issue #359) so a claim can be read on its
    # own ("in the methods" resolves without the DocumentStructure artifact).
    section_title: str | None = None
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
    split_suggestions: list[dict] = field(default_factory=list)
    linked_component_ids: list[str] = field(default_factory=list)
    # How confidently the concepts on this claim may be used downstream (issue #8).
    # See CONCEPT_ASSIGNMENT_STATUSES. Non-atomic / low-confidence claims are never
    # confirmed source_backed so the graph / course mapping do not treat their
    # concepts as strong backing.
    concept_assignment_status: str = "review_required"
    # Deterministic content hash for cross-paper matching (issue #362).
    # "" / 0 on legacy artifacts that predate hashing.
    content_hash: str = ""
    content_hash_version: int = 0


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
                inferred_equation_ids=list(raw.get("inferred_equation_ids", [])),
                figure_ids=list(raw.get("figure_ids", [])),
                table_ids=list(raw.get("table_ids", [])),
                support_status=raw.get("support_status", "source_backed"),
                review_status=raw.get("review_status", "teacher_review_required"),
                review_note=raw.get("review_note", ""),
                section_id=raw.get("section_id"),
                section_title=raw.get("section_title"),
                confidence=float(raw.get("confidence", 0.0)),
                normalized_text=raw.get("normalized_text", "") or raw.get("text", ""),
                # Legacy aliases (compound / non_atomic / split_pending) are
                # converted to canonical values on read (issue #359).
                atomicity=normalize_atomicity(raw.get("atomicity", "atomic")),
                is_atomic=bool(raw.get("is_atomic", raw.get("atomicity", "atomic") == "atomic")),
                qualification_reason=raw.get("qualification_reason"),
                parent_claim_id=raw.get("parent_claim_id"),
                subclaim_ids=list(raw.get("subclaim_ids", [])),
                split_suggestions=list(raw.get("split_suggestions", [])),
                linked_component_ids=list(raw.get("linked_component_ids", [])),
                concept_assignment_status=raw.get(
                    "concept_assignment_status", "review_required"
                ),
                content_hash=str(raw.get("content_hash", "") or ""),
                content_hash_version=int(raw.get("content_hash_version", 0) or 0),
            ))
        issues = [ValidationIssue(**i) for i in d.get("validation_issues", [])]
        return cls(
            document_id=d["document_id"],
            cartridge_id=d.get("cartridge_id"),
            claims=claims,
            validation_issues=issues,
        )
