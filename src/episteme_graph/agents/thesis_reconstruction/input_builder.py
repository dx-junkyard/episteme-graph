"""Build ThesisReconstructionAgent LLM input."""
from __future__ import annotations

from episteme_graph.agents.claim_qualification.schema import ClaimQualificationResult
from episteme_graph.agents.equation_semantics.schema import EquationSemanticsResult
from episteme_graph.agents.paper_skeleton.schema import PaperSkeletonResult
from episteme_graph.agents.claim_selection import (
    headline_text_for_selection,
    select_claim_rows,
)
from episteme_graph.agents.id_canonicalization import (
    canonical_claim_id_for_span,
    legacy_claim_id_for_span,
)

from .schema import CartridgeContext, ThesisLLMInput

_MAX_CLAIMS = 32
_MAX_EQUATIONS = 16
_MAX_LOGICAL_BLOCKS = 16


class ThesisReconstructionInputBuilder:
    def build(
        self,
        skeleton: PaperSkeletonResult,
        qualified_claims: ClaimQualificationResult,
        equations: EquationSemanticsResult | None = None,
        cartridge: CartridgeContext | None = None,
        config: dict | None = None,
        claim_objects=None,
    ) -> ThesisLLMInput:
        cfg = config or {}
        claim_selection = self._accepted_claims(
            qualified_claims,
            int(cfg.get("max_claims", _MAX_CLAIMS)),
            claim_objects=claim_objects,
            skeleton=skeleton,
        )
        accepted_claims = claim_selection.selected
        major_equations = self._major_equations(
            equations, int(cfg.get("max_equations", _MAX_EQUATIONS))
        )
        logical_blocks = self._logical_blocks(
            skeleton, int(cfg.get("max_logical_blocks", _MAX_LOGICAL_BLOCKS))
        )
        excluded_regions = list(skeleton.excluded_regions or [])
        excluded_regions.extend(self._claim_exclusions(qualified_claims))

        return ThesisLLMInput(
            document_id=skeleton.document_id,
            cartridge_id=cartridge.cartridge_id if cartridge else skeleton.cartridge_id,
            paper_goal=self._entry_text(skeleton.paper_goal),
            central_question=self._entry_text(skeleton.central_question),
            headline_claim=self._entry_text(skeleton.headline_claim),
            logical_blocks=logical_blocks,
            accepted_claims=accepted_claims,
            major_equations=major_equations,
            excluded_regions=excluded_regions,
            normalized_terms=self._build_normalized_terms(cartridge) if cartridge else None,
            excluded_from_pipeline_input=claim_selection.excluded,
        )

    @staticmethod
    def _entry_text(entry: dict) -> str | None:
        if isinstance(entry, dict):
            text = entry.get("text")
            return text if isinstance(text, str) and text.strip() else None
        return None

    @staticmethod
    def _logical_blocks(skeleton: PaperSkeletonResult, limit: int) -> list[dict]:
        result = []
        for block in skeleton.logical_blocks[:limit]:
            result.append({
                "block_id": block.block_id,
                "block_type": block.block_type,
                "label": block.label,
                "section_ids": block.section_ids,
                "evidence_block_ids": block.evidence_block_ids,
                "summary": block.summary,
                "confidence": block.confidence,
            })
        return result

    @staticmethod
    def _accepted_claims(
        result: ClaimQualificationResult,
        limit: int,
        claim_objects=None,
        skeleton=None,
    ):
        claim_index = {
            str(getattr(c, "claim_id", "") or ""): c
            for c in getattr(claim_objects, "claims", []) or []
        }
        claims = []
        for record in result.qualified_spans:
            claim_id = canonical_claim_id_for_span(record, claim_objects)
            claim_obj = claim_index.get(claim_id)
            claims.append({
                "claim_id": claim_id,
                "legacy_claim_id": legacy_claim_id_for_span(record),
                "span_id": record.span_id,
                "block_id": record.block_id,
                "section_id": record.section_id,
                # Human-readable section title (issue #359).
                "section_title": getattr(claim_obj, "section_title", None),
                "text": record.text,
                "role_labels": record.role_labels,
                "claim_tier": record.qualification.get("claim_tier"),
                "claim_type_candidate": record.qualification.get("claim_type_candidate"),
                "granularity": record.qualification.get("granularity"),
                "reason": record.reason,
                "confidence": record.confidence,
            })
        return select_claim_rows(
            claims,
            limit,
            stage="thesis_reconstruction",
            headline_text=headline_text_for_selection(skeleton=skeleton),
        )

    @staticmethod
    def _major_equations(
        equations: EquationSemanticsResult | None,
        limit: int,
    ) -> list[dict]:
        if not equations:
            return []
        result = []
        for record in equations.equations[:limit]:
            src = record.source_extraction
            sem = record.semantics
            loc = src.source_location if src.source_location else {}
            result.append({
                "equation_id": record.equation_id,
                "block_id": loc.get("block_id"),
                "section_id": loc.get("section_id"),
                "label": record.label,
                "role": sem.equation_type,
                "summary": sem.summary,
                "defined_symbols": [
                    {
                        "symbol": s.symbol,
                        "definition_status": s.definition_status,
                    }
                    for s in sem.defined_symbols
                ],
                "local_assumptions": list(sem.assumptions),
                "from_equations": list(sem.input_equation_ids),
                "confidence": sem.confidence,
            })
        return result

    @staticmethod
    def _claim_exclusions(result: ClaimQualificationResult) -> list[dict]:
        exclusions = []
        for record in result.rejected_spans:
            q = record.get("qualification", {})
            tier = q.get("claim_tier") or record.get("claim_tier")
            if tier in ("prior_work", "meta"):
                exclusions.append({
                    "category": tier,
                    "claim_ids": [f"claim:{record.get('block_id')}:{record.get('span_id')}"],
                    "reason": record.get("reason", ""),
                })
        return exclusions

    @staticmethod
    def _build_normalized_terms(cartridge: CartridgeContext) -> list[dict]:
        terms: list[dict] = []
        if cartridge.aliases:
            for canonical, aliases in cartridge.aliases.items():
                terms.append({"canonical": canonical, "aliases": aliases})
        hints = cartridge.extraction_hints
        if isinstance(hints, list):
            for hint in hints:
                if isinstance(hint, dict):
                    canonical = hint.get("canonical") or hint.get("label")
                    if canonical and not any(t.get("canonical") == canonical for t in terms):
                        terms.append({**hint, "canonical": canonical})
        elif isinstance(hints, dict):
            for canonical, value in hints.items():
                terms.append({"canonical": canonical, "hints": value})
        return terms if terms else []
