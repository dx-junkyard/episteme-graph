"""DocumentStructureAgent のデータモデル定義。

PDF から復元した文書構造を表す dataclass 群。すべて JSON シリアライズ可能。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Optional

BLOCK_TYPES = [
    "section_heading",
    "subsection_heading",
    "body_paragraph",
    "equation_block",
    "figure_caption",
    "table_caption",
    "footnote",
    "reference_entry",
    "unknown",
]


@dataclass
class CartridgeContext:
    cartridge_id: str
    ontology: dict
    validation_rules: dict
    aliases: dict | None = None
    notation_patterns: list | None = None
    normalization_rules: list | None = None
    extraction_hints: list | None = None


@dataclass
class RawBlock:
    """PDF パーサが抽出した生ブロック（型付け前）。"""
    page: int
    order: int
    text: str
    bbox: tuple[float, float, float, float] | None = None
    font_size: float | None = None
    font_name: str | None = None
    is_bold: bool = False
    is_centered: bool = False


@dataclass
class TypedBlock:
    """型付け済みブロック。block_type は BLOCK_TYPES のいずれか。"""
    block_id: str
    page: int
    order: int
    text: str
    block_type: str
    bbox: tuple[float, float, float, float] | None = None
    confidence: float = 1.0
    equation_label: str | None = None
    section_id: str | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class Section:
    """復元されたセクション（見出し階層）。"""
    section_id: str
    title: str
    level: int
    order: int
    page_start: int
    page_end: int | None = None
    parent_section_id: str | None = None


@dataclass
class DocumentMetadata:
    title: str | None = None
    authors: list[str] = field(default_factory=list)
    pages: int = 0
    parser_pages_processed: int | None = None
    parser_reached_eof: bool | None = None
    source_bytes_total: int | None = None
    source_bytes_processed: int | None = None
    unclosed_math_environments: list[str] = field(default_factory=list)
    # Author provenance (issue #372): which structured front-matter signal the
    # authors came from (grobid_tei / tex_author / pdf_front_matter / none),
    # with confidence + needs_review. None for legacy artifacts.
    author_extraction: dict | None = None


@dataclass
class ValidationIssue:
    rule_id: str
    severity: str  # "error" | "warning"
    message: str
    block_id: str | None = None


@dataclass
class DocumentStructureResult:
    """DocumentStructureAgent の出力。後段 agent がそのまま入力として受け取る。"""
    document_id: str
    source_file: str
    cartridge_id: str | None
    metadata: DocumentMetadata
    sections: list[Section]
    blocks: list[TypedBlock]
    validation_issues: list[ValidationIssue] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_dict(cls, d: dict) -> "DocumentStructureResult":
        metadata = DocumentMetadata(**d["metadata"])
        sections = [Section(**s) for s in d["sections"]]
        blocks = [TypedBlock(**b) for b in d["blocks"]]
        issues = [ValidationIssue(**i) for i in d.get("validation_issues", [])]
        return cls(
            document_id=d["document_id"],
            source_file=d["source_file"],
            cartridge_id=d.get("cartridge_id"),
            metadata=metadata,
            sections=sections,
            blocks=blocks,
            validation_issues=issues,
        )
