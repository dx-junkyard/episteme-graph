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


# ---------------------------------------------------------------------------
# #398: generic operation framework propagates to graph + export validation.
# ---------------------------------------------------------------------------


def test_operation_graph_nodes_expose_generic_operation_family():
    artifact = {
        "graph_schema_version": "0.1.0",
        "nodes": [
            {"component_id": "comp_1", "component_type": "TheoryOperationNode", "graph_layer": "main"},
            {"component_id": "op_1", "component_type": "EquationOperationNode",
             "graph_layer": "equation_detail", "parent_component_id": "comp_1",
             "operation": "linearize the skewness bias equation", "linked_equation_ids": ["eq_1"]},
        ],
        "edges": [],
    }
    out = ea.build_component_graph_export(artifact, document_id="doc_1", known_component_ids={"comp_1"})
    op = out["operation_graph"]["nodes"][0]
    # Generic family, never a paper-specific operation key.
    assert op["operation_family"] == "transform_representation"
    for term in ("skew", "bias", "kurt"):
        assert term not in op["operation_family"]


def test_validation_warns_on_isolated_operation_nodes():
    mod = _export_mod()
    op_nodes = [{"operation_id": f"op_{i}", "operation_family": "derive_consequence"} for i in range(4)]
    report = mod._validate_export_references(
        claims=[], equations=[], components=[],
        component_graph={"nodes": [], "edges": []},
        course_info=None, evidence_snippets=[],
        operation_graph={"nodes": op_nodes, "edges": []},
        component_operation_links=[],
    )
    codes = {w["code"] for w in report["warnings"]}
    assert "ISOLATED_OPERATION_NODES" in codes


def test_validation_warns_when_equations_cannot_reach_components():
    mod = _export_mod()
    op_nodes = [{"operation_id": "op_1", "operation_family": "derive_consequence", "linked_equation_ids": ["eq_1"]}]
    report = mod._validate_export_references(
        claims=[], equations=[], components=[],
        component_graph={"nodes": [], "edges": []},
        course_info=None, evidence_snippets=[],
        operation_graph={"nodes": op_nodes, "edges": []},
        component_operation_links=[],
    )
    codes = {w["code"] for w in report["warnings"]}
    assert "EQUATIONS_UNREACHABLE_THROUGH_OPERATIONS" in codes


def test_validation_warns_on_non_generic_operation_family():
    mod = _export_mod()
    op_nodes = [{"operation_id": "op_1", "operation_family": "linearize_skewness_bias_equation"}]
    report = mod._validate_export_references(
        claims=[], equations=[], components=[],
        component_graph={"nodes": [], "edges": []},
        course_info=None, evidence_snippets=[],
        operation_graph={"nodes": op_nodes, "edges": []},
        component_operation_links=[{"component_id": "c", "operation_id": "op_1"}],
    )
    codes = {w["code"] for w in report["warnings"]}
    assert "NON_GENERIC_OPERATION_FAMILY" in codes


# ---------------------------------------------------------------------------
# Review follow-up: responsibility mapping + subtype/review_reasons propagation.
# ---------------------------------------------------------------------------


def test_operation_node_preserves_cartridge_subtype():
    # A cartridge-supplied operation_family/subtype must survive export (#397/#398).
    artifact = {
        "graph_schema_version": "0.1.0",
        "nodes": [
            {"component_id": "comp_1", "component_type": "TheoryOperationNode", "graph_layer": "main"},
            {"component_id": "op_1", "component_type": "EquationOperationNode",
             "graph_layer": "equation_detail", "parent_component_id": "comp_1",
             "operation": "linearize", "operation_family": "transform_representation",
             "operation_subtype": "bias_linearization", "subtype_source": "cartridge",
             "linked_equation_ids": ["eq_1"]},
        ],
        "edges": [],
    }
    out = ea.build_component_graph_export(artifact, document_id="doc_1", known_component_ids={"comp_1"})
    op = out["operation_graph"]["nodes"][0]
    assert op["operation_family"] == "transform_representation"
    assert op["operation_subtype"] == "bias_linearization"
    assert op["subtype_source"] == "cartridge"


