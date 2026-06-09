"""ClaimQualificationAgent data models."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

QUALIFICATION_STATUSES = ["accepted", "rejected", "deferred"]
CLAIM_TIERS = ["paper_core", "paper_supporting", "background", "prior_work", "meta"]
GRANULARITIES = ["good", "too_broad", "too_narrow"]
EVIDENCE_ADEQUACY = ["sufficient", "weak", "broken"]
REVIEWABILITY = ["good", "moderate", "poor"]

# Atomic rewrite vocabulary (issue #317). The ClaimQualificationAgent is the
# formal step that turns a long / paper-level / multi-proposition span into
# atomic claim candidates. Each candidate carries its own atomicity verdict:
#   atomic        — single minimal proposition (usable as confirmed backing)
#   non_atomic    — still mixes propositions; could not be atomized (review only)
ATOMIC_CLAIM_ATOMICITY = ["atomic", "non_atomic"]
# Per-atomic-claim status. ``review_required`` is used when the agent could not
# produce a confident single proposition and the candidate must be reviewed.
ATOMIC_CLAIM_STATUSES = ["accepted", "review_required"]

# Connectors / discourse markers that an atomic claim must NOT begin with
# (criterion #3): a fragment starting with these is not a standalone proposition.
ATOMIC_CLAIM_LEADING_CONNECTORS = {
    "and", "but", "or", "so", "then", "thus", "hence", "therefore",
    "moreover", "furthermore", "however", "whereas", "while", "also",
    "additionally", "besides", "nor", "yet", "because", "since",
}

CORE_CLAIM_TYPE_FALLBACKS = [
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
    "background",
    "prior_work",
    "meta",
    # issue #312 / #317 required vocabulary (kept compatible with the builder's
    # CLAIM_TYPE_ONTOLOGY) so atomic claims can be typed without collapsing to
    # "unknown".
    "problem_statement",
    "method",
    "structural_property",
    "derivation_result",
    "main_result",
    "interpretation",
    "conclusion",
    "comparison",
    "unknown",
]

EXCLUSION_ROLE_LABELS = {
    "figure_narration",
    "table_narration",
    "section_meta",
    "meta_discourse",
    "citation_context",
}


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
class QualificationLLMInput:
    document_id: str
    cartridge_id: str | None
    span_id: str
    block_id: str
    section_id: str | None
    section_title: str | None
    backbone_block_type: str | None
    headline_claim: str | None
    span_text: str
    role_labels: list[str]
    is_claim_candidate: bool
    is_reject_candidate: bool
    prev_span_text: str | None
    next_span_text: str | None
    source_block_text: str | None
    normalized_terms: list[dict] | None = None


@dataclass
class QualifiedSpanRecord:
    span_id: str
    block_id: str
    section_id: str | None
    text: str
    role_labels: list[str]
    qualification: dict
    edit_suggestions: dict
    reason: str
    confidence: float
    # Atomic claim candidates produced by the LLM atomic-rewrite step (issue #317).
    # Each entry is a dict with: text, normalized_text, claim_type_candidate,
    # atomicity, status, source_span_id, evidence_quote, qualification_reason,
    # confidence. Empty when the span is already atomic or no rewrite was needed.
    atomic_claims: list[dict] = field(default_factory=list)


@dataclass
class ValidationIssue:
    rule_id: str
    severity: str
    message: str
    field: str | None = None


@dataclass
class ClaimQualificationResult:
    document_id: str
    cartridge_id: str | None
    qualified_spans: list[QualifiedSpanRecord]
    rejected_spans: list[dict]
    deferred_spans: list[dict]
    summary_stats: dict
    validation_issues: list[ValidationIssue] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_dict(cls, d: dict) -> "ClaimQualificationResult":
        qualified = [QualifiedSpanRecord(**r) for r in d.get("qualified_spans", [])]
        issues = [ValidationIssue(**i) for i in d.get("validation_issues", [])]
        return cls(
            document_id=d["document_id"],
            cartridge_id=d.get("cartridge_id"),
            qualified_spans=qualified,
            rejected_spans=d.get("rejected_spans", []),
            deferred_spans=d.get("deferred_spans", []),
            summary_stats=d.get("summary_stats", {}),
            validation_issues=issues,
        )

    @classmethod
    def make_fallback(
        cls,
        document_id: str,
        cartridge_id: str | None,
        span_id: str | None,
        reason: str,
    ) -> "ClaimQualificationResult":
        issues = [ValidationIssue(
            rule_id="claim_qualification_failed",
            severity="error",
            message=reason,
            field=span_id,
        )]
        return cls(
            document_id=document_id,
            cartridge_id=cartridge_id,
            qualified_spans=[],
            rejected_spans=[],
            deferred_spans=[],
            summary_stats={
                "accepted": 0,
                "rejected": 0,
                "deferred": 0,
                "split_suggested": 0,
                "merge_suggested": 0,
            },
            validation_issues=issues,
        )
