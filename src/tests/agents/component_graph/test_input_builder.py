"""Tests for ComponentGraphInputBuilder."""
from __future__ import annotations

from episteme_graph.agents.component_assembly.schema import ComponentAssemblyResult, ComponentRecord
from episteme_graph.agents.derivation_chain.schema import (
    DerivationChainRecord,
    DerivationChainResult,
    DerivationStep,
)
from episteme_graph.agents.dsl_linking.schema import DSLEdge, DSLLinkingResult, DSLNode
from episteme_graph.agents.component_graph.input_builder import ComponentGraphInputBuilder


def _make_component(
    cid: str,
    label: str,
    linked_dsl_node_ids: list[str] | None = None,
    linked_claim_ids: list[str] | None = None,
    dependencies: list[dict] | None = None,
) -> ComponentRecord:
    return ComponentRecord(
        component_id=cid,
        component_type="TheoryComponent",
        label=label,
        summary=f"Summary of {label}",
        inputs=[{"name": "input_a", "node_refs": ["n1"], "claim_ids": [], "equation_ids": []}],
        outputs=[{"name": "output_a", "node_refs": ["n2"], "claim_ids": [], "equation_ids": []}],
        preconditions=[],
        cautions=[],
        dependencies=dependencies or [],
        evidence_refs={},
        reason="test",
        confidence=0.9,
        review_notes=[],
        linked_dsl_node_ids=linked_dsl_node_ids or [],
        linked_claim_ids=linked_claim_ids or [],
    )


def _make_assembly(components: list[ComponentRecord]) -> ComponentAssemblyResult:
    return ComponentAssemblyResult(
        document_id="doc_test",
        components_version="v1",
        cartridge_id=None,
        components=components,
        assembly_hints=[],
        review_notes=[],
        confidence=0.9,
    )


def _make_dsl_node(node_id: str) -> DSLNode:
    return DSLNode(
        node_id=node_id,
        node_type="EquationRelation",
        node_value="test",
        source_kind="claim",
        source_refs={},
        reason="test",
        confidence=0.9,
    )


def _make_dsl_edge(edge_id: str, from_id: str, to_id: str) -> DSLEdge:
    return DSLEdge(
        edge_id=edge_id,
        from_node_id=from_id,
        to_node_id=to_id,
        core_predicate="REQUIRES",
        domain_verb="assumes",
        polarity="+",
        evidence_refs={},
        reason="test",
        confidence=0.85,
    )


def _make_dsl(nodes: list[DSLNode], edges: list[DSLEdge]) -> DSLLinkingResult:
    return DSLLinkingResult(
        document_id="doc_test",
        dsl_version="v1",
        nodes=nodes,
        edges=edges,
        graph_hints=[],
        review_notes=[],
        confidence=0.9,
    )


class TestComponentGraphInputBuilderMaterial1:
    def test_basic_material1(self):
        comps = [
            _make_component("comp_001", "Component A", linked_claim_ids=["claim:b1:s1"]),
            _make_component("comp_002", "Component B"),
        ]
        assembly = _make_assembly(comps)
        builder = ComponentGraphInputBuilder()
        inp = builder.build(assembly)
        assert len(inp.components) == 2
        assert inp.components[0]["component_id"] == "comp_001"
        assert inp.components[0]["linked_claim_ids"] == ["claim:b1:s1"]

    def test_component_ids_list(self):
        comps = [_make_component("c1", "L1"), _make_component("c2", "L2")]
        assembly = _make_assembly(comps)
        builder = ComponentGraphInputBuilder()
        inp = builder.build(assembly)
        assert inp.component_ids == ["c1", "c2"]

    def test_max_components_limit(self):
        comps = [_make_component(f"c{i}", f"L{i}") for i in range(50)]
        assembly = _make_assembly(comps)
        builder = ComponentGraphInputBuilder()
        inp = builder.build(assembly, config={"max_components": 5})
        assert len(inp.components) == 5


