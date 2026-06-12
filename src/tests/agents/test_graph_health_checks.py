"""Tests for deterministic graph health checks (issue #358)."""
from __future__ import annotations

import importlib.util
import os
import sys

from episteme_graph.agents.component_assembly.schema import (
    ComponentAssemblyResult,
    ComponentRecord,
)
from episteme_graph.agents.component_assembly.validator import (
    ComponentAssemblyValidator,
)
from episteme_graph.agents.component_graph.schema import (
    ComponentGraphEdge,
    ComponentGraphNode,
    ComponentGraphResult,
    GRAPH_SCHEMA_VERSION,
)
from episteme_graph.agents.component_graph.validator import ComponentGraphValidator

_GATE_PATH = os.path.join(
    os.path.dirname(__file__),
    "../../../backend/core/document_pipeline/export_validation_gate.py",
)
_spec = importlib.util.spec_from_file_location("export_validation_gate_gh", _GATE_PATH)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["export_validation_gate_gh"] = _mod
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]
ExportValidationGate = _mod.ExportValidationGate


# ---------------------------------------------------------------------------
# component_assembly: dependency cycle detection
# ---------------------------------------------------------------------------

def _component(component_id, dependencies=None):
    return ComponentRecord(
        component_id=component_id,
        component_type="TheoryComponent",
        label=f"{component_id} label",
        summary=f"{component_id} summary",
        inputs=[], outputs=[], preconditions=[], cautions=[],
        dependencies=list(dependencies or []),
        evidence_refs={"claim_ids": ["claim_1"], "evidence_ids": ["ev_1"]},
        reason="r",
        confidence=0.8,
        review_notes=[],
        linked_claim_ids=["claim_1"],
    )


def _dep(dep_type, refs):
    return {"dependency_type": dep_type, "component_refs": list(refs), "reason": "r"}


def _assembly(components):
    return ComponentAssemblyResult(
        document_id="doc", components_version="v1", cartridge_id=None,
        components=components, assembly_hints=[], review_notes=[],
        confidence=0.8,
    )


def test_requires_cycle_is_hard_error_with_path():
    components = [
        _component("comp_a", [_dep("requires", ["comp_b"])]),
        _component("comp_b", [_dep("depends_on", ["comp_a"])]),
    ]
    issues = ComponentAssemblyValidator().validate(_assembly(components))
    cycles = [i for i in issues if i.rule_id == "component_dependency_cycle"]
    assert len(cycles) == 1
    assert cycles[0].severity == "error"
    assert "comp_a" in cycles[0].message and "comp_b" in cycles[0].message


def test_self_requires_cycle_is_detected():
    components = [_component("comp_a", [_dep("requires", ["comp_a"])])]
    issues = ComponentAssemblyValidator().validate(_assembly(components))
    assert any(i.rule_id == "component_dependency_cycle" for i in issues)


def test_acyclic_dependencies_produce_no_cycle_error():
    components = [
        _component("comp_a", [_dep("requires", ["comp_b"])]),
        _component("comp_b", [_dep("requires", ["comp_c"])]),
        _component("comp_c"),
    ]
    issues = ComponentAssemblyValidator().validate(_assembly(components))
    assert not any(i.rule_id == "component_dependency_cycle" for i in issues)


def test_non_dependency_edge_types_do_not_count_for_cycles():
    # supports / qualifies edges may legitimately form loops of discussion.
    components = [
        _component("comp_a", [_dep("supports", ["comp_b"])]),
        _component("comp_b", [_dep("qualifies", ["comp_a"])]),
    ]
    issues = ComponentAssemblyValidator().validate(_assembly(components))
    assert not any(i.rule_id == "component_dependency_cycle" for i in issues)


# ---------------------------------------------------------------------------
# component_graph: layer linkage + dependency edge cycles
# ---------------------------------------------------------------------------

def _graph_node(component_id, *, layer="main", component_type="TheoryOperationNode",
                parent="", members=(), operation="define_operator"):
    return ComponentGraphNode(
        component_id=component_id,
        label="Theory basis" if layer == "main" else "derive eq",
        component_type=component_type,
        graph_layer=layer,
        parent_component_id=parent,
        member_component_ids=list(members),
        operation=operation,
        source_backing_status="partially_source_backed",
        review_reasons=["missing_atomic_claim"],
        linked_equation_ids=["eq_1"],
        input_equation_ids=["eq_1"],
    )


