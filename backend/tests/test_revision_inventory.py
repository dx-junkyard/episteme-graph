"""Tests for baseline inventory + checkpoint planning (#403)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.document_pipeline.revision import (  # noqa: E402
    build_baseline_inventory,
    build_revision_plan,
    overlay_projection_state,
    plan_checkpoints,
)
from core.document_pipeline.revision import coordinator  # noqa: E402


# --- sample base-run artifacts --------------------------------------------

def _sample_artifacts():
    return {
        "claim_object_builder": {
            "claims": [
                {
                    "claim_id": "clm_1", "text": "A implies B under condition C.",
                    "is_atomic": True, "confidence": 0.9,
                    "support_status": "source_backed", "review_status": "source_backed",
                    "equation_ids": ["eq_1"], "source_evidence_ids": ["ev_1"],
                    "linked_component_ids": ["cmp_1"], "section_id": "s1",
                },
                {
                    "claim_id": "clm_2", "text": "Long multi part claim and another part.",
                    "is_atomic": False, "atomicity": "non_atomic", "confidence": 0.3,
                    "review_status": "teacher_review_required",
                    "equation_ids": ["eq_missing"], "source_evidence_ids": [],
                    "source_scope": {"section_id": "s2", "span_id": "sp_2"},
                },
                {
                    "claim_id": "clm_protected", "text": "Approved claim.",
                    "is_atomic": True, "confidence": 0.95,
                    "review_status": "teacher_approved",
                    "source_evidence_ids": ["ev_1"],
                },
            ]
        },
        "equation_semantics": {
            "equations": [
                {"equation_id": "eq_1", "label": "(1)",
                 "reconstruction": {"latex": "a = b"},
                 "confidence_policy": {"confidence": 0.8, "review_status": "source_backed"},
                 "source_extraction": {"raw_text": "a=b", "source_location": {"page": 2}}},
                {"equation_id": "eq_2", "label": "",
                 "reconstruction": {"latex": ""},
                 "confidence_policy": {"confidence": 0.2, "review_status": "review_required"}},
            ]
        },
        "evidence_registry": {
            "records": [
                {"evidence_id": "ev_1", "evidence_text": "quoted text",
                 "extraction_status": "verbatim", "needs_review": False},
            ]
        },
        "derivation_chain": {
            "chains": [
                {"derivation_id": "der_1", "label": "chain", "review_status": "review_required",
                 "steps": [{"input_equation_ids": ["eq_1"], "output_equation_ids": ["eq_2"]}]},
            ]
        },
        "component_assembly": {
            "components": [
                {"component_id": "cmp_1", "label": "Comp 1",
                 "review_status": "source_backed", "linked_claim_ids": ["clm_1"],
                 "linked_equation_ids": ["eq_1"], "confidence": 0.8,
                 "split_recommendation": {"split_required": True}},
            ]
        },
        "component_graph": {
            "nodes": [
                {"node_id": "cmp_1", "label": "Comp 1", "graph_layer": "main",
                 "component_type": "TheoryOperationNode", "source_backing_status": "source_backed"},
            ],
            "edges": [
                {"edge_id": "edge_1", "source": "cmp_1", "target": "cmp_missing",
                 "edge_type": "REQUIRES", "source_backing_status": "review_required"},
            ],
        },
        "export_validation": {
            "errors": [
                {"code": "MISSING_EVIDENCE", "message": "claim missing evidence",
                 "artifact": "claims", "path": "claims[clm_2].evidence"},
            ],
            "warnings": [],
            "review_items": [],
        },
    }


def test_inventory_indexes_all_entity_types():
    inv = build_baseline_inventory(_sample_artifacts(), base_run_id="base-1", document_id="doc-1")
    assert inv["base_run_id"] == "base-1"
    counts = inv["entity_counts"]
    assert counts["claims"] == 3
    assert counts["equations"] == 2
    assert counts["evidence"] == 1
    assert counts["derivations"] == 1
    assert counts["components"] == 1
    assert counts["graph_nodes"] == 1
    assert counts["graph_edges"] == 1


def test_inventory_resolves_and_records_unresolved_references():
    inv = build_baseline_inventory(_sample_artifacts(), base_run_id="base-1")
    # clm_1 -> eq_1 resolves
    resolved = [(r["source_id"], r["target_id"]) for r in inv["relations"]]
    assert ("clm_1", "eq_1") in resolved
    assert ("clm_1", "ev_1") in resolved
    # clm_2 -> eq_missing is unresolved, never silently dropped
    unresolved = {(u["source_id"], u["target_id"]) for u in inv["unresolved_references"]}
    assert ("clm_2", "eq_missing") in unresolved
    # graph edge -> missing endpoint unresolved
    assert ("edge_1", "cmp_missing") in unresolved


def test_inventory_records_existing_issues_and_protected():
    inv = build_baseline_inventory(_sample_artifacts(), base_run_id="base-1")
    codes = {i["code"] for i in inv["existing_issues"]}
    assert "MISSING_EVIDENCE" in codes
    protected_ids = {p["entity_id"] for p in inv["protected_decisions"]}
    assert "clm_protected" in protected_ids


def test_checkpoints_cover_all_target_types_and_are_deterministic():
    inv = build_baseline_inventory(_sample_artifacts(), base_run_id="base-1")
    cps1 = plan_checkpoints(inv)
    cps2 = plan_checkpoints(inv)
    # deterministic: identical output for identical inventory
    assert cps1 == cps2
    target_types = {c["target_type"] for c in cps1}
    assert {"claim", "equation", "derivation", "component", "graph_edge"} <= target_types
    # every checkpoint carries target + source scope + trigger
    for c in cps1:
        assert c["checkpoint_id"]
        assert c["target_type"]
        assert "source_scope" in c
        assert c["trigger_reason"]
        assert c["status"] == "planned"


def test_checkpoints_include_deterministic_signals():
    inv = build_baseline_inventory(_sample_artifacts(), base_run_id="base-1")
    triggers = {c["trigger_reason"] for c in plan_checkpoints(inv)}
    assert "non_atomic_claim" in triggers
    assert "low_confidence" in triggers
    assert "unresolved_reference" in triggers
    assert "edge_not_source_backed" in triggers
    assert "component_granularity" in triggers
    assert any(t.startswith("validation:") for t in triggers)


def test_canonical_required_key_drives_component_checkpoint():
    # Regression for #417: a component carrying the canonical ``required: true``
    # split-recommendation key (the schema the granularity analyzer actually
    # emits) must still produce a component_granularity checkpoint. Previously the
    # planner only read ``split_required`` / ``should_split`` and silently skipped
    # ``required: true`` components.
    artifacts = {
        "component_assembly": {
            "components": [
                {
                    "component_id": "cmp_canon",
                    "label": "Canonical split component",
                    "linked_claim_ids": ["clm_x"],
                    "split_recommendation": {
                        "required": True,
                        "reasons": ["responsibility type has more than one primary value"],
                        "suggested_components": [],
                    },
                },
            ],
        },
    }
    inv = build_baseline_inventory(artifacts, base_run_id="base-canon")
    # Inventory normalizes the stored recommendation to the canonical key.
    stored = inv["entities"]["components"]["cmp_canon"]["split_recommendation"]
    assert stored["required"] is True
    assert "split_required" not in stored
    triggers = {
        (c["target_id"], c["trigger_reason"]) for c in plan_checkpoints(inv)
    }
    assert ("cmp_canon", "component_granularity") in triggers


def test_legacy_split_keys_still_drive_component_checkpoint():
    # Migration compatibility (#417): legacy keys are still honored.
    for legacy_key in ("split_required", "should_split"):
        artifacts = {
            "component_assembly": {
                "components": [
                    {
                        "component_id": "cmp_legacy",
                        "label": "Legacy split component",
                        "split_recommendation": {legacy_key: True},
                    },
                ],
            },
        }
        inv = build_baseline_inventory(artifacts, base_run_id="base-legacy")
        triggers = {c["trigger_reason"] for c in plan_checkpoints(inv)}
        assert "component_granularity" in triggers, legacy_key


def test_checkpoint_ids_are_stable_hashes():
    inv = build_baseline_inventory(_sample_artifacts(), base_run_id="base-1")
    cps = plan_checkpoints(inv)
    non_atomic = next(c for c in cps if c["trigger_reason"] == "non_atomic_claim")
    # stable id derived only from target+trigger (not from run id / ordering)
    from core.document_pipeline.revision.checkpoints import _checkpoint_id
    assert non_atomic["checkpoint_id"] == _checkpoint_id("claim", "clm_2", "non_atomic_claim")


def test_build_revision_plan_from_base_run():
    base_run = {
        "id": "base-1", "document_id": "doc-1",
        "stage_outputs": {"_artifacts": _sample_artifacts()},
    }
    plan = build_revision_plan(base_run)
    assert plan["baseline_inventory"]["base_run_id"] == "base-1"
    assert plan["audit_checkpoints"]
    assert plan["baseline_inventory"]["document_id"] == "doc-1"


def test_empty_artifacts_yield_empty_inventory():
    inv = build_baseline_inventory({}, base_run_id="base-1")
    assert inv["relations"] == []
    assert inv["unresolved_references"] == []
    assert plan_checkpoints(inv) == []


def test_projection_overlay_marks_manual_edits_and_teacher_approval_protected():
    inv = build_baseline_inventory(
        _sample_artifacts(), base_run_id="base-1", document_id="doc-1"
    )
    out = overlay_projection_state(inv, {
        "claims": [{
            "id": "db-claim-1",
            "source_scope": {"legacy_ids": ["clm_1"]},
            "text": "Teacher-edited claim",
            "normalized_text": "Teacher-edited claim",
            "claim_type": "result",
            "support_status": "source_backed",
            "review_status": "teacher_approved",
        }],
        "components": [],
        "review_events": [{
            "entity_type": "claim",
            "entity_id": "db-claim-1",
            "old_status": "teacher_review_required",
            "new_status": "teacher_approved",
            "metadata": {},
        }],
    })
    claim = out["entities"]["claims"]["clm_1"]
    assert claim["text"] == "Teacher-edited claim"
    assert claim["projection_id"] == "db-claim-1"
    assert claim["manual_edit"] is True
    assert {item["entity_id"] for item in out["protected_decisions"]} >= {"clm_1"}


def test_start_revision_run_persists_plan_without_touching_active(monkeypatch):
    calls = {}

    monkeypatch.setattr(
        coordinator.persistence, "resolve_artifact_run",
        lambda *, document_id, material_id=None: {
            "id": "base-1", "document_id": document_id,
            "stage_outputs": {"_artifacts": _sample_artifacts()},
        },
    )
    monkeypatch.setattr(
        coordinator.persistence,
        "load_revision_projection_overlay",
        lambda *, document_id: {"claims": [], "components": [], "review_events": []},
    )

    def _create(**kwargs):
        calls["create"] = kwargs
        return "rev-1"
    monkeypatch.setattr(coordinator.persistence, "create_revision_run", _create)

    def _update(**kwargs):
        calls["update"] = kwargs
    monkeypatch.setattr(coordinator.persistence, "update_revision_status", _update)

    # active run switch must NOT be called during planning
    monkeypatch.setattr(
        coordinator.persistence, "set_active_analysis_run",
        lambda **k: pytest.fail("active run must not change during revision planning"),
    )

    result = coordinator.start_revision_run(document_id="doc-1", created_by="user-1")
    assert result["revision_run_id"] == "rev-1"
    assert result["base_run_id"] == "base-1"
    assert calls["create"]["base_run_id"] == "base-1"
    assert calls["update"]["revision_status"] == "preparing"
    assert "_artifacts" in calls["update"]["stage_outputs"]
    assert "baseline_inventory" in calls["update"]["stage_outputs"]["_artifacts"]


def test_start_revision_run_requires_base():
    import core.document_pipeline.persistence as real_persistence

    class _Boom:
        pass
    # No base run resolvable -> ValueError
    coordinator.persistence.resolve_artifact_run  # ensure attr exists
    import unittest.mock as mock
    with mock.patch.object(real_persistence, "resolve_artifact_run", return_value=None):
        with pytest.raises(ValueError):
            coordinator.start_revision_run(document_id="doc-x")
