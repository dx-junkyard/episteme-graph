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

    def fake_reanalyze(document_id, figure_id, *, created_by, guidance=None):
        calls.append((document_id, figure_id, created_by, guidance))
        return {
            "figure_id": figure_id,
            "suggested_mode": "descriptive_image",
            "mode_reason": "vision",
            "analysis_profile": {"summary": "Laser の写真"},
            "guidance": None,
            "guidance_note": "",
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
    # Body-less request (existing button) is fully backward compatible: no
    # guidance is threaded through to the core call or the response.
    assert calls[0][3] is None
    assert body["guidance"] is None
    assert body["guidance_note"] == ""


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


def test_reanalysis_rejects_too_long_hint_with_422(client_and_tokens, monkeypatch):
    """§4-1: value-range validation is the core's job (_normalize_guidance),
    but the route must still surface it as 422. The real reanalyze_figure
    runs here (not mocked) — it raises before touching storage/DB, so no
    document/storage fixture is needed."""
    client, _student, teacher = client_and_tokens
    import routes.figure_presentation as routes

    monkeypatch.setattr(
        routes,
        "_ensure_document_editable",
        lambda *_args, **_kwargs: [{"document_id": "doc-canonical"}],
    )
    path = "/api/admin/documents/doc-1/figures/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa/reanalyze"
    response = client.post(
        path, json={"hint_text": "x" * 2001}, headers=_auth(teacher)
    )
    assert response.status_code == 422
    assert "長すぎ" in response.json()["detail"]


def test_reanalysis_rejects_invalid_focus_bbox_with_422(client_and_tokens, monkeypatch):
    client, _student, teacher = client_and_tokens
    import routes.figure_presentation as routes

    monkeypatch.setattr(
        routes,
        "_ensure_document_editable",
        lambda *_args, **_kwargs: [{"document_id": "doc-canonical"}],
    )
    path = "/api/admin/documents/doc-1/figures/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa/reanalyze"

    too_small = client.post(
        path,
        json={"focus_bbox": [0.1, 0.1, 0.105, 0.9]},
        headers=_auth(teacher),
    )
    assert too_small.status_code == 422
    assert "小さすぎ" in too_small.json()["detail"]

    out_of_order = client.post(
        path,
        json={"focus_bbox": [0.5, 0.1, 0.1, 0.9]},
        headers=_auth(teacher),
    )
    assert out_of_order.status_code == 422

    out_of_range = client.post(
        path,
        json={"focus_bbox": [-0.1, 0.1, 0.5, 0.9]},
        headers=_auth(teacher),
    )
    assert out_of_range.status_code == 422

    wrong_length = client.post(
        path,
        json={"focus_bbox": [0.1, 0.1, 0.9]},
        headers=_auth(teacher),
    )
    assert wrong_length.status_code == 422


def test_reanalysis_guidance_is_returned_and_audited(client_and_tokens, monkeypatch):
    client, _student, teacher = client_and_tokens
    import routes.figure_presentation as routes

    monkeypatch.setattr(
        routes,
        "_ensure_document_editable",
        lambda *_args, **_kwargs: [{"document_id": "doc-canonical"}],
    )
    calls = []

    def fake_reanalyze(document_id, figure_id, *, created_by, guidance=None):
        calls.append((document_id, figure_id, created_by, guidance))
        return {
            "figure_id": figure_id,
            "suggested_mode": "functional_diagram",
            "mode_reason": "vision",
            "analysis_profile": {},
            "guidance": {
                "hint_text": "左下のEOMと書かれた箱が変調器",
                "focus_bbox": [0.1, 0.1, 0.5, 0.5],
            },
            "guidance_note": "指示された部品を検出しました",
            "annotation": {
                "id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
                "kind": "decomposition",
                "body": {"candidate_type": "figure_analysis"},
                "evidence": [],
                "reason": "教員指示付き再解析: vision",
                "confidence": 0.8,
                "status": "candidate",
                "created_at": "2026-07-17T00:00:00+00:00",
            },
        }

    monkeypatch.setattr(routes.figure_reanalysis, "reanalyze_figure", fake_reanalyze)
    audit_calls = []
    monkeypatch.setattr(
        routes, "record_review_event", lambda *args, **kwargs: audit_calls.append(args)
    )

    path = "/api/admin/documents/doc-1/figures/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa/reanalyze"
    response = client.post(
        path,
        json={
            "hint_text": "左下のEOMと書かれた箱が変調器",
            "focus_bbox": [0.1, 0.1, 0.5, 0.5],
        },
        headers=_auth(teacher),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["guidance"] == {
        "hint_text": "左下のEOMと書かれた箱が変調器",
        "focus_bbox": [0.1, 0.1, 0.5, 0.5],
    }
    assert body["guidance_note"] == "指示された部品を検出しました"
    assert calls[0][3] == {
        "hint_text": "左下のEOMと書かれた箱が変調器",
        "focus_bbox": [0.1, 0.1, 0.5, 0.5],
    }

    assert len(audit_calls) == 1
    payload = audit_calls[0][5]
    assert payload["guidance"] == {
        "hint_text": "左下のEOMと書かれた箱が変調器",
        "focus_bbox": [0.1, 0.1, 0.5, 0.5],
    }


def test_reanalysis_without_guidance_omits_guidance_key_from_audit_payload(
    client_and_tokens, monkeypatch
):
    client, _student, teacher = client_and_tokens
    import routes.figure_presentation as routes

    monkeypatch.setattr(
        routes,
        "_ensure_document_editable",
        lambda *_args, **_kwargs: [{"document_id": "doc-canonical"}],
    )

    def fake_reanalyze(document_id, figure_id, *, created_by, guidance=None):
        return {
            "figure_id": figure_id,
            "suggested_mode": "functional_diagram",
            "mode_reason": "vision",
            "analysis_profile": {},
            "guidance": None,
            "guidance_note": "",
            "annotation": {
                "id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
                "kind": "decomposition",
                "body": {"candidate_type": "figure_analysis"},
                "evidence": [],
                "reason": "vision",
                "confidence": 0.8,
                "status": "candidate",
                "created_at": "2026-07-17T00:00:00+00:00",
            },
        }

    monkeypatch.setattr(routes.figure_reanalysis, "reanalyze_figure", fake_reanalyze)
    audit_calls = []
    monkeypatch.setattr(
        routes, "record_review_event", lambda *args, **kwargs: audit_calls.append(args)
    )

    path = "/api/admin/documents/doc-1/figures/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa/reanalyze"
    response = client.post(path, headers=_auth(teacher))
    assert response.status_code == 200
    assert len(audit_calls) == 1
    payload = audit_calls[0][5]
    assert "guidance" not in payload


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


class _FakeDocumentAccess:
    def __init__(self, is_owner: bool):
        self.is_owner = is_owner
        self.can_view = True
        self.can_edit = is_owner
        self.document_id = "doc-canonical"


def _patch_figures_get(routes, monkeypatch, *, figures=None, records=None, is_owner=False):
    monkeypatch.setattr(
        routes,
        "_ensure_document_viewable",
        lambda *_args, **_kwargs: [{"document_id": "doc-canonical"}],
    )
    monkeypatch.setattr(routes, "load_document_figures", lambda _doc: list(figures or []))
    monkeypatch.setattr(routes, "_latest_records", lambda _doc: dict(records or {}))
    monkeypatch.setattr(
        routes,
        "resolve_document_access",
        lambda _uid, _ref: _FakeDocumentAccess(is_owner=is_owner),
    )


def test_figures_get_returns_modes_profile_and_legacy_connections(
    client_and_tokens, monkeypatch
):
    client, _student, teacher = client_and_tokens
    import routes.figure_presentation as routes

    figure_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    _patch_figures_get(
        routes,
        monkeypatch,
        figures=[{
            "id": figure_id,
            "figure_key": "fig_1",
            "caption_text": "Schematic of a detector",
            "status": "extracted",
            "suggested_mode": "functional_diagram",
            "reviewed_mode": None,
            "analysis_profile": {},
        }],
        records={figure_id: {
            "figure_id": figure_id,
            "suggested_mode": "functional_diagram",
            "apparatus_name_candidate": "detector",
            "parts": [{"name": "sensor", "role": "detects"}],
            "connections": [{
                "from_part": "sensor", "to_part": "readout", "relation": "signal"
            }],
        }},
    )

    response = client.get(
        "/api/admin/documents/doc-1/figures", headers=_auth(teacher)
    )
    assert response.status_code == 200
    figure = response.json()["figures"][0]
    assert figure["effective_mode"] == "functional_diagram"
    assert "analysis_profile" in figure
    connection = figure["apparatus_candidates"][0]["connections"][0]
    assert connection["from_part"] == "sensor"


def test_figures_get_reports_viewer_is_owner_true_for_document_owner(
    client_and_tokens, monkeypatch
):
    """所有者には viewer_is_owner=true（例示画像チェックの活性化条件。フロント契約）。"""
    client, _student, teacher = client_and_tokens
    import routes.figure_presentation as routes

    seen = {}

    def fake_resolve(uid, ref):
        seen["args"] = (uid, ref)
        return _FakeDocumentAccess(is_owner=True)

    _patch_figures_get(routes, monkeypatch, is_owner=True)
    monkeypatch.setattr(routes, "resolve_document_access", fake_resolve)

    response = client.get("/api/admin/documents/doc-1/figures", headers=_auth(teacher))
    assert response.status_code == 200
    assert response.json()["viewer_is_owner"] is True
    # 1回だけの集約判定（document 単位）。document_id は URL パラメータのまま渡してよい
    # （resolve_document_access が UUID / material_id の両対応で解決する）。
    assert seen["args"] == ("22222222-2222-2222-2222-222222222222", "doc-1")


def test_figures_get_reports_viewer_is_owner_false_for_group_viewer(
    client_and_tokens, monkeypatch
):
    """グループ共有 viewer（閲覧はできるが所有者ではない）には viewer_is_owner=false。"""
    client, _student, teacher = client_and_tokens
    import routes.figure_presentation as routes

    _patch_figures_get(routes, monkeypatch, is_owner=False)

    response = client.get("/api/admin/documents/doc-1/figures", headers=_auth(teacher))
    assert response.status_code == 200
    assert response.json()["viewer_is_owner"] is False


def test_figures_get_viewer_is_owner_fail_closed_on_resolution_error(
    client_and_tokens, monkeypatch
):
    """所有者判定に失敗しても 500 にせず、フラグは false（fail-closed）で図一覧は返す。"""
    client, _student, teacher = client_and_tokens
    import routes.figure_presentation as routes

    _patch_figures_get(routes, monkeypatch)

    def boom(*_args, **_kwargs):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(routes, "resolve_document_access", boom)

    response = client.get("/api/admin/documents/doc-1/figures", headers=_auth(teacher))
    assert response.status_code == 200
    assert response.json()["viewer_is_owner"] is False


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
