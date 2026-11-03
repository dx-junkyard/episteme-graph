"""claim チェーンでも step ↔ component が結び付くこと（縦の糸 / 実データ由来の是正）。

実測（2026-09-19・2609.* 10 本）では、``linked_component_ids`` が全 detail ノードで空だった。
``_linked_components_for_step`` の突合が derivation_id / step_id / 式 ID しか見ておらず、
式を持たない claim チェーンでは常に空になるためで、component 側には ``linked_claim_ids`` が
入っていた。
"""
from __future__ import annotations

from episteme_graph.agents.component_assembly.schema import (
    ComponentAssemblyResult,
    ComponentRecord,
)
from episteme_graph.agents.component_graph.normalizer import ComponentGraphNormalizer
from episteme_graph.agents.component_graph.schema import (
    GRAPH_LAYER_EQUATION_DETAIL,
    GRAPH_LAYER_MAIN,
    GRAPH_SCHEMA_VERSION,
    RECONSTRUCTION_BACKED_STEP_REASON,
    ComponentGraphResult,
    derivation_step_ref,
)
from episteme_graph.agents.derivation_chain.schema import (
    DerivationChainResult,
    DerivationChainRecord,
    DerivationStep,
)


def _components(**overrides) -> ComponentAssemblyResult:
    defaults = dict(
        component_id="comp_001",
        component_type="RelationComponent",
        label="Observable basis",
        summary="…",
        inputs=[],
        outputs=[],
        preconditions=[],
        cautions=[],
        dependencies=[],
        evidence_refs={},
        reason="component",
        confidence=0.8,
        review_notes=[],
    )
    defaults.update(overrides)
    return ComponentAssemblyResult(
        document_id="doc",
        components_version="v1",
        cartridge_id=None,
        components=[ComponentRecord(**defaults)],
        assembly_hints=[],
        review_notes=[],
        confidence=0.8,
    )


def _step(step_id, operation, **kwargs):
    """`DerivationStep` は式 ID が必須引数なので、既定で空リストを与える薄いヘルパ。"""
    kwargs.setdefault("input_equation_ids", [])
    kwargs.setdefault("output_equation_ids", [])
    return DerivationStep(
        step_id=step_id,
        operation=operation,
        review_status="teacher_review_required",
        **kwargs,
    )


def _chain(steps: list[DerivationStep], derivation_id: str = "derivation_claim_0001"):
    return DerivationChainResult(
        document_id="doc",
        cartridge_id=None,
        chains=[
            DerivationChainRecord(
                derivation_id=derivation_id,
                document_id="doc",
                source_section_ids=[],
                steps=steps,
            )
        ],
    )


def _normalize(components, derivations):
    base = ComponentGraphResult(
        document_id="doc",
        graph_schema_version=GRAPH_SCHEMA_VERSION,
        cartridge_id=None,
        nodes=[],
        edges=[],
        review_notes=[],
        confidence=0.0,
    )
    return ComponentGraphNormalizer().normalize(base, components, derivations, {})


class TestClaimBackedComponentLinking:
    def test_step_claims_link_to_a_component_with_the_same_claims(self):
        components = _components(linked_claim_ids=["claim_a", "claim_b"])
        derivations = _chain([
            _step(
                "step_001",
                "derive_result",
                input_claim_ids=["claim_a"],
                output_claim_ids=["claim_c"],
            )
        ])
        out = _normalize(components, derivations)
        detail = [n for n in out.nodes if n.graph_layer == GRAPH_LAYER_EQUATION_DETAIL]
        assert detail, "claim だけの chain でも detail ノードは立つ"
        assert detail[0].linked_component_ids == ["comp_001"]
        # 部品へ降りる入口（#422: parent は component_assembly の ID）。
        assert detail[0].parent_component_id == "comp_001"

    def test_main_node_gets_a_representative_component(self):
        components = _components(linked_claim_ids=["claim_a"])
        derivations = _chain([
            _step(
                "step_001",
                "derive_result",
                input_claim_ids=["claim_a"],
                output_claim_ids=["claim_b"],
            )
        ])
        out = _normalize(components, derivations)
        main = [n for n in out.nodes if n.graph_layer == GRAPH_LAYER_MAIN]
        assert main and main[0].representative_component_id == "comp_001"

    def test_a_step_without_shared_claims_links_nothing(self):
        """推測で結ばない（claim が重ならなければ component は付かない）。"""
        components = _components(linked_claim_ids=["claim_x"])
        derivations = _chain([
            _step(
                "step_001",
                "derive_result",
                input_claim_ids=["claim_a"],
                output_claim_ids=["claim_b"],
            )
        ])
        out = _normalize(components, derivations)
        detail = [n for n in out.nodes if n.graph_layer == GRAPH_LAYER_EQUATION_DETAIL]
        assert detail[0].linked_component_ids == []

    def test_equation_and_derivation_matches_still_work(self):
        """既存の3経路（chain 宣言 / derivation_id / 式 ID）は非改変。"""
        components = _components(linked_equation_ids=["eq_1"])
        derivations = _chain([
            _step(
                "step_001",
                "derive_result",
                input_equation_ids=["eq_1"],
                output_equation_ids=["eq_2"],
            )
        ])
        out = _normalize(components, derivations)
        detail = [n for n in out.nodes if n.graph_layer == GRAPH_LAYER_EQUATION_DETAIL]
        assert detail[0].linked_component_ids == ["comp_001"]


