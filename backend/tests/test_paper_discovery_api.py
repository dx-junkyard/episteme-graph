"""論文ディスカバリー層 — 管理 API（``api/routes/paper_discovery.py``）。

対象エンドポイント（全て TEACHER 以上）:
  - ``GET  /api/admin/discovery/subscriptions``
  - ``PUT  /api/admin/discovery/subscriptions/{domain_key}``
  - ``GET  /api/admin/discovery/subscriptions/{domain_key}/keyphrase-candidates``
  - ``POST /api/admin/discovery/search``
  - ``POST /api/admin/discovery/ingest``
  - ``POST /api/admin/discovery/dismiss`` / ``/restore``

``tests/test_url_fetch_api.py`` の流儀（実 app + ``TestClient`` + ``_pg_session`` の
フェイク差し替え）を踏襲する。DB・MinIO・arXiv・ネットワークには接続しない。

検証観点（設計書 §2 の PD1〜PD8 / §4.3 / §4.5）:
  1. 権限の fail-closed（STUDENT は全て 403）
  2. 購読の保存が core の store を通り、監査が記帳される（PD3 / §4.5）
  3. 検索レスポンスの素通し（PD4/PD6）と ``max_results`` のサーバ側クランプ
  4. 取り込みの件数上限・空・許可リスト未設定（PD1）と**部分成功**の扱い（PD2）
  5. ``documents.source_url`` の永続化（PD5 の読み時導出の材料）
  6. 見送り / 復帰の遷移・404・監査
  7. 構造的な検査: ルーターの登録 / ingest が url_fetch を経由し独自 HTTP を持たない
"""

from __future__ import annotations

import re
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

ROUTE_SOURCE = BACKEND / "api" / "routes" / "paper_discovery.py"
MAIN_SOURCE = BACKEND / "api" / "main.py"
ADMIN_SOURCE = BACKEND / "api" / "routes" / "admin.py"

_ADMIN = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_TEACHER = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
_STUDENT = "cccccccc-cccc-cccc-cccc-cccccccccccc"

PDF_BYTES = b"%PDF-1.7\nhello"


# ---------------------------------------------------------------------------
# フェイクセッション
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _Stamp:
    def isoformat(self):
        return "2026-08-27T00:00:00+00:00"


class FakeSession:
    """``url_fetch`` の許可リスト SELECT だけ実データを返す最小セッション。"""

    def __init__(self, domains=()):
        self.domains = list(domains)
        self.calls: list[tuple[str, dict]] = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = 0

    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        self.calls.append((sql, dict(params or {})))
        if sql.startswith("SELECT domain, created_at FROM url_fetch_domains"):
            return _Result([(d, _Stamp()) for d in sorted(self.domains)])
        return _Result()

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed += 1

    def sqls(self):
        return [sql for sql, _p in self.calls]


