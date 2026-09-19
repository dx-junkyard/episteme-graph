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
from tests.knowledge_object_fakes import FakeKnowledgeSession  # noqa: E402


class _Res:
    def __init__(self, session, rowcount=0):
        self._session = session
        self.rowcount = rowcount

    def fetchone(self):
        if self._session.ids:
            return (self._session.ids.pop(0),)
        return None

    # 知識オブジェクト同期は live 行を mappings().all() で読む（§5.2）。
    def mappings(self):
        return self

    def all(self):
        return []


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

def test_rebuild_claims_syncs_without_deleting():
    """accept 経路も DELETE ではなく stable_key 同期（KO3）。"""
    session = FakeKnowledgeSession(id_prefix="db")
    id_map = persistence._rebuild_theory_claims_in_session(session, "doc-1", [
        {"claim_id": "clm_1", "text": "A", "claim_type": "result"},
        {"claim_id": "clm_2", "text": "B", "claim_type": "weird_type"},
    ])
    assert id_map == {"clm_1": "db-1", "clm_2": "db-2"}
    assert not any("DELETE FROM theory_claims" in sql for sql in session.sql)
    rows = session.inserted_into("theory_claims")
    # 語彙外の自称は unknown に丸め、自称そのものは claim_type_text に残す（KO7）。
    assert rows[1]["claim_type"] == "unknown"
    assert rows[1]["claim_type_text"] == "weird_type"
    assert all(row["stable_key"].startswith("k1:") for row in rows)


def test_rebuild_claims_keeps_uuid_when_stable_key_matches():
    from core.knowledge_objects.stable_key import claim_stable_key

    key = claim_stable_key("doc-1", "A", [])
    session = FakeKnowledgeSession(live_rows=[
        {"id": "kept-uuid", "stable_key": key, "agent_id": "clm_old",
         "review_status": "teacher_approved", "created_by": None},
        {"id": "gone-uuid", "stable_key": "k1:stale", "agent_id": "clm_stale",
         "review_status": "teacher_review_required", "created_by": None},
    ])
    id_map = persistence._rebuild_theory_claims_in_session(
        session, "doc-1", [{"claim_id": "clm_1", "text": "A", "claim_type": "result"}],
        run_id="run-1",
    )
    assert id_map == {"clm_1": "kept-uuid"}
    assert session.inserted_into("theory_claims") == []
    # 人間の確定列（review_status）は上書きしない。
    updated = [values for table, _w, values in session.updates if table == "theory_claims"]
    assert all("review_status" not in values for values in updated)
    # 一致しなかった旧 live 行は supersede される（行は残る）。
    assert session.superseded == ["gone-uuid"]


def test_rebuild_components_inserts_links_and_remaps_claims():
    session = FakeKnowledgeSession(id_prefix="cdb")
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
    # links だけが DELETE → 再作成の明示例外。components は DELETE しない（KO3）。
    assert "DELETE FROM theory_component_links" in joined
    assert "DELETE FROM theory_components" not in joined
    assert "INSERT INTO theory_component_links" in joined
    # linked claim remapped to db id in evidence_claims
    comp1 = session.inserted_into("theory_components")[0]
    assert "dbclaim1" in comp1["evidence_claims"]


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


def test_revision_graph_preserves_graph_native_operation_nodes():
    graph = persistence._remap_revision_graph(
        {
            "nodes": [
                {
                    "component_id": "theory_op_0001",
                    "component_type": "TheoryOperationNode",
                    "graph_layer": "main",
                    "representative_component_id": "cmp_1",
                },
                {
                    "component_id": "eq_op_0001",
                    "component_type": "EquationOperationNode",
                    "graph_layer": "equation_detail",
                },
                {"component_id": "cmp_1", "label": "Stored component"},
            ],
            "edges": [
                {"source": "theory_op_0001", "target": "eq_op_0001"},
                {"source": "eq_op_0001", "target": "cmp_1"},
            ],
        },
        {"cmp_1": "db-comp-1"},
        {},
    )
    by_agent = {
        n.get("agent_component_id") or n.get("component_id"): n
        for n in graph["nodes"]
    }
    assert by_agent["theory_op_0001"]["component_id"] == "theory_op_0001"
    assert by_agent["eq_op_0001"]["component_id"] == "eq_op_0001"
    assert by_agent["cmp_1"]["component_id"] == "db-comp-1"
    assert graph["edges"][0]["source"] == "theory_op_0001"
    assert graph["edges"][0]["target"] == "eq_op_0001"
    assert graph["edges"][1]["target"] == "db-comp-1"


