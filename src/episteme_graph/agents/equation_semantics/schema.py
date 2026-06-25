"""EquationSemanticsAgent data models.

Issue #245: EquationCandidate / EquationExtraction / EquationReconstruction を分離し、
壊れた式候補を確定式として扱わない。
"""
from __future__ import annotations

import json
import re
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

# Issue #432: inter-equation links must be derived from structural cues, not
# hardcoded paper-specific pairs. Every derived link records how it was found.
LINK_PROVENANCE_KINDS = [
    "textual_reference",   # the equation text references another equation's label
    "shared_symbol",       # uses a symbol another equation defines (symbol registry)
    "derivation_step",     # an upstream-declared derivation link that resolves
]

# Per-equation status describing why a result/relation equation may legitimately
# carry no input links. ``derived`` = links present; ``unresolved`` = no input
# and no justification (a defect the gate flags).
LINK_STATUSES = ["derived", "axiomatic", "external_reference", "unresolved"]

# result / relation equations with no input_equation_ids are only acceptable
# when explicitly justified by one of these statuses (issue #432).
ALLOWED_NO_INPUT_LINK_STATUSES = {"axiomatic", "external_reference"}

ALLOWED_DOWNSTREAM_USES = [
    "blocked",
    "semantic_hint_only",
    "display_with_warning",
    "unrestricted",
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
    # Issue #358: equation links a claim that does not link the equation back.
    "claim_link_asymmetry",
    # Issue #368: an I/O derivation link referenced an id that is not a member
    # of the document's final equation set (block id / candidate id / dangling).
    "nonequation_id_in_links",
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
    # Issue #368: PDF parser / GROBID supplied an equation_label that does not
    # match the allowed label patterns; the label was normalized to None.
    "invalid_equation_label",
    # Issue #368: the candidate originates from a table / table-cell and must
    # not be auto-confirmed as a standalone independent equation.
    "table_derived_equation_candidate",
    # Issue #416: an accepted/provisional candidate produced no EquationRecord
    # (e.g. its source block could not be resolved). A reasoned provisional
    # record is created instead of silently dropping the candidate.
    "candidate_dropped_before_record",
]

# ---------------------------------------------------------------------------
# Issue #368: fidelity guard reason codes.
#
# These deterministic, post-validation reason codes identify *specific* PDF
# equation reconstruction quality problems on top of the existing blanket
# needs_math_review / equation_consistency / confidence_policy safety gate.
# They are aggregated per-type by the ExportValidationGate.
# ---------------------------------------------------------------------------

# A reconstructed LaTeX whose body is prose (paraphrase of surrounding text)
# rather than an actual mathematical expression. Stored in reconstruction /
# equation_consistency review_reason.
FIDELITY_LATEX_IS_PROSE = "latex_is_prose"
# A PDF-supplied equation label that was rejected and normalized to None.
# Stored in candidate / source_extraction review_reason.
FIDELITY_INVALID_LABEL = "invalid_equation_label"
# A candidate originating from a table that must not auto-confirm. Stored in
# candidate review_reason.
FIDELITY_TABLE_DERIVED = "table_derived_equation_candidate"
# A non-equation id (block/candidate/dangling) found in I/O derivation links.
# Stored in semantics.review_flags.
FIDELITY_NONEQUATION_ID = "nonequation_id_in_links"
# Symbol loss between raw text and reconstruction that cannot be verified
# deterministically (image / OCR provenance unavailable). Stored in
# equation_consistency review_reason.
FIDELITY_SYMBOL_LOSS_UNVERIFIABLE = "symbol_loss_unverifiable"

FIDELITY_REVIEW_CODES = [
    FIDELITY_LATEX_IS_PROSE,
    FIDELITY_INVALID_LABEL,
    FIDELITY_TABLE_DERIVED,
    FIDELITY_NONEQUATION_ID,
    FIDELITY_SYMBOL_LOSS_UNVERIFIABLE,
]

