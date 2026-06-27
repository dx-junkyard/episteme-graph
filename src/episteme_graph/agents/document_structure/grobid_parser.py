"""GROBID TEI XML → DocumentStructureResult 変換パーサ。

TEI XML の論文固有構造（abstract / sections / paragraphs / equations /
figure captions / table captions / references）を解析して TypedBlock と
Section のリストを生成する。

設計方針:
- structure-first: タグ構造・見出し階層を優先し、意味解釈は行わない
- references / acknowledgments は除外して body_paragraph に混入させない
- PyMuPDF 補完なしで単体動作できる（bbox / font_size は None）
- parser_source = "grobid_tei" を各 block.raw に付与する
"""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Optional

from .schema import DocumentMetadata, Section, TypedBlock

logger = logging.getLogger(__name__)

try:
    from bs4 import BeautifulSoup, Tag
    _BS4_AVAILABLE = True
except ImportError:
    _BS4_AVAILABLE = False


_EXCLUDED_HEADINGS = re.compile(
    r"(references?|bibliography|acknowledgm|funding|competing\s+interest|"
    r"conflict\s+of\s+interest|author\s+contribution|data\s+availability)",
    re.IGNORECASE,
)

# TEI namespace prefix used by GROBID
_TEI_NS = {"tei": "http://www.tei-c.org/ns/1.0"}


@dataclass
class GROBIDParseResult:
    """GROBIDTEIParser の解析結果。"""
    metadata: DocumentMetadata
    sections: list[Section]
    blocks: list[TypedBlock]


