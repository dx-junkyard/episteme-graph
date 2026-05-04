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
    ClaimConcept,
    ClaimObjectBuildResult,
    ClaimObjectRecord,
    REVIEW_STATUSES,
    SUPPORT_STATUSES,
    ValidationIssue,
)


_DEFAULT_CLAIM_TYPE = "unknown"


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
    """

    def __init__(
        self,
        evidence_registry: object | None = None,
        equation_index: dict[str, object] | None = None,
        concept_resolver: Optional[Callable] = None,
        cartridge_ontology: dict | None = None,
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
            claim_id = self._make_claim_id(document_id, counter, span)
            if claim_id in seen_claim_ids:
                claim_id = f"{claim_id}_{counter}"
            seen_claim_ids.add(claim_id)

            text = self._extract_normalized_text(span)
            claim_type = qual.get("claim_type_candidate") or _DEFAULT_CLAIM_TYPE
            block_id = getattr(span, "block_id", None)
            section_id = getattr(span, "section_id", None)
            role_labels = list(getattr(span, "role_labels", []) or [])

            evidence_ids = self._resolve_evidence_ids(block_id)
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

            equation_ids = self._link_equations(text, claim_type, role_labels)
            if self._claim_type_implies_equation(claim_type) and not equation_ids:
                issues.append(ValidationIssue(
                    rule_id="claim_missing_equation_ref",
                    severity="warning",
                    message=(
                        f"claim {claim_id} of type {claim_type} should reference equations"
                    ),
                    field=claim_id,
                ))

            figure_ids, table_ids = self._link_figures_tables(role_labels, claim_type)

            review_note = self._extract_review_note(span)
            review_status = self._derive_review_status(qual)

            record = ClaimObjectRecord(
                claim_id=claim_id,
                document_id=document_id,
                claim_type=claim_type,
                text=text,
                source_evidence_ids=evidence_ids,
                source_span_ids=[getattr(span, "span_id", "")] if getattr(span, "span_id", None) else [],
                concepts=concepts,
                equation_ids=equation_ids,
                figure_ids=figure_ids,
                table_ids=table_ids,
                support_status="source_backed" if evidence_ids else "inferred",
                review_status=review_status,
                review_note=review_note,
                section_id=section_id,
                confidence=float(getattr(span, "confidence", 0.0) or 0.0),
            )
            claims.append(record)

        return ClaimObjectBuildResult(
            document_id=document_id,
            cartridge_id=cartridge_id,
            claims=claims,
            validation_issues=issues,
        )

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
        return found

    def _link_equations(self, text: str, claim_type: str, role_labels: list[str]) -> list[str]:
        if not self._equation_index:
            return []
        # Match by equation label e.g. "(3.14)" or "Eq. 3.14"
        ids: list[str] = []
        for eq_id, eq in self._equation_index.items():
            label = getattr(eq, "label", None) or (eq.get("label") if isinstance(eq, dict) else None)
            if not label:
                continue
            if label and (
                f"({label})" in text
                or f"Eq. {label}" in text
                or f"eq. {label}" in text
                or f"equation {label}" in text.lower()
            ):
                ids.append(eq_id)
        return ids

    @staticmethod
    def _claim_type_implies_equation(claim_type: str) -> bool:
        return claim_type in {
            "equation_definition",
            "equation_relation",
            "equation_transformation",
            "derivation_step",
        }

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
