"""Synthesise atomic claims from equation semantics and system-level derivations (issue #388).

Some important claims are not stated as prose sentences — they are expressed by
equation structure: a definition, a dependency, a solved/constrained system, or a
derived result. Prose-only claim extraction misses these, leaving the pipeline
with strong equation artifacts but weak claim artifacts.

This deterministic, domain-independent step turns source-backed equation /
derivation structure into atomic claims. Each synthesised claim is ONE minimal
proposition, links back to its equation(s) / evidence / derivation(s), and uses a
source-backed policy:

- prose-backed             → ``source_backed``       (not produced here)
- equation-structure       → ``equation_backed``
- requires a derivation    → ``derived_from_linked_artifacts``
- incomplete support       → ``review_required``
"""
from __future__ import annotations

from typing import Iterable

from .schema import ClaimConcept, ClaimObjectRecord


# General, domain-neutral claim types (issue #388).
DEFINITION_CLAIM = "definition_claim"
DEPENDENCY_CLAIM = "dependency_claim"
EQUATION_SYSTEM_CLAIM = "equation_system_claim"
DERIVATION_CLAIM = "derivation_claim"
CONSTRAINT_CLAIM = "constraint_claim"
RESULT_CLAIM = "result_claim"


_OPERATION_TO_CLAIM_TYPE = {
    "eliminate": EQUATION_SYSTEM_CLAIM,
    "solve": EQUATION_SYSTEM_CLAIM,
    "constrain": CONSTRAINT_CLAIM,
    "approximate": DERIVATION_CLAIM,
    "compare": DERIVATION_CLAIM,
}


def _concepts(symbols: Iterable[str]) -> list[ClaimConcept]:
    out: list[ClaimConcept] = []
    seen: set[str] = set()
    for s in symbols:
        s = str(s).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(ClaimConcept(name=s, normalized=s.lower(), concept_type="symbol", role="subject"))
    return out


def _equation_is_source_backed(record) -> bool:
    src = getattr(record, "source_extraction", None)
    if src is None:
        return False
    has_latex = bool(getattr(src, "latex", None))
    loc = getattr(src, "source_location", None) or {}
    has_block = bool(loc.get("block_id")) if isinstance(loc, dict) else False
    return has_latex and has_block


