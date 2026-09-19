"""SkeletonInputBuilder: DocumentStructureResult から LLM 入力を組み立てる。

設計方針:
- 全文投入ではなく骨格判断に必要な代表コンテキストを圧縮して渡す
- abstract → section headers → major section 代表 → conclusion の優先順序
- appendix は基本的に低優先度

P0-1 と同じ是正（`docs/architecture/knowledge_structure_review_2026-09-12.md` §4）:

1. **既定では節を打ち切らない**。従来は ``_MAX_SECTIONS = 12`` で level-1 節の
   **先頭 12 件**しか骨格判断に見せていなかった。既定は上限なし（0）で、
   上限を敷きたい運用だけが ``config["max_sections"]`` か env
   ``PAPER_SKELETON_MAX_SECTIONS`` で明示する。
2. **上限がある場合も先頭切り捨てにしない**。先頭節と結論節を必ず含めたうえで、
   残り枠を文書全体へ等間隔に配る（結論・限界が骨格から丸ごと落ちない）。
3. **取りこぼしの量を報告する**。:meth:`compute_section_coverage` が母集合
   （level-1 節）・処理数・理由（``max_sections`` / ``appendix_excluded``）を返し、
   orchestrator が stage payload の ``coverage`` に載せる。
"""
from __future__ import annotations

import re

from episteme_graph.agents.document_structure.schema import DocumentStructureResult, TypedBlock
from episteme_graph.agents.stratified_sampling import resolve_limit

from .schema import CartridgeContext, SkeletonLLMInput

_ABSTRACT_TITLES = re.compile(r"abstract", re.IGNORECASE)
_INTRO_TITLES = re.compile(r"introduction|intro\b", re.IGNORECASE)
_CONCLUSION_TITLES = re.compile(r"conclusion|discussion|summary|結論|まとめ", re.IGNORECASE)
_APPENDIX_TITLES = re.compile(r"appendix|supplementary|付録", re.IGNORECASE)

_MAX_ABSTRACT_BLOCKS = 8
_MAX_REP_BLOCKS_PER_SECTION = 2
# 0 = 上限なし（既定）。かつての 12 打ち切りは廃止した。
_DEFAULT_MAX_SECTIONS = 0
_MAX_SECTIONS_ENV = "PAPER_SKELETON_MAX_SECTIONS"
_MAX_CONCLUSION_BLOCKS = 6

_COVERAGE_UNIT = "sections"
_TRUNCATION_REASON = "max_sections"
_APPENDIX_REASON = "appendix_excluded"


class SkeletonInputBuilder:
    """DocumentStructureResult → SkeletonLLMInput の変換器。"""

    def build(
        self,
        structure: DocumentStructureResult,
        cartridge: CartridgeContext | None = None,
        config: dict | None = None,
    ) -> SkeletonLLMInput:
        cfg = config or {}
        max_sections = resolve_limit(
            cfg, "max_sections", _MAX_SECTIONS_ENV, default=_DEFAULT_MAX_SECTIONS
        )

        blocks = structure.blocks
        sections = structure.sections

        title = structure.metadata.title

        selected = self._select_sections(sections, max_sections)
        abstract_blocks = self._extract_abstract(blocks, sections)
        section_headers = self._section_headers(selected)
        representative_blocks = self._extract_representative_blocks(blocks, selected)
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

    # ------------------------------------------------------------------
    # section selection (P0-1: 先頭切り捨てにしない)
    # ------------------------------------------------------------------

    @staticmethod
    def _top_level_sections(sections: list) -> list:
        return [s for s in sections if s.level == 1]

    @classmethod
    def _candidate_sections(cls, sections: list) -> list:
        """骨格判断の対象になる節（level-1 かつ appendix でない）。"""
        return [
            s for s in cls._top_level_sections(sections)
            if not _APPENDIX_TITLES.search(s.title or "")
        ]

    @classmethod
    def _select_sections(cls, sections: list, max_sections: int) -> list:
        """上限のもとで見せる節を決定論的に選ぶ。

        先頭切り捨てにしない: ①先頭節 ②結論・議論・まとめの節（末尾側から）を必ず
        含め、残り枠は文書順に等間隔で配る。出力は文書順。
        """
        candidates = cls._candidate_sections(sections)
        if max_sections <= 0 or len(candidates) <= max_sections:
            return candidates

        chosen: set[int] = set()
        # ① 先頭節（導入）
        chosen.add(0)
        # ② 結論系の節（末尾側を優先。上限を超えない範囲で）
        conclusion_positions = [
            idx for idx, s in enumerate(candidates)
            if _CONCLUSION_TITLES.search(s.title or "")
        ]
        for idx in reversed(conclusion_positions):
            if len(chosen) >= max_sections:
                break
            chosen.add(idx)
        # ③ 残り枠を等間隔に
        remaining = max_sections - len(chosen)
        if remaining > 0:
            total = len(candidates)
            step = total / (remaining + 1)
            for k in range(1, remaining + 1):
                pos = min(int(round(step * k)), total - 1)
                while pos in chosen and pos < total - 1:
                    pos += 1
                while pos in chosen and pos > 0:
                    pos -= 1
                chosen.add(pos)
        # ④ 端数調整（重複で埋まらなかった分を文書順に補う）
        if len(chosen) < max_sections:
            for idx in range(len(candidates)):
                if len(chosen) >= max_sections:
                    break
                chosen.add(idx)
        return [candidates[idx] for idx in sorted(chosen)[:max_sections]]

    def compute_section_coverage(
        self,
        structure: DocumentStructureResult,
        config: dict | None = None,
    ) -> dict:
        """節の母集合・処理数の**事実**を返す（``build_coverage_report`` の引数）。

        母集合は level-1 節（appendix を含む）。appendix は設計として除外するが、
        「見ていない」ことは理由コード ``appendix_excluded`` で正直に残す。
        報告そのものを組み立てないのは、orchestrator 側の ``_attach_coverage`` だけが
        ``coverage`` キーを作るという規律を保つため。
        """
        cfg = config or {}
        max_sections = resolve_limit(
            cfg, "max_sections", _MAX_SECTIONS_ENV, default=_DEFAULT_MAX_SECTIONS
        )
        sections = structure.sections or []
        top_level = self._top_level_sections(sections)
        candidates = self._candidate_sections(sections)
        selected = self._select_sections(sections, max_sections)
        selected_ids = {s.section_id for s in selected}

        reasons: list[str] = []
        if len(candidates) > len(selected):
            reasons.append(_TRUNCATION_REASON)
        if len(top_level) > len(candidates):
            reasons.append(_APPENDIX_REASON)

        details: dict = {"scope": "level_1_sections"}
        unprocessed = [
            {"section_id": s.section_id, "title": s.title}
            for s in top_level
            if s.section_id not in selected_ids
        ]
        if unprocessed:
            details["unprocessed_sections"] = unprocessed
        return {
            "population": len(top_level),
            "processed": len(selected),
            "reasons": reasons,
            "unit": _COVERAGE_UNIT,
            "details": details,
        }

    # ------------------------------------------------------------------

    @staticmethod
    def _section_headers(selected_sections: list) -> list[dict]:
        return [
            {
                "section_id": s.section_id,
                "title": s.title,
                "level": s.level,
                "page_start": s.page_start,
                "parent_section_id": s.parent_section_id,
            }
            for s in selected_sections
        ]

    def _extract_representative_blocks(
        self, blocks: list[TypedBlock], selected_sections: list
    ) -> list[dict]:
        """各 major section の先頭・末尾 body_paragraph を収集する。"""
        top_sections = selected_sections

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
