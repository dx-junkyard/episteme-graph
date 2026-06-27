"""Tests for issue #242 export artifact helpers.

Covers the source-backed evidence registry, first-class equation registry,
equation candidate trace, derivation chain export, course/topic metadata
enrichment, and document boundary detection produced by the export bundle.
"""
from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "api"))


def _import_artifacts():
    from routes import export_artifacts  # type: ignore
    return export_artifacts


def _import_export_module():
    """Re-use the helper from test_export_bundle so we can call _build_zip."""
    sys.path.insert(0, str(ROOT / "backend" / "tests"))
    from test_export_bundle import _import_export_module as _impl  # type: ignore
    return _impl()


SAMPLE_STRUCTURE = {
    "blocks": [
        {
            "block_id": "blk_5",
            "page": 3,
            "text": "F = ma",
            "bbox": [0, 0, 10, 10],
            "block_type": "equation_block",
            "section_id": "sec_1",
            "equation_label": "(1)",
            "confidence": 0.9,
        },
        {
            "block_id": "blk_6",
            "page": 4,
            "text": "leftover formula",
            "bbox": [0, 0, 5, 5],
            "block_type": "equation_block",
            "section_id": "sec_1",
            "equation_label": None,
            "confidence": 0.4,
        },
    ],
    "sections": [
        {"section_id": "sec_1", "title": "Intro", "level": 1, "order": 0,
         "page_start": 1, "page_end": 5},
    ],
    "metadata": {
        "title": "Test paper", "authors": ["Alice"], "pages": 10,
        "author_extraction": {"source": "grobid_tei", "confidence": 0.95,
                              "needs_review": False, "review_reasons": []},
    },
}

SAMPLE_EQUATIONS = {
    "document_id": "doc_001",
    "equations": [
        {
            "equation_id": "eq_001",
            "block_id": "blk_5",
            "section_id": "sec_1",
            "label": "(1)",
            "text": "F = ma",
            "latex": "F = ma",
            "plain_text": "F = ma",
            "equation_role": {"primary": "equation_definition", "secondary": [],
                              "confidence": 0.9, "reason": "Definition of force."},
            "defined_symbols": [
                {"symbol": "F", "definition_status": "defined", "evidence_text": "force"},
                {"symbol": "m", "definition_status": "used"},
            ],
            "local_assumptions": [{"text": "small_x", "source_block_ids": []}],
            "derivation_links": {"from_equations": [], "to_equations": ["eq_002"], "linked_text_spans": []},
            "review_flags": [],
        },
        {
            "equation_id": "eq_002",
            "block_id": "blk_7",
            "section_id": "sec_1",
            "label": "(2)",
            "text": "p = mv",
            "latex": "p = mv",
            "plain_text": "p = mv",
            "equation_role": {"primary": "equation_relation", "secondary": [],
                              "confidence": 0.7, "reason": "Momentum relation."},
            "defined_symbols": [],
            "local_assumptions": [],
            "derivation_links": {"from_equations": ["eq_001"], "to_equations": [], "linked_text_spans": []},
            "review_flags": ["low_confidence"],
        },
    ],
}

SAMPLE_EVIDENCE_REGISTRY = {
    "document_id": "doc_001",
    "records": [
        {
            "evidence_id": "ev_001",
            "document_id": "doc_001",
            "source": {"page": 2, "section_id": "sec_1", "block_id": "blk_5",
                       "span_start": 100, "span_end": 200},
            "evidence_text": "F = ma is the canonical statement of Newton's second law.",
            "evidence_role": "source_quote",
            "review_note": "",
        },
    ],
}


# ---------------------------------------------------------------------------
# Equations export
# ---------------------------------------------------------------------------

class TestEquationsExport:
    def test_first_class_equation_registry_exposes_required_fields(self):
        ea = _import_artifacts()
        eqs = ea.build_equations_export(
            SAMPLE_EQUATIONS, document_id="doc_001",
            structure_artifact=SAMPLE_STRUCTURE,
        )
        assert len(eqs) == 2
        e1 = eqs[0]
        # Issue #242 §3 acceptance criteria:
        for field in (
            "equation_id", "document_id", "label", "latex", "plain_text",
            "source_location", "equation_type", "defined_symbols", "used_symbols",
            "input_equation_ids", "output_equation_ids", "extraction_source",
            "extraction_status", "needs_math_review", "review_reason",
            "candidate_trace_ids",
        ):
            assert field in e1, f"missing {field}"
        assert e1["source_location"]["page"] == 3
        assert e1["source_location"]["block_id"] == "blk_5"
        assert e1["extraction_source"] == "pdf_text_layer"
        assert "F" in e1["defined_symbols"]
        assert "m" in e1["used_symbols"]
        assert e1["candidate_trace_ids"] == ["eqcand_eq_001"]

    def test_equation_with_review_flag_marks_needs_math_review(self):
        ea = _import_artifacts()
        eqs = ea.build_equations_export(
            SAMPLE_EQUATIONS, document_id="doc_001",
            structure_artifact=SAMPLE_STRUCTURE,
        )
        eq2 = next(e for e in eqs if e["equation_id"] == "eq_002")
        assert eq2["needs_math_review"] is True
        assert "low_confidence" in eq2["review_reason"]

    def test_equation_without_artifact_returns_empty(self):
        ea = _import_artifacts()
        assert ea.build_equations_export(None, document_id="doc_001") == []
        assert ea.build_equations_export({}, document_id="doc_001") == []


