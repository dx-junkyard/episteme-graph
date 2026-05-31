"""ClaimObjectBuilder.

QualifiedSpanRecord（ClaimQualificationAgent の出力）と EvidenceRegistry を入力に
最終 ClaimObjectRecord を組み立てる。

このモジュールは LLM を使わない。1 span = 1 claim を厳守する。
concepts / equation_ids は外部から渡される concept resolver / equation index に
基づいて付与する。
"""
from __future__ import annotations

import re
from typing import Callable, Iterable, Optional

from .schema import (
    CLAIM_TYPE_ONTOLOGY,
    ClaimConcept,
    ClaimObjectBuildResult,
    ClaimObjectRecord,
    EQUATION_CLAIM_TYPES,
    REVIEW_STATUSES,
    SUPPORT_STATUSES,
    ValidationIssue,
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
    ) -> None:
        self._evidence_registry = evidence_registry
        self._equation_index = equation_index or {}
        self._concept_resolver = concept_resolver
        self._cartridge_ontology = cartridge_ontology or {}
        self._span_to_evidence: dict[str, list[str]] = {}
        if evidence_registry is not None:
            for r in getattr(evidence_registry, "records", []) or []:
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

            # Handle split claims (atomicity — issue #260)
            edits = getattr(span, "edit_suggestions", {}) or {}
            split_candidates = edits.get("split_claims") or []
            if split_candidates and isinstance(split_candidates, list):
                parent_id = base_claim_id
                if parent_id in seen_claim_ids:
                    parent_id = f"{parent_id}_{counter}"
                seen_claim_ids.add(parent_id)
                subclaim_ids: list[str] = []
                for sub_idx, sub in enumerate(split_candidates, start=1):
                    sub_text = str(sub.get("text", "")).strip() if isinstance(sub, dict) else text
                    if not sub_text:
                        continue
                    sub_type = self._normalize_claim_type(
                        (sub.get("claim_type") if isinstance(sub, dict) else None)
                        or claim_type
                    )
                    sub_id = f"{parent_id}_sub{sub_idx:02d}"
                    if sub_id in seen_claim_ids:
                        sub_id = f"{sub_id}_{counter}"
                    seen_claim_ids.add(sub_id)
                    subclaim_ids.append(sub_id)
                    sub_concepts = self._resolve_concepts(sub_text, role_labels)
                    sub_eqs = self._link_equations(sub_text, sub_type, role_labels, block_id, section_id)
                    claims.append(ClaimObjectRecord(
                        claim_id=sub_id,
                        document_id=document_id,
                        claim_type=sub_type,
                        text=sub_text,
                        source_evidence_ids=evidence_ids,
                        source_span_ids=[span_id] if span_id else [],
                        concepts=sub_concepts,
                        equation_ids=sub_eqs,
                        figure_ids=[],
                        table_ids=[],
                        support_status="source_backed" if evidence_ids else "inferred",
                        review_status=review_status,
                        review_note=review_note,
                        section_id=section_id,
                        confidence=confidence,
                        atomicity="atomic",
                        parent_claim_id=parent_id,
                        subclaim_ids=[],
                    ))
                # Compound parent record (no text of its own, just tracks subclaims)
                claims.append(ClaimObjectRecord(
                    claim_id=parent_id,
                    document_id=document_id,
                    claim_type=claim_type,
                    text=text,
                    source_evidence_ids=evidence_ids,
                    source_span_ids=[span_id] if span_id else [],
                    concepts=self._resolve_concepts(text, role_labels),
                    equation_ids=self._link_equations(text, claim_type, role_labels, block_id, section_id),
                    figure_ids=[],
                    table_ids=[],
                    support_status="source_backed" if evidence_ids else "inferred",
                    review_status=review_status,
                    review_note=review_note,
                    section_id=section_id,
                    confidence=confidence,
                    atomicity="compound",
                    parent_claim_id=None,
                    subclaim_ids=subclaim_ids,
                ))
                self._add_issues_for_record(parent_id, evidence_ids, self._resolve_concepts(text, role_labels), claim_type, self._link_equations(text, claim_type, role_labels, block_id, section_id), issues)
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

            concepts = self._resolve_concepts(text, role_labels)
            if not concepts:
                issues.append(ValidationIssue(
                    rule_id="claim_concepts_empty",
                    severity="warning",
                    message=f"claim {claim_id} has no concepts",
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
                support_status="source_backed" if evidence_ids else "inferred",
                review_status=review_status,
                review_note=review_note,
                section_id=section_id,
                confidence=confidence,
                atomicity="atomic",
                parent_claim_id=None,
                subclaim_ids=[],
            )
            claims.append(record)

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
        if self._claim_type_implies_equation(claim_type) and not equation_ids:
            issues.append(ValidationIssue(
                rule_id="claim_missing_equation_ref",
                severity="warning",
                message=f"claim {claim_id} of type {claim_type} should reference equations",
                field=claim_id,
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

    def _resolve_concepts(self, text: str, role_labels: list[str]) -> list[ClaimConcept]:
        if self._concept_resolver is not None:
            try:
                resolved = self._concept_resolver(text, role_labels, self._cartridge_ontology)
                return [c for c in resolved if isinstance(c, ClaimConcept)]
            except Exception:
                pass
        # Fallback: match aliases from cartridge ontology
        ontology = self._cartridge_ontology or {}
        aliases = ontology.get("aliases", {}) or {}
        concept_types = ontology.get("concept_types", {}) or {}
        found: list[ClaimConcept] = []
        seen = set()
        text_lower = text.lower()
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
