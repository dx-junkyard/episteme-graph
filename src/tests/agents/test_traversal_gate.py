"""ExportValidationGate traversal checks for issues #441 (typed/evidenced DSL
edges) and #442 (thesis traversal anchor).

Two unrelated id sets exercise the checks without asserting domain-specific
verbs, ids, or claim text.
"""
from __future__ import annotations

import importlib.util
import os
import sys

_GATE_PATH = os.path.join(
    os.path.dirname(__file__),
    "../../../backend/core/document_pipeline/export_validation_gate.py",
)
_spec = importlib.util.spec_from_file_location("export_validation_gate_traversal", _GATE_PATH)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["export_validation_gate_traversal"] = _mod
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]

ExportValidationGate = _mod.ExportValidationGate


# --------------------------------------------------------------------------- #
# Stubs
# --------------------------------------------------------------------------- #

class _Node:
    def __init__(self, node_id, claim_ids=(), equation_ids=(), is_thesis_anchor=False,
                 source_kind="claim", node_type="Relation"):
        self.node_id = node_id
        self.source_refs = {
            "claim_ids": list(claim_ids), "equation_ids": list(equation_ids),
            "thesis_refs": [],
        }
        self.is_thesis_anchor = is_thesis_anchor
        self.source_kind = source_kind
        self.node_type = node_type


class _Edge:
    def __init__(self, edge_id, from_node_id, to_node_id, edge_type="REQUIRES",
                 evidence_refs=None):
        self.edge_id = edge_id
        self.from_node_id = from_node_id
        self.to_node_id = to_node_id
        self.edge_type = edge_type
        self.evidence_refs = evidence_refs if evidence_refs is not None else {
            "claim_ids": [], "equation_ids": [], "thesis_refs": [],
        }


class _Dsl:
    def __init__(self, nodes, edges):
        self.nodes = nodes
        self.edges = edges


class _Claim:
    def __init__(self, claim_id):
        self.claim_id = claim_id


class _ClaimObjects:
    def __init__(self, claims):
        self.claims = claims


class _EvidenceRecord:
    def __init__(self, evidence_id):
        self.evidence_id = evidence_id


class _Evidence:
    def __init__(self, records):
        self.records = records


def _artifacts(thesis=None, equations=None):
    base = {
        "document_structure": {"blocks": [], "sections": []},
        "source_chunking": [{"text": "c"}],
        "claim_qualification": {"qualified_spans": []},
        "component_assembly": {"components": [], "validation_issues": []},
        "equation_semantics": equations or {"equations": []},
    }
    if thesis is not None:
        base["thesis_reconstruction"] = thesis
    return base


def _run(artifacts, dsl, claim_objects=None, evidence=None):
    return ExportValidationGate().run(
        artifacts=artifacts, component_result=None, course_mapping=None,
        claim_objects=claim_objects, evidence=evidence, dsl=dsl,
    )


def _thesis(claim_ids=("claim_main",), equation_ids=(), anchor_node_ids=()):
    return {
        "document_id": "doc",
        "central_thesis": {
            "text": "central thesis", "claim_ids": list(claim_ids),
            "equation_ids": list(equation_ids), "evidence_block_ids": [],
            "reason": "r", "confidence": 0.9,
        },
        "headline_claim": "central thesis",
        "central_question": "what is asked?",
        "supporting_subclaim_ids": list(claim_ids),
        "anchor_node_ids": list(anchor_node_ids),
        "support_structure": {},
        "validation_issues": [],
    }


# --------------------------------------------------------------------------- #
# #441 — typed + evidenced edges
# --------------------------------------------------------------------------- #

def test_untyped_edge_is_error():
    dsl = _Dsl([_Node("n1", claim_ids=["c1"]), _Node("n2", claim_ids=["c2"])],
               [_Edge("e1", "n1", "n2", edge_type="",
                      evidence_refs={"claim_ids": ["c1"], "equation_ids": [], "thesis_refs": []})])
    result = _run(_artifacts(thesis=_thesis(anchor_node_ids=["n1"])), dsl,
                  claim_objects=_ClaimObjects([_Claim("c1"), _Claim("c2")]),
                  evidence=_Evidence([]))
    assert "DSL_EDGE_UNTYPED" in {e.code for e in result.errors}


def test_edge_without_evidence_is_error():
    dsl = _Dsl([_Node("n1"), _Node("n2")],
               [_Edge("e1", "n1", "n2", edge_type="REQUIRES",
                      evidence_refs={"claim_ids": [], "equation_ids": [], "thesis_refs": []})])
    result = _run(_artifacts(thesis=_thesis(anchor_node_ids=["n1"])), dsl,
                  claim_objects=_ClaimObjects([]), evidence=_Evidence([]))
    assert "DSL_EDGE_NO_EVIDENCE" in {e.code for e in result.errors}


