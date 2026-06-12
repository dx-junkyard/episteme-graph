"""ClaimObjectBuilder.

QualifiedSpanRecord（ClaimQualificationAgent の出力）と EvidenceRegistry を入力に
最終 ClaimObjectRecord を組み立てる。

このモジュールは LLM を使わない。atomic rewrite（複数命題 claim の分割）は
ClaimQualificationAgent の正式ステップが担当し (issue #317)、builder は
その atomic candidates を ClaimObjectRecord に変換・リンク・検証するだけに責務を
限定する。LLM の atomic rewrite が無い場合のみ、deterministic split を
``split_pending`` / ``review_required`` の review suggestion として保持する
（確定 source_backed atomic claim にはしない）。

concepts / equation_ids は外部から渡される concept resolver / equation index に
基づいて付与する。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field as _dc_field
from typing import Callable, Iterable, Optional

from .schema import (
    CLAIM_TYPE_ONTOLOGY,
    ClaimConcept,
    ClaimObjectBuildResult,
    ClaimObjectRecord,
    EQUATION_CLAIM_TYPES,
    MAIN_RESULT_CLAIM_TYPES,
    REVIEW_STATUSES,
    SUPPORT_STATUSES,
    ValidationIssue,
    derive_support_status,
    normalize_atomicity,
)


_DEFAULT_CLAIM_TYPE = "unknown"

_DOMAIN_CONCEPT_FALLBACKS: dict[str, tuple[str, str]] = {
    "nonlinear galaxy bias": ("nonlinear galaxy bias", "domain_concept"),
    "skewness": ("skewness", "observable"),
    "kurtosis": ("kurtosis", "observable"),
    "consistency relation": ("consistency relation", "result"),
    "matter density perturbation": ("matter density perturbation", "quantity"),
    "dhost": ("DHOST", "theory_family"),
    "horndeski": ("Horndeski", "theory_family"),
    "kernel coefficient": ("kernel coefficient", "parameter"),
    "smoothing scale": ("smoothing scale", "parameter"),
    "tree-level approximation": ("tree-level approximation", "approximation"),
    "local bias": ("local bias", "model"),
    "linear bias": ("linear bias", "parameter"),
    "bias elimination": ("bias elimination", "method"),
    "higher-order moment": ("higher-order moment", "observable"),
    "gravity diagnostic": ("gravity diagnostic", "diagnostic"),
}


@dataclass
class _BuildCtx:
    """Per-span context shared between the build paths (issue #317)."""
    document_id: str
    span: object
    block_id: str | None
    section_id: str | None
    role_labels: list = _dc_field(default_factory=list)
    span_id: str | None = None
    confidence: float = 0.0
    evidence_ids: list = _dc_field(default_factory=list)
    review_note: str = ""
    review_status: str = "teacher_review_required"
    claim_type: str = "unknown"
    text: str = ""
    section_title: str | None = None
    # Canonical support_status from evidence presence + adequacy (issue #359).
    support_status_base: str = "inferred"


class ClaimObjectBuilder:
    """Build final claims.json from QualifiedSpanRecord + EvidenceRegistry.

    Parameters
    ----------
    evidence_registry:
        EvidenceRegistryResult。span_id → evidence_id 解決に使う。
    equation_index:
        equation_id → EquationSemanticsRecord-like の dict（オプション）。
    concept_resolver:
        Callable[[span_text, role_labels, cartridge_ontology], list[ClaimConcept]]。
        指定がない場合は cartridge ontology から alias マッチで抽出する。
    cartridge_ontology:
        cartridge の ontology dict（オプション）。
    equation_semantics_result:
        EquationSemanticsResult（オプション）。source_location proximity で equation を link する (issue #260)。
    """

    def __init__(
        self,
        evidence_registry: object | None = None,
        equation_index: dict[str, object] | None = None,
        concept_resolver: Optional[Callable] = None,
        cartridge_ontology: dict | None = None,
        equation_semantics_result: object | None = None,
        document_structure: object | None = None,
    ) -> None:
        self._evidence_registry = evidence_registry
        self._equation_index = equation_index or {}
        self._concept_resolver = concept_resolver
        self._cartridge_ontology = cartridge_ontology or {}
        self._span_to_evidence: dict[str, list[str]] = {}
        self._known_evidence_ids: set[str] = set()
        # section_id → human-readable title (issue #359).
        self._section_titles: dict[str, str] = {}
        for section in getattr(document_structure, "sections", None) or []:
            sid = getattr(section, "section_id", None)
            title = getattr(section, "title", None)
            if sid and title:
                self._section_titles[str(sid)] = str(title)
        if evidence_registry is not None:
            for r in getattr(evidence_registry, "records", []) or []:
                eid = getattr(r, "evidence_id", None)
                if eid:
                    self._known_evidence_ids.add(eid)
                bid = getattr(r.source, "block_id", None)
                if bid:
                    self._span_to_evidence.setdefault(bid, []).append(r.evidence_id)

        # Build proximity index: block_id → [equation_id], section_id → [equation_id]
        self._block_to_equations: dict[str, list[str]] = {}
        self._section_to_equations: dict[str, list[str]] = {}
        for eq_record in getattr(equation_semantics_result, "equations", []) or []:
            eq_id = getattr(eq_record, "equation_id", None)
            if not eq_id:
                continue
            src = getattr(eq_record, "source_extraction", None)
            loc = getattr(src, "source_location", {}) or {} if src else {}
            bid = loc.get("block_id")
            sid = loc.get("section_id")
            if bid:
                self._block_to_equations.setdefault(bid, []).append(eq_id)
            if sid:
                self._section_to_equations.setdefault(sid, []).append(eq_id)

    # ------------------------------------------------------------------
    def build(
        self,
        document_id: str,
        qualified_spans: Iterable[object],
        cartridge_id: str | None = None,
    ) -> ClaimObjectBuildResult:
        claims: list[ClaimObjectRecord] = []
        issues: list[ValidationIssue] = []
        seen_claim_ids: set[str] = set()
        counter = 0

        for span in qualified_spans:
            qual = getattr(span, "qualification", {}) or {}
            if qual.get("status") != "accepted":
                continue

            counter += 1
            base_claim_id = self._make_claim_id(document_id, counter, span)
            text = self._extract_normalized_text(span)
            claim_type = self._normalize_claim_type(qual.get("claim_type_candidate"))
            block_id = getattr(span, "block_id", None)
            section_id = getattr(span, "section_id", None)
            role_labels = list(getattr(span, "role_labels", []) or [])
            span_id = getattr(span, "span_id", None)
            confidence = float(getattr(span, "confidence", 0.0) or 0.0)

            evidence_ids = self._resolve_evidence_ids(block_id)
            review_note = self._extract_review_note(span)
            review_status = self._derive_review_status(qual)
            support_status_base = derive_support_status(
                bool(evidence_ids), qual.get("evidence_adequacy")
            )
            section_title = self._section_titles.get(str(section_id or "")) or None

            ctx = _BuildCtx(
                document_id=document_id,
                span=span,
                block_id=block_id,
                section_id=section_id,
                role_labels=role_labels,
                span_id=span_id,
                confidence=confidence,
                evidence_ids=evidence_ids,
                review_note=review_note,
                review_status=review_status,
                claim_type=claim_type,
                text=text,
                section_title=section_title,
                support_status_base=support_status_base,
            )

            # Atomic rewrite responsibility (issue #317): the LLM atomic rewrite is
            # performed by ClaimQualificationAgent. The builder no longer rewrites;
            # it consumes the agent's atomic candidates and only constructs / links /
            # validates ClaimObjectRecords.
            llm_atomic = self._gather_llm_atomic_candidates(span, claim_type)
            if llm_atomic:
                self._emit_llm_atomic_claims(
                    claims, issues, seen_claim_ids, counter, base_claim_id, ctx, llm_atomic
                )
                continue

            # No LLM rewrite available. Classify the span text deterministically.
            text_atomicity, text_qual_reason = self._analyze_atomicity(text)

            # Non-atomic span with no LLM rewrite: the deterministic split is kept
            # only as a review suggestion (issue #317 criteria #4 / #6). Neither the
            # parent nor the suggested children are confirmed source_backed atomic
            # claims, so they are not used as strong graph backing.
            if text_atomicity == "non_atomic":
                self._emit_fallback_split(
                    claims, issues, seen_claim_ids, counter, base_claim_id, ctx,
                    text_qual_reason,
                )
                continue

            # Single atomic claim
            claim_id = base_claim_id
            if claim_id in seen_claim_ids:
                claim_id = f"{claim_id}_{counter}"
            seen_claim_ids.add(claim_id)

            if not evidence_ids:
                issues.append(ValidationIssue(
                    rule_id="claim_missing_evidence",
                    severity="warning",
                    message=f"claim {claim_id} has no source evidence (block_id={block_id})",
                    field=claim_id,
                ))

            concepts = self._resolve_concepts(text, role_labels, span)
            if not concepts:
                issues.append(ValidationIssue(
                    rule_id="claim_concepts_empty",
                    severity="warning",
                    message=f"claim {claim_id} has no concepts",
                    field=claim_id,
                ))
                if claim_type in MAIN_RESULT_CLAIM_TYPES:
                    issues.append(ValidationIssue(
                        rule_id="main_result_claim_concepts_empty",
                        severity="warning",
                        message=(
                            f"main-result claim {claim_id} ({claim_type}) has no concepts; "
                            "it cannot back a component or graph node"
                        ),
                        field=claim_id,
                    ))
            elif claim_type in MAIN_RESULT_CLAIM_TYPES and len(concepts) < 2:
                issues.append(ValidationIssue(
                    rule_id="main_claim_insufficient_concepts",
                    severity="warning",
                    message=(
                        f"main-result claim {claim_id} ({claim_type}) has only "
                        f"{len(concepts)} concept(s); main claims need at least 2"
                    ),
                    field=claim_id,
                ))

            equation_ids = self._link_equations(text, claim_type, role_labels, block_id, section_id)
            if self._claim_type_implies_equation(claim_type) and not equation_ids:
                issues.append(ValidationIssue(
                    rule_id="claim_missing_equation_ref",
                    severity="warning",
                    message=f"claim {claim_id} of type {claim_type} should reference equations",
                    field=claim_id,
                ))

            figure_ids, table_ids = self._link_figures_tables(role_labels, claim_type)

            # Single, already-atomic claim (non-atomic spans were routed to the
            # LLM-atomic or deterministic-fallback paths above, issue #317).
            single_support_status = support_status_base
            record = ClaimObjectRecord(
                claim_id=claim_id,
                document_id=document_id,
                claim_type=claim_type,
                text=text,
                source_evidence_ids=evidence_ids,
                source_span_ids=[span_id] if span_id else [],
                concepts=concepts,
                equation_ids=equation_ids,
                figure_ids=figure_ids,
                table_ids=table_ids,
                support_status=single_support_status,
                review_status=review_status,
                review_note=review_note,
                section_id=section_id,
                section_title=section_title,
                confidence=confidence,
                normalized_text=text,
                atomicity="atomic",
                is_atomic=True,
                qualification_reason=None,
                parent_claim_id=None,
                subclaim_ids=[],
                concept_assignment_status=self._concept_assignment_status(
                    is_atomic=True,
                    support_status=single_support_status,
                    concepts=concepts,
                ),
            )
            claims.append(record)

        self._validate_claim_set(claims, issues)

        return ClaimObjectBuildResult(
            document_id=document_id,
            cartridge_id=cartridge_id,
            claims=claims,
            validation_issues=issues,
        )

    def _add_issues_for_record(
        self,
        claim_id: str,
        evidence_ids: list[str],
        concepts: list,
        claim_type: str,
        equation_ids: list[str],
        issues: list[ValidationIssue],
    ) -> None:
        if not evidence_ids:
            issues.append(ValidationIssue(
                rule_id="claim_missing_evidence",
                severity="warning",
                message=f"claim {claim_id} has no source evidence",
                field=claim_id,
            ))
        if not concepts:
            issues.append(ValidationIssue(
                rule_id="claim_concepts_empty",
                severity="warning",
                message=f"claim {claim_id} has no concepts",
                field=claim_id,
            ))
            if claim_type in MAIN_RESULT_CLAIM_TYPES:
                issues.append(ValidationIssue(
                    rule_id="main_result_claim_concepts_empty",
                    severity="warning",
                    message=(
                        f"main-result claim {claim_id} ({claim_type}) has no concepts; "
                        "it cannot back a component or graph node"
                    ),
                    field=claim_id,
                ))
        elif claim_type in MAIN_RESULT_CLAIM_TYPES and len(concepts) < 2:
            issues.append(ValidationIssue(
                rule_id="main_claim_insufficient_concepts",
                severity="warning",
                message=(
                    f"main-result claim {claim_id} ({claim_type}) has only "
                    f"{len(concepts)} concept(s); main claims need at least 2"
                ),
                field=claim_id,
            ))
        if self._claim_type_implies_equation(claim_type) and not equation_ids:
            issues.append(ValidationIssue(
                rule_id="claim_missing_equation_ref",
                severity="warning",
                message=f"claim {claim_id} of type {claim_type} should reference equations",
                field=claim_id,
            ))

    # ------------------------------------------------------------------
    # Atomic-claim sourcing (issue #317).
    # ------------------------------------------------------------------
    def _gather_llm_atomic_candidates(
        self, span: object, default_claim_type: str
    ) -> list[dict]:
        """Collect LLM-rewritten atomic claim candidates from the span.

        Primary source is ``span.atomic_claims`` (produced by ClaimQualificationAgent,
        issue #317). For backward compatibility, falls back to the legacy
        ``edit_suggestions.split_claims`` ({text, claim_type}) list. Returns an empty
        list when the agent provided no atomic rewrite — in that case the builder
        only keeps a deterministic split as a review suggestion.
        """
        normalized: list[dict] = []
        for cand in getattr(span, "atomic_claims", None) or []:
            if not isinstance(cand, dict):
                continue
            text = str(cand.get("text", "")).strip()
            if not text:
                continue
            atomicity = normalize_atomicity(cand.get("atomicity", "atomic"))
            if atomicity not in ("atomic", "split_required"):
                atomicity = "atomic"
            status = str(cand.get("status", "")).strip().lower()
            if status not in ("accepted", "review_required"):
                status = "review_required" if atomicity == "split_required" else "accepted"
            normalized.append({
                "text": text,
                "normalized_text": str(cand.get("normalized_text") or text).strip(),
                "claim_type": self._normalize_claim_type(
                    cand.get("claim_type_candidate")
                    or cand.get("claim_type")
                    or default_claim_type
                ),
                "atomicity": atomicity,
                "status": status,
                "qualification_reason": str(cand.get("qualification_reason", "") or ""),
                "source_span_id": str(cand.get("source_span_id", "") or ""),
            })
        if normalized:
            return normalized

        # Legacy: edit_suggestions.split_claims (LLM-proposed atomic objects).
        edits = getattr(span, "edit_suggestions", {}) or {}
        for cand in edits.get("split_claims") or []:
            if not isinstance(cand, dict):
                continue
            text = str(cand.get("text", "")).strip()
            if not text:
                continue
            normalized.append({
                "text": text,
                "normalized_text": text,
                "claim_type": self._normalize_claim_type(
                    cand.get("claim_type") or default_claim_type
                ),
                "atomicity": "atomic",
                "status": "accepted",
                "qualification_reason": "",
                "source_span_id": "",
            })
        return normalized

    @staticmethod
    def _dedup_id(base: str, counter: int, seen: set[str]) -> str:
        cid = base
        if cid in seen:
            cid = f"{cid}_{counter}"
        while cid in seen:
            cid = f"{cid}_x"
        seen.add(cid)
        return cid

    def _emit_llm_atomic_claims(
        self,
        claims: list[ClaimObjectRecord],
        issues: list[ValidationIssue],
        seen: set[str],
        counter: int,
        base_claim_id: str,
        ctx: _BuildCtx,
        candidates: list[dict],
    ) -> None:
        """Build ClaimObjectRecords from LLM atomic candidates (issue #317).

        The builder performs no rewrite — it only constructs / links / validates.
        A single accepted atomic candidate becomes one atomic claim; multiple
        candidates become a compound parent (not atomic backing) with atomic
        children.
        """
        accepted = [
            c for c in candidates if c["atomicity"] == "atomic" and c["status"] == "accepted"
        ]
        if len(candidates) == 1 and len(accepted) == 1:
            claim_id = self._dedup_id(base_claim_id, counter, seen)
            self._append_atomic_child(claims, issues, ctx, claim_id, candidates[0], parent_id=None)
            return

        parent_id = self._dedup_id(base_claim_id, counter, seen)
        subclaim_ids: list[str] = []
        split_suggestions: list[dict] = []
        for idx, cand in enumerate(candidates, start=1):
            sub_id = self._dedup_id(f"{parent_id}_sub{idx:02d}", counter, seen)
            subclaim_ids.append(sub_id)
            split_suggestions.append({"claim_id": sub_id, "text": cand.get("text", "")})
            self._append_atomic_child(claims, issues, ctx, sub_id, cand, parent_id=parent_id)

        # Compound parent: tracks the original span but is not atomic backing.
        parent_concepts = self._resolve_concepts(ctx.text, ctx.role_labels, ctx.span)
        parent_eqs = self._link_equations(
            ctx.text, ctx.claim_type, ctx.role_labels, ctx.block_id, ctx.section_id
        )
        claims.append(ClaimObjectRecord(
            claim_id=parent_id,
            document_id=ctx.document_id,
            claim_type=ctx.claim_type,
            text=ctx.text,
            source_evidence_ids=ctx.evidence_ids,
            source_span_ids=[ctx.span_id] if ctx.span_id else [],
            concepts=parent_concepts,
            equation_ids=parent_eqs,
            figure_ids=[],
            table_ids=[],
            support_status=ctx.support_status_base,
            review_status=ctx.review_status,
            review_note=ctx.review_note,
            section_id=ctx.section_id,
            section_title=ctx.section_title,
            confidence=ctx.confidence,
            normalized_text=ctx.text,
            atomicity="composite",
            is_atomic=False,
            qualification_reason="atomic rewrite by ClaimQualificationAgent",
            parent_claim_id=None,
            subclaim_ids=subclaim_ids,
            split_suggestions=split_suggestions,
            concept_assignment_status=self._concept_assignment_status(
                is_atomic=False,
                support_status=ctx.support_status_base,
                concepts=parent_concepts,
            ),
        ))
        self._add_issues_for_record(
            parent_id, ctx.evidence_ids, parent_concepts, ctx.claim_type, parent_eqs, issues
        )

    def _append_atomic_child(
        self,
        claims: list[ClaimObjectRecord],
        issues: list[ValidationIssue],
        ctx: _BuildCtx,
        claim_id: str,
        cand: dict,
        parent_id: str | None,
    ) -> None:
        text = cand.get("normalized_text") or cand["text"]
        claim_type = cand["claim_type"]
        concepts = self._resolve_concepts(text, ctx.role_labels, ctx.span)
        equation_ids = self._link_equations(
            text, claim_type, ctx.role_labels, ctx.block_id, ctx.section_id
        )
        source_span_id = cand.get("source_span_id") or ctx.span_id

        if cand["atomicity"] == "atomic" and cand["status"] == "accepted":
            atomicity = "atomic"
            is_atomic = True
            support_status = ctx.support_status_base
            qualification_reason = None
        else:
            # The agent flagged this piece as not confidently atomized.
            atomicity = "split_required"
            is_atomic = False
            support_status = "review_required"
            qualification_reason = (
                cand.get("qualification_reason")
                or "atomic rewrite incomplete; review required"
            )
            issues.append(ValidationIssue(
                rule_id="atomic_claim_needs_review",
                severity="warning",
                message=(
                    f"atomic claim {claim_id} was flagged split_required by "
                    "ClaimQualificationAgent; review required"
                ),
                field=claim_id,
            ))

        claims.append(ClaimObjectRecord(
            claim_id=claim_id,
            document_id=ctx.document_id,
            claim_type=claim_type,
            text=text,
            source_evidence_ids=ctx.evidence_ids,
            source_span_ids=[source_span_id] if source_span_id else [],
            concepts=concepts,
            equation_ids=equation_ids,
            figure_ids=[],
            table_ids=[],
            support_status=support_status,
            review_status=ctx.review_status,
            review_note=ctx.review_note,
            section_id=ctx.section_id,
            section_title=ctx.section_title,
            confidence=ctx.confidence,
            normalized_text=text,
            atomicity=atomicity,
            is_atomic=is_atomic,
            qualification_reason=qualification_reason,
            parent_claim_id=parent_id,
            subclaim_ids=[],
            concept_assignment_status=self._concept_assignment_status(
                is_atomic=is_atomic,
                support_status=support_status,
                concepts=concepts,
            ),
        ))
        self._add_issues_for_record(
            claim_id, ctx.evidence_ids, concepts, claim_type, equation_ids, issues
        )

    def _emit_fallback_split(
        self,
        claims: list[ClaimObjectRecord],
        issues: list[ValidationIssue],
        seen: set[str],
        counter: int,
        base_claim_id: str,
        ctx: _BuildCtx,
        text_qual_reason: str | None,
    ) -> None:
        """Deterministic split kept as a review suggestion only (issue #317 #4/#6).

        The builder does NOT confirm a deterministic split as atomic / source_backed.
        The original span is kept as a non_atomic parent (review_required) and the
        mechanical split is attached as ``split_pending`` children (review_required).
        Neither is used as strong graph backing (is_atomic=False).
        """
        parent_id = self._dedup_id(base_claim_id, counter, seen)
        parts = self._split_into_atomic(ctx.text)
        subclaim_ids: list[str] = []
        split_suggestions: list[dict] = []
        for idx, part in enumerate(parts, start=1):
            part = (part or "").strip()
            if not part:
                continue
            sub_id = self._dedup_id(f"{parent_id}_sub{idx:02d}", counter, seen)
            subclaim_ids.append(sub_id)
            split_suggestions.append({"claim_id": sub_id, "text": part})
            sub_concepts = self._resolve_concepts(part, ctx.role_labels, ctx.span)
            sub_eqs = self._link_equations(
                part, ctx.claim_type, ctx.role_labels, ctx.block_id, ctx.section_id
            )
            claims.append(ClaimObjectRecord(
                claim_id=sub_id,
                document_id=ctx.document_id,
                claim_type=ctx.claim_type,
                text=part,
                source_evidence_ids=ctx.evidence_ids,
                source_span_ids=[ctx.span_id] if ctx.span_id else [],
                concepts=sub_concepts,
                equation_ids=sub_eqs,
                figure_ids=[],
                table_ids=[],
                support_status="review_required",
                review_status=ctx.review_status,
                review_note=ctx.review_note,
                section_id=ctx.section_id,
                section_title=ctx.section_title,
                confidence=ctx.confidence,
                normalized_text=part,
                atomicity="split_required",
                is_atomic=False,
                qualification_reason="deterministic split requires review",
                parent_claim_id=parent_id,
                subclaim_ids=[],
                concept_assignment_status=self._concept_assignment_status(
                    is_atomic=False,
                    support_status="review_required",
                    concepts=sub_concepts,
                ),
            ))

        parent_concepts = self._resolve_concepts(ctx.text, ctx.role_labels, ctx.span)
        parent_eqs = self._link_equations(
            ctx.text, ctx.claim_type, ctx.role_labels, ctx.block_id, ctx.section_id
        )
        claims.append(ClaimObjectRecord(
            claim_id=parent_id,
            document_id=ctx.document_id,
            claim_type=ctx.claim_type,
            text=ctx.text,
            source_evidence_ids=ctx.evidence_ids,
            source_span_ids=[ctx.span_id] if ctx.span_id else [],
            concepts=parent_concepts,
            equation_ids=parent_eqs,
            figure_ids=[],
            table_ids=[],
            support_status="review_required",
            review_status=ctx.review_status,
            review_note=ctx.review_note,
            section_id=ctx.section_id,
            section_title=ctx.section_title,
            confidence=ctx.confidence,
            normalized_text=ctx.text,
            atomicity="split_required",
            is_atomic=False,
            qualification_reason=(
                text_qual_reason
                or "contains multiple propositions; LLM atomic rewrite required"
            ),
            parent_claim_id=None,
            subclaim_ids=subclaim_ids,
            split_suggestions=split_suggestions,
            concept_assignment_status=self._concept_assignment_status(
                is_atomic=False,
                support_status="review_required",
                concepts=parent_concepts,
            ),
        ))
        self._add_issues_for_record(
            parent_id, ctx.evidence_ids, parent_concepts, ctx.claim_type, parent_eqs, issues
        )
        issues.append(ValidationIssue(
            rule_id="claim_split_pending_review",
            severity="warning",
            message=(
                f"claim {parent_id} contains multiple propositions; the deterministic "
                f"split ({len(subclaim_ids)} parts) is kept as a review suggestion and "
                "must be confirmed by ClaimQualificationAgent before use as backing"
            ),
            field=parent_id,
        ))
        if ctx.claim_type in MAIN_RESULT_CLAIM_TYPES:
            issues.append(ValidationIssue(
                rule_id="main_result_claim_non_atomic",
                severity="error",
                message=(
                    f"main-result claim {parent_id} ({ctx.claim_type}) is non_atomic; "
                    "a main result must be a single minimal proposition"
                ),
                field=parent_id,
            ))

    # ------------------------------------------------------------------
    @staticmethod
    def _make_claim_id(document_id: str, counter: int, span: object) -> str:
        span_id = getattr(span, "span_id", None)
        if span_id:
            safe = re.sub(r"[^A-Za-z0-9_]+", "_", str(span_id))
            return f"claim_{safe}"
        return f"claim_{counter:04d}"

    @staticmethod
    def _extract_normalized_text(span: object) -> str:
        edits = getattr(span, "edit_suggestions", {}) or {}
        normalized = edits.get("normalized_text") or edits.get("paraphrase")
        if normalized:
            return str(normalized).strip()
        return (getattr(span, "text", "") or "").strip()

    def _resolve_evidence_ids(self, block_id: str | None) -> list[str]:
        if not block_id:
            return []
        return list(self._span_to_evidence.get(block_id, []))

    def _resolve_concepts(
        self,
        text: str,
        role_labels: list[str],
        span: object | None = None,
    ) -> list[ClaimConcept]:
        if self._concept_resolver is not None:
            try:
                resolved = self._concept_resolver(text, role_labels, self._cartridge_ontology)
                return [c for c in resolved if isinstance(c, ClaimConcept)]
            except Exception:
                pass
        # Concept completion (issue #312): scan the claim text *and* the raw
        # evidence-span text so concepts that only appear in the source span
        # (not the normalized claim) are still recovered.
        match_text = text
        if span is not None:
            raw = (getattr(span, "text", "") or "").strip()
            if raw and raw not in match_text:
                match_text = f"{match_text} {raw}"
        # Fallback: match aliases from cartridge ontology
        ontology = self._cartridge_ontology or {}
        aliases = ontology.get("aliases", {}) or {}
        concept_types = ontology.get("concept_types", {}) or {}
        found: list[ClaimConcept] = []
        seen = set()
        text_lower = match_text.lower()
        for concept_name, alias_list in aliases.items():
            candidates = [concept_name] + list(alias_list or [])
            for cand in candidates:
                if not cand:
                    continue
                if cand.lower() in text_lower and concept_name not in seen:
                    found.append(ClaimConcept(
                        name=cand,
                        normalized=concept_name,
                        concept_type=concept_types.get(concept_name, "unknown"),
                    ))
                    seen.add(concept_name)
                    break
        for needle, (normalized, concept_type) in _DOMAIN_CONCEPT_FALLBACKS.items():
            if needle in text_lower and normalized not in seen:
                found.append(ClaimConcept(
                    name=normalized,
                    normalized=normalized,
                    concept_type=concept_type,
                ))
                seen.add(normalized)
        return found

    def _concept_assignment_status(
        self,
        *,
        is_atomic: bool,
        support_status: str,
        concepts: list,
    ) -> str:
        """Derive how confidently a claim's concepts may be used downstream (issue #8).

        Concepts from low-confidence / review_required sources are never confirmed,
        composite / split_required (non-atomic) claims stay ``tentative``, and atomic
        source-backed claims are only ``source_backed`` when their concepts come from
        the cartridge ontology (otherwise ``inferred``).
        """
        if not concepts:
            return "review_required"
        if support_status == "review_required":
            return "review_required"
        if not is_atomic:
            return "tentative"
        if support_status == "source_backed" and self._concepts_are_cartridge_backed(concepts):
            return "source_backed"
        return "inferred"

    def _concepts_are_cartridge_backed(self, concepts: list) -> bool:
        ontology = self._cartridge_ontology or {}
        if not ontology:
            # A custom concept resolver with no ontology: trust the resolved concepts.
            return self._concept_resolver is not None
        aliases = ontology.get("aliases", {}) or {}
        concept_types = ontology.get("concept_types", {}) or {}
        known = set(aliases.keys()) | set(concept_types.keys())
        for c in concepts:
            normalized = getattr(c, "normalized", "") or getattr(c, "name", "")
            if normalized in known:
                return True
        return False

    @staticmethod
    def _normalize_claim_type(raw: str | None) -> str:
        """Map raw claim type candidate to ontology value (issue #260)."""
        if not raw:
            return _DEFAULT_CLAIM_TYPE
        canonical = raw.strip().lower()
        if canonical in CLAIM_TYPE_ONTOLOGY:
            return canonical
        # Fuzzy match common variants (approximation has its own ontology entry)
        _ALIASES: dict[str, str] = {
            "constraint": "incompatibility_or_constraint",
            "equation": "equation_relation",
            "derivation_step": "equation_transformation",
            "update": "measurement_or_update",
            "observation": "observable_definition",
            # issue #312 required-vocabulary synonyms
            "problem": "problem_statement",
            "problem_setup": "problem_statement",
            "motivation": "method_motivation",
            "method_motivation": "method_motivation",
            "theory": "theory_encoding",
            "theory_encoding": "theory_encoding",
            "method_choice": "method_choice",
            "structural": "structural_property",
            "structure": "structural_property",
            "derivation": "derivation_result",
            "main_conclusion": "main_result",
            "central_result": "main_result",
            "key_result": "main_result",
            "interpret": "interpretation",
        }
        return _ALIASES.get(canonical, _DEFAULT_CLAIM_TYPE)

    def _link_equations(
        self,
        text: str,
        claim_type: str,
        role_labels: list[str],
        block_id: str | None = None,
        section_id: str | None = None,
    ) -> list[str]:
        ids: list[str] = []
        seen: set[str] = set()

        # 1. Label-based matching (primary)
        for eq_id, eq in self._equation_index.items():
            label = getattr(eq, "label", None) or (eq.get("label") if isinstance(eq, dict) else None)
            if not label:
                continue
            if (
                f"({label})" in text
                or f"Eq. {label}" in text
                or f"eq. {label}" in text
                or f"equation {label}" in text.lower()
            ):
                if eq_id not in seen:
                    ids.append(eq_id)
                    seen.add(eq_id)

        # 2. Source proximity: same block (issue #260)
        if block_id and self._claim_type_implies_equation(claim_type):
            for eq_id in self._block_to_equations.get(block_id, []):
                if eq_id not in seen:
                    ids.append(eq_id)
                    seen.add(eq_id)

        # 3. Source proximity: same section (only for equation claim types when no block match)
        if not ids and section_id and self._claim_type_implies_equation(claim_type):
            for eq_id in self._section_to_equations.get(section_id, [])[:3]:
                if eq_id not in seen:
                    ids.append(eq_id)
                    seen.add(eq_id)

        return ids

    @staticmethod
    def _claim_type_implies_equation(claim_type: str) -> bool:
        return claim_type in EQUATION_CLAIM_TYPES

    @staticmethod
    def _link_figures_tables(role_labels: list[str], claim_type: str) -> tuple[list[str], list[str]]:
        # Linking requires figure/table semantics input; default to empty.
        # Downstream FigureTableSemanticsAgent populates this via cross-link pass.
        return [], []

    # ------------------------------------------------------------------
    # Atomicity analysis (issue #312) — deterministic, domain-neutral.
    # ------------------------------------------------------------------

    # Coordinating conjunctions / discourse markers that frequently join
    # independent propositions.
    _CLAUSE_CONNECTORS = re.compile(
        r"\b(?:and|but|whereas|while|then|therefore|moreover|furthermore|however|thus|hence)\b",
        re.IGNORECASE,
    )

    # Domain-neutral finite-verb / predicate hints. Deliberately broad: used only
    # to decide whether a coordinated fragment is itself a full proposition.
    _VERB_HINTS = {
        "is", "are", "was", "were", "be", "been", "being", "has", "have", "had",
        "can", "cannot", "may", "must", "will", "would", "should",
        "show", "shows", "showed", "demonstrate", "demonstrates",
        "derive", "derives", "derived", "eliminate", "eliminates",
        "use", "uses", "test", "tests", "yield", "yields", "give", "gives",
        "provide", "provides", "obtain", "obtains", "reduce", "reduces",
        "appear", "appears", "find", "finds", "present", "presents",
        "propose", "proposes", "construct", "constructs", "define", "defines",
        "relate", "relates", "imply", "implies", "require", "requires",
        "measure", "measures", "compare", "compares", "constrain", "constrains",
        "solve", "solves", "satisfy", "satisfies", "hold", "holds", "follow",
        "follows", "depend", "depends", "describe", "describes", "explain",
        "explains", "predict", "predicts", "assume", "assumes", "allow", "allows",
    }

    @classmethod
    def _analyze_atomicity(cls, text: str) -> tuple[str, str | None]:
        """Classify a claim as atomic / non_atomic (issue #312).

        Returns ``("atomic", None)`` for single-proposition claims and
        ``("non_atomic", reason)`` for claims that mix multiple propositions and
        were not split upstream. Domain-independent: relies only on sentence
        structure, clause connectors, and generic finite-verb hints.
        """
        t = (text or "").strip()
        if not t:
            return "atomic", None
        roles = cls._rhetorical_roles(t)
        if len(roles) >= 2:
            if {"background", "result"} <= roles:
                return "non_atomic", "contains both background and result; split required"
            if {"method", "conclusion"} <= roles:
                return "non_atomic", "contains both method and conclusion; split required"
            if len(roles) >= 3:
                return "non_atomic", "contains multiple rhetorical roles; split required"
        # 1. Multiple full sentences.
        sentences = [s for s in re.split(r"[.!?]+\s+", t) if len(s.split()) >= 3]
        if len(sentences) >= 2:
            return "non_atomic", "contains multiple sentences; split required"
        # 2. Semicolon-joined clauses.
        semi_clauses = [c for c in t.split(";") if len(c.split()) >= 3]
        if len(semi_clauses) >= 2:
            return "non_atomic", "contains multiple propositions; split required"
        # 3. Coordinated finite clauses (e.g. "X derives Y and shows Z").
        parts = cls._CLAUSE_CONNECTORS.split(t)
        if len(parts) >= 2:
            clauses_with_verb = sum(1 for p in parts if cls._clause_has_verb(p))
            if clauses_with_verb >= 2:
                return "non_atomic", "contains multiple propositions; split required"
        return "atomic", None

    @classmethod
    def _rhetorical_roles(cls, text: str) -> set[str]:
        t = text.lower()
        roles: set[str] = set()
        if any(k in t for k in ("dhost", "horndeski", "background", "theory class", "modified gravity")):
            roles.add("background")
        if any(k in t for k in ("motivat", "constrain", "parameter", "test", "useful", "probe")):
            roles.add("conclusion")
        if any(k in t for k in ("construct", "derive", "eliminat", "solve", "relation", "consistency")):
            roles.add("method")
        if any(k in t for k in ("newly derived", "reproduced", "result", "yield", "obtain")):
            roles.add("result")
        if any(k in t for k in ("observable", "skewness", "kurtosis", "spectrum", "statistics")):
            roles.add("observable")
        return roles

    @classmethod
    def _split_into_atomic(cls, text: str) -> list[str]:
        """Deterministically split a non-atomic claim into atomic propositions.

        Domain-independent: splits on sentence boundaries, semicolons, and
        coordinating conjunctions, keeping only fragments that are full
        propositions (contain a finite verb). A leading verb-less fragment is
        merged into the following proposition so no source text is dropped
        (principle #4). Returns ``[]`` when no confident split is possible.
        """
        t = (text or "").strip()
        if not t:
            return []

        # 1. Sentence + semicolon segmentation.
        segments: list[str] = []
        for sent in re.split(r"(?<=[.!?])\s+", t):
            sent = sent.strip()
            if not sent:
                continue
            for semi in sent.split(";"):
                semi = semi.strip()
                if semi:
                    segments.append(semi)

        # 2. Split each segment on coordinating connectors into finite clauses.
        clauses: list[str] = []
        for seg in segments:
            parts = [p.strip() for p in cls._CLAUSE_CONNECTORS.split(seg) if p.strip()]
            if len(parts) <= 1:
                clauses.append(seg)
                continue
            buffer = ""
            for p in parts:
                if cls._clause_has_verb(p):
                    clauses.append(f"{buffer} {p}".strip() if buffer else p)
                    buffer = ""
                else:
                    buffer = f"{buffer} {p}".strip() if buffer else p
            if buffer:
                if clauses:
                    clauses[-1] = f"{clauses[-1].rstrip('.')} {buffer}".strip()
                else:
                    clauses.append(buffer)

        # 3. Normalize: drop tiny fragments, ensure terminal punctuation + capital.
        result: list[str] = []
        for c in clauses:
            c = c.strip().strip(",;").strip()
            if len(c.split()) < 2:
                if result:
                    result[-1] = f"{result[-1].rstrip('.')} {c}."
                continue
            if not c.endswith((".", "!", "?")):
                c = c + "."
            result.append(c[0].upper() + c[1:] if c else c)
        return result

    @classmethod
    def _clause_has_verb(cls, clause: str) -> bool:
        for w in re.findall(r"[A-Za-z]+", clause.lower()):
            if w in cls._VERB_HINTS:
                return True
            # Gerund / past-participle inflection of a multi-character word.
            if len(w) >= 5 and (w.endswith("ing") or w.endswith("ed")):
                return True
        return False

    def _validate_claim_set(
        self,
        claims: list[ClaimObjectRecord],
        issues: list[ValidationIssue],
    ) -> None:
        """Final cross-claim validation (issue #312 hard errors)."""
        seen: set[str] = set()
        for c in claims:
            if c.claim_id in seen:
                issues.append(ValidationIssue(
                    rule_id="claim_id_duplicated",
                    severity="error",
                    message=f"duplicate claim_id {c.claim_id}",
                    field=c.claim_id,
                ))
            seen.add(c.claim_id)

            if c.support_status == "source_backed" and not c.source_evidence_ids:
                issues.append(ValidationIssue(
                    rule_id="source_backed_claim_no_evidence_ids",
                    severity="error",
                    message=(
                        f"source_backed claim {c.claim_id} has no source_evidence_ids; "
                        "PDF evidence cannot be traced"
                    ),
                    field=c.claim_id,
                ))

            for eid in c.source_evidence_ids:
                if self._known_evidence_ids and eid not in self._known_evidence_ids:
                    issues.append(ValidationIssue(
                        rule_id="claim_references_missing_evidence",
                        severity="error",
                        message=f"claim {c.claim_id} references missing evidence {eid}",
                        field=c.claim_id,
                    ))

    @staticmethod
    def _extract_review_note(span: object) -> str:
        # ClaimQualificationAgent stores review-style commentary in `reason` and
        # potentially in edit_suggestions["review_note"]. We never copy these into
        # evidence_text; they belong on the Claim's review_note field.
        edits = getattr(span, "edit_suggestions", {}) or {}
        explicit = edits.get("review_note")
        if explicit:
            return str(explicit)
        reason = getattr(span, "reason", "") or ""
        return reason.strip()

    @staticmethod
    def _derive_review_status(qual: dict) -> str:
        granularity = qual.get("granularity")
        adequacy = qual.get("evidence_adequacy")
        if granularity == "good" and adequacy == "sufficient":
            return "teacher_review_required"  # still require review per design
        return "teacher_review_required"
