"""EquationSemanticsAgent data models.

Issue #245: EquationCandidate / EquationExtraction / EquationReconstruction を分離し、
壊れた式候補を確定式として扱わない。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EXTRACTION_STATUSES = [
    "complete",
    "partial",
    "fragment_only",
    "label_only",
    "missing",
    "unparsed",
]

ACCEPTANCE_STATUSES = ["accepted", "rejected", "needs_merge", "context_only", "provisional"]

RECONSTRUCTION_STATUSES = [
    "none",
    "inferred_from_context",
    "reconstructed_from_neighbors",
    "manually_supplied",
]

SEMANTIC_STATUSES = [
    "source_backed",
    "context_inferred",
    "reconstruction_based",
    "unknown",
]

EQUATION_TYPES = [
    "definition",
    "relation",
    "transformation",
    "approximation",
    "result",
    "constraint",
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
    "needs_reconstruction",
    "reconstruction_only",
    "partial_extraction",
]

CANDIDATE_REVIEW_REASONS = [
    "label_only_candidate",
    "fragment_only_candidate",
    "needs_merge",
    "needs_math_review",
    "equation_body_missing",
    "unparsed_math",
    "ambiguous_math_text",
    "pdf_text_layer_untrusted",
    "inline_pdf_math_untrusted",
]

DETECTION_METHODS = [
    "document_structure_equation_block",
    "equation_number_pattern",
    "math_symbol_density",
    "inline_equation_heuristic",
]


# ---------------------------------------------------------------------------
# Cartridge context
# ---------------------------------------------------------------------------

@dataclass
class CartridgeContext:
    cartridge_id: str
    ontology: dict
    validation_rules: dict
    aliases: dict | None = None
    notation_patterns: list | None = None
    normalization_rules: list | None = None
    extraction_hints: list | None = None


# ---------------------------------------------------------------------------
# EquationCandidate — first-class artifact from document structure
# ---------------------------------------------------------------------------

@dataclass
class EquationCandidate:
    """PDF text layer / document structure から得た式候補。"""
    candidate_id: str
    document_id: str
    source_location: dict  # {page, section_id, block_id, span_start, span_end, bbox}
    raw_text: str
    matched_label: str | None
    detection_method: list[str]
    candidate_score: float
    extraction_status: str   # one of EXTRACTION_STATUSES
    acceptance_status: str   # one of ACCEPTANCE_STATUSES
    accepted_equation_id: str | None = None
    merge_target_hint: str | None = None
    needs_math_review: bool = False
    review_reason: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# NormalizedEquation — internal intermediate, used by normalizer
# ---------------------------------------------------------------------------

@dataclass
class NormalizedEquation:
    equation_id: str
    block_id: str
    section_id: str | None
    label: str | None
    text: str
    latex: str | None = None
    plain_text: str | None = None


# ---------------------------------------------------------------------------
# EquationLLMInput — input to LLM semantics analysis
# ---------------------------------------------------------------------------

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
    # Candidate metadata
    candidate_id: str | None = None
    extraction_status: str = "complete"
    acceptance_status: str = "accepted"
    needs_reconstruction: bool = False


# ---------------------------------------------------------------------------
# EquationSourceExtraction — source-backed extraction data
# ---------------------------------------------------------------------------

@dataclass
class EquationSourceExtraction:
    """PDF text layer から直接得られた式情報。"""
    raw_text: str
    latex: str | None
    plain_text: str | None
    source_location: dict  # {page, section_id, block_id, bbox}
    extraction_source: str  # e.g. "pdf_text_layer"
    extraction_status: str  # one of EXTRACTION_STATUSES
    needs_math_review: bool = False
    review_reason: list[str] = field(default_factory=list)
    source_image: dict | None = None  # cropped equation image for UI/OCR audit


# ---------------------------------------------------------------------------
# EquationReconstruction — context-supplemented equation data
# ---------------------------------------------------------------------------

@dataclass
class EquationReconstruction:
    """文脈から補完・推定した式情報。source_extracted と同一視してはならない。"""
    latex: str | None
    plain_text: str | None
    status: str  # one of RECONSTRUCTION_STATUSES
    method: list[str]
    supporting_refs: list[str]
    confidence: float
    review_required: bool
    review_reason: list[str] = field(default_factory=list)

    @classmethod
    def make_none(cls) -> "EquationReconstruction":
        return cls(
            latex=None,
            plain_text=None,
            status="none",
            method=[],
            supporting_refs=[],
            confidence=0.0,
            review_required=False,
            review_reason=[],
        )


# ---------------------------------------------------------------------------
# DefinedSymbol — reused in EquationSemantics
# ---------------------------------------------------------------------------

@dataclass
class DefinedSymbol:
    symbol: str
    definition_status: str
    evidence_text: str | None = None


# ---------------------------------------------------------------------------
# EquationSemantics — equation role / symbol / derivation
# ---------------------------------------------------------------------------

@dataclass
class EquationSemantics:
    """式の役割・記号・導出関係。"""
    equation_type: str           # one of EQUATION_TYPES
    secondary_types: list[str]   # additional equation types
    semantic_status: str         # one of SEMANTIC_STATUSES
    confidence: float
    reason: str
    defined_symbols: list[DefinedSymbol]
    used_symbols: list[str]
    assumptions: list[str]
    input_equation_ids: list[str]
    output_equation_ids: list[str]
    linked_text_spans: list[dict]
    source_evidence_ids: list[str]
    linked_claim_ids: list[str]
    summary: str
    review_flags: list[str]


# ---------------------------------------------------------------------------
# EquationConfidencePolicy — downstream usability
# ---------------------------------------------------------------------------

@dataclass
class EquationConfidencePolicy:
    """Claim / Derivation / Component / Course で使用可能かの方針。"""
    can_support_claim: bool
    can_be_used_in_derivation: bool
    can_be_displayed_in_course: bool
    display_requires_note: bool
    must_not_treat_as_source_extracted: bool

    @classmethod
    def derive(
        cls,
        source_extraction: EquationSourceExtraction,
        reconstruction: EquationReconstruction,
        semantics: EquationSemantics,
    ) -> "EquationConfidencePolicy":
        """extraction / reconstruction / semantics から決定論的に生成する。"""
        is_reconstruction_based = (
            semantics.semantic_status == "reconstruction_based"
            or reconstruction.status != "none"
            or source_extraction.needs_math_review
        )
        must_not = is_reconstruction_based
        can_claim = (
            not must_not
            and source_extraction.extraction_status == "complete"
            and not source_extraction.needs_math_review
            and semantics.semantic_status not in ("unknown", "reconstruction_based")
        )
        can_derivation = (
            not must_not
            or (
                reconstruction.status != "none"
                and reconstruction.confidence >= 0.7
                and semantics.semantic_status in ("reconstruction_based", "context_inferred")
            )
        )
        display_note = must_not or source_extraction.needs_math_review
        return cls(
            can_support_claim=can_claim,
            can_be_used_in_derivation=can_derivation,
            can_be_displayed_in_course=True,
            display_requires_note=display_note,
            must_not_treat_as_source_extracted=must_not,
        )


# ---------------------------------------------------------------------------
# EquationRecord — accepted equation with full layered structure
# ---------------------------------------------------------------------------

@dataclass
class EquationRecord:
    """確定済み式 (accepted candidate から生成)。"""
    equation_id: str
    document_id: str
    label: str | None
    candidate_trace_ids: list[str]
    source_extraction: EquationSourceExtraction
    reconstruction: EquationReconstruction
    semantics: EquationSemantics
    confidence_policy: EquationConfidencePolicy


# ---------------------------------------------------------------------------
# ValidationIssue
# ---------------------------------------------------------------------------

@dataclass
class ValidationIssue:
    rule_id: str
    severity: str
    message: str
    field: str | None = None


# ---------------------------------------------------------------------------
# EquationSemanticsResult
# ---------------------------------------------------------------------------

@dataclass
class EquationSemanticsResult:
    document_id: str
    cartridge_id: str | None
    equation_candidates: list[EquationCandidate]
    equations: list[EquationRecord]
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
        """equations.json first-class export 形式に変換する。

        Parameters
        ----------
        evidence_index:
            block_id -> [evidence_id, ...] のマップ（オプション）。
        claim_index:
            equation_id -> [claim_id, ...] のマップ（オプション）。
        """
        evidence_index = evidence_index or {}
        claim_index = claim_index or {}
        out: list[dict] = []
        for r in self.equations:
            sem = r.semantics
            src = r.source_extraction
            rec = r.reconstruction

            # latex / plain_text: trusted source_extraction only when available.
            # PDF-derived math marked needs_math_review is audit text, not display math;
            # expose reconstructed math separately and never fall back to raw_text.
            latex = None if src.needs_math_review else src.latex
            plain_text = None if src.needs_math_review else src.plain_text
            if rec.status != "none":
                latex = rec.latex
                plain_text = rec.plain_text

            # defined / used / introduced symbols
            defined = [s.symbol for s in sem.defined_symbols if s.definition_status in ("defined", "redefined")]
            introduced = [s.symbol for s in sem.defined_symbols if s.definition_status == "defined"]
            used = list(sem.used_symbols)

            block_id = src.source_location.get("block_id", "")
            section_id = src.source_location.get("section_id")

            out.append({
                "equation_id": r.equation_id,
                "document_id": r.document_id,
                "label": r.label,
                "raw_text": src.raw_text,
                "latex": latex,
                "plain_text": plain_text,
                "source_location": dict(src.source_location or {}),
                "source_image": dict(src.source_image or {}) if src.source_image else None,
                "extraction_source": src.extraction_source,
                "extraction_status": src.extraction_status,
                "review_reason": list(src.review_reason),
                "candidate_trace_ids": list(r.candidate_trace_ids),
                "reconstruction": asdict(rec),
                "equation_role": {
                    "primary": sem.equation_type,
                    "secondary": list(sem.secondary_types),
                },
                "semantic_kind": sem.summary or None,
                "equation_type": sem.equation_type,
                "secondary_types": list(sem.secondary_types),
                "semantic_status": sem.semantic_status,
                "confidence": sem.confidence,
                "introduced_symbols": introduced,
                "used_symbols": used,
                "defined_symbols": defined,
                "symbol_definitions": {
                    s.symbol: s.evidence_text
                    for s in sem.defined_symbols
                    if s.evidence_text
                },
                "local_assumptions": list(sem.assumptions),
                "assumptions": list(sem.assumptions),
                "derivation_links": {
                    "from_equations": list(sem.input_equation_ids),
                    "to_equations": list(sem.output_equation_ids),
                },
                "input_equation_ids": list(sem.input_equation_ids),
                "output_equation_ids": list(sem.output_equation_ids),
                "linked_claim_ids": sorted(set(sem.linked_claim_ids) | set(claim_index.get(r.equation_id, []))),
                "source_evidence_ids": sorted(set(sem.source_evidence_ids) | set(evidence_index.get(block_id, []))),
                "review_flags": list(sem.review_flags),
                "section_id": section_id,
                "needs_math_review": src.needs_math_review,
                "confidence_policy": asdict(r.confidence_policy),
            })
        return out

    @classmethod
    def from_dict(cls, d: dict) -> "EquationSemanticsResult":
        candidates = [_candidate_from_dict(c) for c in d.get("equation_candidates", [])]
        equations = [_record_from_dict(r) for r in d.get("equations", [])]
        issues = [ValidationIssue(**i) for i in d.get("validation_issues", [])]
        return cls(
            document_id=d["document_id"],
            cartridge_id=d.get("cartridge_id"),
            equation_candidates=candidates,
            equations=equations,
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
            equation_candidates=[],
            equations=[],
            validation_issues=[ValidationIssue(
                rule_id="equation_semantics_failed",
                severity="error",
                message=reason,
                field=block_id,
            )],
        )


# ---------------------------------------------------------------------------
# Private deserialization helpers
# ---------------------------------------------------------------------------

def _candidate_from_dict(d: dict) -> EquationCandidate:
    return EquationCandidate(
        candidate_id=d["candidate_id"],
        document_id=d["document_id"],
        source_location=d.get("source_location", {}),
        raw_text=d.get("raw_text", ""),
        matched_label=d.get("matched_label"),
        detection_method=list(d.get("detection_method", [])),
        candidate_score=float(d.get("candidate_score", 0.0)),
        extraction_status=d.get("extraction_status", "unparsed"),
        acceptance_status=d.get("acceptance_status", "rejected"),
        accepted_equation_id=d.get("accepted_equation_id"),
        merge_target_hint=d.get("merge_target_hint"),
        needs_math_review=bool(d.get("needs_math_review", False)),
        review_reason=list(d.get("review_reason", [])),
    )


def _record_from_dict(d: dict) -> EquationRecord:
    src_raw = d.get("source_extraction", {})
    source_extraction = EquationSourceExtraction(
        raw_text=src_raw.get("raw_text", ""),
        latex=src_raw.get("latex"),
        plain_text=src_raw.get("plain_text"),
        source_location=src_raw.get("source_location", {}),
        extraction_source=src_raw.get("extraction_source", "pdf_text_layer"),
        extraction_status=src_raw.get("extraction_status", "unparsed"),
        needs_math_review=bool(src_raw.get("needs_math_review", False)),
        review_reason=list(src_raw.get("review_reason", [])),
        source_image=src_raw.get("source_image") if isinstance(src_raw.get("source_image"), dict) else None,
    )
    rec_raw = d.get("reconstruction", {})
    reconstruction = EquationReconstruction(
        latex=rec_raw.get("latex"),
        plain_text=rec_raw.get("plain_text"),
        status=rec_raw.get("status", "none"),
        method=list(rec_raw.get("method", [])),
        supporting_refs=list(rec_raw.get("supporting_refs", [])),
        confidence=float(rec_raw.get("confidence", 0.0)),
        review_required=bool(rec_raw.get("review_required", False)),
        review_reason=list(rec_raw.get("review_reason", [])),
    )
    sem_raw = d.get("semantics", {})
    semantics = EquationSemantics(
        equation_type=sem_raw.get("equation_type", "unknown"),
        secondary_types=list(sem_raw.get("secondary_types", [])),
        semantic_status=sem_raw.get("semantic_status", "unknown"),
        confidence=float(sem_raw.get("confidence", 0.5)),
        reason=str(sem_raw.get("reason", "")),
        defined_symbols=[
            DefinedSymbol(**s) for s in sem_raw.get("defined_symbols", [])
        ],
        used_symbols=list(sem_raw.get("used_symbols", [])),
        assumptions=list(sem_raw.get("assumptions", [])),
        input_equation_ids=list(sem_raw.get("input_equation_ids", [])),
        output_equation_ids=list(sem_raw.get("output_equation_ids", [])),
        linked_text_spans=list(sem_raw.get("linked_text_spans", [])),
        source_evidence_ids=list(sem_raw.get("source_evidence_ids", [])),
        linked_claim_ids=list(sem_raw.get("linked_claim_ids", [])),
        summary=str(sem_raw.get("summary", "")),
        review_flags=list(sem_raw.get("review_flags", [])),
    )
    cp_raw = d.get("confidence_policy", {})
    confidence_policy = EquationConfidencePolicy(
        can_support_claim=bool(cp_raw.get("can_support_claim", False)),
        can_be_used_in_derivation=bool(cp_raw.get("can_be_used_in_derivation", False)),
        can_be_displayed_in_course=bool(cp_raw.get("can_be_displayed_in_course", True)),
        display_requires_note=bool(cp_raw.get("display_requires_note", True)),
        must_not_treat_as_source_extracted=bool(cp_raw.get("must_not_treat_as_source_extracted", True)),
    )
    return EquationRecord(
        equation_id=d["equation_id"],
        document_id=d["document_id"],
        label=d.get("label"),
        candidate_trace_ids=list(d.get("candidate_trace_ids", [])),
        source_extraction=source_extraction,
        reconstruction=reconstruction,
        semantics=semantics,
        confidence_policy=confidence_policy,
    )
