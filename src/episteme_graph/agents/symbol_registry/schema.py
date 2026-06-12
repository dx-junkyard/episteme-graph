"""SymbolRegistryBuilder data models (issue #355).

A SymbolRecord makes a math symbol reusable outside its source paper: the
notation variants it appears under, where it is defined and used, its scope,
and (when known) its kind / unit / domain constraints. Unknown values are kept
as ``unknown`` / ``None`` / empty — never dropped.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

SYMBOL_REGISTRY_VERSION = "v1"

SYMBOL_KINDS = [
    "parameter",
    "variable",
    "observable",
    "operator",
    "constant",
    "unknown",
]

SYMBOL_SCOPES = [
    "equation_local",
    "section",
    "document",
]

DEFINITION_STATUSES = [
    "defined",
    "used",
    "redefined",
    "unknown",
]

REVIEW_REASONS = [
    "symbol_redefinition",
    "definition_missing",
]

# Provisional enrichment markers (LLM never finalises maturity).
MATURITY_SOURCES = ["deterministic", "llm_proposed", "teacher_approved"]


@dataclass
class SymbolRecord:
    symbol_id: str
    document_id: str
    canonical_symbol: str
    notation_variants: list[str] = field(default_factory=list)
    kind: str = "unknown"
    # Unit / constraints stay None / empty when the source does not state them
    # (issue #355: keep unknown, never invent). LLM enrichment, when added,
    # must mark maturity_source="llm_proposed".
    unit: str | None = None
    domain_constraints: list[str] = field(default_factory=list)
    scope: str = "equation_local"
    defining_equation_ids: list[str] = field(default_factory=list)
    used_in_equation_ids: list[str] = field(default_factory=list)
    source_evidence_ids: list[str] = field(default_factory=list)
    definition_status: str = "unknown"
    # Raw definition quotes carried from DefinedSymbol.evidence_text.
    definition_evidence_texts: list[str] = field(default_factory=list)
    review_reasons: list[str] = field(default_factory=list)
    maturity_source: str = "deterministic"
    confidence: float = 0.0
    reason: str = ""


@dataclass
class ValidationIssue:
    rule_id: str
    severity: str
    message: str
    field: str | None = None


@dataclass
class SymbolRegistryResult:
    document_id: str
    registry_version: str
    cartridge_id: str | None
    records: list[SymbolRecord]
    validation_issues: list[ValidationIssue] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_dict(cls, d: dict) -> "SymbolRegistryResult":
        records = [SymbolRecord(**r) for r in d.get("records", [])]
        issues = [ValidationIssue(**i) for i in d.get("validation_issues", [])]
        return cls(
            document_id=d["document_id"],
            registry_version=d.get("registry_version", SYMBOL_REGISTRY_VERSION),
            cartridge_id=d.get("cartridge_id"),
            records=records,
            validation_issues=issues,
        )
