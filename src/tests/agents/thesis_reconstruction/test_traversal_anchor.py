"""Issue #442: thesis_reconstruction emits traversal-anchor fields and links to
the DSL graph deterministically. Two unrelated id sets, no domain asserts.
"""
from __future__ import annotations

import pytest

from episteme_graph.agents.dsl_linking.schema import DSLLinkingResult, DSLNode
from episteme_graph.agents.thesis_reconstruction.anchor_linker import link_thesis_anchors
from episteme_graph.agents.thesis_reconstruction.schema import ThesisReconstructionResult


def _thesis(claim_ids, equation_ids=(), support_claim_ids=()):
    return ThesisReconstructionResult(
        document_id="doc", thesis_version="v1", cartridge_id=None,
        central_thesis={
            "text": "headline", "claim_ids": list(claim_ids),
            "equation_ids": list(equation_ids), "evidence_block_ids": [],
            "reason": "r", "confidence": 0.9,
        },
        alternative_theses=[],
        support_structure={"direct_supports": [
            {"text": "s", "claim_ids": list(support_claim_ids), "equation_ids": [],
             "support_type": "result_support", "reason": "r", "confidence": 0.8},
        ]},
        excluded_from_core=[], thesis_graph_hints=[], review_notes=[], confidence=0.9,
    )


def _node(node_id, claim_ids=(), equation_ids=(), source_kind="claim", node_type="Relation"):
    return DSLNode(
        node_id=node_id, node_type=node_type, node_value=f"v_{node_id}",
        source_kind=source_kind,
        source_refs={"claim_ids": list(claim_ids), "equation_ids": list(equation_ids),
                     "thesis_refs": []},
        reason="r", confidence=0.8,
    )


_DOMAINS = {
    "domain_a": {"head": ["claim_a"], "support": ["claim_a2"], "node_claim": "claim_a"},
    "domain_b": {"head": ["C_one"], "support": ["C_two"], "node_claim": "C_one"},
}


@pytest.mark.parametrize("domain", sorted(_DOMAINS))
def test_finalize_traversal_fields(domain):
    spec = _DOMAINS[domain]
    thesis = _thesis(spec["head"], support_claim_ids=spec["support"])
    thesis.finalize_traversal_fields(central_question_hint="the question")
    assert thesis.headline_claim == "headline"
    assert thesis.central_question == "the question"
    # supporting_subclaim_ids gather central + support claims
    assert set(spec["head"]) <= set(thesis.supporting_subclaim_ids)
    assert set(spec["support"]) <= set(thesis.supporting_subclaim_ids)


@pytest.mark.parametrize("domain", sorted(_DOMAINS))
def test_link_thesis_anchors_sets_both_sides(domain):
    spec = _DOMAINS[domain]
    thesis = _thesis(spec["head"], support_claim_ids=spec["support"])
    thesis.finalize_traversal_fields()
    nodes = [
        _node("n1", claim_ids=[spec["node_claim"]]),
        _node("n2", claim_ids=["unrelated"]),
    ]
    dsl = DSLLinkingResult("doc", "v1", nodes, [], [], [], 0.8)
    anchors = link_thesis_anchors(thesis, dsl)

    assert "n1" in anchors
    assert thesis.anchor_node_ids == anchors
    # bidirectional: the matched node carries the flag
    assert dsl.nodes[0].is_thesis_anchor is True
    assert dsl.nodes[1].is_thesis_anchor is False


def test_link_thesis_anchors_falls_back_to_thesis_nodes():
    thesis = _thesis(["claim_x"])
    thesis.finalize_traversal_fields()
    # No node shares the thesis claim, but a thesis-provenance node exists.
    nodes = [
        _node("n1", claim_ids=["other"]),
        _node("nt", source_kind="thesis", node_type="ThesisProxy"),
    ]
    dsl = DSLLinkingResult("doc", "v1", nodes, [], [], [], 0.8)
    anchors = link_thesis_anchors(thesis, dsl)
    assert "nt" in anchors


def test_link_thesis_anchors_tolerates_none():
    assert link_thesis_anchors(None, None) == []
