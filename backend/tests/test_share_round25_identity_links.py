"""第 25 周・面 A 第 3 周: 同一性リンクの行と作成結果の是正（IK-0916 / IK-0917 / IK-0918）。

- IK-0916: 行は部品側を名前・論文の題名で読めるようにし、内部の型名・UUID を本文に描かない。
  確信度の段階は AI・照合由来の未確定の行だけに付け、手動・確定済みは出所の語で示す。
- IK-0917: 未確定の行に、確定の直前に読む事実文（同じものとして扱う・学生に何が出るか）を添える。
- IK-0918: 同じ組を作り直したとき、既存（候補 / 確定済み / 却下済み）を事実文で言い分ける。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "api"))

from core.deliberation import identity_links  # noqa: E402
from core.deliberation.schema import ElementRef  # noqa: E402

DELIBERATION_JS = ROOT / "frontend" / "public" / "js" / "deliberation.js"
_DOC = "11111111-1111-1111-1111-111111111111"
_PART = "33333333-3333-3333-3333-333333333333"
_COMP = "44444444-4444-4444-4444-444444444444"


def _link(**over):
    base = {
        "id": "55555555-5555-5555-5555-555555555555",
        "instance_element_type": "theory_component",
        "instance_element_id": _COMP,
        "instance_document_id": _DOC,
        "shared_part_id": _PART,
        "status": "candidate",
        "local_expression": {},
        "evidence": [],
        "reason": "",
        "confidence": None,
        "mapping_justification": "manual_curation",
    }
    base.update(over)
    return base


class TestConfidenceOnlyForMachineRows:
    def test_manual_candidate_has_no_confidence(self):
        assert identity_links.shows_confidence(_link()) is False
        assert identity_links.provenance_label(_link()) == identity_links.PROVENANCE_TEACHER_CREATED

    def test_confirmed_has_no_confidence(self):
        row = _link(status="confirmed", mapping_justification="vector_similarity")
        assert identity_links.shows_confidence(row) is False
        assert identity_links.provenance_label(row) == "教員が確定"

    def test_machine_candidate_shows_confidence(self):
        row = _link(mapping_justification="vector_similarity", confidence=0.8)
        assert identity_links.shows_confidence(row) is True
        assert identity_links.provenance_label(row) == identity_links.PROVENANCE_MACHINE_CANDIDATE


class TestOutcomeFacts:
    def test_every_outcome_has_a_fact(self):
        for outcome in identity_links.LINK_OUTCOMES:
            assert identity_links.LINK_OUTCOME_FACTS[outcome]

    def test_existing_facts_say_nothing_was_created(self):
        for outcome in identity_links.LINK_OUTCOMES[1:]:
            assert "新しくは作っていません" in identity_links.LINK_OUTCOME_FACTS[outcome]

    def test_create_candidate_reports_existing_status(self, monkeypatch):
        class _Result:
            def __init__(self, row):
                self._row = row

            def fetchone(self):
                return self._row

        existing = (
            "55555555-5555-5555-5555-555555555555", "theory_component", _COMP, _DOC, _PART,
            "confirmed", {}, [], "", None, None, None, None, None, None, "manual_curation",
        )

        class _Session:
            calls = 0

            def execute(self, *_a, **_k):
                _Session.calls += 1
                return _Result(None if _Session.calls == 1 else existing)

            def commit(self):
                pass

            def rollback(self):
                pass

            def close(self):
                pass

        monkeypatch.setattr(identity_links, "get_session", lambda: _Session())
        ref = ElementRef(scope="document", element_type="theory_component", element_id=_COMP, document_id=_DOC)
        link = identity_links.create_candidate(ref, _PART, mapping_justification="manual_curation")
        assert link["outcome"] == identity_links.LINK_OUTCOME_EXISTING_CONFIRMED


@pytest.fixture
def client_and_teacher():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from api.main import app
    from dependencies import _create_token, ROLE_TEACHER

    client = TestClient(app)
    teacher = _create_token("22222222-2222-2222-2222-222222222222", "tea", "tea@x", ROLE_TEACHER)
    return client, {"Authorization": "Bearer " + teacher}


class TestRouteShape:
    def _patch(self, monkeypatch, outcome):
        import routes.deliberation as route_mod

        monkeypatch.setattr(route_mod, "_ensure_document_editable", lambda *a, **k: None)
        monkeypatch.setattr(
            route_mod.refs, "resolve",
            lambda t, i, document_id=None: ElementRef(
                scope="domain" if t == "shared_part" else "document",
                element_type=t, element_id=i,
                document_id=None if t == "shared_part" else _DOC,
                domain_key="astrophysics" if t == "shared_part" else None,
            ),
        )
        row = _link(status="confirmed" if outcome != "created" else "candidate")
        row["outcome"] = outcome
        monkeypatch.setattr(route_mod.identity_links, "create_candidate", lambda *a, **k: dict(row))
        monkeypatch.setattr(
            route_mod.identity_links, "describe_instance",
            lambda link: {
                "instance_type_label": "部品", "instance_label": "TOV network map",
                "instance_summary": "構造ネットワーク", "instance_inputs": [], "instance_outputs": [],
                "instance_document_title": "Neutron star paper",
            },
        )
        monkeypatch.setattr(route_mod.library_store, "get_entry", lambda _id: {"name": "TOV 方程式", "summary": ""})
        events = []
        monkeypatch.setattr(route_mod, "record_review_event", lambda *a, **k: events.append(a))
        return events

    def _post(self, client, headers):
        return client.post(
            "/api/admin/deliberation/identity-links",
            json={
                "instance_element_type": "theory_component",
                "instance_element_id": _COMP,
                "shared_part_id": _PART,
                "document_id": _DOC,
            },
            headers=headers,
        )

    def test_created_is_201_with_fact_and_audit(self, client_and_teacher, monkeypatch):
        client, headers = client_and_teacher
        events = self._patch(monkeypatch, "created")
        response = self._post(client, headers)
        assert response.status_code == 201
        body = response.json()
        assert body["outcome"] == "created"
        assert body["facts"] == [identity_links.LINK_OUTCOME_FACTS["created"]]
        assert body["instance_label"] == "TOV network map"
        assert body["instance_document_title"] == "Neutron star paper"
        assert body["confidence_label"] is None  # 手動作成の行に確信度の段階を付けない
        assert body["confirm_note"] == identity_links.CONFIRM_NOTE
        assert len(events) == 1

    def test_existing_is_200_without_audit(self, client_and_teacher, monkeypatch):
        client, headers = client_and_teacher
        events = self._patch(monkeypatch, "existing_confirmed")
        response = self._post(client, headers)
        assert response.status_code == 200
        body = response.json()
        assert body["outcome"] == "existing_confirmed"
        assert "確定済み" in body["facts"][0]
        assert "confirm_note" not in body
        assert events == []


class TestRowStatic:
    def _block(self):
        src = DELIBERATION_JS.read_text(encoding="utf-8")
        start = src.index("function _identityLinkRowHtml(")
        return src[start:src.index("function _bindIdentityLinkActions(", start)]

    def test_row_does_not_print_internal_type_or_uuid(self):
        block = self._block()
        assert "インスタンス:" not in block
        assert "instance_label" in block
        assert "instance_document_title" in block
        assert "escHtml(link.instance_document_id)" not in block

    def test_row_shows_confirm_note_and_provenance(self):
        block = self._block()
        assert "confirm_note" in block
        assert "provenance_label" in block


class TestSharedPartSearchAttribution:
    """IK-0928: 共通部品の検索の埋め込みは U層の機能に帰属させる。"""

    def test_feature_registered(self):
        from core.llm_usage.schema import KNOWN_FEATURES
        from core.library import search

        assert search.FEATURE_SHARED_PART_SEARCH in KNOWN_FEATURES
        assert search.FEATURE_LIBRARY_SEARCH in KNOWN_FEATURES

    def test_embed_query_uses_embed_with_context(self, monkeypatch):
        from core.library import search
        import core.llm_worker.embedding as emb

        seen = {}

        def _fake(texts, *, feature, **attrs):
            seen["feature"] = feature
            seen["attrs"] = attrs
            return [[0.1, 0.2]]

        monkeypatch.setattr(emb, "embed_with_context", _fake)
        vector = search._embed_query("TOV", feature=search.FEATURE_SHARED_PART_SEARCH, user_id="u1")
        assert vector == [0.1, 0.2]
        assert seen == {"feature": "embedding:shared_part_search", "attrs": {"user_id": "u1"}}

    def test_routes_pass_feature(self):
        src = (ROOT / "backend" / "api" / "routes" / "deliberation.py").read_text(encoding="utf-8")
        assert "feature=library_search.FEATURE_SHARED_PART_SEARCH" in src
        lib = (ROOT / "backend" / "api" / "routes" / "library.py").read_text(encoding="utf-8")
        assert "feature=library_search.FEATURE_LIBRARY_SEARCH" in lib
