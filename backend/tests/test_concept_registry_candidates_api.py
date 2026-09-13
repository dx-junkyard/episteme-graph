"""概念レジストリの候補 API（§9.1）— 導出と同一性候補のレビューキュー。

対象は ``backend/api/routes/library.py`` に足した 2 本:

- ``POST /api/admin/library/atlas-links/derive``（retired は 409 / 骨格なしは 422）
- ``GET  /api/admin/library/identity-candidates``（閲覧不可 document は除外し
  ``hidden_count`` を正直に返す = KR10 / W-β 同型）

流儀は ``tests/test_concept_registry_api.py``（実 app + TestClient + core の monkeypatch）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

try:
    import fastapi  # noqa: F401
    _HAS_FASTAPI = True
except ImportError:  # pragma: no cover
    _HAS_FASTAPI = False

pytestmark = pytest.mark.skipif(not _HAS_FASTAPI, reason="FastAPI not installed")

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _path in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

_TEACHER_ID = "99999999-9999-9999-9999-999999999999"
_STUDENT_ID = "88888888-8888-8888-8888-888888888888"
_ENTRY_ID = "11111111-1111-1111-1111-111111111111"
_DOC_OK = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_DOC_HIDDEN = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

_DERIVE = "/api/admin/library/atlas-links/derive"
_CANDIDATES = "/api/admin/library/identity-candidates"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": "Bearer " + token}


@pytest.fixture
def client_and_tokens():
    from fastapi.testclient import TestClient
    from main import app
    from dependencies import ROLE_STUDENT, ROLE_TEACHER, _create_token

    client = TestClient(app)
    student = _create_token(_STUDENT_ID, "stu", "stu@x", ROLE_STUDENT)
    teacher = _create_token(_TEACHER_ID, "tea", "tea@x", ROLE_TEACHER)
    return client, student, teacher


class _FakeSession:
    def execute(self, *args, **kwargs):
        raise AssertionError("unexpected SQL in this test")

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


@pytest.fixture
def routes(monkeypatch):
    from routes import library as route_mod

    monkeypatch.setattr(route_mod, "_audit", lambda *a, **k: None)
    monkeypatch.setattr(route_mod, "get_session", lambda: _FakeSession())
    monkeypatch.setattr(
        route_mod.atlas_store, "domain_lifecycle", lambda session, domain: "active"
    )
    return route_mod


# ---------------------------------------------------------------------------
# 1. 導出（POST /atlas-links/derive）
# ---------------------------------------------------------------------------


class TestDerive:
    def test_requires_teacher(self, client_and_tokens, routes):
        client, student, _teacher = client_and_tokens
        response = client.post(
            _DERIVE, json={"domain_key": "astro"}, headers=_auth(student)
        )
        assert response.status_code == 403

    def test_retired_domain_is_409(self, client_and_tokens, routes, monkeypatch):
        monkeypatch.setattr(
            routes.atlas_store, "domain_lifecycle", lambda session, domain: "retired"
        )
        client, _student, teacher = client_and_tokens
        response = client.post(
            _DERIVE, json={"domain_key": "astro"}, headers=_auth(teacher)
        )
        assert response.status_code == 409
        assert not any(ch.isdigit() for ch in response.json()["detail"])

    def test_missing_skeleton_is_422_with_a_factual_message(
        self, client_and_tokens, routes, monkeypatch
    ):
        def _raise(session, *, domain_key):
            raise routes.library_atlas_links.SkeletonUnavailableError(
                "この分野には、凍結された地図がまだありません。"
            )

        monkeypatch.setattr(
            routes.library_atlas_links, "derive_node_link_candidates", _raise
        )
        client, _student, teacher = client_and_tokens
        response = client.post(
            _DERIVE, json={"domain_key": "astro"}, headers=_auth(teacher)
        )
        assert response.status_code == 422
        assert "凍結された地図" in response.json()["detail"]

    def test_blank_domain_key_is_422(self, client_and_tokens, routes):
        client, _student, teacher = client_and_tokens
        response = client.post(_DERIVE, json={"domain_key": "  "}, headers=_auth(teacher))
        assert response.status_code == 422

    def test_returns_the_dto_contract_without_counts(
        self, client_and_tokens, routes, monkeypatch
    ):
        monkeypatch.setattr(
            routes.library_atlas_links, "derive_node_link_candidates",
            lambda session, *, domain_key: {
                "candidates": [
                    {
                        "id": "l1",
                        "entry_id": _ENTRY_ID,
                        "domain_key": domain_key,
                        "node_id": "c1",
                        "node_label": "Cosmic web",
                        "kind": "exact_match",
                        "status": "candidate",
                        "mapping_justification": "lexical_match",
                        "node_in_current_version": True,
                        "nearness_label": "かなり近い",
                    }
                ],
                "skeleton_version": "2026.1",
                "facts": ["この分野の地図の概念と、確定済みの概念を照合しました。"],
                "coverage": {"population": 7, "processed": 7, "truncated": 0, "reasons": []},
            },
        )
        client, _student, teacher = client_and_tokens
        response = client.post(
            _DERIVE, json={"domain_key": "astro"}, headers=_auth(teacher)
        )
        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"candidates", "skeleton_version", "facts"}
        # KR6: 件数報告（coverage）は教員に返さない。
        assert "coverage" not in body
        assert body["candidates"][0]["nearness_label"] == "かなり近い"
        assert "confidence" not in body["candidates"][0]


# ---------------------------------------------------------------------------
# 2. 同一性候補のレビューキュー（GET /identity-candidates）
# ---------------------------------------------------------------------------


def _entry(review_status="candidate", name="Zero recoil limit") -> dict:
    return {
        "id": _ENTRY_ID,
        "domain_key": "flavour",
        "entry_type": "concept",
        "name": name,
        "review_status": review_status,
        "review_note": "",
        "mapping_justification": "lexical_match",
        "candidate_key": "cand|flavour|zero recoil limit",
        "status": "active",
    }


def _link(link_id: str, document_id: str, status: str = "candidate") -> dict:
    return {
        "id": link_id,
        "instance_element_type": "theory_component",
        "instance_element_id": "comp-1",
        "instance_document_id": document_id,
        "shared_part_id": _ENTRY_ID,
        "status": status,
        "local_expression": {"name": "zero recoil"},
        "evidence": [],
        "reason": "",
        "confidence": 0.91,
        "mapping_justification": "lexical_match",
    }


@pytest.fixture
def candidates_env(monkeypatch, routes):
    route_mod = routes

    class _Access:
        def __init__(self, can_view):
            self.can_view = can_view

    monkeypatch.setattr(
        route_mod.services, "resolve_document_access",
        lambda uid, document_id: _Access(document_id == _DOC_OK),
    )
    monkeypatch.setattr(
        route_mod, "_document_titles",
        lambda ids: {_DOC_OK: "A paper on zero recoil"},
    )
    return route_mod


class TestIdentityCandidates:
    def test_requires_teacher(self, client_and_tokens, candidates_env):
        client, student, _teacher = client_and_tokens
        assert client.get(_CANDIDATES, headers=_auth(student)).status_code == 403

    def test_invisible_documents_are_hidden_and_counted(
        self, client_and_tokens, candidates_env, monkeypatch
    ):
        monkeypatch.setattr(
            candidates_env.library_store, "list_entries", lambda **kwargs: [_entry()]
        )
        monkeypatch.setattr(
            candidates_env._identity_links, "list_for_shared_part",
            lambda entry_id: [_link("l1", _DOC_OK), _link("l2", _DOC_HIDDEN)],
        )
        client, _student, teacher = client_and_tokens
        response = client.get(_CANDIDATES, headers=_auth(teacher))
        assert response.status_code == 200
        candidate = response.json()["candidates"][0]
        assert [item["link_id"] for item in candidate["links"]] == ["l1"]
        assert candidate["hidden_count"] == 1
        assert candidate["supporting_titles"] == ["A paper on zero recoil"]

    def test_link_dto_does_not_leak_confidence(
        self, client_and_tokens, candidates_env, monkeypatch
    ):
        monkeypatch.setattr(
            candidates_env.library_store, "list_entries", lambda **kwargs: [_entry()]
        )
        monkeypatch.setattr(
            candidates_env._identity_links, "list_for_shared_part",
            lambda entry_id: [_link("l1", _DOC_OK)],
        )
        client, _student, teacher = client_and_tokens
        body = client.get(_CANDIDATES, headers=_auth(teacher)).json()
        item = body["candidates"][0]["links"][0]
        assert "confidence" not in item
        assert set(item) == {
            "link_id", "instance", "document_title", "local_expression",
            "status", "mapping_justification",
        }

    def test_confirmed_entries_are_not_listed(
        self, client_and_tokens, candidates_env, monkeypatch
    ):
        monkeypatch.setattr(
            candidates_env.library_store, "list_entries",
            lambda **kwargs: [_entry(review_status="confirmed")],
        )
        monkeypatch.setattr(
            candidates_env._identity_links, "list_for_shared_part",
            lambda entry_id: [_link("l1", _DOC_OK)],
        )
        client, _student, teacher = client_and_tokens
        body = client.get(_CANDIDATES, headers=_auth(teacher)).json()
        assert body["candidates"] == []
        assert body["facts"]

    def test_include_dismissed_is_opt_in(
        self, client_and_tokens, candidates_env, monkeypatch
    ):
        monkeypatch.setattr(
            candidates_env.library_store, "list_entries",
            lambda **kwargs: [_entry(review_status="dismissed")],
        )
        monkeypatch.setattr(
            candidates_env._identity_links, "list_for_shared_part",
            lambda entry_id: [_link("l1", _DOC_OK)],
        )
        client, _student, teacher = client_and_tokens
        assert client.get(_CANDIDATES, headers=_auth(teacher)).json()["candidates"] == []
        opted_in = client.get(
            _CANDIDATES + "?include_dismissed=true", headers=_auth(teacher)
        ).json()
        assert len(opted_in["candidates"]) == 1

    def test_entry_without_visible_links_is_dropped(
        self, client_and_tokens, candidates_env, monkeypatch
    ):
        """リンクが 1 本も無い候補は判断材料が無いので出さない（件数は 0 のまま）。"""
        monkeypatch.setattr(
            candidates_env.library_store, "list_entries", lambda **kwargs: [_entry()]
        )
        monkeypatch.setattr(
            candidates_env._identity_links, "list_for_shared_part", lambda entry_id: []
        )
        client, _student, teacher = client_and_tokens
        assert client.get(_CANDIDATES, headers=_auth(teacher)).json()["candidates"] == []
