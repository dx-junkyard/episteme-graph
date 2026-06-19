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
        "component_graph": {"nodes": [{"node_id": "cmp_1"}], "edges": []},
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
    persistence.update_revision_status(run_id=rev, revision_status="proposed",
                                       stage_outputs={"_artifacts": {}})

    out = persistence.accept_revision(
        document_id=doc_id, run_id=rev, expected_base_run_id=base_id,
        candidate_artifacts=_artifacts()["_artifacts"],
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
