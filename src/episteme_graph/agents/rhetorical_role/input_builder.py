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

import re


from episteme_graph.agents.coverage_report import build_coverage_report
from episteme_graph.agents.document_structure.schema import DocumentStructureResult, TypedBlock
from episteme_graph.agents.equation_semantics.input_builder import (
    EquationSemanticsInputBuilder,
    context_excerpt,
)
from episteme_graph.agents.equation_semantics.normalizer import EquationNormalizer
from episteme_graph.agents.paper_skeleton.schema import PaperSkeletonResult
from episteme_graph.agents.text_truncation import truncate_text
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
#: ``preceding_equation`` で渡す式の長さの上限。
_MAX_PRECEDING_EQUATION_CHARS = 400
#: 式の続きとして書かれた段落の書き出し（IK-0714 精密化・2026-10-17 X1 seq 3334/3361）。
#: 小文字始まり（``up to an additive constant``）か、ここに挙げた続き語で始まるときだけ
#: 直前の式を材料に入れる。話題の変わった段落（``The problem of PVE …``）には渡さない。
_CONTINUATION_START_RE = re.compile(
    r"^(?:where\b|which\b|i\.\s*e\.|e\.\s*g\.|this\s+is\s+because\b|that\s+is\b"
    r"|in\s+the\s+(?:first|second|third|fourth|last|final)\s+line\b|here\s*,|with\b"
    r"|in\s+which\b|so\s+that\b|and\b)",
    re.IGNORECASE,
)


def starts_as_continuation(text: str) -> bool:
    """段落の書き出しが直前の式の続きに見えるか（決定論）。"""
    head = (text or "").lstrip(" \t\n,;:")
    if not head:
        return False
    if head[0].isalpha() and head[0].islower():
        return True
    return bool(_CONTINUATION_START_RE.match(head))


#: 直前の式を名指しする書き出し（IK-0714 claim_qualification 側・X2 seq 3428）。
#: 「The last factor encodes …」「This expression …」は大文字始まりでも直前の式の続き。
_EQUATION_ANAPHORA_START_RE = re.compile(
    r"^(?:the\s+(?:first|second|third|fourth|last|final|remaining|latter|former)\s+"
    r"(?:term|factor|line|piece|contribution|integral|expression)s?\b"
    r"|th(?:is|ese)\s+(?:equation|expression|relation|formula|integral)s?\b)",
    re.IGNORECASE,
)


def refers_to_preceding_equation(text: str) -> bool:
    """段落の書き出しが直前の式の続きか、直前の式を名指ししているか（決定論）。

    rhetorical_role の ``preceding_equation`` と claim_qualification の
    ``adjacent_equations``（前側）が共有する規則の正本。
    """
    if starts_as_continuation(text):
        return True
    head = (text or "").lstrip(" \t\n,;:")
    return bool(_EQUATION_ANAPHORA_START_RE.match(head))


#: 表示式を導入して終わる段落（「… can be written as」「… parameters,」「… given by:」）か。
#: 文末記号（. ! ?）で閉じていなければ、続く表示式が文の残り（IK-0714 claim_qualification 側）。
def ends_as_equation_intro(text: str) -> bool:
    tail = (text or "").rstrip()
    if not tail:
        return False
    return tail[-1] not in ".!?…"
_ELLIPSIS = "…"


def truncate_block_text(text: str, limit: int = _MAX_BLOCK_CHARS) -> tuple[str, bool]:
    """ブロック本文を ``limit`` 字以内（印込み）で、文境界か語境界で切る（O0 seq 2220 / IK-0808）。

    規則の正本は ``episteme_graph.agents.text_truncation.truncate_text``（文境界 → 語境界・
    ``$…$`` の外・切った側に ``…``）。戻り値の 2 つ目は切ったかどうか（切った事実は coverage に
    残す — P4）。切った本文は元の本文の先頭と同じ文字列なので、span のオフセットは元の本文に
    対しても同じ位置を指す。
    """
    text = text or ""
    if limit <= 0 or len(text) <= limit:
        return text, False
    budget = limit - len(_ELLIPSIS)
    if budget <= 0:
        return _ELLIPSIS, True
    return truncate_text(text, budget)