def test_unknown_operation_node_propagates_review_reasons():
    artifact = {
        "graph_schema_version": "0.1.0",
        "nodes": [
            {"component_id": "comp_1", "component_type": "TheoryOperationNode", "graph_layer": "main"},
            {"component_id": "op_1", "component_type": "EquationOperationNode",
             "graph_layer": "equation_detail", "parent_component_id": "comp_1",
             "operation": "frobnicate the wibble", "linked_equation_ids": ["eq_1"]},
        ],
        "edges": [],
    }
    out = ea.build_component_graph_export(artifact, document_id="doc_1", known_component_ids={"comp_1"})
    op = out["operation_graph"]["nodes"][0]
    assert op["operation_family"] == "unknown_specific_operation"
    assert op["review_required"] is True
    assert "operation_family_unrecognized" in op["review_reasons"]


# ---------------------------------------------------------------------------
# #390 / #393 / #394: review items, coverage, system operations, two-layer model.
# ---------------------------------------------------------------------------


def test_validation_reports_review_items_and_coverage_and_artifact_sources():
    mod = _export_mod()
    report = mod._validate_export_references(
        claims=[{"claim_id": "c1", "source_evidence_ids": ["ev1"]}, {"claim_id": "c2"}],
        equations=[{"equation_id": "eq1", "source_location": {"block_id": "b1"}}],
        components=[{"component_id": "cmp1", "evidence_claims": ["c1"]}],
        component_graph={"nodes": [], "edges": []},
        course_info={"topics": [{"title": "T", "linked_component_ids": ["cmp1"]}]},
        evidence_snippets=[{"evidence_id": "ev1"}],
        operation_graph={"nodes": [], "edges": []},
        component_operation_links=[],
        artifact_sources={"export_source_policy": "artifact_first"},
    )
    assert "review_items" in report
    cov = report["coverage"]
    assert cov["claims_with_source_links"] == {"total": 2, "covered": 1}
    assert cov["equations_with_source_locations"] == {"total": 1, "covered": 1}
    assert cov["components_with_evidence_links"]["covered"] == 1
    assert cov["topics_with_component_links"] == {"total": 1, "covered": 1}
    assert report["artifact_sources"]["export_source_policy"] == "artifact_first"
    assert report["summary"]["review_required_count"] == 0


def test_operation_subtype_without_provenance_is_review_item():
    mod = _export_mod()
    op_nodes = [{
        "operation_id": "op_1", "operation_family": "derive_consequence",
        "operation_subtype": "elimination", "subtype_source": "",
        "linked_equation_ids": ["eq_1"],
    }]
    report = mod._validate_export_references(
        claims=[], equations=[], components=[],
        component_graph={"nodes": [], "edges": []},
        course_info=None, evidence_snippets=[],
        operation_graph={"nodes": op_nodes, "edges": []},
        component_operation_links=[{"component_id": "c", "operation_id": "op_1"}],
    )
    codes = {r["code"] for r in report["review_items"]}
    assert "OPERATION_SUBTYPE_MISSING_PROVENANCE" in codes
    assert report["publish_ready"] is False


def test_unknown_operation_without_review_flags_is_review_item():
    mod = _export_mod()
    op_nodes = [{
        "operation_id": "op_1", "operation_family": "unknown_specific_operation",
        "review_required": False, "linked_equation_ids": ["eq_1"],
    }]
    report = mod._validate_export_references(
        claims=[], equations=[], components=[],
        component_graph={"nodes": [], "edges": []},
        course_info=None, evidence_snippets=[],
        operation_graph={"nodes": op_nodes, "edges": []},
        component_operation_links=[{"component_id": "c", "operation_id": "op_1"}],
    )
    codes = {r["code"] for r in report["review_items"]}
    assert "UNKNOWN_OPERATION_NOT_REVIEWABLE" in codes


def test_build_system_operations_export_from_system_level_chain():
    chains = [
        {"derivation_id": "d1", "chain_type": "equation_chain"},
        {
            "derivation_id": "system_derivation_0001",
            "chain_type": "system_level",
            "system_id": "SYS_0001",
            "operation": "eliminate",
            "operation_family": "derive_consequence",
            "operation_subtype": "eliminate",
            "subtype_source": "source_text",
            "operation_ids": ["sys_001_step_1"],
            "input_equation_ids": ["eq_1", "eq_2"],
            "output_equation_ids": ["eq_3"],
            "input_claim_ids": ["c1"],
            "source_evidence_ids": ["ev1"],
            "review_required": False,
            "review_reasons": [],
        },
    ]
    out = ea.build_system_operations_export(chains)
    assert len(out) == 1
    sys_op = out[0]
    assert sys_op["system_id"] == "SYS_0001"
    assert sys_op["system_family"] == "derive_consequence"
    assert sys_op["equation_ids"] == ["eq_1", "eq_2", "eq_3"]
    assert sys_op["derivation_ids"] == ["system_derivation_0001"]


