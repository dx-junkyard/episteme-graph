"""Integration tests for active-run-based artifact references (#408)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
for p in (str(REPO / "src"), str(BACKEND), str(BACKEND / "api")):
    if p not in sys.path:
        sys.path.insert(0, p)

from core.document_pipeline import persistence  # noqa: E402


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows):
        self.rows = rows
        self.sql = []

    def execute(self, statement, params=None):
        self.sql.append(str(statement))
        return _FakeResult(self.rows)


def test_resolve_artifact_runs_prefers_active_then_completed():
    session = _FakeSession([
        ("doc-1", "run-active", {"_artifacts": {"claim_object_builder": {"claims": []}}}, "completed"),
    ])
    out = persistence.resolve_artifact_runs(session, ["doc-1"])
    assert out["doc-1"]["run_id"] == "run-active"
    sql = session.sql[0]
    # active run preferred, with a completed-only fallback (never running/failed)
    assert "active_analysis_run_id" in sql
    assert "status = 'completed'" in sql


def test_resolve_artifact_runs_empty_documents():
    assert persistence.resolve_artifact_runs(_FakeSession([]), []) == {}


def test_resolve_artifact_runs_parses_json_string_stage_outputs():
    import json
    session = _FakeSession([
        ("doc-1", "run-1", json.dumps({"_artifacts": {"x": 1}}), "completed"),
    ])
    out = persistence.resolve_artifact_runs(session, ["doc-1"])
    assert out["doc-1"]["stage_outputs"]["_artifacts"]["x"] == 1


def test_export_loaders_use_active_run(monkeypatch):
    from routes import export

    monkeypatch.setattr(export, "resolve_artifact_runs", lambda session, ids: {
        "doc-1": {"run_id": "active-1",
                  "stage_outputs": {"_artifacts": {"component_assembly": {"components": [{"component_id": "c1"}]}}},
                  "status": "completed"},
    })
    artifacts = export._load_analysis_artifacts(object(), ["doc-1"])
    assert "component_assembly" in artifacts["doc-1"]
    run_ids = export._load_latest_run_ids(object(), ["doc-1"])
    assert run_ids["doc-1"] == "active-1"


def test_course_content_loader_uses_active_run(monkeypatch):
    import core.course_content_builder as ccb

    monkeypatch.setattr(persistence, "resolve_artifact_runs", lambda session, ids: {
        "doc-1": {"run_id": "active-1",
                  "stage_outputs": {"_artifacts": {"course_mapping": {"topics": []}}},
                  "status": "completed"},
    })
    out = ccb._load_latest_artifacts(object(), ["doc-1"])
    assert "course_mapping" in out["doc-1"]
