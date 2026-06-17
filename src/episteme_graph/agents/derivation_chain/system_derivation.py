"""System-level derivation detection (issue #386).

Many theoretical papers do not derive their main result through a simple
adjacent equation-to-equation chain. They build an equation *system*, apply
substitutions, solve for unknowns, eliminate nuisance quantities, or derive
residual constraints. The local-link DerivationChainAgent walk-back misses these
when the important operation spans a *group* of equations.

This module adds a deterministic, domain-independent pass that detects such
groups from shared symbols and equation roles and represents them as
``chain_type="system_level"`` derivations. It never invents steps not supported
by the equation links / symbols: a candidate with weak structure is kept but
marked ``review_required`` with explicit reasons.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from episteme_graph.agents.equation_semantics.schema import (
    EquationRecord,
    EquationSemanticsResult,
)

from .schema import DerivationChainRecord, DerivationStep


# Domain-neutral operation labels for grouped operations (issue #386).
_SYSTEM_OPERATIONS = {
    "eliminate": "eliminate",
    "solve": "solve",
    "constrain": "constrain",
    "approximate": "approximate",
    "compare": "compare",
}

# Minimum number of equations that must share a symbol to be considered a system.
_MIN_SYSTEM_SIZE = 2


def _symbols_for(record: EquationRecord) -> set[str]:
    sem = record.semantics
    out: set[str] = set()
    for s in sem.defined_symbols or []:
        if getattr(s, "symbol", None):
            out.add(str(s.symbol))
    for s in sem.used_symbols or []:
        if s:
            out.add(str(s))
    return out


def _is_result(record: EquationRecord) -> bool:
    primary = str(record.semantics.equation_type or "")
    secondary = {str(t).lower() for t in (record.semantics.secondary_types or [])}
    return primary in ("result", "constraint") or "result" in secondary or "constraint" in secondary


def _operation_for_group(members: list[EquationRecord], result: EquationRecord | None) -> str:
    """Pick a domain-neutral grouped operation from equation roles / text."""
    text = " ".join(
        " ".join([
            str(getattr(r, "label", "") or ""),
            r.semantics.summary or "",
            r.semantics.reason or "",
        ])
        for r in members
    ).lower()
    if "eliminat" in text:
        return "eliminate"
    if "solve" in text or "solution" in text:
        return "solve"
    if "constraint" in text or "consistency" in text or "residual" in text:
        return "constrain"
    if "approxim" in text or "linear" in text:
        return "approximate"
    if "compar" in text or "limit" in text:
        return "compare"
    if result is not None and _is_result(result):
        return "constrain"
    return "solve"


def detect_system_level_derivations(
    equations: EquationSemanticsResult,
    *,
    existing_chains: Iterable[DerivationChainRecord] = (),
    claim_link_index: dict[str, list[str]] | None = None,
    next_index: int = 1,
) -> list[DerivationChainRecord]:
    """Detect group-level (system) derivations not captured by local chains.

    Returns new ``chain_type="system_level"`` records. The records are additive;
    they never modify or remove the local equation/claim chains.
    """
    claim_link_index = claim_link_index or {}
    records = [r for r in equations.equations if getattr(r, "confidence_policy", None) is None
               or getattr(r.confidence_policy, "can_be_used_in_derivation", True)]
    if len(records) < _MIN_SYSTEM_SIZE:
        return []

    by_id = {r.equation_id: r for r in records}

    # Edges already represented by simple local chains (so we only surface
    # *additional* multi-equation reasoning, not duplicates).
    local_edges: set[frozenset[str]] = set()
    for chain in existing_chains or []:
        for step in chain.steps or []:
            for src in step.input_equation_ids or []:
                for dst in step.output_equation_ids or []:
                    local_edges.add(frozenset((str(src), str(dst))))

    # Group equations into connected components of the "shares ≥1 symbol" graph
    # (union-find). A component is a candidate equation *system*; the operation
    # (solve / eliminate / constrain) spans the whole component, which is exactly
    # the multi-equation reasoning local adjacency chains miss.
    symbol_to_eqs: dict[str, set[str]] = defaultdict(set)
    eq_symbols: dict[str, set[str]] = {}
    for r in records:
        syms = _symbols_for(r)
        eq_symbols[r.equation_id] = syms
        for sym in syms:
            symbol_to_eqs[sym].add(r.equation_id)

    parent: dict[str, str] = {r.equation_id: r.equation_id for r in records}

    def _find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def _union(a: str, b: str) -> None:
        ra, rb = _find(a), _find(b)
        if ra != rb:
            parent[ra] = rb

    for eq_ids in symbol_to_eqs.values():
        if len(eq_ids) < _MIN_SYSTEM_SIZE:
            continue
        ids = list(eq_ids)
        for other in ids[1:]:
            _union(ids[0], other)

    components: dict[str, set[str]] = defaultdict(set)
    for eq_id in parent:
        components[_find(eq_id)].add(eq_id)

    candidates: list[tuple[frozenset[str], set[str]]] = []
    for group_ids in components.values():
        if len(group_ids) < _MIN_SYSTEM_SIZE:
            continue
        # shared_symbols: symbols appearing in ≥2 members of this component.
        sym_count: dict[str, int] = defaultdict(int)
        for eq_id in group_ids:
            for sym in eq_symbols.get(eq_id, set()):
                sym_count[sym] += 1
        shared = {sym for sym, n in sym_count.items() if n >= _MIN_SYSTEM_SIZE}
        if not shared:
            continue
        candidates.append((frozenset(group_ids), shared))

    out: list[DerivationChainRecord] = []
    index = next_index
    for group, shared_symbols in candidates:
        members = [by_id[e] for e in group if e in by_id]
        if len(members) < _MIN_SYSTEM_SIZE:
            continue

        # Skip groups whose only relation is a single local adjacent edge already
        # represented as a simple chain.
        member_ids = [m.equation_id for m in members]
        if len(member_ids) == 2 and frozenset(member_ids) in local_edges:
            continue

        result_members = [m for m in members if _is_result(m)]
        result = result_members[0] if result_members else None
        input_members = [m for m in members if m is not result]

        # Eliminated symbols: shared symbols present in inputs but absent from the
        # result equation. Retained: shared symbols present in the result.
        result_symbols = _symbols_for(result) if result is not None else set()
        input_symbols: set[str] = set()
        for m in input_members:
            input_symbols |= _symbols_for(m)
        eliminated = sorted((input_symbols - result_symbols) & shared_symbols) if result is not None else []
        retained = sorted(result_symbols & shared_symbols) if result is not None else sorted(shared_symbols)
        transformed = sorted(shared_symbols)

        operation = _operation_for_group(members, result)

        input_equation_ids = [m.equation_id for m in input_members] or member_ids
        output_equation_ids = [result.equation_id] if result is not None else []
        intermediate_equation_ids = [
            m.equation_id for m in members
            if m.equation_id not in input_equation_ids and m.equation_id not in output_equation_ids
        ]

        claim_ids: list[str] = []
        evidence_ids: list[str] = []
        assumptions: list[str] = []
        for m in members:
            claim_ids.extend(claim_link_index.get(m.equation_id, []))
            claim_ids.extend(m.semantics.linked_claim_ids or [])
            evidence_ids.extend(m.semantics.source_evidence_ids or [])
            assumptions.extend(m.semantics.assumptions or [])
        claim_ids = sorted(set(claim_ids))
        evidence_ids = sorted(set(evidence_ids))
        assumptions = sorted(set(assumptions))

        # review_required policy (issue #386): a system-level derivation must
        # reference at least one equation or evidence; an elimination must have
        # input and output equations or explicit output claims.
        review_reasons: list[str] = []
        if not (member_ids or evidence_ids):
            review_reasons.append("system_derivation_no_equation_or_evidence")
        if eliminated and not (input_equation_ids and (output_equation_ids or claim_ids)):
            review_reasons.append("elimination_without_input_output_structure")
        if result is None:
            review_reasons.append("no_result_equation_identified")
        if not evidence_ids:
            review_reasons.append("system_derivation_not_source_backed")

        review_required = bool(review_reasons)

        step = DerivationStep(
            step_id=f"sys_{index:03d}_step_1",
            input_equation_ids=input_equation_ids,
            operation=operation,
            output_equation_ids=output_equation_ids,
            required_claim_ids=claim_ids,
            assumption_refs=assumptions,
            reason=f"System-level {operation} over {len(member_ids)} equations sharing {', '.join(transformed[:4])}",
            confidence=0.4,
            assumption_ids=assumptions,
            source_evidence_ids=evidence_ids,
            review_status="teacher_review_required",
            eliminated_symbols=eliminated,
            retained_symbols=retained,
        )

        out.append(DerivationChainRecord(
            derivation_id=f"system_derivation_{index:04d}",
            document_id=equations.document_id,
            source_section_ids=[],
            steps=[step],
            teaching_takeaway=(
                f"{operation.title()} across an equation system "
                f"({len(member_ids)} equations)"
            ),
            chain_type="system_level",
            operation=operation,
            input_equation_ids=input_equation_ids,
            output_equation_ids=output_equation_ids,
            intermediate_equation_ids=intermediate_equation_ids,
            input_claim_ids=claim_ids,
            output_claim_ids=[],
            assumption_ids=assumptions,
            source_evidence_ids=evidence_ids,
            transformed_symbols=transformed,
            eliminated_symbols=eliminated,
            retained_symbols=retained,
            conditions=assumptions,
            confidence=0.4,
            review_required=review_required,
            review_reasons=review_reasons,
            review_status="teacher_review_required",
        ))
        index += 1

    return out
