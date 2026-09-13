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

ソートキーの判定規則（F-1 (c)）:
    ``TypedBlock.order`` は PyMuPDF パーサ・GROBID TEI パーサのどちらでも
    **文書全体で単調増加する通し番号**として振られる（ページ内で振り直されない）。
    一方 ``page`` は GROBID 経路では既定 1 で、PDF 本文との突合に成功したブロックだけ
    実ページに書き換わるため、**突合に失敗したブロックが page=1 のまま残る**（実測:
    936 ブロック中 212）。``(page, order)`` で並べるとこの取りこぼし群が文書先頭に
    集まり、順序が内容と無関係になる。
    そこで:
      - ``order`` が全ブロックで**一意**なら、それは文書全体の通し番号なので
        ``order`` のみで並べる（``sort_key = "order"``）。
      - 一意でないなら通し番号として信用できないので、従来どおり ``(page, order)``
        で並べる（``sort_key = "page_order"``）。
    どちらの規則で並べたかは coverage 報告の ``details.sort_key`` に必ず残す。

層化サンプリングの規則（F-1 (b)）:
    上限 ``max_blocks`` が対象ブロック数より小さいとき、各節（``block.section_id``。
    None は ``""`` の擬似節1つにまとめる）へ**対象ブロック数に比例**して配分する。
      1. 各節に最低 1 件（節数が上限を超える場合は、対象数の多い節から順に 1 件ずつ
         配って上限で止める）。
      2. 残り枠を ``floor(上限 × 節の対象数 / 全対象数)`` で配分（既に配った 1 件を含む）。
      3. 端数は「対象数の多い節」→「節の初出順」の順に 1 件ずつ配る。
      4. 節の中では**順序どおり先頭から**採る。
    節の初出順・対象数による比較はすべて決定論で、同数の節は初出順で安定する。
