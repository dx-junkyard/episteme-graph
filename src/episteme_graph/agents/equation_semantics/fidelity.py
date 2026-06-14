"""Deterministic equation reconstruction fidelity guards (issue #368).

These post-validation passes run on the assembled EquationSemanticsResult after
the LLM semantics step. They do not replace the blanket ``needs_math_review`` /
``equation_consistency`` / ``confidence_policy`` safety gate; they add
*per-problem* reason codes so each quality issue can be identified, blocked from
downstream confirmed use, and aggregated by the ExportValidationGate.

All logic is deterministic and never trusts LLM self-reports.
"""
from __future__ import annotations

import re

from .schema import (
    FIDELITY_LATEX_IS_PROSE,
    FIDELITY_NONEQUATION_ID,
    FIDELITY_SYMBOL_LOSS_UNVERIFIABLE,
    EquationConfidencePolicy,
    EquationRecord,
    EquationSemanticsResult,
    ValidationIssue,
)

# ---------------------------------------------------------------------------
# 1. Prose-as-LaTeX detection
# ---------------------------------------------------------------------------

_TEXT_BLOCK_RE = re.compile(r"\\(?:text|textrm|textit|mbox)\s*\{([^{}]*)\}")
_RELATION_RE = re.compile(
    r"=|\\equiv|\\leq|\\geq|\\neq|\\approx|\\sim|\\propto|\\to|<|>|≤|≥|≈|≡|≠|∝"
)
_MATH_TOKEN_RE = re.compile(r"[A-Za-z]+|[α-ωΑ-Ω]+|\d+(?:\.\d+)?|[+\-*/^]")
_PROSE_WORD_RE = re.compile(r"[A-Za-z]{2,}")

# Minimum number of natural-language words inside \text{} for prose suspicion.
_MIN_PROSE_WORDS = 4
_PLACEHOLDER = " \x00MATHTEXT\x00 "


def is_prose_latex(latex: str | None) -> bool:
    """True if a reconstructed LaTeX body is prose rather than an expression.

    Heuristic (deterministic): there is a long ``\\text{...}`` block (>= 4
    natural-language words) AND the math *outside* the text blocks does not
    itself form a complete relation (operands with real math on both sides).
    A normal equation that merely annotates with ``\\text{subject to ...}`` is
    not flagged because its math forms a complete relation on its own.
    """
    if not latex or not latex.strip():
        return False
    text_blocks = _TEXT_BLOCK_RE.findall(latex)
    if not text_blocks:
        return False
    text_words = _PROSE_WORD_RE.findall(" ".join(text_blocks))
    if len(text_words) < _MIN_PROSE_WORDS:
        return False
    skeleton = _TEXT_BLOCK_RE.sub(_PLACEHOLDER, latex)
    if _has_complete_relation(skeleton):
        return False
    math_tokens = [t for t in _MATH_TOKEN_RE.findall(skeleton.replace("MATHTEXT", " ")) if t]
    # Prose dominates the expression body.
    return len(text_words) > len(math_tokens)


def _has_complete_relation(skeleton: str) -> bool:
    """A relation operator whose both sides carry real (non-placeholder) math."""
    parts = _RELATION_RE.split(skeleton)
    if len(parts) < 2:
        return False
    sides_with_math = 0
    for part in parts:
        cleaned = part.replace("MATHTEXT", " ")
        if _MATH_TOKEN_RE.search(cleaned):
            sides_with_math += 1
    return sides_with_math >= 2


def apply_prose_latex_guard(record: EquationRecord) -> bool:
    """Flag a prose reconstruction and block its confirmed downstream use.

    Returns True if the record was flagged.
    """
    rec = record.reconstruction
    if rec.status == "none":
        return False
    if not is_prose_latex(rec.latex):
        return False

    rec.review_required = True
    _add(rec.review_reason, FIDELITY_LATEX_IS_PROSE)

    consistency = record.equation_consistency
    consistency.review_required = True
    _add(consistency.review_reason, FIDELITY_LATEX_IS_PROSE)

    # Recompute confidence_policy so claim / derivation / final-formula use is
    # blocked. The reconstructed prose LaTeX is never published as final math.
    record.confidence_policy = EquationConfidencePolicy.derive(
        record.source_extraction,
        rec,
        record.semantics,
        consistency,
    )
    return True


