"""Tests for candidate re-validation + before/after diff report (#406)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.document_pipeline.revision import (  # noqa: E402
    apply_operations,
    build_diff_report,
    candidate_has_hard_errors,
    compute_quality_metrics,
    make_operation,
    run_export_validation,
)
from core.document_pipeline.revision import coordinator  # noqa: E402


def _artifacts(extra_review=False):
    return {
        "claim_object_builder": {"claims": [
            {"claim_id": "clm_1", "text": "A.", "is_atomic": True,
             "linked_component_ids": ["cmp_1"], "source_evidence_ids": ["ev_1"]},
        ]},
        "equation_semantics": {"equations": [
            {"equation_id": "eq_1", "reconstruction": {"latex": "a=b"},
             "confidence_policy": {"confidence": 0.2 if extra_review else 0.9}},
        ]},
        "evidence_registry": {"records": [{"evidence_id": "ev_1", "evidence_text": "q"}]},
        "component_assembly": {"components": [
            {"component_id": "cmp_1", "label": "C1", "linked_claim_ids": ["clm_1"],
             "split_recommendation": {"split_required": extra_review}},
        ]},
        "component_graph": {
            "nodes": [{"node_id": "cmp_1", "graph_layer": "main",
                       "source_backing_status": "source_backed"}],
            "edges": [{"edge_id": "edge_1", "source": "cmp_1", "target": "cmp_1",
                       "source_backing_status": "review_required" if extra_review else "source_backed"}],
        },
    }


# --- gate re-validation ----------------------------------------------------

def test_run_export_validation_returns_gate_dict():
    res = run_export_validation(_artifacts())
    assert "summary" in res
    assert "errors" in res and "warnings" in res
    assert isinstance(res["summary"].get("error_count"), int)


def test_run_export_validation_never_raises_on_garbage():
    res = run_export_validation({"claim_object_builder": "not-a-dict"})
    assert "summary" in res  # degraded but structured


# --- quality metrics -------------------------------------------------------

def test_quality_metrics_capture_key_indicators():
    m = compute_quality_metrics(_artifacts(extra_review=True))
    assert m["low_confidence_equation_count"] == 1
    assert m["component_granularity_violation_count"] == 1
    # one of two graph backings is source_backed
    assert 0.0 <= m["source_backed_rate"] <= 1.0
    assert m["main_claim_coverage"] == 1.0


# --- full report -----------------------------------------------------------

def _report_for(ops, base=None):
    base = base or _artifacts(extra_review=True)
    result = apply_operations(base, ops)
    gate_base = run_export_validation(base)
    gate_candidate = run_export_validation(result["candidate_artifacts"])
    return build_diff_report(
        base_run_id="base-1", candidate_run_id="rev-1",
        base_artifacts=base, candidate_artifacts=result["candidate_artifacts"],
        applied_operations=result["applied_operations"],
        gate_base=gate_base, gate_candidate=gate_candidate,
        candidate_invalid=result["invalid"],
        protected_changes=result["protected_changes"],
        requires_confirmation=result["requires_confirmation"],
        id_mapping=result["id_mapping"],
    ), result


def test_report_has_full_schema():
    report, _ = _report_for([
        make_operation(operation="update_entity", target_type="component",
                       target_id="cmp_1", after_json={"split_recommendation": {}},
                       reason="resolved granularity", checkpoint_ids=["ckpt_1"],
                       evidence_refs=["ev_1"], confidence=0.8),
    ])
    for key in ("base_run_id", "candidate_run_id", "summary", "entity_changes",
                "quality_before", "quality_after", "resolved_issues",
                "introduced_issues", "unchanged_review_items", "protected_changes",
                "recommendation"):
        assert key in report
    assert report["recommendation"] in ("accept", "reject", "manual_review")


def test_entity_change_traces_to_checkpoint_and_evidence():
    report, _ = _report_for([
        make_operation(operation="update_entity", target_type="component",
                       target_id="cmp_1", after_json={"label": "C1 new"},
                       reason="clarify", checkpoint_ids=["ckpt_9"],
                       evidence_refs=["ev_1"],
                       source_locations=[{"section_id": "s1"}], confidence=0.7),
    ])
    change = report["entity_changes"][0]
    assert change["change_type"] == "updated"
    assert change["checkpoint_ids"] == ["ckpt_9"]
    assert change["evidence_refs"] == ["ev_1"]
    assert change["source_locations"] == [{"section_id": "s1"}]
    assert change["reason"] == "clarify"


def test_protected_change_listed_independently():
    report, _ = _report_for([
        make_operation(operation="update_entity", target_type="claim",
                       target_id="clm_1", after_json={"text": "B."},
                       protected_target=True),
    ])
    assert report["summary"]["protected_change_count"] == 1
    assert report["protected_changes"][0]["target_id"] == "clm_1"
    assert report["summary"]["requires_confirmation"] is True


def test_invalid_candidate_not_acceptable_and_reject_recommended():
    report, result = _report_for([
        make_operation(operation="update_entity", target_type="claim",
                       target_id="clm_MISSING", after_json={"text": "x"}),
    ])
    assert result["invalid"] is True
    assert report["summary"]["acceptable"] is False
    assert report["recommendation"] == "reject"


def test_resolved_vs_introduced_findings_classified():
    # base has a structural problem the gate flags; we don't actually fix it,
    # but classification must run without error and separate the buckets.
    report, _ = _report_for([
        make_operation(operation="update_entity", target_type="claim",
                       target_id="clm_1", after_json={"text": "A revised"}),
    ])
    assert isinstance(report["resolved_issues"], list)
    assert isinstance(report["introduced_issues"], list)
    # acceptable requires zero hard errors
    if report["quality_after"]["hard_error_count"] == 0:
        assert report["summary"]["acceptable"] is True


def test_candidate_has_hard_errors_helper():
    assert candidate_has_hard_errors({"summary": {"error_count": 2}}) is True
    assert candidate_has_hard_errors({"summary": {"error_count": 0}}) is False


# --- coordinator integration -----------------------------------------------

def test_revalidate_and_report_persists_without_touching_active(monkeypatch):
    base_artifacts = _artifacts(extra_review=True)
    result = apply_operations(base_artifacts, [
        make_operation(operation="update_entity", target_type="component",
                       target_id="cmp_1", after_json={"split_recommendation": {}}),
    ])
    run = {"id": "rev-1", "run_type": "revision", "base_run_id": "base-1",
           "document_id": "doc-1", "stage_outputs": {"_artifacts": {
               "revision_operations": result["applied_operations"],
               "candidate": {
                   "candidate_artifacts": result["candidate_artifacts"],
                   "id_mapping": result["id_mapping"],
                   "protected_changes": result["protected_changes"],
                   "invalid": result["invalid"],
                   "requires_confirmation": result["requires_confirmation"],
               },
           }}}
    base = {"id": "base-1", "run_type": "initial", "document_id": "doc-1",
            "stage_outputs": {"_artifacts": base_artifacts}}

    monkeypatch.setattr(coordinator.persistence, "get_analysis_run",
                        lambda *, run_id: run if run_id == "rev-1" else base)
    captured = {}
    monkeypatch.setattr(coordinator.persistence, "update_revision_status",
                        lambda **k: captured.update(k))
    monkeypatch.setattr(coordinator.persistence, "set_active_analysis_run",
                        lambda **k: pytest.fail("report gen must not change active run"))

    out = coordinator.revalidate_and_report(run_id="rev-1")
    assert out["report"]["candidate_run_id"] == "rev-1"
    arts = captured["stage_outputs"]["_artifacts"]
    assert "diff_report" in arts and "candidate_validation" in arts
