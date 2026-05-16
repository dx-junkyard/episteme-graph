"""Crop equation candidate images from source PDFs for vision reconstruction."""
from __future__ import annotations

import base64
import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class EquationImage:
    mime_type: str
    data_base64: str
    page: int
    bbox: list[float]


class EquationImageExtractor:
    """Render a padded PDF bbox as PNG.

    Coordinates are PyMuPDF page coordinates, which match DocumentStructureAgent
    block bboxes because they are produced by PyMuPDF.
    """

    def __init__(self, *, dpi: int = 240, padding: float = 10.0) -> None:
        self._dpi = dpi
        self._padding = padding

    def crop_candidate(self, pdf_path: str | None, source_location: dict | None) -> EquationImage | None:
        if not pdf_path or not source_location:
            return None
        if not os.path.exists(pdf_path):
            logger.warning("Equation vision crop skipped; PDF not found: %s", pdf_path)
            return None
        bbox = source_location.get("bbox") or []
        page_no = int(source_location.get("page") or 0)
        if page_no <= 0 or len(bbox) != 4:
            return None
        try:
            import fitz  # PyMuPDF
        except Exception:
            logger.warning("Equation vision crop skipped; PyMuPDF is unavailable", exc_info=True)
            return None

        try:
            with fitz.open(pdf_path) as doc:
                if page_no > len(doc):
                    return None
                page = doc[page_no - 1]
                rect = fitz.Rect(*[float(v) for v in bbox])
                rect = rect + (-self._padding, -self._padding, self._padding, self._padding)
                rect = rect & page.rect
                if rect.is_empty or rect.width <= 1 or rect.height <= 1:
                    return None
                zoom = self._dpi / 72.0
                pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=rect, alpha=False)
                return EquationImage(
                    mime_type="image/png",
                    data_base64=base64.b64encode(pix.tobytes("png")).decode("ascii"),
                    page=page_no,
                    bbox=[float(v) for v in bbox],
                )
        except Exception:
            logger.warning("Equation vision crop failed for page=%s bbox=%s", page_no, bbox, exc_info=True)
            return None
