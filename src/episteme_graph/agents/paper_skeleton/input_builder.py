"""SkeletonInputBuilder: DocumentStructureResult から LLM 入力を組み立てる。

設計方針:
- 全文投入ではなく骨格判断に必要な代表コンテキストを圧縮して渡す
- abstract → section headers → major section 代表 → conclusion の優先順序
- appendix は基本的に低優先度
"""
from __future__ import annotations

import re

from episteme_graph.agents.document_structure.schema import DocumentStructureResult, TypedBlock

from .schema import CartridgeContext, SkeletonLLMInput

_ABSTRACT_TITLES = re.compile(r"abstract", re.IGNORECASE)
_INTRO_TITLES = re.compile(r"introduction|intro\b", re.IGNORECASE)
_CONCLUSION_TITLES = re.compile(r"conclusion|discussion|summary|結論|まとめ", re.IGNORECASE)
_APPENDIX_TITLES = re.compile(r"appendix|supplementary|付録", re.IGNORECASE)

_MAX_ABSTRACT_BLOCKS = 8
_MAX_REP_BLOCKS_PER_SECTION = 2
_MAX_SECTIONS = 12
_MAX_CONCLUSION_BLOCKS = 6


class SkeletonInputBuilder:
    """DocumentStructureResult → SkeletonLLMInput の変換器。"""

    def build(
        self,
        structure: DocumentStructureResult,
        cartridge: CartridgeContext | None = None,
        config: dict | None = None,
    ) -> SkeletonLLMInput:
        cfg = config or {}
        max_sections: int = cfg.get("max_sections", _MAX_SECTIONS)

        blocks = structure.blocks
        sections = structure.sections

        title = structure.metadata.title

        abstract_blocks = self._extract_abstract(blocks, sections)
        section_headers = self._extract_section_headers(sections, max_sections)
        representative_blocks = self._extract_representative_blocks(blocks, sections, max_sections)
        conclusion_blocks = self._extract_conclusion_blocks(blocks, sections)
        normalized_terms = self._build_normalized_terms(cartridge) if cartridge else None

        return SkeletonLLMInput(
            document_id=structure.document_id,
            cartridge_id=cartridge.cartridge_id if cartridge else None,
            title=title,
            abstract_blocks=abstract_blocks,
            section_headers=section_headers,
            representative_blocks=representative_blocks,
            conclusion_blocks=conclusion_blocks,
            normalized_terms=normalized_terms,
        )

    # ------------------------------------------------------------------

    def _extract_abstract(
        self, blocks: list[TypedBlock], sections: list
    ) -> list[dict]:
        """Abstract セクション or 最初の見出し前の body_paragraph を返す。"""
        # Priority 1: section named "Abstract"
        abstract_section_ids = {
            s.section_id
            for s in sections
            if _ABSTRACT_TITLES.search(s.title or "")
        }
        if abstract_section_ids:
            result = [
                self._block_to_dict(b)
                for b in blocks
                if b.section_id in abstract_section_ids and b.block_type == "body_paragraph"
            ]
            if result:
                return result[:_MAX_ABSTRACT_BLOCKS]

        # Priority 2: body_paragraphs before the first section heading
        first_heading_order = next(
            (b.order for b in blocks if b.block_type in ("section_heading", "subsection_heading")),
            None,
        )
        if first_heading_order is not None:
            pre_heading = [
                self._block_to_dict(b)
                for b in blocks
                if b.order < first_heading_order and b.block_type == "body_paragraph"
            ]
            if pre_heading:
                return pre_heading[:_MAX_ABSTRACT_BLOCKS]

        # Priority 3: introduction first few paragraphs
        intro_section_ids = {
            s.section_id
            for s in sections
            if _INTRO_TITLES.search(s.title or "")
        }
        if intro_section_ids:
            result = [
                self._block_to_dict(b)
                for b in blocks
                if b.section_id in intro_section_ids and b.block_type == "body_paragraph"
            ]
            return result[:_MAX_ABSTRACT_BLOCKS]

        return []

    def _extract_section_headers(self, sections: list, max_sections: int) -> list[dict]:
        non_appendix = [
            s for s in sections
            if not _APPENDIX_TITLES.search(s.title or "")
        ]
        top_level = [s for s in non_appendix if s.level == 1][:max_sections]
        return [
            {
                "section_id": s.section_id,
                "title": s.title,
                "level": s.level,
                "page_start": s.page_start,
                "parent_section_id": s.parent_section_id,
            }
            for s in top_level
        ]

    def _extract_representative_blocks(
        self, blocks: list[TypedBlock], sections: list, max_sections: int
    ) -> list[dict]:
        """各 major section の先頭・末尾 body_paragraph を収集する。"""
        top_sections = [
            s for s in sections
            if s.level == 1 and not _APPENDIX_TITLES.search(s.title or "")
        ][:max_sections]

        result: list[dict] = []
        for section in top_sections:
            sec_blocks = [
                b for b in blocks
                if b.section_id == section.section_id and b.block_type == "body_paragraph"
            ]
            if not sec_blocks:
                continue
            # First and last paragraph
            selected = sec_blocks[:1]
            if len(sec_blocks) > 1:
                selected.append(sec_blocks[-1])
            for b in selected[:_MAX_REP_BLOCKS_PER_SECTION]:
                result.append({**self._block_to_dict(b), "section_title": section.title})

        return result

    def _extract_conclusion_blocks(
        self, blocks: list[TypedBlock], sections: list
    ) -> list[dict]:
        conclusion_section_ids = {
            s.section_id
            for s in sections
            if _CONCLUSION_TITLES.search(s.title or "")
        }
        if not conclusion_section_ids:
            return []
        result = [
            self._block_to_dict(b)
            for b in blocks
            if b.section_id in conclusion_section_ids and b.block_type == "body_paragraph"
        ]
        return result[:_MAX_CONCLUSION_BLOCKS]

    @staticmethod
    def _build_normalized_terms(cartridge: CartridgeContext) -> list[dict]:
        terms: list[dict] = []

        # Aliases
        if cartridge.aliases:
            for canonical, aliases in cartridge.aliases.items():
                terms.append({"canonical": canonical, "aliases": aliases})

        # Extraction hints from ontology
        hints = cartridge.extraction_hints
        if isinstance(hints, list):
            for hint in hints:
                if isinstance(hint, dict) and hint.get("canonical"):
                    if not any(t["canonical"] == hint["canonical"] for t in terms):
                        terms.append(hint)
        elif isinstance(hints, dict):
            for k, v in hints.items():
                terms.append({"canonical": k, "hints": v})

        return terms if terms else []

    @staticmethod
    def _block_to_dict(b: TypedBlock) -> dict:
        return {
            "block_id": b.block_id,
            "page": b.page,
            "block_type": b.block_type,
            "text": b.text[:500],  # truncate very long blocks
        }
