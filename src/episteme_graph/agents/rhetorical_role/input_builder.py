"""Build RhetoricalRoleAgent LLM inputs from structure and skeleton results.

P0-1（`docs/architecture/knowledge_structure_review_2026-09-12.md` §4 / 付属資料 A の
F-1・F-18）の是正:

1. **既定では打ち切らない**。従来は ``_MAX_BLOCKS = 64`` で先頭 64 ブロックに切り捨てて
   いたため、936 ブロックの論文では 6.8% しか役割判定されず、構造化層の網羅性が
   「上限 × 壊れたページ番号」という事故で決まっていた。既定は上限なし（0）で、
   上限を敷きたい運用は ``config["max_blocks"]`` か env ``RHETORICAL_ROLE_MAX_BLOCKS``
   で明示する。
2. **上限がある場合も先頭切り捨てにしない**。節単位の層化サンプリング（下記）で
   文書全体に配分する。
3. **取りこぼしの量を報告する**。``build_with_coverage()`` が
   ``agents/coverage_report.py::build_coverage_report`` の共通形式で母集合・処理数・
   打ち切り数・理由・未処理節を返し、agent が ``summary_stats["coverage"]`` に載せる。

ソートキーの判定規則（F-1 (c)）と層化サンプリングの規則（F-1 (b)）の**正本は
``episteme_graph.agents.stratified_sampling``**（他ステージと共有）。このモジュールは
そこへ委譲し、ブロック種別の選別と RoleLLMInput の組み立てだけを持つ。
"""
from __future__ import annotations

from episteme_graph.agents.coverage_report import build_coverage_report
from episteme_graph.agents.document_structure.schema import DocumentStructureResult, TypedBlock
from episteme_graph.agents.paper_skeleton.schema import PaperSkeletonResult
from episteme_graph.agents.stratified_sampling import (
    NO_SECTION_KEY,
    order_blocks,
    resolve_limit,
    select_indices,
    unprocessed_section_entries,
)

from .schema import CartridgeContext, RoleLLMInput

_TARGET_BLOCK_TYPES = {"body_paragraph"}


def _is_table_body(block: object) -> bool:
    """表本体として残された body_paragraph か（``raw.in_table`` / ``raw.from_table``）。"""
    raw = getattr(block, "raw", None)
    if not isinstance(raw, dict):
        return False
    return bool(raw.get("in_table") or raw.get("from_table"))
_CONTEXT_BLOCK_TYPES = {"body_paragraph", "equation_block"}
# 0 = 上限なし（既定）。かつての 64 打ち切りは P0-1 で廃止した。
_DEFAULT_MAX_BLOCKS = 0
_MAX_BLOCKS_ENV = "RHETORICAL_ROLE_MAX_BLOCKS"
_MAX_CONTEXT_CHARS = 280
_MAX_BLOCK_CHARS = 1800

_NO_SECTION_KEY = NO_SECTION_KEY
_COVERAGE_UNIT = "blocks"
_TRUNCATION_REASON = "max_blocks"