"""
from __future__ import annotations

import os

from episteme_graph.agents.coverage_report import build_coverage_report
from episteme_graph.agents.document_structure.schema import DocumentStructureResult, TypedBlock
from episteme_graph.agents.paper_skeleton.schema import PaperSkeletonResult

from .schema import CartridgeContext, RoleLLMInput

_TARGET_BLOCK_TYPES = {"body_paragraph"}
_CONTEXT_BLOCK_TYPES = {"body_paragraph", "equation_block"}
# 0 = 上限なし（既定）。かつての 64 打ち切りは P0-1 で廃止した。
_DEFAULT_MAX_BLOCKS = 0
_MAX_BLOCKS_ENV = "RHETORICAL_ROLE_MAX_BLOCKS"
_MAX_CONTEXT_CHARS = 280
_MAX_BLOCK_CHARS = 1800

_NO_SECTION_KEY = ""
_COVERAGE_UNIT = "blocks"
_TRUNCATION_REASON = "max_blocks"


def _env_max_blocks() -> int:
    """env ``RHETORICAL_ROLE_MAX_BLOCKS`` を既定上限として読む。

    A層 agent は backend の ``core.config`` を import できない（src は backend に
    依存しない）ため、ここで直接 env を読む。``core/config.py`` 側の
    ``rhetorical_role_max_blocks`` は同じ env の**宣言**で、値の正本は env そのもの。
    数値でない・負の値は 0（上限なし）として扱う（設定ミスで静かに打ち切らない）。
    """
    raw = os.environ.get(_MAX_BLOCKS_ENV)
    if raw is None:
        return _DEFAULT_MAX_BLOCKS
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return _DEFAULT_MAX_BLOCKS
    return value if value > 0 else _DEFAULT_MAX_BLOCKS


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
        if "max_blocks" in cfg:
            try:
                max_blocks = int(cfg.get("max_blocks") or 0)
            except (TypeError, ValueError):
                max_blocks = _DEFAULT_MAX_BLOCKS
            if max_blocks < 0:
                max_blocks = _DEFAULT_MAX_BLOCKS
        else:
            max_blocks = _env_max_blocks()
        include_equation_blocks = bool(cfg.get("include_equation_blocks", False))

        target_types = set(_TARGET_BLOCK_TYPES)
        if include_equation_blocks:
            target_types.add("equation_block")

        sections_by_id = {s.section_id: s for s in structure.sections}
        backbone_by_section = self._map_backbone_by_section(skeleton)
        ordered_blocks, sort_key_name = self._ordered_blocks(structure.blocks)
        normalized_terms = self._build_normalized_terms(cartridge) if cartridge else None
        headline_claim = self._headline_text(skeleton)

        target_indices = [
            idx
            for idx, block in enumerate(ordered_blocks)
            if block.block_type in target_types
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
        return inputs, coverage

    # ------------------------------------------------------------------
    # ordering / sampling
    # ------------------------------------------------------------------

    @staticmethod
    def _ordered_blocks(blocks: list[TypedBlock]) -> tuple[list[TypedBlock], str]:
        """並べ替えたブロックと、採用したソートキー名を返す。

        判定規則はモジュール docstring の「ソートキーの判定規則」を参照。
        """
        orders = [int(getattr(b, "order", 0) or 0) for b in blocks]
        order_is_unique = len(set(orders)) == len(orders)
        if order_is_unique:
            return (
                sorted(blocks, key=lambda b: int(getattr(b, "order", 0) or 0)),
                "order",
            )
        return (
            sorted(
                blocks,
                key=lambda b: (
                    int(getattr(b, "page", 0) or 0),
                    int(getattr(b, "order", 0) or 0),
                ),
            ),
            "page_order",
        )

    @classmethod
    def _select_indices(
        cls,
        ordered_blocks: list[TypedBlock],
        target_indices: list[int],
        max_blocks: int,
    ) -> list[int]:
        """対象ブロックの添字から、処理する添字を決定論的に選ぶ。

        上限なし（``max_blocks <= 0``）または上限が母集合以上なら全件。
        それ以外は節単位の層化サンプリング（モジュール docstring 参照）。
        """
        if max_blocks <= 0 or len(target_indices) <= max_blocks:
            return list(target_indices)

        # 節ごとの対象添字（節の初出順を保つ）
        by_section: dict[str, list[int]] = {}
        for idx in target_indices:
            key = ordered_blocks[idx].section_id or _NO_SECTION_KEY
            by_section.setdefault(key, []).append(idx)

        section_keys = list(by_section.keys())
        first_seen = {key: pos for pos, key in enumerate(section_keys)}
        total = len(target_indices)

        quota: dict[str, int] = {key: 0 for key in section_keys}
        remaining = max_blocks

        # 1. 各節に最低1件（枠が節数より少なければ、対象数の多い節から順に）
        priority = sorted(
            section_keys,
            key=lambda key: (-len(by_section[key]), first_seen[key]),
        )
        for key in priority:
            if remaining <= 0:
                break
            quota[key] = 1
            remaining -= 1

        # 2. 比例配分（既に配った1件を含む上限まで）
        if remaining > 0:
            for key in section_keys:
                if remaining <= 0:
                    break
                size = len(by_section[key])
                share = (max_blocks * size) // total
                extra = min(share - quota[key], size - quota[key], remaining)
                if extra > 0:
                    quota[key] += extra
                    remaining -= extra

        # 3. 端数は「対象数の多い節」→「初出順」で1件ずつ
        while remaining > 0:
            progressed = False
            for key in priority:
                if remaining <= 0:
                    break
                if quota[key] < len(by_section[key]):
                    quota[key] += 1
                    remaining -= 1
                    progressed = True
            if not progressed:
                break

        # 4. 節内は順序どおり先頭から
        selected: list[int] = []
        for key in section_keys:
            selected.extend(by_section[key][: quota[key]])
        selected.sort()
        return selected

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

        unprocessed_sections: list[dict] = []
        seen: set[str] = set()
        for idx in target_indices:
            if idx in selected_set:
                continue
            block = ordered_blocks[idx]
            key = block.section_id or _NO_SECTION_KEY
            if key in seen:
                continue
            seen.add(key)
            section = sections_by_id.get(key)
            unprocessed_sections.append({
                "section_id": block.section_id,
                "title": getattr(section, "title", None) if section else None,
            })

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
