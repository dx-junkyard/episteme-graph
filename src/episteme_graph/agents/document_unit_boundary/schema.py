"""DocumentUnitBoundaryAgent のデータモデル定義。

PDF 内の分析対象単位（document unit）を block-level で管理する。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Optional

UNIT_TYPES = [
    "article",
    "chapter",
    "section",
    "lecture",
    "appendix",
    "front_matter",
    "back_matter",
    "unknown_unit",
]

BOUNDARY_ROLES = [
    "title",
    "abstract",
    "body",
    "references",
    "appendix",
    "excluded",
]

# needs_review に追加される理由コード
REVIEW_REASON_CODES = [
    "title_not_detected",
    "multiple_candidate_units_detected",
    "start_boundary_inside_page",
    "end_boundary_inside_page",
    "front_matter_boundary_ambiguous",
    "back_matter_boundary_ambiguous",
    "unit_type_ambiguous",
    "confidence_below_threshold",
]

# confidence 閾値定数
_HIGH_CONFIDENCE_THRESHOLD = 0.70

# ValidationIssue.rule_id のうち hard error（completed にしてはいけない）
HARD_ERROR_RULE_IDS = {
    "missing_target_unit_id",
    "missing_start_block_id",
    "missing_end_block_id",
    "empty_included_block_ids",
    "high_confidence_missing_title",
}


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
class DocumentUnit:
    """PDF 内で独立して分析可能な単位。"""
    unit_id: str
    unit_type: str          # UNIT_TYPES のいずれか
    title: str | None
    title_block_id: str | None
    authors: list[str]
    start_page: int
    start_block_id: str
    end_page: int
    end_block_id: str
    confidence: float
    needs_review: bool
    review_reasons: list[str]
    included_block_ids: list[str] = field(default_factory=list)


@dataclass
class ExcludedBlock:
    """対象ユニット外として除外されたブロック。"""
    block_id: str
    page: int
    reason: str             # "front_matter" | "back_matter" | "outside_target_unit" など


@dataclass
class ValidationIssue:
    rule_id: str
    severity: str           # "error" | "warning"
    message: str
    unit_id: str | None = None
    block_id: str | None = None


@dataclass
class DocumentUnitBoundaryResult:
    """DocumentUnitBoundaryAgent の出力。"""
    document_id: str
    source_file: str
    unit_detection_confidence: float
    needs_review: bool
    review_reasons: list[str]
    target_unit_id: str | None
    units: list[DocumentUnit]
    excluded_blocks: list[ExcludedBlock] = field(default_factory=list)
    validation_issues: list[ValidationIssue] = field(default_factory=list)

    def is_completed(self) -> bool:
        """Hard error がない場合のみ True。pipeline を completed 扱いにするか判定する。"""
        return not any(
            i.rule_id in HARD_ERROR_RULE_IDS
            for i in self.validation_issues
            if i.severity == "error"
        )

    def get_target_unit(self) -> DocumentUnit | None:
        if self.target_unit_id is None:
            return None
        for u in self.units:
            if u.unit_id == self.target_unit_id:
                return u
        return None

    def get_target_unit_block_ids(self) -> list[str]:
        """target unit の included_block_ids を返す。target がなければ空リスト。"""
        unit = self.get_target_unit()
        return unit.included_block_ids if unit else []

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_dict(cls, d: dict) -> "DocumentUnitBoundaryResult":
        units = [DocumentUnit(**u) for u in d.get("units", [])]
        excluded = [ExcludedBlock(**e) for e in d.get("excluded_blocks", [])]
        issues = [ValidationIssue(**i) for i in d.get("validation_issues", [])]
        return cls(
            document_id=d["document_id"],
            source_file=d["source_file"],
            unit_detection_confidence=d["unit_detection_confidence"],
            needs_review=d["needs_review"],
            review_reasons=list(d.get("review_reasons", [])),
            target_unit_id=d.get("target_unit_id"),
            units=units,
            excluded_blocks=excluded,
            validation_issues=issues,
        )