DETECTION_METHODS = [
    "document_structure_equation_block",
    "equation_number_pattern",
    "math_symbol_density",
    "inline_equation_heuristic",
    "tex_source",
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
    # Issue #368: source-aware label validity. When a PDF-supplied label fails
    # the allowed patterns it is normalized to label=None, label_is_valid=False,
    # and the original value is preserved in rejected_label for audit.
    label_is_valid: bool = True
    rejected_label: str | None = None


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
    extraction_source: str = "pdf_text_layer"
    source_is_trusted: bool = False


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
# EquationConsistency — raw/source ↔ reconstructed math consistency
# ---------------------------------------------------------------------------

@dataclass
class EquationConsistency:
    raw_text_latex_match: str
    label_location_match: str
    symbol_overlap_score: float
    source_span_quality: str
    review_required: bool
    review_reason: list[str] = field(default_factory=list)

    @classmethod
    def derive(
        cls,
        *,
        source_extraction: EquationSourceExtraction,
        reconstruction: EquationReconstruction,
        label: str | None,
        semantics: "EquationSemantics",
    ) -> "EquationConsistency":
        raw_text = source_extraction.raw_text or ""
        latex = (
            reconstruction.latex
            if reconstruction.status != "none" and reconstruction.latex
            else source_extraction.latex
        ) or ""

        raw_symbols = _math_symbol_set(raw_text)
        latex_symbols = _math_symbol_set(latex)
        if raw_symbols and latex_symbols:
            overlap = len(raw_symbols & latex_symbols) / max(len(raw_symbols | latex_symbols), 1)
            raw_latex_match = "mismatch" if overlap < 0.2 else "match"
        else:
            overlap = 0.0
            raw_latex_match = "uncertain"

        review_reason: list[str] = []
        if raw_latex_match == "mismatch":
            review_reason.append("raw_text_latex_symbol_mismatch")
        elif raw_latex_match == "uncertain":
            review_reason.append("raw_text_latex_symbol_overlap_uncertain")

        raw_numbers = set(re.findall(r"\((\d+[A-Za-z]?)\)", raw_text))
        label_text = str(label or "").strip()
        label_number = label_text.strip("()") if label_text else ""
        if raw_numbers and label_number:
            label_match = "match" if label_number in raw_numbers else "mismatch"
        elif raw_numbers or label_number:
            label_match = "uncertain"
        else:
            label_match = "uncertain"
        if label_match == "mismatch":
            review_reason.append("equation_number_conflicts_with_label")

        if _theory_family_conflict(raw_text, latex):
            raw_latex_match = "mismatch"
            review_reason.append("raw_text_latex_theory_family_conflict")

        src_loc = source_extraction.source_location or {}
        has_block = bool(src_loc.get("block_id"))
        if not raw_text.strip() or not has_block:
            source_span_quality = "corrupted"
            review_reason.append("source_span_missing_or_empty")
        elif (
            source_extraction.extraction_status in ("partial", "fragment_only", "label_only", "unparsed", "missing")
            or source_extraction.needs_math_review
            or raw_latex_match == "uncertain"
        ):
            source_span_quality = "partial"
        else:
            source_span_quality = "clean"

        section_id = str(src_loc.get("section_id") or "").lower()
        semantic_text = " ".join([
            semantics.equation_type or "",
            semantics.summary or "",
            semantics.reason or "",
        ]).lower()
        if section_id and semantic_text:
            if "appendix" in section_id and any(t in semantic_text for t in ("main result", "central result")):
                review_reason.append("source_location_section_semantic_kind_conflict")
                label_match = "mismatch" if label_match == "match" else label_match

        review_required = (
            raw_latex_match != "match"
            or label_match == "mismatch"
            or source_span_quality != "clean"
        )
        return cls(
            raw_text_latex_match=raw_latex_match,
            label_location_match=label_match,
            symbol_overlap_score=round(overlap, 3),
            source_span_quality=source_span_quality,
            review_required=review_required,
            review_reason=_dedupe_text(review_reason),
        )


# ---------------------------------------------------------------------------
# DefinedSymbol — reused in EquationSemantics
# ---------------------------------------------------------------------------

@dataclass
class DefinedSymbol:
    symbol: str
    definition_status: str
    evidence_text: str | None = None
    # Reference into the document SymbolRegistry (issue #355). Optional for
    # backward compatibility; set by SymbolRegistryBuilder when it annotates.
    symbol_id: str | None = None


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
    # Claim links demoted to inferred (#358): the claim does not link this
    # equation back, so the link is moved out of linked_claim_ids and kept
    # here instead of being consumed downstream as a confirmed link.
    inferred_claim_ids: list[str] = field(default_factory=list)
    # Issue #432: provenance for every resolved input link. Maps an
    # input_equation_id → sorted list of LINK_PROVENANCE_KINDS that justify it.
    # Populated deterministically by EquationLinkNormalizer; empty until then.
    link_provenance: dict[str, list[str]] = field(default_factory=dict)
    # Issue #432: why this equation may carry no input links (LINK_STATUSES).
    link_status: str = ""


# ---------------------------------------------------------------------------
# EquationConfidencePolicy — downstream usability
# ---------------------------------------------------------------------------

@dataclass
class EquationConfidencePolicy:
    """Claim / Derivation / Component / Course で使用可能かの方針。"""
    can_support_claim: bool
    can_be_used_in_derivation: bool
    can_be_rendered_as_final_formula: bool
    allowed_downstream_use: str
    can_be_displayed_in_course: bool
    display_requires_note: bool
    must_not_treat_as_source_extracted: bool

    @classmethod
    def derive(
        cls,
        source_extraction: EquationSourceExtraction,
        reconstruction: EquationReconstruction,
        semantics: EquationSemantics,
        equation_consistency: EquationConsistency | None = None,
    ) -> "EquationConfidencePolicy":
        """extraction / reconstruction / semantics から決定論的に生成する。"""
        consistency_review = bool(equation_consistency and equation_consistency.review_required)
        consistency_mismatch = bool(
            equation_consistency
            and (
                equation_consistency.raw_text_latex_match == "mismatch"
                or equation_consistency.label_location_match == "mismatch"
                or equation_consistency.source_span_quality == "corrupted"
            )
        )
        is_reconstruction_based = (
            semantics.semantic_status == "reconstruction_based"
            or reconstruction.status != "none"
            or source_extraction.needs_math_review
            or consistency_review
        )
        must_not = is_reconstruction_based
        can_claim = (
            not must_not
            and not consistency_mismatch
            and source_extraction.extraction_status == "complete"
            and not source_extraction.needs_math_review
            and semantics.semantic_status not in ("unknown", "reconstruction_based")
        )
        can_derivation = (
            not must_not
            or (
                not consistency_mismatch
                and reconstruction.status != "none"
                and reconstruction.confidence >= 0.7
                and semantics.semantic_status in ("reconstruction_based", "context_inferred")
            )
        )
        if consistency_review:
            can_derivation = False
        display_note = must_not or source_extraction.needs_math_review
        can_render_final = (
            can_claim
            and source_extraction.extraction_status == "complete"
            and not consistency_mismatch
            and not display_note
        )
        if can_claim and can_derivation and can_render_final:
            allowed_use = "unrestricted"
        elif can_render_final or can_claim or can_derivation:
            allowed_use = "display_with_warning"
        elif source_extraction.raw_text or source_extraction.latex or reconstruction.latex:
            allowed_use = "semantic_hint_only"
        else:
            allowed_use = "blocked"
        return cls(
            can_support_claim=can_claim,
            can_be_used_in_derivation=can_derivation,
            can_be_rendered_as_final_formula=can_render_final,
            allowed_downstream_use=allowed_use,
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
    equation_consistency: EquationConsistency = field(default_factory=lambda: EquationConsistency(
        raw_text_latex_match="match",
        label_location_match="match",
        symbol_overlap_score=1.0,
        source_span_quality="clean",
        review_required=False,
        review_reason=["equation_consistency_not_computed"],
    ))
    # Deterministic content hash for cross-paper matching (issue #362).
    # "" / 0 on legacy artifacts that predate hashing.
    content_hash: str = ""
    content_hash_version: int = 0


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

            # Issue #368: a prose reconstruction (latex_is_prose) is audit-only.
            # Keep the reconstruction block for audit, but never publish the
            # paraphrased text as display math; the confidence_gate below then
            # blocks claim / derivation / final-formula use.
            if FIDELITY_LATEX_IS_PROSE in (rec.review_reason or []):
                latex = None
                plain_text = None

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
                "link_provenance": {
                    k: list(v) for k, v in (sem.link_provenance or {}).items()
                },
                "link_status": sem.link_status or "",
                "linked_claim_ids": sorted(set(sem.linked_claim_ids) | set(claim_index.get(r.equation_id, []))),
                "inferred_claim_ids": sorted(set(sem.inferred_claim_ids)),
                "source_evidence_ids": sorted(set(sem.source_evidence_ids) | set(evidence_index.get(block_id, []))),
                "review_flags": list(sem.review_flags),
                "section_id": section_id,
                "needs_math_review": src.needs_math_review,
                "confidence_policy": asdict(r.confidence_policy),
                "equation_consistency": asdict(r.equation_consistency),
                "confidence_gate": _confidence_gate_for_equation(
                    r.equation_id,
                    r.confidence_policy,
                    latex=latex,
                    plain_text=plain_text,
                    extraction_status=src.extraction_status,
                ),
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
        inferred_claim_ids=list(sem_raw.get("inferred_claim_ids", [])),
        link_provenance={
            str(k): [str(v) for v in (vals or [])]
            for k, vals in (sem_raw.get("link_provenance", {}) or {}).items()
        },
        link_status=str(sem_raw.get("link_status", "")),
    )
    cp_raw = d.get("confidence_policy", {})
    confidence_policy = EquationConfidencePolicy(
        can_support_claim=bool(cp_raw.get("can_support_claim", False)),
        can_be_used_in_derivation=bool(cp_raw.get("can_be_used_in_derivation", False)),
        can_be_rendered_as_final_formula=bool(
            cp_raw.get(
                "can_be_rendered_as_final_formula",
                cp_raw.get("can_support_claim", False),
            )
        ),
        allowed_downstream_use=str(
            _normalize_allowed_downstream_use(
                cp_raw.get(
                    "allowed_downstream_use",
                    _legacy_allowed_downstream_use(cp_raw),
                )
            )
        ),
        can_be_displayed_in_course=bool(cp_raw.get("can_be_displayed_in_course", True)),
        display_requires_note=bool(cp_raw.get("display_requires_note", True)),
        must_not_treat_as_source_extracted=bool(cp_raw.get("must_not_treat_as_source_extracted", True)),
    )
    consistency_raw = d.get("equation_consistency")
    if isinstance(consistency_raw, dict):
        equation_consistency = EquationConsistency(
            raw_text_latex_match=consistency_raw.get("raw_text_latex_match", "uncertain"),
            label_location_match=consistency_raw.get("label_location_match", "uncertain"),
            symbol_overlap_score=float(consistency_raw.get("symbol_overlap_score", 0.0) or 0.0),
            source_span_quality=consistency_raw.get("source_span_quality", "partial"),
            review_required=bool(consistency_raw.get("review_required", True)),
            review_reason=list(consistency_raw.get("review_reason", [])),
        )
    else:
        equation_consistency = EquationConsistency(
            raw_text_latex_match="match",
            label_location_match="match",
            symbol_overlap_score=1.0,
            source_span_quality="clean",
            review_required=False,
            review_reason=["equation_consistency_not_computed"],
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
        equation_consistency=equation_consistency,
        content_hash=str(d.get("content_hash", "") or ""),
        content_hash_version=int(d.get("content_hash_version", 0) or 0),
    )


def _math_symbol_set(text: str) -> set[str]:
    if not text:
        return set()
    normalized = text.replace("\\", " ")
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9_]*|[α-ωΑ-Ω]+|[=+\-*/^<>≤≥≈≃∝∑∫]", normalized)
    stop = {
        "left", "right", "frac", "sqrt", "sum", "int", "mathrm", "text",
        "begin", "end", "label", "equation", "eqnarray", "align",
    }
    return {t.lower() for t in tokens if t.lower() not in stop and len(t.strip()) > 0}


