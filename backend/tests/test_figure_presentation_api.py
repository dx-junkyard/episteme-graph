from __future__ import annotations

import sys
from pathlib import Path

import pytest

try:
    import fastapi  # noqa: F401
    _HAS_FASTAPI = True
except ImportError:
    _HAS_FASTAPI = False

pytestmark = pytest.mark.skipif(not _HAS_FASTAPI, reason="FastAPI not installed")

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for path in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if path not in sys.path:
        sys.path.insert(0, path)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": "Bearer " + token}


@pytest.fixture
def client_and_tokens():
    from fastapi.testclient import TestClient
    from api.main import app
    from dependencies import ROLE_STUDENT, ROLE_TEACHER, _create_token

    client = TestClient(app)
    student = _create_token(
        "11111111-1111-1111-1111-111111111111", "stu", "stu@x", ROLE_STUDENT
    )
    teacher = _create_token(
        "22222222-2222-2222-2222-222222222222", "tea", "tea@x", ROLE_TEACHER
    )
    return client, student, teacher


def test_only_one_figures_get_route_is_registered():
    from api.main import app
    from routes import admin, figure_presentation

    # FastAPI 0.139 keeps lazy include-router wrappers in app.routes; OpenAPI
    # is the stable flattened registry.  Inspect both source routers as well so
    # a duplicate hidden by OpenAPI's same-path overwrite cannot pass.
    path = "/api/admin/documents/{document_id}/figures"
    legacy_gets = [
        route for route in admin.router.routes
        if getattr(route, "path", "") == path
        and "GET" in (getattr(route, "methods", set()) or set())
    ]
    enriched_gets = [
        route for route in figure_presentation.router.routes
        if getattr(route, "path", "") == path
        and "GET" in (getattr(route, "methods", set()) or set())
    ]
    assert legacy_gets == []
    assert len(enriched_gets) == 1
    operation = app.openapi()["paths"]["/api/admin/documents/{document_id}/figures"]["get"]
    assert operation["operationId"].startswith("list_document_figures_with_presentation_")


def test_patch_requires_teacher(client_and_tokens):
    client, student, _teacher = client_and_tokens
    path = "/api/admin/documents/doc-1/figures/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa/presentation-mode"
    assert client.patch(path, json={"presentation_mode": "data_plot"}).status_code in (401, 403)
    assert client.patch(
        path, json={"presentation_mode": "data_plot"}, headers=_auth(student)
    ).status_code == 403


def test_reanalysis_requires_teacher(client_and_tokens):
    client, student, _teacher = client_and_tokens
    path = "/api/admin/documents/doc-1/figures/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa/reanalyze"
    assert client.post(path).status_code in (401, 403)
    assert client.post(path, headers=_auth(student)).status_code == 403


def test_teacher_reanalysis_returns_unconfirmed_structured_candidate(
    client_and_tokens, monkeypatch
):
    client, _student, teacher = client_and_tokens
    import routes.figure_presentation as routes

    monkeypatch.setattr(
        routes,
        "_ensure_document_editable",
        lambda *_args, **_kwargs: [{"document_id": "doc-canonical"}],
    )
    calls = []

    def fake_reanalyze(document_id, figure_id, *, created_by):
        calls.append((document_id, figure_id, created_by))
        return {
            "figure_id": figure_id,
            "suggested_mode": "descriptive_image",
            "mode_reason": "vision",
            "analysis_profile": {"summary": "Laser の写真"},
            "annotation": {
                "id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
                "kind": "decomposition",
                "body": {
                    "candidate_type": "figure_analysis",
                    "text": "Laser の写真",
                    "presentation_mode": "descriptive_image",
                    "analysis_profile": {"summary": "Laser の写真"},
                },
                "evidence": ["原図"],
                "reason": "vision",
                "confidence": 0.8,
                "status": "candidate",
                "created_at": "2026-07-17T00:00:00+00:00",
            },
        }

    monkeypatch.setattr(routes.figure_reanalysis, "reanalyze_figure", fake_reanalyze)
    monkeypatch.setattr(routes, "record_review_event", lambda *args, **kwargs: None)
    path = "/api/admin/documents/doc-1/figures/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa/reanalyze"
    response = client.post(path, headers=_auth(teacher))
    assert response.status_code == 200
    body = response.json()
    assert body["suggested_mode"] == "descriptive_image"
    assert body["annotation"]["status"] == "candidate"
    assert body["annotation"]["commit_supported"] is True
    assert "confidence" not in body["annotation"]
    assert calls[0][0] == "doc-canonical"


