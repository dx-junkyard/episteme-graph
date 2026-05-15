"""DocumentStructureAgent: PDF からドキュメント構造を復元するエージェント。

design: structure-first / parser-driven / cartridge-aware (not cartridge-dependent)
- cartridge がなくても単独で動作する
- parser_backend="pymupdf": PyMuPDF のみ（MVPデフォルト）
- parser_backend="grobid_hybrid": GROBID TEI XML 優先 + PyMuPDF でページ/bbox を補完
"""

from __future__ import annotations

import logging
import os
import re
from difflib import SequenceMatcher

from .cartridge_loader import CartridgeLoader
from .classifier import BlockClassifier
from .hierarchy import SectionHierarchyBuilder
from .parser import PDFBlockExtractor
from .schema import CartridgeContext, DocumentMetadata, DocumentStructureResult
from .validator import StructureValidator

logger = logging.getLogger(__name__)


class DocumentStructureAgent:
    """PDF を受け取り DocumentStructureResult を返すエージェント。

    Parameters
    ----------
    cartridge_base_dir:
        カートリッジの検索ベースディレクトリ。None の場合は CartridgeLoader のデフォルト。
    parser_backend:
        PDF パーサバックエンド。
        - "pymupdf": PyMuPDF のみ（デフォルト）
        - "grobid_hybrid": GROBID TEI XML 優先、PyMuPDF で bbox / page 補完
    """

    def __init__(
        self,
        cartridge_base_dir: str | None = None,
        parser_backend: str = "pymupdf",
    ) -> None:
        self._extractor = PDFBlockExtractor(parser_backend="pymupdf")
        self._classifier = BlockClassifier()
        self._hierarchy_builder = SectionHierarchyBuilder()
        self._validator = StructureValidator()
        self._cartridge_loader = CartridgeLoader(cartridge_base_dir)
        self._parser_backend = parser_backend

    def run(
        self,
        pdf_path: str,
        cartridge_id: str | None = None,
        config: dict | None = None,
        tei_xml: str | None = None,
    ) -> DocumentStructureResult:
        """PDF を解析して DocumentStructureResult を返す。

        Parameters
        ----------
        pdf_path:
            解析対象の PDF ファイルパス。
        cartridge_id:
            使用するカートリッジ ID。None の場合はカートリッジなしで動作。
        config:
            オプション設定。 "max_pages": int でページ数上限を指定できる。
        tei_xml:
            GROBID が生成した TEI XML 文字列。
            指定された場合、parser_backend の設定に関わらず grobid_hybrid 処理を試みる。
            None の場合は parser_backend の設定に従う。
        """
        cfg = config or {}
        max_pages: int | None = cfg.get("max_pages")

        # Step 1: Load cartridge (optional)
        cartridge: CartridgeContext | None = None
        if cartridge_id:
            try:
                cartridge = self._cartridge_loader.load(cartridge_id)
            except FileNotFoundError:
                pass  # cartridge-dependent ではないので続行

        document_id = self._make_document_id(pdf_path)

        # Step 2: バックエンド選択
        use_grobid = tei_xml is not None or self._parser_backend == "grobid_hybrid"

        if use_grobid and tei_xml:
            typed_blocks, sections, metadata = self._extract_grobid_hybrid(
                pdf_path=pdf_path,
                tei_xml=tei_xml,
                max_pages=max_pages,
                document_id=document_id,
            )
        else:
            typed_blocks, sections, metadata = self._extract_pymupdf(
                pdf_path=pdf_path,
                max_pages=max_pages,
                cartridge=cartridge,
                document_id=document_id,
            )

        # Step 3: Build result
        result = DocumentStructureResult(
            document_id=document_id,
            source_file=os.path.abspath(pdf_path),
            cartridge_id=cartridge_id,
            metadata=metadata,
            blocks=typed_blocks,
            sections=sections,
        )

        # Step 4: Validate
        result.validation_issues = self._validator.validate(result, cartridge)

        return result

    # ------------------------------------------------------------------
    # PyMuPDF backend (既存ロジック)
    # ------------------------------------------------------------------

    def _extract_pymupdf(
        self,
        pdf_path: str,
        max_pages: int | None,
        cartridge: CartridgeContext | None,
        document_id: str,
    ):
        raw_blocks = self._extractor.extract_blocks(pdf_path, max_pages=max_pages)
        page_heights = self._extractor.get_page_heights(pdf_path)

        typed_blocks = self._classifier.classify(
            raw_blocks,
            cartridge=cartridge,
            page_heights=page_heights,
        )

        sections = self._hierarchy_builder.build(typed_blocks, document_id)

        total_pages = max((b.page for b in typed_blocks), default=0)
        metadata = DocumentMetadata(pages=total_pages)

        # pymupdf provenance
        for b in typed_blocks:
            b.raw.setdefault("parser_source", "pymupdf")

        return typed_blocks, sections, metadata

    # ------------------------------------------------------------------
    # GROBID hybrid backend
    # ------------------------------------------------------------------

    def _extract_grobid_hybrid(
        self,
        pdf_path: str,
        tei_xml: str,
        max_pages: int | None,
        document_id: str,
    ):
        from .grobid_parser import GROBIDTEIParser

        parser = GROBIDTEIParser()
        try:
            grobid_result = parser.parse(tei_xml)
        except Exception:
            logger.warning(
                "GROBIDTEIParser failed, falling back to PyMuPDF for document %s",
                document_id,
                exc_info=True,
            )
            return self._extract_pymupdf(
                pdf_path=pdf_path,
                max_pages=max_pages,
                cartridge=None,
                document_id=document_id,
            )

        if not grobid_result.blocks:
            logger.info(
                "GROBID returned no blocks for document %s, falling back to PyMuPDF",
                document_id,
            )
            return self._extract_pymupdf(
                pdf_path=pdf_path,
                max_pages=max_pages,
                cartridge=None,
                document_id=document_id,
            )

        # PyMuPDF でページ情報・bbox を補完する
        try:
            pymupdf_blocks = self._extractor.extract_blocks(
                pdf_path, max_pages=max_pages
            )
            total_pages = (
                max((b.page for b in pymupdf_blocks), default=0)
                if pymupdf_blocks
                else 0
            )
        except Exception:
            logger.warning(
                "PyMuPDF supplemental extraction failed for %s; page numbers will default to 1",
                pdf_path,
                exc_info=True,
            )
            pymupdf_blocks = []
            total_pages = 0

        typed_blocks = grobid_result.blocks
        sections = grobid_result.sections
        metadata = grobid_result.metadata
        metadata.pages = total_pages or metadata.pages

        if pymupdf_blocks:
            self._align_grobid_blocks_to_pdf_blocks(typed_blocks, pymupdf_blocks)
            self._refresh_section_pages_from_blocks(
                sections, typed_blocks, total_pages or metadata.pages or 1
            )

        # grobid_hybrid provenance（既に grobid_tei がセットされているが統一表記を追加）
        for b in typed_blocks:
            if b.raw.get("parser_source") == "grobid_tei" and pymupdf_blocks:
                b.raw["parser_source"] = "grobid_hybrid"

        return typed_blocks, sections, metadata

    @staticmethod
    def _align_grobid_blocks_to_pdf_blocks(typed_blocks, pymupdf_blocks) -> None:
        """Attach best-effort PDF page/bbox data to GROBID TEI blocks.

        GROBID's TEI output preserves logical order but normally lacks physical page
        numbers.  Without this pass all chunks point at page 1, so the studio's
        PDF pane and the displayed source text drift apart.  Matching is monotonic
        and fuzzy: it first looks for normalized containment, then falls back to a
        similarity score against nearby PyMuPDF text blocks.
        """
        if not typed_blocks or not pymupdf_blocks:
            return

        pdf_records = []
        for idx, raw in enumerate(pymupdf_blocks):
            text = getattr(raw, "text", "") or ""
            norm = DocumentStructureAgent._normalize_match_text(text)
            if not norm:
                continue
            pdf_records.append((idx, raw, norm))

        last_idx = 0
        for block in typed_blocks:
            target = DocumentStructureAgent._normalize_match_text(
                getattr(block, "text", "") or ""
            )
            if not target:
                continue
            best = DocumentStructureAgent._find_best_pdf_match(
                target, pdf_records, last_idx
            )
            if best is None:
                continue
            rec_idx, raw, score = best
            last_idx = max(last_idx, rec_idx)
            block.page = getattr(raw, "page", block.page)
            block.bbox = getattr(raw, "bbox", block.bbox)
            block.raw.setdefault("pdf_alignment", {})
            block.raw["pdf_alignment"].update(
                {
                    "page": block.page,
                    "pdf_order": getattr(raw, "order", None),
                    "score": round(float(score), 3),
                }
            )

    @staticmethod
    def _find_best_pdf_match(
        target: str,
        pdf_records: list[tuple[int, object, str]],
        start_idx: int,
    ):
        best: tuple[int, object, float] | None = None
        # Keep the search monotonic but allow a small look-behind for headers or
        # short blocks that PyMuPDF split differently.
        scan_start = max(0, start_idx - 3)
        for rec_idx, raw, candidate in pdf_records[scan_start:]:
            if rec_idx < scan_start:
                continue
            score = 0.0
            short, long = (
                (target, candidate)
                if len(target) <= len(candidate)
                else (candidate, target)
            )
            if short and short in long:
                score = min(1.0, len(short) / max(1, len(long)))
                if len(short) >= 80 or score >= 0.35:
                    score = max(score, 0.92)
            if score == 0.0:
                score = SequenceMatcher(None, target[:900], candidate[:900]).ratio()
            if best is None or score > best[2]:
                best = (rec_idx, raw, score)
            if score >= 0.98:
                break
        if best and best[2] >= 0.42:
            return best
        return None

    @staticmethod
    def _refresh_section_pages_from_blocks(
        sections, typed_blocks, total_pages: int
    ) -> None:
        by_section: dict[str, list[int]] = {}
        for block in typed_blocks:
            sid = getattr(block, "section_id", None)
            page = getattr(block, "page", None)
            if sid and page:
                by_section.setdefault(sid, []).append(int(page))
        for section in sections:
            pages = by_section.get(getattr(section, "section_id", ""))
            if pages:
                section.page_start = min(pages)
                section.page_end = max(pages)
            elif not getattr(section, "page_start", None):
                section.page_start = 1
                section.page_end = total_pages or 1

    @staticmethod
    def _normalize_match_text(text: str) -> str:
        text = (text or "").lower()
        text = re.sub(r"\s+", " ", text)
        text = re.sub(r"[^a-z0-9α-ωΑ-Ω一-龯ぁ-んァ-ン]+", "", text)
        return text

    @staticmethod
    def _make_document_id(pdf_path: str) -> str:
        basename = os.path.splitext(os.path.basename(pdf_path))[0]
        safe = "".join(c if c.isalnum() or c == "_" else "_" for c in basename)
        return f"doc_{safe}"