def test_revision_graph_still_rejects_unresolved_ordinary_component_node():
    with pytest.raises(ValueError, match="unknown component id"):
        persistence._remap_revision_graph(
            {
                "nodes": [
                    {"component_id": "cmp_missing", "label": "Missing component"},
                ],
                "edges": [],
            },
            {},
            {},
        )


def test_delete_component_graph_issues_scoped_delete_and_commits(monkeypatch):
    # フル再解析経路（orchestrator）で components 再生成・graph スキップ時に呼ばれる。
    session = _RebuildSession()
    monkeypatch.setattr(persistence, "_pg_session", lambda: session)
    persistence.delete_component_graph("doc-9")
    assert session.committed is True
    assert any("DELETE FROM theory_component_graphs" in s for s in session.sql)
    assert session.params[0]["doc"] == "doc-9"


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
    for marker in ("UPDATE documents", "INSERT INTO theory_claims",
                   "INSERT INTO theory_components", "theory_component_graphs",
                   "revision_status = 'accepted'", "theory_review_events"):
        assert marker in joined
    # 投影は DELETE ではなく stable_key 同期（KO3）。
    assert "DELETE FROM theory_claims" not in joined
    assert "DELETE FROM theory_components" not in joined
    # promoted artifacts は生成ログ表へ（stage_outputs の jsonb_set は撤去。§6 / KO6）。
    promoted = {
        params["stage"]: json.loads(params["payload"])
        for sql, params in zip(session.sql, session.params)
        if "document_analysis_artifacts" in sql
    }
    assert promoted["claim_object_builder"]["claims"][0]["text"] == "A"
    graph_params = next(
        params for sql, params in zip(session.sql, session.params)
        if "theory_component_graphs" in sql
    )
    stored_graph = json.loads(graph_params["graph"])
    assert stored_graph["nodes"][0]["node_id"] == "dbcomp1"


def test_accept_deletes_stale_graph_when_candidate_has_no_graph(monkeypatch):
    # 候補に component_graph が無い場合: components は同期される（新規は新 UUID）が
    # グラフは作り直せない → 旧 UUID を指す stale グラフを残さないよう、明示的に
    # DELETE FROM theory_component_graphs を発行して整合させる。
    candidate = _candidate()
    candidate.pop("component_graph")
    session = _RebuildSession(
        ids=["dbclaim1", "dbcomp1"], switch_rowcount=1,
        lock_row=("running", "proposed", "base-1", "revision"),
    )
    monkeypatch.setattr(persistence, "_pg_session", lambda: session)
    out = persistence.accept_revision(
        document_id="doc-1", run_id="rev-1", expected_base_run_id="base-1",
        candidate_artifacts=candidate,
    )
    assert out["accepted"] is True
    assert session.committed is True
    graph_stmts = [s for s in session.sql if "theory_component_graphs" in s]
    # グラフ再構築（INSERT ... ON CONFLICT）ではなく DELETE だけが発行される。
    assert graph_stmts, "expected a theory_component_graphs statement"
    assert all("DELETE FROM theory_component_graphs" in s for s in graph_stmts)
    assert not any("INSERT INTO theory_component_graphs" in s for s in graph_stmts)


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
    assert "INSERT INTO theory_claims" not in joined
    assert session.rolled_back is True