def test_system_operation_missing_source_refs_is_hard_error():
    mod = _export_mod()
    report = mod._validate_export_references(
        claims=[], equations=[], components=[],
        component_graph={"nodes": [], "edges": []},
        course_info=None, evidence_snippets=[],
        operation_graph={"nodes": [], "edges": []},
        component_operation_links=[],
        system_operations=[{"system_id": "SYS_1", "system_family": "derive_consequence"}],
    )
    codes = {e["code"] for e in report["errors"]}
    assert "SYSTEM_OPERATION_NO_SOURCE_REFS" in codes
    assert report["exportable"] is False


def test_system_operation_non_generic_family_warns():
    mod = _export_mod()
    report = mod._validate_export_references(
        claims=[{"claim_id": "c1"}], equations=[], components=[],
        component_graph={"nodes": [], "edges": []},
        course_info=None, evidence_snippets=[],
        operation_graph={"nodes": [], "edges": []},
        component_operation_links=[],
        system_operations=[{
            "system_id": "SYS_1", "system_family": "eliminate_second_order_bias",
            "claim_ids": ["c1"],
        }],
    )
    codes = {w["code"] for w in report["warnings"]}
    assert "NON_GENERIC_SYSTEM_FAMILY" in codes


def test_component_spanning_multiple_families_is_review_item():
    # Issue #392: a component collapsing several generic operation families
    # without a split recommendation is flagged for review.
    mod = _export_mod()
    report = mod._validate_export_references(
        claims=[], equations=[],
        components=[{
            "component_id": "cmp1",
            "primary_operation": "define_relation",
            "secondary_operations": ["derive_consequence", "evaluate_condition"],
            "split_recommendation": {"required": False},
        }],
        component_graph={"nodes": [], "edges": []},
        course_info=None, evidence_snippets=[],
        operation_graph={"nodes": [], "edges": []},
        component_operation_links=[],
    )
    codes = {r["code"] for r in report["review_items"]}
    assert "COMPONENT_MULTIPLE_RESPONSIBILITIES" in codes


def test_component_with_split_recommendation_not_double_flagged():
    mod = _export_mod()
    report = mod._validate_export_references(
        claims=[], equations=[],
        components=[{
            "component_id": "cmp1",
            "primary_operation": "define_relation",
            "secondary_operations": ["derive_consequence"],
            "split_recommendation": {"required": True},
        }],
        component_graph={"nodes": [], "edges": []},
        course_info=None, evidence_snippets=[],
        operation_graph={"nodes": [], "edges": []},
        component_operation_links=[],
    )
    codes = {r["code"] for r in report["review_items"]}
    assert "COMPONENT_MULTIPLE_RESPONSIBILITIES" not in codes


# ---------------------------------------------------------------------------
# Review follow-up (PR #399): provenance honesty, ghost nodes, partial graph
# fallback, and system-operation ref integrity with empty target sets.
# ---------------------------------------------------------------------------


def test_existing_family_without_subtype_source_is_not_fabricated_as_cartridge():
    # An operation_family supplied by an agent/core classifier (not a cartridge)
    # must not have its subtype_source fabricated as "cartridge" (#390/#393).
    artifact = {
        "graph_schema_version": "0.1.0",
        "nodes": [
            {"component_id": "comp_1", "component_type": "TheoryOperationNode", "graph_layer": "main"},
            {"component_id": "op_1", "component_type": "EquationOperationNode",
             "graph_layer": "equation_detail", "parent_component_id": "comp_1",
             "operation": "derive", "operation_family": "derive_consequence",
             "operation_subtype": "elimination", "linked_equation_ids": ["eq_1"]},
        ],
        "edges": [],
    }
    out = ea.build_component_graph_export(artifact, document_id="doc_1", known_component_ids={"comp_1"})
    op = out["operation_graph"]["nodes"][0]
    assert op["operation_family"] == "derive_consequence"
    assert op["subtype_source"] == "unknown"


def test_ghost_component_graph_node_is_hard_error():
    mod = _export_mod()
    report = mod._validate_export_references(
        claims=[], equations=[],
        components=[{"component_id": "cmp_real"}],
        component_graph={"nodes": [{"node_id": "cmp_real"}, {"node_id": "ghost"}], "edges": []},
        course_info=None, evidence_snippets=[],
        operation_graph={"nodes": [], "edges": []},
        component_operation_links=[],
    )
    codes = {e["code"] for e in report["errors"]}
    assert "COMPONENT_GRAPH_NODE_NOT_A_COMPONENT" in codes
    assert report["exportable"] is False


