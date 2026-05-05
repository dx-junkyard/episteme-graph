"""Build EquationSemanticsAgent LLM inputs.

Issue #245: build_candidates() で EquationCandidate を生成し、
acceptance gate 通過済みの accepted 候補のみ LLM 入力を作る。
"""
from __future__ import annotations

import uuid

from episteme_graph.agents.document_structure.schema import DocumentStructureResult, TypedBlock
from episteme_graph.agents.paper_skeleton.schema import PaperSkeletonResult
from episteme_graph.agents.rhetorical_role.schema import RhetoricalRoleResult

from .normalizer import EquationNormalizer
from .schema import (
    CartridgeContext,
    EquationCandidate,
    EquationLLMInput,
)

_CONTEXT_BLOCK_TYPES = {"body_paragraph", "equation_block"}
_MAX_EQUATIONS = 64
_MAX_CONTEXT_BLOCKS = 2
_MAX_CONTEXT_CHARS = 500

# extraction_status のうち再構成が必要なもの
_NEEDS_RECONSTRUCTION_STATUSES = {"partial", "fragment_only", "label_only", "missing", "unparsed"}


class EquationSemanticsInputBuilder:
    def __init__(self, normalizer: EquationNormalizer | None = None) -> None:
        self._normalizer = normalizer or EquationNormalizer()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_candidates(
        self,
        structure: DocumentStructureResult,
        skeleton: PaperSkeletonResult | None = None,
        roles: RhetoricalRoleResult | None = None,
        cartridge: CartridgeContext | None = None,
        config: dict | None = None,
    ) -> list[EquationCandidate]:
        """全 equation_block から EquationCandidate を生成する (acceptance gate 適用前)。"""
        cfg = config or {}
        max_equations = int(cfg.get("max_equations", _MAX_EQUATIONS))
        ordered_blocks = sorted(structure.blocks, key=lambda b: (b.page, b.order))

        candidates: list[EquationCandidate] = []
        for block in ordered_blocks:
            if block.block_type != "equation_block":
                continue
            candidates.append(self._block_to_candidate(block, structure.document_id))
            if len(candidates) >= max_equations:
                break
        return candidates

    def build_llm_inputs(
        self,
        structure: DocumentStructureResult,
        accepted_candidates: list[EquationCandidate],
        skeleton: PaperSkeletonResult | None = None,
        roles: RhetoricalRoleResult | None = None,
        cartridge: CartridgeContext | None = None,
    ) -> list[EquationLLMInput]:
        """accepted_candidates に対して LLM 入力を構築する。"""
        if not accepted_candidates:
            return []

        # accepted のみをフィルタ (accepted_status="accepted" のもの)
        _accepted = [c for c in accepted_candidates if c.acceptance_status == "accepted"]
        if not _accepted:
            return []
        accepted_block_ids = {c.source_location.get("block_id") for c in _accepted}
        candidate_by_block_id = {c.source_location.get("block_id"): c for c in _accepted}

        sections_by_id = {s.section_id: s for s in structure.sections}
        backbone_by_section = self._map_backbone_by_section(skeleton)
        spans_by_block = self._map_spans_by_block(roles)
        normalized_terms = self._build_normalized_terms(cartridge) if cartridge else None
        ordered_blocks = sorted(structure.blocks, key=lambda b: (b.page, b.order))

        inputs: list[EquationLLMInput] = []
        for idx, block in enumerate(ordered_blocks):
            if block.block_id not in accepted_block_ids:
                continue
            candidate = candidate_by_block_id[block.block_id]
            equation = self._normalizer.normalize(block)
            section = sections_by_id.get(block.section_id or "")
            needs_reconstruction = candidate.extraction_status in _NEEDS_RECONSTRUCTION_STATUSES

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
                prev_texts=self._neighbor_texts(ordered_blocks, idx, -1, _MAX_CONTEXT_BLOCKS),
                next_texts=self._neighbor_texts(ordered_blocks, idx, 1, _MAX_CONTEXT_BLOCKS),
                nearby_span_annotations=spans_by_block.get(block.block_id, []),
                normalized_terms=normalized_terms,
                candidate_id=candidate.candidate_id,
                extraction_status=candidate.extraction_status,
                acceptance_status=candidate.acceptance_status,
                needs_reconstruction=needs_reconstruction,
            ))
        return inputs

    # ------------------------------------------------------------------
    # Legacy build() — backward compat: runs candidates + accepted filter internally
    # (used by tests that don't use the new pipeline)
    # ------------------------------------------------------------------

    def build(
        self,
        structure: DocumentStructureResult,
        skeleton: PaperSkeletonResult | None = None,
        roles: RhetoricalRoleResult | None = None,
        cartridge: CartridgeContext | None = None,
        config: dict | None = None,
    ) -> list[EquationLLMInput]:
        """全 equation_block から LLM 入力を直接生成 (acceptance gate なし)。

        agent.py は新パイプライン (build_candidates → acceptance_gate → build_llm_inputs)
        を使う。このメソッドは旧 API 互換のために残す。
        """
        candidates = self.build_candidates(structure, skeleton=skeleton, roles=roles, cartridge=cartridge, config=config)
        # acceptance gate なしで全候補を accepted として扱う
        for c in candidates:
            if not c.acceptance_status:
                c.acceptance_status = "accepted"
        return self.build_llm_inputs(
            structure, candidates, skeleton=skeleton, roles=roles, cartridge=cartridge
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _block_to_candidate(self, block: TypedBlock, document_id: str) -> EquationCandidate:
        equation = self._normalizer.normalize(block)
        detection: list[str] = ["document_structure_equation_block"]
        if equation.label:
            detection.append("equation_number_pattern")

        bbox_list: list[float] = []
        if block.bbox:
            bbox_list = list(block.bbox)

        return EquationCandidate(
            candidate_id=f"eqcand_{block.block_id}_{uuid.uuid4().hex[:8]}",
            document_id=document_id,
            source_location={
                "page": block.page,
                "section_id": block.section_id,
                "block_id": block.block_id,
                "span_start": 0,
                "span_end": len(block.text),
                "bbox": bbox_list,
            },
            raw_text=block.text.strip(),
            matched_label=equation.label,
            detection_method=detection,
            candidate_score=block.confidence,
            extraction_status="unparsed",   # gate が上書きする
            acceptance_status="rejected",   # gate が上書きする
            accepted_equation_id=None,
            merge_target_hint=None,
            needs_math_review=False,
            review_reason=[],
        )

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
