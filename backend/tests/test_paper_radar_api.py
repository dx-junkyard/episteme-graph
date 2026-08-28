"""論文レーダー — 管理 API（``api/routes/paper_discovery.py`` の ``/radar/*` 3本）。

対象エンドポイント（全て TEACHER 以上）:
  - ``GET  /api/admin/discovery/radar/seed``
  - ``POST /api/admin/discovery/radar/search``
  - ``POST /api/admin/discovery/radar/compare``
  - ``POST /api/admin/discovery/radar/provenance``（arXiv 出所の後付け登録）

``tests/test_paper_discovery_api.py`` の流儀（実 app + ``TestClient`` + ``_pg_session``
のフェイク差し替え）を踏襲する。DB・MinIO・arXiv・LLM には接続しない。

検証観点（設計書 ``paper_radar_design.md`` §2 の PR1〜PR8 / §5.5）:
  1. 権限の fail-closed（STUDENT は全て 403 / 匿名は 401 か 403）
  2. **document 可視性ゲート**（不可視と不在は同一 404 — PR8）
  3. 距離語彙の fail-closed（語彙外は 422・arXiv を呼ばない）
  4. 比較分析のエラー写像（空 422 / 上限超過 422 / 素材なし 422 / 日次上限 429 /
     LLM・arXiv 失敗 502）と ``caveat`` がサーバ側定数であること（PR4）
  5. DTO に cosine 生値・確度の数値が現れない（PR2 / PD4）
  6. 副作用ゼロ（購読・見送りへ書かない・監査を記帳しない — PR5。例外は
     ``/radar/provenance`` = 教員の明示操作 + 監査記帳）
  7. arXiv 出所の後付け登録の3段階（推定 → タイトル一致で自動記帳 → 不一致は
     ``confirm`` の明示確定）と、**edit 権限**の 403 / 権限フラグ ``can_register``
     の注入
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
    "provenance": {
        "status": "registered",
        "arxiv_id": "2608.20293",
        "arxiv_title": "",
        "arxiv_abs_url": None,
        "document_title": "起点論文",
        "title_match": False,
        "fetched": False,
    },
}

#: 後付け登録の対象（``source_url`` 未登録でファイル名から推定できた教材）の seed。
_INFERRED_ID = "2407.01221"


def _inferred_seed(*, title_match=True, fetched=True, arxiv_id=_INFERRED_ID) -> dict:
    seed = dict(_SEED)
    seed["arxiv_id"] = None
    seed["abs_url"] = None
    seed["categories_source"] = "arxiv_inferred"
    seed["provenance"] = {
        "status": "inferred",
        "arxiv_id": arxiv_id,
        "arxiv_title": "Dark Energy: A Review",
        "arxiv_abs_url": f"https://arxiv.org/abs/{arxiv_id}",
        "document_title": "Dark Energy: A Review",
        "title_match": title_match,
        "fetched": fetched,
    }
    return seed


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
        "register_calls": [],
        "register_error": None,
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

    def _register(session, document_id, arxiv_id):
        if state["register_error"] is not None:
            raise state["register_error"]
        state["register_calls"].append((document_id, arxiv_id))
        # 記帳後は「登録済み」の seed が導出されるようになる（route は再導出して返す）。
        state["seed"] = dict(_SEED)
        return f"https://arxiv.org/abs/{arxiv_id}"

    monkeypatch.setattr(routes.pd_radar, "resolve_seed", _resolve_seed)
    monkeypatch.setattr(routes.pd_radar, "run_radar_search", _run_radar_search)
    monkeypatch.setattr(routes.pd_compare, "run_compare", _run_compare)
    monkeypatch.setattr(routes.pd_radar, "register_arxiv_provenance", _register)

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
_PROVENANCE_PATH = "/api/admin/discovery/radar/provenance"

_ALL_ENDPOINTS = [
    ("get", _SEED_PATH, None),
    ("post", _SEARCH_PATH, {"document_ref": "doc-1", "distance": "near"}),
    ("post", _COMPARE_PATH, {"document_ref": "doc-1", "arxiv_ids": ["2608.00002"]}),
    ("post", _PROVENANCE_PATH, {"document_ref": "doc-1", "arxiv_id": _INFERRED_ID}),
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

    def test_inferred_provenance_is_passed_through(self, env):
        env["seed"] = _inferred_seed()
        seed = env["client"].get(_SEED_PATH, headers=_auth(env, "teacher")).json()["seed"]
        assert seed["arxiv_id"] is None, "推定を登録済み ID に昇格させない"
        assert seed["categories_source"] == "arxiv_inferred"
        assert seed["provenance"]["status"] == "inferred"
        assert seed["provenance"]["arxiv_id"] == _INFERRED_ID

    def test_can_register_is_injected_by_the_route(self, env):
        """権限は core が知らないので route が注入する（フロントの導線出し分け用）。"""
        seed = env["client"].get(_SEED_PATH, headers=_auth(env, "teacher")).json()["seed"]
        assert seed["provenance"]["can_register"] is True

    def test_can_register_is_false_for_a_viewer(self, env):
        env["access"] = env["services"].DocumentAccess(
            document_id="doc-1", source_path="mat-1", uploaded_by="someone",
            is_owner=False, can_view=True, can_edit=False,
        )
        seed = env["client"].get(_SEED_PATH, headers=_auth(env, "teacher")).json()["seed"]
        assert seed["provenance"]["can_register"] is False

    def test_can_register_is_true_for_system_admin(self, env):
        env["access"] = env["services"].DocumentAccess(
            document_id="doc-1", source_path="mat-1", uploaded_by="someone",
            is_owner=False, can_view=False, can_edit=False,
        )
        seed = env["client"].get(_SEED_PATH, headers=_auth(env, "admin")).json()["seed"]
        assert seed["provenance"]["can_register"] is True


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


# ---------------------------------------------------------------------------
# 6. arXiv 出所の後付け登録（3段階）
# ---------------------------------------------------------------------------


class TestRadarProvenance:
    def _post(self, env, body, who="teacher"):
        return env["client"].post(_PROVENANCE_PATH, json=body, headers=_auth(env, who))

    def _body(self, **overrides) -> dict:
        body = {"document_ref": "doc-1", "arxiv_id": _INFERRED_ID}
        body.update(overrides)
        return body

    # ── 権限（view は 404・edit は 403）────────────────────────────────────
    def test_viewer_without_edit_is_403(self, env):
        env["seed"] = _inferred_seed()
        env["access"] = env["services"].DocumentAccess(
            document_id="doc-1", source_path="mat-1", uploaded_by="someone",
            is_owner=False, can_view=True, can_edit=False,
        )
        res = self._post(env, self._body())
        assert res.status_code == 403
        assert res.json()["detail"] == env["routes"]._DETAIL_PROVENANCE_FORBIDDEN
        assert env["register_calls"] == []

    def test_system_admin_may_register_without_edit(self, env):
        env["seed"] = _inferred_seed()
        env["access"] = env["services"].DocumentAccess(
            document_id="doc-1", source_path="mat-1", uploaded_by="someone",
            is_owner=False, can_view=False, can_edit=False,
        )
        res = self._post(env, self._body(), who="admin")
        assert res.status_code == 200
        assert env["register_calls"] == [("doc-1", _INFERRED_ID)]

    # ── 2段階目: タイトル一致は確認なしで記帳 ─────────────────────────────
    def test_title_match_registers_without_confirmation(self, env):
        env["seed"] = _inferred_seed(title_match=True)
        res = self._post(env, self._body())
        assert res.status_code == 200
        payload = res.json()
        assert payload["registered"] is True
        assert env["register_calls"] == [("doc-1", _INFERRED_ID)]
        # 記帳後の seed をそのまま返す（フロントが再取得しなくても表示を切り替えられる）。
        assert payload["seed"]["provenance"]["status"] == "registered"
        assert payload["seed"]["provenance"]["can_register"] is True

    def test_registration_is_audited_with_the_method(self, env):
        env["seed"] = _inferred_seed(title_match=True)
        self._post(env, self._body())
        assert len(env["audits"]) == 1
        entity_type, entity_id, old, new, user_id, metadata = env["audits"][0]
        from core.schema import AUDIT_ENTITY_PAPER_DISCOVERY

        assert entity_type == AUDIT_ENTITY_PAPER_DISCOVERY
        assert entity_id == "doc-1"
        assert old == env["routes"]._STATUS_NONE
        assert new == "provenance_registered"
        assert user_id == _TEACHER
        assert metadata["action"] == "register_provenance"
        assert metadata["arxiv_id"] == _INFERRED_ID
        assert metadata["method"] == env["routes"]._PROVENANCE_METHOD_AUTO

    def test_version_suffix_is_normalized_before_matching(self, env):
        env["seed"] = _inferred_seed(title_match=True)
        res = self._post(env, self._body(arxiv_id=f"{_INFERRED_ID}v2"))
        assert res.status_code == 200
        assert env["register_calls"] == [("doc-1", _INFERRED_ID)]

    # ── 3段階目: 不一致は明示確定でのみ ──────────────────────────────────
    def test_title_mismatch_requires_confirmation(self, env):
        env["seed"] = _inferred_seed(title_match=False)
        res = self._post(env, self._body())
        assert res.status_code == 409
        assert res.json()["detail"] == env["routes"]._DETAIL_PROVENANCE_TITLE_MISMATCH
        assert env["register_calls"] == []
        assert env["audits"] == []

    def test_confirmed_mismatch_is_registered_as_teacher_confirmed(self, env):
        env["seed"] = _inferred_seed(title_match=False)
        res = self._post(env, self._body(confirm=True))
        assert res.status_code == 200
        assert env["register_calls"] == [("doc-1", _INFERRED_ID)]
        assert env["audits"][0][5]["method"] == env["routes"]._PROVENANCE_METHOD_CONFIRMED

    # ── サーバ側の再検証（クライアントの提示を信用しない）──────────────────
    def test_already_registered_is_409(self, env):
        # 既定の seed は登録済み。
        res = self._post(env, self._body())
        assert res.status_code == 409
        assert res.json()["detail"] == env["routes"]._DETAIL_PROVENANCE_ALREADY
        assert env["register_calls"] == []

    def test_id_mismatch_is_422(self, env):
        env["seed"] = _inferred_seed(arxiv_id="2407.09999")
        res = self._post(env, self._body())
        assert res.status_code == 422
        assert res.json()["detail"] == env["routes"]._DETAIL_PROVENANCE_MISMATCH
        assert env["register_calls"] == []

    def test_nothing_inferred_is_422(self, env):
        seed = dict(_SEED)
        seed["arxiv_id"] = None
        seed["abs_url"] = None
        seed["provenance"] = dict(_SEED["provenance"], status="none", arxiv_id=None)
        env["seed"] = seed
        res = self._post(env, self._body())
        assert res.status_code == 422
        assert env["register_calls"] == []

    def test_unfetched_metadata_is_422_even_with_confirmation(self, env):
        """照合材料が無いまま出所を確定させない（confirm でも記帳しない）。"""
        env["seed"] = _inferred_seed(title_match=False, fetched=False)
        res = self._post(env, self._body(confirm=True))
        assert res.status_code == 422
        assert res.json()["detail"] == env["routes"]._DETAIL_PROVENANCE_UNVERIFIED
        assert env["register_calls"] == []

    def test_unparsable_arxiv_id_is_422(self, env):
        env["seed"] = _inferred_seed()
        res = self._post(env, self._body(arxiv_id="not-an-id"))
        assert res.status_code == 422
        assert res.json()["detail"] == env["routes"]._DETAIL_INVALID_ARXIV_ID
        assert env["register_calls"] == []

    def test_concurrent_registration_is_409_with_the_core_message(self, env):
        env["seed"] = _inferred_seed()
        env["register_error"] = ValueError("この教材には、すでに取得元が登録されています。")
        res = self._post(env, self._body())
        assert res.status_code == 409
        assert "すでに取得元" in res.json()["detail"]
        assert env["audits"] == []

    def test_error_details_carry_no_numbers_or_internal_data(self, env):
        details = [
            env["routes"]._DETAIL_PROVENANCE_FORBIDDEN,
            env["routes"]._DETAIL_PROVENANCE_ALREADY,
            env["routes"]._DETAIL_PROVENANCE_MISMATCH,
            env["routes"]._DETAIL_PROVENANCE_UNVERIFIED,
            env["routes"]._DETAIL_PROVENANCE_TITLE_MISMATCH,
        ]
        for detail in details:
            assert not any(ch.isdigit() for ch in detail), detail
            assert "http" not in detail
