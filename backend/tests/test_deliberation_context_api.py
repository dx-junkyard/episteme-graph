"""W層 軽量コンテキスト取得 API（``GET /api/admin/deliberation/elements/{type}/{id}/context``）
のテスト（TestClient + monkeypatch、``test_deliberation_api.py`` と同型）。

overview（面①内訳 + 面②位置づけ + 面③コンテキスト + 説明）を全部走らせずに面③のみを
返す派生経路。加えて theory_claim / theory_component の agent 側 ID（``comp_001`` 等）を
``source_scope.legacy_ids`` 経由で解決する ``core.deliberation.refs.resolve_with_agent_id``
の単体テストを含む（agent 側 ID は文書間で衝突しうるため document スコープが必須・
fail-closed）。

DB / LLM には触らない: refs の DB 読みは fake session、権限ゲートと context_lens.build は
monkeypatch で差し替える。
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

_skip_no_fastapi = pytest.mark.skipif(not _HAS_FASTAPI, reason="FastAPI 未導入")


# ---------------------------------------------------------------------------
# refs.resolve_with_agent_id の単体テスト（fake session。DB 非依存）
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _RecordingSession:
    """発行された SQL 文とパラメータを記録するだけの fake session。

    ``rows`` は ``(document_id, legacy_id) -> row`` の辞書。実 DB の
    ``WHERE document_id = :doc_id AND source_scope->'legacy_ids' ? :raw_id`` と同じ
    絞り込みを Python 側で再現し、SQL が document スコープを落としていないことを
    「別 document を指定したら見つからない」形で検証できるようにする。
    """

    def __init__(self, rows: dict[tuple[str, str], tuple]):
        self.rows = rows
        self.statements: list[str] = []
        self.params: list[dict] = []
        self.closed = False

    def execute(self, statement, params=None):
        self.statements.append(str(statement))
        self.params.append(dict(params or {}))
        key = (str((params or {}).get("doc_id")), str((params or {}).get("raw_id")))
        return _FakeResult(self.rows.get(key))

    def close(self):
        self.closed = True


_COMPONENT_UUID = "aaaaaaaa-0000-0000-0000-000000000001"


@pytest.fixture
def refs_module():
    from core.deliberation import refs

    return refs


class TestResolveWithAgentId:
    def test_agent_id_resolves_within_document_scope(self, refs_module, monkeypatch):
        session = _RecordingSession({("doc-1", "comp_001"): (_COMPONENT_UUID,)})
        monkeypatch.setattr(refs_module, "get_session", lambda: session)

        ref = refs_module.resolve_with_agent_id(
            "theory_component", "comp_001", document_id="doc-1"
        )
        assert ref.element_id == _COMPONENT_UUID
        assert ref.document_id == "doc-1"
        assert ref.scope == "document"
        # 元の agent 側 ID は情報として残す（P4）。
        assert ref.provenance["agent_element_id"] == "comp_001"
        assert session.closed is True

    def test_sql_scopes_by_document_and_legacy_ids_containment(self, refs_module, monkeypatch):
        session = _RecordingSession({("doc-1", "comp_001"): (_COMPONENT_UUID,)})
        monkeypatch.setattr(refs_module, "get_session", lambda: session)

        refs_module.resolve_with_agent_id("theory_component", "comp_001", document_id="doc-1")
        sql = session.statements[0]
        assert "FROM theory_components" in sql
        assert "document_id = :doc_id" in sql
        assert "source_scope->'legacy_ids' ? :raw_id" in sql
        assert session.params[0] == {"doc_id": "doc-1", "raw_id": "comp_001"}

    def test_claim_agent_id_uses_theory_claims_table(self, refs_module, monkeypatch):
        claim_uuid = "bbbbbbbb-0000-0000-0000-000000000002"
        session = _RecordingSession({("doc-9", "claim_span_007"): (claim_uuid,)})
        monkeypatch.setattr(refs_module, "get_session", lambda: session)

        ref = refs_module.resolve_with_agent_id(
            "theory_claim", "claim_span_007", document_id="doc-9"
        )
        assert ref.element_id == claim_uuid
        assert "FROM theory_claims" in session.statements[0]

    def test_agent_id_without_document_id_is_not_found_before_touching_db(
        self, refs_module, monkeypatch
    ):
        def boom():
            raise AssertionError("must not open a session without a document scope")

        monkeypatch.setattr(refs_module, "get_session", boom)
        from core.deliberation.schema import ElementResolutionError

        with pytest.raises(ElementResolutionError) as excinfo:
            refs_module.resolve_with_agent_id("theory_component", "comp_001", document_id=None)
        assert excinfo.value.kind == "not_found"

    def test_agent_id_in_another_document_is_not_resolved(self, refs_module, monkeypatch):
        """別 document の legacy_ids に一致しても、指定 document 外なら解決しない
        （fail-closed。SQL の document_id 条件が効いていることの検証）。"""
        session = _RecordingSession({("doc-other", "comp_001"): (_COMPONENT_UUID,)})
        monkeypatch.setattr(refs_module, "get_session", lambda: session)
        from core.deliberation.schema import ElementResolutionError

        with pytest.raises(ElementResolutionError) as excinfo:
            refs_module.resolve_with_agent_id(
                "theory_component", "comp_001", document_id="doc-1"
            )
        assert excinfo.value.kind == "not_found"

    def test_uuid_delegates_to_existing_resolve(self, refs_module, monkeypatch):
        calls: list[tuple] = []

        def fake_resolve(element_type, element_id, *, document_id=None, domain_key=None):
            calls.append((element_type, element_id, document_id))
            from core.deliberation.schema import ElementRef

            return ElementRef(
                scope="document",
                element_type=element_type,
                element_id=element_id,
                document_id="doc-from-row",
            )

        monkeypatch.setattr(refs_module, "resolve", fake_resolve)
        ref = refs_module.resolve_with_agent_id(
            "theory_component", _COMPONENT_UUID, document_id="doc-hint"
        )
        assert calls == [("theory_component", _COMPONENT_UUID, "doc-hint")]
        assert ref.document_id == "doc-from-row"

    @pytest.mark.parametrize("element_type", ["figure", "equation", "shared_part"])
    def test_other_element_types_always_delegate(self, refs_module, monkeypatch, element_type):
        calls: list[str] = []

        def fake_resolve(et, element_id, *, document_id=None, domain_key=None):
            calls.append(et)
            from core.deliberation.schema import ElementRef

            if et == "shared_part":
                return ElementRef(
                    scope="domain", element_type=et, element_id=element_id, domain_key="dk"
                )
            return ElementRef(
                scope="document", element_type=et, element_id=element_id, document_id="doc-1"
            )

        monkeypatch.setattr(refs_module, "resolve", fake_resolve)
        refs_module.resolve_with_agent_id(element_type, "not-a-uuid", document_id="doc-1")
        assert calls == [element_type]

    def test_unknown_element_type_is_invalid(self, refs_module, monkeypatch):
        def boom():
            raise AssertionError("must not open a session for an unknown element_type")

        monkeypatch.setattr(refs_module, "get_session", boom)
        from core.deliberation.schema import ElementResolutionError

        with pytest.raises(ElementResolutionError) as excinfo:
            refs_module.resolve_with_agent_id("nonsense", "x", document_id="doc-1")
        assert excinfo.value.kind == "invalid"


# ---------------------------------------------------------------------------
# API: GET /api/admin/deliberation/elements/{element_type}/{element_id}/context
# ---------------------------------------------------------------------------


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


def _contract_context_dict():
    """``core.deliberation.context_lens.build()`` の契約に沿った fake 結果。"""
    return {
        "focus": {
            "element_type": "theory_component",
            "element_id": _COMPONENT_UUID,
            "document_id": "doc-1",
            "label": "focus label",
            "intrinsic_summary": "summary text",
            "contextual_role": "中心命題を支持する",
            "contextual_role_status": "source_backed",
            "provenance": ["theory_components:" + _COMPONENT_UUID],
            "generic": None,
        },
        "upper": [
            {
                "element_type": "thesis",
                "element_id": None,
                "document_id": "doc-1",
                "label": "中心命題",
                "relation": "supports_thesis",
                "relation_label": "を支える",
                "relation_status": "source_backed",
                "evidence_refs": [],
                "navigable": False,
            }
        ],
        "lower": [],
        "notes": ["note-1"],
    }


_PATH = "/api/admin/deliberation/elements/theory_component/" + _COMPONENT_UUID + "/context"


@_skip_no_fastapi
class TestContextEndpointAuth:
    def test_unauthenticated_request_is_rejected(self, client_and_tokens):
        client, _s, _t = client_and_tokens
        assert client.get(_PATH).status_code in (401, 403)

    def test_student_is_rejected(self, client_and_tokens):
        client, student, _t = client_and_tokens
        assert client.get(_PATH, headers=_auth(student)).status_code == 403


@_skip_no_fastapi
class TestContextEndpoint:
    def _fake_document_ref(self, element_type="theory_component", element_id=_COMPONENT_UUID,
                           document_id="doc-1"):
        from core.deliberation.schema import ElementRef, SCOPE_DOCUMENT

        return ElementRef(
            scope=SCOPE_DOCUMENT, element_type=element_type, element_id=element_id,
            document_id=document_id,
        )

    def test_uuid_component_returns_available_true_with_full_contract(
        self, client_and_tokens, monkeypatch
    ):
        client, _s, teacher = client_and_tokens
        import routes.deliberation as route_mod

        monkeypatch.setattr(
            route_mod.refs, "resolve_with_agent_id", lambda *a, **k: self._fake_document_ref()
        )
        monkeypatch.setattr(route_mod, "_ensure_document_viewable", lambda *a, **k: None)
        monkeypatch.setattr(route_mod.context_lens, "build", lambda ref: _contract_context_dict())

        response = client.get(_PATH, headers=_auth(teacher))
        assert response.status_code == 200
        body = response.json()
        assert body["available"] is True
        assert body["focus"]["label"] == "focus label"
        assert body["upper"][0]["relation_label"] == "を支える"
        assert body["lower"] == []
        assert body["notes"] == ["note-1"]
        # overview の重い面（内訳・位置づけ・説明）は含まない（軽量経路であること）。
        assert "positioning" not in body
        assert "decomposition" not in body
        assert "explanations" not in body

    def test_agent_id_is_resolved_within_document_scope(self, client_and_tokens, monkeypatch):
        """agent 側 ID（非UUID）+ document_id が legacy_ids 経路で解決される。"""
        client, _s, teacher = client_and_tokens
        import routes.deliberation as route_mod

        session = _RecordingSession({("doc-1", "comp_001"): (_COMPONENT_UUID,)})
        monkeypatch.setattr(route_mod.refs, "get_session", lambda: session)
        gated: list[str] = []
        monkeypatch.setattr(
            route_mod, "_ensure_document_viewable",
            lambda document_id, user: gated.append(document_id),
        )
        seen: list[str] = []

        def fake_build(ref):
            seen.append(ref.element_id)
            return _contract_context_dict()

        monkeypatch.setattr(route_mod.context_lens, "build", fake_build)

        response = client.get(
            "/api/admin/deliberation/elements/theory_component/comp_001/context",
            params={"document_id": "doc-1"},
            headers=_auth(teacher),
        )
        assert response.status_code == 200
        assert response.json()["available"] is True
        # context_lens には解決済みの DB UUID が渡る。
        assert seen == [_COMPONENT_UUID]
        # 権限ゲートは DB 行由来の document_id に対して行われる。
        assert gated == ["doc-1"]

    def test_agent_id_without_document_id_is_404(self, client_and_tokens, monkeypatch):
        client, _s, teacher = client_and_tokens
        import routes.deliberation as route_mod

        def boom():
            raise AssertionError("must not open a session without a document scope")

        monkeypatch.setattr(route_mod.refs, "get_session", boom)
        monkeypatch.setattr(
            route_mod.context_lens, "build",
            lambda ref: pytest.fail("must not build a projection for an unresolved ref"),
        )

        response = client.get(
            "/api/admin/deliberation/elements/theory_component/comp_001/context",
            headers=_auth(teacher),
        )
        assert response.status_code == 404

    def test_agent_id_matching_another_document_is_404(self, client_and_tokens, monkeypatch):
        """別 document の legacy_ids に一致しても、指定 document 外なら解決しない
        （fail-closed）。"""
        client, _s, teacher = client_and_tokens
        import routes.deliberation as route_mod

        session = _RecordingSession({("doc-other", "comp_001"): (_COMPONENT_UUID,)})
        monkeypatch.setattr(route_mod.refs, "get_session", lambda: session)
        monkeypatch.setattr(
            route_mod, "_ensure_document_viewable",
            lambda *a, **k: pytest.fail("must not reach the permission gate"),
        )

        response = client.get(
            "/api/admin/deliberation/elements/theory_component/comp_001/context",
            params={"document_id": "doc-1"},
            headers=_auth(teacher),
        )
        assert response.status_code == 404

    def test_non_viewable_document_is_404(self, client_and_tokens, monkeypatch):
        client, _s, teacher = client_and_tokens
        import routes.deliberation as route_mod
        from fastapi import HTTPException

        monkeypatch.setattr(
            route_mod.refs, "resolve_with_agent_id", lambda *a, **k: self._fake_document_ref()
        )

        def deny(document_id, user):
            raise HTTPException(status_code=404, detail="教材が見つかりません")

        monkeypatch.setattr(route_mod, "_ensure_document_viewable", deny)
        monkeypatch.setattr(
            route_mod.context_lens, "build",
            lambda ref: pytest.fail("must not build a projection for a non-viewable document"),
        )

        response = client.get(_PATH, headers=_auth(teacher))
        assert response.status_code == 404

    def test_unknown_element_type_is_422(self, client_and_tokens, monkeypatch):
        client, _s, teacher = client_and_tokens
        import routes.deliberation as route_mod

        monkeypatch.setattr(
            route_mod.context_lens, "build",
            lambda ref: pytest.fail("must not build a projection for an unknown element_type"),
        )

        response = client.get(
            "/api/admin/deliberation/elements/nonsense/x/context",
            params={"document_id": "doc-1"},
            headers=_auth(teacher),
        )
        assert response.status_code == 422

    def test_shared_part_reports_unavailable_with_factual_note(self, client_and_tokens, monkeypatch):
        client, _s, teacher = client_and_tokens
        import routes.deliberation as route_mod
        from core.deliberation.schema import ElementRef, SCOPE_DOMAIN

        monkeypatch.setattr(
            route_mod.refs, "resolve_with_agent_id",
            lambda *a, **k: ElementRef(
                scope=SCOPE_DOMAIN, element_type="shared_part", element_id="sp-1",
                domain_key="particle_physics",
            ),
        )
        monkeypatch.setattr(
            route_mod, "_ensure_document_viewable",
            lambda *a, **k: pytest.fail("domain-scoped element must not hit the document gate"),
        )
        monkeypatch.setattr(route_mod.context_lens, "build", lambda ref: None)

        response = client.get(
            "/api/admin/deliberation/elements/shared_part/sp-1/context",
            headers=_auth(teacher),
        )
        assert response.status_code == 200
        assert response.json() == {
            "available": False,
            "note": "この要素型にはコンテキスト投影がありません",
        }

    def test_builder_exception_is_fail_soft_not_500(self, client_and_tokens, monkeypatch):
        client, _s, teacher = client_and_tokens
        import routes.deliberation as route_mod

        monkeypatch.setattr(
            route_mod.refs, "resolve_with_agent_id", lambda *a, **k: self._fake_document_ref()
        )
        monkeypatch.setattr(route_mod, "_ensure_document_viewable", lambda *a, **k: None)

        def boom(_ref):
            raise RuntimeError("simulated context_lens outage")

        monkeypatch.setattr(route_mod.context_lens, "build", boom)

        response = client.get(_PATH, headers=_auth(teacher))
        assert response.status_code == 200
        assert response.json() == {
            "available": False,
            "note": "コンテキスト投影の取得に失敗しました",
        }

    def test_response_does_not_leak_raw_confidence(self, client_and_tokens, monkeypatch):
        """W8: context_lens の契約に confidence は含まれないため、応答にも現れない。"""
        client, _s, teacher = client_and_tokens
        import routes.deliberation as route_mod

        monkeypatch.setattr(
            route_mod.refs, "resolve_with_agent_id", lambda *a, **k: self._fake_document_ref()
        )
        monkeypatch.setattr(route_mod, "_ensure_document_viewable", lambda *a, **k: None)
        monkeypatch.setattr(route_mod.context_lens, "build", lambda ref: _contract_context_dict())

        response = client.get(_PATH, headers=_auth(teacher))
        assert "confidence" not in response.text


# ---------------------------------------------------------------------------
# 構造（ソース検査）: 軽量経路が overview の重い面を呼ばず、権限ゲートを通ること
# ---------------------------------------------------------------------------


_ROUTE_SRC = (BACKEND / "api" / "routes" / "deliberation.py").read_text(encoding="utf-8")
_REFS_SRC = (BACKEND / "core" / "deliberation" / "refs.py").read_text(encoding="utf-8")


class TestContextRouteStructure:
    def _body(self) -> str:
        from tests.guardrail_helpers import extract_function_source

        return extract_function_source(_ROUTE_SRC, "get_element_context")

    def test_route_is_registered_as_get_only(self):
        assert '@router.get("/elements/{element_type}/{element_id}/context")' in _ROUTE_SRC

    def test_route_goes_through_document_viewable_gate_on_resolved_document_id(self):
        body = self._body()
        assert "_ensure_document_viewable(ref.document_id or \"\", current_user)" in body

    def test_route_does_not_call_the_heavy_faces(self):
        body = self._body()
        for needle in ("positioning.build(", "decomposition.build(", "explanations_for_element("):
            assert needle not in body, f"lightweight context route must not call {needle}"

    def test_route_uses_agent_id_aware_resolver(self):
        body = self._body()
        assert "refs.resolve_with_agent_id(" in body

    def test_route_maps_resolution_errors_like_overview(self):
        body = self._body()
        assert "_http_from_resolution_error(exc)" in body


class TestRefsLegacyResolutionStructure:
    def _body(self) -> str:
        from tests.guardrail_helpers import extract_function_source

        return extract_function_source(_REFS_SRC, "_resolve_by_legacy_id")

    def test_legacy_resolution_scopes_by_document_in_sql(self):
        body = self._body()
        assert "document_id = :doc_id" in body
        assert "source_scope->'legacy_ids' ? :raw_id" in body

    def test_legacy_resolution_tables_are_limited_to_claim_and_component(self):
        from core.deliberation import refs
        from core.deliberation.schema import ELEMENT_THEORY_CLAIM, ELEMENT_THEORY_COMPONENT

        assert set(refs._LEGACY_ID_TABLES) == {ELEMENT_THEORY_CLAIM, ELEMENT_THEORY_COMPONENT}

    def test_legacy_resolution_closes_the_session(self):
        body = self._body()
        assert "finally:" in body
        assert "session.close()" in body

    def test_refs_module_has_no_disallowed_imports(self):
        from tests.guardrail_helpers import assert_source_does_not_import

        assert_source_does_not_import(
            _REFS_SRC,
            ["fastapi", "episteme_graph.agents", "routes", "services"],
            context="core/deliberation/refs.py",
        )
