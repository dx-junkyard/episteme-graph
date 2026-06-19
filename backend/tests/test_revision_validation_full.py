"""#410 P1-2: candidate validation runs the full gate and is fail-closed.

Reconstructs real agent Result objects from candidate artifacts so the
cross-artifact checks run, and proves that broken evidence links / non-atomic
support / dangling component refs become hard errors, and that any internal
validation failure is fail-closed (never acceptable).
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.document_pipeline.revision import run_export_validation  # noqa: E402
from core.document_pipeline.revision import validation as validation_mod  # noqa: E402


def _valid_artifacts():
    return {
        # Required-artifact presence (gate _check_required_artifacts). A real
        # candidate carries these from its base run; minimal non-empty stand-ins
        # keep the fixture focused on the cross-artifact checks under test.
        "document_structure": {"document_id": "doc-1", "blocks": [{"block_id": "b1"}]},
        "source_chunking": {"chunks": [{"chunk_id": "c1"}]},
        "claim_qualification": {"document_id": "doc-1", "qualified_spans": [{"span_id": "sp1"}]},
        "claim_object_builder": {"document_id": "doc-1", "cartridge_id": None, "claims": [
            {"claim_id": "clm_1", "document_id": "doc-1", "claim_type": "result",
             "text": "A holds under C.", "atomicity": "atomic", "is_atomic": True,
             "support_status": "source_backed", "source_evidence_ids": ["ev_1"],
             "linked_component_ids": ["cmp_1"]},
        ]},
        "evidence_registry": {"document_id": "doc-1", "cartridge_id": None, "records": [
            {"evidence_id": "ev_1", "document_id": "doc-1", "evidence_text": "quote",
             "source": {"page": 1, "section_id": "s1", "block_id": "b1"}},
        ]},
        "component_assembly": {"document_id": "doc-1", "cartridge_id": None, "components": [
            {"component_id": "cmp_1", "label": "C1", "component_type": "theory",
             "linked_claim_ids": ["clm_1"], "evidence_refs": {"claim_ids": ["clm_1"]},
             "source_scope": {"document_id": "doc-1"}},
        ]},
        "component_graph": {"nodes": [
            {"node_id": "cmp_1", "label": "Theory basis", "graph_layer": "main",
             "component_type": "TheoryOperationNode", "source_backing_status": "source_backed"}],
            "edges": []},
    }


def _codes(result):
    return {e.get("code") for e in (result.get("errors") or [])}


def test_clean_candidate_runs_full_gate_no_hard_errors():
    result = run_export_validation(_valid_artifacts())
    # reconstruction + gate ran; not the fail-closed sentinel
    assert "CANDIDATE_VALIDATION_FAILED" not in _codes(result)
    assert result["summary"]["error_count"] == 0


def test_broken_evidence_link_is_hard_error():
    arts = _valid_artifacts()
    arts["claim_object_builder"]["claims"][0]["source_evidence_ids"] = ["ev_MISSING"]
    result = run_export_validation(arts)
    assert result["summary"]["error_count"] >= 1
    assert "SOURCE_BACKED_CLAIM_UNRESOLVED_EVIDENCE_ID" in _codes(result)


def test_non_atomic_claim_as_component_support_is_hard_error():
    arts = _valid_artifacts()
    arts["claim_object_builder"]["claims"][0]["atomicity"] = "non_atomic"
    arts["claim_object_builder"]["claims"][0]["is_atomic"] = False
    result = run_export_validation(arts)
    assert "NON_ATOMIC_CLAIM_USED_AS_COMPONENT_SUPPORT" in _codes(result)


def test_dangling_component_claim_ref_is_hard_error():
    arts = _valid_artifacts()
    arts["component_assembly"]["components"][0]["evidence_refs"] = {"claim_ids": ["clm_GONE"]}
    arts["component_assembly"]["components"][0]["linked_claim_ids"] = ["clm_GONE"]
    result = run_export_validation(arts)
    assert "UNRESOLVED_COMPONENT_CLAIM_ID" in _codes(result)


def test_gate_runtime_error_is_fail_closed(monkeypatch):
    import core.document_pipeline.export_validation_gate as gate_mod

    def _boom(self, **kwargs):
        raise RuntimeError("gate exploded")
    monkeypatch.setattr(gate_mod.ExportValidationGate, "run", _boom)
    result = run_export_validation(_valid_artifacts())
    assert result["status"] == "failed_validation"
    assert result["summary"]["error_count"] >= 1
    assert "CANDIDATE_VALIDATION_FAILED" in _codes(result)


def test_unparseable_present_artifact_is_fail_closed():
    arts = _valid_artifacts()
    # remove a required key so ClaimObjectBuildResult.from_dict raises
    del arts["claim_object_builder"]["claims"][0]["document_id"]
    result = run_export_validation(arts)
    assert result["status"] == "failed_validation"
    assert "CANDIDATE_VALIDATION_FAILED" in _codes(result)


def test_gate_import_failure_is_fail_closed(monkeypatch):
    # Force the reconstruction step to succeed but the gate import to fail.
    monkeypatch.setattr(validation_mod, "_reconstruct_inputs",
                        lambda arts: (None, None, None, None))
    import builtins
    real_import = builtins.__import__

    def _fake_import(name, *a, **k):
        if name.endswith("export_validation_gate"):
            raise ImportError("no gate")
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", _fake_import)
    result = run_export_validation(_valid_artifacts())
    assert result["status"] == "failed_validation"
    assert "CANDIDATE_VALIDATION_FAILED" in _codes(result)


def test_base_artifacts_not_mutated_by_validation():
    arts = _valid_artifacts()
    snapshot = copy.deepcopy(arts)
    run_export_validation(arts)
    assert arts == snapshot