def test_dangling_evidence_ref_is_error():
    dsl = _Dsl([_Node("n1", claim_ids=["c1"]), _Node("n2", claim_ids=["c2"])],
               [_Edge("e1", "n1", "n2", edge_type="REQUIRES",
                      evidence_refs={"claim_ids": ["ghost"], "equation_ids": [], "thesis_refs": []})])
    result = _run(_artifacts(thesis=_thesis(anchor_node_ids=["n1"])), dsl,
                  claim_objects=_ClaimObjects([_Claim("c1"), _Claim("c2")]),
                  evidence=_Evidence([]))
    assert "DSL_EDGE_DANGLING_EVIDENCE" in {e.code for e in result.errors}


def test_well_formed_edges_have_no_traversal_errors():
    dsl = _Dsl([_Node("n1", claim_ids=["c1"]), _Node("n2", claim_ids=["c2"])],
               [_Edge("e1", "n1", "n2", edge_type="REQUIRES",
                      evidence_refs={"claim_ids": ["c1"], "equation_ids": [], "thesis_refs": []})])
    result = _run(_artifacts(thesis=_thesis(claim_ids=["c1"], anchor_node_ids=["n1"])), dsl,
                  claim_objects=_ClaimObjects([_Claim("c1"), _Claim("c2")]),
                  evidence=_Evidence([]))
    codes = {e.code for e in result.errors}
    assert "DSL_EDGE_UNTYPED" not in codes
    assert "DSL_EDGE_NO_EVIDENCE" not in codes
    assert "DSL_EDGE_DANGLING_EVIDENCE" not in codes


# --------------------------------------------------------------------------- #
# #442 — thesis traversal anchor
# --------------------------------------------------------------------------- #

def test_missing_thesis_artifact_is_error():
    dsl = _Dsl([_Node("n1", claim_ids=["c1"])], [])
    result = _run(_artifacts(thesis=None), dsl,
                  claim_objects=_ClaimObjects([_Claim("c1")]), evidence=_Evidence([]))
    assert "THESIS_ARTIFACT_MISSING" in {e.code for e in result.errors}


def test_anchor_resolves_and_is_reachable():
    # root n1 -> n2(anchor); anchor resolves and is reachable from a root.
    dsl = _Dsl(
        [_Node("n1", claim_ids=["c1"]), _Node("n2", claim_ids=["c_main"], is_thesis_anchor=True)],
        [_Edge("e1", "n1", "n2", edge_type="REQUIRES",
               evidence_refs={"claim_ids": ["c1"], "equation_ids": [], "thesis_refs": []})],
    )
    thesis = _thesis(claim_ids=["c_main"], anchor_node_ids=["n2"])
    result = _run(_artifacts(thesis=thesis), dsl,
                  claim_objects=_ClaimObjects([_Claim("c1"), _Claim("c_main")]),
                  evidence=_Evidence([]))
    codes = {e.code for e in result.errors} | {r.code for r in result.review_items}
    assert "THESIS_ARTIFACT_MISSING" not in codes
    assert "THESIS_ANCHOR_UNRESOLVED" not in codes
    assert "THESIS_ANCHOR_MISSING" not in codes
    assert "THESIS_ANCHOR_UNREACHABLE" not in codes


def test_unresolved_anchor_is_reported():
    dsl = _Dsl([_Node("n1", claim_ids=["c1"])], [])
    thesis = _thesis(claim_ids=["c1"], anchor_node_ids=["n_ghost"])
    result = _run(_artifacts(thesis=thesis), dsl,
                  claim_objects=_ClaimObjects([_Claim("c1")]), evidence=_Evidence([]))
    assert "THESIS_ANCHOR_UNRESOLVED" in {r.code for r in result.review_items}


def test_anchor_unreachable_is_reported():
    # Root n1 -> n2 forms the only root-reachable region. The anchor `a` lives in
    # a separate 2-cycle (a -> b -> a); neither has in-degree 0, so no root can
    # reach it.
    ev = {"claim_ids": ["c1"], "equation_ids": [], "thesis_refs": []}
    dsl = _Dsl(
        [
            _Node("n1", claim_ids=["c1"]),
            _Node("n2", claim_ids=["c2"]),
            _Node("a", claim_ids=["c_main"], is_thesis_anchor=True),
            _Node("b", claim_ids=["c3"]),
        ],
        [
            _Edge("e1", "n1", "n2", edge_type="REQUIRES", evidence_refs=ev),
            _Edge("e2", "a", "b", edge_type="REQUIRES", evidence_refs=ev),
            _Edge("e3", "b", "a", edge_type="REQUIRES", evidence_refs=ev),
        ],
    )
    thesis = _thesis(claim_ids=["c_main"], anchor_node_ids=["a"])
    result = _run(_artifacts(thesis=thesis), dsl,
                  claim_objects=_ClaimObjects([_Claim("c1"), _Claim("c2"),
                                               _Claim("c3"), _Claim("c_main")]),
                  evidence=_Evidence([]))
    assert "THESIS_ANCHOR_UNREACHABLE" in {r.code for r in result.review_items}
