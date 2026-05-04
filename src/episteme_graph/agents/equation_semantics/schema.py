"""EquationSemanticsAgent data models."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

EQUATION_ROLES = [
    "equation_definition",
    "equation_relation",
    "equation_transformation",
    "equation_approximation",
    "equation_result",
    "equation_constraint",
    "unknown",
]

DEFINITION_STATUSES = ["defined", "used", "redefined", "unknown"]

LINKED_TEXT_RELATIONS = [
    "introduced_by",
    "explained_by",
    "derived_from_text",
    "qualified_by",
    "normalized_by_text",
]

REVIEW_FLAGS = [
    "broken_context",
    "missing_label",
    "ambiguous_role",
    "symbol_extraction_uncertain",
    "weak_derivation_link",
    "low_confidence",
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
class NormalizedEquation:
    equation_id: str
    block_id: str
    section_id: str | None
    label: str | None
    text: str
    latex: str | None = None
    plain_text: str | None = None


@dataclass
class EquationLLMInput:
    document_id: str
    cartridge_id: str | None
    equation_id: str
    block_id: str
    section_id: str | None
    section_title: str | None
    backbone_block_type: str | None
    label: str | None
    equation_text: str
    latex: str | None
    plain_text: str | None
    prev_texts: list[str]
    next_texts: list[str]
    nearby_span_annotations: list[dict]
    normalized_terms: list[dict] | None = None


@dataclass
class EquationRolePrediction:
    primary: str
    secondary: list[str]
    confidence: float
    reason: str


@dataclass
class DefinedSymbol:
    symbol: str
    definition_status: str
    evidence_text: str | None = None


@dataclass
class LocalAssumption:
    text: str
    source_block_ids: list[str]


@dataclass
class DerivationLinks:
    from_equations: list[str]
    to_equations: list[str]
    linked_text_spans: list[dict]


@dataclass
class EquationSemanticsRecord:
    equation_id: str
    block_id: str
    section_id: str | None
    section_title: str | None
    label: str | None
    text: str
    latex: str | None
    plain_text: str | None
    equation_role: EquationRolePrediction
    defined_symbols: list[DefinedSymbol]
    local_assumptions: list[LocalAssumption]
    derivation_links: DerivationLinks
    summary: str
    review_flags: list[str]


@dataclass
class ValidationIssue:
    rule_id: str
    severity: str
    message: str
    field: str | None = None


@dataclass
class EquationSemanticsResult:
    document_id: str
    cartridge_id: str | None
    equations: list[EquationSemanticsRecord]
    validation_issues: list[ValidationIssue] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def to_equations_export(
        self,
        *,
        evidence_index: dict | None = None,
        claim_index: dict | None = None,
    ) -> list[dict]:
        """Return equations.json first-class export shape.

        各 record を Issue #237 で定義された equations.json の形式に変換する。

        Parameters
        ----------
        evidence_index:
            block_id -> [evidence_id, ...] のマップ（オプション）。
            指定があれば source_evidence_ids に展開する。
        claim_index:
            equation_id -> [claim_id, ...] のマップ（オプション）。
            指定があれば linked_claim_ids に展開する。
        """
        evidence_index = evidence_index or {}
        claim_index = claim_index or {}
        out: list[dict] = []
        for r in self.equations:
            primary_role = r.equation_role.primary if r.equation_role else "unknown"
            secondary = list(r.equation_role.secondary) if r.equation_role else []
            used = [s.symbol for s in r.defined_symbols if s.definition_status == "used"]
            defined = [s.symbol for s in r.defined_symbols if s.definition_status in ("defined", "redefined")]
            introduced = [s.symbol for s in r.defined_symbols if s.definition_status == "defined"]
            local_assumptions = [a.text for a in r.local_assumptions]
            derivation_links = {
                "from_equations": list(r.derivation_links.from_equations) if r.derivation_links else [],
                "to_equations": list(r.derivation_links.to_equations) if r.derivation_links else [],
            }
            out.append({
                "equation_id": r.equation_id,
                "document_id": self.document_id,
                "label": r.label,
                "latex": r.latex,
                "plain_text": r.plain_text or r.text,
                "equation_role": {
                    "primary": primary_role,
                    "secondary": secondary,
                },
                "semantic_kind": r.summary or None,
                "introduced_symbols": introduced,
                "used_symbols": used,
                "defined_symbols": defined,
                "local_assumptions": local_assumptions,
                "derivation_links": derivation_links,
                "linked_claim_ids": list(claim_index.get(r.equation_id, [])),
                "source_evidence_ids": list(evidence_index.get(r.block_id, [])),
                "review_flags": list(r.review_flags),
                "section_id": r.section_id,
            })
        return out

    @classmethod
    def from_dict(cls, d: dict) -> "EquationSemanticsResult":
        records = []
        for raw in d.get("equations", []):
            records.append(EquationSemanticsRecord(
                equation_id=raw["equation_id"],
                block_id=raw["block_id"],
                section_id=raw.get("section_id"),
                section_title=raw.get("section_title"),
                label=raw.get("label"),
                text=raw.get("text", ""),
                latex=raw.get("latex"),
                plain_text=raw.get("plain_text"),
                equation_role=EquationRolePrediction(**raw.get("equation_role", {})),
                defined_symbols=[
                    DefinedSymbol(**s) for s in raw.get("defined_symbols", [])
                ],
                local_assumptions=[
                    LocalAssumption(**a) for a in raw.get("local_assumptions", [])
                ],
                derivation_links=DerivationLinks(**raw.get("derivation_links", {
                    "from_equations": [],
                    "to_equations": [],
                    "linked_text_spans": [],
                })),
                summary=raw.get("summary", ""),
                review_flags=raw.get("review_flags", []),
            ))
        issues = [ValidationIssue(**i) for i in d.get("validation_issues", [])]
        return cls(
            document_id=d["document_id"],
            cartridge_id=d.get("cartridge_id"),
            equations=records,
            validation_issues=issues,
        )

    @classmethod
    def make_fallback(
        cls,
        document_id: str,
        cartridge_id: str | None,
        block_id: str | None,
        reason: str,
    ) -> "EquationSemanticsResult":
        return cls(
            document_id=document_id,
            cartridge_id=cartridge_id,
            equations=[],
            validation_issues=[ValidationIssue(
                rule_id="equation_semantics_failed",
                severity="error",
                message=reason,
                field=block_id,
            )],
        )
