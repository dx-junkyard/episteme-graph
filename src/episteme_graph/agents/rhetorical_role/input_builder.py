"""Build RhetoricalRoleAgent LLM inputs from structure and skeleton results."""
from __future__ import annotations

from episteme_graph.agents.document_structure.schema import DocumentStructureResult, TypedBlock
from episteme_graph.agents.paper_skeleton.schema import PaperSkeletonResult

from .schema import CartridgeContext, RoleLLMInput

_TARGET_BLOCK_TYPES = {"body_paragraph"}
_CONTEXT_BLOCK_TYPES = {"body_paragraph", "equation_block"}
_MAX_BLOCKS = 64
_MAX_CONTEXT_CHARS = 280
_MAX_BLOCK_CHARS = 1800


class RhetoricalRoleInputBuilder:
    """DocumentStructureResult + PaperSkeletonResult -> per-block RoleLLMInput."""

    def build(
        self,
        structure: DocumentStructureResult,
        skeleton: PaperSkeletonResult,
        cartridge: CartridgeContext | None = None,
        config: dict | None = None,
    ) -> list[RoleLLMInput]:
        cfg = config or {}
        max_blocks = int(cfg.get("max_blocks", _MAX_BLOCKS))
        include_equation_blocks = bool(cfg.get("include_equation_blocks", False))

        target_types = set(_TARGET_BLOCK_TYPES)
        if include_equation_blocks:
            target_types.add("equation_block")

        sections_by_id = {s.section_id: s for s in structure.sections}
        backbone_by_section = self._map_backbone_by_section(skeleton)
        ordered_blocks = sorted(structure.blocks, key=lambda b: (b.page, b.order))
        normalized_terms = self._build_normalized_terms(cartridge) if cartridge else None
        headline_claim = self._headline_text(skeleton)

        inputs: list[RoleLLMInput] = []
        for idx, block in enumerate(ordered_blocks):
            if block.block_type not in target_types:
                continue
            section = sections_by_id.get(block.section_id or "")
            inputs.append(RoleLLMInput(
                document_id=structure.document_id,
                cartridge_id=cartridge.cartridge_id if cartridge else structure.cartridge_id,
                block_id=block.block_id,
                section_id=block.section_id,
                section_title=section.title if section else None,
                backbone_block_type=backbone_by_section.get(block.section_id or ""),
                headline_claim=headline_claim,
                block_text=block.text[:_MAX_BLOCK_CHARS],
                prev_text=self._neighbor_text(ordered_blocks, idx, direction=-1),
                next_text=self._neighbor_text(ordered_blocks, idx, direction=1),
                normalized_terms=normalized_terms,
            ))
            if len(inputs) >= max_blocks:
                break

        return inputs

    @staticmethod
    def _map_backbone_by_section(skeleton: PaperSkeletonResult) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for logical_block in skeleton.logical_blocks:
            for section_id in logical_block.section_ids:
                mapping.setdefault(section_id, logical_block.block_type)
        return mapping

    @staticmethod
    def _headline_text(skeleton: PaperSkeletonResult) -> str | None:
        if not isinstance(skeleton.headline_claim, dict):
            return None
        text = skeleton.headline_claim.get("text")
        return text if isinstance(text, str) and text.strip() else None

    @staticmethod
    def _neighbor_text(
        blocks: list[TypedBlock],
        index: int,
        direction: int,
    ) -> str | None:
        i = index + direction
        while 0 <= i < len(blocks):
            block = blocks[i]
            if block.block_type in _CONTEXT_BLOCK_TYPES:
                return block.text[:_MAX_CONTEXT_CHARS]
            i += direction
        return None

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
