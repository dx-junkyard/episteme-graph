"""Tests for issue #366: document completeness gate + document_boundary fixes.

Covers:
- collapsed boundary detection (boundary_page_end ≪ pages_total) drops
  confidence below 1.0 and raises needs_review,
- reference / bibliography authors are stripped from the document author list,
- equation-label discontinuity, missing terminal section, and low page coverage
  are surfaced in the completeness report,
- a complete document produces no false positives,
- export_validation.json carries the aggregated completeness report and is not
  promoted to publish_ready while a document is incomplete.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "api"))


def _import_artifacts():
    from routes import export_artifacts  # type: ignore
    return export_artifacts


def _import_export_module():
    sys.path.insert(0, str(ROOT / "backend" / "tests"))
    from test_export_bundle import _import_export_module as _impl  # type: ignore
    return _impl()


# ---------------------------------------------------------------------------
# Fixtures — a complete and a truncated structure artifact
# ---------------------------------------------------------------------------

def _eq_block(block_id: str, page: int, label: str | None) -> dict:
    return {
        "block_id": block_id,
        "page": page,
        "text": "equation",
        "block_type": "equation_block",
        "section_id": "sec_body",
        "equation_label": label,
    }


def _heading(block_id: str, page: int, text: str) -> dict:
    return {
        "block_id": block_id,
        "page": page,
        "text": text,
        "block_type": "section_heading",
        "section_id": block_id,
    }


def _reference(block_id: str, page: int, text: str) -> dict:
    return {
        "block_id": block_id,
        "page": page,
        "text": text,
        "block_type": "reference_entry",
        "section_id": "sec_refs",
    }


def _complete_structure() -> dict:
    """A 6-page article: contiguous equations, a Conclusion, broad page coverage."""
    blocks = [_heading("h_intro", 1, "1 Introduction")]
    # Contiguous equations (2.1)..(2.4) and (3.1)..(3.3) spread across pages.
    blocks += [_eq_block("eq_2_1", 2, "(2.1)"), _eq_block("eq_2_2", 2, "(2.2)")]
    blocks += [_eq_block("eq_2_3", 3, "(2.3)"), _eq_block("eq_2_4", 3, "(2.4)")]
    blocks += [_eq_block("eq_3_1", 4, "(3.1)"), _eq_block("eq_3_2", 4, "(3.2)")]
    blocks += [_eq_block("eq_3_3", 5, "(3.3)")]
    blocks += [_heading("h_concl", 6, "5 Conclusion")]
    return {
        "blocks": blocks,
        "sections": [
            {"section_id": "h_intro", "title": "1 Introduction", "level": 1,
             "order": 0, "page_start": 1, "page_end": 5},
            {"section_id": "h_concl", "title": "5 Conclusion", "level": 1,
             "order": 1, "page_start": 6, "page_end": 6},
        ],
        "metadata": {"title": "Complete paper", "authors": ["Alice", "Bob"], "pages": 6},
    }


def _truncated_structure() -> dict:
    """Mirrors issue #366: 20-page paper truncated, all blocks on pages 1 & 3.

    Equations stop at (3.36) with an internal gap, no Conclusion section, and the
    document authors are contaminated with bibliography authors.
    """
    blocks = [_heading("h_intro", 1, "1 Introduction")]
    # (3.1)..(3.36) but (3.5) is missing → discontinuity.
    for minor in list(range(1, 5)) + list(range(6, 37)):
        page = 1 if minor < 18 else 3
        blocks.append(_eq_block(f"eq_3_{minor}", page, f"(3.{minor})"))
    # Bibliography authors leaked into the author list.
    blocks += [
        _reference("ref_1", 3, "Horndeski, G. W. Second-order scalar-tensor field equations."),
        _reference("ref_2", 3, "Lesgourgues, J. The Cosmic Linear Anisotropy Solving System."),
        _reference("ref_3", 3, "Deffayet, C. and Steer, D. A formal introduction to Horndeski."),
    ]
    real_authors = ["Taro Yamada", "Hanako Suzuki", "Jiro Tanaka", "Saburo Kato"]
    leaked = ["Horndeski", "Lesgourgues", "Deffayet", "Steer"]
    return {
        "blocks": blocks,
        "sections": [
            {"section_id": "h_intro", "title": "1 Introduction", "level": 1,
             "order": 0, "page_start": 1, "page_end": 1},
        ],
        "metadata": {
            "title": "Kurtosis consistency relation",
            "authors": real_authors + leaked,
            "pages": 20,
        },
    }


# ---------------------------------------------------------------------------
# build_document_completeness
# ---------------------------------------------------------------------------

class TestCompleteness:
    def test_complete_structure_has_no_false_positive(self):
        ea = _import_artifacts()
        rep = ea.build_document_completeness(_complete_structure(), document_id="doc_ok")
        assert rep["complete"] is True
        assert rep["review_reasons"] == []
        assert rep["equation_label_continuity"]["has_gaps"] is False
        assert rep["equation_label_continuity"]["missing_labels"] == []
        assert rep["terminal_section"]["present"] is True
        assert rep["page_coverage"]["sufficient"] is True

    def test_equation_label_gap_detected(self):
        ea = _import_artifacts()
        rep = ea.build_document_completeness(_truncated_structure(), document_id="doc_bad")
        assert rep["equation_label_continuity"]["has_gaps"] is True
        assert "(3.5)" in rep["equation_label_continuity"]["missing_labels"]
        assert "equation_label_discontinuity" in rep["review_reasons"]

    def test_missing_terminal_section_detected(self):
        ea = _import_artifacts()
        rep = ea.build_document_completeness(_truncated_structure(), document_id="doc_bad")
        assert rep["terminal_section"]["present"] is False
        assert "terminal_section_missing" in rep["review_reasons"]

    def test_low_page_coverage_detected(self):
        ea = _import_artifacts()
        rep = ea.build_document_completeness(_truncated_structure(), document_id="doc_bad")
        cov = rep["page_coverage"]
        assert cov["pages_total"] == 20
        assert cov["distinct_page_count"] < 20
        assert cov["sufficient"] is False
        assert "page_coverage_insufficient" in rep["review_reasons"]
        assert rep["complete"] is False


# ---------------------------------------------------------------------------
# build_document_boundary
# ---------------------------------------------------------------------------

class TestBoundaryFixes:
    def test_collapsed_boundary_flags_review_and_lowers_confidence(self):
        ea = _import_artifacts()
        b = ea.build_document_boundary(_truncated_structure(), document_id="doc_bad")
        assert b["pages_total"] == 20
        assert b["boundary_page_end"] == 1
        assert b["confidence"] < 1.0
        assert b["needs_review"] is True
        assert "boundary_page_span_too_small" in b["review_reason"]

    def test_reference_authors_filtered_out(self):
        ea = _import_artifacts()
        b = ea.build_document_boundary(_truncated_structure(), document_id="doc_bad")
        assert b["authors"] == ["Taro Yamada", "Hanako Suzuki", "Jiro Tanaka", "Saburo Kato"]
        assert "Horndeski" not in b["authors"]
        assert "reference_authors_in_author_list" in b["review_reason"]

    def test_equation_gap_propagates_to_boundary_review(self):
        ea = _import_artifacts()
        b = ea.build_document_boundary(_truncated_structure(), document_id="doc_bad")
        assert "equation_label_discontinuity" in b["review_reason"]

    def test_complete_boundary_stays_confident(self):
        ea = _import_artifacts()
        b = ea.build_document_boundary(_complete_structure(), document_id="doc_ok")
        assert b["confidence"] == 1.0
        assert b["needs_review"] is False
        assert b["review_reason"] == []
        assert b["authors"] == ["Alice", "Bob"]


# ---------------------------------------------------------------------------
# export_validation.json completeness section
# ---------------------------------------------------------------------------

class TestExportValidationCompleteness:
    def _validate(self, completeness_reports):
        mod = _import_export_module()
        return mod._validate_export_references(
            claims=[],
            equations=[],
            components=[],
            component_graph={"edges": []},
            course_info=None,
            evidence_snippets=[],
            derivation_chains=[],
            completeness_reports=completeness_reports,
        )

    def test_export_validation_has_completeness_section(self):
        ea = _import_artifacts()
        rep = ea.build_document_completeness(_complete_structure(), document_id="doc_ok")
        ev = self._validate([rep])
        assert "completeness" in ev
        assert ev["completeness"]["checked"] is True
        assert ev["completeness"]["all_documents_complete"] is True
        assert ev["publish_ready"] is True

    def test_incomplete_document_blocks_publish_ready(self):
        ea = _import_artifacts()
        rep = ea.build_document_completeness(_truncated_structure(), document_id="doc_bad")
        ev = self._validate([rep])
        assert ev["completeness"]["all_documents_complete"] is False
        assert ev["publish_ready"] is False
        codes = {w["code"] for w in ev["warnings"]}
        assert "DOCUMENT_EQUATION_LABEL_DISCONTINUITY" in codes
        assert "DOCUMENT_TERMINAL_SECTION_MISSING" in codes
        assert "DOCUMENT_PAGE_COVERAGE_INSUFFICIENT" in codes
        # The missing equation labels are reported for operators.
        doc = ev["completeness"]["documents"][0]
        assert "(3.5)" in doc["missing_equation_labels"]
        assert doc["pages_total"] == 20

    def test_complete_document_emits_no_completeness_warning(self):
        ea = _import_artifacts()
        rep = ea.build_document_completeness(_complete_structure(), document_id="doc_ok")
        ev = self._validate([rep])
        completeness_codes = {
            w["code"] for w in ev["warnings"]
            if w["code"].startswith("DOCUMENT_")
        }
        assert completeness_codes == set()
