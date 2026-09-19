"""論文ディスカバリー Phase 3 — 引用グラフ拡張口
（``core/paper_discovery/citation_client.py`` / ``citation_search.py`` +
``POST /api/admin/discovery/citation-search``）。

正本: ``docs/features/paper_discovery_design.md`` §6（Phase 3）/ §2
（PD1 取り込みは教員の明示承認のみ・PD2 取得は既存経路のみ・PD4 数値スコアなし・
PD5 候補を保存しない・PD6 閉世界・PD7 外部 API の行儀）。

検証観点:

1. client: 宛先固定・スロットル・タイムアウト・``externalIds.ArXiv`` フィルタ
2. オプトイン（``DISCOVERY_CITATION_SOURCE_ENABLED``）が off なら外部 API を呼ばず
   ``enabled: False`` + 事実文（403/404 にしない = 機能の存在は隠さない）
3. シードゼロは ``available: False`` + 事実文（空一覧を「該当なし」と偽らない）
4. 候補の ``derived_from``（出所の明示）と Phase 1 と同じ注釈（ingested/dismissed/new）
5. **取り込み経路を持たない**（PD1）— 候補提示のみ
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _path in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, str(_path))

from core.paper_discovery import citation_client, citation_search  # noqa: E402
from core.paper_discovery.schema import CitationEntry  # noqa: E402

CORE_DIR = BACKEND / "core" / "paper_discovery"
ROUTE_SOURCE = BACKEND / "api" / "routes" / "paper_discovery.py"

#: オプトイン判定の**素の**実装（autouse fixture が差し替える前に捕まえておく）。
_REAL_CITATION_SOURCE_ENABLED = citation_search.citation_source_enabled


# ---------------------------------------------------------------------------
# フェイク
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def fetchall(self):
        return list(self._rows)


class FakeSession:
    """``learning_courses`` / ``documents`` / ``paper_discovery_dismissals`` の最小実装。"""

    def __init__(self, *, documents=(), dismissed=()):
        self.documents = list(documents)
        self.dismissed = set(dismissed)
        self.calls: list[str] = []

    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        p = dict(params or {})
        self.calls.append(sql)
        if "FROM learning_courses" in sql:
            return _Result([({"cartridge_id": p.get("domain_key"),
                              "sources": [{"material_id": "m1"}]},)])
        if "paper_discovery_dismissals" in sql:
            return _Result([(a,) for a in sorted(self.dismissed)])
        if "FROM documents" in sql and "COALESCE(title" in sql:
            # corpus.domain_document_rows: (id, title, source_url)
            return _Result(
                [
                    (d["id"], d.get("title") or "", d.get("source_url") or "")
                    for d in self.documents
                ]
            )
        if "FROM documents" in sql:
            # search.ingested_arxiv_ids: (source_url,)
            return _Result([(d.get("source_url") or "",) for d in self.documents])
        return _Result()

    def commit(self):  # pragma: no cover
        raise AssertionError("core must not commit")

    def close(self):
        pass


def _session(*, papers=(), dismissed=()):
    documents = [
        {
            "id": f"doc-{index}",
            "source_path": "m1",
            "title": title,
            "source_url": f"https://arxiv.org/pdf/{arxiv_id}",
        }
        for index, (arxiv_id, title) in enumerate(papers)
    ]
    return FakeSession(documents=documents, dismissed=dismissed)


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    """既定 off なので、明示的に有効化したテスト以外は disabled 経路になる。"""
    monkeypatch.setattr(citation_search, "citation_source_enabled", lambda: True)
    citation_client.reset_throttle()
    yield
    citation_client.reset_throttle()


def _stub_recommendations(monkeypatch, mapping):
    """``recommendations_for_arxiv`` を差し替える（値 or 例外）。"""
    calls: list[tuple[str, int]] = []

    def _fake(arxiv_id, *, limit=20, timeout=30.0):
        calls.append((arxiv_id, limit))
        value = mapping.get(arxiv_id, [])
        if isinstance(value, Exception):
            raise value
        return list(value)

    monkeypatch.setattr(citation_client, "recommendations_for_arxiv", _fake)
    return calls


def _entry(arxiv_id, seed, title="rec"):
    return CitationEntry(arxiv_id=arxiv_id, title=title, summary="s", seed_arxiv_id=seed)


# ---------------------------------------------------------------------------
# 1. client（PD7 の行儀）
# ---------------------------------------------------------------------------


class TestCitationClient:
    def test_endpoint_is_the_recommendations_api_on_the_fixed_host(self):
        url = citation_client._api_url("2608.20293")
        assert url == (
            "https://api.semanticscholar.org/recommendations/v1/papers/forpaper/arXiv:2608.20293"
        )

    def test_http_get_throttles_and_passes_a_timeout(self, monkeypatch):
        seen: dict = {}
        throttled: list[int] = []

        class _Response:
            status_code = 200

            @staticmethod
            def json():
                return {"recommendedPapers": []}

        def _get(url, params=None, timeout=None):
            seen["url"] = url
            seen["params"] = dict(params or {})
            seen["timeout"] = timeout
            return _Response()

        monkeypatch.setattr(citation_client.requests, "get", _get)
        monkeypatch.setattr(citation_client, "_throttle", lambda: throttled.append(1))

        citation_client.recommendations_for_arxiv("arXiv:2608.20293v2", limit=7)
        assert throttled == [1], "すべての HTTP 呼び出しがスロットルを通る"
        assert seen["url"].startswith("https://api.semanticscholar.org/")
        assert "arXiv:2608.20293" in seen["url"] and "v2" not in seen["url"]
        assert seen["params"] == {"fields": citation_client.FIELDS, "limit": 7}
        assert seen["timeout"] == citation_client.DEFAULT_TIMEOUT_SECONDS

    def test_throttle_waits_the_minimum_interval(self, monkeypatch):
        slept: list[float] = []
        clock = {"now": 100.0}
        monkeypatch.setattr(citation_client.time, "monotonic", lambda: clock["now"])
        monkeypatch.setattr(citation_client.time, "sleep", lambda s: slept.append(s))

        citation_client.reset_throttle()
        citation_client._throttle()
        clock["now"] = 101.0
        citation_client._throttle()
        assert slept and slept[0] == pytest.approx(2.0)

    def test_limit_is_clamped(self, monkeypatch):
        seen: dict = {}

        class _Response:
            status_code = 200

            @staticmethod
            def json():
                return {"recommendedPapers": []}

        monkeypatch.setattr(citation_client, "_throttle", lambda: None)
        monkeypatch.setattr(
            citation_client.requests, "get",
            lambda url, params=None, timeout=None: (seen.update(params or {}), _Response())[1],
        )
        citation_client.recommendations_for_arxiv("2608.20293", limit=10_000)
        assert seen["limit"] == citation_client.MAX_LIMIT

    def test_only_arxiv_backed_papers_survive(self):
        payload = {
            "recommendedPapers": [
                {
                    "title": "  A   Paper ",
                    "abstract": "abs",
                    "year": "2026",
                    "authors": [{"name": "Doe, J"}, {"name": ""}],
                    "externalIds": {"ArXiv": "https://arxiv.org/abs/2608.20293v3"},
                },
                {"title": "doi only", "externalIds": {"DOI": "10.1/x"}},
                {"title": "no external ids"},
                "garbage",
            ]
        }
        entries = citation_client.parse_recommendations(payload, "2601.00001")
        assert len(entries) == 1
        entry = entries[0]
        assert entry.arxiv_id == "2608.20293"
        assert entry.title == "A Paper"
        assert entry.authors == ["Doe, J"]
        assert entry.year == 2026
        assert entry.seed_arxiv_id == "2601.00001"
        payload_dict = entry.to_dict()
        assert payload_dict["pdf_url"] == "https://arxiv.org/pdf/2608.20293"
        assert not any(
            key in payload_dict for key in ("score", "similarity", "relevance", "rank")
        )

    def test_malformed_payload_degrades_to_empty(self):
        assert citation_client.parse_recommendations({}, "s") == []
        assert citation_client.parse_recommendations({"recommendedPapers": None}, "s") == []

    def test_transport_failures_raise_the_layer_error(self, monkeypatch):
        monkeypatch.setattr(citation_client, "_throttle", lambda: None)

        class _Boom:
            status_code = 500

            @staticmethod
            def json():
                return {}

        monkeypatch.setattr(
            citation_client.requests, "get", lambda *a, **k: _Boom()
        )
        with pytest.raises(citation_client.CitationApiError):
            citation_client.recommendations_for_arxiv("2608.20293")

    def test_unparsable_id_raises_before_any_request(self, monkeypatch):
        called: list[int] = []
        monkeypatch.setattr(
            citation_client.requests, "get",
            lambda *a, **k: called.append(1),
        )
        with pytest.raises(citation_client.CitationApiError):
            citation_client.recommendations_for_arxiv("not-an-id")
        assert called == []

    def test_error_messages_do_not_leak_internals(self, monkeypatch):
        monkeypatch.setattr(citation_client, "_throttle", lambda: None)

        def _boom(*a, **k):
            raise citation_client.requests.RequestException("connect to 10.0.0.1 failed")

        monkeypatch.setattr(citation_client.requests, "get", _boom)
        with pytest.raises(citation_client.CitationApiError) as excinfo:
            citation_client.recommendations_for_arxiv("2608.20293")
        assert "10.0.0.1" not in str(excinfo.value)


# ---------------------------------------------------------------------------
# 2. オプトイン
# ---------------------------------------------------------------------------


class TestOptIn:
    def test_disabled_returns_a_factual_note_without_calling_the_api(self, monkeypatch):
        monkeypatch.setattr(citation_search, "citation_source_enabled", lambda: False)
        calls = _stub_recommendations(monkeypatch, {})
        session = _session(papers=[("2608.00001", "seed")])

        result = citation_search.run_citation_search(session, "astrophysics")
        assert result["enabled"] is False
        assert result["available"] is False
        assert result["note"] == citation_search.NOTE_DISABLED
        assert "DISCOVERY_CITATION_SOURCE_ENABLED" in result["note"]
        assert result["candidates"] == [] and result["seeds"] == []
        assert calls == [], "無効時は外部 API を呼ばない（ゲートは core 側）"

    def test_setting_default_is_off(self, monkeypatch):
        from core.config import Settings

        assert Settings().discovery_citation_source_enabled is False

    def test_reader_fails_closed(self, monkeypatch):
        import core.config as config_module

        monkeypatch.setattr(
            config_module, "get_settings",
            lambda: (_ for _ in ()).throw(RuntimeError("no settings")),
        )
        assert _REAL_CITATION_SOURCE_ENABLED() is False


# ---------------------------------------------------------------------------
# 3. シードと候補の導出
# ---------------------------------------------------------------------------


class TestRunCitationSearch:
    def test_no_seeds_is_available_false_with_a_factual_note(self, monkeypatch):
        calls = _stub_recommendations(monkeypatch, {})
        result = citation_search.run_citation_search(FakeSession(), "astrophysics")
        assert result["enabled"] is True
        assert result["available"] is False
        assert result["note"] == citation_search.NOTE_NO_SEEDS
        assert result["candidates"] == []
        assert calls == []

    def test_manual_uploads_are_not_seeds(self, monkeypatch):
        """``source_url`` の無い document（手動アップロード）は起点にできない。"""
        calls = _stub_recommendations(monkeypatch, {})
        session = FakeSession(documents=[{"id": "doc-1", "title": "manual", "source_url": ""}])
        result = citation_search.run_citation_search(session, "astrophysics")
        assert result["available"] is False
        assert calls == []

    def test_candidates_carry_their_origin_and_status(self, monkeypatch):
        calls = _stub_recommendations(
            monkeypatch,
            {
                "2608.00001": [
                    _entry("2608.10001", "2608.00001"),
                    _entry("2608.10002", "2608.00001"),
                ],
                "2608.00002": [
                    _entry("2608.10002", "2608.00002"),  # 2シードから辿れる
                    _entry("2608.00001", "2608.00002"),  # シード自身は候補にしない
                ],
            },
        )
        session = _session(
            papers=[("2608.00001", "Seed A"), ("2608.00002", "Seed B")],
            dismissed={"2608.10001"},
        )

        result = citation_search.run_citation_search(session, "astrophysics")

        assert result["available"] is True
        assert result["enabled"] is True
        assert result["closed_world_note"] == (
            "この一覧は取り込み済み論文の引用・推薦関係から導出した範囲のみを示します。"
        )
        assert result["seeds"] == [
            {"arxiv_id": "2608.00001", "title": "Seed A"},
            {"arxiv_id": "2608.00002", "title": "Seed B"},
        ]
        by_id = {c["arxiv_id"]: c for c in result["candidates"]}
        assert set(by_id) == {"2608.10001", "2608.10002"}
        assert by_id["2608.10001"]["status"] == "dismissed"
        assert by_id["2608.10002"]["status"] == "new"
        assert by_id["2608.10001"]["derived_from"] == [
            {"arxiv_id": "2608.00001", "title": "Seed A"}
        ]
        assert by_id["2608.10002"]["derived_from"] == [
            {"arxiv_id": "2608.00001", "title": "Seed A"},
            {"arxiv_id": "2608.00002", "title": "Seed B"},
        ]
        assert [c[0] for c in calls] == ["2608.00001", "2608.00002"]

    def test_already_ingested_candidates_are_labelled(self, monkeypatch):
        _stub_recommendations(
            monkeypatch, {"2608.00001": [_entry("2608.20293", "2608.00001")]}
        )
        session = _session(papers=[("2608.00001", "Seed"), ("2608.20293", "Already")])
        # 2本目の取り込み済み論文もシードになるので推薦は空を返す（mapping 既定）。
        result = citation_search.run_citation_search(session, "astrophysics")
        assert result["candidates"] == [], "シード自身は候補に出さない"

        session2 = _session(papers=[("2608.00001", "Seed")])
        session2.documents.append(
            {"id": "doc-9", "source_path": "m1", "title": "other",
             "source_url": "https://arxiv.org/abs/2608.20293v1"}
        )
        # documents に居るが seeds は max_seeds で切れる想定 → status=ingested を確認
        result2 = citation_search.run_citation_search(session2, "astrophysics", max_seeds=1)
        assert [c["status"] for c in result2["candidates"]] == ["ingested"]

    def test_seed_and_limit_bounds_are_respected(self, monkeypatch):
        calls = _stub_recommendations(monkeypatch, {})
        session = _session(papers=[(f"2608.0000{i}", f"S{i}") for i in range(1, 6)])
        citation_search.run_citation_search(
            session, "astrophysics", max_seeds=2, limit_per_seed=3
        )
        assert len(calls) == 2
        assert {limit for _id, limit in calls} == {3}

    def test_partial_failure_keeps_what_was_reachable(self, monkeypatch):
        _stub_recommendations(
            monkeypatch,
            {
                "2608.00001": citation_client.CitationApiError("down"),
                "2608.00002": [_entry("2608.10003", "2608.00002")],
            },
        )
        session = _session(papers=[("2608.00001", "A"), ("2608.00002", "B")])
        result = citation_search.run_citation_search(session, "astrophysics")
        assert result["available"] is True
        assert result["partial"] is True
        assert [c["arxiv_id"] for c in result["candidates"]] == ["2608.10003"]

    def test_total_failure_raises_instead_of_faking_an_empty_list(self, monkeypatch):
        _stub_recommendations(
            monkeypatch, {"2608.00001": citation_client.CitationApiError("down")}
        )
        session = _session(papers=[("2608.00001", "A")])
        with pytest.raises(citation_client.CitationApiError):
            citation_search.run_citation_search(session, "astrophysics")

    def test_no_numeric_scores_in_the_dto(self, monkeypatch):
        _stub_recommendations(
            monkeypatch, {"2608.00001": [_entry("2608.10001", "2608.00001")]}
        )
        session = _session(papers=[("2608.00001", "A")])
        result = citation_search.run_citation_search(session, "astrophysics")
        for candidate in result["candidates"]:
            assert not any(
                key in candidate
                for key in ("score", "similarity", "relevance", "confidence", "rank")
            )


# ---------------------------------------------------------------------------
# 4. PD1: 候補提示のみ（取り込み経路を持たない）
# ---------------------------------------------------------------------------


class TestNoIngestFromCitationSearch:
    def test_core_module_has_no_fetch_or_accept(self):
        src = (CORE_DIR / "citation_search.py").read_text(encoding="utf-8")
        for banned in ("fetch_source_from_url", "_accept_material_source", "requests."):
            assert banned not in src

    def test_route_only_reads(self):
        src = ROUTE_SOURCE.read_text(encoding="utf-8")
        start = src.index("def citation_search_candidates")
        body = src[start : src.index("\ndef ", start + 1)]
        for banned in (
            "_accept_material_source(",
            "fetch_source_from_url(",
            "enqueue_items(",
            "record_review_event(",  # 読み取りのみ = 監査記帳しない（/search と同じ扱い）
            "session.commit()",
        ):
            assert banned not in body, banned


# ---------------------------------------------------------------------------
# 5. API
# ---------------------------------------------------------------------------


try:
    import fastapi  # noqa: F401
    _HAS_FASTAPI = True
except ImportError:  # pragma: no cover
    _HAS_FASTAPI = False


@pytest.fixture
def api(monkeypatch):
    from fastapi.testclient import TestClient
    from api.main import app
    from dependencies import ROLE_STUDENT, ROLE_TEACHER, _create_token
    import routes.paper_discovery as routes

    state: dict = {"result": None, "error": None, "calls": [], "closed": 0}

    class _Session:
        def execute(self, *a, **k):
            return _Result()

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            state["closed"] += 1

    monkeypatch.setattr(routes, "_pg_session", lambda: _Session())

    def _run(session, domain_key, **kwargs):
        state["calls"].append((domain_key, kwargs))
        if state["error"] is not None:
            raise state["error"]
        return state["result"] or {
            "enabled": True,
            "available": True,
            "domain_key": domain_key,
            "candidates": [
                {
                    "arxiv_id": "2608.10001",
                    "title": "rec",
                    "status": "new",
                    "derived_from": [{"arxiv_id": "2608.00001", "title": "Seed"}],
                }
            ],
            "seeds": [{"arxiv_id": "2608.00001", "title": "Seed"}],
            "closed_world_note": routes.pd_citation_search.CLOSED_WORLD_NOTE,
        }

    monkeypatch.setattr(routes.pd_citation_search, "run_citation_search", _run)

    state["client"] = TestClient(app)
    state["headers"] = {
        "teacher": {
            "Authorization": "Bearer "
            + _create_token("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "k", "k@x", ROLE_TEACHER)
        },
        "student": {
            "Authorization": "Bearer "
            + _create_token("cccccccc-cccc-cccc-cccc-cccccccccccc", "g", "g@x", ROLE_STUDENT)
        },
    }
    return state


@pytest.mark.skipif(not _HAS_FASTAPI, reason="FastAPI not installed")
class TestCitationSearchApi:
    _PATH = "/api/admin/discovery/citation-search"

    def test_requires_teacher(self, api):
        assert api["client"].post(self._PATH, json={"domain_key": "x"}).status_code in (401, 403)
        res = api["client"].post(
            self._PATH, json={"domain_key": "x"}, headers=api["headers"]["student"]
        )
        assert res.status_code == 403

    def test_returns_candidates_with_origin(self, api):
        res = api["client"].post(
            self._PATH, json={"domain_key": "astrophysics"},
            headers=api["headers"]["teacher"],
        )
        assert res.status_code == 200
        body = res.json()
        assert body["enabled"] is True and body["available"] is True
        assert body["candidates"][0]["derived_from"][0]["arxiv_id"] == "2608.00001"
        assert body["closed_world_note"]
        assert api["closed"] == 1

    def test_disabled_is_200_not_403(self, api):
        api["result"] = {
            "enabled": False,
            "available": False,
            "note": citation_search.NOTE_DISABLED,
            "candidates": [],
            "seeds": [],
        }
        res = api["client"].post(
            self._PATH, json={"domain_key": "astrophysics"},
            headers=api["headers"]["teacher"],
        )
        assert res.status_code == 200
        assert res.json()["enabled"] is False
        assert "DISCOVERY_CITATION_SOURCE_ENABLED" in res.json()["note"]

    def test_api_failure_is_502_with_a_fixed_factual_detail(self, api):
        api["error"] = citation_client.CitationApiError("connect to 10.0.0.1 failed")
        res = api["client"].post(
            self._PATH, json={"domain_key": "astrophysics"},
            headers=api["headers"]["teacher"],
        )
        assert res.status_code == 502
        detail = res.json()["detail"]
        assert detail == (
            "引用グラフの照会に接続できませんでした。時間をおいて再度お試しください。"
        )
        assert "10.0.0.1" not in detail
        assert api["closed"] == 1

    def test_subscriptions_expose_the_opt_in_flag(self, api, monkeypatch):
        import routes.paper_discovery as routes

        monkeypatch.setattr(routes.pd_store, "list_subscriptions", lambda session: [])
        monkeypatch.setattr(
            routes.pd_citation_search, "citation_source_enabled", lambda: True
        )
        res = api["client"].get(
            "/api/admin/discovery/subscriptions", headers=api["headers"]["teacher"]
        )
        assert res.status_code == 200
        assert res.json() == {"subscriptions": [], "citation_source_enabled": True}
