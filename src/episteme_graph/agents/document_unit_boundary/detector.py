"""UnitDetector: ヒューリスティックでPDF内の document unit 境界を検出する。

設計方針:
- heuristic-first（LLMなし）
- 単一論文PDFと複数記事/章を持つPDFの両方を扱う
- 検出できない場合は全体を unknown_unit として扱う（情報を削除しない）
- cartridge があれば用語を補助信号として使用（依存はしない）
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from episteme_graph.agents.document_structure.schema import TypedBlock

from .schema import CartridgeContext, DocumentUnit, ExcludedBlock

# ---------------------------------------------------------------------------
# Compiled patterns
# ---------------------------------------------------------------------------

# Chapter markers: "Chapter 1", "CHAPTER 1", "第1章", "第 1 章"
_CHAPTER_RE = re.compile(
    r"^(Chapter|CHAPTER|第\s*\d+\s*章)\b",
    re.UNICODE,
)

# Abstract marker
_ABSTRACT_RE = re.compile(
    r"^(Abstract|ABSTRACT|要旨|概要|アブストラクト)\s*$",
    re.IGNORECASE,
)

# Introduction marker
_INTRO_RE = re.compile(
    r"^(\d+\.?\s*)?(Introduction|INTRODUCTION|はじめに|序論|背景)\s*$",
    re.IGNORECASE | re.UNICODE,
)

# Front matter markers (TOC, editorial, etc.)
_FRONT_MATTER_RE = re.compile(
    r"^(Contents|Table of Contents|目次|Editor|Editorial|Preface|Foreword|About the|Introduction to|Overview of)\b",
    re.IGNORECASE | re.UNICODE,
)

# Back matter markers
_BACK_MATTER_HEADER_RE = re.compile(
    r"^(References|Bibliography|REFERENCES|参考文献|文献|Acknowledgements?|謝辞|Appendix|APPENDIX|付録|Index|索引|Colophon|奥付)\s*$",
    re.IGNORECASE | re.UNICODE,
)

# Author-like block heuristics (comma-separated names, short, often italic or smaller font)
_AUTHOR_HINT_RE = re.compile(
    r"^[A-Z][a-z]+(\s+[A-Z][a-z\.]+)*"  # Firstname Lastname
    r"(\s*,\s*[A-Z][a-z]+(\s+[A-Z][a-z\.]+)*)*"  # , Another Name
    r"(\s+(and|&)\s+[A-Z][a-z]+(\s+[A-Z][a-z\.]+)*)?\s*$"  # and/& Last Author
)

# Title confidence threshold — units with confidence below this need review
_CONFIDENCE_THRESHOLD = 0.55

# high confidence threshold — for title-missing checks
_HIGH_CONFIDENCE_THRESHOLD = 0.70


@dataclass
class _Span:
    """block インデックスの [start, end) 区間と推測される unit_type。"""
    start_idx: int
    end_idx: int      # exclusive
    unit_type: str
    title_idx: int | None = None
    author_indices: list[int] = field(default_factory=list)
    confidence: float = 0.6
    review_reasons: list[str] = field(default_factory=list)


class UnitDetector:
    """TypedBlock リストから DocumentUnit / ExcludedBlock を検出する。"""

    def detect(
        self,
        blocks: list[TypedBlock],
        document_id: str,
        cartridge: CartridgeContext | None = None,
    ) -> tuple[list[DocumentUnit], list[ExcludedBlock], float, list[str]]:
        """
        Returns
        -------
        units:
            検出された DocumentUnit のリスト。
        excluded_blocks:
            いずれの unit にも属さないブロック。
        overall_confidence:
            unit detection の総合信頼度。
        review_reasons:
            needs_review の理由コードリスト。
        """
        if not blocks:
            return [], [], 0.0, ["title_not_detected"]

        # Step 1: front matter / back matter の境界を求める
        front_end_idx = self._find_front_matter_end(blocks)
        back_start_idx = self._find_back_matter_start(blocks)

        body_blocks = blocks[front_end_idx:back_start_idx]

        # Step 2: body 内で複数 unit（複数 article / chapter）を検出
        spans = self._detect_unit_spans(body_blocks, front_end_idx, cartridge)

        # Step 3: DocumentUnit / ExcludedBlock に変換
        included_ids: set[str] = set()
        units: list[DocumentUnit] = []

        for i, span in enumerate(spans):
            unit_blocks = blocks[span.start_idx: span.end_idx]
            if not unit_blocks:
                continue

            title_block = blocks[span.title_idx] if span.title_idx is not None else None
            authors = self._extract_authors(blocks, span.author_indices)

            unit = DocumentUnit(
                unit_id=f"unit_{i + 1:03d}",
                unit_type=span.unit_type,
                title=title_block.text.strip() if title_block else None,
                title_block_id=title_block.block_id if title_block else None,
                authors=authors,
                start_page=unit_blocks[0].page,
                start_block_id=unit_blocks[0].block_id,
                end_page=unit_blocks[-1].page,
                end_block_id=unit_blocks[-1].block_id,
                confidence=span.confidence,
                needs_review=bool(span.review_reasons),
                review_reasons=list(span.review_reasons),
                included_block_ids=[b.block_id for b in unit_blocks],
            )
            units.append(unit)
            included_ids.update(unit.included_block_ids)

        # Step 4: 除外ブロックを収集
        excluded_blocks: list[ExcludedBlock] = []
        for idx, block in enumerate(blocks):
            if block.block_id in included_ids:
                continue
            if idx < front_end_idx:
                reason = "front_matter"
            elif idx >= back_start_idx:
                reason = "back_matter"
            else:
                reason = "outside_target_unit"
            excluded_blocks.append(
                ExcludedBlock(block_id=block.block_id, page=block.page, reason=reason)
            )

        # Step 5: 総合信頼度 / needs_review 計算
        overall_confidence, review_reasons = self._compute_overall_confidence(
            units, spans, blocks, front_end_idx, back_start_idx
        )

        return units, excluded_blocks, overall_confidence, review_reasons

    # ------------------------------------------------------------------
    # Front / back matter detection
    # ------------------------------------------------------------------

    def _find_front_matter_end(self, blocks: list[TypedBlock]) -> int:
        """front matter が終わり body が始まる先頭インデックスを返す。"""
        for i, b in enumerate(blocks):
            text = b.text.strip()
            # Chapter marker → body starts here
            if _CHAPTER_RE.match(text) and b.block_type in (
                "section_heading",
                "subsection_heading",
            ):
                return i
            # Abstract または Introduction が見つかれば body 開始
            if _ABSTRACT_RE.match(text) or _INTRO_RE.match(text):
                return i
            # 明示的な front_matter パターン以外で body_paragraph が来たら
            if (
                b.block_type == "body_paragraph"
                and not _FRONT_MATTER_RE.match(text)
                and i > 0
            ):
                # 直前が heading なら heading から body とみなす
                if blocks[i - 1].block_type in ("section_heading", "subsection_heading"):
                    return i - 1
                return i
        return 0

    def _find_back_matter_start(self, blocks: list[TypedBlock]) -> int:
        """back matter が始まるインデックスを返す（その index 以降は back matter）。"""
        last_back_start = len(blocks)
        in_back = False
        for i, b in enumerate(blocks):
            text = b.text.strip()
            if _BACK_MATTER_HEADER_RE.match(text) and b.block_type in (
                "section_heading",
                "subsection_heading",
            ):
                if not in_back:
                    last_back_start = i
                in_back = True
            elif in_back and b.block_type not in (
                "reference_entry",
                "footnote",
                "figure_caption",
                "table_caption",
                "unknown",
            ):
                # body_paragraph が再び現れたら appendix などの可能性
                if b.block_type == "body_paragraph" and not _BACK_MATTER_HEADER_RE.match(text):
                    in_back = False
        return last_back_start

    # ------------------------------------------------------------------
    # Unit span detection
    # ------------------------------------------------------------------

    def _detect_unit_spans(
        self,
        body_blocks: list[TypedBlock],
        offset: int,
        cartridge: CartridgeContext | None,
    ) -> list[_Span]:
        """body_blocks 内で unit span を検出し、元 blocks インデックス (offset 付き) で返す。"""
        if not body_blocks:
            return []

        # Try chapter detection first
        chapter_starts = self._find_chapter_starts(body_blocks)
        if len(chapter_starts) > 1:
            return self._build_chapter_spans(body_blocks, chapter_starts, offset)

        # Try multiple-article detection (title + abstract pattern)
        article_starts = self._find_article_starts(body_blocks)
        if len(article_starts) > 1:
            return self._build_article_spans(body_blocks, article_starts, offset)

        # Single unit fallback
        return self._build_single_span(body_blocks, offset)

    def _find_chapter_starts(self, blocks: list[TypedBlock]) -> list[int]:
        return [
            i
            for i, b in enumerate(blocks)
            if b.block_type in ("section_heading", "subsection_heading")
            and _CHAPTER_RE.match(b.text.strip())
        ]

    def _find_article_starts(self, blocks: list[TypedBlock]) -> list[int]:
        """abstract / intro の直前に title-like block がある位置を article start と判定する。"""
        starts: list[int] = []
        for i, b in enumerate(blocks):
            text = b.text.strip()
            # Abstract または intro が来た直前の title-like block を探す
            if _ABSTRACT_RE.match(text) or _INTRO_RE.match(text):
                title_idx = self._find_preceding_title(blocks, i)
                if title_idx is not None:
                    starts.append(title_idx)
        return starts

    def _find_preceding_title(
        self, blocks: list[TypedBlock], abstract_idx: int
    ) -> int | None:
        """abstract_idx の直前（最大5ブロック遡る）で title-like なブロックを探す。"""
        for j in range(max(0, abstract_idx - 5), abstract_idx):
            b = blocks[j]
            if self._looks_like_title(b):
                return j
        return None

    def _looks_like_title(self, b: TypedBlock) -> bool:
        text = b.text.strip()
        if len(text) < 5 or len(text) > 300:
            return False
        if b.block_type not in ("section_heading", "body_paragraph", "unknown"):
            return False
        # Short + doesn't look like a section number
        if re.match(r"^\d+\.?\s", text):
            return False
        # Large font or bold or centered
        raw = b.raw or {}
        is_large = (b.confidence >= 0.8 and b.block_type == "section_heading")
        is_bold_centered = raw.get("is_bold") or b.is_centered if hasattr(b, "is_centered") else False
        if is_large or is_bold_centered:
            return True
        # section_heading without a section number is a strong title signal
        if b.block_type == "section_heading" and not re.match(r"^\d", text):
            return True
        return False

    # ------------------------------------------------------------------
    # Span builders
    # ------------------------------------------------------------------

    def _build_chapter_spans(
        self,
        blocks: list[TypedBlock],
        chapter_starts: list[int],
        offset: int,
    ) -> list[_Span]:
        spans: list[_Span] = []
        for k, start in enumerate(chapter_starts):
            end = chapter_starts[k + 1] if k + 1 < len(chapter_starts) else len(blocks)
            span = _Span(
                start_idx=start + offset,
                end_idx=end + offset,
                unit_type="chapter",
                title_idx=start + offset,
                confidence=0.82,
            )
            if start == end - 1:
                span.review_reasons.append("empty_chapter_body")
            spans.append(span)
        return spans

    def _build_article_spans(
        self,
        blocks: list[TypedBlock],
        article_starts: list[int],
        offset: int,
    ) -> list[_Span]:
        spans: list[_Span] = []
        for k, start in enumerate(article_starts):
            end = article_starts[k + 1] if k + 1 < len(article_starts) else len(blocks)
            span = _Span(
                start_idx=start + offset,
                end_idx=end + offset,
                unit_type="article",
                title_idx=start + offset,
                confidence=0.75,
            )
            # Detect authors between title and abstract
            span.author_indices = self._find_author_blocks_in_range(
                blocks, start, min(start + 5, end)
            )
            spans.append(span)
        return spans

    def _build_single_span(
        self, blocks: list[TypedBlock], offset: int
    ) -> list[_Span]:
        """全 body_blocks が 1 つの unit（最も一般的な単一論文 PDF）。"""
        if not blocks:
            return []

        title_idx: int | None = None
        author_indices: list[int] = []
        review_reasons: list[str] = []

        # タイトル探索: 最初の section_heading か body_paragraph（前 10 ブロック以内）
        for i, b in enumerate(blocks[:10]):
            if self._looks_like_title(b):
                title_idx = i
                break

        if title_idx is None:
            review_reasons.append("title_not_detected")

        # 著者探索: タイトルの直後〜5ブロック以内
        if title_idx is not None:
            author_indices = self._find_author_blocks_in_range(
                blocks, title_idx + 1, min(title_idx + 6, len(blocks))
            )

        confidence = 0.72 if title_idx is not None else 0.40

        span = _Span(
            start_idx=offset,
            end_idx=offset + len(blocks),
            unit_type="article",
            title_idx=(title_idx + offset) if title_idx is not None else None,
            author_indices=[a + offset for a in author_indices],
            confidence=confidence,
            review_reasons=review_reasons,
        )
        return [span]

    # ------------------------------------------------------------------
    # Author extraction
    # ------------------------------------------------------------------

    def _find_author_blocks_in_range(
        self, blocks: list[TypedBlock], start: int, end: int
    ) -> list[int]:
        result = []
        for i in range(start, min(end, len(blocks))):
            b = blocks[i]
            if b.block_type not in ("body_paragraph", "unknown"):
                continue
            text = b.text.strip()
            if _ABSTRACT_RE.match(text) or _INTRO_RE.match(text):
                break
            if _AUTHOR_HINT_RE.match(text) and len(text) < 200:
                result.append(i)
        return result

    @staticmethod
    def _extract_authors(
        blocks: list[TypedBlock], author_indices: list[int]
    ) -> list[str]:
        authors: list[str] = []
        for idx in author_indices:
            if 0 <= idx < len(blocks):
                text = blocks[idx].text.strip()
                # 複数著者がカンマ区切りの場合は分割
                for name in re.split(r"\s*(?:,|and|&)\s*", text):
                    name = name.strip()
                    if name:
                        authors.append(name)
        return authors

    # ------------------------------------------------------------------
    # Overall confidence / review reasons
    # ------------------------------------------------------------------

    def _compute_overall_confidence(
        self,
        units: list[DocumentUnit],
        spans: list[_Span],
        all_blocks: list[TypedBlock],
        front_end_idx: int,
        back_start_idx: int,
    ) -> tuple[float, list[str]]:
        review_reasons: list[str] = []

        if not units:
            return 0.0, ["title_not_detected"]

        if len(units) > 1:
            review_reasons.append("multiple_candidate_units_detected")

        # Confidence は span の平均
        avg_conf = sum(u.confidence for u in units) / len(units)

        # 各ユニットの review_reasons を集約
        for u in units:
            for r in u.review_reasons:
                if r not in review_reasons:
                    review_reasons.append(r)

        if avg_conf < _CONFIDENCE_THRESHOLD:
            review_reasons.append("confidence_below_threshold")

        # front matter 境界があいまいかチェック
        if front_end_idx == 0 and any(
            b.block_type == "body_paragraph" for b in all_blocks[:3]
        ):
            if not any(
                _ABSTRACT_RE.match(b.text.strip()) or _INTRO_RE.match(b.text.strip())
                for b in all_blocks[:5]
            ):
                review_reasons.append("front_matter_boundary_ambiguous")

        return avg_conf, review_reasons
