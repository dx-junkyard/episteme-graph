"""BoundaryValidator: DocumentUnitBoundaryResult の検証を行い ValidationIssue を返す。

Hard error → is_completed() = False:
  - target_unit_id が存在しない
  - target unit に start_block_id または end_block_id がない
  - target unit の included_block_ids が空
  - title_block_id == null かつ confidence が高信頼

Warning / needs_review:
  - unit が複数検出されたが target が自動選択された
  - title が未検出
  - start/end boundary がページ途中にある
  - confidence が閾値未満
"""
from __future__ import annotations

from .schema import (
    DocumentUnit,
    DocumentUnitBoundaryResult,
    ValidationIssue,
    _HIGH_CONFIDENCE_THRESHOLD,
)

_CONFIDENCE_WARNING_THRESHOLD = 0.55


class BoundaryValidator:
    def validate(
        self, result: DocumentUnitBoundaryResult
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []

        issues += self._check_target_unit_exists(result)
        if result.target_unit_id:
            target = result.get_target_unit()
            if target:
                issues += self._check_target_unit_boundaries(target)
                issues += self._check_included_block_ids(target)
                issues += self._check_title_confidence(target)

        issues += self._check_multiple_units_auto_selected(result)
        issues += self._check_unit_confidences(result)

        return issues

    # ------------------------------------------------------------------

    def _check_target_unit_exists(
        self, result: DocumentUnitBoundaryResult
    ) -> list[ValidationIssue]:
        if result.target_unit_id is None:
            return [ValidationIssue(
                rule_id="missing_target_unit_id",
                severity="error",
                message="target_unit_id is not set; cannot determine analysis scope",
            )]
        unit_ids = {u.unit_id for u in result.units}
        if result.target_unit_id not in unit_ids:
            return [ValidationIssue(
                rule_id="missing_target_unit_id",
                severity="error",
                message=(
                    f"target_unit_id '{result.target_unit_id}' not found in units"
                ),
            )]
        return []

    def _check_target_unit_boundaries(
        self, unit: DocumentUnit
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if not unit.start_block_id:
            issues.append(ValidationIssue(
                rule_id="missing_start_block_id",
                severity="error",
                message=f"unit '{unit.unit_id}' has no start_block_id",
                unit_id=unit.unit_id,
            ))
        if not unit.end_block_id:
            issues.append(ValidationIssue(
                rule_id="missing_end_block_id",
                severity="error",
                message=f"unit '{unit.unit_id}' has no end_block_id",
                unit_id=unit.unit_id,
            ))
        return issues

    def _check_included_block_ids(
        self, unit: DocumentUnit
    ) -> list[ValidationIssue]:
        if not unit.included_block_ids:
            return [ValidationIssue(
                rule_id="empty_included_block_ids",
                severity="error",
                message=f"unit '{unit.unit_id}' has no included_block_ids",
                unit_id=unit.unit_id,
            )]
        return []

    def _check_title_confidence(
        self, unit: DocumentUnit
    ) -> list[ValidationIssue]:
        if unit.title_block_id is None and unit.confidence >= _HIGH_CONFIDENCE_THRESHOLD:
            return [ValidationIssue(
                rule_id="high_confidence_missing_title",
                severity="error",
                message=(
                    f"unit '{unit.unit_id}' has confidence={unit.confidence:.2f} "
                    f"but title_block_id is null"
                ),
                unit_id=unit.unit_id,
            )]
        if unit.title_block_id is None:
            return [ValidationIssue(
                rule_id="title_not_detected",
                severity="warning",
                message=f"unit '{unit.unit_id}' has no detected title block",
                unit_id=unit.unit_id,
            )]
        return []

    def _check_multiple_units_auto_selected(
        self, result: DocumentUnitBoundaryResult
    ) -> list[ValidationIssue]:
        if len(result.units) > 1:
            return [ValidationIssue(
                rule_id="multiple_units_auto_selected_target",
                severity="warning",
                message=(
                    f"{len(result.units)} units detected; "
                    f"target auto-selected as '{result.target_unit_id}'"
                ),
            )]
        return []

    def _check_unit_confidences(
        self, result: DocumentUnitBoundaryResult
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for unit in result.units:
            if unit.confidence < _CONFIDENCE_WARNING_THRESHOLD:
                issues.append(ValidationIssue(
                    rule_id="low_unit_confidence",
                    severity="warning",
                    message=(
                        f"unit '{unit.unit_id}' confidence={unit.confidence:.2f} "
                        f"is below threshold {_CONFIDENCE_WARNING_THRESHOLD}"
                    ),
                    unit_id=unit.unit_id,
                ))
        return issues