def _graph(nodes, edges=()):
    return ComponentGraphResult(
        document_id="doc", graph_schema_version=GRAPH_SCHEMA_VERSION,
        cartridge_id=None, nodes=nodes, edges=list(edges),
        review_notes=[], confidence=0.8,
    )


def _dep_edge(edge_id, source, target, edge_type="requires"):
    return ComponentGraphEdge(
        edge_id=edge_id, source=source, target=target, edge_type=edge_type,
        support_status="dependency_declared", evidence_claims=["claim_1"],
        reasoning="r", evidence_equation_ids=["eq_1"],
        source_backing_status="source_backed", review_status="source_backed",
    )


def test_orphan_detail_node_is_warned():
    nodes = [
        _graph_node("main_1", members=["detail_1"]),
        _graph_node("detail_1", layer="equation_detail",
                    component_type="EquationOperationNode", parent="main_1"),
        _graph_node("detail_orphan", layer="equation_detail",
                    component_type="EquationOperationNode", parent=""),
    ]
    issues = ComponentGraphValidator().validate(_graph(nodes))
    orphans = [i for i in issues if i.rule_id == "orphan_detail_node"]
    assert len(orphans) == 1
    assert "detail_orphan" in orphans[0].message
    assert orphans[0].severity == "warning"


def test_empty_main_node_is_warned():
    nodes = [_graph_node("main_lonely", members=())]
    issues = ComponentGraphValidator().validate(_graph(nodes))
    assert any(i.rule_id == "empty_main_node" for i in issues)


def test_linked_layers_produce_no_linkage_warnings():
    nodes = [
        _graph_node("main_1", members=["detail_1"]),
        _graph_node("detail_1", layer="equation_detail",
                    component_type="EquationOperationNode", parent="main_1"),
    ]
    issues = ComponentGraphValidator().validate(_graph(nodes))
    assert not any(
        i.rule_id in ("orphan_detail_node", "empty_main_node") for i in issues
    )


def test_dependency_edge_cycle_is_hard_error():
    nodes = [
        _graph_node("main_1", members=["x"]),
        _graph_node("main_2", members=["y"]),
    ]
    edges = [
        _dep_edge("e1", "main_1", "main_2"),
        _dep_edge("e2", "main_2", "main_1", edge_type="depends_on"),
    ]
    issues = ComponentGraphValidator().validate(_graph(nodes, edges))
    cycles = [i for i in issues if i.rule_id == "component_graph_dependency_cycle"]
    assert len(cycles) == 1
    assert cycles[0].severity == "error"


def test_derivation_flow_edges_do_not_count_for_cycles():
    nodes = [
        _graph_node("main_1", members=["x"]),
        _graph_node("main_2", members=["y"]),
    ]
    edges = [
        _dep_edge("e1", "main_1", "main_2", edge_type="derives"),
        _dep_edge("e2", "main_2", "main_1", edge_type="diagnoses"),
    ]
    issues = ComponentGraphValidator().validate(_graph(nodes, edges))
    assert not any(
        i.rule_id == "component_graph_dependency_cycle" for i in issues
    )


# ---------------------------------------------------------------------------
# export gate: claim↔equation symmetry + equation role conflicts
# ---------------------------------------------------------------------------

class _ClaimObject:
    def __init__(self, claim_id, equation_ids=()):
        self.claim_id = claim_id
        self.support_status = "source_backed"
        self.source_evidence_ids = ["ev_001"]
        self.atomicity = "atomic"
        self.claim_type = "definition"
        self.equation_ids = list(equation_ids)


class _ClaimObjectResult:
    def __init__(self, claims):
        self.claims = claims


class _EvidenceRecord:
    def __init__(self, evidence_id):
        self.evidence_id = evidence_id


class _EvidenceResult:
    def __init__(self, records):
        self.records = records