def test_reanalysis_limit_is_mapped_to_429(client_and_tokens, monkeypatch):
    client, _student, teacher = client_and_tokens
    import routes.figure_presentation as routes

    monkeypatch.setattr(
        routes,
        "_ensure_document_editable",
        lambda *_args, **_kwargs: [{"document_id": "doc-canonical"}],
    )

    def fail(*_args, **_kwargs):
        raise routes.figure_reanalysis.FigureReanalysisError("上限です", kind="limit")

    monkeypatch.setattr(routes.figure_reanalysis, "reanalyze_figure", fail)
    path = "/api/admin/documents/doc-1/figures/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa/reanalyze"
    response = client.post(path, headers=_auth(teacher))
    assert response.status_code == 429
    assert response.json()["detail"] == "上限です"


def test_teacher_can_review_and_clear_mode(client_and_tokens, monkeypatch):
    client, _student, teacher = client_and_tokens
    import routes.figure_presentation as routes

    monkeypatch.setattr(
        routes,
        "_ensure_document_editable",
        lambda *_args, **_kwargs: [{"document_id": "doc-canonical"}],
    )
    calls: list[tuple] = []

    def fake_set(document_id, figure_id, mode, user_id):
        calls.append((document_id, figure_id, mode, user_id))
        return {
            "old": {"reviewed_mode": ""},
            "new": {"reviewed_mode": mode},
            "suggested_mode": "data_plot",
            "reviewed_mode": mode,
            "effective_mode": mode or "data_plot",
            "mode_reason": "vision",
            "mode_review_status": "reviewed" if mode else "pending",
            "analysis_profile": {},
        }

    monkeypatch.setattr(routes, "set_reviewed_mode", fake_set)
    monkeypatch.setattr(routes, "record_review_event", lambda *args, **kwargs: None)
    path = "/api/admin/documents/doc-1/figures/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa/presentation-mode"
    response = client.patch(
        path,
        json={"presentation_mode": "functional_diagram"},
        headers=_auth(teacher),
    )
    assert response.status_code == 200
    assert response.json()["effective_mode"] == "functional_diagram"

    cleared = client.patch(path, json={"presentation_mode": None}, headers=_auth(teacher))
    assert cleared.status_code == 200
    assert cleared.json()["reviewed_mode"] is None
    assert calls[-1][2] is None


def test_figures_get_returns_modes_profile_and_legacy_connections(
    client_and_tokens, monkeypatch
):
    client, _student, teacher = client_and_tokens
    import routes.figure_presentation as routes

    figure_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    monkeypatch.setattr(
        routes,
        "_ensure_document_viewable",
        lambda *_args, **_kwargs: [{"document_id": "doc-canonical"}],
    )
    monkeypatch.setattr(routes, "load_document_figures", lambda _doc: [{
        "id": figure_id,
        "figure_key": "fig_1",
        "caption_text": "Schematic of a detector",
        "status": "extracted",
        "suggested_mode": "functional_diagram",
        "reviewed_mode": None,
        "analysis_profile": {},
    }])
    monkeypatch.setattr(routes, "_latest_records", lambda _doc: {figure_id: {
        "figure_id": figure_id,
        "suggested_mode": "functional_diagram",
        "apparatus_name_candidate": "detector",
        "parts": [{"name": "sensor", "role": "detects"}],
        "connections": [{
            "from_part": "sensor", "to_part": "readout", "relation": "signal"
        }],
    }})

    response = client.get(
        "/api/admin/documents/doc-1/figures", headers=_auth(teacher)
    )
    assert response.status_code == 200
    figure = response.json()["figures"][0]
    assert figure["effective_mode"] == "functional_diagram"
    assert "analysis_profile" in figure
    connection = figure["apparatus_candidates"][0]["connections"][0]
    assert connection["from_part"] == "sensor"


def test_selected_context_is_bounded_and_typed():
    from pydantic import ValidationError
    from routes.deliberation import MessageCreateRequest

    request = MessageCreateRequest(
        content="What does this do?",
        selected_context={"kind": "part", "id": "f1", "label": "Sensor"},
    )
    assert request.selected_context and request.selected_context.kind == "part"
    with pytest.raises(ValidationError):
        MessageCreateRequest(
            content="x",
            selected_context={"kind": "system_prompt", "id": "x", "label": "x"},
        )
    with pytest.raises(ValidationError):
        MessageCreateRequest(
            content="x",
            selected_context={"kind": "part", "id": "x", "label": "x" * 301},
        )


def test_migration_052_has_separate_suggestion_and_teacher_review_columns():
    sql = (BACKEND / "db" / "052_figure_presentation_modes.sql").read_text()
    for token in (
        "suggested_mode", "analysis_profile", "reviewed_mode", "mode_review_status",
        "mode_reviewed_by", "mode_reviewed_at",
    ):
        assert token in sql