# ---------------------------------------------------------------------------
# Equation candidate trace
# ---------------------------------------------------------------------------

class TestEquationCandidatesExport:
    def test_accepted_candidates_link_back_to_equation_id(self):
        ea = _import_artifacts()
        cands = ea.build_equation_candidates_export(
            SAMPLE_EQUATIONS, document_id="doc_001",
            structure_artifact=SAMPLE_STRUCTURE,
        )
        # eq_001 → accepted; blk_6 (no equation_semantics record) → rejected
        accepted = [c for c in cands if c["acceptance_status"] == "accepted"]
        rejected = [c for c in cands if c["acceptance_status"] == "rejected"]
        assert len(accepted) == 2
        assert len(rejected) == 1
        for field in (
            "candidate_id", "document_id", "source_location", "raw_text",
            "detection_method", "matched_label", "acceptance_status",
            "accepted_equation_id", "rejection_reason", "needs_math_review",
            "review_reason",
        ):
            assert field in accepted[0], f"missing {field}"
        assert accepted[0]["accepted_equation_id"] in ("eq_001", "eq_002")
        assert rejected[0]["accepted_equation_id"] is None
        assert rejected[0]["rejection_reason"]


# ---------------------------------------------------------------------------
# Evidence registry export (source-backed)
# ---------------------------------------------------------------------------

class TestEvidenceExport:
    def test_evidence_registry_artifact_yields_source_backed_rows(self):
        ea = _import_artifacts()
        evs = ea.build_evidence_export(SAMPLE_EVIDENCE_REGISTRY, document_id="doc_001")
        assert len(evs) == 1
        e = evs[0]
        assert e["evidence_text"].startswith("F = ma")
        assert e["extraction_source"] == "pdf_text_layer"
        assert e["extraction_status"] == "extracted"
        assert e["needs_review"] is False
        assert e["block_id"] == "blk_5"
        assert e["span_start"] == 100
        assert e["span_end"] == 200

    def test_fallback_evidence_marks_unverified(self):
        ea = _import_artifacts()
        evs = ea.build_evidence_export(None, document_id="doc_001", fallback_claims=[
            {"claim_id": "c1", "evidence_text": "This span states the law.",
             "document_id": "doc_001", "source_scope": {"page": 2}},
        ])
        assert len(evs) == 1
        assert evs[0]["needs_review"] is True
        assert evs[0]["extraction_status"] == "reconstructed"
        # LLM-style commentary should not be silently treated as a source quote.
        assert "evidence_not_source_verified" in evs[0]["review_reason"]

    def test_fallback_evidence_moves_text_to_analysis_note_not_evidence_text(self):
        """Issue #257: legacy claim.evidence_text (may be span.reason) must not
        pollute evidence_text in the export bundle. It goes to analysis_note."""
        ea = _import_artifacts()
        llm_reason = "This claim is an approximation valid in the heavy-quark limit."
        evs = ea.build_evidence_export(None, document_id="doc_001", fallback_claims=[
            {"claim_id": "c2", "evidence_text": llm_reason,
             "document_id": "doc_001", "source_scope": {"section_id": "sec_1"}},
        ])
        assert len(evs) == 1
        ev = evs[0]
        # evidence_text must be empty — the text is NOT PDF-derived (#257)
        assert ev["evidence_text"] == ""
        # text is preserved in analysis_note for audit purposes
        assert ev["analysis_note"] == llm_reason
        assert ev["needs_review"] is True
        assert ev["extraction_source"] == "reconstructed"
        assert "legacy_evidence_text_in_analysis_note" in ev["review_reason"]

    def test_fallback_evidence_qualification_reason_in_review_note(self):
        """Issue #257: qualification_reason stored in source_scope by persist_theory_claims
        should appear in review_note of the fallback evidence row."""
        ea = _import_artifacts()
        q_reason = "LLM classified this as an approximation with confidence 0.9"
        evs = ea.build_evidence_export(None, document_id="doc_001", fallback_claims=[
            {
                "claim_id": "c3",
                "evidence_text": "some evidence text",
                "document_id": "doc_001",
                "source_scope": {
                    "section_id": "sec_2",
                    "block_id": "blk_007",
                    "qualification_reason": q_reason,
                },
            },
        ])
        assert len(evs) == 1
        assert evs[0]["review_note"] == q_reason

    def test_fallback_skips_claims_with_empty_evidence_text(self):
        """Issue #257: after persist_theory_claims fix, new DB rows have empty
        evidence_text. Fallback should produce no evidence rows for those."""
        ea = _import_artifacts()
        evs = ea.build_evidence_export(None, document_id="doc_001", fallback_claims=[
            {"claim_id": "c4", "evidence_text": "",
             "document_id": "doc_001", "source_scope": {}},
        ])
        assert evs == []