class TestDerivationStepReferences:
    def test_bare_and_composite_step_ids_are_both_emitted(self):
        components = _components(linked_claim_ids=["claim_a"])
        derivations = _chain([
            _step(
                "step_001",
                "derive_result",
                input_claim_ids=["claim_a"],
                output_claim_ids=["claim_b"],
            )
        ])
        out = _normalize(components, derivations)
        detail = [n for n in out.nodes if n.graph_layer == GRAPH_LAYER_EQUATION_DETAIL][0]
        assert "step_001" in detail.linked_derivation_ids
        assert "derivation_claim_0001:step_001" in detail.linked_derivation_ids
        assert derivation_step_ref("derivation_claim_0001", "step_001") == \
            "derivation_claim_0001:step_001"

    def test_edges_carry_the_composite_reference(self):
        components = _components(linked_claim_ids=["claim_a"])
        derivations = _chain([
            _step(
                "step_001",
                "define_basis",
                output_claim_ids=["claim_a"],
            ),
            _step(
                "step_002",
                "derive_result",
                input_claim_ids=["claim_a"],
                output_claim_ids=["claim_b"],
            ),
        ])
        out = _normalize(components, derivations)
        detail_edges = [e for e in out.edges if e.graph_layer == GRAPH_LAYER_EQUATION_DETAIL]
        assert detail_edges
        assert "derivation_claim_0001:step_001" in detail_edges[0].evidence_derivation_ids

    def test_ref_falls_back_when_one_side_is_missing(self):
        assert derivation_step_ref("", "step_001") == "step_001"
        assert derivation_step_ref("chain", "") == "chain"


class TestEdgeGraphLayer:
    def test_every_edge_declares_a_layer(self):
        components = _components(linked_claim_ids=["claim_a"])
        derivations = _chain([
            _step(
                "step_001",
                "define_basis",
                output_claim_ids=["claim_a"],
            ),
            _step(
                "step_002",
                "derive_result",
                input_claim_ids=["claim_a"],
                output_claim_ids=["claim_b"],
            ),
        ])
        out = _normalize(components, derivations)
        assert out.edges
        assert all(e.graph_layer for e in out.edges)
        payload = out.to_graph_payload()
        assert all("graph_layer" in e for e in payload["edges"])

    def test_main_edges_are_main_layer(self):
        components = _components(linked_claim_ids=["claim_a"])
        derivations = _chain([
            _step(
                "step_001",
                "define_basis",
                output_claim_ids=["claim_a"],
            ),
            _step(
                "step_002",
                "derive_result",
                input_claim_ids=["claim_a"],
                output_claim_ids=["claim_b"],
            ),
        ])
        out = _normalize(components, derivations)
        main_edges = [
            e for e in out.edges
            if e.source.startswith("theory_op_") and e.target.startswith("theory_op_")
        ]
        assert main_edges
        assert {e.graph_layer for e in main_edges} == {GRAPH_LAYER_MAIN}


