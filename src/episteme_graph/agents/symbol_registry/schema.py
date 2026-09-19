"""SymbolRegistryBuilder data models (issue #355).

A SymbolRecord makes a math symbol reusable outside its source paper: the
notation variants it appears under, where it is defined and used, its scope,
and (when known) its kind / unit / domain constraints. Unknown values are kept
as ``unknown`` / ``None`` / empty — never dropped.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from episteme_graph.agents.validation import ValidationIssue

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

# 候補が記号として登録されない理由（2026-09-19）。上流の used_symbols /
# defined_symbols には数値リテラル・語句・LaTeX 環境が混ざるため、書式だけで
# 判定して弾く。弾いた候補は捨てずに coverage.details.excluded に理由付きで残す
# （P4: 情報を落とさない）。分野語はハードコードしない。
EXCLUSION_REASONS = [
    "numeric_literal",      # 0.015 / 1 / 10^-2 / 2! / (3,2)
    "expression",           # (2π)^3 — 括弧で始まる式・組であって記号ではない
    "phrase",               # "radial derivative" / "angular variables / basis functions"
    "latex_environment",    # \begin{pmatrix} … \end{pmatrix}
    "document_reference",   # Eq. (3.7) / Equation 12 / Fig. 2 / Table 3
    "too_long",             # 40 字超
]

#: 記号候補の最大長（正規化後）。
MAX_SYMBOL_LENGTH = 40

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
class SymbolRegistryResult:
    document_id: str
    registry_version: str
    cartridge_id: str | None
    records: list[SymbolRecord]
    validation_issues: list[ValidationIssue] = field(default_factory=list)
    # 取りこぼし報告（coverage_report.py の共通形式）。population = 走査した
    # 候補キー数、processed = 登録した記号数、reasons = EXCLUSION_REASONS の
    # 部分列、details["excluded"] = 弾いた候補そのもの（raw / normalized /
    # reason / equation_ids）。
    coverage: dict = field(default_factory=dict)

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
            coverage=dict(d.get("coverage") or {}),
        )
