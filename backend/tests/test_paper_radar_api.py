"""論文レーダー — 管理 API（``api/routes/paper_discovery.py`` の ``/radar/*` 3本）。

対象エンドポイント（全て TEACHER 以上）:
  - ``GET  /api/admin/discovery/radar/seed``
  - ``POST /api/admin/discovery/radar/search``
  - ``POST /api/admin/discovery/radar/compare``

``tests/test_paper_discovery_api.py`` の流儀（実 app + ``TestClient`` + ``_pg_session``
のフェイク差し替え）を踏襲する。DB・MinIO・arXiv・LLM には接続しない。

検証観点（設計書 ``paper_radar_design.md`` §2 の PR1〜PR8 / §5.5）:
  1. 権限の fail-closed（STUDENT は全て 403 / 匿名は 401 か 403）
  2. **document 可視性ゲート**（不可視と不在は同一 404 — PR8）
  3. 距離語彙の fail-closed（語彙外は 422・arXiv を呼ばない）
  4. 比較分析のエラー写像（空 422 / 上限超過 422 / 素材なし 422 / 日次上限 429 /
     LLM・arXiv 失敗 502）と ``caveat`` がサーバ側定数であること（PR4）
  5. DTO に cosine 生値・確度の数値が現れない（PR2 / PD4）
  6. 副作用ゼロ（購読・見送りへ書かない・監査を記帳しない — PR5）
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
        sys.path.insert(0, str(_path))

_ADMIN = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_TEACHER = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
_STUDENT = "cccccccc-cccc-cccc-cccc-cccccccccccc"

_SEED = {
    "document_id": "doc-1",
    "title": "起点論文",
    "arxiv_id": "2608.20293",
    "abs_url": "https://arxiv.org/abs/2608.20293",
    "summary": "An abstract.",
    "categories": ["astro-ph.CO"],
    "categories_source": "arxiv",
    "keyphrase_candidates": [{"text": "dark energy", "source": "component", "enabled": True}],
    "domain_key": "astrophysics",
}


class _Result:
    def fetchall(self):
        return []

    def fetchone(self):
        return None


class _Session:
    def execute(self, *args, **kwargs):
        return _Result()

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


@pytest.fixture
def env(monkeypatch):
    from fastapi.testclient import TestClient
    from api.main import app
    from dependencies import ROLE_STUDENT, ROLE_SYSTEM_ADMIN, ROLE_TEACHER, _create_token
    import routes.paper_discovery as routes
    import services as services_mod

    state: dict = {
        "audits": [],
        "access": services_mod.DocumentAccess(
            document_id="doc-1", source_path="mat-1", uploaded_by=_TEACHER,
            is_owner=True, can_view=True, can_edit=True,
        ),
        "seed": dict(_SEED),
        "seed_error": None,
        "search_calls": [],
        "search_result": None,
        "search_error": None,
        "compare_calls": [],
        "compare_result": {
            "items": [
                {
                    "arxiv_id": "2608.00002",
                    "title": "候補",
                    "common_ground": "どちらも状態方程式を扱っているようです。",
                    "differences": [
                        {
                            "aspect": "method",
                            "statement": "統計手法が異なるように見えます。",
                            "evidence_quote": "a Bayesian approach",
                        }
                    ],
                    "caveat": routes.pd_compare.CAVEAT,
                }
            ],
            "skipped": [],
            "notes": [],
        },
        "compare_error": None,
    }

    monkeypatch.setattr(routes, "_pg_session", lambda: _Session())
    monkeypatch.setattr(
        routes, "record_review_event", lambda *args: state["audits"].append(args)
    )
    monkeypatch.setattr(
        routes.services, "resolve_document_access",
        lambda user_id, ref: state["access"],
    )

    def _resolve_seed(session, document_id, **kwargs):
        if state["seed_error"] is not None:
            raise state["seed_error"]
        return dict(state["seed"])

    def _run_radar_search(session, document_id, **kwargs):
        state["search_calls"].append((document_id, kwargs))
        if state["search_error"] is not None:
            raise state["search_error"]
        return state["search_result"] or {
            "seed": dict(state["seed"]),
            "query": '(cat:astro-ph.CO) AND (all:"dark energy")',
            "distance": kwargs.get("distance"),
            "total": 1,
            "start": kwargs.get("start", 0),
            "candidates": [
                {"arxiv_id": "2608.00002", "title": "候補", "status": "new",
                 "matched_keyphrases": ["dark energy"], "distance_label": "近い"},
            ],
            "banding": {"available": True},
            "closed_world_note": routes.pd_search.CLOSED_WORLD_NOTE,
        }

    def _run_compare(session, document_id, arxiv_ids, **kwargs):
        state["compare_calls"].append((document_id, list(arxiv_ids), kwargs))
        if state["compare_error"] is not None:
            raise state["compare_error"]
        return state["compare_result"]

    monkeypatch.setattr(routes.pd_radar, "resolve_seed", _resolve_seed)
    monkeypatch.setattr(routes.pd_radar, "run_radar_search", _run_radar_search)
    monkeypatch.setattr(routes.pd_compare, "run_compare", _run_compare)

    # 日次ゲートはプロセス内 in-memory なのでテストごとに初期化する。
    routes._radar_compare_gate.daily_counts.clear()
    routes._radar_compare_gate.session_counts.clear()

    state["client"] = TestClient(app)
    state["routes"] = routes
    state["services"] = services_mod
    state["tokens"] = {
        "admin": _create_token(_ADMIN, "kanri", "kanri@x", ROLE_SYSTEM_ADMIN),
        "teacher": _create_token(_TEACHER, "kyoin", "kyoin@x", ROLE_TEACHER),
        "student": _create_token(_STUDENT, "gakusei", "g@x", ROLE_STUDENT),
    }
    return state


def _auth(env, who):
    return {"Authorization": "Bearer " + env["tokens"][who]}


def _cap(env, monkeypatch, value: int) -> None:
    """比較分析の日次上限だけを差し替える（route が読む get_settings を置き換える）。"""
    from types import SimpleNamespace

    monkeypatch.setattr(
        env["routes"],
        "get_settings",
        lambda: SimpleNamespace(discovery_compare_max_calls_per_day=value),
    )


_SEED_PATH = "/api/admin/discovery/radar/seed?document_ref=doc-1"
_SEARCH_PATH = "/api/admin/discovery/radar/search"
_COMPARE_PATH = "/api/admin/discovery/radar/compare"

_ALL_ENDPOINTS = [
    ("get", _SEED_PATH, None),
    ("post", _SEARCH_PATH, {"document_ref": "doc-1", "distance": "near"}),
    ("post", _COMPARE_PATH, {"document_ref": "doc-1", "arxiv_ids": ["2608.00002"]}),
]


# ---------------------------------------------------------------------------
# 1. 権限（PR8: TEACHER 以上）
# ---------------------------------------------------------------------------


class TestPermissions:
    @pytest.mark.parametrize("method,path,body", _ALL_ENDPOINTS)
    def test_student_is_forbidden(self, env, method, path, body):
        call = getattr(env["client"], method)
        res = call(path, json=body, headers=_auth(env, "student")) if body is not None \
            else call(path, headers=_auth(env, "student"))
        assert res.status_code == 403
        assert env["search_calls"] == []
        assert env["compare_calls"] == []

    @pytest.mark.parametrize("method,path,body", _ALL_ENDPOINTS)
    def test_anonymous_is_rejected(self, env, method, path, body):
        call = getattr(env["client"], method)
        res = call(path, json=body) if body is not None else call(path)
        assert res.status_code in (401, 403)

    def test_no_learning_route_is_registered(self, env):
        """PR8: 学習者向けのレーダー系ルートを作らない。"""
        paths = {getattr(route, "path", "") for route in env["client"].app.routes}
        assert not [p for p in paths if p.startswith("/api/learning") and "radar" in p]


# ---------------------------------------------------------------------------
# 2. document 可視性ゲート（PR8: 不可視と不在は同一 404）
# ---------------------------------------------------------------------------


class TestDocumentGate:
    @pytest.mark.parametrize("method,path,body", _ALL_ENDPOINTS)
    def test_missing_document_is_404(self, env, method, path, body):
        env["access"] = env["services"].DocumentAccess(document_id=None)
        call = getattr(env["client"], method)
        res = call(path, json=body, headers=_auth(env, "teacher")) if body is not None \
            else call(path, headers=_auth(env, "teacher"))
        assert res.status_code == 404
        assert env["search_calls"] == [] and env["compare_calls"] == []

    @pytest.mark.parametrize("method,path,body", _ALL_ENDPOINTS)
    def test_invisible_document_is_404_with_the_same_detail(self, env, method, path, body):
        env["access"] = env["services"].DocumentAccess(
            document_id="doc-1", source_path="mat-1", uploaded_by="someone",
            is_owner=False, can_view=False, can_edit=False,
        )
        call = getattr(env["client"], method)
        res = call(path, json=body, headers=_auth(env, "teacher")) if body is not None \
            else call(path, headers=_auth(env, "teacher"))
        assert res.status_code == 404
        assert res.json()["detail"] == env["routes"]._DETAIL_DOCUMENT_NOT_FOUND

    def test_system_admin_bypasses_visibility(self, env):
        env["access"] = env["services"].DocumentAccess(
            document_id="doc-1", source_path="mat-1", uploaded_by="someone",
            is_owner=False, can_view=False, can_edit=False,
        )
        res = env["client"].get(_SEED_PATH, headers=_auth(env, "admin"))
        assert res.status_code == 200


# ---------------------------------------------------------------------------
# 3. seed
# ---------------------------------------------------------------------------


class TestRadarSeed:
    def test_returns_the_seed_dto(self, env):
        res = env["client"].get(_SEED_PATH, headers=_auth(env, "teacher"))
        assert res.status_code == 200
        seed = res.json()["seed"]
        assert seed["arxiv_id"] == "2608.20293"
        assert seed["categories_source"] == "arxiv"

    def test_lookup_error_is_404(self, env):
        env["seed_error"] = LookupError("document not found")
        res = env["client"].get(_SEED_PATH, headers=_auth(env, "teacher"))
        assert res.status_code == 404

    def test_no_audit_is_recorded(self, env):
        env["client"].get(_SEED_PATH, headers=_auth(env, "teacher"))
        assert env["audits"] == []


# ---------------------------------------------------------------------------
# 4. 検索
# ---------------------------------------------------------------------------


class TestRadarSearch:
    @pytest.mark.parametrize("distance", ["near", "mid", "far"])
    def test_accepts_the_vocabulary(self, env, distance):
        res = env["client"].post(
            _SEARCH_PATH,
            json={"document_ref": "doc-1", "distance": distance},
            headers=_auth(env, "teacher"),
        )
        assert res.status_code == 200
        assert res.json()["distance"] == distance

    @pytest.mark.parametrize("distance", ["", "close", "NEAR", "0"])
    def test_invalid_distance_is_422(self, env, distance):
        res = env["client"].post(
            _SEARCH_PATH,
            json={"document_ref": "doc-1", "distance": distance},
            headers=_auth(env, "teacher"),
        )
        assert res.status_code == 422
        assert res.json()["detail"] == env["routes"]._DETAIL_INVALID_DISTANCE
        assert env["search_calls"] == []

    def test_clamps_max_results_and_start(self, env):
        env["client"].post(
            _SEARCH_PATH,
            json={"document_ref": "doc-1", "distance": "near",
                  "max_results": 99999, "start": -5},
            headers=_auth(env, "teacher"),
        )
        _document_id, kwargs = env["search_calls"][0]
        assert kwargs["max_results"] == env["routes"].MAX_SEARCH_RESULTS
        assert kwargs["start"] == 0

    def test_response_carries_query_and_closed_world_note(self, env):
        res = env["client"].post(
            _SEARCH_PATH,
            json={"document_ref": "doc-1", "distance": "near"},
            headers=_auth(env, "teacher"),
        )
        body = res.json()
        assert body["query"]
        assert body["closed_world_note"]
        assert body["banding"] == {"available": True}

    def test_no_raw_numbers_in_the_candidate_dto(self, env):
        res = env["client"].post(
            _SEARCH_PATH,
            json={"document_ref": "doc-1", "distance": "near"},
            headers=_auth(env, "teacher"),
        )
        for candidate in res.json()["candidates"]:
            assert candidate["distance_label"] == "近い"
            assert not [
                key for key in candidate
                if key in ("score", "similarity", "confidence", "relevance", "rank")
            ]
            assert not [v for v in candidate.values() if isinstance(v, float)]

    def test_arxiv_failure_is_502(self, env):
        from core.paper_discovery import arxiv_client

        env["search_error"] = arxiv_client.ArxivApiError("arXiv への接続に失敗しました")
        res = env["client"].post(
            _SEARCH_PATH,
            json={"document_ref": "doc-1", "distance": "near"},
            headers=_auth(env, "teacher"),
        )
        assert res.status_code == 502
        assert res.json()["detail"] == env["routes"]._DETAIL_ARXIV_UNAVAILABLE

    def test_no_audit_and_no_subscription_write(self, env):
        env["client"].post(
            _SEARCH_PATH,
            json={"document_ref": "doc-1", "distance": "near"},
            headers=_auth(env, "teacher"),
        )
        assert env["audits"] == []


# ---------------------------------------------------------------------------
# 5. 比較分析
# ---------------------------------------------------------------------------


class TestRadarCompare:
    def _post(self, env, body, who="teacher"):
        return env["client"].post(_COMPARE_PATH, json=body, headers=_auth(env, who))

    def test_returns_items_with_the_server_side_caveat(self, env):
        res = self._post(env, {"document_ref": "doc-1", "arxiv_ids": ["2608.00002"]})
        assert res.status_code == 200
        item = res.json()["items"][0]
        assert item["caveat"] == env["routes"].pd_compare.CAVEAT
        assert item["differences"][0]["evidence_quote"]

    def test_no_numeric_confidence_in_the_dto(self, env):
        res = self._post(env, {"document_ref": "doc-1", "arxiv_ids": ["2608.00002"]})
        item = res.json()["items"][0]
        assert not [v for v in item.values() if isinstance(v, float)]
        for difference in item["differences"]:
            assert set(difference) == {"aspect", "statement", "evidence_quote"}

    def test_empty_selection_is_422(self, env):
        res = self._post(env, {"document_ref": "doc-1", "arxiv_ids": []})
        assert res.status_code == 422
        assert res.json()["detail"] == env["routes"]._DETAIL_COMPARE_EMPTY
        assert env["compare_calls"] == []

    def test_too_many_candidates_is_422(self, env):
        limit = env["routes"].pd_compare.RADAR_COMPARE_MAX_CANDIDATES
        ids = [f"2608.{index:05d}" for index in range(limit + 1)]
        res = self._post(env, {"document_ref": "doc-1", "arxiv_ids": ids})
        assert res.status_code == 422
        assert res.json()["detail"] == env["routes"]._DETAIL_COMPARE_TOO_MANY
        assert env["compare_calls"] == []

    def test_no_seed_material_is_422_with_the_core_message(self, env):
        env["compare_error"] = env["routes"].pd_compare.NoSeedMaterialError(
            "この教材には、比較に使える要旨・解析結果がありません。"
        )
        res = self._post(env, {"document_ref": "doc-1", "arxiv_ids": ["2608.00002"]})
        assert res.status_code == 422
        assert "比較に使える" in res.json()["detail"]

    def test_unavailable_is_502(self, env):
        env["compare_error"] = env["routes"].pd_compare.CompareUnavailableError("failed")
        res = self._post(env, {"document_ref": "doc-1", "arxiv_ids": ["2608.00002"]})
        assert res.status_code == 502
        assert res.json()["detail"] == env["routes"]._DETAIL_COMPARE_UNAVAILABLE

    def test_arxiv_failure_is_502(self, env):
        from core.paper_discovery import arxiv_client

        env["compare_error"] = arxiv_client.ArxivApiError("接続に失敗しました")
        res = self._post(env, {"document_ref": "doc-1", "arxiv_ids": ["2608.00002"]})
        assert res.status_code == 502
        assert res.json()["detail"] == env["routes"]._DETAIL_ARXIV_UNAVAILABLE

    def test_daily_limit_is_429_without_numbers(self, env, monkeypatch):
        _cap(env, monkeypatch, 1)
        first = self._post(env, {"document_ref": "doc-1", "arxiv_ids": ["2608.00002"]})
        assert first.status_code == 200
        second = self._post(env, {"document_ref": "doc-1", "arxiv_ids": ["2608.00002"]})
        assert second.status_code == 429
        detail = second.json()["detail"]
        assert detail == env["routes"]._DETAIL_COMPARE_LIMIT
        assert not any(ch.isdigit() for ch in detail)

    def test_quota_is_per_user(self, env, monkeypatch):
        _cap(env, monkeypatch, 1)
        assert self._post(
            env, {"document_ref": "doc-1", "arxiv_ids": ["2608.00002"]}
        ).status_code == 200
        # 別ユーザー（SYSTEM_ADMIN）は自分のカウンタを持つ。
        assert self._post(
            env, {"document_ref": "doc-1", "arxiv_ids": ["2608.00002"]}, who="admin"
        ).status_code == 200

    def test_no_audit_is_recorded(self, env):
        self._post(env, {"document_ref": "doc-1", "arxiv_ids": ["2608.00002"]})
        assert env["audits"] == []
