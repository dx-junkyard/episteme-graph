"""Tests for issues #366 / #371: document completeness gate + document_boundary.

Covers:
- collapsed boundary detection (boundary_page_end ≪ pages_total) drops
  confidence below 1.0 and raises needs_review,
- bibliography authors leaked into the author list are stripped, while a
  self-citing real author is preserved,
- equation-label discontinuity (internal gaps AND tail truncation detected via
  prose cross-references) and missing terminal section are surfaced and
  propagate to the boundary needs_review,
- ingest reachability (issue #371): the pass/fail signal is whether the
  DocumentStructure ingest reached the document end (trailing un-ingested
  ranges), NOT what fraction of pages carry content,
- EvidenceRegistry page distribution is audit-only: a complete document with
  evidence clustered on a few pages is NOT flagged as incomplete (issue #371),
- a complete document produces no false positives,
- export_validation.json carries the aggregated completeness report and is not
  promoted to publish_ready while a document's ingest is truncated.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "api"))


def _import_artifacts():
    from routes import export_artifacts  # type: ignore
    return export_artifacts


def _import_completeness_mod():
    # Load the dependency-free leaf module directly so the test does not require
    # the full pipeline package (orchestrator / agents) to be importable.
    import importlib.util

    path = ROOT / "backend" / "core" / "document_pipeline" / "completeness.py"
    spec = importlib.util.spec_from_file_location("_dp_completeness_test", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _import_completeness():
    return _import_completeness_mod().analyze_document_completeness


def _import_export_module():
    sys.path.insert(0, str(ROOT / "backend" / "tests"))
    from test_export_bundle import _import_export_module as _impl  # type: ignore
    return _impl()


def _import_gate():
    # Load the gate standalone (no heavy package __init__); register in
    # sys.modules so its dataclasses resolve their annotations.
    import importlib.util

    path = ROOT / "backend" / "core" / "document_pipeline" / "export_validation_gate.py"
    spec = importlib.util.spec_from_file_location("export_validation_gate", str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["export_validation_gate"] = mod
    spec.loader.exec_module(mod)
    return mod


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


def _para(block_id: str, page: int, text: str) -> dict:
    return {"block_id": block_id, "page": page, "text": text,
            "block_type": "body_paragraph", "section_id": "sec_body"}


def _heading(block_id: str, page: int, text: str) -> dict:
    return {"block_id": block_id, "page": page, "text": text,
            "block_type": "section_heading", "section_id": block_id}


def _reference(block_id: str, page: int, text: str) -> dict:
    return {"block_id": block_id, "page": page, "text": text,
            "block_type": "reference_entry", "section_id": "sec_refs"}


def _complete_structure() -> dict:
    """A 6-page article: contiguous equations, a Conclusion, broad page coverage."""
    blocks = [_heading("h_intro", 1, "1 Introduction")]
    blocks += [_para(f"p_{p}", p, "body text") for p in range(1, 7)]
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
        "metadata": {
            "title": "Complete paper",
            "authors": ["Alice", "Bob"],
            "pages": 6,
            "author_extraction": {"source": "grobid_tei", "confidence": 0.95,
                                  "needs_review": False, "review_reasons": []},
        },
    }


def _truncated_structure() -> dict:
    """Mirrors issue #366: a 20-page paper truncated at (3.36).

    Equations (3.1)…(3.36) are ingested *contiguously* (no artificial internal
    gap). The surviving introduction forward-references the kurtosis consistency
    relations (3.38)–(3.40), which were never ingested — that is how the tail
    truncation is detected. There is no Conclusion section and the author list is
    contaminated with bibliography authors.
    """
    blocks = [_heading("h_intro", 1, "1 Introduction")]
    blocks.append(_para(
        "p_intro", 1,
        "We derive the skewness relation (3.38) and the kurtosis consistency "
        "relations (3.39) and (3.40) below.",
    ))
    # Contiguous (3.1)..(3.36), all on pages 1 and 3 (truncated ingest).
    for minor in range(1, 37):
        page = 1 if minor < 18 else 3
        blocks.append(_eq_block(f"eq_3_{minor}", page, f"(3.{minor})"))
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


def _full_20page_structure() -> dict:
    """A complete 20-page article: content blocks on every page, a Conclusion.

    Used to verify (issue #371) that a fully-ingested document with evidence
    clustered on only a few pages is NOT flagged as incomplete.
    """
    blocks = [_heading("h_intro", 1, "1 Introduction")]
    blocks += [_para(f"p_{p}", p, "body text") for p in range(1, 21)]
    blocks += [
        _eq_block("eq_2_1", 2, "(2.1)"), _eq_block("eq_2_2", 3, "(2.2)"),
        _eq_block("eq_2_3", 4, "(2.3)"), _eq_block("eq_2_4", 5, "(2.4)"),
    ]
    blocks += [_heading("h_concl", 20, "5 Conclusion")]
    return {
        "blocks": blocks,
        "sections": [
            {"section_id": "h_intro", "title": "1 Introduction", "level": 1,
             "order": 0, "page_start": 1, "page_end": 19},
            {"section_id": "h_concl", "title": "5 Conclusion", "level": 1,
             "order": 1, "page_start": 20, "page_end": 20},
        ],
        "metadata": {"title": "Full paper", "authors": ["Alice"], "pages": 20},
    }


def _short_ingest_structure(pages_total: int = 20) -> dict:
    """Content blocks only on pages 1 and 3 of a `pages_total`-page document.

    Mirrors issue #366/#371: the body never reaches the document end, so the
    ingest is truncated regardless of the EvidenceRegistry distribution.
    """
    blocks = [_heading("h_intro", 1, "1 Introduction")]
    blocks += [_para("p_1", 1, "body text on page 1")]
    blocks += [_para("p_3", 3, "body text on page 3")]
    blocks += [_eq_block(f"eq_1_{i}", 1, f"(1.{i})") for i in range(1, 6)]
    blocks += [_eq_block(f"eq_1_{i}", 3, f"(1.{i})") for i in range(6, 11)]
    return {
        "blocks": blocks,
        "sections": [
            {"section_id": "h_intro", "title": "1 Introduction", "level": 1,
             "order": 0, "page_start": 1, "page_end": 3},
        ],
        "metadata": {"title": "Truncated ingest", "authors": ["Alice"], "pages": pages_total},
    }


def _evidence(pages: list[int]) -> dict:
    return {
        "document_id": "doc",
        "records": [
            {"evidence_id": f"ev_{i}", "source": {"page": p, "block_id": f"b_{i}"},
             "evidence_text": "x"}
            for i, p in enumerate(pages)
        ],
    }


def _complete_eq_sequence() -> dict:
    """(3.1)…(3.36) contiguous, a Conclusion, document end reached (issue #373).

    No prose reference to a missing equation and no terminal anomaly — the lone
    fact that the run ends at (3.36) must not invent (3.37) nor suspect a tail
    truncation.
    """
    blocks = [_heading("h_intro", 1, "1 Introduction")]
    blocks += [_para(f"p_{p}", p, "body text") for p in range(1, 7)]
    blocks += [_eq_block(f"eq_3_{i}", (i % 6) + 1, f"(3.{i})") for i in range(1, 37)]
    blocks += [_heading("h_concl", 6, "5 Conclusion")]
    return {
        "blocks": blocks,
        "sections": [
            {"section_id": "h_intro", "title": "1 Introduction", "level": 1,
             "order": 0, "page_start": 1, "page_end": 5},
            {"section_id": "h_concl", "title": "5 Conclusion", "level": 1,
             "order": 1, "page_start": 6, "page_end": 6},
        ],
        "metadata": {"title": "Complete", "authors": ["Alice"], "pages": 6},
    }


def _tail_cut_no_reference() -> dict:
    """(3.1)…(3.36) contiguous, NO Conclusion, document end not reached, and no
    prose reference to a later equation (issue #373).

    Confirmed missing labels must stay empty while a tail truncation is
    suspected from the combination of deterministic signals.
    """
    blocks = [_heading("h_intro", 1, "1 Introduction")]
    blocks += [_para("p_1", 1, "body"), _para("p_2", 2, "body"), _para("p_3", 3, "body")]
    blocks += [_eq_block(f"eq_3_{i}", 1 if i < 18 else 3, f"(3.{i})") for i in range(1, 37)]
    return {
        "blocks": blocks,
        "sections": [
            {"section_id": "h_intro", "title": "1 Introduction", "level": 1,
             "order": 0, "page_start": 1, "page_end": 3},
        ],
        "metadata": {"title": "Truncated", "authors": ["Alice"], "pages": 20},
    }


# ---------------------------------------------------------------------------
# analyze_document_completeness (core)
# ---------------------------------------------------------------------------

class TestCompleteness:
    def test_complete_structure_has_no_false_positive(self):
        analyze = _import_completeness()
        rep = analyze(_complete_structure(), None, document_id="doc_ok")
        assert rep["complete"] is True
        assert rep["review_reasons"] == []
        assert rep["equation_label_continuity"]["has_gaps"] is False
        assert rep["terminal_section"]["present"] is True
        assert rep["ingest_coverage"]["sufficient"] is True
        assert rep["ingest_coverage"]["reached_document_end"] is True

    def test_tail_truncation_detected_via_cross_reference(self):
        analyze = _import_completeness()
        rep = analyze(_truncated_structure(), None, document_id="doc_bad")
        cont = rep["equation_label_continuity"]
        # No internal gap exists; the gap is the un-ingested tail (3.37)..(3.40).
        assert cont["has_gaps"] is True
        for label in ("(3.38)", "(3.39)", "(3.40)"):
            assert label in cont["missing_labels"]
            assert label in cont["referenced_missing_labels"]
        assert "equation_label_discontinuity" in rep["review_reasons"]

    def test_internal_gap_still_detected(self):
        analyze = _import_completeness()
        structure = _complete_structure()
        # Drop (2.3) → internal gap.
        structure["blocks"] = [b for b in structure["blocks"] if b.get("block_id") != "eq_2_3"]
        rep = analyze(structure, None, document_id="doc_gap")
        assert "(2.3)" in rep["equation_label_continuity"]["missing_labels"]

    def test_missing_terminal_section_detected(self):
        analyze = _import_completeness()
        rep = analyze(_truncated_structure(), None, document_id="doc_bad")
        assert rep["terminal_section"]["present"] is False
        assert "terminal_section_missing" in rep["review_reasons"]

    # --- ingest reachability (issue #371) --------------------------------

    def test_full_content_with_sparse_evidence_is_complete(self):
        # 20 pages all carry content blocks; evidence only on pages 1, 10, 20.
        # The ingest reached the document end, so this is NOT incomplete, and
        # the evidence sparseness alone must not flag page coverage (issue #371).
        analyze = _import_completeness()
        rep = analyze(_full_20page_structure(), _evidence([1, 10, 20]), document_id="doc_full")
        ingest = rep["ingest_coverage"]
        assert ingest["sufficient"] is True
        assert ingest["reached_document_end"] is True
        assert ingest["last_content_page"] == 20
        assert ingest["trailing_uningested_page_ranges"] == []
        assert rep["complete"] is True
        assert "ingest_incomplete" not in rep["review_reasons"]

    def test_sparse_evidence_is_audit_only_not_a_review_reason(self):
        analyze = _import_completeness()
        rep = analyze(_full_20page_structure(), _evidence([1, 10, 20]), document_id="doc_full")
        dist = rep["evidence_page_distribution"]
        assert dist["pages"] == [1, 10, 20]
        assert dist["distribution_ratio"] == 0.15
        assert dist["sparse"] is True
        # Audit-only: sparseness never enters review_reasons / complete.
        assert rep["review_reasons"] == []
        assert rep["complete"] is True

    def test_truncated_ingest_detected_via_trailing_range(self):
        # Content blocks only on pages 1 and 3 of 20 → the ingest never reached
        # the document end (issue #366/#371). Detected even when evidence is None.
        analyze = _import_completeness()
        rep = analyze(_short_ingest_structure(20), None, document_id="doc_short")
        ingest = rep["ingest_coverage"]
        assert ingest["reached_document_end"] is False
        assert ingest["sufficient"] is False
        assert ingest["last_ingested_page"] == 3
        assert [4, 20] in ingest["trailing_uningested_page_ranges"]
        assert rep["complete"] is False
        assert "ingest_incomplete" in rep["review_reasons"]

    def test_blank_middle_page_is_not_a_false_positive(self):
        # A blank page 10 in an otherwise fully-ingested 20-page document must
        # not be reported as truncated (issue #371).
        analyze = _import_completeness()
        structure = _full_20page_structure()
        structure["blocks"] = [b for b in structure["blocks"] if b.get("page") != 10]
        rep = analyze(structure, None, document_id="doc_blank")
        ingest = rep["ingest_coverage"]
        assert ingest["reached_document_end"] is True
        assert ingest["sufficient"] is True
        assert ingest["trailing_uningested_page_ranges"] == []
        assert "ingest_incomplete" not in rep["review_reasons"]

    def test_references_only_last_page_counts_as_reached(self):
        # Body ends on page 18; only References blocks sit on pages 19-20.
        # reference_entry is not a "content block" but still proves the parser
        # reached the document end (issue #371).
        analyze = _import_completeness()
        structure = _full_20page_structure()
        structure["blocks"] = [b for b in structure["blocks"] if b.get("page") not in (19, 20)]
        structure["blocks"] += [
            _reference("ref_1", 19, "Author, A. A prior result. 2019."),
            _reference("ref_2", 20, "Author, B. Another result. 2020."),
        ]
        rep = analyze(structure, None, document_id="doc_refs")
        ingest = rep["ingest_coverage"]
        assert ingest["last_content_page"] == 18
        assert ingest["last_ingested_page"] == 20
        assert ingest["reached_document_end"] is True
        assert ingest["sufficient"] is True

    def test_parser_pages_processed_signals_reached_end(self):
        # Content sits only on pages 1-3, but the parser recorded that it
        # processed all 20 pages: the rest are genuinely blank, not truncated.
        analyze = _import_completeness()
        structure = _short_ingest_structure(20)
        structure["metadata"]["parser_pages_processed"] = 20
        rep = analyze(structure, None, document_id="doc_parser")
        ingest = rep["ingest_coverage"]
        assert ingest["parser_pages_processed"] == 20
        assert ingest["reached_document_end"] is True
        assert ingest["sufficient"] is True
        assert "ingest_incomplete" not in rep["review_reasons"]

    @pytest.mark.parametrize("last_page", [15, 16, 17, 19])
    def test_fractional_reach_never_proves_document_end(self, last_page):
        analyze = _import_completeness()
        structure = _full_20page_structure()
        structure["blocks"] = [
            block for block in structure["blocks"]
            if block.get("page", 0) <= last_page
        ]
        structure["sections"] = [
            section for section in structure["sections"]
            if section.get("page_start", 0) <= last_page
        ]
        rep = analyze(structure, None, document_id=f"doc_{last_page}")
        ingest = rep["ingest_coverage"]
        assert ingest["reached_document_end"] is False
        assert ingest["sufficient"] is False
        assert ingest["trailing_uningested_page_ranges"] == [
            [last_page + 1, 20]
        ]
        assert "ingest_incomplete" in rep["review_reasons"]

    def test_parser_eof_allows_blank_trailing_pages(self):
        analyze = _import_completeness()
        structure = _short_ingest_structure(20)
        structure["metadata"]["parser_pages_processed"] = 20
        structure["metadata"]["parser_reached_eof"] = True
        rep = analyze(structure, None, document_id="doc_blank_tail")
        assert rep["ingest_coverage"]["reached_document_end"] is True
        assert rep["ingest_coverage"]["trailing_uningested_page_ranges"] == []


# ---------------------------------------------------------------------------
# Equation continuity separation + tail-truncation suspicion (issue #373)
# ---------------------------------------------------------------------------

class TestEquationContinuitySeparation:
    def test_unreferenced_tail_not_added_to_missing_labels(self):
        analyze = _import_completeness()
        rep = analyze(_complete_eq_sequence(), None, document_id="doc_seq")
        eq = rep["equation_label_continuity"]
        # The run ends at (3.36): (3.37) is neither an internal gap nor referenced.
        assert eq["missing_labels"] == []
        assert eq["internal_gaps"] == []
        assert "(3.37)" not in eq["missing_labels"]
        assert rep["tail_truncation"]["suspected"] is False

    def test_referenced_missing_are_confirmed_not_speculative(self):
        analyze = _import_completeness()
        rep = analyze(_truncated_structure(), None, document_id="doc_bad")
        eq = rep["equation_label_continuity"]
        assert eq["referenced_missing_labels"] == ["(3.38)", "(3.39)", "(3.40)"]
        assert eq["missing_labels"] == ["(3.38)", "(3.39)", "(3.40)"]
        assert eq["internal_gaps"] == []
        # (3.37) is never invented even though the tail is truncated.
        assert "(3.37)" not in eq["missing_labels"]

    def test_internal_gap_is_reported_separately(self):
        analyze = _import_completeness()
        structure = _complete_eq_sequence()
        # Drop (3.4) → an internal gap inside the observed run.
        structure["blocks"] = [b for b in structure["blocks"] if b.get("block_id") != "eq_3_4"]
        rep = analyze(structure, None, document_id="doc_gap")
        eq = rep["equation_label_continuity"]
        assert "(3.4)" in eq["internal_gaps"]
        assert "(3.4)" in eq["missing_labels"]

    def test_tail_truncation_suspected_without_confirmed_missing(self):
        analyze = _import_completeness()
        rep = analyze(_tail_cut_no_reference(), None, document_id="doc_cut")
        tail = rep["tail_truncation"]
        assert tail["suspected"] is True
        assert "document_end_not_reached" in tail["signals"]
        assert "terminal_section_missing" in tail["signals"]
        assert tail["confidence"] >= 0.5
        # No confirmed missing labels are invented.
        assert rep["equation_label_continuity"]["missing_labels"] == []
        assert "tail_truncation_suspected" in rep["review_reasons"]

    def test_complete_document_has_no_tail_truncation(self):
        analyze = _import_completeness()
        rep = analyze(_complete_structure(), None, document_id="doc_ok")
        assert rep["tail_truncation"]["suspected"] is False
        assert rep["tail_truncation"]["signals"] == []
        assert rep["tail_truncation"]["confidence"] == 0.0

    def test_tex_unclosed_align_raises_environment_signal(self):
        analyze = _import_completeness()
        tex = r"\begin{align} a &= b \\ c &= d "  # never closed → truncated mid-align
        rep = analyze(_tail_cut_no_reference(), None, document_id="doc_tex", tex_source=tex)
        tail = rep["tail_truncation"]
        assert "equation_environment_cut_at_boundary" in tail["signals"]
        assert tail["unclosed_math_environments"] == ["align"]
        assert tail["suspected"] is True

    def test_single_signal_does_not_conclude_truncation(self):
        # Terminal section missing alone (one signal) must not conclude tail
        # truncation (issue #373: never conclude from a single signal).
        analyze = _import_completeness()
        structure = _full_20page_structure()
        # Remove the Conclusion only; the document still reaches its end.
        structure["blocks"] = [b for b in structure["blocks"] if b.get("block_id") != "h_concl"]
        structure["sections"] = [s for s in structure["sections"] if s["section_id"] != "h_concl"]
        rep = analyze(structure, None, document_id="doc_one")
        tail = rep["tail_truncation"]
        assert tail["signals"] == ["terminal_section_missing"]
        assert tail["suspected"] is False


class TestTexEnvironmentDetection:
    def test_complete_align_is_balanced(self):
        mod = _import_completeness_mod()
        assert mod.detect_unclosed_math_environments(
            r"\begin{align} a &= b \end{align}"
        ) == []

    def test_unclosed_align_detected(self):
        mod = _import_completeness_mod()
        assert mod.detect_unclosed_math_environments(
            r"\begin{align} a &= b \\ c &= d"
        ) == ["align"]

    def test_unclosed_multline_detected(self):
        mod = _import_completeness_mod()
        assert mod.detect_unclosed_math_environments(
            r"\begin{multline} x + y + z"
        ) == ["multline"]

    def test_unbalanced_prose_environment_ignored(self):
        # Only math environments count; an unbalanced itemize is not truncation.
        mod = _import_completeness_mod()
        assert mod.detect_unclosed_math_environments(
            r"\begin{itemize}\item a \begin{equation} x=y \end{equation}"
        ) == []

    def test_reversed_end_then_begin_is_detected(self):
        mod = _import_completeness_mod()
        assert mod.detect_unclosed_math_environments(
            r"\end{align}\begin{align}"
        ) == ["align"]

    def test_commented_environment_is_ignored(self):
        mod = _import_completeness_mod()
        assert mod.detect_unclosed_math_environments(
            "% \\\\begin{align}\nplain text"
        ) == []

    def test_precomputed_tex_signal_is_used_by_pipeline_shape(self):
        analyze = _import_completeness()
        structure = _tail_cut_no_reference()
        structure["metadata"]["unclosed_math_environments"] = ["align"]
        rep = analyze(structure, None, document_id="doc_precomputed")
        assert rep["tail_truncation"]["unclosed_math_environments"] == ["align"]
        assert "equation_environment_cut_at_boundary" in rep["tail_truncation"]["signals"]

    def test_no_tex_returns_empty(self):
        mod = _import_completeness_mod()
        assert mod.detect_unclosed_math_environments(None) == []
        assert mod.detect_unclosed_math_environments("") == []


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

    def test_export_never_deletes_authors(self):
        # Issue #372: the export layer no longer token-deletes "reference"
        # authors. A contaminated list (no structured provenance) is preserved
        # verbatim and flagged for review instead of silently corrected.
        ea = _import_artifacts()
        b = ea.build_document_boundary(_truncated_structure(), document_id="doc_bad")
        assert b["authors"] == [
            "Taro Yamada", "Hanako Suzuki", "Jiro Tanaka", "Saburo Kato",
            "Horndeski", "Lesgourgues", "Deffayet", "Steer",
        ]
        assert b["needs_review"] is True
        assert "author_provenance_missing" in b["review_reason"]

    def test_self_citing_author_is_preserved(self):
        ea = _import_artifacts()
        structure = {
            "blocks": [
                _para("byline", 1, "Alice Smith"),
                _reference("r1", 9, "Alice Smith. A prior result. 2019."),
                _reference("r2", 9, "Bob Other. Unrelated work. 2020."),
            ],
            "sections": [{"section_id": "s", "title": "Intro", "level": 1, "order": 0,
                          "page_start": 1, "page_end": 9}],
            "metadata": {
                "title": "T", "authors": ["Alice Smith"], "pages": 9,
                "author_extraction": {"source": "pdf_front_matter", "confidence": 0.6,
                                      "needs_review": False, "review_reasons": []},
            },
        }
        b = ea.build_document_boundary(structure, document_id="doc_self")
        assert b["authors"] == ["Alice Smith"]
        assert "author_provenance_missing" not in b["review_reason"]

    def test_author_count_over_limit_preserves_values(self):
        # An anomalously large author list is preserved but flagged (issue #372).
        ea = _import_artifacts()
        many = [f"Author{i:03d}" for i in range(40)]
        structure = {
            "blocks": [],
            "sections": [{"section_id": "s", "title": "Intro", "level": 1, "order": 0,
                          "page_start": 1, "page_end": 9}],
            "metadata": {
                "title": "T", "authors": list(many), "pages": 9,
                "author_extraction": {"source": "grobid_tei", "confidence": 0.95,
                                      "needs_review": False, "review_reasons": []},
            },
        }
        b = ea.build_document_boundary(structure, document_id="doc_many")
        assert b["authors"] == many  # nothing deleted
        assert b["needs_review"] is True
        assert "author_count_exceeds_limit" in b["review_reason"]

    def test_author_provenance_and_confidence_surfaced(self):
        # provenance + confidence are reflected in document_boundary.json (#372).
        ea = _import_artifacts()
        b = ea.build_document_boundary(_complete_structure(), document_id="doc_ok")
        assert b["author_extraction"]["source"] == "grobid_tei"
        assert b["author_extraction"]["confidence"] == 0.95
        assert b["author_extraction"]["needs_review"] is False

    def test_all_completeness_failures_propagate_to_boundary(self):
        ea = _import_artifacts()
        # A doc that is complete on boundary span but missing a terminal section.
        structure = _complete_structure()
        structure["blocks"] = [b for b in structure["blocks"] if b.get("block_id") != "h_concl"]
        structure["sections"] = [s for s in structure["sections"] if s["section_id"] != "h_concl"]
        b = ea.build_document_boundary(structure, document_id="doc_noconcl")
        assert b["completeness"]["complete"] is False
        assert "terminal_section_missing" in b["review_reason"]
        assert b["needs_review"] is True
        assert b["confidence"] < 1.0

    def test_complete_boundary_stays_confident(self):
        ea = _import_artifacts()
        b = ea.build_document_boundary(_complete_structure(), document_id="doc_ok")
        assert b["confidence"] == 1.0
        assert b["needs_review"] is False
        assert b["review_reason"] == []
        assert b["authors"] == ["Alice", "Bob"]

    def test_tail_truncation_propagates_to_boundary(self):
        # Issue #373: a suspected tail truncation flags the boundary for review.
        ea = _import_artifacts()
        b = ea.build_document_boundary(_tail_cut_no_reference(), document_id="doc_cut")
        assert b["completeness"]["tail_truncation"]["suspected"] is True
        assert "tail_truncation_suspected" in b["review_reason"]
        assert b["needs_review"] is True


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
        analyze = _import_completeness()
        rep = analyze(_complete_structure(), None, document_id="doc_ok")
        ev = self._validate([rep])
        assert ev["completeness"]["checked"] is True
        assert ev["completeness"]["all_documents_complete"] is True
        assert ev["publish_ready"] is True

    def test_incomplete_document_blocks_publish_ready(self):
        analyze = _import_completeness()
        rep = analyze(_truncated_structure(), _evidence([1, 3]), document_id="doc_bad")
        ev = self._validate([rep])
        assert ev["completeness"]["all_documents_complete"] is False
        assert ev["publish_ready"] is False
        codes = {w["code"] for w in ev["warnings"]}
        assert "DOCUMENT_EQUATION_LABEL_DISCONTINUITY" in codes
        assert "DOCUMENT_TERMINAL_SECTION_MISSING" in codes
        assert "DOCUMENT_INGEST_INCOMPLETE" in codes
        doc = ev["completeness"]["documents"][0]
        assert "(3.39)" in doc["missing_equation_labels"]
        assert doc["pages_total"] == 20
        assert doc["reached_document_end"] is False
        assert doc["trailing_uningested_page_ranges"]

    def test_complete_document_emits_no_completeness_warning(self):
        analyze = _import_completeness()
        rep = analyze(_complete_structure(), None, document_id="doc_ok")
        ev = self._validate([rep])
        completeness_codes = {
            w["code"] for w in ev["warnings"] if w["code"].startswith("DOCUMENT_")
        }
        assert completeness_codes == set()

    def test_tail_truncation_blocks_publish_ready(self):
        # Issue #373: a suspected tail truncation (no confirmed missing label)
        # emits DOCUMENT_TAIL_TRUNCATION_SUSPECTED and blocks publish_ready.
        analyze = _import_completeness()
        rep = analyze(_tail_cut_no_reference(), None, document_id="doc_cut")
        ev = self._validate([rep])
        codes = {w["code"] for w in ev["warnings"]}
        assert "DOCUMENT_TAIL_TRUNCATION_SUSPECTED" in codes
        assert ev["publish_ready"] is False
        doc = ev["completeness"]["documents"][0]
        assert doc["tail_truncation_suspected"] is True
        # Confirmed missing labels stay empty — (3.37) is not invented.
        assert doc["missing_equation_labels"] == []

    def test_confirmed_missing_and_tail_truncation_both_reported(self):
        # #366 fixture: (3.38)-(3.40) referenced → confirmed discontinuity AND a
        # suspected tail truncation, as distinct warning codes.
        analyze = _import_completeness()
        rep = analyze(_truncated_structure(), _evidence([1, 3]), document_id="doc_bad")
        ev = self._validate([rep])
        codes = {w["code"] for w in ev["warnings"]}
        assert "DOCUMENT_EQUATION_LABEL_DISCONTINUITY" in codes
        assert "DOCUMENT_TAIL_TRUNCATION_SUSPECTED" in codes
        doc = ev["completeness"]["documents"][0]
        assert "(3.39)" in doc["referenced_missing_labels"]
        assert "(3.37)" not in doc["missing_equation_labels"]

    def test_full_document_with_sparse_evidence_stays_publish_ready(self):
        # Issue #371: a fully-ingested 20-page document with evidence on only
        # pages 1/10/20 must NOT be blocked from publish_ready, and must emit no
        # DOCUMENT_* completeness warning, despite the sparse evidence.
        analyze = _import_completeness()
        rep = analyze(_full_20page_structure(), _evidence([1, 10, 20]), document_id="doc_full")
        ev = self._validate([rep])
        assert ev["completeness"]["all_documents_complete"] is True
        assert ev["publish_ready"] is True
        completeness_codes = {
            w["code"] for w in ev["warnings"] if w["code"].startswith("DOCUMENT_")
        }
        assert completeness_codes == set()
        doc = ev["completeness"]["documents"][0]
        assert doc["evidence_sparse"] is True
        assert doc["ingest_sufficient"] is True


# ---------------------------------------------------------------------------
# Pipeline ExportValidationGate runs the completeness check at the stage exit
# ---------------------------------------------------------------------------

class TestPipelineGateCompleteness:
    def _struct_dict(self, structure: dict, document_id: str) -> dict:
        d = dict(structure)
        d["document_id"] = document_id
        d.setdefault("validation_issues", [])
        return d

    def test_gate_flags_incomplete_document(self):
        mod = _import_gate()
        artifacts = {
            "document_structure": self._struct_dict(_truncated_structure(), "doc_bad"),
            "evidence_registry": _evidence([1, 3]),
        }
        res = mod.ExportValidationGate().run(artifacts=artifacts).to_dict()
        dc = res["document_completeness"]
        assert dc["checked"] is True
        assert dc["all_documents_complete"] is False
        assert res["publish_ready"] is False
        codes = {w["code"] for w in res["warnings"]}
        assert "DOCUMENT_EQUATION_LABEL_DISCONTINUITY" in codes
        assert "DOCUMENT_TERMINAL_SECTION_MISSING" in codes
        assert "DOCUMENT_INGEST_INCOMPLETE" in codes

    def test_gate_passes_complete_document(self):
        mod = _import_gate()
        artifacts = {
            "document_structure": self._struct_dict(_complete_structure(), "doc_ok"),
            "evidence_registry": _evidence([1, 2, 3, 4, 5, 6]),
        }
        res = mod.ExportValidationGate().run(artifacts=artifacts).to_dict()
        dc = res["document_completeness"]
        assert dc["all_documents_complete"] is True
        completeness_codes = {
            w["code"] for w in res["warnings"] if w["code"].startswith("DOCUMENT_")
        }
        assert completeness_codes == set()

    def test_gate_passes_full_document_with_sparse_evidence(self):
        # Issue #371: a fully-ingested document with evidence on a few pages
        # must not be flagged incomplete by the gate (no DOCUMENT_INGEST_INCOMPLETE
        # / DOCUMENT_* warning), so document completeness is not what blocks
        # publish_ready. (Missing downstream artifacts are out of scope here.)
        mod = _import_gate()
        artifacts = {
            "document_structure": self._struct_dict(_full_20page_structure(), "doc_full"),
            "evidence_registry": _evidence([1, 10, 20]),
        }
        res = mod.ExportValidationGate().run(artifacts=artifacts).to_dict()
        dc = res["document_completeness"]
        assert dc["all_documents_complete"] is True
        completeness_codes = {
            w["code"] for w in res["warnings"] if w["code"].startswith("DOCUMENT_")
        }
        assert completeness_codes == set()

    def test_gate_flags_tail_truncation(self):
        # Issue #373: the gate surfaces DOCUMENT_TAIL_TRUNCATION_SUSPECTED and
        # blocks publish_ready when a tail truncation is suspected.
        mod = _import_gate()
        artifacts = {
            "document_structure": self._struct_dict(_tail_cut_no_reference(), "doc_cut"),
        }
        res = mod.ExportValidationGate().run(artifacts=artifacts).to_dict()
        assert res["document_completeness"]["all_documents_complete"] is False
        assert res["publish_ready"] is False
        assert "DOCUMENT_TAIL_TRUNCATION_SUSPECTED" in {w["code"] for w in res["warnings"]}

    def test_gate_flags_truncated_ingest(self):
        # Issue #371: body content only on pages 1 and 3 of 20 → the ingest
        # never reached the document end, so the gate flags it.
        mod = _import_gate()
        artifacts = {
            "document_structure": self._struct_dict(_short_ingest_structure(20), "doc_short"),
            "evidence_registry": _evidence([1, 3]),
        }
        res = mod.ExportValidationGate().run(artifacts=artifacts).to_dict()
        assert res["document_completeness"]["all_documents_complete"] is False
        assert res["publish_ready"] is False
        assert "DOCUMENT_INGEST_INCOMPLETE" in {w["code"] for w in res["warnings"]}

    def test_gate_prefers_precomputed_completeness_artifact(self):
        mod = _import_gate()
        # A structure that would look complete, but an orchestrator-computed
        # artifact marks it incomplete: the gate must honour the artifact.
        precomputed = {
            "document_id": "doc_pre",
            "complete": False,
            "review_reasons": ["terminal_section_missing"],
            "equation_label_continuity": {"missing_labels": [], "has_gaps": False},
            "terminal_section": {"present": False, "missing": True, "matched_titles": []},
            "ingest_coverage": {"sufficient": True, "reached_document_end": True,
                                "pages_total": 6, "last_ingested_page": 6,
                                "structure_page_coverage_ratio": 1.0,
                                "trailing_uningested_page_ranges": []},
            "evidence_page_distribution": {"pages": [], "distribution_ratio": None,
                                           "sparse": False},
        }
        artifacts = {
            "document_structure": self._struct_dict(_complete_structure(), "doc_pre"),
            "document_completeness": precomputed,
        }
        res = mod.ExportValidationGate().run(artifacts=artifacts).to_dict()
        assert res["document_completeness"]["all_documents_complete"] is False
        assert "DOCUMENT_TERMINAL_SECTION_MISSING" in {w["code"] for w in res["warnings"]}


# ---------------------------------------------------------------------------
# Issue #416: TeX equation-artifact coverage / pages=0 silent pass
# ---------------------------------------------------------------------------

class TestEquationArtifactCoverage:
    def test_tex_equation_inventory_counts_blocks_and_symbolic_labels(self):
        mod = _import_completeness_mod()
        tex = r"""
        \begin{equation}\label{eq:energy} E = mc^2 \end{equation}
        \begin{align}\label{eq:force} F = ma \end{align}
        \[ a^2 + b^2 = c^2 \]
        """
        inv = mod.tex_equation_inventory(tex)
        assert inv["display_math_blocks"] == 3
        assert "eq:energy" in inv["labels"]
        assert "eq:force" in inv["labels"]
        assert inv["label_count"] == 2

    def test_pages_zero_tex_with_math_but_no_records_is_incomplete(self):
        # The #416 silent pass: pages=0 + parser_reached_eof=true would let a TeX
        # document with display math but no equation registry pass. Artifact
        # coverage must catch it.
        mod = _import_completeness_mod()
        structure = {
            "document_id": "doc_tex",
            "metadata": {"pages": 0, "parser_reached_eof": True,
                         "tex_source": r"\begin{equation}\label{eq:a} x=1 \end{equation}"},
            "blocks": [],
            "sections": [],
        }
        report = mod.analyze_document_completeness(
            structure, None, document_id="doc_tex",
            equations={"equations": [], "equation_candidates": []},
        )
        cov = report["equation_artifact_coverage"]
        assert cov["tex_display_math_blocks"] == 1
        assert cov["complete"] is False
        assert "tex_display_math_without_equation_records" in cov["review_reasons"]
        assert "equation_artifact_coverage_incomplete" in report["review_reasons"]
        assert report["complete"] is False

    def test_accepted_candidate_not_in_registry_is_coverage_gap(self):
        mod = _import_completeness_mod()
        equations = {
            "equations": [{"equation_id": "eq_1"}],
            "equation_candidates": [
                {"candidate_id": "c1", "acceptance_status": "accepted",
                 "accepted_equation_id": "eq_1"},
                {"candidate_id": "c2", "acceptance_status": "accepted",
                 "accepted_equation_id": "eq_missing"},
            ],
        }
        cov = mod.analyze_equation_artifact_coverage(
            {}, equations, tex_source=None, pages_total=10
        )
        assert cov["complete"] is False
        assert "accepted_candidate_not_in_registry" in cov["review_reasons"]

    def test_complete_tex_coverage_passes(self):
        mod = _import_completeness_mod()
        equations = {
            "equations": [{"equation_id": "eq_1"}],
            "equation_candidates": [
                {"candidate_id": "c1", "acceptance_status": "accepted",
                 "accepted_equation_id": "eq_1"},
            ],
        }
        cov = mod.analyze_equation_artifact_coverage(
            {}, equations,
            tex_source=r"\begin{equation} x=1 \end{equation}",
            pages_total=0,
        )
        assert cov["complete"] is True
        assert cov["review_reasons"] == []

    def test_gate_refreshes_coverage_from_equation_artifacts(self):
        # A precomputed (stale, pre-equation_semantics) completeness report gets
        # its equation coverage refreshed at the gate where artifacts exist.
        mod = _import_gate()
        precomputed = {
            "document_id": "doc_ref",
            "complete": True,
            "review_reasons": [],
            "equation_label_continuity": {"missing_labels": [], "has_gaps": False},
            "terminal_section": {"present": True, "missing": False, "matched_titles": []},
            "ingest_coverage": {"sufficient": True, "reached_document_end": True,
                                "pages_total": 0, "last_ingested_page": None,
                                "structure_page_coverage_ratio": None,
                                "trailing_uningested_page_ranges": []},
            "evidence_page_distribution": {"pages": [], "distribution_ratio": None,
                                           "sparse": False},
            "equation_artifact_coverage": {},
        }
        artifacts = {
            "document_structure": {
                "document_id": "doc_ref",
                "metadata": {"pages": 0, "parser_reached_eof": True,
                             "tex_source": r"\begin{equation} x=1 \end{equation}"},
                "blocks": [],
                "sections": [],
            },
            "document_completeness": precomputed,
            "equation_semantics": {"equations": [], "equation_candidates": []},
        }
        res = mod.ExportValidationGate().run(artifacts=artifacts).to_dict()
        codes = {w["code"] for w in res["warnings"]}
        assert "DOCUMENT_EQUATION_ARTIFACT_COVERAGE_INCOMPLETE" in codes
        assert res["document_completeness"]["all_documents_complete"] is False

    def test_gate_refresh_uses_precomputed_inventory_without_tex_source(self):
        # AC #420(5,6): the gate refresh detects TeX math from the persisted
        # inventory alone — no raw tex_source on the structure.
        mod = _import_gate()
        precomputed = {
            "document_id": "doc_inv",
            "complete": True,
            "review_reasons": [],
            "equation_label_continuity": {"missing_labels": [], "has_gaps": False},
            "terminal_section": {"present": True, "missing": False, "matched_titles": []},
            "ingest_coverage": {"sufficient": True, "reached_document_end": True,
                                "pages_total": 0, "last_ingested_page": None,
                                "structure_page_coverage_ratio": None,
                                "trailing_uningested_page_ranges": []},
            "evidence_page_distribution": {"pages": [], "distribution_ratio": None,
                                           "sparse": False},
            "equation_artifact_coverage": {},
        }
        artifacts = {
            "document_structure": {
                "document_id": "doc_inv",
                "metadata": {
                    "pages": 0,
                    "parser_reached_eof": True,
                    # No tex_source — only the inventory persisted at ingest.
                    "tex_equation_inventory": {
                        "display_math_blocks": 5,
                        "labels": ["eq:a", "eq:b"],
                        "label_count": 2,
                    },
                },
                "blocks": [],
                "sections": [],
            },
            "document_completeness": precomputed,
            "equation_semantics": {"equations": [], "equation_candidates": []},
        }
        res = mod.ExportValidationGate().run(artifacts=artifacts).to_dict()
        doc = res["document_completeness"]["documents"][0]
        cov = doc["equation_artifact_coverage"]
        assert cov["tex_display_math_blocks"] == 5
        assert cov["tex_equation_labels"] == ["eq:a", "eq:b"]
        assert cov["complete"] is False
        assert "DOCUMENT_EQUATION_ARTIFACT_COVERAGE_INCOMPLETE" in {
            w["code"] for w in res["warnings"]
        }

    def test_gate_clears_stale_coverage_reason_when_records_present(self):
        # Regression for #420 P1: the orchestrator's precomputed report ran before
        # equation_semantics (record_count=0 → equation_artifact_coverage_incomplete
        # + complete=false). Once real EquationRecords exist, the gate must REMOVE
        # the stale reason and restore complete=true — not leave the document
        # permanently incomplete.
        mod = _import_gate()
        precomputed = {
            "document_id": "doc_stale",
            "complete": False,
            "review_reasons": ["equation_artifact_coverage_incomplete"],
            "equation_label_continuity": {"missing_labels": [], "has_gaps": False},
            "terminal_section": {"present": True, "missing": False, "matched_titles": []},
            "ingest_coverage": {"sufficient": True, "reached_document_end": True,
                                "pages_total": 0, "last_ingested_page": None,
                                "structure_page_coverage_ratio": None,
                                "trailing_uningested_page_ranges": []},
            "evidence_page_distribution": {"pages": [], "distribution_ratio": None,
                                           "sparse": False},
            "equation_artifact_coverage": {
                "tex_display_math_blocks": 3, "equation_record_count": 0,
                "complete": False,
                "review_reasons": ["tex_display_math_without_equation_records"],
            },
        }
        artifacts = {
            "document_structure": {
                "document_id": "doc_stale",
                "metadata": {
                    "pages": 0, "parser_reached_eof": True,
                    "tex_equation_inventory": {
                        "display_math_blocks": 3, "labels": ["eq:a"], "label_count": 1,
                    },
                },
                "blocks": [],
                "sections": [],
            },
            "document_completeness": precomputed,
            # Real EquationRecords now exist and cover the math.
            "equation_semantics": {
                "equations": [{"equation_id": "eq_1"}],
                "equation_candidates": [
                    {"candidate_id": "c1", "acceptance_status": "accepted",
                     "accepted_equation_id": "eq_1"},
                ],
            },
        }
        res = mod.ExportValidationGate().run(artifacts=artifacts).to_dict()
        doc = res["document_completeness"]["documents"][0]
        assert doc["equation_artifact_coverage"]["complete"] is True
        assert "equation_artifact_coverage_incomplete" not in doc["review_reasons"]
        assert doc["complete"] is True
        assert res["document_completeness"]["all_documents_complete"] is True
        assert "DOCUMENT_EQUATION_ARTIFACT_COVERAGE_INCOMPLETE" not in {
            w["code"] for w in res["warnings"]
        }

    def test_gate_preserves_other_reasons_when_coverage_clears(self):
        # Clearing the coverage reason must not resurrect completeness when an
        # unrelated failure (terminal section missing) is still present.
        mod = _import_gate()
        precomputed = {
            "document_id": "doc_mixed",
            "complete": False,
            "review_reasons": [
                "terminal_section_missing",
                "equation_artifact_coverage_incomplete",
            ],
            "equation_label_continuity": {"missing_labels": [], "has_gaps": False},
            "terminal_section": {"present": False, "missing": True, "matched_titles": []},
            "ingest_coverage": {"sufficient": True, "reached_document_end": True,
                                "pages_total": 0, "last_ingested_page": None,
                                "structure_page_coverage_ratio": None,
                                "trailing_uningested_page_ranges": []},
            "evidence_page_distribution": {"pages": [], "distribution_ratio": None,
                                           "sparse": False},
            "equation_artifact_coverage": {},
        }
        artifacts = {
            "document_structure": {
                "document_id": "doc_mixed",
                "metadata": {
                    "pages": 0, "parser_reached_eof": True,
                    "tex_equation_inventory": {
                        "display_math_blocks": 3, "labels": ["eq:a"], "label_count": 1,
                    },
                },
                "blocks": [],
                "sections": [],
            },
            "document_completeness": precomputed,
            "equation_semantics": {
                "equations": [{"equation_id": "eq_1"}],
                "equation_candidates": [
                    {"candidate_id": "c1", "acceptance_status": "accepted",
                     "accepted_equation_id": "eq_1"},
                ],
            },
        }
        res = mod.ExportValidationGate().run(artifacts=artifacts).to_dict()
        doc = res["document_completeness"]["documents"][0]
        assert "equation_artifact_coverage_incomplete" not in doc["review_reasons"]
        assert "terminal_section_missing" in doc["review_reasons"]
        assert doc["complete"] is False