# ---------------------------------------------------------------------------
# Derivation chains
# ---------------------------------------------------------------------------

class TestDerivationChains:
    def test_derivation_chain_artifact_passthrough(self):
        ea = _import_artifacts()
        artifact = {
            "document_id": "doc_001",
            "chains": [{
                "derivation_id": "deriv_001",
                "document_id": "doc_001",
                "source_section_ids": ["sec_2"],
                "steps": [{
                    "step_id": "deriv_001_1",
                    "input_equation_ids": ["eq_001"],
                    "operation": "substitute",
                    "output_equation_ids": ["eq_002"],
                    "claim_ids": ["claim_007"],
                    "required_claim_ids": ["claim_007"],
                    "source_evidence_ids": ["ev_001"],
                    "assumption_refs": ["small_x"],
                    "reason": "Substituting eq_001 into ...",
                    "confidence": 0.8,
                }],
                "teaching_takeaway": "Take-home text.",
                "blackbox_policy_suggestion": {"blackbox": ["eq_002"]},
            }],
        }
        chains = ea.build_derivation_chains_export(artifact, document_id="doc_001")
        assert len(chains) == 1
        s = chains[0]["steps"][0]
        assert s["operation"] == "substitute"
        assert s["input_equation_ids"] == ["eq_001"]
        assert s["output_equation_ids"] == ["eq_002"]
        assert s["claim_ids"] == ["claim_007"]
        assert s["source_evidence_ids"] == ["ev_001"]
        assert s["assumption_refs"] == ["small_x"]

    def test_derivation_fallback_uses_equation_links(self):
        ea = _import_artifacts()
        chains = ea.build_derivation_chains_export(
            None, document_id="doc_001", equation_artifact=SAMPLE_EQUATIONS,
        )
        # eq_002 has from_equations=[eq_001] → 1 chain
        assert len(chains) == 1
        step = chains[0]["steps"][0]
        assert step["input_equation_ids"] == ["eq_001"]
        assert step["output_equation_ids"] == ["eq_002"]

    def test_derivation_fallback_builds_source_order_chain_when_links_missing(self):
        ea = _import_artifacts()
        artifact = {
            "document_id": "doc_001",
            "equations": [
                {
                    "equation_id": "eq_001",
                    "block_id": "blk_5",
                    "section_id": "sec_1",
                    "equation_role": {"primary": "equation_definition"},
                    "derivation_links": {"from_equations": [], "to_equations": []},
                },
                {
                    "equation_id": "eq_002",
                    "block_id": "blk_6",
                    "section_id": "sec_1",
                    "equation_role": {"primary": "equation_relation"},
                    "derivation_links": {"from_equations": [], "to_equations": []},
                },
            ],
        }
        evidence = {
            "records": [
                {"evidence_id": "ev_001", "source": {"block_id": "blk_5"}},
                {"evidence_id": "ev_002", "source": {"block_id": "blk_6"}},
            ]
        }
        chains = ea.build_derivation_chains_export(
            None,
            document_id="doc_001",
            equation_artifact=artifact,
            evidence_artifact=evidence,
        )
        assert len(chains) == 1
        steps = chains[0]["steps"]
        assert [s["output_equation_ids"] for s in steps] == [["eq_001"], ["eq_002"]]
        assert steps[0]["source_evidence_ids"] == ["ev_001"]
        assert steps[1]["source_evidence_ids"] == ["ev_002"]


# ---------------------------------------------------------------------------
# Document boundary
# ---------------------------------------------------------------------------

class TestDocumentBoundary:
    def test_boundary_pulls_from_document_structure(self):
        ea = _import_artifacts()
        b = ea.build_document_boundary(SAMPLE_STRUCTURE, document_id="doc_001")
        assert b["title"] == "Test paper"
        assert b["authors"] == ["Alice"]
        assert b["boundary_page_start"] == 1
        assert b["boundary_page_end"] == 5
        assert b["needs_review"] is False
        assert "sec_1" in b["active_section_ids"]

    def test_boundary_flags_review_when_pages_missing(self):
        ea = _import_artifacts()
        structure = {
            "blocks": [],
            "sections": [{"section_id": "sec_x", "title": "x", "level": 1,
                          "order": 0, "page_start": None, "page_end": None}],
            "metadata": {},
        }
        b = ea.build_document_boundary(structure, document_id="doc_002")
        assert b["needs_review"] is True
        assert b["review_reason"]