def _make_artifacts(equations):
    return {
        "document_structure": {"blocks": [], "sections": []},
        "source_chunking": [{"text": "chunk"}],
        "claim_qualification": {"qualified_spans": []},
        "component_assembly": {"components": [], "validation_issues": []},
        "equation_semantics": {"equations": equations},
    }


def _run_gate(artifacts, *, claim_objects=None, component_result=None):
    return ExportValidationGate().run(
        artifacts=artifacts,
        component_result=component_result,
        course_mapping=None,
        claim_objects=claim_objects,
        evidence=_EvidenceResult([_EvidenceRecord("ev_001")]),
        dsl=None,
    )


def test_one_way_claim_to_equation_link_is_warned():
    artifacts = _make_artifacts([
        {"equation_id": "eq_1", "linked_claim_ids": []},
    ])
    result = _run_gate(
        artifacts,
        claim_objects=_ClaimObjectResult([_ClaimObject("claim_1", ["eq_1"])]),
    )
    asym = [w for w in result.warnings if w.code == "CLAIM_EQUATION_LINK_ASYMMETRY"]
    assert len(asym) == 1
    assert "claim_1" in asym[0].message
    # report only — never a hard error
    assert not any(e.code == "CLAIM_EQUATION_LINK_ASYMMETRY" for e in result.errors)


def test_one_way_equation_to_claim_link_is_warned():
    artifacts = _make_artifacts([
        {"equation_id": "eq_1", "linked_claim_ids": ["claim_1"]},
    ])
    result = _run_gate(
        artifacts,
        claim_objects=_ClaimObjectResult([_ClaimObject("claim_1", [])]),
    )
    asym = [w for w in result.warnings if w.code == "CLAIM_EQUATION_LINK_ASYMMETRY"]
    assert len(asym) == 1
    assert asym[0].artifact == "equation_semantics"


def test_symmetric_links_produce_no_warning():
    artifacts = _make_artifacts([
        {"equation_id": "eq_1", "linked_claim_ids": ["claim_1"]},
    ])
    result = _run_gate(
        artifacts,
        claim_objects=_ClaimObjectResult([_ClaimObject("claim_1", ["eq_1"])]),
    )
    assert not any(
        w.code == "CLAIM_EQUATION_LINK_ASYMMETRY" for w in result.warnings
    )


class _RoleComponent:
    def __init__(self, component_id, *, output_equation_ids=(),
                 definition_equation_ids=()):
        self.component_id = component_id
        self.component_type = "TheoryComponent"
        self.label = component_id
        self.summary = ""
        self.evidence_refs = {"claim_ids": ["claim_1"], "evidence_ids": ["ev_001"]}
        self.linked_claim_ids = ["claim_1"]
        self.linked_equation_ids = []
        self.output_equation_ids = list(output_equation_ids)
        self.definition_equation_ids = list(definition_equation_ids)
        self.review_status = "teacher_review_required"
        self.internal_flow = []
        self.maturity_source = "llm_proposed"
        self.fallback_reason = ""
        self.inputs = []
        self.outputs = []
        self.preconditions = []
        self.cautions = []


class _ComponentResult:
    def __init__(self, components):
        self.components = components


def test_definition_equation_used_as_output_is_role_conflict():
    artifacts = _make_artifacts([
        {"equation_id": "eq_def", "semantics": {"equation_type": "definition"}},
    ])
    result = _run_gate(
        artifacts,
        claim_objects=_ClaimObjectResult([_ClaimObject("claim_1")]),
        component_result=_ComponentResult([
            _RoleComponent("comp_1", output_equation_ids=["eq_def"]),
        ]),
    )
    conflicts = [w for w in result.warnings if w.code == "EQUATION_ROLE_CONFLICT"]
    assert len(conflicts) == 1
    assert "eq_def" in conflicts[0].message


def test_consistent_equation_roles_produce_no_conflict():
    artifacts = _make_artifacts([
        {"equation_id": "eq_def", "semantics": {"equation_type": "definition"}},
        {"equation_id": "eq_res", "semantics": {"equation_type": "result"}},
    ])
    result = _run_gate(
        artifacts,
        claim_objects=_ClaimObjectResult([_ClaimObject("claim_1")]),
        component_result=_ComponentResult([
            _RoleComponent(
                "comp_1",
                definition_equation_ids=["eq_def"],
                output_equation_ids=["eq_res"],
            ),
        ]),
    )
    assert not any(w.code == "EQUATION_ROLE_CONFLICT" for w in result.warnings)


