"""#410 P1-6: real LLM re-audit client + audit-driven proposal generation."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.document_pipeline.revision import (  # noqa: E402
    LLMAuditClient,
    audit_checkpoint,
    propose_operations,
    validate_proposed_operation,
    validate_proposed_operations,
)
from core.document_pipeline.revision import coordinator  # noqa: E402


def _inventory():
    return {"entities": {
        "claims": {"clm_1": {"entity_id": "clm_1", "text": "A holds.",
                             "equation_ids": ["eq_1"], "evidence_ids": ["ev_1"]}},
        "equations": {"eq_1": {"entity_id": "eq_1", "latex": "a=b", "raw_text": "a=b"}},
        "evidence": {"ev_1": {"entity_id": "ev_1", "evidence_text": "quoted"}},
        "components": {}, "derivations": {}, "graph_nodes": {}, "graph_edges": {}, "thesis": {},
    }}


def _chunks():
    return [{"chunk_id": "c1", "chunk_index": 0, "section_id": "s1",
             "block_ids": ["b1"], "page_start": 1, "page_end": 1, "text": "A holds because..."}]


def _cp(**kw):
    base = {"checkpoint_id": "ckpt_1", "target_type": "claim", "target_id": "clm_1",
            "question": "valid?", "trigger_reason": "low_confidence",
            "source_scope": [{"section_id": "s1"}], "status": "planned"}
    base.update(kw)
    return base


# --- LLM audit client ------------------------------------------------------

def test_llm_audit_client_parses_and_includes_entity_and_source():
    captured = {}

    def fake_generate(messages):
        captured["messages"] = messages
        return json.dumps({"verdict": "incorrect", "evidence_refs": ["ev_1"],
                           "analysis_notes": ["sign differs"], "confidence": 0.9,
                           "requires_revision": True, "review_required": False,
                           "findings": [{"kind": "sign_error"}]})

    client = LLMAuditClient(_inventory(), generate=fake_generate)
    res = audit_checkpoint(_cp(), _chunks(), llm_client=client)
    # entity + source provided to the model
    user_payload = json.loads(captured["messages"][1]["content"])
    assert user_payload["entity"]["entity_id"] == "clm_1"
    assert user_payload["source"]  # retrieved source included
    assert user_payload["related"]["equations"]  # related equation context
    # verdict came from comparison (not the trigger name)
    assert res["verdict"] == "incorrect"
    assert res["audit_method"] == "llm"
    assert res["requires_revision"] is True


def test_llm_audit_no_source_never_auto_confirms():
    calls = {"n": 0}

    def fake_generate(messages):
        calls["n"] += 1
        return "{}"

    client = LLMAuditClient(_inventory(), generate=fake_generate)
    # checkpoint scope matches nothing -> no retrieval
    res = audit_checkpoint(_cp(source_scope=[{"section_id": "NOPE"}]), _chunks(), llm_client=client)
    assert calls["n"] == 0  # LLM not consulted without source
    assert res["requires_revision"] is False
    assert res["review_required"] is True


def test_llm_audit_low_confidence_forced_to_review():
    def fake_generate(messages):
        return json.dumps({"verdict": "incorrect", "evidence_refs": ["ev_1"],
                           "confidence": 0.2, "requires_revision": True})
    client = LLMAuditClient(_inventory(), generate=fake_generate)
    res = audit_checkpoint(_cp(), _chunks(), llm_client=client)
    assert res["requires_revision"] is False  # forced off by low confidence
    assert res["review_required"] is True


# --- proposal validation ---------------------------------------------------

def test_validate_rejects_unknown_op_and_target():
    op, reason = validate_proposed_operation({"operation": "nope", "target_type": "claim",
                                              "target_id": "clm_1"}, _inventory())
    assert op is None and reason.startswith("unknown_operation")
    op, reason = validate_proposed_operation({"operation": "update_entity",
                                              "target_type": "claim", "target_id": "ghost"},
                                             _inventory())
    assert op is None and reason.startswith("unknown_target")


def test_validate_accepts_and_attaches_traceability():
    audit = {"checkpoint_id": "ckpt_1", "evidence_refs": ["ev_1"],
             "source_locations": [{"chunk_id": "c1"}], "confidence": 0.8,
             "findings": [{"detail": "fix wording"}]}
    op, reason = validate_proposed_operation(
        {"operation": "update_entity", "target_type": "claim", "target_id": "clm_1",
         "after_json": {"text": "A holds under C."}}, _inventory(), audit)
    assert op is not None and reason == ""
    assert op["checkpoint_ids"] == ["ckpt_1"]
    assert op["evidence_refs"] == ["ev_1"]
    assert op["source_locations"] == [{"chunk_id": "c1"}]


def test_validate_rejects_unknown_evidence_instead_of_dropping_it():
    audit = {"checkpoint_id": "ckpt_1", "evidence_refs": ["ev_1", "ev_GONE"],
             "source_locations": [{"chunk_id": "c1"}]}
    op, reason = validate_proposed_operation(
        {"operation": "update_entity", "target_type": "claim", "target_id": "clm_1",
         "after_json": {"text": "fixed"}}, _inventory(), audit)
    assert op is None
    assert reason == "unknown_evidence:ev_GONE"


# --- #412 P0-6: source-chunk vs EvidenceRegistry ID spaces -----------------

def test_chunk_reference_is_not_rejected_as_unknown_evidence():
    # The LLM cited the retrieved chunk id in the (legacy mixed) evidence_refs.
    # It must be routed to source_chunk_ids, NOT rejected as unknown evidence.
    audit = {"checkpoint_id": "ckpt_1", "evidence_refs": ["c1"],
             "source_locations": [{"chunk_id": "c1"}]}
    op, reason = validate_proposed_operation(
        {"operation": "update_entity", "target_type": "claim", "target_id": "clm_1",
         "after_json": {"text": "fixed"}}, _inventory(), audit)
    assert op is not None and reason == ""
    assert op["source_chunk_ids"] == ["c1"]
    assert op["evidence_refs"] == []


def test_typed_evidence_and_chunk_fields_validated_against_their_own_space():
    op, reason = validate_proposed_operation(
        {"operation": "update_entity", "target_type": "claim", "target_id": "clm_1",
         "after_json": {"text": "x"}, "evidence_ids": ["ev_1"],
         "source_chunk_ids": ["c1"], "checkpoint_ids": ["ckpt_1"]},
        _inventory(),
        {"checkpoint_id": "ckpt_1", "source_locations": [{"chunk_id": "c1"}]})
    assert op is not None and reason == ""
    assert op["evidence_refs"] == ["ev_1"]
    assert op["source_chunk_ids"] == ["c1"]


def test_chunk_uuid_not_accepted_as_registry_evidence_id():
    # A chunk id placed in the *evidence* field is unknown registry evidence.
    op, reason = validate_proposed_operation(
        {"operation": "update_entity", "target_type": "claim", "target_id": "clm_1",
         "after_json": {"text": "x"}, "evidence_ids": ["c1"], "checkpoint_ids": ["ckpt_1"]},
        _inventory(),
        {"checkpoint_id": "ckpt_1", "source_locations": [{"chunk_id": "c1"}]})
    assert op is None
    assert reason == "unknown_evidence:c1"


def test_valid_chunk_refs_do_not_cause_mass_rejection():
    # Mirrors the real-data failure: 58/59 proposals were rejected only because
    # valid chunk refs were validated against the EvidenceRegistry ID space.
    audit_results = [
        {"checkpoint_id": f"ckpt_{i}", "target_type": "claim", "target_id": "clm_1",
         "evidence_refs": [f"chunk-{i}"], "source_locations": [{"chunk_id": f"chunk-{i}"}]}
        for i in range(20)
    ]
    raw_ops = [
        {"operation": "update_entity", "target_type": "claim", "target_id": "clm_1",
         "after_json": {"text": f"fix {i}"}, "checkpoint_ids": [f"ckpt_{i}"]}
        for i in range(20)
    ]
    out = validate_proposed_operations(
        raw_ops, _inventory(), audit_results=audit_results,
        checkpoints=[{"checkpoint_id": f"ckpt_{i}"} for i in range(20)],
    )
    # all but the duplicate-target collisions accepted; none rejected for evidence
    rejected_reasons = {r["reason"] for r in out["rejected"]}
    assert "unknown_evidence" not in " ".join(rejected_reasons)
    assert out["operations"], "valid chunk-backed proposals must not be mass-rejected"
    assert out["operations"][0]["source_chunk_ids"] == ["chunk-0"]


def test_propose_operations_uses_generate_and_validates():
    audit_results = [{"checkpoint_id": "ckpt_1", "target_type": "claim", "target_id": "clm_1",
                      "verdict": "incorrect", "requires_revision": True,
                      "evidence_refs": ["ev_1"], "findings": [{"detail": "x"}]}]

    def fake_generate(messages):
        return json.dumps({"operation": "update_entity", "target_type": "claim",
                           "target_id": "clm_1", "after_json": {"text": "fixed"}})

    out = propose_operations(audit_results, _inventory(), generate=fake_generate)
    assert len(out["operations"]) == 1
    assert out["operations"][0]["operation"] == "update_entity"
    assert out["operations"][0]["checkpoint_ids"] == ["ckpt_1"]


def test_propose_operations_without_llm_is_manual_review_not_auto():
    audit_results = [{"checkpoint_id": "ckpt_1", "target_type": "claim", "target_id": "clm_1",
                      "requires_revision": True}]
    out = propose_operations(audit_results, _inventory(), generate=None)
    assert out["operations"] == []
    assert out["manual_review"] and out["manual_review"][0]["target_id"] == "clm_1"


def test_propose_operation_targeting_missing_entity_is_rejected():
    audit_results = [{"checkpoint_id": "c", "target_type": "claim", "target_id": "clm_1",
                      "requires_revision": True}]

    def fake_generate(messages):
        return json.dumps({"operation": "update_entity", "target_type": "claim",
                           "target_id": "GHOST", "after_json": {"text": "x"}})
    out = propose_operations(audit_results, _inventory(), generate=fake_generate)
    assert out["operations"] == []
    assert out["rejected"]


def test_validate_batch_raw_json_path():
    out = validate_proposed_operations([
        {"operation": "update_entity", "target_type": "claim", "target_id": "clm_1",
         "after_json": {"text": "x"}, "checkpoint_ids": ["ckpt_1"],
         "source_locations": [{"chunk_id": "c1"}]},
        {"operation": "bogus"},
    ], _inventory(), checkpoints=[{"checkpoint_id": "ckpt_1"}])
    assert len(out["operations"]) == 1
    assert len(out["rejected"]) == 1


def test_validate_raw_requires_known_checkpoint_and_source():
    missing_checkpoint = validate_proposed_operations([
        {"operation": "update_entity", "target_type": "claim", "target_id": "clm_1",
         "after_json": {"text": "x"}, "source_locations": [{"chunk_id": "c1"}]},
    ], _inventory(), checkpoints=[{"checkpoint_id": "ckpt_1"}])
    assert missing_checkpoint["rejected"][0]["reason"] == "missing_checkpoint"

    missing_source = validate_proposed_operations([
        {"operation": "update_entity", "target_type": "claim", "target_id": "clm_1",
         "after_json": {"text": "x"}, "checkpoint_ids": ["ckpt_1"]},
    ], _inventory(), checkpoints=[{"checkpoint_id": "ckpt_1"}])
    assert missing_source["rejected"][0]["reason"] == "missing_source_or_evidence"

    unknown_checkpoint = validate_proposed_operations([
        {"operation": "update_entity", "target_type": "claim", "target_id": "clm_1",
         "after_json": {"text": "x"}, "checkpoint_ids": ["ghost"],
         "source_locations": [{"chunk_id": "c1"}]},
    ], _inventory(), checkpoints=[{"checkpoint_id": "ckpt_1"}])
    assert unknown_checkpoint["rejected"][0]["reason"] == "unknown_checkpoint:ghost"


# --- representative entity types -------------------------------------------

@pytest.mark.parametrize("target_type,bucket,tid", [
    ("claim", "claims", "clm_1"),
    ("equation", "equations", "eq_1"),
    ("evidence", "evidence", "ev_1"),
])
def test_proposal_for_each_entity_type(target_type, bucket, tid):
    inv = _inventory()
    audit = [{"checkpoint_id": "c", "target_type": target_type, "target_id": tid,
              "requires_revision": True, "evidence_refs": ["ev_1"]}]

    def fake_generate(messages):
        return json.dumps({"operation": "update_entity", "target_type": target_type,
                           "target_id": tid, "after_json": {"note": "fixed"}})
    out = propose_operations(audit, inv, generate=fake_generate)
    assert len(out["operations"]) == 1


# --- coordinator integration ----------------------------------------------

def test_generate_proposals_stores_artifact(monkeypatch):
    run = {"id": "rev-1", "run_type": "revision", "document_id": "doc-1",
           "stage_outputs": {"_artifacts": {
               "baseline_inventory": _inventory(),
               "audit_results": {"audit_results": [
                   {"checkpoint_id": "c", "target_type": "claim", "target_id": "clm_1",
                    "requires_revision": True, "evidence_refs": ["ev_1"]}]},
           }}}
    monkeypatch.setattr(coordinator.persistence, "get_analysis_run", lambda *, run_id: run)
    captured = {}
    monkeypatch.setattr(coordinator.persistence, "update_revision_status",
                        lambda **k: captured.update(k))

    def fake_generate(messages):
        return json.dumps({"operation": "update_entity", "target_type": "claim",
                           "target_id": "clm_1", "after_json": {"text": "fixed"}})

    out = coordinator.generate_proposals(run_id="rev-1", generate=fake_generate)
    assert len(out["operations"]) == 1
    assert "proposed_operations" in captured["stage_outputs"]["_artifacts"]