# ---------------------------------------------------------------------------
# Course / topic enrichment
# ---------------------------------------------------------------------------

class TestCourseEnrichment:
    def test_topic_metadata_gets_pulled_from_course_mapping_and_blueprint(self):
        ea = _import_artifacts()
        course = {
            "topics": [
                {"title": "Newton's law", "description": "...", "prerequisites": ["calc"]},
            ],
        }
        mapping = {
            "topics": [{
                "title": "Newton's law",
                "learning_objectives": ["state F=ma"],
                "prerequisite_concepts": ["calc", "force_concept"],
                "blackbox_policy": {"show_derivation": ["eq_001"], "blackbox": []},
                "assessment_prompts": ["Derive F=ma."],
                "expected_misconceptions": ["confuse mass with weight"],
            }],
        }
        blueprint = {"narrative_arc": [
            {"step": 1, "role": "problem_setup", "visual_strategy": "schematic",
             "rationale": "intro", "linked_component_ids": ["comp_1"]},
            {"step": 2, "role": "core_relation", "visual_strategy": "equation_map",
             "rationale": "main eq", "linked_component_ids": ["comp_2"]},
        ]}
        enriched = ea.enrich_course_topics(course,
            course_mapping_artifact=mapping, blueprint_artifact=blueprint)
        topic = enriched["topics"][0]
        assert topic["learning_objectives"] == ["state F=ma"]
        assert topic["prerequisite_concepts"] == ["calc", "force_concept"]
        assert topic["blackbox_policy"]["show_derivation"] == ["eq_001"]
        assert topic["assessment_prompts"] == ["Derive F=ma."]
        assert topic["expected_misconceptions"] == ["confuse mass with weight"]
        assert len(topic["visualization_plan"]) == 2

    def test_topics_without_artifacts_keep_existing_fields(self):
        ea = _import_artifacts()
        course = {"topics": [{"title": "T", "description": "d", "prerequisites": ["x"]}]}
        enriched = ea.enrich_course_topics(course)
        topic = enriched["topics"][0]
        assert topic["title"] == "T"
        assert topic["learning_objectives"] == []
        assert topic["prerequisite_concepts"] == ["x"]


# ---------------------------------------------------------------------------
# End-to-end: ZIP bundle includes new artifacts
# ---------------------------------------------------------------------------

class TestExportZipNewArtifacts:
    def _make_zip(self):
        mod = _import_export_module()
        manifest = mod._build_manifest(
            export_id="x",
            scope_type="course",
            scope_id="c",
            material_ids=[],
            document_ids=["doc_001"],
            claims=[],
            dsl_graph={"nodes": [], "edges": []},
            components=[],
            component_graph={"nodes": [], "edges": []},
            evidence_snippets=[],
            equations=[{"equation_id": "eq_1"}],
            equation_candidates=[{"candidate_id": "eqcand_eq_1"}],
            derivation_chains=[{"derivation_id": "deriv_1"}],
            document_boundaries=[{"document_id": "doc_001"}],
            options={},
        )
        return mod._build_zip(
            manifest=manifest,
            claims=[],
            dsl_graph={"nodes": [], "edges": []},
            components=[],
            component_graph={"nodes": [], "edges": []},
            evidence_snippets=[],
            equations=[{"equation_id": "eq_1"}],
            equation_candidates=[{"candidate_id": "eqcand_eq_1"}],
            derivation_chains=[{"derivation_id": "deriv_1"}],
            document_boundaries=[{"document_id": "doc_001"}],
        )

    def test_zip_includes_new_files(self):
        zip_bytes = self._make_zip()
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            names = zf.namelist()
        for name in (
            "equations/equations.json",
            "equations/equation_candidates.json",
            "derivations/derivation_chains.json",
            "document_boundary.json",
        ):
            assert name in names, f"missing {name}"

    def test_manifest_counts_include_new_artifacts(self):
        zip_bytes = self._make_zip()
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            manifest = json.loads(zf.read("manifest.json"))
        for key in (
            "equations", "equation_candidates", "derivation_chains",
            "document_boundaries",
        ):
            assert key in manifest["counts"], f"missing count: {key}"
        for key in (
            "equations", "equation_candidates", "derivation_chains",
            "document_boundary",
        ):
            assert key in manifest["files"], f"missing file index: {key}"

    def test_readme_describes_new_artifacts(self):
        zip_bytes = self._make_zip()
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            readme = zf.read("README.md").decode("utf-8")
        assert "equations.json" in readme
        assert "equation_candidates.json" in readme
        assert "derivation_chains.json" in readme
        assert "document_boundary.json" in readme
