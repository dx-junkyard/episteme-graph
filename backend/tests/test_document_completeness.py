"""Tests for issue #366: document completeness gate + document_boundary fixes.

Covers:
- collapsed boundary detection (boundary_page_end ≪ pages_total) drops
  confidence below 1.0 and raises needs_review,
- bibliography authors leaked into the author list are stripped, while a
  self-citing real author is preserved,
- equation-label discontinuity (internal gaps AND tail truncation detected via
  prose cross-references), missing terminal section, and low page coverage are
  surfaced and propagate to the boundary needs_review,
- page coverage is judged from EvidenceRegistry pages and reports un-ingested
  page ranges,
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


def _import_completeness():
    # Load the dependency-free leaf module directly so the test does not require
    # the full pipeline package (orchestrator / agents) to be importable.
    import importlib.util

    path = ROOT / "backend" / "core" / "document_pipeline" / "completeness.py"
    spec = importlib.util.spec_from_file_location("_dp_completeness_test", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.analyze_document_completeness


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
        "metadata": {"title": "Complete paper", "authors": ["Alice", "Bob"], "pages": 6},
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


def _evidence(pages: list[int]) -> dict:
    return {
        "document_id": "doc",
        "records": [
            {"evidence_id": f"ev_{i}", "source": {"page": p, "block_id": f"b_{i}"},
             "evidence_text": "x"}
            for i, p in enumerate(pages)
        ],
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
        assert rep["page_coverage"]["sufficient"] is True

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

    def test_page_coverage_from_evidence_and_uningested_ranges(self):
        analyze = _import_completeness()
        # Evidence only on pages 1 and 3 of 20 → insufficient, ranges reported.
        rep = analyze(_truncated_structure(), _evidence([1, 3]), document_id="doc_bad")
        cov = rep["page_coverage"]
        assert cov["source"] == "evidence"
        assert cov["ingested_pages"] == [1, 3]
        assert cov["sufficient"] is False
        assert [2, 2] in cov["missing_pages"]
        assert [4, 20] in cov["missing_pages"]
        assert "page_coverage_insufficient" in rep["review_reasons"]

    def test_wide_heading_spread_does_not_mask_truncated_evidence(self):
        analyze = _import_completeness()
        # Headings span every page, but evidence sits on pages 1 and 3 only.
        structure = _truncated_structure()
        structure["blocks"] += [_heading(f"h_{p}", p, f"Section {p}") for p in range(1, 21)]
        rep = analyze(structure, _evidence([1, 3]), document_id="doc_bad")
        assert rep["page_coverage"]["sufficient"] is False


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
            "metadata": {"title": "T", "authors": ["Alice Smith"], "pages": 9},
        }
        b = ea.build_document_boundary(structure, document_id="doc_self")
        assert b["authors"] == ["Alice Smith"]

    def test_authors_never_emptied_from_nonempty_list(self):
        ea = _import_artifacts()
        # Author appears only in references (no byline block) — must not vanish.
        structure = {
            "blocks": [_reference("r1", 9, "Alice Smith. Prior result. 2019.")],
            "sections": [{"section_id": "s", "title": "Intro", "level": 1, "order": 0,
                          "page_start": 1, "page_end": 9}],
            "metadata": {"title": "T", "authors": ["Alice Smith"], "pages": 9},
        }
        b = ea.build_document_boundary(structure, document_id="doc_self2")
        assert b["authors"] == ["Alice Smith"]
        assert "reference_authors_in_author_list" in b["review_reason"]

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
        assert "DOCUMENT_PAGE_COVERAGE_INSUFFICIENT" in codes
        doc = ev["completeness"]["documents"][0]
        assert "(3.39)" in doc["missing_equation_labels"]
        assert doc["pages_total"] == 20
        assert doc["uningested_page_ranges"]

    def test_complete_document_emits_no_completeness_warning(self):
        analyze = _import_completeness()
        rep = analyze(_complete_structure(), None, document_id="doc_ok")
        ev = self._validate([rep])
        completeness_codes = {
            w["code"] for w in ev["warnings"] if w["code"].startswith("DOCUMENT_")
        }
        assert completeness_codes == set()


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
        assert "DOCUMENT_PAGE_COVERAGE_INSUFFICIENT" in codes

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
            "page_coverage": {"sufficient": True, "coverage_ratio": 1.0, "pages_total": 6,
                              "ingested_pages": [1, 2, 3, 4, 5, 6], "missing_pages": []},
        }
        artifacts = {
            "document_structure": self._struct_dict(_complete_structure(), "doc_pre"),
            "document_completeness": precomputed,
        }
        res = mod.ExportValidationGate().run(artifacts=artifacts).to_dict()
        assert res["document_completeness"]["all_documents_complete"] is False
        assert "DOCUMENT_TERMINAL_SECTION_MISSING" in {w["code"] for w in res["warnings"]}
