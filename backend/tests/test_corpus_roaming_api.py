"""コーパス回遊層 — API（``api/routes/corpus.py`` + 教員向け k-匿名集約）。

対象エンドポイント:
  - ``GET  /api/learning/corpus/domains``
  - ``GET  /api/learning/corpus/landscape?domain_key=``
  - ``GET  /api/learning/corpus/documents?domain_key=``
  - ``POST /api/learning/corpus/frontier-interest``
  - ``POST /api/learning/corpus/frontier-interest/{trace_id}/withdraw``
  - ``GET  /api/admin/discovery/frontier-interest``（TEACHER 以上・k-匿名レンジ）

``tests/test_paper_discovery_api.py`` の流儀（実 app + ``TestClient`` + 依存の
monkeypatch）。DB・ネットワークには接続しない。

検証観点（設計書 §4.1 / §6 / §7・CR1/CR3/CR5/CR6/CR8/CR10）:
  1. 可視集合が本人の ``list_visible_document_ids`` から取られ core へ渡る（CR1）
  2. 骨格なしは 404 / domain_key 空は 422（fail-closed）
  3. 関心は語彙 fail-closed（ring 語彙外は 422）・本文を持たない（CR6）
  4. 取り消しは status 遷移のみ・他人の行は 404・**DELETE ルートが存在しない**（CR8）
  5. 教員向けは k-匿名レンジのみで、生の件数・個人・時系列を返さない（CR6/CR3）
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
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_LEARNER = "dddddddd-dddd-dddd-dddd-dddddddddddd"
_TEACHER = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
_STUDENT = "cccccccc-cccc-cccc-cccc-cccccccccccc"


class _FakeSession:
    def close(self):
        pass


@pytest.fixture
def env(monkeypatch):
    from fastapi.testclient import TestClient
    from api.main import app
    from dependencies import ROLE_STUDENT, ROLE_TEACHER, _create_token
    import routes.corpus as corpus_routes
    import routes.paper_discovery as discovery_routes

    state: dict = {
        "visible": {"d1", "d2"},
        "visible_calls": [],
        "domains": [],
        "landscape": None,
        "documents": [],
        "core_calls": [],
        "recorded": [],
        "record_result": "trace-1",
        "withdrawn": [],
        "withdraw_result": True,
        "aggregate_calls": [],
        "aggregate_rows": [],
    }

    def _visible(user_id):
        state["visible_calls"].append(user_id)
        return set(state["visible"])

    monkeypatch.setattr(corpus_routes, "list_visible_document_ids", _visible)
    monkeypatch.setattr(corpus_routes, "_session", lambda: _FakeSession())

    def _domains(session, visible):
        state["core_calls"].append(("domains", sorted(visible)))
        return list(state["domains"])

    def _landscape(session, domain_key, visible):
        state["core_calls"].append(("landscape", domain_key, sorted(visible)))
        return state["landscape"]

    def _documents(session, domain_key, visible):
        state["core_calls"].append(("documents", domain_key, sorted(visible)))
        return list(state["documents"])

    monkeypatch.setattr(corpus_routes.corpus_view, "list_corpus_domains", _domains)
    monkeypatch.setattr(corpus_routes.corpus_view, "build_corpus_landscape", _landscape)
    monkeypatch.setattr(corpus_routes.corpus_view, "list_corpus_documents", _documents)

    def _record(user_id, domain_key, ring, region_id=""):
        state["recorded"].append((user_id, domain_key, ring, region_id))
        return state["record_result"]

    def _withdraw(user_id, trace_id):
        state["withdrawn"].append((user_id, trace_id))
        return state["withdraw_result"]

    monkeypatch.setattr(corpus_routes, "record_frontier_interest", _record)
    monkeypatch.setattr(corpus_routes, "withdraw_frontier_interest", _withdraw)

    def _aggregate(domain_key=None):
        state["aggregate_calls"].append(domain_key)
        return list(state["aggregate_rows"])

    monkeypatch.setattr(discovery_routes, "aggregate_frontier_interest", _aggregate)

    state["client"] = TestClient(app)
    state["tokens"] = {
        "learner": _create_token(_LEARNER, "manabi", "m@x", ROLE_STUDENT),
        "teacher": _create_token(_TEACHER, "kyoin", "k@x", ROLE_TEACHER),
        "student": _create_token(_STUDENT, "gakusei", "g@x", ROLE_STUDENT),
    }
    return state


def _auth(env, who="learner"):
    return {"Authorization": "Bearer " + env["tokens"][who]}


# ---------------------------------------------------------------------------
# 1. 認証と可視性（CR1）
# ---------------------------------------------------------------------------


_LEARNER_ENDPOINTS = [
    ("get", "/api/learning/corpus/domains", None),
    ("get", "/api/learning/corpus/landscape?domain_key=astrophysics", None),
    ("get", "/api/learning/corpus/documents?domain_key=astrophysics", None),
    ("post", "/api/learning/corpus/frontier-interest",
     {"domain_key": "astrophysics", "ring": "fringe"}),
]


class TestAuthentication:
    @pytest.mark.parametrize("method,path,body", _LEARNER_ENDPOINTS)
    def test_requires_authentication(self, env, method, path, body):
        call = getattr(env["client"], method)
        response = call(path) if body is None else call(path, json=body)
        assert response.status_code in (401, 403)

    def test_visible_set_comes_from_the_authenticated_user(self, env):
        env["landscape"] = {"domain_key": "astrophysics", "placements": []}
        env["client"].get(
            "/api/learning/corpus/landscape?domain_key=astrophysics", headers=_auth(env)
        )
        assert env["visible_calls"] == [_LEARNER]
        assert env["core_calls"] == [("landscape", "astrophysics", ["d1", "d2"])]

    def test_empty_visible_set_is_passed_through_as_empty(self, env):
        env["visible"] = set()
        env["documents"] = []
        response = env["client"].get(
            "/api/learning/corpus/documents?domain_key=astrophysics", headers=_auth(env)
        )
        assert response.status_code == 200
        assert response.json() == {"documents": []}
        assert env["core_calls"] == [("documents", "astrophysics", [])]


# ---------------------------------------------------------------------------
# 2. 読み取り3本（§4.1 / §6）
# ---------------------------------------------------------------------------


class TestReadEndpoints:
    def test_domains_wraps_core_output(self, env):
        env["domains"] = [
            {"domain_key": "astrophysics", "domain_name": "宇宙物理",
             "frozen_version": "v3", "has_visible_papers": True},
        ]
        response = env["client"].get("/api/learning/corpus/domains", headers=_auth(env))
        assert response.status_code == 200
        assert response.json() == {"domains": env["domains"]}

    def test_landscape_without_skeleton_is_404(self, env):
        env["landscape"] = None
        response = env["client"].get(
            "/api/learning/corpus/landscape?domain_key=nothing", headers=_auth(env)
        )
        assert response.status_code == 404

    @pytest.mark.parametrize(
        "path",
        ["/api/learning/corpus/landscape", "/api/learning/corpus/documents"],
    )
    def test_missing_domain_key_is_422(self, env, path):
        response = env["client"].get(path, headers=_auth(env))
        assert response.status_code == 422
        assert env["core_calls"] == []

    def test_landscape_payload_is_passed_through(self, env):
        env["landscape"] = {
            "domain_key": "astrophysics",
            "skeleton_version": "v3",
            "placements": [],
            "fringe": [{"region_id": "r_dark", "region_label": "ダークエネルギー",
                        "fact_line": "…", "paper_titles": ["A"]}],
            "outer": None,
        }
        response = env["client"].get(
            "/api/learning/corpus/landscape?domain_key=astrophysics", headers=_auth(env)
        )
        assert response.status_code == 200
        assert response.json() == env["landscape"]


# ---------------------------------------------------------------------------
# 3. 関心信号（Phase D / §7）
# ---------------------------------------------------------------------------


class TestFrontierInterest:
    def test_records_ring_and_region_only(self, env):
        response = env["client"].post(
            "/api/learning/corpus/frontier-interest",
            json={"domain_key": "astrophysics", "ring": "fringe", "region_id": "r_dark"},
            headers=_auth(env),
        )
        assert response.status_code == 201
        assert response.json() == {"ok": True, "trace_id": "trace-1"}
        assert env["recorded"] == [(_LEARNER, "astrophysics", "fringe", "r_dark")]

    def test_outer_ring_without_region_is_allowed(self, env):
        response = env["client"].post(
            "/api/learning/corpus/frontier-interest",
            json={"domain_key": "astrophysics", "ring": "outer"},
            headers=_auth(env),
        )
        assert response.status_code == 201
        assert env["recorded"] == [(_LEARNER, "astrophysics", "outer", "")]

    @pytest.mark.parametrize("ring", ["", "inner", "FRINGE", "edge"])
    def test_unknown_ring_is_422(self, env, ring):
        response = env["client"].post(
            "/api/learning/corpus/frontier-interest",
            json={"domain_key": "astrophysics", "ring": ring},
            headers=_auth(env),
        )
        assert response.status_code == 422
        assert env["recorded"] == []

    def test_missing_domain_key_is_422(self, env):
        response = env["client"].post(
            "/api/learning/corpus/frontier-interest",
            json={"domain_key": "  ", "ring": "fringe"},
            headers=_auth(env),
        )
        assert response.status_code == 422
        assert env["recorded"] == []

    def test_withdraw_transitions_and_is_scoped_to_the_owner(self, env):
        response = env["client"].post(
            "/api/learning/corpus/frontier-interest/trace-1/withdraw", headers=_auth(env)
        )
        assert response.status_code == 200
        assert env["withdrawn"] == [(_LEARNER, "trace-1")]

    def test_withdraw_of_another_users_trace_is_404(self, env):
        env["withdraw_result"] = False
        response = env["client"].post(
            "/api/learning/corpus/frontier-interest/other/withdraw", headers=_auth(env)
        )
        assert response.status_code == 404

    def test_no_delete_route_exists(self, env):
        from tests.guardrail_helpers import collect_route_pairs
        from api.main import app

        deletes = [
            path for path, method in collect_route_pairs(app)
            if method == "DELETE" and path.startswith("/api/learning/corpus")
        ]
        assert deletes == [], "コーパス回遊層に行削除 API を作らない（CR8）"


# ---------------------------------------------------------------------------
# 4. 教員向け k-匿名集約（§7）
# ---------------------------------------------------------------------------


class TestTeacherAggregation:
    def test_student_is_rejected(self, env):
        response = env["client"].get(
            "/api/admin/discovery/frontier-interest", headers=_auth(env, "student")
        )
        assert response.status_code == 403

    def test_teacher_gets_range_labels_only(self, env):
        env["aggregate_rows"] = [
            {"domain_key": "astrophysics", "region_id": "r_dark",
             "ring": "fringe", "range_label": "3-5"},
        ]
        response = env["client"].get(
            "/api/admin/discovery/frontier-interest?domain_key=astrophysics",
            headers=_auth(env, "teacher"),
        )
        assert response.status_code == 200
        body = response.json()
        assert body == {"rows": env["aggregate_rows"]}
        assert env["aggregate_calls"] == ["astrophysics"]
        for row in body["rows"]:
            assert "count" not in row and "learners" not in row
            assert "user_id" not in row and "created_at" not in row

    def test_domain_key_is_optional(self, env):
        response = env["client"].get(
            "/api/admin/discovery/frontier-interest", headers=_auth(env, "teacher")
        )
        assert response.status_code == 200
        assert env["aggregate_calls"] == [None]