@pytest.fixture
def env(monkeypatch):
    """TestClient + フェイクセッション + store / vocab / search / 取得 / 受理の記録。"""
    from fastapi.testclient import TestClient
    from api.main import app
    from dependencies import ROLE_STUDENT, ROLE_SYSTEM_ADMIN, ROLE_TEACHER, _create_token
    import routes.paper_discovery as routes

    state: dict = {
        "session": FakeSession(),
        "audits": [],
        "accepted": [],
        "fetches": [],
        "fetch_result": None,
        "fetch_errors": {},          # arxiv_id -> 例外（無ければ成功）
        "fetch_error": None,         # 全 item 共通の例外
        "subscriptions": [],
        "subscription": None,
        "upserts": [],
        "candidates": [],
        "search_calls": [],
        "search_result": None,
        "search_error": None,
        "dismiss_calls": [],
        "restore_calls": [],
        "restore_result": {"domain_key": "astrophysics", "arxiv_id": "2608.20293", "revoked": True},
    }

    monkeypatch.setattr(routes, "_pg_session", lambda: state["session"])
    monkeypatch.setattr(
        routes, "record_review_event",
        lambda *args: state["audits"].append(args),
    )

    # ── core.paper_discovery の差し替え（DB へ行かない） ──────────────────
    monkeypatch.setattr(
        routes.pd_store, "list_subscriptions", lambda session: list(state["subscriptions"])
    )
    monkeypatch.setattr(
        routes.pd_store, "get_subscription", lambda session, key: state["subscription"]
    )

    def _upsert(session, domain_key, **kwargs):
        if not str(domain_key or "").strip():
            raise ValueError("domain_key must not be empty")
        state["upserts"].append((domain_key, kwargs))
        return {
            "domain_key": domain_key,
            "arxiv_categories": list(kwargs.get("arxiv_categories") or []),
            "keyphrases": [
                {"text": p, "source": "manual", "enabled": True} if isinstance(p, str) else p
                for p in (kwargs.get("keyphrases") or [])
            ],
            "followed_authors": list(kwargs.get("followed_authors") or []),
            "updated_by": str(kwargs.get("updated_by") or ""),
            "updated_at": "2026-08-27T00:00:00+00:00",
            "last_checked_at": "",
        }

    monkeypatch.setattr(routes.pd_store, "upsert_subscription", _upsert)

    def _dismiss(session, domain_key, arxiv_id, user_id=None):
        normalized = routes.pd_schema.normalize_arxiv_id(arxiv_id)
        if not normalized:
            raise ValueError(f"invalid arXiv id: {arxiv_id!r}")
        state["dismiss_calls"].append((domain_key, normalized, user_id))
        return {"domain_key": domain_key, "arxiv_id": normalized, "revoked": False}

    def _restore(session, domain_key, arxiv_id, user_id=None):
        normalized = routes.pd_schema.normalize_arxiv_id(arxiv_id)
        if not normalized:
            raise ValueError(f"invalid arXiv id: {arxiv_id!r}")
        state["restore_calls"].append((domain_key, normalized, user_id))
        return state["restore_result"]

    monkeypatch.setattr(routes.pd_store, "dismiss", _dismiss)
    monkeypatch.setattr(routes.pd_store, "restore", _restore)
    monkeypatch.setattr(
        routes.pd_vocab, "keyphrase_candidates",
        lambda session, domain_key, **kw: list(state["candidates"]),
    )

    def _run_search(session, domain_key, **kwargs):
        state["search_calls"].append((domain_key, kwargs))
        if state["search_error"] is not None:
            raise state["search_error"]
        return state["search_result"] or {
            "domain_key": domain_key,
            "query": '(cat:astro-ph.CO) AND (all:"dark energy")',
            "total": 2,
            "start": kwargs.get("start", 0),
            "candidates": [],
            "closed_world_note": routes.pd_search.CLOSED_WORLD_NOTE,
        }

    monkeypatch.setattr(routes.pd_search, "run_search", _run_search)

    # ── 取得（PD2: 既存 url_fetch へ委譲） ────────────────────────────────
    def _fetch(url, allowed_domains):
        state["fetches"].append((url, list(allowed_domains)))
        if state["fetch_error"] is not None:
            raise state["fetch_error"]
        for token, exc in state["fetch_errors"].items():
            if token in url:
                raise exc
        return state["fetch_result"] or routes.url_fetch.FetchedSource(
            content=PDF_BYTES, source_kind="pdf", filename="2608.20293.pdf",
        )

    monkeypatch.setattr(routes.url_fetch, "fetch_source_from_url", _fetch)

    def _accept(**kwargs):
        state["accepted"].append(kwargs)
        return {
            "task_id": "task-1",
            "material_id": "mat-1",
            "filename": kwargs["filename"],
            "title": "2608.20293",
            "source_kind": kwargs["source_kind"],
            "status": "pending",
            "uploaded_at": "2026-08-27T00:00:00",
            "analyze_images": bool(kwargs["analyze_images"]),
        }

    monkeypatch.setattr(routes, "_accept_material_source", _accept)

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


def _items(*ids):
    return [{"arxiv_id": i} for i in ids]


# ---------------------------------------------------------------------------
# 1. 権限の fail-closed（PD: 全エンドポイント TEACHER 以上）
# ---------------------------------------------------------------------------


