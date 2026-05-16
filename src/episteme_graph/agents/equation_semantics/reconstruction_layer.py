"""Equation reconstruction policy for untrusted PDF math extraction.

PDF text layers routinely corrupt mathematical glyphs, subscripts, fractions,
and inline symbols.  This layer makes that assumption explicit: every equation
candidate coming from the PDF text layer must pass through reconstruction before
it can be used as a displayed or semantic equation.
"""
from __future__ import annotations

from .schema import EquationCandidate

UNTRUSTED_PDF_REASON = "pdf_text_layer_untrusted"

_RECONSTRUCTABLE_STATUSES = {"accepted", "provisional"}
_NON_RECONSTRUCTABLE_STATUSES = {"rejected", "needs_merge", "context_only"}


class EquationReconstructionLayer:
    """Mark PDF-derived equation candidates as reconstruction-required."""

    def prepare_candidates(self, candidates: list[EquationCandidate]) -> list[EquationCandidate]:
        prepared: list[EquationCandidate] = []
        for candidate in candidates:
            c = candidate
            if c.source_location.get("extraction_source") == "tex_source":
                c.needs_math_review = False
                prepared.append(c)
                continue
            c.needs_math_review = True
            if UNTRUSTED_PDF_REASON not in c.review_reason:
                c.review_reason.append(UNTRUSTED_PDF_REASON)
            if c.acceptance_status in _RECONSTRUCTABLE_STATUSES and c.extraction_status == "complete":
                c.extraction_status = "partial"
            prepared.append(c)
        return prepared

    def reconstructable_candidates(self, candidates: list[EquationCandidate]) -> list[EquationCandidate]:
        return [
            c for c in candidates
            if c.acceptance_status not in _NON_RECONSTRUCTABLE_STATUSES
        ]
