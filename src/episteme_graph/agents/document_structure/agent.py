"""DocumentStructureAgent: PDF からドキュメント構造を復元するエージェント。

design: structure-first / parser-driven / cartridge-aware (not cartridge-dependent)
- cartridge がなくても単独で動作する
- parser_backend="pymupdf": PyMuPDF のみ（MVPデフォルト）
- parser_backend="grobid_hybrid": GROBID TEI XML 優先 + PyMuPDF でページ/bbox を補完
"""
from __future__ import annotations

import logging
import os

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
        use_grobid = (
            tei_xml is not None
            or self._parser_backend == "grobid_hybrid"
        )

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
            pymupdf_blocks = self._extractor.extract_blocks(pdf_path, max_pages=max_pages)
            total_pages = max((b.page for b in pymupdf_blocks), default=0) if pymupdf_blocks else 0
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

        # grobid_hybrid provenance（既に grobid_tei がセットされているが統一表記を追加）
        for b in typed_blocks:
            if b.raw.get("parser_source") == "grobid_tei" and pymupdf_blocks:
                b.raw["parser_source"] = "grobid_hybrid"

        return typed_blocks, sections, metadata

    @staticmethod
    def _make_document_id(pdf_path: str) -> str:
        basename = os.path.splitext(os.path.basename(pdf_path))[0]
        safe = "".join(c if c.isalnum() or c == "_" else "_" for c in basename)
        return f"doc_{safe}"
