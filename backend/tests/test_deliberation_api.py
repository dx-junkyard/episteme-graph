"""W層 Phase 2（対話的検討 + 候補注釈 + コミットルーティング）API の結合テスト
（TestClient + monkeypatch、`test_reconstruction_api.py` と同型）。

認証・RBAC・入力検証は DB アクセス前に評価されるため実 DB なしで検証できる。
happy-path / エラーパスは core の DB プリミティブ（``core.deliberation.store`` /
``core.deliberation.dialogue`` / ``core.deliberation.annotations``）・権限ゲート
（``_ensure_document_viewable``/``_ensure_document_editable``）・監査記録
（``record_review_event``）を monkeypatch して実 DB・実 LLM なしで 200/4xx まで通す。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from fastapi.testclient import TestClient  # noqa: F401
    _HAS_FASTAPI = True
except Exception:  # pragma: no cover
    _HAS_FASTAPI = False

pytestmark = pytest.mark.skipif(not _HAS_FASTAPI, reason="FastAPI 未導入")


@pytest.fixture
def client_and_tokens():
    from fastapi.testclient import TestClient
    from api.main import app
    from dependencies import _create_token, ROLE_STUDENT, ROLE_TEACHER

    client = TestClient(app)
    student = _create_token("11111111-1111-1111-1111-111111111111", "stu", "stu@x", ROLE_STUDENT)
    teacher = _create_token("22222222-2222-2222-2222-222222222222", "tea", "tea@x", ROLE_TEACHER)
    return client, student, teacher


def _auth(tok):
    return {"Authorization": "Bearer " + tok}


def _fake_document_ref(element_type="theory_claim", element_id="c1", document_id="doc-1"):
    from core.deliberation.schema import ElementRef, SCOPE_DOCUMENT

    return ElementRef(scope=SCOPE_DOCUMENT, element_type=element_type, element_id=element_id, document_id=document_id)


def _fake_shared_part_ref(element_id="sp-1", domain_key="particle_physics"):
    from core.deliberation.schema import ELEMENT_SHARED_PART, ElementRef, SCOPE_DOMAIN

    return ElementRef(
        scope=SCOPE_DOMAIN, element_type=ELEMENT_SHARED_PART, element_id=element_id, domain_key=domain_key,
    )


class TestAuthRequired:
    """全経路が未認証で 401/403 を返す（TestPermissionGate の実行時側）。"""

    @pytest.mark.parametrize(
        "method,path",
        [
            ("post", "/api/admin/deliberation/sessions"),
            ("get", "/api/admin/deliberation/sessions/00000000-0000-0000-0000-000000000000"),
            ("post", "/api/admin/deliberation/sessions/00000000-0000-0000-0000-000000000000/messages"),
            ("get", "/api/admin/deliberation/elements/theory_claim/c1/annotations"),
            ("post", "/api/admin/deliberation/annotations/00000000-0000-0000-0000-000000000000/commit"),
            ("post", "/api/admin/deliberation/annotations/00000000-0000-0000-0000-000000000000/dismiss"),
            ("post", "/api/admin/deliberation/shared-parts/sp-1/standardization/assess"),
            ("post", "/api/admin/deliberation/domains/particle_physics/standardization/assess"),
        ],
    )
    def test_unauthenticated_request_is_rejected(self, client_and_tokens, method, path):
        client, _s, _t = client_and_tokens
        if method == "post":
            response = client.post(path, json={})
        else:
            response = client.get(path)
        assert response.status_code in (401, 403)


class TestRbacStudentRejected:
    def test_student_cannot_create_session(self, client_and_tokens):
        client, student, _t = client_and_tokens
        response = client.post(
            "/api/admin/deliberation/sessions",
            json={"scope": "document", "element_type": "theory_claim", "element_id": "c1"},
            headers=_auth(student),
        )
        assert response.status_code == 403

    def test_student_cannot_commit_annotation(self, client_and_tokens):
        client, student, _t = client_and_tokens
        response = client.post(
            "/api/admin/deliberation/annotations/00000000-0000-0000-0000-000000000000/commit",
            headers=_auth(student),
        )
        assert response.status_code == 403

    def test_student_cannot_trigger_standardization_assessment(self, client_and_tokens):
        client, student, _t = client_and_tokens
        response = client.post(
            "/api/admin/deliberation/shared-parts/sp-1/standardization/assess",
            headers=_auth(student),
        )
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# 要素中心コンテキストビュー（Issue #498）: GET .../overview の "context" キー
# ---------------------------------------------------------------------------


def _contract_context_dict():
    """`core.deliberation.context_lens.build()` の契約（設計書 §5）に沿った fake 結果。"""
    return {
        "focus": {
            "element_type": "theory_claim",
            "element_id": "c1",
            "document_id": "doc-1",
            "label": "focus label",
            "intrinsic_summary": "summary text",
            "contextual_role": "中心命題を支持する",
            "contextual_role_status": "source_backed",
            "provenance": ["theory_claims:c1"],
        },
        "upper": [
            {
                "element_type": "theory_component",
                "element_id": "comp-1",
                "document_id": "doc-1",
                "label": "upper component",
                "relation": "supports_component",
                "relation_label": "の根拠となる",
                "relation_status": "source_backed",
                "evidence_refs": [],
                "navigable": True,
            }
        ],
        "lower": [],
        "notes": ["note-1"],
    }


class TestOverviewContext:
    """overview 応答の3本目の面（"context"）: available/note/フル契約の3分岐（Task 1）。"""

    PATH = "/api/admin/deliberation/elements/theory_claim/c1/overview"

    def _patch_decomposition_and_positioning(self, monkeypatch, route_mod):
        monkeypatch.setattr(route_mod.refs, "resolve", lambda *a, **k: _fake_document_ref())
        monkeypatch.setattr(route_mod, "_ensure_document_viewable", lambda *a, **k: None)
        monkeypatch.setattr(
            route_mod.decomposition, "build",
            lambda ref: {"element_type": ref.element_type, "label": "L", "fields": {}, "notes": []},
        )
        monkeypatch.setattr(route_mod.positioning, "build", lambda ref: {})

    def test_context_available_returns_full_contract_shape(self, client_and_tokens, monkeypatch):
        client, _s, teacher = client_and_tokens
        import routes.deliberation as route_mod

        self._patch_decomposition_and_positioning(monkeypatch, route_mod)
        monkeypatch.setattr(route_mod.context_lens, "build", lambda ref: _contract_context_dict())

        response = client.get(self.PATH, headers=_auth(teacher))
        assert response.status_code == 200
        context = response.json()["context"]
        assert context["available"] is True
        assert context["focus"]["label"] == "focus label"
        assert context["focus"]["contextual_role_status"] == "source_backed"
        assert context["upper"][0]["relation_label"] == "の根拠となる"
        assert context["lower"] == []
        assert context["notes"] == ["note-1"]

    def test_shared_part_context_is_unavailable_with_factual_note(self, client_and_tokens, monkeypatch):
        client, _s, teacher = client_and_tokens
        import routes.deliberation as route_mod

        monkeypatch.setattr(route_mod.refs, "resolve", lambda *a, **k: _fake_shared_part_ref())
        monkeypatch.setattr(
            route_mod.decomposition, "build",
            lambda ref: {"element_type": "shared_part", "label": "L", "fields": {}, "notes": []},
        )
        monkeypatch.setattr(route_mod.positioning, "build", lambda ref: {})
        monkeypatch.setattr(route_mod.context_lens, "build", lambda ref: None)

        response = client.get(
            "/api/admin/deliberation/elements/shared_part/sp-1/overview",
            headers=_auth(teacher),
        )
        assert response.status_code == 200
        assert response.json()["context"] == {
            "available": False,
            "note": "この要素型にはコンテキスト投影がありません",
        }

    def test_context_builder_exception_is_fail_soft_independent_of_other_lenses(
        self, client_and_tokens, monkeypatch
    ):
        client, _s, teacher = client_and_tokens
        import routes.deliberation as route_mod

        self._patch_decomposition_and_positioning(monkeypatch, route_mod)

        def boom(_ref):
            raise RuntimeError("simulated context_lens outage")

        monkeypatch.setattr(route_mod.context_lens, "build", boom)

        response = client.get(self.PATH, headers=_auth(teacher))
        assert response.status_code == 200
        body = response.json()
        assert body["context"] == {
            "available": False,
            "note": "コンテキスト投影の取得に失敗しました",
        }
        # decomposition/positioning は context の失敗に巻き込まれず引き続き返る（fail-soft の独立性）。
        assert body["decomposition"]["label"] == "L"
        assert body["positioning"]["available"] is True


class TestCreateSessionHappyPath:
    def test_create_session_returns_200_with_session_shape(self, client_and_tokens, monkeypatch):
        client, _s, teacher = client_and_tokens
        import routes.deliberation as route_mod

        monkeypatch.setattr(route_mod.refs, "resolve", lambda *a, **k: _fake_document_ref())
        monkeypatch.setattr(route_mod, "_ensure_document_viewable", lambda *a, **k: None)
        monkeypatch.setattr(
            route_mod.delib_store, "create_session",
            lambda ref, *, title, created_by: {
                "id": "session-1", "scope": ref.scope, "element_type": ref.element_type,
                "element_id": ref.element_id, "document_id": ref.document_id, "domain_key": ref.domain_key,
                "title": title, "messages": [], "created_by": created_by, "created_at": "2026-07-16T00:00:00",
            },
        )
        monkeypatch.setattr(route_mod, "record_review_event", lambda *a, **k: None)

        response = client.post(
            "/api/admin/deliberation/sessions",
            json={"scope": "document", "element_type": "theory_claim", "element_id": "c1", "title": "t"},
            headers=_auth(teacher),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["session"]["id"] == "session-1"
        assert body["session"]["messages"] == []

    def test_scope_mismatch_returns_422(self, client_and_tokens, monkeypatch):
        client, _s, teacher = client_and_tokens
        import routes.deliberation as route_mod

        monkeypatch.setattr(route_mod.refs, "resolve", lambda *a, **k: _fake_document_ref())

        response = client.post(
            "/api/admin/deliberation/sessions",
            json={"scope": "domain", "element_type": "theory_claim", "element_id": "c1"},
            headers=_auth(teacher),
        )
        assert response.status_code == 422


class TestGetSessionOwnership:
    def test_returns_404_for_other_users_session(self, client_and_tokens, monkeypatch):
        client, _s, teacher = client_and_tokens
        import routes.deliberation as route_mod

        monkeypatch.setattr(
            route_mod.delib_store, "get_session_by_id",
            lambda _id: {"id": _id, "created_by": "someone-else", "scope": "document"},
        )
        response = client.get(
            "/api/admin/deliberation/sessions/00000000-0000-0000-0000-000000000000",
            headers=_auth(teacher),
        )
        assert response.status_code == 404

    def test_returns_200_for_own_session(self, client_and_tokens, monkeypatch):
        client, _s, teacher = client_and_tokens
        import routes.deliberation as route_mod

        # _create_token に渡した固定 UUID をそのまま "created_by" として使う
        # （teacher token の user_id と一致させ、所有者ガードを通す）。
        owner_id = "22222222-2222-2222-2222-222222222222"
        monkeypatch.setattr(
            route_mod.delib_store, "get_session_by_id",
            lambda _id: {
                "id": _id, "created_by": owner_id, "scope": "document", "element_type": "theory_claim",
                "element_id": "c1", "document_id": "doc-1", "domain_key": None, "title": "",
                "messages": [], "created_at": "2026-07-16T00:00:00",
            },
        )
        response = client.get(
            "/api/admin/deliberation/sessions/00000000-0000-0000-0000-000000000000",
            headers=_auth(teacher),
        )
        assert response.status_code == 200
        assert response.json()["session"]["id"] == "00000000-0000-0000-0000-000000000000"


class TestPostMessageFlow:
    _OWNER_ID = "22222222-2222-2222-2222-222222222222"

    def _patch_owned_session(self, monkeypatch, route_mod, *, messages=None):
        monkeypatch.setattr(
            route_mod.delib_store, "get_session_by_id",
            lambda _id: {
                "id": _id, "created_by": self._OWNER_ID, "scope": "document", "element_type": "theory_claim",
                "element_id": "c1", "document_id": "doc-1", "domain_key": None, "title": "",
                "messages": messages or [], "created_at": "2026-07-16T00:00:00",
            },
        )
        monkeypatch.setattr(route_mod.refs, "resolve", lambda *a, **k: _fake_document_ref())
        monkeypatch.setattr(route_mod, "_ensure_document_viewable", lambda *a, **k: None)

    def test_empty_content_returns_422(self, client_and_tokens, monkeypatch):
        client, _s, teacher = client_and_tokens
        import routes.deliberation as route_mod

        self._patch_owned_session(monkeypatch, route_mod)
        response = client.post(
            "/api/admin/deliberation/sessions/s1/messages",
            json={"content": "   "},
            headers=_auth(teacher),
        )
        assert response.status_code == 422

    def test_cost_gate_exceeded_returns_429_without_numbers_in_detail(self, client_and_tokens, monkeypatch):
        client, _s, teacher = client_and_tokens
        import routes.deliberation as route_mod

        self._patch_owned_session(monkeypatch, route_mod)
        monkeypatch.setattr(route_mod.dialogue, "check_and_count_llm_call", lambda *a, **k: False)

        response = client.post(
            "/api/admin/deliberation/sessions/s1/messages",
            json={"content": "質問です"},
            headers=_auth(teacher),
        )
        assert response.status_code == 429
        assert not any(c.isdigit() for c in response.json()["detail"])

    def test_happy_path_returns_reply_and_annotations(self, client_and_tokens, monkeypatch):
        client, _s, teacher = client_and_tokens
        import routes.deliberation as route_mod
        from core.deliberation.dialogue import DialogueTurnResult

        seen = {}
        self._patch_owned_session(monkeypatch, route_mod)
        monkeypatch.setattr(route_mod.dialogue, "check_and_count_llm_call", lambda *a, **k: True)
        monkeypatch.setattr(
            route_mod.dialogue, "build_grounding",
            lambda ref: {"breakdown": {}, "positioning": {"available": False}},
        )
        monkeypatch.setattr(route_mod.dialogue, "grounding_to_text", lambda g: "GROUNDING")
        def fake_run_turn(*args, **kwargs):
            seen["llm_user_content"] = kwargs["user_content"]
            return DialogueTurnResult(
                reply="回答です",
                annotations=[{"kind": "meaning", "body": "x", "evidence": ["q"], "reason": "r", "confidence": 0.5}],
                degraded=False,
            )

        def fake_append_messages(_session_id, messages):
            seen["persisted_messages"] = messages

        monkeypatch.setattr(route_mod.dialogue, "run_turn", fake_run_turn)
        monkeypatch.setattr(route_mod.delib_store, "append_messages", fake_append_messages)
        monkeypatch.setattr(
            route_mod.delib_annotations, "create_candidates_from_dialogue",
            lambda *a, **k: [
                {
                    "id": "ann-1", "kind": "meaning", "body": {"text": "x"}, "evidence": ["q"], "reason": "r",
                    "confidence": 0.5, "status": "candidate", "committed_target": {}, "created_at": "",
                }
            ],
        )
        monkeypatch.setattr(route_mod, "record_review_event", lambda *a, **k: None)

        response = client.post(
            "/api/admin/deliberation/sessions/s1/messages",
            json={
                "content": "これは何ですか？",
                "selected_context": {"kind": "part", "id": "sensor-1", "label": "センサー"},
            },
            headers=_auth(teacher),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["reply"] == "回答です"
        assert body["degraded"] is False
        assert len(body["annotations"]) == 1
        assert body["annotations"][0]["confidence_label"] in ("暫定", "参考", "確度高")
        assert "confidence" not in body["annotations"][0]
        assert "kind=part; id=sensor-1; label=センサー" in seen["llm_user_content"]
        assert "not as evidence" in seen["llm_user_content"]
        assert seen["persisted_messages"][0]["content"] == "これは何ですか？"


class TestListAnnotations:
    def test_returns_annotations_for_viewable_element(self, client_and_tokens, monkeypatch):
        client, _s, teacher = client_and_tokens
        import routes.deliberation as route_mod

        monkeypatch.setattr(route_mod.refs, "resolve", lambda *a, **k: _fake_document_ref())
        monkeypatch.setattr(route_mod, "_ensure_document_viewable", lambda *a, **k: None)
        monkeypatch.setattr(
            route_mod.delib_store, "list_annotations_for_element",
            lambda *a, **k: [
                {
                    "id": "ann-1", "kind": "meaning", "body": {"text": "x"}, "evidence": ["q"], "reason": "r",
                    "confidence": None, "status": "candidate", "committed_target": {}, "created_at": "",
                }
            ],
        )
        response = client.get(
            "/api/admin/deliberation/elements/theory_claim/c1/annotations",
            headers=_auth(teacher),
        )
        assert response.status_code == 200
        assert len(response.json()["annotations"]) == 1


class TestCommitAnnotation:
    def test_returns_404_when_annotation_missing(self, client_and_tokens, monkeypatch):
        client, _s, teacher = client_and_tokens
        import routes.deliberation as route_mod

        monkeypatch.setattr(route_mod.delib_store, "get_annotation", lambda _id: None)
        response = client.post(
            "/api/admin/deliberation/annotations/00000000-0000-0000-0000-000000000000/commit",
            headers=_auth(teacher),
        )
        assert response.status_code == 404

    def test_unsupported_kind_returns_422(self, client_and_tokens, monkeypatch):
        client, _s, teacher = client_and_tokens
        import routes.deliberation as route_mod
        from core.deliberation import annotations as delib_annotations

        monkeypatch.setattr(
            route_mod.delib_store, "get_annotation",
            lambda _id: {
                "id": _id, "status": "candidate", "kind": "positioning_note", "scope": "document",
                "document_id": "doc-1",
            },
        )
        monkeypatch.setattr(route_mod, "_ensure_document_editable", lambda *a, **k: None)

        def _raise(*a, **k):
            raise delib_annotations.CommitRoutingError(
                "この種別のコミットは未対応（後続フェーズ）", kind="unsupported",
            )

        monkeypatch.setattr(route_mod.delib_annotations, "commit", _raise)

        response = client.post(
            "/api/admin/deliberation/annotations/00000000-0000-0000-0000-000000000000/commit",
            headers=_auth(teacher),
        )
        assert response.status_code == 422

    def test_happy_path_returns_committed_annotation(self, client_and_tokens, monkeypatch):
        client, _s, teacher = client_and_tokens
        import routes.deliberation as route_mod

        existing = {
            "id": "ann-1", "status": "candidate", "kind": "identity", "scope": "document", "document_id": "doc-1",
        }
        committed = {
            "id": "ann-1", "kind": "identity", "body": {"shared_part_id": "sp-1"}, "evidence": ["q"],
            "reason": "r", "confidence": 0.5, "status": "committed",
            "committed_target": {"type": "identity_link", "id": "link-1"}, "created_at": "",
        }
        monkeypatch.setattr(route_mod.delib_store, "get_annotation", lambda _id: existing)
        monkeypatch.setattr(route_mod, "_ensure_document_editable", lambda *a, **k: None)
        monkeypatch.setattr(route_mod.delib_annotations, "commit", lambda *a, **k: committed)
        monkeypatch.setattr(route_mod, "record_review_event", lambda *a, **k: None)

        response = client.post(
            "/api/admin/deliberation/annotations/ann-1/commit",
            headers=_auth(teacher),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["annotation"]["status"] == "committed"
        assert body["committed_target"] == {"type": "identity_link", "id": "link-1"}


class TestDismissAnnotation:
    def test_returns_409_when_already_decided(self, client_and_tokens, monkeypatch):
        client, _s, teacher = client_and_tokens
        import routes.deliberation as route_mod

        monkeypatch.setattr(
            route_mod.delib_store, "get_annotation",
            lambda _id: {"id": _id, "status": "committed", "kind": "meaning", "scope": "document", "document_id": "doc-1"},
        )
        monkeypatch.setattr(route_mod, "_ensure_document_editable", lambda *a, **k: None)
        monkeypatch.setattr(route_mod.delib_annotations, "dismiss", lambda *a, **k: None)

        response = client.post(
            "/api/admin/deliberation/annotations/ann-1/dismiss",
            headers=_auth(teacher),
        )
        assert response.status_code == 409

    def test_happy_path_returns_dismissed_annotation(self, client_and_tokens, monkeypatch):
        client, _s, teacher = client_and_tokens
        import routes.deliberation as route_mod

        existing = {"id": "ann-1", "status": "candidate", "kind": "meaning", "scope": "document", "document_id": "doc-1"}
        dismissed = {
            "id": "ann-1", "kind": "meaning", "body": {"text": "x"}, "evidence": ["q"], "reason": "r",
            "confidence": None, "status": "dismissed", "committed_target": {}, "created_at": "",
        }
        monkeypatch.setattr(route_mod.delib_store, "get_annotation", lambda _id: existing)
        monkeypatch.setattr(route_mod, "_ensure_document_editable", lambda *a, **k: None)
        monkeypatch.setattr(route_mod.delib_annotations, "dismiss", lambda *a, **k: dismissed)
        monkeypatch.setattr(route_mod, "record_review_event", lambda *a, **k: None)

        response = client.post(
            "/api/admin/deliberation/annotations/ann-1/dismiss",
            headers=_auth(teacher),
        )
        assert response.status_code == 200
        assert response.json()["annotation"]["status"] == "dismissed"


# ---------------------------------------------------------------------------
# Phase S: 標準化判定 worker の手動バッチ起動 API
# ---------------------------------------------------------------------------


class TestAssessSharedPartStandardization:
    def test_returns_404_when_shared_part_not_found(self, client_and_tokens, monkeypatch):
        client, _s, teacher = client_and_tokens
        import routes.deliberation as route_mod
        from core.deliberation.schema import ElementResolutionError

        def _raise(*a, **k):
            raise ElementResolutionError("not found", kind="not_found")

        monkeypatch.setattr(route_mod.refs, "resolve", _raise)
        response = client.post(
            "/api/admin/deliberation/shared-parts/missing/standardization/assess",
            headers=_auth(teacher),
        )
        assert response.status_code == 404

    def test_happy_path_schedules_background_assessment(self, client_and_tokens, monkeypatch):
        client, _s, teacher = client_and_tokens
        import routes.deliberation as route_mod

        monkeypatch.setattr(route_mod.refs, "resolve", lambda *a, **k: _fake_shared_part_ref())
        monkeypatch.setattr(
            route_mod.standardization_worker, "has_pending_or_committed_assessment", lambda *a, **k: False,
        )
        scheduled = []
        monkeypatch.setattr(
            route_mod.standardization_worker, "schedule_assess_shared_part",
            lambda entry_id, *, force: scheduled.append((entry_id, force)),
        )
        monkeypatch.setattr(route_mod, "record_review_event", lambda *a, **k: None)

        response = client.post(
            "/api/admin/deliberation/shared-parts/sp-1/standardization/assess",
            headers=_auth(teacher),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["queued"] is True
        assert not any(c.isdigit() for c in body["note"])
        assert scheduled == [("sp-1", False)]

    def test_skips_without_scheduling_when_already_assessed(self, client_and_tokens, monkeypatch):
        client, _s, teacher = client_and_tokens
        import routes.deliberation as route_mod

        monkeypatch.setattr(route_mod.refs, "resolve", lambda *a, **k: _fake_shared_part_ref())
        monkeypatch.setattr(
            route_mod.standardization_worker, "has_pending_or_committed_assessment", lambda *a, **k: True,
        )
        scheduled = []
        monkeypatch.setattr(
            route_mod.standardization_worker, "schedule_assess_shared_part",
            lambda *a, **k: scheduled.append((a, k)),
        )

        response = client.post(
            "/api/admin/deliberation/shared-parts/sp-1/standardization/assess",
            headers=_auth(teacher),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["queued"] is False
        assert not any(c.isdigit() for c in body["note"])
        assert scheduled == []

    def test_force_bypasses_the_idempotency_skip(self, client_and_tokens, monkeypatch):
        client, _s, teacher = client_and_tokens
        import routes.deliberation as route_mod

        monkeypatch.setattr(route_mod.refs, "resolve", lambda *a, **k: _fake_shared_part_ref())
        monkeypatch.setattr(
            route_mod.standardization_worker, "has_pending_or_committed_assessment", lambda *a, **k: True,
        )
        scheduled = []
        monkeypatch.setattr(
            route_mod.standardization_worker, "schedule_assess_shared_part",
            lambda entry_id, *, force: scheduled.append((entry_id, force)),
        )
        monkeypatch.setattr(route_mod, "record_review_event", lambda *a, **k: None)

        response = client.post(
            "/api/admin/deliberation/shared-parts/sp-1/standardization/assess?force=true",
            headers=_auth(teacher),
        )
        assert response.status_code == 200
        assert response.json()["queued"] is True
        assert scheduled == [("sp-1", True)]


class TestAssessDomainStandardization:
    def test_happy_path_schedules_background_assessment(self, client_and_tokens, monkeypatch):
        client, _s, teacher = client_and_tokens
        import routes.deliberation as route_mod

        scheduled = []
        monkeypatch.setattr(
            route_mod.standardization_worker, "schedule_assess_domain",
            lambda domain_key, *, force: scheduled.append((domain_key, force)),
        )
        monkeypatch.setattr(route_mod, "record_review_event", lambda *a, **k: None)

        response = client.post(
            "/api/admin/deliberation/domains/particle_physics/standardization/assess",
            headers=_auth(teacher),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["queued"] is True
        assert not any(c.isdigit() for c in body["note"])
        assert scheduled == [("particle_physics", False)]


# ---------------------------------------------------------------------------
# N2: 手動リンク作成用の共通部品候補検索
# GET /elements/{type}/{id}/shared-part-candidates
# ---------------------------------------------------------------------------


class TestSharedPartCandidates:
    PATH = "/api/admin/deliberation/elements/theory_claim/c1/shared-part-candidates"

    def test_unauthenticated_rejected(self, client_and_tokens):
        client, _s, _t = client_and_tokens
        response = client.get(self.PATH)
        assert response.status_code in (401, 403)

    def test_student_rejected(self, client_and_tokens):
        client, student, _t = client_and_tokens
        response = client.get(self.PATH, headers=_auth(student))
        assert response.status_code == 403

    def test_shared_part_element_returns_422(self, client_and_tokens, monkeypatch):
        client, _s, teacher = client_and_tokens
        import routes.deliberation as route_mod

        monkeypatch.setattr(route_mod.refs, "resolve", lambda *a, **k: _fake_shared_part_ref())
        response = client.get(
            "/api/admin/deliberation/elements/shared_part/sp-1/shared-part-candidates",
            headers=_auth(teacher),
        )
        assert response.status_code == 422

    def test_viewable_gate_is_enforced(self, client_and_tokens, monkeypatch):
        client, _s, teacher = client_and_tokens
        import routes.deliberation as route_mod
        from fastapi import HTTPException

        monkeypatch.setattr(route_mod.refs, "resolve", lambda *a, **k: _fake_document_ref())

        def deny(*a, **k):
            raise HTTPException(status_code=404, detail="document not found")

        monkeypatch.setattr(route_mod, "_ensure_document_viewable", deny)
        response = client.get(self.PATH, headers=_auth(teacher))
        assert response.status_code == 404

    def test_unresolved_domain_degrades_with_factual_note(self, client_and_tokens, monkeypatch):
        client, _s, teacher = client_and_tokens
        import routes.deliberation as route_mod

        monkeypatch.setattr(route_mod.refs, "resolve", lambda *a, **k: _fake_document_ref())
        monkeypatch.setattr(route_mod, "_ensure_document_viewable", lambda *a, **k: None)
        monkeypatch.setattr(route_mod.dialogue, "document_domain_key", lambda _d: "")

        called = {"search": False}
        monkeypatch.setattr(
            route_mod.library_search, "find_similar_entries",
            lambda **k: called.__setitem__("search", True) or [],
        )
        response = client.get(self.PATH, headers=_auth(teacher))
        assert response.status_code == 200
        body = response.json()
        assert body["entries"] == []
        assert "検索できません" in body["note"]
        assert called["search"] is False

    def test_hits_are_returned_without_numeric_distance(self, client_and_tokens, monkeypatch):
        client, _s, teacher = client_and_tokens
        import routes.deliberation as route_mod

        monkeypatch.setattr(route_mod.refs, "resolve", lambda *a, **k: _fake_document_ref())
        monkeypatch.setattr(route_mod, "_ensure_document_viewable", lambda *a, **k: None)
        monkeypatch.setattr(route_mod.dialogue, "document_domain_key", lambda _d: "particle_physics")

        captured = {}

        def fake_find(**kwargs):
            captured.update(kwargs)
            return [
                {
                    "entry_id": "e-1", "version_no": 3, "name": "PMT",
                    "aliases": ["photomultiplier"], "summary": "sum",
                    "body": {"typical_parts": []}, "distance": 0.07,
                },
                {"entry_id": "", "name": "id なしはスキップ"},
            ]

        monkeypatch.setattr(route_mod.library_search, "find_similar_entries", fake_find)

        response = client.get(self.PATH + "?q=PMT", headers=_auth(teacher))
        assert response.status_code == 200
        body = response.json()
        assert body["domain_key"] == "particle_physics"
        assert body["entries"] == [
            {
                "shared_part_id": "e-1",
                "name": "PMT",
                "aliases": ["photomultiplier"],
                "summary": "sum",
            }
        ]
        # 数値（distance 等）はレスポンスに出さない（W8）。
        assert "distance" not in body["entries"][0]
        # 明示クエリはそのまま検索に使われ、既存 find_similar_entries を再利用する。
        assert captured["text"] == "PMT"
        assert captured["domain_key"] == "particle_physics"
        # theory_claim → theory_component エントリ絞り込み（dialogue と同じ規約）。
        assert captured["entry_type"] == "theory_component"

    def test_empty_query_falls_back_to_breakdown_text(self, client_and_tokens, monkeypatch):
        client, _s, teacher = client_and_tokens
        import routes.deliberation as route_mod

        monkeypatch.setattr(route_mod.refs, "resolve", lambda *a, **k: _fake_document_ref())
        monkeypatch.setattr(route_mod, "_ensure_document_viewable", lambda *a, **k: None)
        monkeypatch.setattr(route_mod.dialogue, "document_domain_key", lambda _d: "pp")
        monkeypatch.setattr(
            route_mod.decomposition, "build",
            lambda ref: {"element_type": "theory_claim", "label": "ラベルL", "fields": {"text": "本文T"}, "notes": []},
        )

        captured = {}

        def fake_find(**kwargs):
            captured.update(kwargs)
            return []

        monkeypatch.setattr(route_mod.library_search, "find_similar_entries", fake_find)

        response = client.get(self.PATH, headers=_auth(teacher))
        assert response.status_code == 200
        body = response.json()
        # 0件は事実文で正直に（煽らない）。
        assert body["entries"] == []
        assert "見つかりません" in body["note"]
        assert "ラベルL" in captured["text"]
        assert "本文T" in captured["text"]