_ALL_ENDPOINTS = [
    ("get", "/api/admin/discovery/subscriptions", None),
    ("put", "/api/admin/discovery/subscriptions/astrophysics", {"arxiv_categories": []}),
    ("get", "/api/admin/discovery/subscriptions/astrophysics/keyphrase-candidates", None),
    ("post", "/api/admin/discovery/search", {"domain_key": "astrophysics"}),
    ("post", "/api/admin/discovery/ingest", {"items": [{"arxiv_id": "2608.20293"}]}),
    ("post", "/api/admin/discovery/dismiss", {"domain_key": "a", "arxiv_id": "2608.20293"}),
    ("post", "/api/admin/discovery/restore", {"domain_key": "a", "arxiv_id": "2608.20293"}),
]


class TestPermissions:
    @pytest.mark.parametrize("method,path,body", _ALL_ENDPOINTS)
    def test_student_is_forbidden(self, env, method, path, body):
        call = getattr(env["client"], method)
        res = call(path, json=body, headers=_auth(env, "student")) if body is not None \
            else call(path, headers=_auth(env, "student"))
        assert res.status_code == 403
        assert env["fetches"] == []
        assert env["accepted"] == []
        assert env["audits"] == []

    @pytest.mark.parametrize("method,path,body", _ALL_ENDPOINTS)
    def test_anonymous_is_rejected(self, env, method, path, body):
        call = getattr(env["client"], method)
        res = call(path, json=body) if body is not None else call(path)
        assert res.status_code in (401, 403)

    def test_teacher_can_read_subscriptions(self, env):
        env["subscriptions"] = [{"domain_key": "astrophysics", "arxiv_categories": ["astro-ph.CO"]}]
        res = env["client"].get(
            "/api/admin/discovery/subscriptions", headers=_auth(env, "teacher")
        )
        assert res.status_code == 200
        assert res.json() == {"subscriptions": env["subscriptions"]}

    def test_system_admin_can_read_subscriptions(self, env):
        res = env["client"].get(
            "/api/admin/discovery/subscriptions", headers=_auth(env, "admin")
        )
        assert res.status_code == 200

    def test_session_is_closed(self, env):
        env["client"].get("/api/admin/discovery/subscriptions", headers=_auth(env, "teacher"))
        assert env["session"].closed == 1


# ---------------------------------------------------------------------------
# 2. PUT /subscriptions/{domain_key}
# ---------------------------------------------------------------------------


class TestUpsertSubscription:
    PATH = "/api/admin/discovery/subscriptions/astrophysics"

    def test_store_is_called_with_body(self, env):
        res = env["client"].put(
            self.PATH,
            json={
                "arxiv_categories": ["astro-ph.CO"],
                "keyphrases": [{"text": "dark energy", "source": "skeleton", "enabled": True}],
                "followed_authors": ["Doe, J"],
            },
            headers=_auth(env, "teacher"),
        )
        assert res.status_code == 200
        assert list(res.json()) == ["subscription"]
        assert res.json()["subscription"]["domain_key"] == "astrophysics"

        assert len(env["upserts"]) == 1
        domain_key, kwargs = env["upserts"][0]
        assert domain_key == "astrophysics"
        assert kwargs["arxiv_categories"] == ["astro-ph.CO"]
        assert kwargs["keyphrases"][0]["text"] == "dark energy"
        assert kwargs["followed_authors"] == ["Doe, J"]
        # 保存の記録者は認証ユーザー（クライアント指定を受けない）
        assert str(kwargs["updated_by"]) == _TEACHER
        assert env["session"].commits == 1

    def test_audit_is_recorded(self, env):
        env["client"].put(
            self.PATH,
            json={"arxiv_categories": ["astro-ph.CO"], "keyphrases": ["dark energy"]},
            headers=_auth(env, "teacher"),
        )
        assert len(env["audits"]) == 1
        entity_type, entity_id, old, new, user_id, metadata = env["audits"][0]
        assert entity_type == "paper_discovery"
        assert entity_id == "astrophysics"
        assert new == "subscribed"
        assert str(user_id) == _TEACHER
        assert metadata["action"] == "subscribe"
        assert metadata["keyphrases"] == ["dark energy"]

    def test_audit_marks_existing_subscription(self, env):
        env["subscription"] = {"domain_key": "astrophysics"}
        env["client"].put(
            self.PATH, json={"arxiv_categories": []}, headers=_auth(env, "teacher"),
        )
        _t, _id, old, new, _u, _m = env["audits"][0]
        assert (old, new) == ("subscribed", "subscribed")

    def test_empty_domain_key_is_422_without_audit(self, env):
        # 分野キーが空（末尾スラッシュ）はルート不一致 or 422。いずれにせよ保存しない。
        res = env["client"].put(
            "/api/admin/discovery/subscriptions/%20",
            json={"arxiv_categories": []},
            headers=_auth(env, "teacher"),
        )
        assert res.status_code == 422
        assert env["upserts"] == []
        assert env["audits"] == []
        assert env["session"].rollbacks == 1


