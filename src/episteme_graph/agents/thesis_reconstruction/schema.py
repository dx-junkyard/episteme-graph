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
    # Claims dropped by the shared input limit (issue #356).
    excluded_from_pipeline_input: list[dict] = field(default_factory=list)


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
    # Issue #442: traversal-anchor fields so the artifact is the explicit goal of
    # graph traversal. central_question / headline_claim describe the thesis;
    # supporting_subclaim_ids are the claims that support it; anchor_node_ids are
    # the DSL graph nodes corresponding to the thesis (traversal entry/exit),
    # filled deterministically after DSL linking.
    central_question: str = ""
    headline_claim: str = ""
    supporting_subclaim_ids: list[str] = field(default_factory=list)
    anchor_node_ids: list[str] = field(default_factory=list)
    validation_issues: list[ValidationIssue] = field(default_factory=list)
    # Claims dropped from the LLM input by the shared selection policy
    # (issue #356): {claim_id, span_id, claim_tier, excluded_at_stage, reason,
    # referenced_by_thesis}. Recorded so the drop is explicit, never silent.
    excluded_from_pipeline_input: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def finalize_traversal_fields(
        self,
        *,
        central_question_hint: str | None = None,
        headline_claim_hint: str | None = None,
    ) -> None:
        """Populate central_question / headline_claim / supporting_subclaim_ids (#442).

        Deterministic and domain-agnostic: the headline claim is the central
        thesis text (falling back to the skeleton hint); the central question
        comes from the skeleton hint; supporting subclaim ids are gathered from
        the central thesis and every support_structure section. Never overwrites
        a non-empty value already present.
        """
        central = self.central_thesis if isinstance(self.central_thesis, dict) else {}
        if not self.headline_claim:
            self.headline_claim = str(
                central.get("text") or headline_claim_hint or ""
            )
        if not self.central_question:
            self.central_question = str(central_question_hint or "")

        subclaims: list[str] = list(self.supporting_subclaim_ids or [])
        seen = set(subclaims)
        for cid in central.get("claim_ids") or []:
            cid = str(cid or "")
            if cid and cid not in seen:
                seen.add(cid)
                subclaims.append(cid)
        if isinstance(self.support_structure, dict):
            for entries in self.support_structure.values():
                if not isinstance(entries, list):
                    continue
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    for cid in entry.get("claim_ids") or []:
                        cid = str(cid or "")
                        if cid and cid not in seen:
                            seen.add(cid)
                            subclaims.append(cid)
        self.supporting_subclaim_ids = subclaims

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
            central_question=str(d.get("central_question", "") or ""),
            headline_claim=str(d.get("headline_claim", "") or ""),
            supporting_subclaim_ids=list(d.get("supporting_subclaim_ids", []) or []),
            anchor_node_ids=list(d.get("anchor_node_ids", []) or []),
            validation_issues=issues,
            excluded_from_pipeline_input=d.get("excluded_from_pipeline_input", []),
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