class TestComponentGraphInputBuilderMaterial2:
    def test_cross_component_dsl_edges(self):
        comp_a = _make_component("comp_001", "A", linked_dsl_node_ids=["n1"])
        comp_b = _make_component("comp_002", "B", linked_dsl_node_ids=["n2"])
        assembly = _make_assembly([comp_a, comp_b])

        nodes = [_make_dsl_node("n1"), _make_dsl_node("n2")]
        edges = [_make_dsl_edge("e1", "n1", "n2")]
        dsl = _make_dsl(nodes, edges)

        builder = ComponentGraphInputBuilder()
        inp = builder.build(assembly, dsl=dsl)
        assert len(inp.cross_component_dsl_edges) == 1
        cross = inp.cross_component_dsl_edges[0]
        assert cross["from_component_id"] == "comp_001"
        assert cross["to_component_id"] == "comp_002"

    def test_intra_component_dsl_edge_excluded(self):
        comp_a = _make_component("comp_001", "A", linked_dsl_node_ids=["n1", "n2"])
        assembly = _make_assembly([comp_a])

        nodes = [_make_dsl_node("n1"), _make_dsl_node("n2")]
        edges = [_make_dsl_edge("e1", "n1", "n2")]
        dsl = _make_dsl(nodes, edges)

        builder = ComponentGraphInputBuilder()
        inp = builder.build(assembly, dsl=dsl)
        assert inp.cross_component_dsl_edges == []

    def test_no_dsl(self):
        comp_a = _make_component("comp_001", "A")
        assembly = _make_assembly([comp_a])
        builder = ComponentGraphInputBuilder()
        inp = builder.build(assembly, dsl=None)
        assert inp.cross_component_dsl_edges == []


class TestComponentGraphInputBuilderMaterial3:
    def _make_derivation(self, doc_id: str, linked_comp_ids: list[str]) -> DerivationChainRecord:
        step = DerivationStep(
            step_id="step_001",
            input_equation_ids=["eq_1"],
            operation="transform",
            output_equation_ids=["eq_2"],
            required_claim_ids=["claim:b1:s1"],
        )
        return DerivationChainRecord(
            derivation_id="deriv_001",
            document_id=doc_id,
            source_section_ids=["sec_1"],
            steps=[step],
            linked_component_ids=linked_comp_ids,
        )

    def test_multi_component_derivation(self):
        comps = [_make_component("c1", "C1"), _make_component("c2", "C2")]
        assembly = _make_assembly(comps)
        chain = self._make_derivation("doc_test", ["c1", "c2"])
        derivations = DerivationChainResult(
            document_id="doc_test", cartridge_id=None, chains=[chain]
        )
        builder = ComponentGraphInputBuilder()
        inp = builder.build(assembly, derivations=derivations)
        assert len(inp.multi_component_derivations) == 1
        assert set(inp.multi_component_derivations[0]["linked_component_ids"]) == {"c1", "c2"}

    def test_single_component_derivation_excluded(self):
        comps = [_make_component("c1", "C1")]
        assembly = _make_assembly(comps)
        chain = self._make_derivation("doc_test", ["c1"])
        derivations = DerivationChainResult(
            document_id="doc_test", cartridge_id=None, chains=[chain]
        )
        builder = ComponentGraphInputBuilder()
        inp = builder.build(assembly, derivations=derivations)
        assert inp.multi_component_derivations == []


class TestComponentGraphInputBuilderMaterial4:
    def test_claim_texts_populated(self):
        comp_a = _make_component("comp_001", "A", linked_claim_ids=["claim:b1:s1"])
        assembly = _make_assembly([comp_a])
        claims = [{"claim_id": "claim:b1:s1", "text": "Heavy quark limit is assumed."}]
        builder = ComponentGraphInputBuilder()
        inp = builder.build(assembly, claims=claims)
        assert "claim:b1:s1" in inp.claim_texts
        assert "Heavy quark" in inp.claim_texts["claim:b1:s1"]

    def test_no_claims(self):
        comp_a = _make_component("comp_001", "A")
        assembly = _make_assembly([comp_a])
        builder = ComponentGraphInputBuilder()
        inp = builder.build(assembly, claims=None)
        assert inp.claim_texts == {}


class TestBuildNodes:
    def test_nodes_order(self):
        comps = [
            _make_component("c1", "L1"),
            _make_component("c2", "L2"),
            _make_component("c3", "L3"),
        ]
        assembly = _make_assembly(comps)
        builder = ComponentGraphInputBuilder()
        nodes = builder.build_nodes(assembly)
        assert [n.component_id for n in nodes] == ["c1", "c2", "c3"]
        assert nodes[0].display_order == 0
        assert nodes[2].display_order == 2