# ---------------------------------------------------------------------------
# 4. I/O link referential integrity
# ---------------------------------------------------------------------------

def validate_io_links(records: list[EquationRecord]) -> list[ValidationIssue]:
    """Verify input/output equation links by membership, not by ``eq_`` prefix.

    Any link that is not a member of the document's final equation-id set (a
    block id, candidate id, self-reference, or dangling id) is removed and the
    removed values are recorded in a ValidationIssue. ``nonequation_id_in_links``
    is added to the record's review_flags.
    """
    issues: list[ValidationIssue] = []
    final_ids = {r.equation_id for r in records if r.equation_id}
    for record in records:
        sem = record.semantics
        eid = record.equation_id
        removed: list[str] = []

        kept_in = []
        for ref in sem.input_equation_ids:
            if ref and ref != eid and ref in final_ids:
                kept_in.append(ref)
            elif ref:
                removed.append(f"input:{ref}")
        kept_out = []
        for ref in sem.output_equation_ids:
            if ref and ref != eid and ref in final_ids:
                kept_out.append(ref)
            elif ref:
                removed.append(f"output:{ref}")

        if removed:
            sem.input_equation_ids = kept_in
            sem.output_equation_ids = kept_out
            _add(sem.review_flags, FIDELITY_NONEQUATION_ID)
            issues.append(ValidationIssue(
                rule_id="nonequation_id_in_links",
                severity="warning",
                message=(
                    f"{eid} had non-equation / dangling derivation link(s) removed: "
                    f"{', '.join(removed)}"
                ),
                field=f"{eid}.semantics.input_equation_ids/output_equation_ids",
            ))
    return issues


# ---------------------------------------------------------------------------
# 5. Symbol-loss unverifiability
# ---------------------------------------------------------------------------

_IMAGE_UNAVAILABLE_HINTS = (
    "vision_ocr_unavailable",
    "vision_ocr_not_attempted",
    "equation_image_unavailable",
)


def apply_symbol_loss_guard(record: EquationRecord) -> bool:
    """Mark unverifiable symbol loss between raw text and reconstruction (#368).

    When the deterministic symbol-overlap comparison already signals a raw↔latex
    mismatch but the source image / Vision OCR provenance is unavailable, the
    loss cannot be auto-confirmed or auto-repaired, so it is surfaced as
    ``symbol_loss_unverifiable`` for review rather than treated as source-backed.
    """
    consistency = record.equation_consistency
    if consistency.raw_text_latex_match != "mismatch":
        return False
    if "raw_text_latex_symbol_mismatch" not in consistency.review_reason:
        return False
    if not _image_unavailable(record):
        return False
    _add(consistency.review_reason, FIDELITY_SYMBOL_LOSS_UNVERIFIABLE)
    consistency.review_required = True
    return True


def _image_unavailable(record: EquationRecord) -> bool:
    if record.source_extraction.source_image:
        return False
    hints = list(record.reconstruction.review_reason) + list(
        record.source_extraction.review_reason
    )
    return any(any(h in reason for h in _IMAGE_UNAVAILABLE_HINTS) for reason in hints)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def apply_fidelity_guards(
    result: EquationSemanticsResult,
) -> list[ValidationIssue]:
    """Run all deterministic fidelity guards over an assembled result.

    Mutates records in place (review reasons / flags / blocked policy) and
    returns ValidationIssues for the removed I/O links. Idempotent.
    """
    issues: list[ValidationIssue] = []
    for record in result.equations:
        apply_prose_latex_guard(record)
        apply_symbol_loss_guard(record)
    issues += validate_io_links(result.equations)
    return issues


def _add(values: list[str], code: str) -> None:
    if code not in values:
        values.append(code)
