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

from .author_extraction import (
    AuthorExtractionResult,
    extract_authors_from_front_matter,
    extract_authors_from_tex,
    resolve_author_extraction,
)
from .cartridge_loader import CartridgeLoader
from .classifier import BlockClassifier
from .hierarchy import SectionHierarchyBuilder
from .parser import PDFBlockExtractor
from .schema import (
    CartridgeContext,
    DocumentMetadata,
    DocumentStructureResult,
    TypedBlock,
)
from .validator import StructureValidator

logger = logging.getLogger(__name__)

# GROBID が <figure>/<figDesc> を落とした PDF だけを PyMuPDF から補うための保守的な
# caption パターン。本文の ``Figure 2 shows ...`` は対象にせず、図番号直後に明示的な
# caption 区切り（colon / dash）がある独立ブロックだけを採用する。
_PDF_FIGURE_CAPTION_RE = re.compile(
    r"^(?:Figure|Fig\.?|図)\s*\d+(?:\s*\.\s*\d+)*(?:[A-Za-z])?\s*(?::|[-–—])\s*\S",
    re.IGNORECASE,
)

# 単調alignmentの1 blockあたり先読み上限。全文末尾まで検索すると、局所blockが
# 見つからない1回の低い類似一致で数十〜百ページ先へ飛び、その後を全て失う。
_PDF_ALIGNMENT_LOOKAHEAD = 240


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
        tex_source: str | None = None,
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
        tex_source:
            LaTeX ソース文字列（任意）。``\\author{...}`` から正規著者を抽出し、
            構造化メタデータとして最優先で採用する（issue #372）。
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

        # Author provenance resolution (issue #372): prefer explicit structured
        # metadata. Priority: TeX \author{} > parser front-matter (GROBID TEI /
        # PDF byline). Authors are only ever read from front-matter sources.
        self._resolve_author_provenance(metadata, tex_source)

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
    # Author provenance (issue #372)
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_author_provenance(
        metadata: DocumentMetadata, tex_source: str | None
    ) -> None:
        """Resolve authors from structured front-matter sources by priority.

        The backend already populated ``metadata.authors`` /
        ``metadata.author_extraction`` from GROBID TEI or the PDF byline. If a
        TeX source is supplied, its ``\\author{}`` names take precedence. The
        chosen list and provenance are written back onto ``metadata``.
        """
        existing = AuthorExtractionResult(
            authors=list(metadata.authors or []),
            source=str((metadata.author_extraction or {}).get("source", "none")),
            confidence=float((metadata.author_extraction or {}).get("confidence", 0.0) or 0.0),
            needs_review=bool((metadata.author_extraction or {}).get("needs_review", False)),
            review_reasons=list((metadata.author_extraction or {}).get("review_reasons", []) or []),
        )

        candidates: list[AuthorExtractionResult] = []
        if tex_source:
            tex_result = extract_authors_from_tex(tex_source)
            if tex_result.authors:
                candidates.append(tex_result)
        candidates.append(existing)

        resolved = resolve_author_extraction(candidates)
        metadata.authors = list(resolved.authors)
        metadata.author_extraction = resolved.to_metadata()

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

        total_pages = len(page_heights)
        pages_processed = (
            min(total_pages, max_pages) if max_pages is not None else total_pages
        )
        metadata = DocumentMetadata(
            pages=total_pages,
            parser_pages_processed=pages_processed,
            parser_reached_eof=pages_processed >= total_pages if total_pages else None,
        )

        # Authors from the PDF front-matter byline only (issue #372): never from
        # the body or references, so a body term cannot inject a citation author.
        front_matter = extract_authors_from_front_matter(typed_blocks)
        metadata.authors = list(front_matter.authors)
        metadata.author_extraction = front_matter.to_metadata()

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

        # PyMuPDF で原文ページ数・ページ情報・bbox を補完する
        try:
            page_heights = self._extractor.get_page_heights(pdf_path)
            total_pages = len(page_heights)
        except Exception:
            logger.warning(
                "Could not read source page count for %s",
                pdf_path,
                exc_info=True,
            )
            page_heights = {}
            total_pages = 0
        pages_processed = (
            min(total_pages, max_pages) if max_pages is not None else total_pages
        )
        try:
            pymupdf_blocks = self._extractor.extract_blocks(
                pdf_path, max_pages=max_pages
            )
        except Exception:
            logger.warning(
                "PyMuPDF supplemental extraction failed for %s; page numbers will default to 1",
                pdf_path,
                exc_info=True,
            )
            pymupdf_blocks = []

        typed_blocks = grobid_result.blocks
        sections = grobid_result.sections
        metadata = grobid_result.metadata
        metadata.pages = total_pages or metadata.pages
        metadata.parser_pages_processed = pages_processed or None
        metadata.parser_reached_eof = (
            pages_processed >= total_pages if total_pages else None
        )

        if pymupdf_blocks:
            self._align_grobid_blocks_to_pdf_blocks(typed_blocks, pymupdf_blocks)
            self._supplement_grobid_figure_captions(typed_blocks, pymupdf_blocks)
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
        for raw in pymupdf_blocks:
            text = getattr(raw, "text", "") or ""
            norm = DocumentStructureAgent._normalize_match_text(text)
            if not norm:
                continue
            # rec_idx は filtered ``pdf_records`` 自身の添字にする。元raw_blocksの添字を
            # 保存すると、空blockを除外した分だけ ``pdf_records[start_idx:]`` と座標が
            # ずれ、直前の正解候補を走査範囲から落としていた。
            pdf_records.append((len(pdf_records), raw, norm))

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
        # ページ番号・式番号・箇条書き番号のような短い断片は文書内で反復し、誤った
        # 後方ページへ単調カーソルを飛ばして以降の全blockを未整列にする。短いblock
        # 自体のpage補完より文書全体のanchor維持を優先する。
        if len(target) < 12:
            return None
        best: tuple[int, object, float] | None = None
        # Keep the search monotonic but allow a small look-behind for headers or
        # short blocks that PyMuPDF split differently.
        scan_start = max(0, start_idx - 3)
        scan_end = scan_start + _PDF_ALIGNMENT_LOOKAHEAD
        for rec_idx, raw, candidate in pdf_records[scan_start:scan_end]:
            if rec_idx < scan_start:
                continue
            score = 0.0
            if target in candidate:
                score = min(1.0, len(target) / max(1, len(candidate)))
                # GROBID の短い段落が大きなPDF text block内に完全包含されるケース。
                if len(target) >= 20 or score >= 0.35:
                    score = max(score, 0.92)
            elif candidate in target:
                score = min(1.0, len(candidate) / max(1, len(target)))
                # PDF側の短い軸ラベルや記号が長いGROBID段落に偶然含まれてもanchorに
                # しない。PDF断片側が十分長い場合だけ従来どおり採用する。
                if len(candidate) >= 80:
                    score = max(score, 0.92)
            if score == 0.0:
                score = SequenceMatcher(None, target[:900], candidate[:900]).ratio()
            if best is None or score > best[2]:
                best = (rec_idx, raw, score)
            if score >= 0.98:
                break
        # 0.4台の弱い類似は数式・定型句で容易に発生し、単調カーソルを誤って進める。
        # 完全包含は上で0.92へ昇格するため、ここでは非包含のfuzzy一致に十分な差を要求する。
        if best and best[2] >= 0.60:
            return best
        return None

    @staticmethod
    def _supplement_grobid_figure_captions(typed_blocks, pymupdf_blocks) -> None:
        """GROBID TEI が欠落させた明示的な図 caption を PDF text layer から補う。

        hybrid backend はこれまで PyMuPDF を既存 TEI block の page/bbox 補完にしか使わず、
        TEI に存在しない caption は捨てていた。その結果 ``figure_table_semantics`` が0件、
        ``document_figures`` が caption 無しの残余 embedded image だけになる。本文参照を
        caption と誤認しないよう、``_PDF_FIGURE_CAPTION_RE`` に一致する独立 block のみ追加する。
        """
        if not typed_blocks or not pymupdf_blocks:
            return

        existing_texts = {
            DocumentStructureAgent._normalize_match_text(getattr(block, "text", "") or "")
            for block in typed_blocks
            if getattr(block, "block_type", "") == "figure_caption"
        }
        aligned = [
            block
            for block in typed_blocks
            if getattr(block, "section_id", None)
            and isinstance(getattr(block, "raw", None), dict)
            and isinstance(block.raw.get("pdf_alignment"), dict)
        ]

        for raw in pymupdf_blocks:
            text = str(getattr(raw, "text", "") or "").strip()
            if not _PDF_FIGURE_CAPTION_RE.match(text):
                continue
            normalized = DocumentStructureAgent._normalize_match_text(text)
            if not normalized or normalized in existing_texts:
                continue

            page = int(getattr(raw, "page", 1) or 1)
            raw_order = int(getattr(raw, "order", 0) or 0)
            same_page = [
                block for block in aligned
                if int(getattr(block, "page", 0) or 0) == page
            ]
            nearest = min(
                same_page,
                key=lambda block: abs(
                    int(block.raw.get("pdf_alignment", {}).get("pdf_order") or 0) - raw_order
                ),
                default=None,
            )
            section_id = getattr(nearest, "section_id", None) if nearest else None

            typed_blocks.append(TypedBlock(
                block_id=f"blk_pdf_fig_{page}_{raw_order}",
                page=page,
                order=raw_order,
                text=text,
                block_type="figure_caption",
                bbox=getattr(raw, "bbox", None),
                confidence=0.95,
                section_id=section_id,
                raw={
                    "parser_source": "grobid_hybrid_pdf_supplement",
                    "pdf_alignment": {
                        "page": page,
                        "pdf_order": raw_order,
                        "score": 1.0,
                    },
                },
            ))
            existing_texts.add(normalized)

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
