"""FigureTableSemantics data models."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field


@dataclass
class FigureSourceLocation:
    page: int | None
    caption_block_id: str
    section_id: str | None = None


@dataclass
class FigureRecord:
    figure_id: str
    document_id: str
    figure_type: str
    source_location: FigureSourceLocation
    caption: str
    inputs: list[dict] = field(default_factory=list)
    outputs: list[dict] = field(default_factory=list)
    comparison_axes: list[str] = field(default_factory=list)
    visual_elements: list[dict] = field(default_factory=list)
    linked_claim_ids: list[str] = field(default_factory=list)
    linked_component_candidates: list[str] = field(default_factory=list)
    interpretation: str = ""
    teaching_takeaway: str = ""
    source_evidence_ids: list[str] = field(default_factory=list)


@dataclass
class TableRecord:
    table_id: str
    document_id: str
    table_type: str
    source_location: FigureSourceLocation
    caption: str
    columns: list[str] = field(default_factory=list)
    rows_summary: str = ""
    comparison_axes: list[str] = field(default_factory=list)
    linked_claim_ids: list[str] = field(default_factory=list)
    interpretation: str = ""
    teaching_takeaway: str = ""
    source_evidence_ids: list[str] = field(default_factory=list)


@dataclass
class ValidationIssue:
    rule_id: str
    severity: str
    message: str
    field: str | None = None


@dataclass
class FigureTableSemanticsResult:
    document_id: str
    cartridge_id: str | None
    figures: list[FigureRecord] = field(default_factory=list)
    tables: list[TableRecord] = field(default_factory=list)
    validation_issues: list[ValidationIssue] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)
