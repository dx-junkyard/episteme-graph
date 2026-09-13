"""コーパスを補う論文 — 管理 API（``api/routes/paper_discovery.py`` の2ルート）。

対象エンドポイント（どちらも TEACHER 以上 — CC8）:
  - ``POST /api/admin/discovery/complement/search``（レンズA / B）
  - ``POST /api/admin/discovery/complement/foundation``（レンズC）

正本: ``docs/features/corpus_complement_design.md`` §5.5（API 契約）/ §2（CC1〜CC8）。
``tests/test_paper_discovery_api.py`` の流儀（実 app + ``TestClient`` + ``_pg_session``
と core 関数のフェイク差し替え）を踏襲する。DB・arXiv・Semantic Scholar・LLM には
接続しない。

検証観点:
  1. 権限の fail-closed（STUDENT は 403 / 匿名は 401・403）
  2. ``complement/search`` の DTO 形（``complement.lenses`` の2キー・候補の
     ``complement`` 素通し・補完あり候補が先頭・``order`` は常に relevance）
  3. レンズ・並べ替えが縮退しても 200 で候補が残る（CC8 / PD6）
  4. arXiv 到達不能は 502 + 事実文（空一覧に化けさせない）
  5. ``complement/foundation`` の素通し・オプトイン未設定・502 写像・``commit``
  6. 設定値（``core/config.py``）が core へそのまま渡る
  7. どちらのルートも監査を記帳しない（CC3）
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

SEARCH_PATH = "/api/admin/discovery/complement/search"
FOUNDATION_PATH = "/api/admin/discovery/complement/foundation"


# ---------------------------------------------------------------------------
# フェイクセッション
# ---------------------------------------------------------------------------


class _Result:
    def fetchall(self):
        return []

    def fetchone(self):
        return None


class FakeSession:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0
        self.closed = 0

    def execute(self, stmt, params=None):
        return _Result()

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed += 1


def _candidate(arxiv_id: str, **extra) -> dict:
    payload = {
        "arxiv_id": arxiv_id,
        "title": f"Paper {arxiv_id}",
        "summary": "abstract",
        "status": "new",
    }
    payload.update(extra)
    return payload


COMPLEMENT_ANNOTATION = {
    "fills": [{"node_label": "ダークエネルギー", "region_label": "宇宙論"}],
    "skies": [
        {
            "statement": "背景重力は一様等方とみなせる",
            "document_title": "Seed paper",
            "closed_world_note": "このコーパスの中では検証記録がありません",
        }
    ],
}


@pytest.fixture
def env(monkeypatch):
    """TestClient + フェイクセッション + core（search / complement / ranking / foundation）。"""
    from fastapi.testclient import TestClient
    from api.main import app
    from dependencies import ROLE_STUDENT, ROLE_SYSTEM_ADMIN, ROLE_TEACHER, _create_token
    import routes.paper_discovery as routes

    state: dict = {
        "session": FakeSession(),
        "audits": [],
        # ── 検索（arXiv） ────────────────────────────────────────────────
        "search_calls": [],
        "search_error": None,
        "search_candidates": [_candidate("2608.00001"), _candidate("2608.00002")],
        # ── 補完の材料 ──────────────────────────────────────────────────
        "anchor_context": {"anchors": ["anchor"], "skeleton_version": "v3"},
        "context_calls": [],
        "context_error": None,
        "context_result": None,
        # ── 並べ替え ────────────────────────────────────────────────────
        "rank_calls": [],
        "rank_error": None,
        "rank_result": None,
        # ── レンズC ─────────────────────────────────────────────────────
        "foundation_calls": [],
        "foundation_error": None,
        "foundation_result": None,
    }

    monkeypatch.setattr(routes, "_pg_session", lambda: state["session"])
    monkeypatch.setattr(
        routes, "record_review_event", lambda *args: state["audits"].append(args)
    )

    def _run_search(session, domain_key, **kwargs):
        state["search_calls"].append((domain_key, kwargs))
        if state["search_error"] is not None:
            raise state["search_error"]
        return {
            "domain_key": domain_key,
            "query": '(cat:astro-ph.CO) AND (all:"dark energy")',
            "total": len(state["search_candidates"]),
            "start": kwargs.get("start", 0),
            "candidates": [dict(c) for c in state["search_candidates"]],
            "closed_world_note": routes.pd_search.CLOSED_WORLD_NOTE,
        }

    monkeypatch.setattr(routes.pd_search, "run_search", _run_search)
    monkeypatch.setattr(
        routes, "_anchor_context", lambda session, key: state["anchor_context"]
    )

    def _build_context(session, domain_key, anchor_context, **kwargs):
        state["context_calls"].append((domain_key, anchor_context, kwargs))
        if state["context_error"] is not None:
            raise state["context_error"]
        if state["context_result"] is not None:
            return state["context_result"]
        return {
            "thin_node_ids": {"node-1"},
            "sky_statements": [{"statement": "s", "document_title": "t"}],
            "facts": {"coverage": {"available": True}, "skies": {"available": True}},
        }

    monkeypatch.setattr(routes.pd_complement, "build_complement_context", _build_context)

    def _rank(session, domain_key, candidates, **kwargs):
        state["rank_calls"].append((domain_key, [dict(c) for c in candidates], kwargs))
        if state["rank_error"] is not None:
            raise state["rank_error"]
        if state["rank_result"] is not None:
            return state["rank_result"]
        ordered = []
        for index, candidate in enumerate(candidates):
            payload = dict(candidate)
            payload["relevance_label"] = "関連: 高" if index == 0 else "関連: 中"
            ordered.append(payload)
        facts = dict((kwargs.get("complement_context") or {}).get("facts") or {})
        return {"available": True, "ordered": ordered, "complement_facts": facts}

    monkeypatch.setattr(routes.pd_ranking, "rank_candidates", _rank)

    def _foundation(session, domain_key, **kwargs):
        state["foundation_calls"].append((domain_key, kwargs))
        if state["foundation_error"] is not None:
            raise state["foundation_error"]
        if state["foundation_result"] is not None:
            return state["foundation_result"]
        return {
            "enabled": True,
            "available": True,
            "domain_key": domain_key,
            "candidates": [
                dict(
                    _candidate("1905.12345"),
                    cited_by=[{"arxiv_id": "2608.00001", "title": "Seed"}],
                )
            ],
            "seeds_read": [{"arxiv_id": "2608.00001", "title": "Seed"}],
            "pending_seeds": False,
            "closed_world_note": routes.pd_foundation.CLOSED_WORLD_NOTE,
        }

    monkeypatch.setattr(routes.pd_foundation, "run_foundation_search", _foundation)

    state["client"] = TestClient(app)
    state["routes"] = routes
    state["tokens"] = {
        "admin": _create_token(_ADMIN, "kanri", "kanri@x", ROLE_SYSTEM_ADMIN),
        "teacher": _create_token(_TEACHER, "kyoin", "kyoin@x", ROLE_TEACHER),
        "student": _create_token(_STUDENT, "gakusei", "g@x", ROLE_STUDENT),
    }
    return state


def _auth(env, who):
    return {"Authorization": "Bearer " + env["tokens"][who]}


def _search(env, who="teacher", **body):
    payload = {"domain_key": "astrophysics"}
    payload.update(body)
    return env["client"].post(SEARCH_PATH, json=payload, headers=_auth(env, who))


def _foundation(env, who="teacher", **body):
    payload = {"domain_key": "astrophysics"}
    payload.update(body)
    return env["client"].post(FOUNDATION_PATH, json=payload, headers=_auth(env, who))


# ---------------------------------------------------------------------------
# 1. 権限の fail-closed（CC8 — 教員専用・学習者向け経路を作らない）
# ---------------------------------------------------------------------------


_ENDPOINTS = [
    (SEARCH_PATH, {"domain_key": "astrophysics"}),
    (FOUNDATION_PATH, {"domain_key": "astrophysics"}),
]


class TestPermissions:
    @pytest.mark.parametrize("path,body", _ENDPOINTS)
    def test_student_is_forbidden(self, env, path, body):
        res = env["client"].post(path, json=body, headers=_auth(env, "student"))
        assert res.status_code == 403
        assert env["search_calls"] == []
        assert env["foundation_calls"] == []

    @pytest.mark.parametrize("path,body", _ENDPOINTS)
    def test_anonymous_is_rejected(self, env, path, body):
        res = env["client"].post(path, json=body)
        assert res.status_code in (401, 403)

    @pytest.mark.parametrize("path,body", _ENDPOINTS)
    def test_teacher_and_admin_are_allowed(self, env, path, body):
        for who in ("teacher", "admin"):
            res = env["client"].post(path, json=body, headers=_auth(env, who))
            assert res.status_code == 200

    @pytest.mark.parametrize("path,body", _ENDPOINTS)
    def test_session_is_closed(self, env, path, body):
        env["client"].post(path, json=body, headers=_auth(env, "teacher"))
        assert env["session"].closed == 1


# ---------------------------------------------------------------------------
# 2. POST /complement/search — DTO 形（設計書 §5.5）
# ---------------------------------------------------------------------------


class TestComplementSearchPayload:
    def test_search_dto_is_passed_through_with_complement_block(self, env):
        res = _search(env)
        assert res.status_code == 200
        body = res.json()
        # 既存 /search の DTO はそのまま（PD6 の閉世界注記・クエリを落とさない）。
        assert body["domain_key"] == "astrophysics"
        assert body["query"]
        assert body["closed_world_note"]
        # 並び順は常に relevance（body.order は無視する）。
        assert body["order"] == env["routes"].ORDER_RELEVANCE
        assert body["ranking"] == {"available": True}

    def test_complement_block_has_two_lenses(self, env):
        body = _search(env).json()
        block = body["complement"]
        assert set(block["lenses"]) == {"coverage", "skies"}
        assert block["available"] is True
        assert block["skeleton_version"] == "v3"

    def test_lens_notes_are_passed_through(self, env):
        routes = env["routes"]
        env["context_result"] = {
            "thin_node_ids": set(),
            "sky_statements": [],
            "facts": {
                "coverage": {
                    "available": True,
                    "note": routes.pd_complement.NOTE_NO_THIN_NODES,
                },
                "skies": {
                    "available": False,
                    "note": routes.pd_complement.NOTE_NO_SKIES,
                },
            },
        }
        block = _search(env).json()["complement"]
        assert block["lenses"]["coverage"] == {
            "available": True,
            "note": routes.pd_complement.NOTE_NO_THIN_NODES,
        }
        assert block["lenses"]["skies"] == {
            "available": False,
            "note": routes.pd_complement.NOTE_NO_SKIES,
        }
        # 片方でも成立していれば top-level は available。
        assert block["available"] is True

    def test_both_lenses_degraded_marks_block_unavailable(self, env):
        env["context_result"] = {
            "thin_node_ids": set(),
            "sky_statements": [],
            "facts": {
                "coverage": {"available": False, "note": "a"},
                "skies": {"available": False, "note": "b"},
            },
        }
        block = _search(env).json()["complement"]
        assert block["available"] is False

    def test_skeleton_version_absent_without_anchors(self, env):
        env["anchor_context"] = None
        block = _search(env).json()["complement"]
        assert "skeleton_version" not in block

    def test_candidate_complement_is_passed_through(self, env):
        env["search_candidates"] = [
            _candidate("2608.00001"),
            _candidate("2608.00002", complement=COMPLEMENT_ANNOTATION),
        ]
        candidates = _search(env).json()["candidates"]
        annotated = [c for c in candidates if c.get("complement")]
        assert len(annotated) == 1
        assert annotated[0]["complement"] == COMPLEMENT_ANNOTATION

    def test_candidates_without_complement_have_no_key(self, env):
        candidates = _search(env).json()["candidates"]
        assert all("complement" not in c for c in candidates)

    def test_complement_candidates_come_first(self, env):
        """補完のある候補を先頭へ（残りは関連度順のまま・候補は捨てない）。"""
        env["search_candidates"] = [
            _candidate("2608.00001"),
            _candidate("2608.00002", complement=COMPLEMENT_ANNOTATION),
            _candidate("2608.00003"),
        ]
        candidates = _search(env).json()["candidates"]
        assert [c["arxiv_id"] for c in candidates] == [
            "2608.00002",
            "2608.00001",
            "2608.00003",
        ]

    def test_relevance_label_is_kept(self, env):
        candidates = _search(env).json()["candidates"]
        assert candidates[0]["relevance_label"] == "関連: 高"


# ---------------------------------------------------------------------------
# 3. 入力の扱い（order 無視 / max_results クランプ / 422）
# ---------------------------------------------------------------------------


class TestComplementSearchInput:
    def test_order_is_ignored(self, env):
        for order in ("date", "relevance", "bogus", ""):
            res = _search(env, order=order)
            assert res.status_code == 200
            assert res.json()["order"] == env["routes"].ORDER_RELEVANCE

    def test_max_results_is_clamped(self, env):
        _search(env, max_results=9999)
        assert env["search_calls"][-1][1]["max_results"] == env["routes"].MAX_SEARCH_RESULTS
        _search(env, max_results=0)
        assert env["search_calls"][-1][1]["max_results"] == 1

    def test_negative_start_is_clamped(self, env):
        _search(env, start=-5)
        assert env["search_calls"][-1][1]["start"] == 0

    def test_invalid_condition_is_422(self, env):
        env["search_error"] = ValueError("domain_key must not be empty")
        res = _search(env, domain_key="")
        assert res.status_code == 422
        assert env["session"].rollbacks >= 1

    def test_arxiv_failure_is_502(self, env):
        routes = env["routes"]
        env["search_error"] = routes.arxiv_client.ArxivApiError("boom")
        res = _search(env)
        assert res.status_code == 502
        assert res.json()["detail"] == routes._DETAIL_ARXIV_UNAVAILABLE
        # 内部情報（例外文言）を detail に載せない。
        assert "boom" not in res.json()["detail"]

    def test_search_is_committed_before_complement(self, env):
        _search(env)
        assert env["session"].commits == 1


# ---------------------------------------------------------------------------
# 4. fail-soft（CC8 — 縮退しても検索は成立させる）
# ---------------------------------------------------------------------------


class TestComplementSearchFailSoft:
    def test_ranking_failure_keeps_candidates(self, env):
        env["rank_error"] = RuntimeError("embedding down")
        res = _search(env)
        assert res.status_code == 200
        body = res.json()
        assert [c["arxiv_id"] for c in body["candidates"]] == [
            "2608.00001",
            "2608.00002",
        ]
        assert body["ranking"]["available"] is False
        assert body["ranking"]["note"] == env["routes"].pd_ranking.NOTE_UNAVAILABLE
        # 並べ替えが不成立なら両レンズも事実文で縮退する。
        lenses = body["complement"]["lenses"]
        assert lenses["coverage"]["available"] is False
        assert lenses["skies"]["available"] is False
        assert lenses["coverage"]["note"]

    def test_ranking_unavailable_result_is_reported(self, env):
        routes = env["routes"]
        env["rank_result"] = {
            "available": False,
            "note": routes.pd_ranking.NOTE_NO_CORPUS,
            "ordered": [dict(c) for c in env["search_candidates"]],
            "complement_facts": routes.pd_complement.degraded_facts(),
        }
        body = _search(env).json()
        assert body["ranking"] == {
            "available": False,
            "note": routes.pd_ranking.NOTE_NO_CORPUS,
        }
        assert len(body["candidates"]) == 2

    def test_context_failure_keeps_search(self, env):
        env["context_error"] = RuntimeError("ledger down")
        res = _search(env)
        assert res.status_code == 200
        body = res.json()
        assert len(body["candidates"]) == 2
        # complement_context が組めなければ core へは None が渡る（後方互換の経路）。
        assert env["rank_calls"][-1][2]["complement_context"] is None
        assert body["complement"]["available"] is False

    def test_no_candidates_still_returns_block(self, env):
        env["search_candidates"] = []
        body = _search(env).json()
        assert body["candidates"] == []
        assert set(body["complement"]["lenses"]) == {"coverage", "skies"}


# ---------------------------------------------------------------------------
# 5. core へ渡る材料（設定値・アンカー文脈）
# ---------------------------------------------------------------------------


class TestComplementWiring:
    def test_settings_reach_build_complement_context(self, env):
        from core.config import get_settings

        _search(env)
        domain_key, anchor_context, kwargs = env["context_calls"][-1]
        assert domain_key == "astrophysics"
        assert anchor_context == env["anchor_context"]
        assert kwargs["thin_max_documents"] == (
            get_settings().discovery_complement_thin_max_documents
        )

    def test_rank_candidates_receives_both_contexts(self, env):
        _search(env)
        _domain, _candidates, kwargs = env["rank_calls"][-1]
        assert kwargs["anchor_context"] == env["anchor_context"]
        assert kwargs["complement_context"]["thin_node_ids"] == {"node-1"}

    def test_no_audit_is_recorded(self, env):
        _search(env)
        _foundation(env)
        assert env["audits"] == []


# ---------------------------------------------------------------------------
# 6. POST /complement/foundation（レンズC）
# ---------------------------------------------------------------------------


class TestFoundation:
    def test_core_result_is_passed_through(self, env):
        res = _foundation(env)
        assert res.status_code == 200
        body = res.json()
        assert body["enabled"] is True
        assert body["available"] is True
        assert body["closed_world_note"]
        assert body["candidates"][0]["cited_by"] == [
            {"arxiv_id": "2608.00001", "title": "Seed"}
        ]
        assert body["seeds_read"] == [{"arxiv_id": "2608.00001", "title": "Seed"}]
        assert body["pending_seeds"] is False

    def test_settings_reach_core(self, env):
        from core.config import get_settings

        settings = get_settings()
        _foundation(env)
        domain_key, kwargs = env["foundation_calls"][-1]
        assert domain_key == "astrophysics"
        assert kwargs == {
            "min_citing_seeds": settings.discovery_foundation_min_citing_seeds,
            "fetch_per_call": settings.discovery_foundation_fetch_per_call,
            "ttl_days": settings.discovery_reference_cache_ttl_days,
        }

    def test_reference_cache_upsert_is_committed(self, env):
        _foundation(env)
        assert env["session"].commits == 1
        assert env["session"].rollbacks == 0

    def test_disabled_optin_is_200_not_error(self, env):
        routes = env["routes"]
        env["foundation_result"] = routes.pd_foundation._disabled_result("astrophysics")
        res = _foundation(env)
        assert res.status_code == 200
        body = res.json()
        assert body["enabled"] is False
        assert body["available"] is False
        assert body["note"] == routes.pd_foundation.NOTE_DISABLED
        assert body["candidates"] == []

    def test_pending_seeds_is_passed_through(self, env):
        routes = env["routes"]
        env["foundation_result"] = {
            "enabled": True,
            "available": True,
            "domain_key": "astrophysics",
            "candidates": [],
            "seeds_read": [{"arxiv_id": "2608.00001", "title": "Seed"}],
            "pending_seeds": True,
            "partial": True,
            "note": routes.pd_foundation.NOTE_PENDING_SEEDS,
            "closed_world_note": routes.pd_foundation.CLOSED_WORLD_NOTE,
        }
        body = _foundation(env).json()
        assert body["pending_seeds"] is True
        assert body["partial"] is True
        assert body["note"] == routes.pd_foundation.NOTE_PENDING_SEEDS

    def test_citation_api_error_is_502_with_fixed_detail(self, env):
        routes = env["routes"]
        env["foundation_error"] = routes.pd_citation_client.CitationApiError(
            "references unreachable"
        )
        res = _foundation(env)
        assert res.status_code == 502
        assert res.json()["detail"] == routes._DETAIL_CITATION_UNAVAILABLE
        assert "references unreachable" not in res.json()["detail"]
        assert env["session"].rollbacks == 1
        assert env["session"].commits == 0

    def test_unexpected_error_rolls_back(self, env):
        env["foundation_error"] = RuntimeError("boom")
        with pytest.raises(RuntimeError):
            _foundation(env)
        assert env["session"].rollbacks == 1
        assert env["session"].closed == 1
