"""Build EquationSemanticsAgent LLM inputs."""
from __future__ import annotations

from episteme_graph.agents.document_structure.schema import DocumentStructureResult, TypedBlock
from episteme_graph.agents.paper_skeleton.schema import PaperSkeletonResult
from episteme_graph.agents.rhetorical_role.schema import RhetoricalRoleResult

from .normalizer import EquationNormalizer
from .schema import CartridgeContext, EquationLLMInput

_CONTEXT_BLOCK_TYPES = {"body_paragraph", "equation_block"}
_MAX_EQUATIONS = 64
_MAX_CONTEXT_BLOCKS = 2
_MAX_CONTEXT_CHARS = 500


class EquationSemanticsInputBuilder:
    def __init__(self, normalizer: EquationNormalizer | None = None) -> None:
        self._normalizer = normalizer or EquationNormalizer()

    def build(
        self,
        structure: DocumentStructureResult,
        skeleton: PaperSkeletonResult | None = None,
        roles: RhetoricalRoleResult | None = None,
        cartridge: CartridgeContext | None = None,
        config: dict | None = None,
    ) -> list[EquationLLMInput]:
        cfg = config or {}
        max_equations = int(cfg.get("max_equations", _MAX_EQUATIONS))
        context_blocks = int(cfg.get("context_blocks", _MAX_CONTEXT_BLOCKS))

        sections_by_id = {s.section_id: s for s in structure.sections}
        backbone_by_section = self._map_backbone_by_section(skeleton)
        spans_by_block = self._map_spans_by_block(roles)
        normalized_terms = self._build_normalized_terms(cartridge) if cartridge else None
        ordered_blocks = sorted(structure.blocks, key=lambda b: (b.page, b.order))

        inputs: list[EquationLLMInput] = []
        for idx, block in enumerate(ordered_blocks):
            if block.block_type != "equation_block":
                continue
            equation = self._normalizer.normalize(block)
            section = sections_by_id.get(block.section_id or "")
            inputs.append(EquationLLMInput(
                document_id=structure.document_id,
                cartridge_id=cartridge.cartridge_id if cartridge else structure.cartridge_id,
                equation_id=equation.equation_id,
                block_id=block.block_id,
                section_id=block.section_id,
                section_title=section.title if section else None,
                backbone_block_type=backbone_by_section.get(block.section_id or ""),
                label=equation.label,
                equation_text=equation.text,
                latex=equation.latex,
                plain_text=equation.plain_text,
                prev_texts=self._neighbor_texts(ordered_blocks, idx, -1, context_blocks),
                next_texts=self._neighbor_texts(ordered_blocks, idx, 1, context_blocks),
                nearby_span_annotations=spans_by_block.get(block.block_id, []),
                normalized_terms=normalized_terms,
            ))
            if len(inputs) >= max_equations:
                break
        return inputs

    @staticmethod
    def _map_backbone_by_section(
        skeleton: PaperSkeletonResult | None,
    ) -> dict[str, str]:
        if not skeleton:
            return {}
        mapping: dict[str, str] = {}
        for logical_block in skeleton.logical_blocks:
            for section_id in logical_block.section_ids:
                mapping.setdefault(section_id, logical_block.block_type)
        return mapping

    @staticmethod
    def _map_spans_by_block(roles: RhetoricalRoleResult | None) -> dict[str, list[dict]]:
        if not roles:
            return {}
        result: dict[str, list[dict]] = {}
        for annotation in roles.role_annotations:
            result[annotation.block_id] = [
                {
                    "span_id": span.span_id,
                    "text": span.text,
                    "role_labels": span.role_labels,
                    "is_claim_candidate": span.is_claim_candidate,
                    "is_reject_candidate": span.is_reject_candidate,
                }
                for span in annotation.span_annotations
            ]
        return result

    @staticmethod
    def _neighbor_texts(
        blocks: list[TypedBlock],
        index: int,
        direction: int,
        limit: int,
    ) -> list[str]:
        texts: list[str] = []
        i = index + direction
        while 0 <= i < len(blocks) and len(texts) < limit:
            block = blocks[i]
            if block.block_type in _CONTEXT_BLOCK_TYPES:
                texts.append(f"[{block.block_id}] {block.text[:_MAX_CONTEXT_CHARS]}")
            i += direction
        if direction < 0:
            texts.reverse()
        return texts

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