#: 対象ブロックの本文を上限で切って渡した理由（coverage ``details.truncated_blocks``）。
TRUNCATED_BLOCK_TEXT_REASON = "block_text_over_char_limit"

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
        truncated_text_block_ids: list[str] = []
        for idx in selected_indices:
            block = ordered_blocks[idx]
            section = sections_by_id.get(block.section_id or "")
            block_text, text_truncated = truncate_block_text(block.text, _MAX_BLOCK_CHARS)
            if text_truncated:
                truncated_text_block_ids.append(block.block_id)
            inputs.append(RoleLLMInput(
                document_id=structure.document_id,
                cartridge_id=cartridge.cartridge_id if cartridge else structure.cartridge_id,
                block_id=block.block_id,
                section_id=block.section_id,
                section_title=section.title if section else None,
                backbone_block_type=backbone_by_section.get(block.section_id or ""),
                headline_claim=headline_claim,
                block_text=block_text,
                prev_text=self._neighbor_text(ordered_blocks, idx, direction=-1),
                next_text=self._neighbor_text(ordered_blocks, idx, direction=1),
                normalized_terms=normalized_terms,
                preceding_equation=self._preceding_equation(ordered_blocks, idx),
                prev_text_other_section=self._neighbor_crosses_section(
                    ordered_blocks, idx, direction=-1
                ),
                next_text_other_section=self._neighbor_crosses_section(
                    ordered_blocks, idx, direction=1
                ),
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
        if truncated_text_block_ids:
            # 上限で本文の後ろを切って渡したブロック（ID のみ・件数は書かない）。ブロック自体は
            # 処理済みなので母集合・処理数・打ち切り数は変えない。
            coverage.setdefault("details", {})["truncated_text_block_ids"] = truncated_text_block_ids
            # 3 ステージ共通の形（IK-0808）: どのブロックを・なぜ切ったか。
            coverage["details"]["truncated_blocks"] = [
                {"block_id": block_id, "reason": TRUNCATED_BLOCK_TEXT_REASON}
                for block_id in truncated_text_block_ids
            ]
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
            # 表本体（``raw.in_table``）は前後の文脈にしない（IK-0806）。
            if block.block_type in _CONTEXT_BLOCK_TYPES and not _is_table_body(block):
                # 前の文脈は対象に近い末尾側、後ろの文脈は先頭側を残し、切った側に
                # 「…」を付ける（語の途中で切らない — IK-0808）。
                return truncate_text(
                    block.text or "", _MAX_CONTEXT_CHARS, keep_end=direction < 0
                )[0]
            i += direction
        return None

    @staticmethod
    def _neighbor_crosses_section(
        blocks: list[TypedBlock], index: int, direction: int
    ) -> bool:
        """``_neighbor_text`` が拾う文脈ブロックが別の節のものか（X1 seq 3376 / 3316）。

        前後の文脈は節境界をまたいで拾う（節の最初の段落にも前の流れを見せる）が、
        別の節の文だと分かる見出しを付けないと、前節の末尾（付録への言及など）を
        この段落の前置きとして読ませてしまう。
        """
        if not (0 <= index < len(blocks)):
            return False
        own_section = getattr(blocks[index], "section_id", None)
        i = index + direction
        while 0 <= i < len(blocks):
            block = blocks[i]
            if block.block_type in _CONTEXT_BLOCK_TYPES and not _is_table_body(block):
                return getattr(block, "section_id", None) != own_section
            i += direction
        return False

    @staticmethod
    def _preceding_equation(blocks: list[TypedBlock], index: int) -> dict | None:
        """直前の文脈ブロックが同じ小節の式ブロックで、本文が続き語で始まるときだけその式。

        IK-0714 の精密化（2026-10-17 X1 seq 3334 / 3361）: 「直前 2 ブロック以内」では
        話題の変わった段落にも前の式を「continue from it」で渡し、誤帰属を誘った。
        常には渡さない（入力長を増やさない）。TeX 由来の式だけ本文を載せ、PDF 由来の式は
        信用しないので ``latex`` を None にしてラベルだけ渡す（信頼境界 TB1）。
        """
        if not (0 <= index < len(blocks)):
            return None
        current = blocks[index]
        if not refers_to_preceding_equation(getattr(current, "text", "") or ""):
            return None
        own_section = getattr(current, "section_id", None)
        i = index - 1
        while i >= 0:
            block = blocks[i]
            if getattr(block, "section_id", None) != own_section:
                return None
            if block.block_type in _CONTEXT_BLOCK_TYPES:
                if block.block_type != "equation_block":
                    return None
                label = (
                    EquationNormalizer.extract_label(block.text or "")
                    or getattr(block, "equation_label", None)
                )
                latex = None
                if EquationSemanticsInputBuilder._is_trusted_tex_block(block):
                    latex = context_excerpt(block.text or "", _MAX_PRECEDING_EQUATION_CHARS)
                return {"block_id": block.block_id, "label": label, "latex": latex}
            i -= 1
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
