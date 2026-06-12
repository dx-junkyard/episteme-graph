"""EvidenceRegistry data models.

Evidence は Claim の根拠であり、LLM レビューコメントとは厳密に分離する。
- evidence_text: PDF 原文の引用のみ
- review_note: LLM が出した品質コメント（任意）
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

EVIDENCE_ROLES = [
    "source_quote",         # PDF 原文の直接引用
    "section_summary",      # セクション全体を指す抽象的 evidence（参照のみ）
    "equation_quote",       # 式ブロックの引用
    "figure_caption_quote", # 図キャプションの引用
    "table_caption_quote",  # 表キャプションの引用
    "sentence_quote",       # block 内の 1 文単位の引用 (issue #363)
]

PUBLIC_EXPORT_POLICIES = [
    "location_only",   # 公開時には source pointer のみ出す
    "snippet_allowed", # 短い引用なら公開可
    "internal_only",   # 内部利用のみ、外部公開禁止
]


@dataclass
class EvidenceSource:
    """Evidence が指す PDF 上の位置情報。

    block_id / section_id は DocumentStructureAgent が発行した識別子と一致する。
    span_start / span_end は block.text 内の文字オフセット。
    """
    page: int | None
    section_id: str | None
    block_id: str
    span_start: int = 0
    span_end: int = 0


@dataclass
class EvidenceRecord:
    evidence_id: str
    document_id: str
    source: EvidenceSource
    evidence_text: str
    evidence_role: str = "source_quote"
    public_export_policy: str = "location_only"
    review_note: str = ""
    cartridge_id: str | None = None
    # Sentence-level records point back at their block-level record (issue #363).
    parent_evidence_id: str | None = None


@dataclass
class ValidationIssue:
    rule_id: str
    severity: str
    message: str
    field: str | None = None


@dataclass
class EvidenceRegistryResult:
    document_id: str
    cartridge_id: str | None
    records: list[EvidenceRecord] = field(default_factory=list)
    validation_issues: list[ValidationIssue] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def index_by_id(self) -> dict[str, EvidenceRecord]:
        return {r.evidence_id: r for r in self.records}

    @classmethod
    def from_dict(cls, d: dict) -> "EvidenceRegistryResult":
        records = []
        for r in d.get("records", []):
            src = EvidenceSource(**r["source"])
            records.append(EvidenceRecord(
                evidence_id=r["evidence_id"],
                document_id=r["document_id"],
                source=src,
                evidence_text=r.get("evidence_text", ""),
                evidence_role=r.get("evidence_role", "source_quote"),
                public_export_policy=r.get("public_export_policy", "location_only"),
                review_note=r.get("review_note", ""),
                cartridge_id=r.get("cartridge_id"),
                parent_evidence_id=r.get("parent_evidence_id"),
            ))
        issues = [ValidationIssue(**i) for i in d.get("validation_issues", [])]
        return cls(
            document_id=d["document_id"],
            cartridge_id=d.get("cartridge_id"),
            records=records,
            validation_issues=issues,
        )
