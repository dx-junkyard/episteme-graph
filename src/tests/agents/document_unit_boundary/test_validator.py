"""BoundaryValidator のユニットテスト。"""
from __future__ import annotations

import pytest

from episteme_graph.agents.document_unit_boundary.schema import (
    DocumentUnit,
    DocumentUnitBoundaryResult,
    ExcludedBlock,
)
from episteme_graph.agents.document_unit_boundary.validator import BoundaryValidator


def _make_unit(
    unit_id: str = "unit_001",
    unit_type: str = "article",
    title: str | None = "Test Title",
    title_block_id: str | None = "blk_001_0000",
    start_block_id: str = "blk_001_0000",
    end_block_id: str = "blk_003_0010",
    confidence: float = 0.75,
    included_block_ids: list[str] | None = None,
    review_reasons: list[str] | None = None,
) -> DocumentUnit:
    return DocumentUnit(
        unit_id=unit_id,
        unit_type=unit_type,
        title=title,
        title_block_id=title_block_id,
        authors=[],
        start_page=1,
        start_block_id=start_block_id,
        end_page=3,
        end_block_id=end_block_id,
        confidence=confidence,
        needs_review=bool(review_reasons),
        review_reasons=review_reasons or [],
        included_block_ids=(
            included_block_ids
            if included_block_ids is not None
            else ["blk_001_0000", "blk_002_0005"]
        ),
    )


def _make_result(
    units: list[DocumentUnit] | None = None,
    target_unit_id: str | None = "unit_001",
    excluded_blocks: list[ExcludedBlock] | None = None,
) -> DocumentUnitBoundaryResult:
    if units is None:
        units = [_make_unit()]
    return DocumentUnitBoundaryResult(
        document_id="doc_test",
        source_file="/fake/path.pdf",
        unit_detection_confidence=0.75,
        needs_review=False,
        review_reasons=[],
        target_unit_id=target_unit_id,
        units=units,
        excluded_blocks=excluded_blocks or [],
    )


@pytest.fixture
def validator() -> BoundaryValidator:
    return BoundaryValidator()


class TestHardErrors:
    def test_missing_target_unit_id_is_error(self, validator):
        result = _make_result(target_unit_id=None)
        issues = validator.validate(result)
        rule_ids = [i.rule_id for i in issues]
        assert "missing_target_unit_id" in rule_ids
        error_issues = [i for i in issues if i.rule_id == "missing_target_unit_id"]
        assert all(i.severity == "error" for i in error_issues)

    def test_nonexistent_target_unit_id_is_error(self, validator):
        result = _make_result(target_unit_id="unit_999")
        issues = validator.validate(result)
        rule_ids = [i.rule_id for i in issues]
        assert "missing_target_unit_id" in rule_ids

    def test_missing_start_block_id_is_error(self, validator):
        unit = _make_unit(start_block_id="")
        result = _make_result(units=[unit])
        issues = validator.validate(result)
        rule_ids = [i.rule_id for i in issues]
        assert "missing_start_block_id" in rule_ids

    def test_missing_end_block_id_is_error(self, validator):
        unit = _make_unit(end_block_id="")
        result = _make_result(units=[unit])
        issues = validator.validate(result)
        rule_ids = [i.rule_id for i in issues]
        assert "missing_end_block_id" in rule_ids

    def test_empty_included_block_ids_is_error(self, validator):
        unit = _make_unit(included_block_ids=[])
        result = _make_result(units=[unit])
        issues = validator.validate(result)
        rule_ids = [i.rule_id for i in issues]
        assert "empty_included_block_ids" in rule_ids

    def test_high_confidence_missing_title_is_error(self, validator):
        unit = _make_unit(title_block_id=None, confidence=0.85)
        result = _make_result(units=[unit])
        issues = validator.validate(result)
        rule_ids = [i.rule_id for i in issues]
        assert "high_confidence_missing_title" in rule_ids

    def test_is_not_completed_when_hard_error(self, validator):
        result = _make_result(target_unit_id=None)
        result.validation_issues = validator.validate(result)
        assert not result.is_completed()


class TestWarnings:
    def test_valid_result_has_no_hard_errors(self, validator):
        result = _make_result()
        issues = validator.validate(result)
        hard_errors = [i for i in issues if i.severity == "error"]
        assert hard_errors == []

    def test_valid_result_is_completed(self, validator):
        result = _make_result()
        result.validation_issues = validator.validate(result)
        assert result.is_completed()

    def test_multiple_units_triggers_warning(self, validator):
        units = [
            _make_unit("unit_001"),
            _make_unit("unit_002"),
        ]
        result = _make_result(units=units, target_unit_id="unit_001")
        issues = validator.validate(result)
        rule_ids = [i.rule_id for i in issues]
        assert "multiple_units_auto_selected_target" in rule_ids

    def test_low_confidence_unit_warning(self, validator):
        unit = _make_unit(confidence=0.30)
        result = _make_result(units=[unit])
        issues = validator.validate(result)
        rule_ids = [i.rule_id for i in issues]
        assert "low_unit_confidence" in rule_ids

    def test_title_not_detected_warning_for_low_confidence(self, validator):
        unit = _make_unit(title_block_id=None, confidence=0.40)
        result = _make_result(units=[unit])
        issues = validator.validate(result)
        rule_ids = [i.rule_id for i in issues]
        # Low confidence + no title → warning, not error
        assert "title_not_detected" in rule_ids
        title_issues = [i for i in issues if i.rule_id == "title_not_detected"]
        assert all(i.severity == "warning" for i in title_issues)
