"""Tests for iterative-improvement run version management (#402).

Covers:
    - migration SQL (019 + runtime migration in main.py) adds version columns
    - active-run vs latest-run helper separation
    - create_revision_run requires base_run_id and only writes the runs table
    - optimistic active-run switch (rowcount-based)
    - run lineage marks active / latest distinctly
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


# --- fake DB session -------------------------------------------------------

class _FakeResult:
    def __init__(self, *, rows=None, one=None, rowcount=0):
        self._rows = rows if rows is not None else []
        self._one = one
        self.rowcount = rowcount

    def mappings(self):
        return self

    def fetchone(self):
        if self._one is not None:
            return self._one
        return self._rows[0] if self._rows else None

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, results):
        self._results = list(results)
        self.executed_sql: list[str] = []
        self.executed_params: list[dict] = []
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def execute(self, statement, params=None):
        self.executed_sql.append(str(statement))
        self.executed_params.append(params or {})
        return self._results.pop(0) if self._results else _FakeResult()

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


@pytest.fixture
def fake_session(monkeypatch):
    holder: dict = {}

    def _install(results):
        session = _FakeSession(results)
        holder["session"] = session
        monkeypatch.setattr(persistence, "_pg_session", lambda: session)
        return session

    holder["install"] = _install
    return holder


# --- migration SQL ---------------------------------------------------------

def test_migration_019_adds_version_columns():
    sql = (ROOT / "backend/db/019_revision_runs.sql").read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS run_type" in sql
    assert "ADD COLUMN IF NOT EXISTS base_run_id" in sql
    assert "ADD COLUMN IF NOT EXISTS parent_revision_id" in sql
    assert "ADD COLUMN IF NOT EXISTS revision_status" in sql
    assert "ADD COLUMN IF NOT EXISTS active_analysis_run_id UUID" in sql
    # revision runs must carry a base_run_id
    assert "run_type <> 'revision' OR base_run_id IS NOT NULL" in sql
    # backfill latest completed run as active, never failed runs
    assert "WHERE status = 'completed'" in sql
    assert "active_analysis_run_id IS NULL" in sql


def test_runtime_migration_in_main_matches_file():
    main_src = (ROOT / "backend/api/main.py").read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS run_type" in main_src
    assert "ADD COLUMN IF NOT EXISTS active_analysis_run_id UUID" in main_src
    assert "document_analysis_runs_base_run_required_chk" in main_src


# --- run-fetch SQL must be alias-qualified (regression: ambiguous "id") -------

def test_get_active_analysis_run_columns_are_alias_qualified(fake_session):
    session = fake_session["install"]([_FakeResult(rows=[])])
    persistence.get_active_analysis_run(document_id="doc-1")
    sql = session.executed_sql[0]
    # joins documents (shares column names) -> columns must be r.-qualified
    assert "JOIN documents d" in sql
    assert "r.id::text" in sql
    assert "SELECT id::text" not in sql  # no bare ambiguous column


def test_get_analysis_run_aliases_run_table(fake_session):
    session = fake_session["install"]([_FakeResult(rows=[])])
    persistence.get_analysis_run(run_id="run-1")
    sql = session.executed_sql[0]
    assert "FROM document_analysis_runs r" in sql
    assert "r.id::text" in sql
    assert "WHERE r.id = CAST" in sql


# --- create_revision_run ---------------------------------------------------

def test_create_revision_run_requires_base_run_id():
    with pytest.raises(ValueError):
        persistence.create_revision_run(document_id="doc-1", base_run_id="")


def test_create_revision_run_only_writes_runs_table(fake_session):
    session = fake_session["install"]([_FakeResult(one=("rev-run-1",))])
    run_id = persistence.create_revision_run(
        document_id="doc-1",
        base_run_id="base-run-1",
        created_by="11111111-1111-1111-1111-111111111111",
    )
    assert run_id == "rev-run-1"
    assert session.committed is True
    joined = "\n".join(session.executed_sql).lower()
    # candidate creation must NOT touch projection tables
    assert "insert into document_analysis_runs" in joined
    assert "theory_claims" not in joined
    assert "theory_components" not in joined
    assert "theory_component_graphs" not in joined
    # records run_type=revision and base linkage
    assert "'revision'" in "\n".join(session.executed_sql)
    assert session.executed_params[0]["base_run_id"] == "base-run-1"


# --- optimistic active-run switch ------------------------------------------

def test_set_active_run_returns_true_on_single_row(fake_session):
    fake_session["install"]([_FakeResult(rowcount=1)])
    ok = persistence.set_active_analysis_run(
        document_id="doc-1", run_id="cand", expected_run_id="base",
    )
    assert ok is True


def test_set_active_run_returns_false_on_conflict(fake_session):
    session = fake_session["install"]([_FakeResult(rowcount=0)])
    ok = persistence.set_active_analysis_run(
        document_id="doc-1", run_id="cand", expected_run_id="stale-base",
    )
    assert ok is False
    # optimistic guard present in SQL
    assert "IS NOT DISTINCT FROM" in session.executed_sql[0]


# --- run lineage -----------------------------------------------------------

def test_get_run_lineage_marks_active_and_latest(fake_session):
    runs = [
        {"id": "r1", "status": "completed", "run_type": "initial",
         "base_run_id": None, "parent_revision_id": None, "revision_status": None,
         "current_stage": "completed", "created_by": None,
         "created_at": "t1", "completed_at": "t1"},
        {"id": "r2", "status": "proposed", "run_type": "revision",
         "base_run_id": "r1", "parent_revision_id": None, "revision_status": "proposed",
         "current_stage": None, "created_by": None,
         "created_at": "t2", "completed_at": None},
    ]
    fake_session["install"]([
        _FakeResult(one=("r1",)),   # active id
        _FakeResult(rows=runs),     # lineage rows
    ])
    lineage = persistence.get_run_lineage(document_id="doc-1")
    assert lineage["active_run_id"] == "r1"
    assert lineage["latest_run_id"] == "r2"
    by_id = {r["id"]: r for r in lineage["runs"]}
    assert by_id["r1"]["is_active"] is True
    assert by_id["r1"]["is_latest"] is False
    assert by_id["r2"]["is_active"] is False
    assert by_id["r2"]["is_latest"] is True


def test_resolve_artifact_run_prefers_active(monkeypatch):
    monkeypatch.setattr(
        persistence, "get_active_analysis_run",
        lambda *, document_id: {"id": "active-run", "status": "completed"},
    )
    monkeypatch.setattr(
        persistence, "get_latest_analysis_run",
        lambda *, document_id, material_id=None: {"id": "latest-run", "status": "completed"},
    )
    run = persistence.resolve_artifact_run(document_id="doc-1")
    assert run["id"] == "active-run"


def test_resolve_artifact_run_falls_back_to_latest_completed(monkeypatch):
    monkeypatch.setattr(
        persistence, "get_active_analysis_run", lambda *, document_id: None,
    )
    monkeypatch.setattr(
        persistence, "get_latest_analysis_run",
        lambda *, document_id, material_id=None: {"id": "latest-run", "status": "completed"},
    )
    run = persistence.resolve_artifact_run(document_id="doc-1")
    assert run["id"] == "latest-run"


def test_resolve_artifact_run_ignores_latest_when_not_completed(monkeypatch):
    monkeypatch.setattr(
        persistence, "get_active_analysis_run", lambda *, document_id: None,
    )
    monkeypatch.setattr(
        persistence, "get_latest_analysis_run",
        lambda *, document_id, material_id=None: {"id": "latest-run", "status": "running"},
    )
    assert persistence.resolve_artifact_run(document_id="doc-1") is None
