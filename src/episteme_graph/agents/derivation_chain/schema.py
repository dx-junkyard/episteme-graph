"""DerivationChain data models.

Issue #261: equation-only chain を Claim/Evidence/Assumption を含む推論チェーンへ拡張。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field


DEFAULT_OPERATION = "transform"

# Domain-neutral operation ontology (issue #261)
OPERATION_ONTOLOGY = [
    "state_assumption",
    "apply_definition",
    "apply_criterion",
    "introduce_observable",
    "apply_equation",
    "substitute",
    "solve_linear_system",
    "eliminate_parameter",
    "normalize",
    "approximate",
    "compare",
    "branch_on_condition",
    "apply_measurement_or_update",
    "apply_non_disturbance_or_independence",
    "infer_intermediate_claim",
    "infer_conclusion",
    "flag_limitation",
    # Retained from existing ontology
    "define",
    "relate",
    "transform",
    "derive_result",
    "apply_constraint",
    # Generic transformation verbs (domain-neutral). Paper-specific operation
    # names must come from a cartridge, never from this core list (issues
    # #395 / #397).
    "linearize",
    "substitute",
    "eliminate",
    "solve",
    "normalize",
]

STEP_REVIEW_STATUSES = [
    "auto_accepted",
    "teacher_review_required",
    "needs_verification",
]


@dataclass
class DerivationStep:
    step_id: str
    input_equation_ids: list[str]
    operation: str
    output_equation_ids: list[str]
    required_claim_ids: list[str] = field(default_factory=list)
    assumption_refs: list[str] = field(default_factory=list)
    reason: str = ""
    confidence: float = 0.0
    # Extended fields for Claim/Evidence/Assumption (issue #261)
    input_claim_ids: list[str] = field(default_factory=list)
    output_claim_ids: list[str] = field(default_factory=list)
    assumption_ids: list[str] = field(default_factory=list)
    source_evidence_ids: list[str] = field(default_factory=list)
    review_status: str = "teacher_review_required"
    review_reason: str = ""
    confidence_gate: dict = field(default_factory=dict)
    eliminated_symbols: list[str] = field(default_factory=list)
    retained_symbols: list[str] = field(default_factory=list)


@dataclass
class DerivationChainRecord:
    derivation_id: str
    document_id: str
    source_section_ids: list[str]
    steps: list[DerivationStep]
    teaching_takeaway: str = ""
    blackbox_policy_suggestion: dict = field(default_factory=dict)
    # Extended fields (issue #261)
    source_scope: dict = field(default_factory=dict)
    linked_component_ids: list[str] = field(default_factory=list)
    chain_type: str = "equation_chain"  # "equation_chain" | "claim_chain" | "mixed_chain" | "system_level"
    review_status: str = "teacher_review_required"
    review_reason: str = ""
    confidence_gate: dict = field(default_factory=dict)
    # System-level derivation fields (issue #386). Populated only for
    # chain_type="system_level" derivations that represent an operation over a
    # *group* of equations (a solved/eliminated/constrained system) rather than a
    # simple adjacent equation-to-equation chain. Domain-neutral.
    operation: str = ""
    input_equation_ids: list[str] = field(default_factory=list)
    output_equation_ids: list[str] = field(default_factory=list)
    intermediate_equation_ids: list[str] = field(default_factory=list)
    input_claim_ids: list[str] = field(default_factory=list)
    output_claim_ids: list[str] = field(default_factory=list)
    assumption_ids: list[str] = field(default_factory=list)
    source_evidence_ids: list[str] = field(default_factory=list)
    transformed_symbols: list[str] = field(default_factory=list)
    eliminated_symbols: list[str] = field(default_factory=list)
    retained_symbols: list[str] = field(default_factory=list)
    conditions: list[str] = field(default_factory=list)
    confidence: float = 0.0
    review_required: bool = True
    review_reasons: list[str] = field(default_factory=list)


@dataclass
class ValidationIssue:
    rule_id: str
    severity: str
    message: str
    field: str | None = None


@dataclass
class DerivationChainResult:
    document_id: str
    cartridge_id: str | None
    chains: list[DerivationChainRecord] = field(default_factory=list)
    validation_issues: list[ValidationIssue] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_dict(cls, d: dict) -> "DerivationChainResult":
        chains = []
        for c in d.get("chains", []):
            steps = []
            for s in c.get("steps", []):
                steps.append(DerivationStep(
                    step_id=s.get("step_id", ""),
                    input_equation_ids=list(s.get("input_equation_ids") or []),
                    operation=s.get("operation", DEFAULT_OPERATION),
                    output_equation_ids=list(s.get("output_equation_ids") or []),
                    required_claim_ids=list(s.get("required_claim_ids") or []),
                    assumption_refs=list(s.get("assumption_refs") or []),
                    reason=s.get("reason", ""),
                    confidence=float(s.get("confidence", 0.0)),
                    input_claim_ids=list(s.get("input_claim_ids") or []),
                    output_claim_ids=list(s.get("output_claim_ids") or []),
                    assumption_ids=list(s.get("assumption_ids") or []),
                    source_evidence_ids=list(s.get("source_evidence_ids") or []),
                    review_status=s.get("review_status", "teacher_review_required"),
                    review_reason=s.get("review_reason", ""),
                    confidence_gate=s.get("confidence_gate") or {},
                    eliminated_symbols=list(s.get("eliminated_symbols") or []),
                    retained_symbols=list(s.get("retained_symbols") or []),
                ))
            chains.append(DerivationChainRecord(
                derivation_id=c["derivation_id"],
                document_id=c["document_id"],
                source_section_ids=list(c.get("source_section_ids", [])),
                steps=steps,
                teaching_takeaway=c.get("teaching_takeaway", ""),
                blackbox_policy_suggestion=c.get("blackbox_policy_suggestion", {}),
                source_scope=c.get("source_scope", {}),
                linked_component_ids=list(c.get("linked_component_ids") or []),
                chain_type=c.get("chain_type", "equation_chain"),
                review_status=c.get("review_status", "teacher_review_required"),
                review_reason=c.get("review_reason", ""),
                confidence_gate=c.get("confidence_gate") or {},
                operation=c.get("operation", ""),
                input_equation_ids=list(c.get("input_equation_ids") or []),
                output_equation_ids=list(c.get("output_equation_ids") or []),
                intermediate_equation_ids=list(c.get("intermediate_equation_ids") or []),
                input_claim_ids=list(c.get("input_claim_ids") or []),
                output_claim_ids=list(c.get("output_claim_ids") or []),
                assumption_ids=list(c.get("assumption_ids") or []),
                source_evidence_ids=list(c.get("source_evidence_ids") or []),
                transformed_symbols=list(c.get("transformed_symbols") or []),
                eliminated_symbols=list(c.get("eliminated_symbols") or []),
                retained_symbols=list(c.get("retained_symbols") or []),
                conditions=list(c.get("conditions") or []),
                confidence=float(c.get("confidence", 0.0) or 0.0),
                review_required=bool(c.get("review_required", True)),
                review_reasons=list(c.get("review_reasons") or []),
            ))
        issues = [ValidationIssue(**i) for i in d.get("validation_issues", [])]
        return cls(
            document_id=d["document_id"],
            cartridge_id=d.get("cartridge_id"),
            chains=chains,
            validation_issues=issues,
        )
