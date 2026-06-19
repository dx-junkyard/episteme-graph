"""Tests for checkpoint-driven source re-audit (#404)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.document_pipeline.revision import (  # noqa: E402
    VERDICTS,
    audit_checkpoint,
    retrieve_source_for_checkpoint,
    run_source_audit,
)
from core.document_pipeline.revision import coordinator  # noqa: E402


def _chunk_index():
    return [
        {"chunk_id": "c0", "chunk_index": 0, "section_id": "s1",
         "block_ids": ["b1"], "page_start": 1, "page_end": 1, "text": "Intro text."},
        {"chunk_id": "c1", "chunk_index": 1, "section_id": "s2",
         "block_ids": ["b2", "b3"], "page_start": 2, "page_end": 2, "text": "Method: a=b derived."},
        {"chunk_id": "c2", "chunk_index": 2, "section_id": "s2",
         "block_ids": ["b4"], "page_start": 2, "page_end": 3, "text": "More method."},
    ]


def _cp(**kw):
    base = {
        "checkpoint_id": "ckpt_x", "target_type": "claim", "target_id": "clm_1",
        "question": "q", "trigger_reason": "non_atomic_claim", "severity": "warning",
        "expected_evidence_type": "source_text", "source_scope": [], "status": "planned",
    }
    base.update(kw)
    return base


# --- retrieval -------------------------------------------------------------

def test_retrieval_matches_section_and_includes_neighbors():
    cp = _cp(source_scope=[{"section_id": "s2", "block_id": "", "span_id": ""}])
    r = retrieve_source_for_checkpoint(cp, _chunk_index())
    ids = {c["chunk_id"] for c in r["retrieved"]}
    # s2 chunks c1,c2 matched; c0 pulled in as neighbor of c1
    assert {"c1", "c2"} <= ids
    assert r["retrieval_reason"] == "scope_resolved"
    assert any(s["match_reason"] == "section_match" for s in r["scope_used"])


def test_retrieval_records_reason_when_no_scope():
    r = retrieve_source_for_checkpoint(_cp(source_scope=[]), _chunk_index())
    assert r["retrieved"] == []
    assert r["retrieval_reason"] == "no_scope"


def test_retrieval_block_and_page_match():
    cp = _cp(source_scope=[{"block_id": "b2"}])
    r = retrieve_source_for_checkpoint(cp, _chunk_index())
    assert any(c["chunk_id"] == "c1" for c in r["retrieved"])
    cp2 = _cp(source_scope=[{"page": 3}])
    r2 = retrieve_source_for_checkpoint(cp2, _chunk_index())
    assert any(c["chunk_id"] == "c2" for c in r2["retrieved"])


# --- deterministic verdicts ------------------------------------------------

def test_deterministic_never_marks_valid():
    for trig in ("non_atomic_claim", "low_confidence", "edge_not_source_backed",
                 "validation:MISSING_EVIDENCE", "unresolved_reference"):
        res = audit_checkpoint(_cp(trigger_reason=trig), _chunk_index())
        assert res["verdict"] in VERDICTS
        assert res["verdict"] != "valid"


def test_deterministic_defect_requires_revision():
    res = audit_checkpoint(
        _cp(trigger_reason="unresolved_reference", target_type="claim"),
        _chunk_index(),
    )
    assert res["requires_revision"] is True
    assert res["verdict"] in ("incomplete", "unsupported")


def test_deterministic_ambiguous_not_auto_confirmed():
    res = audit_checkpoint(_cp(trigger_reason="low_confidence"), _chunk_index())
    assert res["verdict"] == "ambiguous"
    assert res["requires_revision"] is False
    assert res["review_required"] is True


def test_source_evidence_separate_from_analysis_notes():
    cp = _cp(trigger_reason="non_atomic_claim",
             source_scope=[{"section_id": "s2"}])
    res = audit_checkpoint(cp, _chunk_index())
    assert "source_evidence" in res and "analysis_notes" in res
    # deterministic path has no LLM commentary
    assert res["analysis_notes"] == []
    assert all("text" in se for se in res["source_evidence"])


# --- LLM path safety -------------------------------------------------------

def test_llm_valid_without_evidence_is_downgraded():
    def fake_llm(checkpoint, retrieval):
        return {"verdict": "valid", "evidence_refs": [], "confidence": 0.9,
                "analysis_notes": ["looks fine"]}
    cp = _cp(trigger_reason="low_confidence", source_scope=[])
    res = audit_checkpoint(cp, _chunk_index(), llm_client=fake_llm)
    # no evidence -> cannot be valid
    assert res["verdict"] == "ambiguous"
    assert res["requires_revision"] is False
    assert res["analysis_notes"] == ["looks fine"]


def test_llm_low_confidence_forces_review():
    def fake_llm(checkpoint, retrieval):
        return {"verdict": "incorrect", "evidence_refs": ["ev_1"],
                "confidence": 0.2, "requires_revision": True}
    res = audit_checkpoint(_cp(), _chunk_index(), llm_client=fake_llm)
    assert res["requires_revision"] is False  # forced off by low confidence
    assert res["review_required"] is True


def test_llm_high_confidence_incorrect_requires_revision():
    def fake_llm(checkpoint, retrieval):
        return {"verdict": "incorrect", "evidence_refs": ["ev_1"],
                "confidence": 0.9, "requires_revision": True,
                "findings": [{"kind": "sign_error"}]}
    res = audit_checkpoint(_cp(), _chunk_index(), llm_client=fake_llm)
    assert res["verdict"] == "incorrect"
    assert res["requires_revision"] is True
    assert res["audit_method"] == "llm"


def test_llm_exception_is_review_required_not_revision_target():
    def boom(checkpoint, retrieval):
        raise RuntimeError("llm down")
    res = audit_checkpoint(_cp(trigger_reason="unresolved_reference"),
                           _chunk_index(), llm_client=boom)
    assert res["audit_method"] == "llm_failed"
    assert res["requires_revision"] is False
    assert res["review_required"] is True
    assert res["confidence"] == 0.0
    assert res["audit_error"]["type"] == "RuntimeError"


# --- run_source_audit summary ----------------------------------------------

def test_run_source_audit_builds_worklists():
    checkpoints = [
        _cp(checkpoint_id="a", trigger_reason="unresolved_reference", target_id="clm_2"),
        _cp(checkpoint_id="b", trigger_reason="low_confidence", target_id="clm_3"),
    ]
    out = run_source_audit(checkpoints=checkpoints, chunk_index=_chunk_index())
    assert len(out["audit_results"]) == 2
    rev_targets = {t["target_id"] for t in out["revision_targets"]}
    review = {t["target_id"] for t in out["review_items"]}
    assert "clm_2" in rev_targets
    assert "clm_3" in review
    assert out["verdict_counts"]


# --- DB-integrated runner isolation ----------------------------------------

def test_audit_revision_run_does_not_touch_base_or_active(monkeypatch):
    run = {
        "id": "rev-1", "run_type": "revision", "document_id": "doc-1",
        "stage_outputs": {"_artifacts": {"audit_checkpoints": [
            _cp(checkpoint_id="a", trigger_reason="unresolved_reference", target_id="clm_2"),
        ]}},
    }
    monkeypatch.setattr(coordinator.persistence, "get_analysis_run",
                        lambda *, run_id: run)
    monkeypatch.setattr(coordinator.persistence, "load_source_chunk_index",
                        lambda *, document_id: _chunk_index())
    captured = {}
    monkeypatch.setattr(coordinator.persistence, "update_revision_status",
                        lambda **k: captured.update(k))
    monkeypatch.setattr(coordinator.persistence, "set_active_analysis_run",
                        lambda **k: pytest.fail("audit must not change active run"))
    monkeypatch.setattr(coordinator.persistence, "persist_qualified_claims",
                        lambda **k: pytest.fail("audit must not persist projections"))

    out = coordinator.audit_revision_run(run_id="rev-1")
    assert captured["revision_status"] == "auditing"
    assert "audit_results" in captured["stage_outputs"]["_artifacts"]
    assert out["revision_targets"]


def test_audit_revision_run_rejects_non_revision(monkeypatch):
    monkeypatch.setattr(
        coordinator.persistence, "get_analysis_run",
        lambda *, run_id: {"id": run_id, "run_type": "initial",
                           "document_id": "d", "stage_outputs": {}},
    )
    with pytest.raises(ValueError):
        coordinator.audit_revision_run(run_id="init-1")
