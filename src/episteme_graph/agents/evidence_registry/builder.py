"""EvidenceRegistryBuilder.

DocumentStructureResult と各 agent の中間成果物（QualifiedSpan / EquationSemantics 等）
を受け取り、Evidence を一元化した EvidenceRegistryResult を生成する。

このモジュールは LLM を使わない決定論的ビルダー。
review_note には外部から渡された LLM コメントが入るが、evidence_text には
DocumentStructureResult の block.text の原文範囲のみを採用する。
"""
from __future__ import annotations

from .schema import (
    EVIDENCE_ROLES,
    EvidenceRecord,
    EvidenceRegistryResult,
    EvidenceSource,
    PUBLIC_EXPORT_POLICIES,
    ValidationIssue,
)


class EvidenceRegistryBuilder:
    """Evidence Registry を構築するビルダー。

    Usage:
        builder = EvidenceRegistryBuilder(doc_structure_result)
        ev_id = builder.add_for_block("blk_004_0065", role="source_quote")
        ev_id = builder.add_for_span("blk_004_0065", 0, 180)
        result = builder.build(document_id="doc_001", cartridge_id="particle_physics")
    """

    def __init__(self, document_structure_result: object | None = None) -> None:
        self._block_index: dict[str, object] = {}
        if document_structure_result is not None:
            blocks = getattr(document_structure_result, "blocks", None) or []
            for b in blocks:
                bid = getattr(b, "block_id", None)
                if bid:
                    self._block_index[bid] = b
        self._records: list[EvidenceRecord] = []
        self._issues: list[ValidationIssue] = []
        self._dedup_key_to_id: dict[tuple, str] = {}
        self._counter = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def register_block(self, document_structure_block: object) -> None:
        """外部で組み立てた block を登録する補助 API（テスト用）。"""
        bid = getattr(document_structure_block, "block_id", None)
        if bid:
            self._block_index[bid] = document_structure_block

    def add_for_block(
        self,
        block_id: str,
        *,
        evidence_role: str = "source_quote",
        public_export_policy: str = "location_only",
        review_note: str = "",
    ) -> str | None:
        """Block 全体を evidence として登録する。
        重複登録時は既存 evidence_id を返す。
        """
        block = self._block_index.get(block_id)
        if block is None:
            self._issues.append(ValidationIssue(
                rule_id="evidence_block_missing",
                severity="error",
                message=f"block not found: {block_id}",
                field=block_id,
            ))
            return None
        text = getattr(block, "text", "") or ""
        return self._add(
            block_id=block_id,
            section_id=getattr(block, "section_id", None),
            page=getattr(block, "page", None),
            span_start=0,
            span_end=len(text),
            evidence_text=text,
            evidence_role=evidence_role,
            public_export_policy=public_export_policy,
            review_note=review_note,
        )

    def add_for_span(
        self,
        block_id: str,
        span_start: int,
        span_end: int,
        *,
        evidence_role: str = "source_quote",
        public_export_policy: str = "location_only",
        review_note: str = "",
    ) -> str | None:
        """Block 内の span 範囲を evidence として登録する。"""
        block = self._block_index.get(block_id)
        if block is None:
            self._issues.append(ValidationIssue(
                rule_id="evidence_block_missing",
                severity="error",
                message=f"block not found: {block_id}",
                field=block_id,
            ))
            return None
        text = getattr(block, "text", "") or ""
        if span_start < 0 or span_end > len(text) or span_start > span_end:
            self._issues.append(ValidationIssue(
                rule_id="evidence_span_out_of_range",
                severity="warning",
                message=f"span out of range for {block_id}: [{span_start}, {span_end}] vs len={len(text)}",
                field=block_id,
            ))
            span_start = max(0, min(span_start, len(text)))
            span_end = max(span_start, min(span_end, len(text)))
        snippet = text[span_start:span_end]
        return self._add(
            block_id=block_id,
            section_id=getattr(block, "section_id", None),
            page=getattr(block, "page", None),
            span_start=span_start,
            span_end=span_end,
            evidence_text=snippet,
            evidence_role=evidence_role,
            public_export_policy=public_export_policy,
            review_note=review_note,
        )

    def attach_review_note(self, evidence_id: str, review_note: str) -> bool:
        """既存 evidence にレビューコメントを追記する。
        evidence_text を汚染しないことを保証する。
        """
        for r in self._records:
            if r.evidence_id == evidence_id:
                if review_note:
                    r.review_note = (
                        f"{r.review_note}\n{review_note}".strip()
                        if r.review_note
                        else review_note
                    )
                return True
        return False

    def build(
        self,
        document_id: str,
        cartridge_id: str | None = None,
    ) -> EvidenceRegistryResult:
        for r in self._records:
            r.cartridge_id = cartridge_id
            r.document_id = document_id
        self._validate()
        return EvidenceRegistryResult(
            document_id=document_id,
            cartridge_id=cartridge_id,
            records=list(self._records),
            validation_issues=list(self._issues),
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    def _add(
        self,
        *,
        block_id: str,
        section_id: str | None,
        page: int | None,
        span_start: int,
        span_end: int,
        evidence_text: str,
        evidence_role: str,
        public_export_policy: str,
        review_note: str,
    ) -> str:
        if evidence_role not in EVIDENCE_ROLES:
            self._issues.append(ValidationIssue(
                rule_id="evidence_role_invalid",
                severity="warning",
                message=f"unknown evidence_role: {evidence_role}",
                field=block_id,
            ))
            evidence_role = "source_quote"
        if public_export_policy not in PUBLIC_EXPORT_POLICIES:
            public_export_policy = "location_only"

        key = (block_id, span_start, span_end, evidence_role)
        if key in self._dedup_key_to_id:
            evidence_id = self._dedup_key_to_id[key]
            if review_note:
                self.attach_review_note(evidence_id, review_note)
            return evidence_id

        self._counter += 1
        evidence_id = f"ev_{self._counter:04d}"
        record = EvidenceRecord(
            evidence_id=evidence_id,
            document_id="",  # set in build()
            source=EvidenceSource(
                page=page,
                section_id=section_id,
                block_id=block_id,
                span_start=span_start,
                span_end=span_end,
            ),
            evidence_text=evidence_text,
            evidence_role=evidence_role,
            public_export_policy=public_export_policy,
            review_note=review_note,
        )
        self._records.append(record)
        self._dedup_key_to_id[key] = evidence_id
        return evidence_id

    def _validate(self) -> None:
        for r in self._records:
            # Evidence text must be source-derived (not LLM commentary).
            # We cannot perfectly detect, but we flag obvious review-style markers.
            text = r.evidence_text or ""
            review_markers = (
                "should be split", "this span", "this is", "this evidence",
                "rewrite", "consider", "needs to", "we recommend",
            )
            lower = text.lower()
            if any(m in lower for m in review_markers) and r.evidence_role == "source_quote":
                self._issues.append(ValidationIssue(
                    rule_id="evidence_text_looks_like_review_note",
                    severity="warning",
                    message=(
                        f"evidence_text for {r.evidence_id} contains review-style language; "
                        "review_note may have leaked into evidence_text"
                    ),
                    field=r.evidence_id,
                ))
            if not r.source.block_id:
                self._issues.append(ValidationIssue(
                    rule_id="evidence_missing_block_id",
                    severity="error",
                    message=f"evidence {r.evidence_id} has no block_id",
                    field=r.evidence_id,
                ))
