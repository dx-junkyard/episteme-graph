"""Build ClaimQualificationAgent LLM inputs."""
from __future__ import annotations

from episteme_graph.agents.document_structure.schema import DocumentStructureResult
from episteme_graph.agents.paper_skeleton.schema import PaperSkeletonResult
from episteme_graph.agents.rhetorical_role.schema import RhetoricalRoleResult, SpanAnnotation

from .schema import CartridgeContext, QualificationLLMInput

_MAX_SPANS = 96
_MAX_SOURCE_BLOCK_CHARS = 1800


class ClaimQualificationInputBuilder:
    """Package role-labeled spans with local and paper-level context."""

    def build(
        self,
        structure: DocumentStructureResult,
        skeleton: PaperSkeletonResult,
        roles: RhetoricalRoleResult,
        cartridge: CartridgeContext | None = None,
        config: dict | None = None,
    ) -> list[QualificationLLMInput]:
        cfg = config or {}
        max_spans = int(cfg.get("max_spans", _MAX_SPANS))
        include_reject_candidates = bool(cfg.get("include_reject_candidates", True))

        sections_by_id = {s.section_id: s for s in structure.sections}
        block_text_by_id = {b.block_id: b.text for b in structure.blocks}
        normalized_terms = self._build_normalized_terms(cartridge) if cartridge else None
        headline_claim = self._headline_text(skeleton)

        inputs: list[QualificationLLMInput] = []
        for annotation in roles.role_annotations:
            spans = annotation.span_annotations
            for idx, span in enumerate(spans):
                if not self._should_include(span, include_reject_candidates):
                    continue
                section = sections_by_id.get(annotation.section_id or "")
                inputs.append(QualificationLLMInput(
                    document_id=roles.document_id,
                    cartridge_id=cartridge.cartridge_id if cartridge else roles.cartridge_id,
                    span_id=span.span_id,
                    block_id=annotation.block_id,
                    section_id=annotation.section_id,
                    section_title=section.title if section else None,
                    backbone_block_type=annotation.backbone_block_type,
                    headline_claim=headline_claim,
                    span_text=span.text,
                    role_labels=span.role_labels,
                    is_claim_candidate=span.is_claim_candidate,
                    is_reject_candidate=span.is_reject_candidate,
                    prev_span_text=self._neighbor_span_text(spans, idx, -1),
                    next_span_text=self._neighbor_span_text(spans, idx, 1),
                    source_block_text=(block_text_by_id.get(annotation.block_id) or "")[:_MAX_SOURCE_BLOCK_CHARS],
                    normalized_terms=normalized_terms,
                ))
                if len(inputs) >= max_spans:
                    return inputs

        return inputs

    @staticmethod
    def _should_include(span: SpanAnnotation, include_reject_candidates: bool) -> bool:
        if span.is_claim_candidate:
            return True
        if include_reject_candidates and span.is_reject_candidate:
            return True
        return "unknown" in span.role_labels

    @staticmethod
    def _neighbor_span_text(
        spans: list[SpanAnnotation],
        index: int,
        direction: int,
    ) -> str | None:
        i = index + direction
        if 0 <= i < len(spans):
            return spans[i].text
        return None

    @staticmethod
    def _headline_text(skeleton: PaperSkeletonResult) -> str | None:
        if not isinstance(skeleton.headline_claim, dict):
            return None
        text = skeleton.headline_claim.get("text")
        return text if isinstance(text, str) and text.strip() else None

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
