"""PaperSkeletonAgent のデータモデル定義。

すべて JSON シリアライズ可能。DocumentStructureResult と互換の入出力設計。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Optional

LOGICAL_BLOCK_TYPES = [
    "problem_setting",
    "background",
    "assumptions",
    "derivation",
    "core_relation",
    "correction",
    "uncertainty",
    "method_choice",
    "result",
    "diagnostic",
    "future_work",
    "conclusion",
    "prior_work",
    "meta",
]

EXCLUDED_REGION_TYPES = [
    "prior_work",
    "meta",
    "figure_narration",
    "section_transition",
    "appendix",
]

SKELETON_VERSION = "v1"


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
class SkeletonLLMInput:
    """InputBuilder が DocumentStructureResult から組み立てる LLM 入力。"""
    document_id: str
    cartridge_id: str | None
    title: str | None
    abstract_blocks: list[dict]
    section_headers: list[dict]
    representative_blocks: list[dict]
    conclusion_blocks: list[dict]
    normalized_terms: list[dict] | None = None


@dataclass
class LogicalBlock:
    """backbone の論理ブロック（仮定・導出・不確かさなど）。"""
    block_id: str
    block_type: str
    label: str
    section_ids: list[str]
    evidence_block_ids: list[str]
    summary: str
    reason: str
    confidence: float


@dataclass
class ValidationIssue:
    rule_id: str
    severity: str  # "error" | "warning"
    message: str
    field: str | None = None


@dataclass
class PaperSkeletonResult:
    """PaperSkeletonAgent の出力。後段 agent がそのまま入力として受け取る。"""
    document_id: str
    skeleton_version: str
    cartridge_id: str | None
    paper_goal: dict
    central_question: dict
    headline_claim: dict
    supporting_subclaims: list[dict]
    logical_blocks: list[LogicalBlock]
    excluded_regions: list[dict]
    review_notes: list[str]
    confidence: float
    validation_issues: list[ValidationIssue] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_dict(cls, d: dict) -> "PaperSkeletonResult":
        logical_blocks = [LogicalBlock(**b) for b in d.get("logical_blocks", [])]
        issues = [ValidationIssue(**i) for i in d.get("validation_issues", [])]
        return cls(
            document_id=d["document_id"],
            skeleton_version=d.get("skeleton_version", SKELETON_VERSION),
            cartridge_id=d.get("cartridge_id"),
            paper_goal=d.get("paper_goal", {}),
            central_question=d.get("central_question", {}),
            headline_claim=d.get("headline_claim", {}),
            supporting_subclaims=d.get("supporting_subclaims", []),
            logical_blocks=logical_blocks,
            excluded_regions=d.get("excluded_regions", []),
            review_notes=d.get("review_notes", []),
            confidence=d.get("confidence", 0.0),
            validation_issues=issues,
        )

    @classmethod
    def make_fallback(cls, document_id: str, cartridge_id: str | None, reason: str) -> "PaperSkeletonResult":
        """LLM 生成失敗時の暫定 fallback 出力を返す。"""
        empty_entry = {"text": "", "evidence_block_ids": [], "reason": reason, "confidence": 0.0}
        return cls(
            document_id=document_id,
            skeleton_version=SKELETON_VERSION,
            cartridge_id=cartridge_id,
            paper_goal=empty_entry,
            central_question=empty_entry,
            headline_claim=empty_entry,
            supporting_subclaims=[],
            logical_blocks=[],
            excluded_regions=[],
            review_notes=[f"Skeleton generation failed: {reason}"],
            confidence=0.0,
        )
