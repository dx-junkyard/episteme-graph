"""ClaimQualificationAgent data models."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from episteme_graph.agents.validation import ValidationIssue

from episteme_graph.agents.cartridge_context import CartridgeContext

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
# This ordered tuple is the single source: the validator checks against it and
# the prompt / repair message enumerate it verbatim (do not restate a subset).
ATOMIC_CLAIM_LEADING_CONNECTOR_WORDS: tuple[str, ...] = (
    "and", "but", "or", "nor", "yet", "so", "then", "thus", "hence", "therefore",
    "because", "since", "moreover", "furthermore", "however", "whereas", "while",
    "also", "additionally", "besides",
)
ATOMIC_CLAIM_LEADING_CONNECTORS = frozenset(ATOMIC_CLAIM_LEADING_CONNECTOR_WORDS)


def leading_connector_list_text() -> str:
    """Slash-separated enumeration of every forbidden leading connector."""
    return " / ".join(ATOMIC_CLAIM_LEADING_CONNECTOR_WORDS)


# How to rewrite a causal span ("Because X, Y") without a leading connector.
CAUSAL_REWRITE_GUIDANCE = (
    'A causal span such as "Because X, Y" must not keep the leading connector: '
    'restate it subject-first as one relation claim ("Y because X" / "X causes Y"), '
    "optionally also emitting X as its own atomic claim. Never move X into "
    "qualification_reason (that drops content). If it cannot be restated, keep it "
    'with atomicity="non_atomic" and status="review_required".'
)


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


# IK-0803: rhetorical_role が is_reject_candidate=True を付け、かつ役割が主張になり得ない
# 語彙（書式・節見出し・メタ言説・引用の文脈）だけの span は、LLM を呼ばず決定論で
# rejected として記録する（結果の決まった呼び出しで予算を食わない）。prior_work /
# figure_narration / table_narration / background_general は主張を含み得るので含めない
# （先行研究の主張は claim_tier="prior_work" で残す規律 — #317 の情報を落とさない）。
NON_CLAIMABLE_ROLE_LABELS = frozenset({
    "section_meta",
    "meta_discourse",
    "citation_context",
})
DETERMINISTIC_REJECT_REASON = "reject_candidate_from_rhetorical_role"


def is_deterministic_reject(
    role_labels, is_claim_candidate: bool, is_reject_candidate: bool
) -> bool:
    """True when the span can be rejected without an LLM call (IK-0803)."""
    labels = set(role_labels or [])
    return (
        bool(is_reject_candidate)
        and not is_claim_candidate
        and bool(labels)
        and labels <= NON_CLAIMABLE_ROLE_LABELS
    )


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
    # rhetorical_role が付けた span の confidence（決定論 reject の記録に引き継ぐ。IK-0803）
    role_confidence: float | None = None
    # 対象ブロックに隣接する同じ小節の式（前 1・後 1。IK-0714 claim_qualification 側）。
    # 各要素 = {position: before|after, block_id, label, printed_number, latex}。
    # latex は TeX 由来の式だけ（PDF 由来は None — 信頼境界 TB1）。
    adjacent_equations: list[dict] | None = None


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
    # context_refs (structured intra-document references, issue #357),
    # confidence. Empty when the span is already atomic or no rewrite was needed.
    atomic_claims: list[dict] = field(default_factory=list)



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