class RhetoricalRoleInputBuilder:
    """DocumentStructureResult + PaperSkeletonResult -> per-block RoleLLMInput."""

    def build(
        self,
        structure: DocumentStructureResult,
        skeleton: PaperSkeletonResult,
        cartridge: CartridgeContext | None = None,
        config: dict | None = None,
    ) -> list[RoleLLMInput]:
        """LLM 入力のリストのみを返す（既存シグネチャ・戻り値は不変）。

        orchestrator の ``_agent_input_count`` が ``len(build(...))`` を呼ぶため、
        戻り値の形を変えない。coverage 報告も欲しい場合は
        :meth:`build_with_coverage` を使う。
        """
        inputs, _ = self.build_with_coverage(
            structure, skeleton, cartridge=cartridge, config=config
        )
        return inputs

    def build_with_coverage(
        self,
        structure: DocumentStructureResult,
        skeleton: PaperSkeletonResult,
        cartridge: CartridgeContext | None = None,
        config: dict | None = None,
    ) -> tuple[list[RoleLLMInput], dict]:
        """LLM 入力と、取りこぼし報告（``build_coverage_report`` 形式）を返す。"""
        cfg = config or {}
        max_blocks = resolve_limit(
            cfg, "max_blocks", _MAX_BLOCKS_ENV, default=_DEFAULT_MAX_BLOCKS
        )
        include_equation_blocks = bool(cfg.get("include_equation_blocks", False))

        target_types = set(_TARGET_BLOCK_TYPES)
        if include_equation_blocks:
            target_types.add("equation_block")

        sections_by_id = {s.section_id: s for s in structure.sections}
        backbone_by_section = self._map_backbone_by_section(skeleton)
        ordered_blocks, sort_key_name = self._ordered_blocks(structure.blocks)
        normalized_terms = self._build_normalized_terms(cartridge) if cartridge else None
        headline_claim = self._headline_text(skeleton)

        # 表本体（GROBID の <figure type="table"> を行テキストで残した body_paragraph、
        # ``raw.in_table``）は主張の候補ではないので母集合から外す（2026-09-19 レビュー
        # R-4: 役割判定 → 主張採否の母集合に表のセルが並んでいた）。除外した block は
        # ``details.excluded_table_block_ids`` に ID で残す（件数は書かない）。
        excluded_table_block_ids = [
            block.block_id for block in ordered_blocks if _is_table_body(block)
        ]
        target_indices = [
            idx
            for idx, block in enumerate(ordered_blocks)
            if block.block_type in target_types and not _is_table_body(block)
        ]
        selected_indices = self._select_indices(
            ordered_blocks, target_indices, max_blocks
        )
        selected_set = set(selected_indices)

        inputs: list[RoleLLMInput] = []
        for idx in selected_indices:
            block = ordered_blocks[idx]
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

        coverage = self._build_coverage(
            ordered_blocks=ordered_blocks,
            target_indices=target_indices,
            selected_set=selected_set,
            sections_by_id=sections_by_id,
            sort_key_name=sort_key_name,
        )
        if excluded_table_block_ids:
            coverage.setdefault("details", {})["excluded_table_block_ids"] = (
                excluded_table_block_ids
            )
        return inputs, coverage

    # ------------------------------------------------------------------
    # ordering / sampling
    # ------------------------------------------------------------------

    @staticmethod
    def _ordered_blocks(blocks: list[TypedBlock]) -> tuple[list[TypedBlock], str]:
        """並べ替えたブロックと、採用したソートキー名を返す（正本へ委譲）。"""
        return order_blocks(blocks)

    @classmethod
    def _select_indices(
        cls,
        ordered_blocks: list[TypedBlock],
        target_indices: list[int],
        max_blocks: int,
    ) -> list[int]:
        """対象ブロックの添字から、処理する添字を決定論的に選ぶ（正本へ委譲）。"""
        return select_indices(
            ordered_blocks,
            target_indices,
            max_blocks,
            section_key_of=lambda b: b.section_id or _NO_SECTION_KEY,
        )

    @staticmethod
    def _build_coverage(
        ordered_blocks: list[TypedBlock],
        target_indices: list[int],
        selected_set: set[int],
        sections_by_id: dict,
        sort_key_name: str,
    ) -> dict:
        """取りこぼし報告を共通形式で組み立てる（件数は details に載せない）。"""
        population = len(target_indices)
        processed = len(selected_set)
        truncated = population - processed

        unprocessed_sections = unprocessed_section_entries(
            ordered_blocks,
            target_indices,
            selected_set,
            section_key_of=lambda b: b.section_id or _NO_SECTION_KEY,
            title_of=lambda key: getattr(sections_by_id.get(key), "title", None),
        )

        details: dict = {"sort_key": sort_key_name}
        if unprocessed_sections:
            details["unprocessed_sections"] = unprocessed_sections
        return build_coverage_report(
            population=population,
            processed=processed,
            reasons=[_TRUNCATION_REASON] if truncated > 0 else [],
            unit=_COVERAGE_UNIT,
            details=details,
        )

    # ------------------------------------------------------------------
    # context helpers
    # ------------------------------------------------------------------

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
