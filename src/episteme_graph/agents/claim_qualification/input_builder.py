"""Build ClaimQualificationAgent LLM inputs.

P0-1 と同じ是正をこのステージにも適用した（`docs/architecture/
knowledge_structure_review_2026-09-12.md` §4 / 付属資料 A）:

1. **既定では打ち切らない**。従来は ``_MAX_SPANS = 96`` で**文書順の先頭 96 span**
   に切り捨てていたため、結論・限界の節が丸ごと claim 化されない論文があった
   （実測: 803 ブロックの論文で先頭 151 ブロック分しか到達せず、§3〜§5 と付録の
   span が 1 件も採否判定されなかった）。既定は上限なし（0）で、上限を敷きたい
   運用だけが ``config["max_spans"]`` か env ``CLAIM_QUALIFICATION_MAX_SPANS``
   で明示する。
2. **上限がある場合も先頭切り捨てにしない**。節単位の層化サンプリング
   （正本 ``episteme_graph.agents.stratified_sampling``）で文書全体に配分する。
3. **取りこぼしの量を報告する**。``build_with_coverage()`` が
   ``agents/coverage_report.py::build_coverage_report`` の共通形式で返し、
   agent が ``summary_stats["coverage"]`` に載せる。
"""
from __future__ import annotations

from episteme_graph.agents.coverage_report import build_coverage_report
from episteme_graph.agents.document_structure.schema import DocumentStructureResult
from episteme_graph.agents.paper_skeleton.schema import PaperSkeletonResult
from episteme_graph.agents.rhetorical_role.schema import RhetoricalRoleResult, SpanAnnotation
from episteme_graph.agents.stratified_sampling import (
    NO_SECTION_KEY,
    resolve_limit,
    select_indices,
    unprocessed_section_entries,
)

from .schema import CartridgeContext, QualificationLLMInput

# 0 = 上限なし（既定）。かつての 96 打ち切りは廃止した。
_DEFAULT_MAX_SPANS = 0
_MAX_SPANS_ENV = "CLAIM_QUALIFICATION_MAX_SPANS"
_MAX_SOURCE_BLOCK_CHARS = 1800

_COVERAGE_UNIT = "spans"
_TRUNCATION_REASON = "max_spans"
# roles の並び（rhetorical_role が文書順に並べた結果）をそのまま使う。
_SORT_KEY = "role_annotation_order"


class _SpanSlot:
    """層化サンプリングにかける1件（span とその文脈）。"""

    __slots__ = ("annotation", "spans", "index")

    def __init__(self, annotation, spans: list[SpanAnnotation], index: int) -> None:
        self.annotation = annotation
        self.spans = spans
        self.index = index

    @property
    def span(self) -> SpanAnnotation:
        return self.spans[self.index]

    @property
    def section_key(self) -> str:
        return getattr(self.annotation, "section_id", None) or NO_SECTION_KEY


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
        """LLM 入力のリストのみを返す（既存シグネチャ・戻り値は不変）。"""
        inputs, _ = self.build_with_coverage(
            structure, skeleton, roles, cartridge=cartridge, config=config
        )
        return inputs

    def build_with_coverage(
        self,
        structure: DocumentStructureResult,
        skeleton: PaperSkeletonResult,
        roles: RhetoricalRoleResult,
        cartridge: CartridgeContext | None = None,
        config: dict | None = None,
    ) -> tuple[list[QualificationLLMInput], dict]:
        """LLM 入力と、取りこぼし報告（``build_coverage_report`` 形式）を返す。"""
        cfg = config or {}
        max_spans = resolve_limit(
            cfg, "max_spans", _MAX_SPANS_ENV, default=_DEFAULT_MAX_SPANS
        )
        include_reject_candidates = bool(cfg.get("include_reject_candidates", True))

        sections_by_id = {s.section_id: s for s in structure.sections}
        block_text_by_id = {b.block_id: b.text for b in structure.blocks}
        # 表本体（``raw.in_table``）の span は主張の候補にしない（R-4）。役割判定側でも
        # 母集合から外しているが、旧 run の roles artifact や別経路の annotation が
        # 混ざっても採否の母集合に表のセルを並べないよう、ここでも同じ判定を通す。
        table_block_ids = {
            b.block_id
            for b in structure.blocks
            if isinstance(getattr(b, "raw", None), dict)
            and (b.raw.get("in_table") or b.raw.get("from_table"))
        }
        normalized_terms = self._build_normalized_terms(cartridge) if cartridge else None
        headline_claim = self._headline_text(skeleton)

        slots: list[_SpanSlot] = []
        target_indices: list[int] = []
        excluded_table_block_ids: list[str] = []
        for annotation in roles.role_annotations:
            if annotation.block_id in table_block_ids:
                if annotation.block_id not in excluded_table_block_ids:
                    excluded_table_block_ids.append(annotation.block_id)
                continue
            spans = annotation.span_annotations
            for idx in range(len(spans)):
                slot = _SpanSlot(annotation, spans, idx)
                position = len(slots)
                slots.append(slot)
                if self._should_include(slot.span, include_reject_candidates):
                    target_indices.append(position)

        selected = select_indices(
            slots,
            target_indices,
            max_spans,
            section_key_of=lambda slot: slot.section_key,
        )

        inputs: list[QualificationLLMInput] = []
        for position in selected:
            slot = slots[position]
            annotation = slot.annotation
            span = slot.span
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
                prev_span_text=self._neighbor_span_text(slot.spans, slot.index, -1),
                next_span_text=self._neighbor_span_text(slot.spans, slot.index, 1),
                source_block_text=(block_text_by_id.get(annotation.block_id) or "")[:_MAX_SOURCE_BLOCK_CHARS],
                normalized_terms=normalized_terms,
            ))

        coverage = self._build_coverage(
            slots=slots,
            target_indices=target_indices,
            selected=selected,
            sections_by_id=sections_by_id,
        )
        if excluded_table_block_ids:
            coverage.setdefault("details", {})["excluded_table_block_ids"] = (
                excluded_table_block_ids
            )
        return inputs, coverage

    # ------------------------------------------------------------------

    @staticmethod
    def _build_coverage(
        slots: list[_SpanSlot],
        target_indices: list[int],
        selected: list[int],
        sections_by_id: dict,
    ) -> dict:
        population = len(target_indices)
        processed = len(selected)
        details: dict = {"sort_key": _SORT_KEY}
        unprocessed = unprocessed_section_entries(
            slots,
            target_indices,
            selected,
            section_key_of=lambda slot: slot.section_key,
            title_of=lambda key: getattr(sections_by_id.get(key), "title", None),
        )
        if unprocessed:
            details["unprocessed_sections"] = unprocessed
        return build_coverage_report(
            population=population,
            processed=processed,
            reasons=[_TRUNCATION_REASON] if population > processed else [],
            unit=_COVERAGE_UNIT,
            details=details,
        )

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