def test_partial_graph_artifact_records_per_document_fallback():
    mod = _export_mod()
    fallback_sources: list = []
    artifacts_by_doc = {
        "doc_a": {"component_graph": {"nodes": [
            {"component_id": "cmp_a", "graph_layer": "main", "component_type": "TheoryOperationNode"}
        ], "edges": []}},
        "doc_b": {},  # no component_graph artifact
    }
    mod._resolve_artifact_first_graph(
        artifacts_by_doc,
        ["doc_a", "doc_b"],
        components=[{"component_id": "cmp_a"}],
        db_component_graph={"nodes": [], "edges": []},
        fallback_sources=fallback_sources,
    )
    fb = [f for f in fallback_sources if f["artifact"] == "component_graph" and f["document_id"] == "doc_b"]
    assert fb, "missing per-document component_graph fallback record"


def test_system_operation_ref_unresolved_even_when_equation_set_empty():
    mod = _export_mod()
    report = mod._validate_export_references(
        claims=[], equations=[], components=[],
        component_graph={"nodes": [], "edges": []},
        course_info=None, evidence_snippets=[],
        operation_graph={"nodes": [], "edges": []},
        component_operation_links=[],
        system_operations=[{
            "system_id": "SYS_1", "system_family": "derive_consequence",
            "equation_ids": ["eq_missing"],
        }],
    )
    codes = {e["code"] for e in report["errors"]}
    assert "UNRESOLVED_EXPORT_REF" in codes
    assert report["exportable"] is False


# ---------------------------------------------------------------------------
# Review follow-up #2 (PR #399): partial fallback merges real DB graph data,
# fallback usage warns, ghost nodes are caught even with no components.
# ---------------------------------------------------------------------------


def test_partial_graph_fallback_merges_db_graph_data():
    mod = _export_mod()
    fallback_sources: list = []
    artifacts_by_doc = {
        "doc_a": {"component_graph": {"nodes": [
            {"component_id": "cmp_a", "graph_layer": "main", "component_type": "TheoryOperationNode"}
        ], "edges": []}},
        "doc_b": {},  # no artifact -> must fall back to DB graph and merge it
    }
    db_graphs = {
        "doc_b": {"nodes": [
            {"component_id": "cmp_b", "graph_layer": "main", "component_type": "TheoryOperationNode"}
        ], "edges": []},
    }
    bundle = mod._resolve_artifact_first_graph(
        artifacts_by_doc,
        ["doc_a", "doc_b"],
        components=[{"component_id": "cmp_a"}, {"component_id": "cmp_b"}],
        db_component_graph={"nodes": [], "edges": []},
        fallback_sources=fallback_sources,
        load_db_graph=lambda d: db_graphs.get(d, {"nodes": [], "edges": []}),
    )
    node_ids = {n["node_id"] for n in bundle["component_graph"]["nodes"]}
    assert node_ids == {"cmp_a", "cmp_b"}  # doc_b's DB graph was actually merged
    assert any(f["document_id"] == "doc_b" for f in fallback_sources)


def test_fallback_used_emits_warning_and_blocks_publish_ready():
    mod = _export_mod()
    report = mod._validate_export_references(
        claims=[{"claim_id": "c1", "source_evidence_ids": ["ev1"]}],
        equations=[], components=[],
        component_graph={"nodes": [], "edges": []},
        course_info=None, evidence_snippets=[{"evidence_id": "ev1"}],
        operation_graph={"nodes": [], "edges": []},
        component_operation_links=[],
        artifact_sources={
            "fallback_used": True,
            "fallback_sources": [{"artifact": "component_graph", "document_id": "doc_b"}],
        },
    )
    codes = {w["code"] for w in report["warnings"]}
    assert "FALLBACK_DATA_USED" in codes
    assert report["publish_ready"] is False


def test_ghost_node_is_hard_error_even_with_no_components():
    mod = _export_mod()
    report = mod._validate_export_references(
        claims=[], equations=[], components=[],
        component_graph={"nodes": [{"node_id": "ghost"}], "edges": []},
        course_info=None, evidence_snippets=[],
        operation_graph={"nodes": [], "edges": []},
        component_operation_links=[],
    )
    codes = {e["code"] for e in report["errors"]}
    assert "COMPONENT_GRAPH_NODE_NOT_A_COMPONENT" in codes
    assert report["exportable"] is False