class TestReconstructedEquationBacking:
    """復元由来の式しか根拠が無い箇所を確定（source_backed）に上げない（原則1）。

    目印は DerivationChainAgent が step の ``review_reason`` に書く
    ``RECONSTRUCTION_BACKED_REASON``（式側の
    ``confidence_policy.must_not_treat_as_source_extracted`` と同じ事実）。
    """

    @staticmethod
    def _chain_with(reason_of_first: str):
        return _chain([
            _step(
                "step_001",
                "define_basis",
                input_equation_ids=["eq_1"],
                output_equation_ids=["eq_2"],
                source_evidence_ids=["ev_1"],
                review_reason=reason_of_first,
            ),
            _step(
                "step_002",
                "derive_result",
                input_equation_ids=["eq_2"],
                output_equation_ids=["eq_3"],
                source_evidence_ids=["ev_2"],
                review_reason=reason_of_first,
            ),
        ])

    def test_extracted_equations_still_confirm(self):
        out = _normalize(_components(), self._chain_with(""))
        detail = [n for n in out.nodes if n.graph_layer == GRAPH_LAYER_EQUATION_DETAIL]
        assert {n.source_backing_status for n in detail} == {"source_backed"}

    def test_reconstructed_equations_cap_the_node(self):
        out = _normalize(_components(), self._chain_with(RECONSTRUCTION_BACKED_STEP_REASON))
        detail = [n for n in out.nodes if n.graph_layer == GRAPH_LAYER_EQUATION_DETAIL]
        assert {n.source_backing_status for n in detail} == {"partially_source_backed"}
        for node in detail:
            assert RECONSTRUCTION_BACKED_STEP_REASON in node.review_reasons

    def test_main_node_is_capped_too(self):
        out = _normalize(_components(), self._chain_with(RECONSTRUCTION_BACKED_STEP_REASON))
        main = [n for n in out.nodes if n.graph_layer == GRAPH_LAYER_MAIN]
        assert main
        for node in main:
            assert node.source_backing_status != "source_backed"
            assert RECONSTRUCTION_BACKED_STEP_REASON in node.review_reasons

    def test_edges_are_capped_too(self):
        out = _normalize(_components(), self._chain_with(RECONSTRUCTION_BACKED_STEP_REASON))
        assert out.edges
        for edge in out.edges:
            assert edge.source_backing_status != "source_backed"
            assert RECONSTRUCTION_BACKED_STEP_REASON in edge.review_reasons

    def test_one_extracted_step_keeps_the_group_confirmable(self):
        derivations = _chain([
            _step(
                "step_001",
                "define_basis",
                input_equation_ids=["eq_1"],
                output_equation_ids=["eq_2"],
                source_evidence_ids=["ev_1"],
                review_reason=RECONSTRUCTION_BACKED_STEP_REASON,
            ),
            _step(
                "step_002",
                "state_assumption",
                input_equation_ids=["eq_2"],
                output_equation_ids=["eq_3"],
                source_evidence_ids=["ev_2"],
            ),
        ])
        out = _normalize(_components(), derivations)
        main = [n for n in out.nodes if n.graph_layer == GRAPH_LAYER_MAIN]
        # 同じ stage に抽出済みの step が混ざるグループは従来どおり確定できる。
        assert any(n.source_backing_status == "source_backed" for n in main)

    def test_steps_without_equations_are_unaffected(self):
        derivations = _chain([
            _step(
                "step_001",
                "derive_result",
                input_claim_ids=["claim_a"],
                output_claim_ids=["claim_b"],
                review_reason=RECONSTRUCTION_BACKED_STEP_REASON,
            )
        ])
        out = _normalize(_components(linked_claim_ids=["claim_a"]), derivations)
        detail = [n for n in out.nodes if n.graph_layer == GRAPH_LAYER_EQUATION_DETAIL]
        # 式の根拠が無いノードはそもそも source_backed にならない（判定は不変）。
        assert RECONSTRUCTION_BACKED_STEP_REASON not in detail[0].review_reasons

    def test_marker_matches_the_derivation_agent(self):
        from episteme_graph.agents.derivation_chain.agent import RECONSTRUCTION_BACKED_REASON

        assert RECONSTRUCTION_BACKED_STEP_REASON == RECONSTRUCTION_BACKED_REASON

    def test_reason_is_in_the_vocabulary(self):
        from episteme_graph.agents.component_graph.schema import REVIEW_REASONS

        assert RECONSTRUCTION_BACKED_STEP_REASON in REVIEW_REASONS
