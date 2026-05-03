"""DocumentStructureAgent: PDF からドキュメント構造を復元するエージェント。

design: structure-first / parser-driven / cartridge-aware (not cartridge-dependent)
- LLM は使用しない（MVP）
- cartridge がなくても単独で動作する
"""
from __future__ import annotations

import os

from .cartridge_loader import CartridgeLoader
from .classifier import BlockClassifier
from .hierarchy import SectionHierarchyBuilder
from .parser import PDFBlockExtractor
from .schema import CartridgeContext, DocumentMetadata, DocumentStructureResult
from .validator import StructureValidator


class DocumentStructureAgent:
    """PDF を受け取り DocumentStructureResult を返すエージェント。

    Parameters
    ----------
    cartridge_base_dir:
        カートリッジの検索ベースディレクトリ。None の場合は CartridgeLoader のデフォルト。
    parser_backend:
        PDF パーサバックエンド。"pymupdf" のみ MVP でサポート。
    """

    def __init__(
        self,
        cartridge_base_dir: str | None = None,
        parser_backend: str = "pymupdf",
    ) -> None:
        self._extractor = PDFBlockExtractor(parser_backend=parser_backend)
        self._classifier = BlockClassifier()
        self._hierarchy_builder = SectionHierarchyBuilder()
        self._validator = StructureValidator()
        self._cartridge_loader = CartridgeLoader(cartridge_base_dir)

    def run(
        self,
        pdf_path: str,
        cartridge_id: str | None = None,
        config: dict | None = None,
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

        # Step 2: Extract raw blocks
        raw_blocks = self._extractor.extract_blocks(pdf_path, max_pages=max_pages)
        page_heights = self._extractor.get_page_heights(pdf_path)

        # Step 3: Classify blocks
        typed_blocks = self._classifier.classify(
            raw_blocks,
            cartridge=cartridge,
            page_heights=page_heights,
        )

        # Step 4: Build section hierarchy (assigns block.section_id in-place)
        document_id = self._make_document_id(pdf_path)
        sections = self._hierarchy_builder.build(typed_blocks, document_id)

        # Step 5: Build metadata
        total_pages = max((b.page for b in typed_blocks), default=0)
        metadata = DocumentMetadata(pages=total_pages)

        # Step 6: Build result
        result = DocumentStructureResult(
            document_id=document_id,
            source_file=os.path.abspath(pdf_path),
            cartridge_id=cartridge_id,
            metadata=metadata,
            blocks=typed_blocks,
            sections=sections,
        )

        # Step 7: Validate
        result.validation_issues = self._validator.validate(result, cartridge)

        return result

    @staticmethod
    def _make_document_id(pdf_path: str) -> str:
        basename = os.path.splitext(os.path.basename(pdf_path))[0]
        safe = "".join(c if c.isalnum() or c == "_" else "_" for c in basename)
        return f"doc_{safe}"
