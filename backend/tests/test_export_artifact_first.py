"""Tests for issue #383 (artifact-first export) and #387 (component vs operation graph)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT / "backend", ROOT / "backend" / "api", ROOT / "src", Path(__file__).resolve().parent):
    sys.path.insert(0, str(p))

from routes import export_artifacts as ea  # noqa: E402

# Reuse the stub-based loader so export.py imports without FastAPI/DB.
from test_export_bundle import _import_export_module  # noqa: E402


def _export_mod():
    return _import_export_module()


# ---------------------------------------------------------------------------
# build_claims_export (#383)
# ---------------------------------------------------------------------------


def test_build_claims_export_preserves_claim_object_builder_fields():
    artifact = {
        "document_id": "doc_1",
        "claims": [
            {
                "claim_id": "claim_001",
                "document_id": "doc_1",
                "claim_type": "equation_relation",
                "text": "x depends on y",
                "normalized_text": "x depends on y",
                "equation_ids": ["eq_1", "eq_2"],
                "source_evidence_ids": ["evidence_0001"],
                "source_span_ids": ["span_3"],
                "linked_component_ids": ["comp_1"],
                "atomicity": "atomic",
                "is_atomic": True,
                "support_status": "equation_backed",
                "concepts": [{"name": "X", "normalized": "x"}],
            }
        ],
    }
    claims = ea.build_claims_export(artifact, document_id="doc_1")
    assert len(claims) == 1
    c = claims[0]
    assert c["claim_id"] == "claim_001"
    assert c["equation_ids"] == ["eq_1", "eq_2"]
    assert c["equation"] == {"equation_ids": ["eq_1", "eq_2"]}
    assert c["source_evidence_ids"] == ["evidence_0001"]
    assert c["linked_component_ids"] == ["comp_1"]
    assert c["is_atomic"] is True
    assert c["support_status"] == "equation_backed"
    assert c["source_scope"]["span_id"] == "span_3"


# ---------------------------------------------------------------------------
# build_components_export (#383 / #384)
# ---------------------------------------------------------------------------


def test_build_components_export_preserves_component_assembly_fields():
    artifact = {
        "document_id": "doc_1",
        "components": [
            {
                "component_id": "comp_1",
                "component_type": "MethodComponent",
                "label": "Linearization",
                "summary": "Linearizes the system",
                "responsibility_type": "transformation",
                "linked_claim_ids": ["claim_001"],
                "linked_equation_ids": ["eq_1"],
                "input_equation_ids": ["eq_1"],
                "output_equation_ids": ["eq_2"],
                "evidence_refs": {"claim_ids": ["claim_002"]},
                "internal_flow": [{"operation": "linearize"}],
                "publish_ready": False,
            }
        ],
    }
    comps = ea.build_components_export(artifact, document_id="doc_1")
    assert len(comps) == 1
    comp = comps[0]
    assert comp["component_id"] == "comp_1"
    assert comp["name"] == "Linearization"
    assert comp["component_type"] == "MethodComponent"
    assert comp["responsibility_type"] == "transformation"
    # evidence_claims merges linked + evidence_refs claim ids.
    assert "claim_001" in comp["evidence_claims"]
    assert "claim_002" in comp["evidence_claims"]
    assert comp["output_equation_ids"] == ["eq_2"]
    assert comp["internal_flow"] == [{"operation": "linearize"}]


# ---------------------------------------------------------------------------
# build_component_graph_export (#387)
# ---------------------------------------------------------------------------


def test_component_graph_export_separates_operation_nodes():
    artifact = {
        "graph_schema_version": "0.1.0",
        "nodes": [
            {"component_id": "comp_1", "label": "Theory basis", "component_type": "TheoryOperationNode",
             "graph_layer": "main", "member_component_ids": ["op_1"]},
            {"component_id": "op_1", "label": "linearize", "component_type": "EquationOperationNode",
             "graph_layer": "equation_detail", "parent_component_id": "comp_1"},
        ],
        "edges": [
            {"source_component_id": "comp_1", "target_component_id": "comp_1", "relation": "defines"},
            {"source_component_id": "op_1", "target_component_id": "comp_1", "relation": "derives"},
        ],
    }
    out = ea.build_component_graph_export(artifact, document_id="doc_1", known_component_ids={"comp_1"})
    comp_nodes = {n["node_id"] for n in out["component_graph"]["nodes"]}
    op_nodes = {n["operation_id"] for n in out["operation_graph"]["nodes"]}
    assert comp_nodes == {"comp_1"}
    assert op_nodes == {"op_1"}
    # Edge touching an operation node moved out of the component graph.
    assert len(out["component_graph"]["edges"]) == 1
    assert len(out["operation_graph"]["edges"]) == 1
    # Links connect the parent component to its operation node.
    assert {"component_id": "comp_1", "operation_id": "op_1"} in out["component_operation_links"]


# ---------------------------------------------------------------------------
# Resolvers + metadata (#383)
# ---------------------------------------------------------------------------


def test_resolve_claims_prefers_artifact_and_records_fallback():
    mod = _export_mod()
    artifacts_by_doc = {
        "doc_1": {"claim_object_builder": {"document_id": "doc_1", "claims": [
            {"claim_id": "art_claim", "document_id": "doc_1", "claim_type": "result", "text": "t"}
        ]}},
        # doc_2 has no claim artifact -> falls back to DB rows.
    }
    db_claims = [
        {"claim_id": "db_claim", "document_id": "doc_2", "claim_type": "result", "text": "x"},
    ]
    fallback_sources: list[dict] = []
    claims = mod._resolve_artifact_first_claims(
        artifacts_by_doc, ["doc_1", "doc_2"], db_claims, fallback_sources
    )
    ids = {c["claim_id"] for c in claims}
    assert ids == {"art_claim", "db_claim"}
    assert any(f["document_id"] == "doc_2" and f["artifact"] == "claim_object_builder" for f in fallback_sources)


def test_resolve_graph_falls_back_to_db_when_no_artifact():
    mod = _export_mod()
    db_graph = {
        "graph_schema_version": "0.1.0",
        "nodes": [{"node_id": "comp_1", "node_type": "component", "component_type": "component"}],
        "edges": [],
    }
    fallback_sources: list[dict] = []
    out = mod._resolve_artifact_first_graph(
        {}, ["doc_1"], [{"component_id": "comp_1"}], db_graph, fallback_sources
    )
    assert {n["node_id"] for n in out["component_graph"]["nodes"]} == {"comp_1"}
    assert any(f["artifact"] == "component_graph" for f in fallback_sources)


def test_manifest_carries_export_source_policy_and_fallback():
    mod = _export_mod()
    manifest = mod._build_manifest(
        export_id="e1", scope_type="document", scope_id="doc_1",
        material_ids=[], document_ids=["doc_1"],
        claims=[], dsl_graph={"nodes": [], "edges": []}, components=[],
        component_graph={"nodes": [], "edges": []},
        evidence_snippets=[], options={},
        operation_graph={"nodes": [{"operation_id": "op_1"}], "edges": []},
        component_operation_links=[{"component_id": "c", "operation_id": "op_1"}],
        export_source={
            "export_source_policy": "artifact_first",
            "artifact_run_id": "run_9",
            "fallback_used": True,
            "fallback_sources": [{"artifact": "component_graph", "document_id": "doc_1"}],
        },
    )
    assert manifest["export_source_policy"] == "artifact_first"
    assert manifest["artifact_run_id"] == "run_9"
    assert manifest["fallback_used"] is True
    assert manifest["fallback_sources"][0]["artifact"] == "component_graph"
    assert manifest["files"]["operation_graph"] == "graph/operation_graph.json"
    assert manifest["counts"]["operation_nodes"] == 1
    assert manifest["counts"]["component_operation_links"] == 1


def test_validation_flags_operation_id_in_component_graph():
    mod = _export_mod()
    report = mod._validate_export_references(
        claims=[], equations=[], components=[{"component_id": "comp_1"}],
        component_graph={"nodes": [{"node_id": "op_1"}], "edges": []},
        course_info=None, evidence_snippets=[],
        operation_graph={"nodes": [{"operation_id": "op_1"}], "edges": []},
        component_operation_links=[],
    )
    codes = {e["code"] for e in report["errors"]}
    assert "OPERATION_ID_IN_COMPONENT_GRAPH" in codes


# ---------------------------------------------------------------------------
# Pedagogical course/blueprint metadata (#389)
# ---------------------------------------------------------------------------


def test_enrich_course_seeds_topics_from_mapping_artifact():
    course = {"title": "C", "topics": []}
    mapping = {"topics": [
        {"title": "Definitions", "linked_component_ids": ["comp_def"],
         "learning_objectives": ["understand X"], "prerequisite_concepts": ["calc"]},
        {"title": "Results", "linked_component_ids": ["comp_res"]},
    ]}
    components = [
        {"component_id": "comp_def", "review_status": "source_backed",
         "linked_claim_ids": ["claim_1"], "linked_equation_ids": ["eq_1"]},
        {"component_id": "comp_res", "review_status": "teacher_review_required",
         "linked_claim_ids": ["claim_2"]},
    ]
    graph = {"edges": [{"source": "comp_def", "target": "comp_res", "edge_type": "prerequisite_for"}]}
    out = ea.enrich_course_topics(course, course_mapping_artifact=mapping,
                                  components=components, component_graph=graph)
    topics = out["topics"]
    assert len(topics) == 2
    # Ordered by dependency: definitions (prereq) before results.
    assert topics[0]["title"] == "Definitions"
    t0 = topics[0]
    assert t0["topic_id"]
    assert t0["linked_claim_ids"] == ["claim_1"]
    assert t0["linked_equation_ids"] == ["eq_1"]
    # Results topic inherits review_required from its review-required component.
    t_res = next(t for t in topics if t["title"] == "Results")
    assert t_res["review_required"] is True
    assert "linked_component_review_required" in t_res["review_reasons"]


def test_topic_without_component_is_review_required_and_warned():
    mod = _export_mod()
    course = {"title": "C", "topics": [{"title": "Floating"}]}
    out = ea.enrich_course_topics(course, course_mapping_artifact={"topics": []},
                                  components=[], component_graph={})
    topic = out["topics"][0]
    assert topic["review_required"] is True
    assert "topic_without_component" in topic["review_reasons"]

    report = mod._validate_export_references(
        claims=[], equations=[], components=[],
        component_graph={"nodes": [], "edges": []},
        course_info=out, evidence_snippets=[],
        operation_graph={"nodes": [], "edges": []}, component_operation_links=[],
    )
    codes = {w["code"] for w in report["warnings"]}
    assert "COURSE_TOPIC_WITHOUT_COMPONENT" in codes


# ---------------------------------------------------------------------------
# #392: component coverage warning for over-compressed theory papers.
# ---------------------------------------------------------------------------


def test_few_components_many_equations_emits_coverage_warning():
    mod = _export_mod()
    equations = [
        {"equation_id": f"eq_{i}", "latex": "x=y", "source_location": {"block_id": f"b_{i}"}}
        for i in range(16)
    ]
    report = mod._validate_export_references(
        claims=[], equations=equations,
        components=[{"component_id": "component_one"}, {"component_id": "component_two"}],
        component_graph={"nodes": [], "edges": []},
        course_info=None, evidence_snippets=[],
        operation_graph={"nodes": [], "edges": []}, component_operation_links=[],
    )
    codes = {w["code"] for w in report["warnings"]}
    assert "FEW_COMPONENTS_FOR_SOURCE_BACKED_EQUATIONS" in codes


def test_adequate_components_no_coverage_warning():
    mod = _export_mod()
    equations = [
        {"equation_id": f"eq_{i}", "latex": "x=y", "source_location": {"block_id": f"b_{i}"}}
        for i in range(16)
    ]
    components = [{"component_id": f"component_{i}"} for i in range(6)]
    report = mod._validate_export_references(
        claims=[], equations=equations, components=components,
        component_graph={"nodes": [], "edges": []},
        course_info=None, evidence_snippets=[],
        operation_graph={"nodes": [], "edges": []}, component_operation_links=[],
    )
    codes = {w["code"] for w in report["warnings"]}
    assert "FEW_COMPONENTS_FOR_SOURCE_BACKED_EQUATIONS" not in codes
