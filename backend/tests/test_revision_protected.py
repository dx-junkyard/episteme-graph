"""#410 P1-3: protected-change detection is server-side and cannot be bypassed."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.document_pipeline.revision import apply_operations, make_operation  # noqa: E402


def _base():
    return {
        "claim_object_builder": {"claims": [
            {"claim_id": "clm_1", "text": "A.", "review_status": "teacher_approved",
             "source_evidence_ids": ["ev_1"]},
            {"claim_id": "clm_2", "text": "B.", "review_status": "source_backed",
             "source_evidence_ids": ["ev_1"]},
        ]},
        "evidence_registry": {"records": [{"evidence_id": "ev_1", "evidence_text": "q"},
                                          {"evidence_id": "ev_2", "evidence_text": "q2"}]},
        "component_assembly": {"components": [
            {"component_id": "cmp_1", "label": "C", "review_status": "manually_edited",
             "linked_claim_ids": ["clm_1"]},
        ]},
    }


def _inventory():
    # server source of truth — derived independently of client operation flags
    return {"protected_decisions": [
        {"entity_type": "claim", "entity_id": "clm_1"},
        {"entity_type": "component", "entity_id": "cmp_1"},
    ]}


def test_protected_detected_even_when_flag_false_or_omitted():
    res = apply_operations(_base(), [
        make_operation(operation="update_entity", target_type="claim", target_id="clm_1",
                       after_json={"text": "edited"}),  # no protected_target at all
    ], base_inventory=_inventory())
    assert res["requires_confirmation"] is True
    ids = {p["target_id"] for p in res["protected_changes"]}
    assert "clm_1" in ids


def test_client_protected_flag_is_not_trusted_for_unprotected_entity():
    # client lies that clm_2 is protected; server knows it is not.
    res = apply_operations(_base(), [
        make_operation(operation="update_entity", target_type="claim", target_id="clm_2",
                       after_json={"text": "x"}, protected_target=True),
    ], base_inventory=_inventory())
    assert res["requires_confirmation"] is False
    assert res["protected_changes"] == []
    # the op's flag was overwritten by the server determination
    assert res["applied_operations"][0]["protected_target"] is False
    assert res["applied_operations"][0]["protected_target_source"] == "server"


def test_inventory_absent_falls_back_to_base_artifact_protection():
    # No inventory passed -> protection derived from base artifact review_status.
    res = apply_operations(_base(), [
        make_operation(operation="update_entity", target_type="component", target_id="cmp_1",
                       after_json={"label": "X"}),
    ])
    assert res["requires_confirmation"] is True
    assert any(p["target_id"] == "cmp_1" for p in res["protected_changes"])


def test_split_with_one_protected_source_requires_confirmation():
    res = apply_operations(_base(), [
        make_operation(operation="split_entity", target_type="claim", target_id="clm_1",
                       after_json=[{"claim_id": "clm_1a", "text": "A1"},
                                   {"claim_id": "clm_1b", "text": "A2"}]),
    ], base_inventory=_inventory())
    assert res["requires_confirmation"] is True


def test_merge_with_protected_source_requires_confirmation():
    res = apply_operations(_base(), [
        make_operation(operation="merge_entities", target_type="claim", target_id="clm_2",
                       source_ids=["clm_2", "clm_1"],
                       after_json={"claim_id": "clm_m", "text": "merged"}),
    ], base_inventory=_inventory())
    assert res["requires_confirmation"] is True
    assert any("clm_1" in (p.get("protected_entity_ids") or []) for p in res["protected_changes"])


def test_relation_change_on_protected_entity_requires_confirmation():
    res = apply_operations(_base(), [
        make_operation(operation="add_relation", target_type="claim", target_id="clm_1",
                       relation_target_type="evidence", relation_target_id="ev_2"),
    ], base_inventory=_inventory())
    assert res["requires_confirmation"] is True


def test_relink_evidence_on_protected_entity_requires_confirmation():
    res = apply_operations(_base(), [
        make_operation(operation="relink_evidence", target_type="claim", target_id="clm_1",
                       evidence_refs=["ev_2"]),
    ], base_inventory=_inventory())
    assert res["requires_confirmation"] is True


def test_add_entity_is_not_a_protected_change():
    res = apply_operations(_base(), [
        make_operation(operation="add_entity", target_type="evidence",
                       after_json={"evidence_id": "ev_9", "evidence_text": "new"}),
    ], base_inventory=_inventory())
    assert res["requires_confirmation"] is False
