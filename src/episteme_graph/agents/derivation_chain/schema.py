"""DerivationChain data models."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field


DEFAULT_OPERATION = "transform"


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


@dataclass
class DerivationChainRecord:
    derivation_id: str
    document_id: str
    source_section_ids: list[str]
    steps: list[DerivationStep]
    teaching_takeaway: str = ""
    blackbox_policy_suggestion: dict = field(default_factory=dict)


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
            steps = [DerivationStep(**s) for s in c.get("steps", [])]
            chains.append(DerivationChainRecord(
                derivation_id=c["derivation_id"],
                document_id=c["document_id"],
                source_section_ids=list(c.get("source_section_ids", [])),
                steps=steps,
                teaching_takeaway=c.get("teaching_takeaway", ""),
                blackbox_policy_suggestion=c.get("blackbox_policy_suggestion", {}),
            ))
        issues = [ValidationIssue(**i) for i in d.get("validation_issues", [])]
        return cls(
            document_id=d["document_id"],
            cartridge_id=d.get("cartridge_id"),
            chains=chains,
            validation_issues=issues,
        )
