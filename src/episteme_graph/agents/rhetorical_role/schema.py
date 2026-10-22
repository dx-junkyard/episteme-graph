"""RhetoricalRoleAgent data models.

The output is a reviewable intermediate layer between paper-level skeleton
hypotheses and downstream claim extraction. All dataclasses are JSON
serializable.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from episteme_graph.agents.validation import ValidationIssue

from episteme_graph.agents.cartridge_context import CartridgeContext

ROLE_LABELS = [
    "definition",
    "observable_definition",
    "equation_definition",
    "relation",
    "equation_relation",
    "equation_transformation",
    "assumption",
    "approximation",
    "derivation_step",
    "correction",
    "uncertainty",
    "limitation",
    "result",
    "diagnostic_claim",
    "method_choice",
    "prior_work",
    "figure_narration",
    "table_narration",
    "section_meta",
    "meta_discourse",
    "citation_context",
    "background_general",
    "unknown",
]

CORE_CLAIM_ROLES = {
    "definition",
    "observable_definition",
    "equation_definition",
    "relation",
    "equation_relation",
    "equation_transformation",
    "assumption",
    "approximation",
    "derivation_step",
    "correction",
    "uncertainty",
    "limitation",
    "result",
    "diagnostic_claim",
    "method_choice",
}

EXCLUSION_ROLES = {
    "prior_work",
    "figure_narration",
    "table_narration",
    "section_meta",
    "meta_discourse",
    "citation_context",
    "background_general",
}

PRIOR_WORK_NOT_REJECT_NOTE = (
    "prior_work is context, not a reject candidate (IK-0803)"
)


def normalize_reject_flag(role_labels, is_reject_candidate: bool) -> tuple[bool, bool]:
    """先行研究の言及を reject 候補にしない（IK-0803・#317 の情報を落とさない）。

    LLM は prior_work の span に ``is_reject_candidate=True`` を付けることがある
    （seq 3028: ``["prior_work", "citation_context"]``）。先行研究の主張は claim 候補から
    外すが reject ではなく文脈として後段の採否判定に残すので、reject 印を外す。
    戻り値は ``(is_reject_candidate, changed)``。
    """
    if is_reject_candidate and "prior_work" in (role_labels or []):
        return False, True
    return bool(is_reject_candidate), False


@dataclass
class RoleLLMInput:
    document_id: str
    cartridge_id: str | None
    block_id: str
    section_id: str | None
    section_title: str | None
    backbone_block_type: str | None
    headline_claim: str | None
    block_text: str
    prev_text: str | None
    next_text: str | None
    normalized_terms: list[dict] | None = None
    # 2026-10-16（S1 seq 2722 ほか）: 直前 2 ブロック以内（同じ節）に式ブロックがあるときだけ
    # その式 ``{block_id, label, latex}``（TeX 由来のみ本文・PDF 由来は None）。
    # ブロック先頭の where / which / i.e. が指す式を材料に入れる。
    preceding_equation: dict | None = None
    # 2026-10-17（X1 seq 3376）: 前後の文脈ブロックが別の節のものか。プロンプトの見出しで明示する。
    prev_text_other_section: bool = False
    next_text_other_section: bool = False


@dataclass
class SpanAnnotation:
    span_id: str
    text: str
    char_start: int
    char_end: int
    role_labels: list[str]
    is_claim_candidate: bool
    is_reject_candidate: bool
    confidence: float
    reason: str
    # How ``char_start`` / ``char_end`` were obtained when the LLM's own offsets
    # did not quote ``text`` verbatim: ``None`` = the LLM's numbers were used as
    # given, otherwise one of the ``repair.OFFSET_CORRECTION_*`` markers
    # (the offsets were recomputed deterministically from ``text``). Recorded so
    # the rewrite is visible instead of silent (P4).
    offset_correction: str | None = None


@dataclass
class BlockRoleAnnotation:
    block_id: str
    section_id: str | None
    backbone_block_type: str | None
    span_annotations: list[SpanAnnotation]



@dataclass
class RhetoricalRoleResult:
    document_id: str
    cartridge_id: str | None
    role_annotations: list[BlockRoleAnnotation]
    summary_stats: dict
    validation_issues: list[ValidationIssue] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_dict(cls, d: dict) -> "RhetoricalRoleResult":
        annotations = []
        for a in d.get("role_annotations", []):
            spans = [SpanAnnotation(**s) for s in a.get("span_annotations", [])]
            annotations.append(BlockRoleAnnotation(
                block_id=a["block_id"],
                section_id=a.get("section_id"),
                backbone_block_type=a.get("backbone_block_type"),
                span_annotations=spans,
            ))
        issues = [ValidationIssue(**i) for i in d.get("validation_issues", [])]
        return cls(
            document_id=d["document_id"],
            cartridge_id=d.get("cartridge_id"),
            role_annotations=annotations,
            summary_stats=d.get("summary_stats", {}),
            validation_issues=issues,
        )

    @classmethod
    def make_fallback(
        cls,
        document_id: str,
        cartridge_id: str | None,
        block_id: str | None,
        reason: str,
    ) -> "RhetoricalRoleResult":
        issues = [ValidationIssue(
            rule_id="role_generation_failed",
            severity="error",
            message=reason,
            field=block_id,
        )]
        return cls(
            document_id=document_id,
            cartridge_id=cartridge_id,
            role_annotations=[],
            summary_stats={"claim_candidate_spans": 0, "reject_candidate_spans": 0},
            validation_issues=issues,
        )