# ---------------------------------------------------------------------------
# 3. GET /subscriptions/{domain_key}/keyphrase-candidates
# ---------------------------------------------------------------------------


class TestKeyphraseCandidates:
    PATH = "/api/admin/discovery/subscriptions/astrophysics/keyphrase-candidates"

    def test_returns_candidates_with_source(self, env):
        env["candidates"] = [
            {"text": "dark energy", "source": "skeleton"},
            {"text": "w0waCDM", "source": "cartridge"},
        ]
        res = env["client"].get(self.PATH, headers=_auth(env, "teacher"))
        assert res.status_code == 200
        assert res.json() == {"candidates": env["candidates"]}

    def test_does_not_write_the_subscription(self, env):
        env["client"].get(self.PATH, headers=_auth(env, "teacher"))
        # PD3: 候補の提示は購読条件を書き換えない
        assert env["upserts"] == []
        assert env["audits"] == []
        assert env["session"].commits == 0


# ---------------------------------------------------------------------------
# 4. POST /search
# ---------------------------------------------------------------------------


class TestSearch:
    PATH = "/api/admin/discovery/search"

    def test_response_is_passed_through(self, env):
        env["search_result"] = {
            "domain_key": "astrophysics",
            "query": '(cat:astro-ph.CO)',
            "total": 7,
            "start": 0,
            "candidates": [{"arxiv_id": "2608.20293", "title": "T", "status": "new"}],
            "closed_world_note": "この一覧は検索条件に一致した範囲のみを示します。",
        }
        res = env["client"].post(
            self.PATH, json={"domain_key": "astrophysics"}, headers=_auth(env, "teacher"),
        )
        assert res.status_code == 200
        assert res.json() == env["search_result"]

    def test_conditions_are_forwarded(self, env):
        env["client"].post(
            self.PATH,
            json={
                "domain_key": "astrophysics",
                "categories": ["astro-ph.CO"],
                "keyphrases": ["dark energy"],
                "followed_authors": ["Doe, J"],
                "start": 25,
            },
            headers=_auth(env, "teacher"),
        )
        domain_key, kwargs = env["search_calls"][0]
        assert domain_key == "astrophysics"
        assert kwargs["categories"] == ["astro-ph.CO"]
        assert kwargs["keyphrases"] == ["dark energy"]
        assert kwargs["followed_authors"] == ["Doe, J"]
        assert kwargs["start"] == 25

    @pytest.mark.parametrize(
        "requested,expected", [(0, 1), (-5, 1), (1, 1), (50, 50), (100, 100), (5000, 100)]
    )
    def test_max_results_is_clamped(self, env, requested, expected):
        env["client"].post(
            self.PATH,
            json={"domain_key": "astrophysics", "max_results": requested},
            headers=_auth(env, "teacher"),
        )
        assert env["search_calls"][0][1]["max_results"] == expected

    def test_negative_start_is_clamped(self, env):
        env["client"].post(
            self.PATH, json={"domain_key": "astrophysics", "start": -3},
            headers=_auth(env, "teacher"),
        )
        assert env["search_calls"][0][1]["start"] == 0

    def test_arxiv_failure_is_502_with_a_fact_sentence(self, env):
        env["search_error"] = env["routes"].arxiv_client.ArxivApiError(
            "HTTP 503 from export.arxiv.org"
        )
        res = env["client"].post(
            self.PATH, json={"domain_key": "astrophysics"}, headers=_auth(env, "teacher"),
        )
        # PD6: 失敗を空一覧（=該当なし）に化けさせない
        assert res.status_code == 502
        detail = res.json()["detail"]
        assert "arXiv" in detail
        assert "503" not in detail and "export.arxiv.org" not in detail

    def test_invalid_condition_is_422(self, env):
        env["search_error"] = ValueError("bad condition")
        res = env["client"].post(
            self.PATH, json={"domain_key": "astrophysics"}, headers=_auth(env, "teacher"),
        )
        assert res.status_code == 422

    def test_search_does_not_record_audit(self, env):
        env["client"].post(
            self.PATH, json={"domain_key": "astrophysics"}, headers=_auth(env, "teacher"),
        )
        # 検索は状態変更ではない（副作用は last_checked_at のみ）
        assert env["audits"] == []
        assert env["session"].commits == 1


