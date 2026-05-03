"""PaperSkeletonValidator: PaperSkeletonResult の自己検証を行い ValidationIssue を返す。

検証ルール:
- headline_claim.text が空でない
- logical_blocks が 2 件以上ある
- logical_blocks[].block_type が許可語彙にある
- paper_goal / central_question / headline_claim の evidence_block_ids が存在
- excluded_regions に prior_work / meta が最低1件ある（警告のみ）
- headline_claim が露骨な figure narration でない
- confidence が 0〜1 に収まる
"""
from __future__ import annotations

import re

from .schema import (
    LOGICAL_BLOCK_TYPES,
    CartridgeContext,
    PaperSkeletonResult,
    ValidationIssue,
)

_FIGURE_NARRATION_RE = re.compile(
    r"^(Figure|Fig\.?|Table|図|表)\s*\d+", re.IGNORECASE
)

_MIN_LOGICAL_BLOCKS = 2


class PaperSkeletonValidator:
    def validate(
        self,
        result: PaperSkeletonResult,
        cartridge: CartridgeContext | None = None,
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []

        issues += self._check_headline_claim_not_empty(result)
        issues += self._check_logical_blocks_count(result)
        issues += self._check_logical_block_types(result)
        issues += self._check_evidence_block_ids(result)
        issues += self._check_excluded_regions(result)
        issues += self._check_headline_not_figure_narration(result)
        issues += self._check_confidence_range(result)

        return issues

    # ------------------------------------------------------------------

    def _check_headline_claim_not_empty(
        self, result: PaperSkeletonResult
    ) -> list[ValidationIssue]:
        text = (result.headline_claim or {}).get("text", "")
        if not text or not text.strip():
            return [ValidationIssue(
                rule_id="empty_headline_claim",
                severity="error",
                message="headline_claim.text is empty",
                field="headline_claim.text",
            )]
        return []

    def _check_logical_blocks_count(
        self, result: PaperSkeletonResult
    ) -> list[ValidationIssue]:
        n = len(result.logical_blocks)
        if n < _MIN_LOGICAL_BLOCKS:
            return [ValidationIssue(
                rule_id="insufficient_logical_blocks",
                severity="error",
                message=f"logical_blocks has {n} item(s); expected at least {_MIN_LOGICAL_BLOCKS}",
                field="logical_blocks",
            )]
        return []

    def _check_logical_block_types(
        self, result: PaperSkeletonResult
    ) -> list[ValidationIssue]:
        issues = []
        for lb in result.logical_blocks:
            if lb.block_type not in LOGICAL_BLOCK_TYPES:
                issues.append(ValidationIssue(
                    rule_id="invalid_logical_block_type",
                    severity="error",
                    message=(
                        f"logical_block '{lb.block_id}' has invalid block_type "
                        f"'{lb.block_type}'"
                    ),
                    field=f"logical_blocks[{lb.block_id}].block_type",
                ))
        return issues

    def _check_evidence_block_ids(
        self, result: PaperSkeletonResult
    ) -> list[ValidationIssue]:
        issues = []
        for field_name, entry in [
            ("paper_goal", result.paper_goal),
            ("central_question", result.central_question),
            ("headline_claim", result.headline_claim),
        ]:
            if not isinstance(entry, dict):
                continue
            ids = entry.get("evidence_block_ids", [])
            if not ids:
                issues.append(ValidationIssue(
                    rule_id="missing_evidence_block_ids",
                    severity="warning",
                    message=f"{field_name}.evidence_block_ids is empty",
                    field=f"{field_name}.evidence_block_ids",
                ))
        return issues

    def _check_excluded_regions(
        self, result: PaperSkeletonResult
    ) -> list[ValidationIssue]:
        has_prior_or_meta = any(
            r.get("region_type") in ("prior_work", "meta")
            for r in result.excluded_regions
            if isinstance(r, dict)
        )
        if not has_prior_or_meta:
            return [ValidationIssue(
                rule_id="no_excluded_prior_work_or_meta",
                severity="warning",
                message=(
                    "excluded_regions contains no prior_work or meta entry; "
                    "consider whether prior work was correctly separated"
                ),
                field="excluded_regions",
            )]
        return []

    def _check_headline_not_figure_narration(
        self, result: PaperSkeletonResult
    ) -> list[ValidationIssue]:
        text = (result.headline_claim or {}).get("text", "")
        if text and _FIGURE_NARRATION_RE.match(text.strip()):
            return [ValidationIssue(
                rule_id="headline_claim_is_figure_narration",
                severity="error",
                message=(
                    f"headline_claim appears to be a figure narration: '{text[:80]}'"
                ),
                field="headline_claim.text",
            )]
        return []

    def _check_confidence_range(
        self, result: PaperSkeletonResult
    ) -> list[ValidationIssue]:
        issues = []
        val = result.confidence
        if not (0.0 <= val <= 1.0):
            issues.append(ValidationIssue(
                rule_id="confidence_out_of_range",
                severity="error",
                message=f"confidence={val} is out of range [0.0, 1.0]",
                field="confidence",
            ))
        for lb in result.logical_blocks:
            if not (0.0 <= lb.confidence <= 1.0):
                issues.append(ValidationIssue(
                    rule_id="confidence_out_of_range",
                    severity="error",
                    message=(
                        f"logical_block '{lb.block_id}' confidence={lb.confidence} "
                        "is out of range"
                    ),
                    field=f"logical_blocks[{lb.block_id}].confidence",
                ))
        return issues