class GROBIDTEIParser:
    """GROBID が返す TEI XML を解析して DocumentStructure を構築する。

    Parameters
    ----------
    tei_xml:
        GROBID の processFulltextDocument が返す TEI XML 文字列。
    """

    def parse(self, tei_xml: str) -> GROBIDParseResult:
        """TEI XML を解析して GROBIDParseResult を返す。

        GROBID が利用不可だった場合など空文字列が渡された場合は
        空の結果を返す（呼び出し側で PyMuPDF にフォールバックする）。
        """
        if not _BS4_AVAILABLE:
            raise RuntimeError("beautifulsoup4 is not installed. Run: pip install beautifulsoup4")
        if not tei_xml or not tei_xml.strip():
            return GROBIDParseResult(
                metadata=DocumentMetadata(),
                sections=[],
                blocks=[],
            )

        try:
            soup = BeautifulSoup(tei_xml, "xml")
        except Exception:
            soup = BeautifulSoup(tei_xml, "lxml")

        metadata = self._parse_metadata(soup)
        sections, blocks = self._parse_body(soup, metadata)

        return GROBIDParseResult(metadata=metadata, sections=sections, blocks=blocks)

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def _parse_metadata(self, soup) -> DocumentMetadata:
        title = None
        title_tag = soup.find("titleStmt")
        if title_tag:
            t = title_tag.find("title", level="a")
            if t is None:
                t = title_tag.find("title")
            if t:
                title = t.get_text(strip=True) or None

        # Issue #372: extract authors ONLY from the front-matter <teiHeader>
        # (fileDesc/sourceDesc/titleStmt). GROBID records citation authors under
        # <text><back><listBibl>, so a document-wide soup.find_all("author")
        # would mix ~100 reference authors into the document author list.
        header = soup.find("teiHeader") or soup.find("fileDesc")
        authors: list[str] = []
        seen: set[str] = set()
        if header is not None:
            for author_tag in header.find_all("author"):
                # Defensive: never read authors that sit inside a bibliography
                # list even if it appears under the header.
                if author_tag.find_parent("listBibl") is not None:
                    continue
                persname = author_tag.find("persName")
                if not persname:
                    continue
                forename = persname.find("forename")
                surname = persname.find("surname")
                parts = []
                if forename:
                    parts.append(forename.get_text(strip=True))
                if surname:
                    parts.append(surname.get_text(strip=True))
                name = " ".join(p for p in parts if p).strip()
                if name and name not in seen:
                    seen.add(name)
                    authors.append(name)

        author_extraction = {
            "source": "grobid_tei" if authors else "none",
            "confidence": 0.95 if authors else 0.0,
            "needs_review": False,
            "review_reasons": [],
            "candidate_sources": {"grobid_tei": list(authors)} if authors else {},
        }
        return DocumentMetadata(
            title=title,
            authors=authors,
            pages=0,
            author_extraction=author_extraction,
        )

    # ------------------------------------------------------------------
    # Body parsing
    # ------------------------------------------------------------------

    def _parse_body(
        self, soup, metadata: DocumentMetadata
    ) -> tuple[list[Section], list[TypedBlock]]:
        sections: list[Section] = []
        blocks: list[TypedBlock] = []
        block_order = 0
        section_order = 0

        # --- Abstract ---
        abstract_tag = soup.find("abstract")
        if abstract_tag:
            abstract_text = abstract_tag.get_text(separator=" ", strip=True)
            if abstract_text:
                sec_id = f"sec_abstract"
                sections.append(Section(
                    section_id=sec_id,
                    title="Abstract",
                    level=1,
                    order=section_order,
                    page_start=1,
                ))
                section_order += 1
                block_id = f"blk_{uuid.uuid4().hex[:8]}"
                blocks.append(TypedBlock(
                    block_id=block_id,
                    page=1,
                    order=block_order,
                    text=abstract_text,
                    block_type="body_paragraph",
                    section_id=sec_id,
                    raw={"parser_source": "grobid_tei", "tei_section_id": "abstract"},
                ))
                block_order += 1

        # --- Body sections ---
        body_tag = soup.find("body")
        if body_tag:
            for div in body_tag.find_all("div", recursive=False):
                section_order, block_order = self._process_div(
                    div=div,
                    sections=sections,
                    blocks=blocks,
                    section_order=section_order,
                    block_order=block_order,
                    level=1,
                    parent_section_id=None,
                )

        return sections, blocks

    def _process_div(
        self,
        div,
        sections: list[Section],
        blocks: list[TypedBlock],
        section_order: int,
        block_order: int,
        level: int,
        parent_section_id: str | None,
    ) -> tuple[int, int]:
        head_tag = div.find("head", recursive=False)
        heading = head_tag.get_text(strip=True) if head_tag else ""

        # References / Acknowledgments は除外
        if heading and _EXCLUDED_HEADINGS.search(heading):
            return section_order, block_order

        # セクション ID
        n_attr = div.get("n", "")
        tei_section_id = f"div_{n_attr}" if n_attr else f"div_{uuid.uuid4().hex[:6]}"
        sec_id = f"sec_{tei_section_id}"

        if heading:
            sections.append(Section(
                section_id=sec_id,
                title=heading,
                level=level,
                order=section_order,
                page_start=1,
                parent_section_id=parent_section_id,
            ))
            section_order += 1

        current_section_id = sec_id if heading else parent_section_id

        # 直下の p / formula / figure を処理
        for child in div.children:
            if not hasattr(child, "name") or child.name is None:
                continue

            if child.name == "p":
                block_order = self._process_paragraph(
                    p_tag=child,
                    blocks=blocks,
                    section_id=current_section_id,
                    block_order=block_order,
                    tei_section_id=tei_section_id,
                )
            elif child.name == "formula":
                block_order = self._process_formula(
                    formula_tag=child,
                    blocks=blocks,
                    section_id=current_section_id,
                    block_order=block_order,
                    tei_section_id=tei_section_id,
                )
            elif child.name == "figure":
                block_order = self._process_figure(
                    fig_tag=child,
                    blocks=blocks,
                    section_id=current_section_id,
                    block_order=block_order,
                    tei_section_id=tei_section_id,
                )
            elif child.name == "div":
                section_order, block_order = self._process_div(
                    div=child,
                    sections=sections,
                    blocks=blocks,
                    section_order=section_order,
                    block_order=block_order,
                    level=level + 1,
                    parent_section_id=current_section_id,
                )

        return section_order, block_order

    def _process_paragraph(
        self,
        p_tag,
        blocks: list[TypedBlock],
        section_id: str | None,
        block_order: int,
        tei_section_id: str,
    ) -> int:
        text = p_tag.get_text(separator=" ", strip=True)
        if not text:
            return block_order
        block_id = f"blk_{uuid.uuid4().hex[:8]}"
        blocks.append(TypedBlock(
            block_id=block_id,
            page=1,
            order=block_order,
            text=text,
            block_type="body_paragraph",
            section_id=section_id,
            raw={
                "parser_source": "grobid_tei",
                "tei_section_id": tei_section_id,
            },
        ))
        return block_order + 1

    def _process_formula(
        self,
        formula_tag,
        blocks: list[TypedBlock],
        section_id: str | None,
        block_order: int,
        tei_section_id: str,
    ) -> int:
        text = formula_tag.get_text(separator=" ", strip=True)
        if not text:
            return block_order

        label_tag = formula_tag.find("label")
        equation_label = label_tag.get_text(strip=True) if label_tag else None

        formula_xml_id = formula_tag.get("xml:id") or formula_tag.get("n") or ""
        block_id = f"blk_{uuid.uuid4().hex[:8]}"
        raw = {
            "parser_source": "grobid_tei",
            "tei_section_id": tei_section_id,
            "tei_formula_id": formula_xml_id,
        }
        # Issue #368: preserve table / container provenance so the equation
        # semantics acceptance gate does not auto-confirm a table-cell formula
        # as a standalone independent equation.
        table_provenance = self._table_container_provenance(formula_tag)
        if table_provenance:
            raw.update(table_provenance)
        blocks.append(TypedBlock(
            block_id=block_id,
            page=1,
            order=block_order,
            text=text,
            block_type="equation_block",
            section_id=section_id,
            equation_label=equation_label,
            raw=raw,
        ))
        return block_order + 1

    @staticmethod
    def _table_container_provenance(formula_tag) -> dict | None:
        """Return table provenance if the formula is inside a <figure type=table>.

        Walks the TEI ancestors of the formula; a ``<figure type="table">``
        container means the formula is a table cell, not a standalone numbered
        equation (issue #368).
        """
        parent = getattr(formula_tag, "parent", None)
        while parent is not None and getattr(parent, "name", None) is not None:
            if parent.name == "figure" and (parent.get("type") or "").lower() == "table":
                table_id = parent.get("xml:id") or parent.get("n") or ""
                provenance: dict = {"container_type": "table", "in_table": True}
                if table_id:
                    provenance["table_id"] = str(table_id)
                return provenance
            parent = getattr(parent, "parent", None)
        return None

    def _process_figure(
        self,
        fig_tag,
        blocks: list[TypedBlock],
        section_id: str | None,
        block_order: int,
        tei_section_id: str,
    ) -> int:
        fig_type = fig_tag.get("type", "figure")

        # figDesc = figure/table caption
        desc_tag = fig_tag.find("figDesc")
        if desc_tag:
            text = desc_tag.get_text(separator=" ", strip=True)
            if text:
                block_type = "table_caption" if fig_type == "table" else "figure_caption"
                block_id = f"blk_{uuid.uuid4().hex[:8]}"
                blocks.append(TypedBlock(
                    block_id=block_id,
                    page=1,
                    order=block_order,
                    text=text,
                    block_type=block_type,
                    section_id=section_id,
                    raw={
                        "parser_source": "grobid_tei",
                        "tei_section_id": tei_section_id,
                        "tei_fig_type": fig_type,
                    },
                ))
                block_order += 1

        # Issue #368: formulas nested inside a <figure type="table"> are table
        # cells, not standalone numbered equations. Emit them so they are not
        # lost, but with table provenance (attached by _process_formula) so the
        # equation semantics gate keeps them out of the auto-confirmed set.
        if fig_type == "table":
            for formula_tag in fig_tag.find_all("formula"):
                block_order = self._process_formula(
                    formula_tag=formula_tag,
                    blocks=blocks,
                    section_id=section_id,
                    block_order=block_order,
                    tei_section_id=tei_section_id,
                )

        return block_order