# ---------------------------------------------------------------------------
# issue #358 review fixes: 降格・review reason の artifact への付与
# ---------------------------------------------------------------------------

def test_asymmetry_annotation_demotes_links_on_both_artifacts():
    from episteme_graph.agents.id_canonicalization import (
        annotate_claim_equation_link_asymmetries,
    )

    class _Sem:
        def __init__(self, linked):
            self.linked_claim_ids = list(linked)
            self.review_flags = []

    class _Equation:
        def __init__(self, equation_id, linked):
            self.equation_id = equation_id
            self.semantics = _Sem(linked)

    class _Equations:
        def __init__(self, equations):
            self.equations = equations

    class _Claim:
        def __init__(self, claim_id, equation_ids):
            self.claim_id = claim_id
            self.equation_ids = list(equation_ids)
            self.review_note = ""

    class _Claims:
        def __init__(self, claims):
            self.claims = claims

    claim_one_way = _Claim("claim_a", ["eq_1"])      # eq_1 does not link back
    claim_symmetric = _Claim("claim_b", ["eq_2"])
    eq_one_way = _Equation("eq_3", ["claim_b"])      # claim_b does not link eq_3
    equations = _Equations([
        _Equation("eq_1", []),
        _Equation("eq_2", ["claim_b"]),
        eq_one_way,
    ])
    count = annotate_claim_equation_link_asymmetries(
        _Claims([claim_one_way, claim_symmetric]), equations
    )
    assert count == 2
    # claim 側: review_note に明示。リンクは保持
    assert "one-way" in claim_one_way.review_note
    assert claim_one_way.equation_ids == ["eq_1"]
    assert claim_symmetric.review_note == ""
    # equation 側: review flag に明示。リンクは保持
    assert "claim_link_asymmetry" in eq_one_way.semantics.review_flags
    assert eq_one_way.semantics.linked_claim_ids == ["claim_b"]


def test_enrichment_flags_equation_role_conflict_on_component():
    from episteme_graph.agents.component_assembly.enrichment import (
        enrich_component_assembly,
    )
    from episteme_graph.agents.component_assembly.schema import (
        COMPONENTS_VERSION,
        ComponentAssemblyLLMInput,
        ComponentAssemblyResult,
        ComponentRecord,
    )

    component = ComponentRecord(
        "comp_1", "TheoryComponent", "label", "summary",
        [], [], [], [], [],
        {"claim_ids": [], "equation_ids": ["eq_def"],
         "dsl_refs": {"node_ids": [], "edge_ids": []}},
        "r", 0.8, [],
        output_equation_ids=["eq_def"],
        review_status="auto_accepted",
    )
    llm_input = ComponentAssemblyLLMInput(
        document_id="doc", cartridge_id=None, accepted_claims=[],
        equations=[{"equation_id": "eq_def", "role": "definition"}],
        thesis_nodes=[], dsl_nodes=[], dsl_edges=[],
        allowed_component_types=["TheoryComponent"],
        allowed_dependency_types=["requires"],
    )
    enrich_component_assembly(
        ComponentAssemblyResult("doc", COMPONENTS_VERSION, None, [component], [], [], 0.8),
        llm_input,
    )
    assert any("equation_role_conflict" in note for note in component.review_notes)
    assert component.review_status == "teacher_review_required"
    assert "eq_def" in component.output_equation_ids  # link kept


def test_normalizer_annotates_orphan_and_empty_main_nodes():
    from episteme_graph.agents.component_graph.normalizer import (
        _annotate_layer_linkage_gaps,
    )

    main = _graph_node("main_1", members=())
    orphan = _graph_node(
        "detail_orphan", layer="equation_detail",
        component_type="EquationOperationNode", parent="",
    )
    _annotate_layer_linkage_gaps([main, orphan])
    assert "empty_main_node" in main.review_reasons
    assert "orphan_detail_node" in orphan.review_reasons