# ---------------------------------------------------------------------------
# 5. POST /ingest（PD1 / PD2 の要）
# ---------------------------------------------------------------------------


class TestIngest:
    PATH = "/api/admin/discovery/ingest"

    def test_over_the_limit_is_422(self, env):
        env["session"].domains = ["arxiv.org"]
        res = env["client"].post(
            self.PATH,
            json={"items": _items("2608.1", "2608.2", "2608.3", "2608.4", "2608.5", "2608.6")},
            headers=_auth(env, "teacher"),
        )
        assert res.status_code == 422
        assert "5件" in res.json()["detail"]
        assert env["fetches"] == []
        assert env["accepted"] == []

    def test_at_the_limit_is_accepted(self, env):
        env["session"].domains = ["arxiv.org"]
        res = env["client"].post(
            self.PATH,
            json={"items": _items("2608.20291", "2608.20292", "2608.20293",
                                  "2608.20294", "2608.20295")},
            headers=_auth(env, "teacher"),
        )
        assert res.status_code == 202
        assert len(res.json()["accepted"]) == 5

    def test_empty_items_is_422(self, env):
        res = env["client"].post(self.PATH, json={"items": []}, headers=_auth(env, "teacher"))
        assert res.status_code == 422
        assert env["fetches"] == []

    def test_missing_items_is_422(self, env):
        res = env["client"].post(self.PATH, json={}, headers=_auth(env, "teacher"))
        assert res.status_code == 422

    def test_no_allowed_domains_is_422_for_the_whole_request(self, env):
        env["session"].domains = []
        env["fetch_error"] = env["routes"].url_fetch.NoDomainsConfiguredError(
            "URLからの取得は、管理者が取得先ドメインを許可リストに登録すると利用できます"
        )
        res = env["client"].post(
            self.PATH, json={"items": _items("2608.20293")}, headers=_auth(env, "teacher"),
        )
        assert res.status_code == 422
        assert "許可リストに登録" in res.json()["detail"]
        assert env["accepted"] == []
        # 許可リストの判定は core が正本（ルートは空リストをそのまま渡す）
        assert env["fetches"] == [("https://arxiv.org/pdf/2608.20293", [])]

    def test_partial_failure_keeps_the_batch_alive(self, env):
        env["session"].domains = ["arxiv.org"]
        env["fetch_errors"] = {
            "2608.20294": env["routes"].url_fetch.DomainNotAllowedError(
                "このURLのドメインは許可されていません"
            )
        }
        res = env["client"].post(
            self.PATH, json={"items": _items("2608.20293", "2608.20294")},
            headers=_auth(env, "teacher"),
        )
        # 1件の失敗で HTTPException にしない
        assert res.status_code == 202
        body = res.json()
        assert len(body["accepted"]) == 1
        assert body["accepted"][0]["arxiv_id"] == "2608.20293"
        assert body["failed"] == [
            {"arxiv_id": "2608.20294", "detail": "このURLのドメインは許可されていません"}
        ]
        assert len(env["accepted"]) == 1

    def test_invalid_arxiv_id_becomes_a_failed_row(self, env):
        env["session"].domains = ["arxiv.org"]
        res = env["client"].post(
            self.PATH, json={"items": _items("これはIDではない", "2608.20293")},
            headers=_auth(env, "teacher"),
        )
        assert res.status_code == 202
        body = res.json()
        assert [f["arxiv_id"] for f in body["failed"]] == ["これはIDではない"]
        assert len(body["accepted"]) == 1
        # 不正 ID では取得を試みない
        assert env["fetches"] == [("https://arxiv.org/pdf/2608.20293", ["arxiv.org"])]

    def test_source_url_is_persisted(self, env):
        env["session"].domains = ["arxiv.org"]
        env["client"].post(
            self.PATH, json={"items": _items("arXiv:2608.20293v2")},
            headers=_auth(env, "teacher"),
        )
        accepted = env["accepted"][0]
        # PD5: 取り込み済み判定の材料。version は正規化で落ちる
        assert accepted["source_url"] == "https://arxiv.org/pdf/2608.20293"
        assert accepted["source_bytes"] == PDF_BYTES
        assert accepted["source_kind"] == "pdf"

    def test_response_is_upload_shaped_plus_arxiv_id(self, env):
        env["session"].domains = ["arxiv.org"]
        res = env["client"].post(
            self.PATH, json={"items": _items("2608.20293")}, headers=_auth(env, "teacher"),
        )
        item = res.json()["accepted"][0]
        assert set(item) == {
            "task_id", "material_id", "filename", "title", "source_kind",
            "status", "uploaded_at", "analyze_images", "arxiv_id",
        }
        assert item["arxiv_id"] == "2608.20293"

    def test_analyze_images_is_forwarded(self, env):
        env["session"].domains = ["arxiv.org"]
        env["client"].post(
            self.PATH, json={"items": _items("2608.20293"), "analyze_images": True},
            headers=_auth(env, "teacher"),
        )
        assert env["accepted"][0]["analyze_images"] is True

    def test_models_are_validated_by_the_existing_helper(self, env, monkeypatch):
        env["session"].domains = ["arxiv.org"]
        seen: list[dict] = []
        monkeypatch.setattr(
            env["routes"], "_validate_models_option",
            lambda models: seen.append(models) or {"pipeline": "checked"},
        )
        env["client"].post(
            self.PATH,
            json={"items": _items("2608.20293"), "models": {"pipeline": "gpt-x"}},
            headers=_auth(env, "teacher"),
        )
        assert seen == [{"pipeline": "gpt-x"}]
        assert env["accepted"][0]["models_option"] == {"pipeline": "checked"}

    def test_invalid_models_is_422_before_any_fetch(self, env):
        env["session"].domains = ["arxiv.org"]
        res = env["client"].post(
            self.PATH,
            json={"items": _items("2608.20293"), "models": {"nope": "gpt-x"}},
            headers=_auth(env, "teacher"),
        )
        assert res.status_code == 422
        assert env["fetches"] == []

    def test_allowlist_is_passed_to_core(self, env):
        env["session"].domains = ["arxiv.org", "example.com"]
        env["client"].post(
            self.PATH, json={"items": _items("2608.20293")}, headers=_auth(env, "teacher"),
        )
        assert env["fetches"] == [
            ("https://arxiv.org/pdf/2608.20293", ["arxiv.org", "example.com"])
        ]

    def test_audit_lists_the_targets(self, env):
        env["session"].domains = ["arxiv.org"]
        env["fetch_errors"] = {
            "2608.20294": env["routes"].url_fetch.FetchFailedError("URLからの取得に失敗しました")
        }
        env["client"].post(
            self.PATH,
            json={"items": _items("2608.20293", "2608.20294"), "domain_key": "astrophysics"},
            headers=_auth(env, "teacher"),
        )
        assert len(env["audits"]) == 1
        entity_type, entity_id, old, new, user_id, metadata = env["audits"][0]
        assert entity_type == "paper_discovery"
        assert entity_id == "astrophysics"
        assert (old, new) == ("candidate", "ingest_requested")
        assert str(user_id) == _TEACHER
        assert metadata["action"] == "ingest"
        assert metadata["arxiv_ids"] == ["2608.20293"]
        assert metadata["failed_arxiv_ids"] == ["2608.20294"]
        assert (metadata["accepted"], metadata["failed"]) == (1, 1)

    def test_audit_entity_falls_back_when_domain_is_unknown(self, env):
        env["session"].domains = ["arxiv.org"]
        env["client"].post(
            self.PATH, json={"items": _items("2608.20293")}, headers=_auth(env, "teacher"),
        )
        assert env["audits"][0][1] == "arxiv"

    def test_no_audit_when_rejected_up_front(self, env):
        res = env["client"].post(self.PATH, json={"items": []}, headers=_auth(env, "teacher"))
        assert res.status_code == 422
        assert env["audits"] == []