def _legacy_allowed_downstream_use(policy: dict) -> str:
    if not bool(policy.get("can_support_claim", False)) and not bool(policy.get("can_be_used_in_derivation", False)):
        return "semantic_hint_only" if bool(policy.get("can_be_displayed_in_course", True)) else "blocked"
    if bool(policy.get("display_requires_note", False)):
        return "display_with_warning"
    return "unrestricted"


def _normalize_allowed_downstream_use(value: object) -> str:
    raw = str(value or "").strip()
    return raw if raw in ALLOWED_DOWNSTREAM_USES else "semantic_hint_only"


def _theory_family_conflict(raw_text: str, latex: str) -> bool:
    # Removed paper-specific cross-contamination heuristic (issues #395 / #397):
    # core logic must not hard-code the vocabulary of particular paper families.
    # The generic raw_text↔latex symbol-overlap consistency check already flags
    # mismatched extractions; a domain cartridge may add stricter rules.
    return False


def _dedupe_text(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _confidence_gate_for_equation(
    equation_id: str,
    policy: EquationConfidencePolicy,
    *,
    latex: str | None,
    plain_text: str | None,
    extraction_status: str,
) -> dict:
    allowed_use = (
        policy.allowed_downstream_use
        if policy.allowed_downstream_use in ALLOWED_DOWNSTREAM_USES
        else _legacy_allowed_downstream_use(asdict(policy))
    )
    blocked = (
        allowed_use == "blocked"
        or (
            policy.can_support_claim is False
            and policy.can_be_used_in_derivation is False
            and allowed_use in {"blocked", "semantic_hint_only"}
        )
    ) or (
        latex is None
        and plain_text is None
        and extraction_status in ("partial", "fragment_only", "label_only", "missing", "unparsed")
    )
    if blocked:
        return {
            "blocked_by_equation_ids": [equation_id],
            "blocked_reason": "linked equation cannot support claim or derivation",
            "downstream_allowed_use": allowed_use if allowed_use != "unrestricted" else "display_with_warning",
            "allowed_downstream_use": allowed_use if allowed_use != "unrestricted" else "display_with_warning",
        }
    if policy.display_requires_note or allowed_use == "display_with_warning":
        return {
            "blocked_by_equation_ids": [],
            "blocked_reason": "",
            "downstream_allowed_use": "display_with_warning",
            "allowed_downstream_use": "display_with_warning",
        }
    return {
        "blocked_by_equation_ids": [],
        "blocked_reason": "",
        "downstream_allowed_use": allowed_use,
        "allowed_downstream_use": allowed_use,
    }
