"""#410: real-PostgreSQL integration for the revision lifecycle.

Runs only when a Postgres URL is provided (EPISTEME_TEST_DATABASE_URL or
DATABASE_URL) and the schema (init.sql + migrations) is present; skips otherwise
so local/mocked runs are unaffected. Exercises the parts that function-level
mocks cannot: real JSONB deep-merge across consecutive stage updates and the
accept transaction's atomic active-switch + projection rebuild + conflict (409).
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

_DB_URL = os.getenv("EPISTEME_TEST_DATABASE_URL") or os.getenv("DATABASE_URL")

pytestmark = pytest.mark.skipif(not _DB_URL, reason="no Postgres URL for integration test")


@pytest.fixture
def pg(monkeypatch):
    try:
        from sqlalchemy import create_engine, text
        from sqlalchemy.orm import sessionmaker
    except Exception:  # pragma: no cover
        pytest.skip("sqlalchemy unavailable")
    engine = create_engine(_DB_URL)
    Session = sessionmaker(bind=engine)
    # Verify the expected schema is present, else skip.
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT active_analysis_run_id FROM documents LIMIT 1"))
            conn.execute(text("SELECT run_type, base_run_id FROM document_analysis_runs LIMIT 1"))
    except Exception:
        pytest.skip("revision schema (migration 019) not applied to test DB")

    from core.document_pipeline import persistence
    monkeypatch.setattr(persistence, "_pg_session", lambda: Session())
    return {"engine": engine, "Session": Session, "text": text, "persistence": persistence}


def _seed_document(pg, artifacts):
    text, Session = pg["text"], pg["Session"]
    doc_id = str(uuid.uuid4())
    s = Session()
    try:
        s.execute(text("INSERT INTO documents (id, title) VALUES (CAST(:id AS uuid), :t)"),
                  {"id": doc_id, "t": "rev-int-test"})
        row = s.execute(text(
            """INSERT INTO document_analysis_runs (document_id, status, run_type, stage_outputs, completed_at)
               VALUES (:doc, 'completed', 'initial', CAST(:so AS jsonb), now()) RETURNING id::text"""),
            {"doc": doc_id, "so": _json(artifacts)}).fetchone()
        base_id = row[0]
        s.execute(text("UPDATE documents SET active_analysis_run_id = CAST(:r AS uuid) WHERE id = CAST(:d AS uuid)"),
                  {"r": base_id, "d": doc_id})
        s.commit()
        return doc_id, base_id
    finally:
        s.close()


def _json(obj):
    import json
    return json.dumps(obj)


def _artifacts():
    return {"_artifacts": {
        "claim_object_builder": {"claims": [{"claim_id": "clm_1", "text": "A", "claim_type": "result"}]},
        "component_assembly": {"components": [{"component_id": "cmp_1", "label": "C1",
                                              "linked_claim_ids": ["clm_1"]}]},
        "component_graph": {
            "nodes": [{"node_id": "cmp_1", "linked_claim_ids": ["clm_1"]}],
            "edges": [{"edge_id": "edge_1", "source": "cmp_1", "target": "cmp_1",
                       "evidence": {"claim_ids": ["clm_1"]}}],
        },
    }}


def test_jsonb_deep_merge_preserves_artifacts_across_stages(pg):
    persistence = pg["persistence"]
    doc_id, base_id = _seed_document(pg, {"_artifacts": {}})
    rev = persistence.create_revision_run(document_id=doc_id, base_run_id=base_id)

    persistence.update_revision_status(run_id=rev, revision_status="preparing",
                                       stage_outputs={"_artifacts": {"baseline_inventory": {"a": 1}}})
    persistence.update_revision_status(run_id=rev, revision_status="auditing",
                                       stage_outputs={"_artifacts": {"audit_results": {"b": 2}}})
    persistence.update_revision_status(run_id=rev, revision_status="proposed",
                                       stage_outputs={"_artifacts": {"candidate": {"c": 3}}})

    run = persistence.get_analysis_run(run_id=rev)
    arts = run["stage_outputs"]["_artifacts"]
    assert set(arts) == {"baseline_inventory", "audit_results", "candidate"}


def test_accept_switches_active_and_rebuilds_projection_atomically(pg):
    persistence = pg["persistence"]
    text, Session = pg["text"], pg["Session"]
    doc_id, base_id = _seed_document(pg, {"_artifacts": {}})
    rev = persistence.create_revision_run(document_id=doc_id, base_run_id=base_id)
    candidate = _artifacts()["_artifacts"]
    persistence.update_revision_status(run_id=rev, revision_status="proposed",
                                       stage_outputs={"_artifacts": {
                                           "candidate": {"candidate_artifacts": candidate},
                                           "diff_report": {"summary": {"acceptable": True}},
                                       }})

    out = persistence.accept_revision(
        document_id=doc_id, run_id=rev, expected_base_run_id=base_id,
        candidate_artifacts=candidate,
    )
    assert out["accepted"] is True
    s = Session()
    try:
        active = s.execute(text("SELECT active_analysis_run_id::text FROM documents WHERE id = CAST(:d AS uuid)"),
                           {"d": doc_id}).fetchone()[0]
        assert active == rev
        claims = s.execute(text("SELECT count(*) FROM theory_claims WHERE document_id = :d"),
                           {"d": doc_id}).fetchone()[0]
        assert claims == 1
        stage_outputs = s.execute(text(
            "SELECT stage_outputs FROM document_analysis_runs WHERE id = CAST(:r AS uuid)"
        ), {"r": rev}).fetchone()[0]
        artifacts = stage_outputs["_artifacts"]
        assert artifacts["claim_object_builder"]["claims"][0]["text"] == "A"
        assert artifacts["candidate"]["candidate_artifacts"]["claim_object_builder"]
        assert artifacts["diff_report"]["summary"]["acceptable"] is True

        claim_id = s.execute(text(
            "SELECT id::text FROM theory_claims WHERE document_id = :d"
        ), {"d": doc_id}).fetchone()[0]
        component_id = s.execute(text(
            "SELECT id::text FROM theory_components WHERE document_id = :d"
        ), {"d": doc_id}).fetchone()[0]
        graph = s.execute(text(
            "SELECT graph_json FROM theory_component_graphs WHERE document_id = :d"
        ), {"d": doc_id}).fetchone()[0]
        assert graph["nodes"][0]["node_id"] == component_id
        assert graph["nodes"][0]["linked_claim_ids"] == [claim_id]
        assert graph["edges"][0]["source"] == component_id
        assert graph["edges"][0]["target"] == component_id
        assert graph["edges"][0]["evidence"]["claim_ids"] == [claim_id]
    finally:
        s.close()


def test_stale_base_accept_conflicts(pg):
    persistence = pg["persistence"]
    from core.document_pipeline.persistence import RevisionConflictError
    doc_id, base_id = _seed_document(pg, {"_artifacts": {}})
    rev = persistence.create_revision_run(document_id=doc_id, base_run_id=base_id)
    persistence.update_revision_status(run_id=rev, revision_status="proposed",
                                       stage_outputs={"_artifacts": {}})
    with pytest.raises(RevisionConflictError):
        persistence.accept_revision(document_id=doc_id, run_id=rev,
                                    expected_base_run_id="00000000-0000-0000-0000-000000000000",
                                    candidate_artifacts={})


def _seed_revision(pg, *, with_checkpoints=False):
    persistence = pg["persistence"]
    doc_id, base_id = _seed_document(pg, {"_artifacts": {}})
    rev = persistence.create_revision_run(document_id=doc_id, base_run_id=base_id)
    if with_checkpoints:
        persistence.update_revision_status(
            run_id=rev, revision_status="preparing",
            stage_outputs={"_artifacts": {
                "baseline_inventory": {"entities": {}, "unresolved_references": []},
                "audit_checkpoints": [
                    {"checkpoint_id": "ckpt_1", "target_type": "claim", "target_id": "clm_1",
                     "trigger_reason": "non_atomic_claim", "source_scope": [], "status": "planned"},
                    {"checkpoint_id": "ckpt_2", "target_type": "claim", "target_id": "clm_1",
                     "trigger_reason": "low_confidence", "source_scope": [], "status": "planned"},
                ],
            }})
    return doc_id, base_id, rev


# --- #414-1: atomic run-claim exclusion (real concurrency) -----------------

def test_concurrent_run_claim_is_exclusive(pg):
    import concurrent.futures
    persistence = pg["persistence"]
    _doc, _base, rev = _seed_revision(pg)

    workers = 8
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(lambda _i: persistence.claim_revision_run(run_id=rev),
                              range(workers)))
    # Exactly one POST wins the claim; the rest must get 409.
    assert sum(1 for r in results if r) == 1
    # A further claim while running fails too.
    assert persistence.claim_revision_run(run_id=rev) is False
    # Releasing (task/worker launch failure path) makes it re-claimable.
    persistence.release_revision_run(run_id=rev, error_message="launch failed")
    run = persistence.get_analysis_run(run_id=rev)
    assert run["status"] == "failed"
    assert persistence.claim_revision_run(run_id=rev) is True


def test_different_revisions_claim_independently(pg):
    persistence = pg["persistence"]
    _d1, _b1, rev1 = _seed_revision(pg)
    _d2, _b2, rev2 = _seed_revision(pg)
    assert persistence.claim_revision_run(run_id=rev1) is True
    assert persistence.claim_revision_run(run_id=rev2) is True  # independent runs


# --- #414-3: status transitions persisted to real DB -----------------------

def test_run_pipeline_persists_status_transitions(pg):
    persistence = pg["persistence"]
    from core.document_pipeline.revision import coordinator
    _doc, _base, rev = _seed_revision(pg, with_checkpoints=True)
    assert persistence.claim_revision_run(run_id=rev) is True  # endpoint claim
    coordinator.run_revision_pipeline(run_id=rev)
    run = persistence.get_analysis_run(run_id=rev)
    assert run["status"] == "completed"
    assert run["current_stage"] == "completed"
    assert run["revision_status"] == "proposed"
    assert run["started_at"] is not None
    assert run["completed_at"] is not None


# --- #414-2: revision task stage not overwritten by the initial run --------

def test_revision_task_stage_not_overwritten_by_initial_run(pg, monkeypatch):
    Session, text = pg["Session"], pg["text"]
    try:
        import api.services as services
    except Exception:  # pragma: no cover
        pytest.skip("api.services unavailable")
    monkeypatch.setattr(services, "_pg_session", lambda: Session())

    doc_id, base_id, rev = _seed_revision(pg)
    task_id = "rt_" + uuid.uuid4().hex[:9]   # unique so reruns don't collide
    s = Session()
    try:
        try:
            s.execute(text("SELECT 1 FROM background_tasks LIMIT 1"))
        except Exception:
            pytest.skip("background_tasks table not present")
        s.execute(text("UPDATE document_analysis_runs SET current_stage='document_pipeline' "
                       "WHERE id=CAST(:r AS uuid)"), {"r": base_id})
        s.execute(text("UPDATE document_analysis_runs SET current_stage='audit' "
                       "WHERE id=CAST(:r AS uuid)"), {"r": rev})
        s.execute(text("""INSERT INTO background_tasks (id, task_type, status, result_data)
                          VALUES (:id, 'revision_pipeline', 'processing', CAST(:rd AS jsonb))"""),
                  {"id": task_id, "rd": _json({
                      "document_id": doc_id, "revision_run_id": rev,
                      "stage": "audit", "total_count": 2, "completed_count": 1})})
        s.commit()
    finally:
        s.close()

    task = services.get_background_task(task_id)
    # NOT overwritten by the initial run's 'document_pipeline' current_stage.
    assert task["result_data"]["stage"] == "audit"
    assert task["result_data"]["completed_count"] == 1
    latest = services.get_latest_revision_task(rev)
    assert latest and latest["task_id"] == task_id and latest["stage"] == "audit"


def test_reject_leaves_active_unchanged(pg):
    persistence = pg["persistence"]
    text, Session = pg["text"], pg["Session"]
    doc_id, base_id = _seed_document(pg, {"_artifacts": {}})
    rev = persistence.create_revision_run(document_id=doc_id, base_run_id=base_id)
    persistence.update_revision_status(run_id=rev, revision_status="proposed",
                                       stage_outputs={"_artifacts": {}})
    persistence.reject_revision(run_id=rev, comment="no")
    s = Session()
    try:
        active = s.execute(text("SELECT active_analysis_run_id::text FROM documents WHERE id = CAST(:d AS uuid)"),
                           {"d": doc_id}).fetchone()[0]
        assert active == base_id  # unchanged
    finally:
        s.close()