def synthesize_equation_claims(
    equations,
    *,
    derivations=None,
    existing_claims: Iterable = (),
    start_index: int = 1,
) -> list[ClaimObjectRecord]:
    """Build atomic equation-/derivation-backed claims.

    ``equations`` is an EquationSemanticsResult; ``derivations`` an optional
    DerivationChainResult whose ``system_level`` chains seed equation-system /
    constraint claims. Returns new ClaimObjectRecord objects (never mutates
    inputs). Existing claims are used only to avoid duplicating an equation that
    a prose claim already covers.
    """
    document_id = getattr(equations, "document_id", "")
    records = list(getattr(equations, "equations", []) or [])

    # Equation IDs already covered by an existing (prose) claim — skip those so we
    # only *add* the missing equation-structure claims.
    covered_equation_ids: set[str] = set()
    for c in existing_claims or []:
        for eq_id in getattr(c, "equation_ids", []) or []:
            covered_equation_ids.add(str(eq_id))

    out: list[ClaimObjectRecord] = []
    index = start_index

    for r in records:
        eq_id = r.equation_id
        if eq_id in covered_equation_ids:
            continue
        sem = r.semantics
        source_backed = _equation_is_source_backed(r)
        evidence_ids = list(sem.source_evidence_ids or [])
        defined = [s.symbol for s in (sem.defined_symbols or [])
                   if getattr(s, "definition_status", "") in ("defined", "redefined") and s.symbol]
        used = [s for s in (sem.used_symbols or []) if s]
        label = str(getattr(r, "label", "") or eq_id)

        primary = str(sem.equation_type or "")
        review_reasons: list[str] = []
        if not source_backed:
            review_reasons.append("equation_not_source_backed")
        if not evidence_ids:
            review_reasons.append("no_evidence_link")

        claim: ClaimObjectRecord | None = None
        if primary == "definition" and defined:
            symbol = defined[0]
            claim = _make_claim(
                index, document_id,
                claim_type=DEFINITION_CLAIM,
                text=f"Equation ({label}) defines {symbol}.",
                equation_ids=[eq_id],
                evidence_ids=evidence_ids,
                concepts=_concepts(defined),
                support_status="equation_backed" if source_backed else "review_required",
                review_reasons=review_reasons,
                section_id=getattr(r, "section_id", None) or (r.source_extraction.source_location or {}).get("section_id"),
            )
        elif primary in ("relation", "result", "constraint", "transformation") and len(used) >= 2:
            target = defined[0] if defined else used[0]
            inputs = ", ".join(used[:4])
            claim = _make_claim(
                index, document_id,
                claim_type=RESULT_CLAIM if primary == "result" else DEPENDENCY_CLAIM,
                text=f"In equation ({label}), {target} depends on {inputs}.",
                equation_ids=[eq_id],
                evidence_ids=evidence_ids,
                concepts=_concepts([target] + used),
                support_status="equation_backed" if source_backed else "review_required",
                review_reasons=review_reasons,
                section_id=getattr(r, "section_id", None) or (r.source_extraction.source_location or {}).get("section_id"),
            )
        if claim is not None:
            out.append(claim)
            index += 1

    # System-level derivations → equation-system / constraint / derivation claims.
    for chain in getattr(derivations, "chains", []) or []:
        if getattr(chain, "chain_type", "") != "system_level":
            continue
        operation = getattr(chain, "operation", "") or "solve"
        claim_type = _OPERATION_TO_CLAIM_TYPE.get(operation, DERIVATION_CLAIM)
        eliminated = list(getattr(chain, "eliminated_symbols", []) or [])
        retained = list(getattr(chain, "retained_symbols", []) or [])
        equation_ids = list(getattr(chain, "input_equation_ids", []) or []) + list(
            getattr(chain, "output_equation_ids", []) or []
        )
        evidence_ids = list(getattr(chain, "source_evidence_ids", []) or [])
        review_reasons = list(getattr(chain, "review_reasons", []) or [])
        if eliminated:
            text = (
                f"The equation system {operation}s to eliminate "
                f"{', '.join(eliminated[:4])}"
                + (f" while retaining {', '.join(retained[:4])}." if retained else ".")
            )
        else:
            text = f"The equation system supports a {operation} relating {', '.join(retained[:4] or ['the listed quantities'])}."
        support_status = "derived_from_linked_artifacts" if evidence_ids or equation_ids else "review_required"
        claim = _make_claim(
            index, document_id,
            claim_type=claim_type,
            text=text,
            equation_ids=sorted(set(str(e) for e in equation_ids if e)),
            evidence_ids=sorted(set(str(e) for e in evidence_ids if e)),
            concepts=_concepts(retained + eliminated),
            support_status=support_status,
            review_reasons=review_reasons,
            derivation_ids=[getattr(chain, "derivation_id", "")],
            synthesis_method="system_derivation",
        )
        out.append(claim)
        index += 1

    return out


def _make_claim(
    index: int,
    document_id: str,
    *,
    claim_type: str,
    text: str,
    equation_ids: list[str],
    evidence_ids: list[str],
    concepts: list[ClaimConcept],
    support_status: str,
    review_reasons: list[str],
    section_id: str | None = None,
    derivation_ids: list[str] | None = None,
    synthesis_method: str = "equation_semantics",
) -> ClaimObjectRecord:
    review_required = support_status == "review_required" or bool(review_reasons)
    return ClaimObjectRecord(
        claim_id=f"synth_claim_{index:04d}",
        document_id=document_id,
        claim_type=claim_type,
        text=text,
        normalized_text=text,
        source_evidence_ids=list(evidence_ids),
        source_span_ids=[],
        concepts=concepts,
        equation_ids=list(equation_ids),
        support_status=support_status,
        review_status="teacher_review_required",
        section_id=section_id,
        confidence=0.5,
        atomicity="atomic",
        is_atomic=True,
        concept_assignment_status="review_required",
        derivation_ids=list(derivation_ids or []),
        synthesis_method=synthesis_method,
        review_reasons=list(review_reasons),
    )