# ---------------------------------------------------------------------------
# Issue #400: complete-fallback merges every DB graph; full claim/component
# cross-ref validation; empty component_operation_link endpoints error.
# ---------------------------------------------------------------------------


def test_complete_fallback_merges_every_document_db_graph():
    mod = _export_mod()
    fallback_sources: list = []
    artifacts_by_doc = {"doc_a": {}, "doc_b": {}}  # no current-run graph artifacts
    db_graphs = {
        "doc_a": {"nodes": [
            {"component_id": "ca", "graph_layer": "main", "component_type": "TheoryOperationNode"}
        ], "edges": []},
        "doc_b": {"nodes": [
            {"component_id": "cb", "graph_layer": "main", "component_type": "TheoryOperationNode"}
        ], "edges": []},
    }
    bundle = mod._resolve_artifact_first_graph(
        artifacts_by_doc,
        ["doc_a", "doc_b"],
        components=[{"component_id": "ca"}, {"component_id": "cb"}],
        db_component_graph=db_graphs["doc_a"],  # course-wide DB returns only one
        fallback_sources=fallback_sources,
        load_db_graph=lambda d: db_graphs.get(d, {"nodes": [], "edges": []}),
    )
    node_ids = {n["node_id"] for n in bundle["component_graph"]["nodes"]}
    assert node_ids == {"ca", "cb"}  # both documents' DB graphs exported, not just one
    fb_docs = {f["document_id"] for f in fallback_sources if f["artifact"] == "component_graph"}
    assert fb_docs == {"doc_a", "doc_b"}


def test_claim_with_only_missing_refs_is_hard_error():
    mod = _export_mod()
    report = mod._validate_export_references(
        claims=[{
            "claim_id": "c1",
            "source_evidence_ids": ["ev_missing"],
            "equation_ids": ["eq_missing"],
            "derivation_ids": ["d_missing"],
            "linked_component_ids": ["cmp_missing"],
        }],
        equations=[{"equation_id": "eq_real"}],
        components=[{"component_id": "cmp_real"}],
        component_graph={"nodes": [], "edges": []},
        course_info=None,
        evidence_snippets=[{"evidence_id": "ev_real"}],
        derivation_chains=[{"derivation_id": "d_real", "steps": [{"input_equation_ids": ["eq_real"], "output_equation_ids": ["eq_real"]}]}],
        operation_graph={"nodes": [], "edges": []},
        component_operation_links=[],
    )
    codes = {e["code"] for e in report["errors"]}
    assert "UNRESOLVED_EXPORT_REF" in codes
    assert report["exportable"] is False
    assert report["publish_ready"] is False


def test_component_with_missing_equation_and_evidence_refs_is_hard_error():
    mod = _export_mod()
    report = mod._validate_export_references(
        claims=[], equations=[{"equation_id": "eq_real"}],
        components=[{
            "component_id": "cmp1",
            "linked_equation_ids": ["eq_missing"],
            "output_equation_ids": ["eq_missing2"],
            "linked_evidence_ids": ["ev_missing"],
            "linked_derivation_ids": ["d_missing"],
            "evidence_refs": {"evidence_ids": ["ev_missing2"]},
        }],
        component_graph={"nodes": [], "edges": []},
        course_info=None, evidence_snippets=[{"evidence_id": "ev_real"}],
        operation_graph={"nodes": [], "edges": []},
        component_operation_links=[],
    )
    err_paths = {e["path"] for e in report["errors"] if e["code"] == "UNRESOLVED_EXPORT_REF"}
    assert any("linked_equation_ids" in p for p in err_paths)
    assert any("output_equation_ids" in p for p in err_paths)
    assert any("linked_evidence_ids" in p for p in err_paths)
    assert any("evidence_refs.evidence_ids" in p for p in err_paths)
    assert report["exportable"] is False


def test_empty_component_operation_link_endpoint_is_hard_error():
    mod = _export_mod()
    report = mod._validate_export_references(
        claims=[], equations=[], components=[],
        component_graph={"nodes": [], "edges": []},
        course_info=None, evidence_snippets=[],
        operation_graph={"nodes": [], "edges": []},
        component_operation_links=[{"component_id": "", "operation_id": ""}],
    )
    codes = {e["code"] for e in report["errors"]}
    assert "EMPTY_COMPONENT_OPERATION_LINK_ENDPOINT" in codes
    assert report["exportable"] is False
