"""#410 P1-5: accept rebuilds all projections from the candidate in one txn."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.document_pipeline import persistence  # noqa: E402
from core.document_pipeline.persistence import RevisionConflictError  # noqa: E402


class _Res:
    def __init__(self, session, rowcount=0):
        self._session = session
        self.rowcount = rowcount

    def fetchone(self):
        if self._session.ids:
            return (self._session.ids.pop(0),)
        return None


class _RebuildSession:
    def __init__(self, ids=None, switch_rowcount=1, lock_row=None, fail_on=None):
        self.ids = list(ids or [])
        self.sql = []
        self.params = []
        self.committed = False
        self.rolled_back = False
        self.switch_rowcount = switch_rowcount
        self.lock_row = lock_row
        self.fail_on = fail_on

    def execute(self, statement, params=None):
        s = str(statement)
        self.sql.append(s)
        self.params.append(params or {})
        if self.fail_on and self.fail_on in s:
            raise RuntimeError("projection insert failed")
        if "FOR UPDATE" in s:
            return _FixedRow(self.lock_row)
        if "UPDATE documents" in s:
            return _Res(self, rowcount=self.switch_rowcount)
        return _Res(self)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        pass


class _FixedRow:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


# --- direct rebuilders -----------------------------------------------------

def test_rebuild_claims_maps_ids_and_deletes_first():
    session = _RebuildSession(ids=["db1", "db2"])
    id_map = persistence._rebuild_theory_claims_in_session(session, "doc-1", [
        {"claim_id": "clm_1", "text": "A", "claim_type": "result"},
        {"claim_id": "clm_2", "text": "B", "claim_type": "weird_type"},
    ])
    assert id_map == {"clm_1": "db1", "clm_2": "db2"}
    assert "DELETE FROM theory_claims" in session.sql[0]
    # invalid claim_type coerced to diagnostic_claim
    assert session.params[2]["claim_type"] == "diagnostic_claim"


def test_rebuild_components_inserts_links_and_remaps_claims():
    session = _RebuildSession(ids=["cdb1", "cdb2"])
    id_map = persistence._rebuild_theory_components_in_session(
        session, "doc-1",
        [
            {"component_id": "cmp_1", "label": "C1", "linked_claim_ids": ["clm_1"],
             "dependencies": [{"dependency_type": "requires", "component_refs": ["cmp_2"]}]},
            {"component_id": "cmp_2", "label": "C2"},
        ],
        claim_id_map={"clm_1": "dbclaim1"},
    )
    assert set(id_map) == {"cmp_1", "cmp_2"}
    joined = "\n".join(session.sql)
    assert "DELETE FROM theory_component_links" in joined
    assert "DELETE FROM theory_components" in joined
    assert "INSERT INTO theory_component_links" in joined
    # linked claim remapped to db id in evidence_claims
    comp1_params = session.params[2]  # 0,1 are deletes; 2 is first component insert
    assert "dbclaim1" in comp1_params["evidence_claims"]


def test_revision_graph_remaps_component_claim_and_edge_ids():
    graph = persistence._remap_revision_graph(
        {
            "nodes": [{
                "node_id": "cmp_1",
                "linked_claim_ids": ["clm_1"],
            }],
            "edges": [{
                "source": "cmp_1",
                "target": "cmp_1",
                "evidence": {"claim_ids": ["clm_1"]},
            }],
        },
        {"cmp_1": "db-comp-1"},
        {"clm_1": "db-claim-1"},
    )
    assert graph["nodes"][0]["node_id"] == "db-comp-1"
    assert graph["nodes"][0]["component_id"] == "db-comp-1"
    assert graph["nodes"][0]["linked_claim_ids"] == ["db-claim-1"]
    assert graph["edges"][0]["source"] == "db-comp-1"
    assert graph["edges"][0]["target"] == "db-comp-1"
    assert graph["edges"][0]["evidence"]["claim_ids"] == ["db-claim-1"]


def test_revision_graph_rejects_unknown_component_endpoint():
    with pytest.raises(ValueError, match="unknown"):
        persistence._remap_revision_graph(
            {
                "nodes": [{"node_id": "cmp_1"}],
                "edges": [{"source": "cmp_1", "target": "ghost"}],
            },
            {"cmp_1": "db-comp-1"},
            {},
        )


# --- full accept transaction ----------------------------------------------

def _candidate():
    return {
        "claim_object_builder": {"claims": [{"claim_id": "clm_1", "text": "A", "claim_type": "result"}]},
        "component_assembly": {"components": [{"component_id": "cmp_1", "label": "C1",
                                              "linked_claim_ids": ["clm_1"]}]},
        "component_graph": {"nodes": [{"node_id": "cmp_1"}], "edges": []},
    }


def test_accept_rebuilds_all_projections_in_one_transaction(monkeypatch):
    session = _RebuildSession(
        ids=["dbclaim1", "dbcomp1"], switch_rowcount=1,
        lock_row=("running", "proposed", "base-1", "revision"),
    )
    monkeypatch.setattr(persistence, "_pg_session", lambda: session)
    out = persistence.accept_revision(
        document_id="doc-1", run_id="rev-1", expected_base_run_id="base-1",
        candidate_artifacts=_candidate(),
    )
    assert out["accepted"] is True
    assert session.committed is True
    joined = "\n".join(session.sql)
    for marker in ("UPDATE documents", "DELETE FROM theory_claims",
                   "DELETE FROM theory_components", "theory_component_graphs",
                   "revision_status = 'accepted'", "theory_review_events"):
        assert marker in joined
    promoted = next(
        params["promoted"] for params in session.params if "promoted" in params
    )
    assert json.loads(promoted)["claim_object_builder"]["claims"][0]["text"] == "A"
    graph_params = next(
        params for sql, params in zip(session.sql, session.params)
        if "theory_component_graphs" in sql
    )
    stored_graph = json.loads(graph_params["graph"])
    assert stored_graph["nodes"][0]["node_id"] == "dbcomp1"


def test_accept_rolls_back_when_projection_fails(monkeypatch):
    session = _RebuildSession(
        ids=["dbclaim1"], switch_rowcount=1,
        lock_row=("running", "proposed", "base-1", "revision"),
        fail_on="INSERT INTO theory_components",
    )
    monkeypatch.setattr(persistence, "_pg_session", lambda: session)
    with pytest.raises(RuntimeError):
        persistence.accept_revision(
            document_id="doc-1", run_id="rev-1", expected_base_run_id="base-1",
            candidate_artifacts=_candidate(),
        )
    # whole transaction rolled back: active switch + projections undone
    assert session.rolled_back is True
    assert session.committed is False


def test_accept_conflict_skips_projection(monkeypatch):
    session = _RebuildSession(
        switch_rowcount=0,
        lock_row=("running", "proposed", "base-1", "revision"),
    )
    monkeypatch.setattr(persistence, "_pg_session", lambda: session)
    with pytest.raises(RevisionConflictError):
        persistence.accept_revision(
            document_id="doc-1", run_id="rev-1", expected_base_run_id="base-1",
            candidate_artifacts=_candidate(),
        )
    joined = "\n".join(session.sql)
    # conflict detected before any projection write
    assert "DELETE FROM theory_claims" not in joined
    assert session.rolled_back is True
