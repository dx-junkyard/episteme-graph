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

    def close(self):
        pass


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


def test_resolve_artifact_runs_latest_policy_ignores_status():
    """``policy="latest"`` は resume / 進捗表示 専用（成果物には使わない。C-8）。"""
    session = _FakeSession([("doc-1", "run-running", {"_artifacts": {}}, "running")])
    out = persistence.resolve_artifact_runs(session, ["doc-1"], policy="latest")
    assert out["doc-1"]["run_id"] == "run-running"
    targets_cte = session.sql[0].split("SELECT t.document_id")[0]
    assert "active_analysis_run_id" not in targets_cte
    assert "status" not in targets_cte


def test_resolve_artifact_runs_exposes_cartridge_id_when_selected():
    session = _FakeSession([
        ("doc-1", "run-1", {"_artifacts": {}}, "completed", "particle_physics"),
    ])
    out = persistence.resolve_artifact_runs(session, ["doc-1"])
    assert out["doc-1"]["cartridge_id"] == "particle_physics"


def test_resolve_artifact_runs_tolerates_short_rows():
    """列を増やしても既存の短い行（fake session 等）で壊れないこと。"""
    session = _FakeSession([("doc-1", "run-1", {"_artifacts": {}}, "completed")])
    assert persistence.resolve_artifact_runs(session, ["doc-1"])["doc-1"]["cartridge_id"] == ""


def test_document_run_artifacts_returns_artifacts_dict(monkeypatch):
    monkeypatch.setattr(
        persistence, "resolve_artifact_runs",
        lambda session, ids, *, policy="adopted": {
            ids[0]: {"stage_outputs": {"_artifacts": {"equation_semantics": {"equations": []}}}},
        },
    )
    monkeypatch.setattr(persistence, "_pg_session", lambda: _FakeSession([]))
    assert "equation_semantics" in persistence.document_run_artifacts("doc-1")


def test_document_run_artifacts_empty_without_run(monkeypatch):
    monkeypatch.setattr(
        persistence, "resolve_artifact_runs",
        lambda session, ids, *, policy="adopted": {},
    )
    monkeypatch.setattr(persistence, "_pg_session", lambda: _FakeSession([]))
    assert persistence.document_run_artifacts("doc-1") == {}
    # 空 document_id は SQL を発行しない。
    assert persistence.document_run_artifacts("  ") == {}


def test_document_run_cartridge_id_reads_the_same_run(monkeypatch):
    monkeypatch.setattr(
        persistence, "resolve_artifact_runs",
        lambda session, ids, *, policy="adopted": {ids[0]: {"cartridge_id": "astrophysics"}},
    )
    monkeypatch.setattr(persistence, "_pg_session", lambda: _FakeSession([]))
    assert persistence.document_run_cartridge_id("doc-1") == "astrophysics"
    assert persistence.document_run_cartridge_id("") == ""


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
