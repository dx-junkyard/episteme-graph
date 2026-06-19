"""Tests for revision operations + candidate assembly (#405)."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.document_pipeline.revision import (  # noqa: E402
    OPERATIONS,
    apply_operations,
    make_operation,
)
from core.document_pipeline.revision import coordinator  # noqa: E402


def _base_artifacts():
    return {
        "claim_object_builder": {"claims": [
            {"claim_id": "clm_1", "text": "A and B hold.", "is_atomic": False,
             "equation_ids": ["eq_1"], "source_evidence_ids": ["ev_1"],
             "linked_component_ids": ["cmp_1"]},
            {"claim_id": "clm_2", "text": "C holds.", "is_atomic": True,
             "source_evidence_ids": ["ev_1"]},
        ]},
        "equation_semantics": {"equations": [
            {"equation_id": "eq_1", "label": "(1)", "reconstruction": {"latex": "a=b"}},
        ]},
        "evidence_registry": {"records": [
            {"evidence_id": "ev_1", "evidence_text": "quote"},
            {"evidence_id": "ev_2", "evidence_text": "quote2"},
        ]},
        "component_assembly": {"components": [
            {"component_id": "cmp_1", "label": "Comp 1",
             "linked_claim_ids": ["clm_1"], "linked_equation_ids": ["eq_1"]},
        ]},
        "component_graph": {
            "nodes": [
                {"node_id": "cmp_1", "label": "Comp 1", "graph_layer": "main"},
            ],
            "edges": [
                {"edge_id": "edge_1", "source": "cmp_1", "target": "cmp_1",
                 "edge_type": "RELATED_TO"},
            ],
        },
        "derivation_chain": {"chains": [
            {"derivation_id": "der_1", "steps": [
                {"input_equation_ids": ["eq_1"], "output_equation_ids": ["eq_1"]}]},
        ]},
    }


def _claims(result):
    return {c["claim_id"]: c for c in result["candidate_artifacts"]["claim_object_builder"]["claims"]}


# --- schema ----------------------------------------------------------------

def test_make_operation_rejects_unknown():
    with pytest.raises(ValueError):
        make_operation(operation="frobnicate", target_type="claim")
    op = make_operation(operation="update_entity", target_type="claim", target_id="clm_1")
    assert op["validation_result"] == {"status": "pending"}
    assert set(("operation_id", "before_json", "after_json", "reason",
                "checkpoint_ids", "evidence_refs", "source_locations",
                "confidence", "protected_target")) <= set(op)


def test_all_operations_listed():
    assert set(OPERATIONS) == {
        "add_entity", "update_entity", "split_entity", "merge_entities",
        "remove_entity", "add_relation", "remove_relation", "relink_evidence",
    }


# --- base immutability ------------------------------------------------------

def test_base_artifacts_not_mutated():
    base = _base_artifacts()
    snapshot = copy.deepcopy(base)
    apply_operations(base, [
        make_operation(operation="update_entity", target_type="claim",
                       target_id="clm_1", after_json={"text": "changed"}),
    ])
    assert base == snapshot  # base is immutable input


def test_untargeted_entities_unchanged():
    base = _base_artifacts()
    result = apply_operations(base, [
        make_operation(operation="update_entity", target_type="claim",
                       target_id="clm_1", after_json={"text": "changed"}),
    ])
    claims = _claims(result)
    assert claims["clm_1"]["text"] == "changed"
    # clm_2 identical to base
    assert claims["clm_2"] == {"claim_id": "clm_2", "text": "C holds.",
                               "is_atomic": True, "source_evidence_ids": ["ev_1"]}


# --- individual operations --------------------------------------------------

def test_add_entity_and_remove_entity():
    base = _base_artifacts()
    res = apply_operations(base, [
        make_operation(operation="add_entity", target_type="evidence",
                       after_json={"evidence_id": "ev_3", "evidence_text": "new"}),
    ])
    assert not res["invalid"]
    ids = {e["evidence_id"] for e in res["candidate_artifacts"]["evidence_registry"]["records"]}
    assert "ev_3" in ids

    res2 = apply_operations(base, [
        make_operation(operation="remove_entity", target_type="evidence", target_id="ev_2"),
    ])
    ids2 = {e["evidence_id"] for e in res2["candidate_artifacts"]["evidence_registry"]["records"]}
    assert "ev_2" not in ids2
    assert not res2["invalid"]


def test_split_entity_records_mapping_and_follows_refs():
    base = _base_artifacts()
    res = apply_operations(base, [
        make_operation(operation="split_entity", target_type="claim", target_id="clm_1",
                       after_json=[
                           {"claim_id": "clm_1a", "text": "A holds.", "is_atomic": True},
                           {"claim_id": "clm_1b", "text": "B holds.", "is_atomic": True},
                       ]),
    ])
    assert not res["invalid"], res["operation_errors"]
    assert res["id_mapping"]["clm_1"] == ["clm_1a", "clm_1b"]
    claims = _claims(res)
    assert "clm_1" not in claims and {"clm_1a", "clm_1b"} <= set(claims)
    # component reference to clm_1 followed to both new ids
    comp = res["candidate_artifacts"]["component_assembly"]["components"][0]
    assert comp["linked_claim_ids"] == ["clm_1a", "clm_1b"]


def test_merge_entities_maps_refs_to_merged():
    base = _base_artifacts()
    res = apply_operations(base, [
        make_operation(operation="merge_entities", target_type="evidence",
                       target_id="ev_1", source_ids=["ev_1", "ev_2"],
                       after_json={"evidence_id": "ev_merged", "evidence_text": "m"}),
    ])
    assert not res["invalid"], res["operation_errors"]
    assert res["id_mapping"]["ev_1"] == ["ev_merged"]
    # claim evidence refs now point at merged id
    claims = _claims(res)
    assert claims["clm_1"]["source_evidence_ids"] == ["ev_merged"]


def test_remove_entity_prunes_references_and_drops_edges():
    base = _base_artifacts()
    res = apply_operations(base, [
        make_operation(operation="remove_entity", target_type="component", target_id="cmp_1"),
    ])
    assert not res["invalid"], res["operation_errors"]
    # claim's linked_component_ids no longer references cmp_1
    claims = _claims(res)
    assert "cmp_1" not in claims["clm_1"]["linked_component_ids"]
    # graph edge whose endpoint was the removed component-node is dropped only if
    # the node was also removed; here only the component entity was removed, the
    # graph node remains, so edge stays. Removing the node removes the edge:
    res2 = apply_operations(base, [
        make_operation(operation="remove_entity", target_type="graph_node", target_id="cmp_1"),
    ])
    assert "edge_1" in res2["dropped_edges"]


def test_relink_evidence():
    base = _base_artifacts()
    res = apply_operations(base, [
        make_operation(operation="relink_evidence", target_type="claim",
                       target_id="clm_2", evidence_refs=["ev_2"]),
    ])
    claims = _claims(res)
    assert claims["clm_2"]["source_evidence_ids"] == ["ev_2"]


def test_add_and_remove_relation():
    base = _base_artifacts()
    res = apply_operations(base, [
        make_operation(operation="add_relation", target_type="claim", target_id="clm_2",
                       relation_target_type="evidence", relation_target_id="ev_2"),
    ])
    claims = _claims(res)
    assert "ev_2" in claims["clm_2"]["source_evidence_ids"]

    res2 = apply_operations(base, [
        make_operation(operation="remove_relation", target_type="claim", target_id="clm_1",
                       relation_target_type="evidence", relation_target_id="ev_1"),
    ])
    claims2 = _claims(res2)
    assert "ev_1" not in claims2["clm_1"]["source_evidence_ids"]


# --- safety: invalid candidate ---------------------------------------------

def test_new_unknown_reference_marks_candidate_invalid():
    base = _base_artifacts()
    res = apply_operations(base, [
        make_operation(operation="add_relation", target_type="claim", target_id="clm_2",
                       relation_target_type="evidence", relation_target_id="ev_DOESNOTEXIST"),
    ])
    assert res["invalid"] is True
    assert any(e.get("error") == "new_unknown_references" for e in res["operation_errors"])


def test_failed_operation_marks_invalid_without_partial_active():
    base = _base_artifacts()
    res = apply_operations(base, [
        make_operation(operation="update_entity", target_type="claim",
                       target_id="clm_MISSING", after_json={"text": "x"}),
    ])
    assert res["invalid"] is True
    failed = [o for o in res["applied_operations"] if o["validation_result"]["status"] == "failed"]
    assert failed


def test_pre_existing_unresolved_ref_does_not_invalidate():
    base = _base_artifacts()
    # clm_2 already references a missing equation in base; inventory says so.
    base["claim_object_builder"]["claims"][1]["equation_ids"] = ["eq_missing"]
    inventory = {"unresolved_references": [
        {"source_id": "clm_2", "ref_field": "equation_ids", "target_id": "eq_missing"},
    ]}
    res = apply_operations(base, [
        make_operation(operation="update_entity", target_type="claim",
                       target_id="clm_1", after_json={"text": "x"}),
    ], base_inventory=inventory)
    assert res["invalid"] is False
    assert any(d["target_id"] == "eq_missing" for d in res["carried_unresolved_references"])


# --- protected target ------------------------------------------------------

def test_protected_target_flagged_not_auto_adoptable():
    base = _base_artifacts()
    res = apply_operations(base, [
        make_operation(operation="update_entity", target_type="claim", target_id="clm_1",
                       after_json={"text": "x"}, protected_target=True),
    ])
    assert res["requires_confirmation"] is True
    assert res["protected_changes"] and res["protected_changes"][0]["target_id"] == "clm_1"


# --- determinism -----------------------------------------------------------

def test_apply_is_deterministic_regardless_of_input_order():
    base = _base_artifacts()
    ops = [
        make_operation(operation="add_relation", target_type="claim", target_id="clm_2",
                       relation_target_type="evidence", relation_target_id="ev_2",
                       operation_id="op_b"),
        make_operation(operation="remove_entity", target_type="evidence", target_id="ev_1",
                       operation_id="op_a"),
        make_operation(operation="add_entity", target_type="evidence",
                       after_json={"evidence_id": "ev_9", "evidence_text": "x"},
                       operation_id="op_c"),
    ]
    res1 = apply_operations(base, ops)
    res2 = apply_operations(base, list(reversed(ops)))
    assert res1["candidate_artifacts"] == res2["candidate_artifacts"]


# --- coordinator integration -----------------------------------------------

def test_assemble_candidate_persists_without_touching_projections(monkeypatch):
    run = {"id": "rev-1", "run_type": "revision", "base_run_id": "base-1",
           "document_id": "doc-1",
           "stage_outputs": {"_artifacts": {"baseline_inventory": {"unresolved_references": []}}}}
    base = {"id": "base-1", "run_type": "initial", "document_id": "doc-1",
            "stage_outputs": {"_artifacts": _base_artifacts()}}

    def _get(run_id):
        return run if run_id == "rev-1" else base
    monkeypatch.setattr(coordinator.persistence, "get_analysis_run",
                        lambda *, run_id: _get(run_id))
    captured = {}
    monkeypatch.setattr(coordinator.persistence, "update_revision_status",
                        lambda **k: captured.update(k))
    monkeypatch.setattr(coordinator.persistence, "set_active_analysis_run",
                        lambda **k: pytest.fail("assembly must not change active run"))
    for fn in ("persist_qualified_claims", "persist_components", "persist_component_graph"):
        monkeypatch.setattr(coordinator.persistence, fn,
                            lambda **k: pytest.fail(f"{fn} must not run during assembly"))

    out = coordinator.assemble_candidate(run_id="rev-1", operations=[
        make_operation(operation="update_entity", target_type="claim",
                       target_id="clm_1", after_json={"text": "patched"}),
    ])
    assert out["invalid"] is False
    assert captured["revision_status"] == "proposed"
    arts = captured["stage_outputs"]["_artifacts"]
    assert "candidate" in arts and "revision_operations" in arts
    assert arts["candidate"]["candidate_artifacts"]["claim_object_builder"]["claims"][0]["text"] == "patched"