# ---------------------------------------------------------------------------
# 6. POST /dismiss・/restore
# ---------------------------------------------------------------------------


class TestDismissRestore:
    def test_dismiss_records_the_transition(self, env):
        res = env["client"].post(
            "/api/admin/discovery/dismiss",
            json={"domain_key": "astrophysics", "arxiv_id": "https://arxiv.org/abs/2608.20293v1"},
            headers=_auth(env, "teacher"),
        )
        assert res.status_code == 200
        assert res.json()["arxiv_id"] == "2608.20293"
        assert env["dismiss_calls"] == [("astrophysics", "2608.20293", _TEACHER)]
        assert env["session"].commits == 1

        entity_type, entity_id, old, new, user_id, metadata = env["audits"][0]
        assert (entity_type, entity_id) == ("paper_discovery", "astrophysics")
        assert (old, new) == ("candidate", "dismissed")
        assert metadata == {"action": "dismiss", "arxiv_id": "2608.20293"}

    def test_restore_records_the_reverse_transition(self, env):
        res = env["client"].post(
            "/api/admin/discovery/restore",
            json={"domain_key": "astrophysics", "arxiv_id": "2608.20293"},
            headers=_auth(env, "teacher"),
        )
        assert res.status_code == 200
        assert res.json()["revoked"] is True
        _t, _id, old, new, _u, metadata = env["audits"][0]
        assert (old, new) == ("dismissed", "candidate")
        assert metadata["action"] == "restore"

    def test_restore_without_a_record_is_404(self, env):
        env["restore_result"] = None
        res = env["client"].post(
            "/api/admin/discovery/restore",
            json={"domain_key": "astrophysics", "arxiv_id": "2608.20293"},
            headers=_auth(env, "teacher"),
        )
        assert res.status_code == 404
        assert env["audits"] == []
        assert env["session"].commits == 0
        assert env["session"].rollbacks == 1

    def test_invalid_arxiv_id_is_422(self, env):
        res = env["client"].post(
            "/api/admin/discovery/dismiss",
            json={"domain_key": "astrophysics", "arxiv_id": "___"},
            headers=_auth(env, "teacher"),
        )
        assert res.status_code == 422
        assert env["audits"] == []

    def test_missing_body_field_is_422(self, env):
        res = env["client"].post(
            "/api/admin/discovery/dismiss", json={"domain_key": "astrophysics"},
            headers=_auth(env, "teacher"),
        )
        assert res.status_code == 422


