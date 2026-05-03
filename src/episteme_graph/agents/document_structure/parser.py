"""PDFBlockExtractor: PyMuPDF を使って PDF からテキストブロックを抽出する。

pdfplumber が利用可能な環境ではそちらへの切り替えも可能にする設計にしているが、
MVP では PyMuPDF (fitz) をデフォルトとする。
"""
from __future__ import annotations

import os
from typing import Optional

from .schema import RawBlock

try:
    import fitz  # PyMuPDF
    _FITZ_AVAILABLE = True
except ImportError:
    _FITZ_AVAILABLE = False


class PDFBlockExtractor:
    """PDF ファイルから RawBlock のリストを抽出する。

    parser_backend: "pymupdf" (デフォルト) のみ MVP でサポート。
    """

    def __init__(self, parser_backend: str = "pymupdf") -> None:
        self._backend = parser_backend

    def extract_blocks(
        self,
        pdf_path: str,
        max_pages: int | None = None,
    ) -> list[RawBlock]:
        if not os.path.exists(pdf_path):
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        if self._backend == "pymupdf":
            return self._extract_with_pymupdf(pdf_path, max_pages)

        raise ValueError(f"Unsupported parser_backend: {self._backend}")

    def get_page_heights(self, pdf_path: str) -> dict[int, float]:
        """ページ番号 → ページ高さ のマップを返す（脚注検出に使用）。"""
        if not _FITZ_AVAILABLE:
            return {}
        doc = fitz.open(pdf_path)
        result = {i + 1: doc[i].rect.height for i in range(len(doc))}
        doc.close()
        return result

    # ------------------------------------------------------------------
    # PyMuPDF backend
    # ------------------------------------------------------------------

    def _extract_with_pymupdf(
        self, pdf_path: str, max_pages: int | None
    ) -> list[RawBlock]:
        if not _FITZ_AVAILABLE:
            raise RuntimeError("PyMuPDF (fitz) is not installed.")

        doc = fitz.open(pdf_path)
        total_pages = len(doc)
        limit = min(total_pages, max_pages) if max_pages else total_pages

        blocks: list[RawBlock] = []
        order = 0

        for page_idx in range(limit):
            page = doc[page_idx]
            page_width = page.rect.width
            page_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)

            for raw_block in page_dict.get("blocks", []):
                if raw_block.get("type") != 0:  # skip image blocks
                    continue

                text, font_size, font_name, is_bold = self._extract_text_props(
                    raw_block
                )
                text = text.strip()
                if not text:
                    continue

                bbox = tuple(raw_block.get("bbox", (0.0, 0.0, 0.0, 0.0)))
                is_centered = self._is_centered(bbox, page_width)

                blocks.append(
                    RawBlock(
                        page=page_idx + 1,
                        order=order,
                        text=text,
                        bbox=bbox,
                        font_size=font_size,
                        font_name=font_name,
                        is_bold=is_bold,
                        is_centered=is_centered,
                    )
                )
                order += 1

        doc.close()
        return blocks

    @staticmethod
    def _extract_text_props(
        raw_block: dict,
    ) -> tuple[str, float | None, str | None, bool]:
        text_parts: list[str] = []
        font_sizes: list[float] = []
        font_names: list[str] = []
        is_bold = False

        for line in raw_block.get("lines", []):
            for span in line.get("spans", []):
                span_text = span.get("text", "")
                text_parts.append(span_text)

                size = span.get("size")
                if size:
                    font_sizes.append(float(size))

                font = span.get("font", "")
                if font:
                    font_names.append(font)

                # PyMuPDF flags: bit 4 (value 16) = bold
                flags = span.get("flags", 0)
                if flags & 16:
                    is_bold = True
                elif "Bold" in font or "bold" in font:
                    is_bold = True

            text_parts.append("\n")

        text = "".join(text_parts)
        avg_size = sum(font_sizes) / len(font_sizes) if font_sizes else None
        dominant_font = (
            max(set(font_names), key=font_names.count) if font_names else None
        )
        return text, avg_size, dominant_font, is_bold

    @staticmethod
    def _is_centered(bbox: tuple, page_width: float, tolerance: float = 0.15) -> bool:
        if page_width <= 0:
            return False
        block_center_x = (bbox[0] + bbox[2]) / 2
        page_center_x = page_width / 2
        return abs(block_center_x - page_center_x) < page_width * tolerance
