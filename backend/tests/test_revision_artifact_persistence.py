"""#410 P0: revision artifacts must survive every stage update.

PostgreSQL JSONB ``||`` only replaces top-level keys, so a shallow merge wiped
``_artifacts`` on each stage. These tests cover the deep-merge fix at three
levels: the pure merge model, the generated SQL shape, and a full
in-memory lifecycle (start → audit → assemble → report) proving every artifact
is still present right before accept.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.document_pipeline import persistence  # noqa: E402
from core.document_pipeline.persistence import merge_revision_stage_outputs  # noqa: E402
from core.document_pipeline.revision import coordinator  # noqa: E402
from core.document_pipeline.revision import make_operation  # noqa: E402


# --- pure merge model ------------------------------------------------------

def test_merge_preserves_sibling_artifacts():
    so = {"_artifacts": {"baseline_inventory": {"a": 1}, "audit_checkpoints": [1]}}
    so = merge_revision_stage_outputs(so, {"_artifacts": {"audit_results": {"b": 2}}})
    assert set(so["_artifacts"]) == {"baseline_inventory", "audit_checkpoints", "audit_results"}
    so = merge_revision_stage_outputs(so, {"_artifacts": {"candidate": {"c": 3}}})
    assert set(so["_artifacts"]) == {
        "baseline_inventory", "audit_checkpoints", "audit_results", "candidate"}
    # same key replaces only that key
    so = merge_revision_stage_outputs(so, {"_artifacts": {"audit_results": {"b": 99}}})
    assert so["_artifacts"]["audit_results"] == {"b": 99}
    assert so["_artifacts"]["baseline_inventory"] == {"a": 1}


def test_merge_handles_non_artifact_top_level_keys():
    so = merge_revision_stage_outputs({"_artifacts": {"x": 1}}, {"progress": 50})
    assert so["progress"] == 50
    assert so["_artifacts"] == {"x": 1}


# --- generated SQL shape ---------------------------------------------------

class _FakeResult:
    rowcount = 1

    def fetchone(self):
        return None


class _CaptureSession:
    def __init__(self):
        self.sql = []
        self.params = []

    def execute(self, statement, params=None):
        self.sql.append(str(statement))
        self.params.append(params or {})
        return _FakeResult()

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def test_update_revision_status_sql_deep_merges_artifacts(monkeypatch):
    session = _CaptureSession()
    monkeypatch.setattr(persistence, "_pg_session", lambda: session)
    persistence.update_revision_status(
        run_id="rev-1", revision_status="auditing",
        stage_outputs={"_artifacts": {"audit_results": {"x": 1}}},
    )
    sql = session.sql[0]
    # must jsonb_set the _artifacts path, not replace the whole object
    assert "jsonb_set" in sql
    assert "'{_artifacts}'" in sql
    assert "stage_outputs->'_artifacts'" in sql
    # payload split: artifacts delta separate from other keys
    assert session.params[0]["artifacts"]
    assert "audit_results" in session.params[0]["artifacts"]


# --- full in-memory lifecycle ----------------------------------------------

class _InMemoryRuns:
    """Models document_analysis_runs with the deep-merge semantics of the fix."""

    def __init__(self):
        self.runs: dict[str, dict] = {}
        self._n = 0

    def add_initial(self, document_id, artifacts):
        self._n += 1
        rid = f"base-{self._n}"
        self.runs[rid] = {
            "id": rid, "document_id": document_id, "run_type": "initial",
            "status": "completed", "base_run_id": None, "revision_status": None,
            "stage_outputs": {"_artifacts": artifacts},
        }
        return rid

    # persistence shims --------------------------------------------------
    def get_analysis_run(self, *, run_id):
        return self.runs.get(run_id)

    def resolve_artifact_run(self, *, document_id, material_id=None):
        for r in self.runs.values():
            if r["document_id"] == document_id and r["run_type"] == "initial":
                return r
        return None

    def create_revision_run(self, *, document_id, base_run_id, revision_status="preparing", **kw):
        self._n += 1
        rid = f"rev-{self._n}"
        self.runs[rid] = {
            "id": rid, "document_id": document_id, "run_type": "revision",
            "status": "pending", "base_run_id": base_run_id,
            "revision_status": revision_status, "stage_outputs": {},
        }
        return rid

    def update_revision_status(self, *, run_id, revision_status, status=None,
                               error_message=None, stage_outputs=None):
        run = self.runs[run_id]
        run["revision_status"] = revision_status
        if status:
            run["status"] = status
        run["stage_outputs"] = merge_revision_stage_outputs(
            run.get("stage_outputs"), stage_outputs)

    def load_source_chunk_index(self, *, document_id):
        return [{"chunk_id": "c1", "chunk_index": 0, "section_id": "s1",
                 "block_ids": ["b1"], "page_start": 1, "page_end": 1, "text": "txt"}]

    def load_revision_projection_overlay(self, *, document_id):
        return {"claims": [], "components": [], "review_events": []}


def _base_artifacts():
    return {
        "claim_object_builder": {"claims": [
            {"claim_id": "clm_1", "text": "A.", "is_atomic": True,
             "source_evidence_ids": ["ev_1"], "linked_component_ids": ["cmp_1"]},
        ]},
        "evidence_registry": {"records": [{"evidence_id": "ev_1", "evidence_text": "q"}]},
        "component_assembly": {"components": [
            {"component_id": "cmp_1", "label": "C1", "linked_claim_ids": ["clm_1"]},
        ]},
        "component_graph": {"nodes": [{"node_id": "cmp_1", "graph_layer": "main"}], "edges": []},
    }


def test_full_lifecycle_keeps_every_artifact(monkeypatch):
    store = _InMemoryRuns()
    base_artifacts = _base_artifacts()
    store.add_initial("doc-1", base_artifacts)

    for fn in ("get_analysis_run", "resolve_artifact_run", "create_revision_run",
               "update_revision_status", "load_source_chunk_index",
               "load_revision_projection_overlay"):
        monkeypatch.setattr(coordinator.persistence, fn, getattr(store, fn))

    started = coordinator.start_revision_run(document_id="doc-1")
    rev_id = started["revision_run_id"]
    coordinator.audit_revision_run(run_id=rev_id)
    checkpoint_id = started["audit_checkpoints"][0]["checkpoint_id"]
    coordinator.assemble_candidate(run_id=rev_id, operations=[
        make_operation(operation="update_entity", target_type="claim",
                       target_id="clm_1", after_json={"text": "A revised"},
                       checkpoint_ids=[checkpoint_id], evidence_refs=["ev_1"]),
    ])
    coordinator.revalidate_and_report(run_id=rev_id)

    arts = store.runs[rev_id]["stage_outputs"]["_artifacts"]
    # every stage artifact must still be present right before accept
    for key in ("baseline_inventory", "audit_checkpoints", "audit_results",
                "revision_operations", "candidate", "candidate_validation", "diff_report"):
        assert key in arts, f"{key} was lost during stage updates"
    # base run artifacts are never mutated
    assert store.runs["base-1"]["stage_outputs"]["_artifacts"] == base_artifacts
    # candidate is the patched version, base is not
    cand_claims = arts["candidate"]["candidate_artifacts"]["claim_object_builder"]["claims"]
    assert cand_claims[0]["text"] == "A revised"
    assert base_artifacts["claim_object_builder"]["claims"][0]["text"] == "A."