# ---------------------------------------------------------------------------
# 7. 構造的な検査（PD1 / PD2 / 登録）
# ---------------------------------------------------------------------------


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestRouterRegistration:
    def test_router_is_registered_in_main(self):
        main = _read(MAIN_SOURCE)
        assert "from routes import paper_discovery as paper_discovery_routes" in main
        assert "app.include_router(paper_discovery_routes.router)" in main

    def test_routes_are_mounted_under_admin_discovery(self):
        from api.main import app

        paths = {
            (tuple(sorted(getattr(route, "methods", []) or [])), route.path)
            for route in app.routes
            if "/api/admin/discovery" in getattr(route, "path", "")
        }
        assert (("GET",), "/api/admin/discovery/subscriptions") in paths
        assert (("PUT",), "/api/admin/discovery/subscriptions/{domain_key}") in paths
        assert (
            ("GET",), "/api/admin/discovery/subscriptions/{domain_key}/keyphrase-candidates"
        ) in paths
        assert (("POST",), "/api/admin/discovery/search") in paths
        assert (("POST",), "/api/admin/discovery/ingest") in paths
        assert (("POST",), "/api/admin/discovery/dismiss") in paths
        assert (("POST",), "/api/admin/discovery/restore") in paths

    def test_no_delete_route(self):
        # P4 / PD5: 見送りも購読も行削除の入口を作らない
        assert "@router.delete" not in _read(ROUTE_SOURCE)


