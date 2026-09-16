"""概念レジストリ — 管理 API 層のテスト（concept_registry_design.md §9）。

対象は ``backend/api/routes/library.py`` に足した 7 本
（entries の ``include_candidates`` / 確定 / ラベル / 関係 / 地図との対応）。

流儀は ``tests/test_atlas_vectors_api.py``（実 app + TestClient + core の monkeypatch）を
踏襲する。core（store / registry）は fake に差し替え、**route 層の契約**だけを見る:

- 権限（TEACHER 以上・KR10）
- 422 が事実文で返る（見送り理由の欠落・語彙外の status / kind）
- 404 の統一（存在しない entry / label / relation / link）
- レスポンス形が UI 側の契約どおり（KR6: 数値を返さない）
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
_OTHER_ID = "22222222-2222-2222-2222-222222222222"
_LABEL_ID = "33333333-3333-3333-3333-333333333333"
_RELATION_ID = "55555555-5555-5555-5555-555555555555"
_LINK_ID = "66666666-6666-6666-6666-666666666666"

_ENTRIES = "/api/admin/library/entries"
_REVIEW = f"{_ENTRIES}/{_ENTRY_ID}/review"
_LABELS = f"{_ENTRIES}/{_ENTRY_ID}/labels"
_RELATIONS = "/api/admin/library/relations"
_ATLAS_LINKS = "/api/admin/library/atlas-links"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": "Bearer " + token}


def _entry(*, review_status="confirmed", entry_id=_ENTRY_ID, name="Cosmic web") -> dict:
    return {
        "id": entry_id,
        "domain_key": "astro",
        "entry_type": "concept",
        "name": name,
        "aliases": [],
        "summary": "",
        "body": {},
        "exemplar_images": [],
        "source_component_ids": [],
        "source_document_ids": [],
        "status": "active",
        "standardization_status": "unknown",
        "review_status": review_status,
        "review_note": "",
        "mapping_justification": None,
        "candidate_key": None,
        "decided_by": None,
        "decided_at": None,
        "revision": 1,
        "latest_version_no": 0,
        "created_by": None,
        "updated_by": None,
        "created_at": "",
        "updated_at": "",
    }


@pytest.fixture
def client_and_tokens():
    from fastapi.testclient import TestClient
    from main import app
    from dependencies import ROLE_STUDENT, ROLE_TEACHER, _create_token

    client = TestClient(app)
    student = _create_token(_STUDENT_ID, "stu", "stu@x", ROLE_STUDENT)
    teacher = _create_token(_TEACHER_ID, "tea", "tea@x", ROLE_TEACHER)
    return client, student, teacher


@pytest.fixture
def routes(monkeypatch):
    """route が呼ぶ core を fake に差し替える（DB に触らない）。"""
    from routes import library as route_mod

    calls: dict[str, list] = {}

    def _record(name):
        def _inner(*args, **kwargs):
            calls.setdefault(name, []).append((args, kwargs))
            return calls.get(f"{name}:return", [None])[-1]

        return _inner

    monkeypatch.setattr(route_mod.library_store, "get_entry", lambda entry_id: _entry(entry_id=entry_id))
    monkeypatch.setattr(route_mod, "_audit", lambda *a, **k: None)
    return route_mod, calls


# ---------------------------------------------------------------------------
# 1. 一覧（include_candidates）
# ---------------------------------------------------------------------------


class TestListEntries:
    def test_defaults_to_confirmed_only(self, client_and_tokens, routes, monkeypatch):
        route_mod, _calls = routes
        seen: dict = {}

        def _list_entries(**kwargs):
            seen.update(kwargs)
            return []

        monkeypatch.setattr(route_mod.library_store, "list_entries", _list_entries)
        client, _student, teacher = client_and_tokens
        response = client.get(_ENTRIES, headers=_auth(teacher))
        assert response.status_code == 200
        assert seen["include_candidates"] is False

    def test_include_candidates_is_passed_through(self, client_and_tokens, routes, monkeypatch):
        route_mod, _calls = routes
        seen: dict = {}

        def _list_entries(**kwargs):
            seen.update(kwargs)
            return [_entry(review_status="candidate")]

        monkeypatch.setattr(route_mod.library_store, "list_entries", _list_entries)
        client, _student, teacher = client_and_tokens
        response = client.get(
            _ENTRIES + "?include_candidates=true", headers=_auth(teacher)
        )
        assert response.status_code == 200
        assert seen["include_candidates"] is True
        entry = response.json()["entries"][0]
        # UI 契約（担当 D）: 候補の見分けに必要な列がそのまま載る（数値は無い）。
        for key in ("review_status", "review_note", "mapping_justification", "candidate_key"):
            assert key in entry
        assert "confidence" not in entry

    def test_requires_teacher(self, client_and_tokens):
        client, student, _teacher = client_and_tokens
        assert client.get(_ENTRIES, headers=_auth(student)).status_code == 403


class TestCandidateVisibility:
    """P3-R2: AI が立てた候補だけに document 可視性を掛ける。

    候補はパイプラインが論文から起こすので、``source_document_ids`` に閲覧できない
    論文が混じり得る。確定済みは教員が分野の共同財として確定したものなので従来どおり。
    """

    _OK = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    _HIDDEN = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"

    @pytest.fixture
    def visibility(self, routes, monkeypatch):
        route_mod, _calls = routes

        class _Access:
            def __init__(self, can_view):
                self.can_view = can_view

        monkeypatch.setattr(
            route_mod.services,
            "resolve_document_access",
            lambda uid, document_id: _Access(document_id == self._OK),
        )
        return route_mod, monkeypatch

    def _serve(self, route_mod, monkeypatch, entries):
        monkeypatch.setattr(route_mod.library_store, "list_entries", lambda **kw: entries)

    def test_invisible_sources_are_stripped_from_candidates(
        self, client_and_tokens, visibility
    ):
        route_mod, monkeypatch = visibility
        entry = _entry(review_status="candidate")
        entry["source_document_ids"] = [self._OK, self._HIDDEN]
        self._serve(route_mod, monkeypatch, [entry])
        client, _student, teacher = client_and_tokens
        body = client.get(_ENTRIES + "?include_candidates=true", headers=_auth(teacher)).json()
        assert body["entries"][0]["source_document_ids"] == [self._OK]
        assert body["hidden_count"] == 0

    def test_candidate_with_no_visible_source_is_dropped_and_counted(
        self, client_and_tokens, visibility
    ):
        route_mod, monkeypatch = visibility
        entry = _entry(review_status="candidate")
        entry["source_document_ids"] = [self._HIDDEN]
        self._serve(route_mod, monkeypatch, [entry])
        client, _student, teacher = client_and_tokens
        body = client.get(_ENTRIES + "?include_candidates=true", headers=_auth(teacher)).json()
        assert body["entries"] == []
        assert body["hidden_count"] == 1

    def test_confirmed_entries_keep_their_sources(self, client_and_tokens, visibility):
        route_mod, monkeypatch = visibility
        entry = _entry(review_status="confirmed")
        entry["source_document_ids"] = [self._OK, self._HIDDEN]
        self._serve(route_mod, monkeypatch, [entry])
        client, _student, teacher = client_and_tokens
        body = client.get(_ENTRIES, headers=_auth(teacher)).json()
        assert body["entries"][0]["source_document_ids"] == [self._OK, self._HIDDEN]
        assert body["hidden_count"] == 0

    def test_access_check_failure_hides_the_source(self, client_and_tokens, routes, monkeypatch):
        """判定できないものは見せない（fail-closed）。"""
        route_mod, _calls = routes

        def _boom(uid, document_id):
            raise RuntimeError("db down")

        monkeypatch.setattr(route_mod.services, "resolve_document_access", _boom)
        entry = _entry(review_status="candidate")
        entry["source_document_ids"] = [self._OK]
        monkeypatch.setattr(route_mod.library_store, "list_entries", lambda **kw: [entry])
        client, _student, teacher = client_and_tokens
        body = client.get(_ENTRIES + "?include_candidates=true", headers=_auth(teacher)).json()
        assert body["entries"] == []
        assert body["hidden_count"] == 1


# ---------------------------------------------------------------------------
# 2. 概念の確定（KR2 / KR7）
# ---------------------------------------------------------------------------


class TestEntryReview:
    def test_confirm_returns_the_entry(self, client_and_tokens, routes, monkeypatch):
        route_mod, _calls = routes
        seen: dict = {}

        def _decide(entry_id, **kwargs):
            seen.update({"entry_id": entry_id, **kwargs})
            return _entry(review_status="confirmed")

        monkeypatch.setattr(route_mod.library_registry, "decide_entry_review", _decide)
        client, _student, teacher = client_and_tokens
        response = client.post(_REVIEW, json={"status": "confirmed"}, headers=_auth(teacher))
        assert response.status_code == 200
        assert response.json()["entry"]["review_status"] == "confirmed"
        assert seen["actor_id"] == _TEACHER_ID

    def test_dismiss_without_a_reason_is_422(self, client_and_tokens, routes, monkeypatch):
        route_mod, _calls = routes

        def _decide(entry_id, **kwargs):
            raise ValueError("dismiss requires a reason")

        monkeypatch.setattr(route_mod.library_registry, "decide_entry_review", _decide)
        client, _student, teacher = client_and_tokens
        response = client.post(_REVIEW, json={"status": "dismissed"}, headers=_auth(teacher))
        assert response.status_code == 422

    def test_unknown_status_is_422(self, client_and_tokens, routes, monkeypatch):
        route_mod, _calls = routes

        def _decide(entry_id, **kwargs):
            raise ValueError("invalid status: 'approved'")

        monkeypatch.setattr(route_mod.library_registry, "decide_entry_review", _decide)
        client, _student, teacher = client_and_tokens
        response = client.post(_REVIEW, json={"status": "approved"}, headers=_auth(teacher))
        assert response.status_code == 422

    def test_missing_entry_is_404(self, client_and_tokens, routes, monkeypatch):
        route_mod, _calls = routes
        monkeypatch.setattr(route_mod.library_store, "get_entry", lambda entry_id: None)
        client, _student, teacher = client_and_tokens
        response = client.post(_REVIEW, json={"status": "confirmed"}, headers=_auth(teacher))
        assert response.status_code == 404

    def test_requires_teacher(self, client_and_tokens, routes):
        client, student, _teacher = client_and_tokens
        response = client.post(_REVIEW, json={"status": "confirmed"}, headers=_auth(student))
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# 3. ラベル（別名・隠しラベル）
# ---------------------------------------------------------------------------


def _label(*, status="confirmed", kind="alternate") -> dict:
    return {
        "id": _LABEL_ID,
        "entry_id": _ENTRY_ID,
        "kind": kind,
        "label": "cosmic web",
        "normalized_label": "cosmic web",
        "language": "",
        "status": status,
        "mapping_justification": "manual_curation",
        "evidence": [],
        "review_note": "",
        "created_by": None,
        "decided_by": None,
        "created_at": "",
        "updated_at": "",
    }


class TestLabels:
    def test_list_returns_the_ui_contract_shape(self, client_and_tokens, routes, monkeypatch):
        route_mod, _calls = routes
        monkeypatch.setattr(
            route_mod.library_registry, "list_labels", lambda *a, **k: [_label()]
        )
        client, _student, teacher = client_and_tokens
        response = client.get(_LABELS, headers=_auth(teacher))
        assert response.status_code == 200
        label = response.json()["labels"][0]
        for key in ("id", "kind", "label", "status", "mapping_justification"):
            assert key in label
        assert "confidence" not in label

    def test_add_returns_201(self, client_and_tokens, routes, monkeypatch):
        route_mod, _calls = routes
        monkeypatch.setattr(
            route_mod.library_registry, "add_label", lambda *a, **k: _label(kind="hidden")
        )
        client, _student, teacher = client_and_tokens
        response = client.post(
            _LABELS, json={"kind": "hidden", "label": "c0smic web"}, headers=_auth(teacher)
        )
        assert response.status_code == 201
        assert response.json()["label"]["kind"] == "hidden"

    def test_invalid_kind_is_422(self, client_and_tokens, routes, monkeypatch):
        route_mod, _calls = routes

        def _add(*a, **k):
            raise ValueError("invalid label kind: 'preferred'")

        monkeypatch.setattr(route_mod.library_registry, "add_label", _add)
        client, _student, teacher = client_and_tokens
        response = client.post(
            _LABELS, json={"kind": "preferred", "label": "x"}, headers=_auth(teacher)
        )
        assert response.status_code == 422

    def test_dismiss_of_another_entrys_label_is_404(self, client_and_tokens, routes, monkeypatch):
        """URL の entry と実際の所属が食い違うラベルは 404（スコープの取り違え防止）。"""
        route_mod, _calls = routes
        foreign = _label(status="dismissed")
        foreign["entry_id"] = _OTHER_ID
        monkeypatch.setattr(
            route_mod.library_registry, "dismiss_label", lambda *a, **k: foreign
        )
        client, _student, teacher = client_and_tokens
        response = client.post(
            f"{_LABELS}/{_LABEL_ID}/dismiss",
            json={"review_note": "旧表記のため"},
            headers=_auth(teacher),
        )
        assert response.status_code == 404

    def test_dismiss_without_reason_is_422(self, client_and_tokens, routes, monkeypatch):
        route_mod, _calls = routes

        def _dismiss(*a, **k):
            raise ValueError("review_note is required when dismissing a label")

        monkeypatch.setattr(route_mod.library_registry, "dismiss_label", _dismiss)
        client, _student, teacher = client_and_tokens
        response = client.post(
            f"{_LABELS}/{_LABEL_ID}/dismiss", json={"review_note": ""}, headers=_auth(teacher)
        )
        assert response.status_code == 422

    def test_requires_teacher(self, client_and_tokens, routes):
        client, student, _teacher = client_and_tokens
        assert client.get(_LABELS, headers=_auth(student)).status_code == 403


# ---------------------------------------------------------------------------
# 4. 関係
# ---------------------------------------------------------------------------


def _relation(*, status="candidate") -> dict:
    return {
        "id": _RELATION_ID,
        "relation_key": "rel|exact_match|a|b",
        "subject_entry_id": _ENTRY_ID,
        "object_entry_id": _OTHER_ID,
        "kind": "exact_match",
        "status": status,
        "mapping_justification": "manual_curation",
        "reason": "",
        "evidence": [],
        "review_note": "",
        "created_by": None,
        "decided_by": None,
        "decided_at": None,
        "created_at": "",
        "updated_at": "",
    }


class TestRelations:
    def test_list_adds_endpoint_names(self, client_and_tokens, routes, monkeypatch):
        route_mod, _calls = routes
        monkeypatch.setattr(
            route_mod.library_registry, "list_relations", lambda **k: [_relation()]
        )
        monkeypatch.setattr(
            route_mod.library_store,
            "get_entry",
            lambda entry_id: _entry(entry_id=entry_id, name=f"name-{entry_id[:4]}"),
        )
        client, _student, teacher = client_and_tokens
        response = client.get(_RELATIONS, headers=_auth(teacher))
        assert response.status_code == 200
        relation = response.json()["relations"][0]
        assert relation["subject_name"] == "name-1111"
        assert relation["object_name"] == "name-2222"
        assert "confidence" not in relation

    def test_manual_creation_uses_manual_curation(self, client_and_tokens, routes, monkeypatch):
        route_mod, _calls = routes
        seen: dict = {}

        def _create(**kwargs):
            seen.update(kwargs)
            return _relation()

        monkeypatch.setattr(route_mod.library_registry, "create_relation", _create)
        client, _student, teacher = client_and_tokens
        response = client.post(
            _RELATIONS,
            json={
                "subject_entry_id": _ENTRY_ID,
                "object_entry_id": _OTHER_ID,
                "kind": "exact_match",
            },
            headers=_auth(teacher),
        )
        assert response.status_code == 201
        assert seen["mapping_justification"] == "manual_curation"
        assert response.json()["relation"]["status"] == "candidate"

    def test_decide_missing_relation_is_404(self, client_and_tokens, routes, monkeypatch):
        route_mod, _calls = routes
        monkeypatch.setattr(
            route_mod.library_registry, "decide_relation", lambda *a, **k: None
        )
        client, _student, teacher = client_and_tokens
        response = client.post(
            f"{_RELATIONS}/{_RELATION_ID}/decide",
            json={"status": "confirmed"},
            headers=_auth(teacher),
        )
        assert response.status_code == 404

    def test_decide_confirms(self, client_and_tokens, routes, monkeypatch):
        route_mod, _calls = routes
        monkeypatch.setattr(
            route_mod.library_registry,
            "decide_relation",
            lambda *a, **k: _relation(status="confirmed"),
        )
        client, _student, teacher = client_and_tokens
        response = client.post(
            f"{_RELATIONS}/{_RELATION_ID}/decide",
            json={"status": "confirmed"},
            headers=_auth(teacher),
        )
        assert response.status_code == 200
        assert response.json()["relation"]["status"] == "confirmed"

    def test_requires_teacher(self, client_and_tokens, routes):
        client, student, _teacher = client_and_tokens
        assert client.get(_RELATIONS, headers=_auth(student)).status_code == 403


# ---------------------------------------------------------------------------
# 5. 地図との対応（版非依存 = KR9）
# ---------------------------------------------------------------------------


def _node_link(*, status="candidate") -> dict:
    return {
        "id": _LINK_ID,
        "link_key": f"anode|{_ENTRY_ID}|astro|cosmic_web",
        "entry_id": _ENTRY_ID,
        "domain_key": "astro",
        "node_id": "cosmic_web",
        "node_kind": "concept",
        "kind": "exact_match",
        "status": status,
        "mapping_justification": "lexical_match",
        "reason": "",
        "evidence": [],
        "review_note": "",
        "created_by": None,
        "decided_by": None,
        "decided_at": None,
        "created_at": "",
        "updated_at": "",
    }


class TestAtlasLinks:
    def test_list_annotates_presence_in_the_current_version(
        self, client_and_tokens, routes, monkeypatch
    ):
        route_mod, _calls = routes
        monkeypatch.setattr(
            route_mod.library_registry, "list_node_links", lambda **k: [_node_link()]
        )
        monkeypatch.setattr(
            route_mod.atlas_store, "load_frozen_skeleton", lambda *a, **k: None
        )
        client, _student, teacher = client_and_tokens
        response = client.get(_ATLAS_LINKS + "?domain_key=astro", headers=_auth(teacher))
        assert response.status_code == 200
        payload = response.json()
        link = payload["links"][0]
        # 骨格が読めないときは「分からない」を false と偽らない（KR9）。
        assert link["node_in_current_version"] is None
        assert payload["skeleton_version"] == ""
        assert "confidence" not in link

    def test_skeleton_failure_does_not_break_the_listing(
        self, client_and_tokens, routes, monkeypatch
    ):
        route_mod, _calls = routes

        def _boom(*a, **k):
            raise RuntimeError("db down")

        monkeypatch.setattr(
            route_mod.library_registry, "list_node_links", lambda **k: [_node_link()]
        )
        monkeypatch.setattr(route_mod.atlas_store, "load_frozen_skeleton", _boom)
        client, _student, teacher = client_and_tokens
        response = client.get(_ATLAS_LINKS + "?domain_key=astro", headers=_auth(teacher))
        assert response.status_code == 200
        assert response.json()["links"][0]["node_in_current_version"] is None

    def test_decide_missing_link_is_404(self, client_and_tokens, routes, monkeypatch):
        route_mod, _calls = routes
        monkeypatch.setattr(
            route_mod.library_registry, "decide_node_link", lambda *a, **k: None
        )
        client, _student, teacher = client_and_tokens
        response = client.post(
            f"{_ATLAS_LINKS}/{_LINK_ID}/decide",
            json={"status": "dismissed", "review_note": "別の概念の方が近い"},
            headers=_auth(teacher),
        )
        assert response.status_code == 404

    def test_decide_dismisses(self, client_and_tokens, routes, monkeypatch):
        route_mod, _calls = routes
        monkeypatch.setattr(
            route_mod.library_registry,
            "decide_node_link",
            lambda *a, **k: _node_link(status="dismissed"),
        )
        client, _student, teacher = client_and_tokens
        response = client.post(
            f"{_ATLAS_LINKS}/{_LINK_ID}/decide",
            json={"status": "dismissed", "review_note": "別の概念の方が近い"},
            headers=_auth(teacher),
        )
        assert response.status_code == 200
        assert response.json()["link"]["status"] == "dismissed"

    def test_requires_teacher(self, client_and_tokens, routes):
        client, student, _teacher = client_and_tokens
        assert client.get(_ATLAS_LINKS, headers=_auth(student)).status_code == 403


# ---------------------------------------------------------------------------
# 6. 凍結の弁（KR2）— 未確定の概念は凍結できない
# ---------------------------------------------------------------------------


class TestFreezeGate:
    def test_unconfirmed_entry_freeze_is_409(self, client_and_tokens, routes, monkeypatch):
        route_mod, _calls = routes

        def _freeze(entry_id, **kwargs):
            raise route_mod.LibraryConflictError(
                "確定していない概念は凍結できません。先に確定してください"
            )

        monkeypatch.setattr(route_mod.library_store, "freeze_entry", _freeze)
        client, _student, teacher = client_and_tokens
        response = client.post(f"{_ENTRIES}/{_ENTRY_ID}/freeze", json={}, headers=_auth(teacher))
        assert response.status_code == 409
        assert "確定" in response.json()["detail"]