class TestIngestGoesThroughUrlFetch:
    """PD2: 取得は既存の url_fetch 経由。独自の HTTP クライアントを持たない。"""

    def test_no_http_client_import(self):
        source = _read(ROUTE_SOURCE)
        for banned in ("import requests", "import httpx", "import urllib.request", "urlopen("):
            assert banned not in source, f"routes/paper_discovery.py が {banned} を持っている"

    def test_fetch_is_delegated_to_url_fetch(self):
        source = _read(ROUTE_SOURCE)
        assert "url_fetch.fetch_source_from_url(" in source
        assert "url_fetch.list_url_fetch_domains(" in source

    def test_ingest_limit_constant_exists(self):
        import routes.paper_discovery as routes

        assert routes.MAX_INGEST_PER_REQUEST == 5

    def test_no_background_ingest_path(self):
        # PD1: 教員のリクエスト以外から取り込みが起きる経路を作らない
        source = _read(ROUTE_SOURCE)
        for banned in ("threading.Thread", "BackgroundTasks", "schedule", "cron"):
            assert banned not in source


class TestAuditVocabulary:
    def test_entity_type_is_in_the_catalog(self):
        from core.schema import AUDIT_ENTITY_PAPER_DISCOVERY, AUDIT_ENTITY_TYPES

        assert AUDIT_ENTITY_PAPER_DISCOVERY == "paper_discovery"
        assert AUDIT_ENTITY_PAPER_DISCOVERY in AUDIT_ENTITY_TYPES

    def test_route_uses_the_catalog_constant(self):
        source = _read(ROUTE_SOURCE)
        assert "AUDIT_ENTITY_PAPER_DISCOVERY" in source
        assert '"paper_discovery"' not in source.split("AUDIT_ENTITY_PAPER_DISCOVERY", 1)[1]


class TestSourceUrlPersistence:
    """``documents.source_url`` の保存（PD5 の読み時導出の材料）。"""

    def test_insert_carries_source_url(self):
        import inspect

        import routes.admin as admin_routes

        body = inspect.getsource(admin_routes._accept_material_source)
        insert = re.search(r"INSERT INTO documents \(.*?\)\s*VALUES\s*\(.*?\n", body, re.S)
        assert insert is not None, "documents への INSERT が見つからない"
        statement = insert.group(0)
        assert "source_url" in statement, "INSERT の列に source_url が無い"
        assert ":source_url" in statement, "INSERT の VALUES に :source_url が無い"
        assert '"source_url": (source_url or None)' in body

    def test_accept_material_source_has_the_keyword(self):
        import inspect

        import routes.admin as admin_routes

        signature = inspect.signature(admin_routes._accept_material_source)
        assert "source_url" in signature.parameters
        assert signature.parameters["source_url"].default is None

    def test_upload_from_url_passes_the_url(self):
        import inspect

        import routes.admin as admin_routes

        body = inspect.getsource(admin_routes.upload_material_from_url)
        assert "source_url=body.url" in body

    def test_multipart_upload_does_not_pass_a_source_url(self):
        import inspect

        import routes.admin as admin_routes

        body = inspect.getsource(admin_routes.upload_material)
        assert "source_url" not in body
