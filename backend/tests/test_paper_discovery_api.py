"""論文ディスカバリー層 — 管理 API（``api/routes/paper_discovery.py``）。

対象エンドポイント（全て TEACHER 以上）:
  - ``GET  /api/admin/discovery/subscriptions``
  - ``PUT  /api/admin/discovery/subscriptions/{domain_key}``
  - ``GET  /api/admin/discovery/subscriptions/{domain_key}/keyphrase-candidates``
  - ``POST /api/admin/discovery/search``
  - ``POST /api/admin/discovery/ingest``
  - ``POST /api/admin/discovery/dismiss`` / ``/restore``
  - ``POST /api/admin/discovery/ingest-batch``（Phase 2 — キューへ積むだけ）
  - ``GET  /api/admin/discovery/ingest-queue``
  - ``POST /api/admin/discovery/ingest-queue/{item_id}/retry``
  - ``GET  /api/admin/discovery/ingest-estimate``

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
  8. Phase 2: バッチの件数上限・skip 事実文・許可リスト未設定の ``notice``・
     retry が ``failed`` 限定・事前見積りの分離（U1）とレンジのみ（U5）
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

#: 購読検索の**本物**の実装（``env`` フィクスチャがモジュール属性を差し替える前に
#: 束縛しておく）。arXiv からアクセスを制限されている間の縮退（設計書 §14.7）は
#: route と core の合わせ技なので、その1本だけは本物を通して検査する。
from core.paper_discovery.search import run_search as _REAL_RUN_SEARCH  # noqa: E402

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

    def __init__(self, domains=(), usage_rows=()):
        self.domains = list(domains)
        self.usage_rows = list(usage_rows)
        self.calls: list[tuple[str, dict]] = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = 0

    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        self.calls.append((sql, dict(params or {})))
        if sql.startswith("SELECT domain, created_at FROM url_fetch_domains"):
            return _Result([(d, _Stamp()) for d in sorted(self.domains)])
        if "FROM llm_usage_events" in sql:
            return _Result(self.usage_rows)
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
        # ── Phase 2（取り込みキュー） ───────────────────────────────────
        "enqueue_calls": [],
        "enqueue_result": None,
        "queue_items": [],
        "queue_list_calls": [],
        "retry_calls": [],
        "retry_result": {
            "item_id": "11111111-1111-1111-1111-111111111111",
            "domain_key": "astrophysics",
            "arxiv_id": "2608.20293",
            "status": "queued",
            "detail": "取得できませんでした。",
        },
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

    # ── 取り込みキュー（Phase 2 / migration 072） ─────────────────────────
    def _enqueue(session, items, **kwargs):
        entries = [dict(item) for item in items]
        state["enqueue_calls"].append((entries, kwargs))
        if state["enqueue_result"] is not None:
            return state["enqueue_result"]
        queued = []
        skipped = []
        for index, entry in enumerate(entries):
            normalized = routes.pd_schema.normalize_arxiv_id(entry.get("arxiv_id"))
            if not normalized:
                skipped.append(
                    {
                        "arxiv_id": str(entry.get("arxiv_id") or ""),
                        "detail": routes.pd_queue.SKIP_INVALID_ID,
                    }
                )
                continue
            queued.append(
                {
                    "item_id": f"item-{index}",
                    "arxiv_id": normalized,
                    "title": str(entry.get("title") or ""),
                }
            )
        return {"queued": queued, "skipped": skipped}

    def _list_items(session, **kwargs):
        state["queue_list_calls"].append(kwargs)
        return list(state["queue_items"])

    def _retry(session, item_id):
        state["retry_calls"].append(item_id)
        return state["retry_result"]

    monkeypatch.setattr(routes.pd_queue, "enqueue_items", _enqueue)
    monkeypatch.setattr(routes.pd_queue, "list_items", _list_items)
    monkeypatch.setattr(routes.pd_queue, "retry_item", _retry)

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
    ("post", "/api/admin/discovery/ingest-batch", {"items": [{"arxiv_id": "2608.20293"}]}),
    ("get", "/api/admin/discovery/ingest-queue", None),
    (
        "post",
        "/api/admin/discovery/ingest-queue/11111111-1111-1111-1111-111111111111/retry",
        {},
    ),
    ("get", "/api/admin/discovery/ingest-estimate", None),
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
        # Phase 3: ``citation_source_enabled`` はフロントの活性判定用の補助キー
        # （既定 off。強制はサーバ側 — 設計書 §6）。他のキーは Phase 1 のまま。
        assert res.json() == {
            "subscriptions": env["subscriptions"],
            "citation_source_enabled": False,
        }

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
        from tests.guardrail_helpers import iter_app_routes

        paths = {
            (tuple(sorted(m for m in (getattr(route, "methods", []) or []) if m != "HEAD")), route.path)
            for route in iter_app_routes(app)
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


# ---------------------------------------------------------------------------
# 8. バッチ取り込み（Phase 2 / migration 072）
# ---------------------------------------------------------------------------


class TestIngestSourceFormat:
    """取得する配信形式（TeX ソース / PDF）の選択。

    形式の分岐点は ``pd_schema.source_url_for`` の1箇所だけで、route が組み立てた
    URL がそのまま ``url_fetch`` と ``documents.source_url`` へ渡る。ここで固定するのは:

    - 既定（未指定）は **PDF**。この API の既定を変えると、形式スイッチを持たない
      分野購読モーダルの取り込みまで黙って変わる（画面の既定 = TeX はフロントが送る）。
    - 語彙外は 422 で、取得を1件も試みない（選んだ形式と実際に取る形式を食い違わせない）。
    - TeX を選んだときの ``documents.source_url`` は ``/src/<id>``。この URL も
      ``normalize_arxiv_id`` で同じ ID へ畳まれるので「取り込み済み」判定は効き続ける。
    """

    PATH = "/api/admin/discovery/ingest"

    def test_default_is_pdf(self, env):
        env["session"].domains = ["arxiv.org"]
        res = env["client"].post(
            self.PATH, json={"items": _items("2608.20293")}, headers=_auth(env, "teacher"),
        )
        assert res.status_code == 202
        assert env["fetches"] == [("https://arxiv.org/pdf/2608.20293", ["arxiv.org"])]

    def test_tex_fetches_the_source_url(self, env):
        env["session"].domains = ["arxiv.org"]
        res = env["client"].post(
            self.PATH,
            json={"items": _items("arXiv:2608.20293v2"), "source_format": "tex"},
            headers=_auth(env, "teacher"),
        )
        assert res.status_code == 202
        assert env["fetches"] == [("https://arxiv.org/src/2608.20293", ["arxiv.org"])]
        assert env["accepted"][0]["source_url"] == "https://arxiv.org/src/2608.20293"

    def test_tex_source_url_still_resolves_to_the_same_arxiv_id(self, env):
        from routes.paper_discovery import pd_schema

        assert (
            pd_schema.normalize_arxiv_id("https://arxiv.org/src/2608.20293")
            == pd_schema.normalize_arxiv_id("https://arxiv.org/pdf/2608.20293")
            == "2608.20293"
        )

    def test_explicit_pdf_is_honoured(self, env):
        env["session"].domains = ["arxiv.org"]
        env["client"].post(
            self.PATH,
            json={"items": _items("2608.20293"), "source_format": "pdf"},
            headers=_auth(env, "teacher"),
        )
        assert env["fetches"] == [("https://arxiv.org/pdf/2608.20293", ["arxiv.org"])]

    def test_unknown_format_is_422_and_fetches_nothing(self, env):
        env["session"].domains = ["arxiv.org"]
        res = env["client"].post(
            self.PATH,
            json={"items": _items("2608.20293"), "source_format": "html"},
            headers=_auth(env, "teacher"),
        )
        assert res.status_code == 422
        assert "形式" in res.json()["detail"]
        assert env["fetches"] == []
        assert env["accepted"] == []

    def test_format_is_recorded_in_the_audit(self, env):
        env["session"].domains = ["arxiv.org"]
        env["client"].post(
            self.PATH,
            json={"items": _items("2608.20293"), "source_format": "tex"},
            headers=_auth(env, "teacher"),
        )
        metadata = env["audits"][0][-1]
        assert metadata["source_format"] == "tex"

    def test_batch_passes_the_format_to_the_queue(self, env):
        env["session"].domains = ["arxiv.org"]
        res = env["client"].post(
            "/api/admin/discovery/ingest-batch",
            json={"items": [{"arxiv_id": "2608.20293"}], "source_format": "tex"},
            headers=_auth(env, "teacher"),
        )
        assert res.status_code == 202
        assert env["enqueue_calls"][0][1]["source_format"] == "tex"

    def test_batch_default_is_pdf(self, env):
        env["session"].domains = ["arxiv.org"]
        env["client"].post(
            "/api/admin/discovery/ingest-batch",
            json={"items": [{"arxiv_id": "2608.20293"}]},
            headers=_auth(env, "teacher"),
        )
        assert env["enqueue_calls"][0][1]["source_format"] == "pdf"

    def test_batch_unknown_format_is_422_and_queues_nothing(self, env):
        env["session"].domains = ["arxiv.org"]
        res = env["client"].post(
            "/api/admin/discovery/ingest-batch",
            json={"items": [{"arxiv_id": "2608.20293"}], "source_format": "TeX Source"},
            headers=_auth(env, "teacher"),
        )
        assert res.status_code == 422
        assert env["enqueue_calls"] == []
        assert env["audits"] == []


class TestIngestBatch:
    """``POST /ingest-batch`` — 積むのは教員の明示操作だけ（PD1）。"""

    def _post(self, env, body):
        return env["client"].post(
            "/api/admin/discovery/ingest-batch", json=body, headers=_auth(env, "teacher")
        )

    def test_empty_items_is_422(self, env):
        res = self._post(env, {"items": []})
        assert res.status_code == 422
        assert env["enqueue_calls"] == []
        assert env["audits"] == []

    def test_over_the_batch_limit_is_422(self, env):
        from routes.paper_discovery import MAX_INGEST_BATCH

        assert MAX_INGEST_BATCH == 50
        body = {"items": [{"arxiv_id": f"2608.{20000 + i}"} for i in range(MAX_INGEST_BATCH + 1)]}
        res = self._post(env, body)
        assert res.status_code == 422
        assert "50" in res.json()["detail"]
        assert env["enqueue_calls"] == []

    def test_at_the_batch_limit_is_accepted(self, env):
        env["session"].domains = ["arxiv.org"]
        from routes.paper_discovery import MAX_INGEST_BATCH

        body = {"items": [{"arxiv_id": f"2608.{20000 + i}"} for i in range(MAX_INGEST_BATCH)]}
        res = self._post(env, body)
        assert res.status_code == 202
        assert len(res.json()["queued"]) == MAX_INGEST_BATCH

    def test_returns_queued_and_skipped(self, env):
        env["session"].domains = ["arxiv.org"]
        res = self._post(
            env,
            {
                "items": [
                    {"arxiv_id": "2608.20293", "title": "Dark energy"},
                    {"arxiv_id": "not-an-id"},
                ],
                "domain_key": "astrophysics",
            },
        )
        assert res.status_code == 202
        payload = res.json()
        assert payload["queued"] == [
            {"item_id": "item-0", "arxiv_id": "2608.20293", "title": "Dark energy"}
        ]
        assert payload["skipped"][0]["arxiv_id"] == "not-an-id"
        assert payload["skipped"][0]["detail"]
        # 許可リストに arxiv.org がある間は注記を付けない
        assert "notice" not in payload

    def test_notice_when_domain_not_allowed(self, env):
        env["session"].domains = []
        res = self._post(env, {"items": [{"arxiv_id": "2608.20293"}]})
        assert res.status_code == 202
        payload = res.json()
        # 受理はする（許可が後から入れば流れる）が、黙らない（PD6）
        assert payload["queued"]
        assert payload["notice"]
        assert "許可" in payload["notice"]

    def test_no_notice_when_nothing_was_queued(self, env):
        env["session"].domains = []
        res = self._post(env, {"items": [{"arxiv_id": "not-an-id"}]})
        assert res.status_code == 202
        assert res.json()["queued"] == []
        assert "notice" not in res.json()

    def test_models_are_validated_at_enqueue_time(self, env, monkeypatch):
        """worker は再検証しない — 検証の正本は投入時（M4/M5 の fail-closed）。"""
        import routes.paper_discovery as routes

        calls = []

        def _validate(models):
            calls.append(models)
            return {"pipeline": "gpt-x"}

        monkeypatch.setattr(routes, "_validate_models_option", _validate)
        res = self._post(
            env, {"items": [{"arxiv_id": "2608.20293"}], "models": {"pipeline": "gpt-x"}}
        )
        assert res.status_code == 202
        assert calls == [{"pipeline": "gpt-x"}]
        assert env["enqueue_calls"][0][1]["models"] == {"pipeline": "gpt-x"}

    def test_invalid_models_reject_the_whole_request(self, env, monkeypatch):
        import fastapi

        import routes.paper_discovery as routes

        def _validate(models):
            raise fastapi.HTTPException(status_code=422, detail="bad model")

        monkeypatch.setattr(routes, "_validate_models_option", _validate)
        res = self._post(
            env, {"items": [{"arxiv_id": "2608.20293"}], "models": {"pipeline": "nope"}}
        )
        assert res.status_code == 422
        assert env["enqueue_calls"] == []

    def test_enqueue_receives_requester_and_options(self, env):
        self._post(
            env,
            {
                "items": [{"arxiv_id": "2608.20293"}],
                "domain_key": "astrophysics",
                "analyze_images": True,
            },
        )
        _entries, kwargs = env["enqueue_calls"][0]
        assert kwargs["domain_key"] == "astrophysics"
        assert kwargs["requested_by"] == _TEACHER
        assert kwargs["analyze_images"] is True

    def test_does_not_fetch_or_accept_synchronously(self, env):
        """バッチは積むだけ — リクエスト内で arXiv を取りに行かない。"""
        self._post(env, {"items": [{"arxiv_id": "2608.20293"}]})
        assert env["fetches"] == []
        assert env["accepted"] == []

    def test_audit_uses_ingest_batch_action(self, env):
        self._post(env, {"items": [{"arxiv_id": "2608.20293"}], "domain_key": "astrophysics"})
        entity_type, entity_id, _before, after, actor, metadata = env["audits"][0]
        assert entity_type == "paper_discovery"
        assert entity_id == "astrophysics"
        assert after == "queued"
        assert actor == _TEACHER
        assert metadata["action"] == "ingest_batch"
        assert metadata["arxiv_ids"] == ["2608.20293"]
        assert metadata["queued"] == 1

    def test_audit_carries_the_decision_context(self, env):
        """DC1（是正 A-04 / F-18）: 一括取り込みは確定文脈なしに記帳しない。"""
        from core import decision_context as dc

        env["session"].domains = ["arxiv.org"]
        self._post(
            env,
            {
                "items": [{"arxiv_id": "2608.20293"}, {"arxiv_id": "not-an-id"}],
                "domain_key": "astrophysics",
            },
        )
        metadata = env["audits"][0][5]
        assert metadata["bulk"] is True
        ctx = metadata[dc.DECISION_CONTEXT_KEY]
        assert ctx["basis"] == dc.BASIS_DISCOVERY_INGEST_BATCH
        # 提示 = 選ばれた候補集合 / 適用 = 実際に積まれた行（差は隠さない — DC2）。
        assert ctx["presented"]["ids"] == ["2608.20293", "not-an-id"]
        assert ctx["applied"]["ids"] == ["2608.20293"]
        assert ctx["presented_matches_applied"] is False
        assert ctx["alternatives_available"] == ["deselect", "dismiss"]
        assert ctx["decline_possible"] is True
        assert ctx["reopen"]["path"].startswith("DELETE /api/admin/materials/")
        assert ctx["reopen"]["statuses"] == []
        assert ctx["evidence_shown"] is None
        assert ctx["client_reported"] is None

    def test_decision_context_matches_when_everything_is_queued(self, env):
        from core import decision_context as dc

        env["session"].domains = ["arxiv.org"]
        self._post(env, {"items": [{"arxiv_id": "2608.20293"}]})
        ctx = env["audits"][0][5][dc.DECISION_CONTEXT_KEY]
        assert ctx["presented_matches_applied"] is True

    def test_response_shape_is_unchanged(self, env):
        """B（記帳のみ・挙動不変）: レスポンスに確定文脈を足していない。"""
        env["session"].domains = ["arxiv.org"]
        res = self._post(env, {"items": [{"arxiv_id": "2608.20293"}]})
        assert set(res.json()) == {"queued", "skipped"}

    def test_session_is_committed_and_closed(self, env):
        self._post(env, {"items": [{"arxiv_id": "2608.20293"}]})
        assert env["session"].commits >= 1
        assert env["session"].closed >= 1


class TestIngestQueueListing:
    def test_returns_items(self, env):
        env["queue_items"] = [
            {
                "item_id": "i-1",
                "arxiv_id": "2608.20293",
                "status": "failed",
                "detail": "取得できませんでした。",
            }
        ]
        res = env["client"].get(
            "/api/admin/discovery/ingest-queue", headers=_auth(env, "teacher")
        )
        assert res.status_code == 200
        assert res.json() == {"items": env["queue_items"]}

    def test_passes_filters_through(self, env):
        env["client"].get(
            "/api/admin/discovery/ingest-queue?domain_key=astrophysics&limit=5",
            headers=_auth(env, "teacher"),
        )
        assert env["queue_list_calls"][0] == {"domain_key": "astrophysics", "limit": 5}

    def test_works_without_domain_key(self, env):
        res = env["client"].get(
            "/api/admin/discovery/ingest-queue", headers=_auth(env, "teacher")
        )
        assert res.status_code == 200
        assert env["queue_list_calls"][0]["domain_key"] == ""

    def test_listing_does_not_write_audit(self, env):
        env["client"].get("/api/admin/discovery/ingest-queue", headers=_auth(env, "teacher"))
        assert env["audits"] == []


class TestIngestQueueRetry:
    _PATH = "/api/admin/discovery/ingest-queue/11111111-1111-1111-1111-111111111111/retry"

    def test_failed_item_is_requeued(self, env):
        res = env["client"].post(self._PATH, headers=_auth(env, "teacher"))
        assert res.status_code == 200
        assert res.json()["item"]["status"] == "queued"
        assert env["retry_calls"] == ["11111111-1111-1111-1111-111111111111"]

    def test_non_failed_item_is_422(self, env):
        """``retry_item`` が None を返す = 失敗行ではない（処理中 / 受理済み / 不在）。"""
        env["retry_result"] = None
        res = env["client"].post(self._PATH, headers=_auth(env, "teacher"))
        assert res.status_code == 422
        assert res.json()["detail"] == "再試行できるのは失敗した項目だけです。"
        assert env["audits"] == []
        assert env["session"].rollbacks >= 1

    def test_audit_uses_ingest_retry_action(self, env):
        env["client"].post(self._PATH, headers=_auth(env, "teacher"))
        entity_type, _entity_id, before, after, _actor, metadata = env["audits"][0]
        assert entity_type == "paper_discovery"
        assert (before, after) == ("failed", "queued")
        assert metadata["action"] == "ingest_retry"
        assert metadata["arxiv_id"] == "2608.20293"

    def test_retry_does_not_fetch_synchronously(self, env):
        env["client"].post(self._PATH, headers=_auth(env, "teacher"))
        assert env["fetches"] == []
        assert env["accepted"] == []


class TestIngestEstimate:
    """事前見積り（U1 分離 / U5 レンジのみ・金額なし / 実績ゼロは正直に）。"""

    _PATH = "/api/admin/discovery/ingest-estimate"

    def test_no_history_reports_unavailable(self, env):
        env["session"].usage_rows = []
        res = env["client"].get(self._PATH, headers=_auth(env, "teacher"))
        assert res.status_code == 200
        payload = res.json()
        assert payload["available"] is False
        assert payload["note"] == "解析の実績がまだないため、目安を示せません。"
        assert "per_document" not in payload

    def test_history_yields_separated_ranges(self, env):
        env["session"].usage_rows = [
            ("reported", "doc-1", 12000),
            ("reported", "doc-2", 16000),
            ("estimated_heuristic", "doc-3", 900),
        ]
        res = env["client"].get(self._PATH + "?count=3", headers=_auth(env, "teacher"))
        assert res.status_code == 200
        payload = res.json()
        assert payload["available"] is True
        assert payload["item_count"] == 3

        reported = payload["per_document"]["reported"]
        estimated = payload["per_document"]["estimated"]
        # U1: 実測と推計を合算しない（別バケットで返す）
        assert reported["documents"] == 2
        assert estimated["documents"] == 1
        for bucket in (reported, estimated):
            low, high = bucket["total_tokens_range"]
            assert isinstance(low, int) and isinstance(high, int)
            assert 0 <= low <= high
        assert payload["batch"]["reported"]["total_tokens_range"][1] > \
            reported["total_tokens_range"][1]
        assert payload["basis_note"]

    def test_missing_bucket_is_none_not_zero(self, env):
        env["session"].usage_rows = [("reported", "doc-1", 12000)]
        payload = env["client"].get(self._PATH, headers=_auth(env, "teacher")).json()
        assert payload["per_document"]["estimated"] is None
        assert payload["batch"]["estimated"] is None

    def test_no_cost_or_point_estimate_keys(self, env):
        env["session"].usage_rows = [
            ("reported", "doc-1", 12000),
            ("estimated_heuristic", "doc-2", 900),
        ]
        payload = env["client"].get(self._PATH + "?count=2", headers=_auth(env, "teacher")).json()

        def _walk(obj, path=""):
            found = []
            if isinstance(obj, dict):
                for key, value in obj.items():
                    lowered = str(key).lower()
                    cur = f"{path}.{key}" if path else str(key)
                    if "cost" in lowered or "usd" in lowered or "price" in lowered:
                        found.append(cur)
                    if lowered.endswith("tokens") and not lowered.endswith("_range"):
                        found.append(cur)
                    found.extend(_walk(value, cur))
            elif isinstance(obj, list):
                for i, item in enumerate(obj):
                    found.extend(_walk(item, f"{path}[{i}]"))
            return found

        assert _walk(payload) == []

    def test_estimate_does_not_call_arxiv_or_llm(self, env):
        env["session"].usage_rows = [("reported", "doc-1", 12000)]
        env["client"].get(self._PATH, headers=_auth(env, "teacher"))
        assert env["search_calls"] == []
        assert env["fetches"] == []
        assert env["accepted"] == []

    def test_only_pipeline_features_are_summed(self, env):
        env["session"].usage_rows = [("reported", "doc-1", 12000)]
        env["client"].get(self._PATH, headers=_auth(env, "teacher"))
        usage_sqls = [sql for sql in env["session"].sqls() if "llm_usage_events" in sql]
        assert usage_sqls
        assert "feature LIKE" in usage_sqls[0]
        assert "DELETE" not in usage_sqls[0].upper()


class TestPhase2RouteRegistration:
    def test_all_new_routes_require_teacher(self):
        import routes.paper_discovery as routes

        wanted = {
            "/api/admin/discovery/ingest-batch",
            "/api/admin/discovery/ingest-queue",
            "/api/admin/discovery/ingest-queue/{item_id}/retry",
            "/api/admin/discovery/ingest-estimate",
        }
        seen = set()
        for route in routes.router.routes:
            if route.path not in wanted:
                continue
            seen.add(route.path)
            names = {dep.call.__name__ for dep in route.dependant.dependencies if dep.call}
            assert "_require_teacher" in names, f"{route.path}: {names}"
        assert seen == wanted

    def test_no_delete_routes(self):
        src = ROUTE_SOURCE.read_text(encoding="utf-8")
        assert "@router.delete" not in src

    def test_ingest_batch_returns_202(self):
        src = ROUTE_SOURCE.read_text(encoding="utf-8")
        assert re.search(r'@router\.post\("/ingest-batch", status_code=202\)', src)


# ---------------------------------------------------------------------------
# 12. arXiv からアクセスを制限されている間の購読検索（設計書 §14.7）
# ---------------------------------------------------------------------------


class TestSearchWhileArxivBlocked:
    """制限中と**呼ぶ前から分かっている**検索は、arXiv を呼ばずに 200 で事実を返す。

    live の 429（探して断られた）は従来どおり 502 のまま（§13.3）。ここで扱うのは
    「押しても出ていかない」と分かっている状態だけで、0 件を「該当なし」と偽らない。
    """

    PATH = "/api/admin/discovery/search"

    def _blocked(self, env, monkeypatch):
        monkeypatch.setattr(
            env["routes"].arxiv_client, "cooldown_active", lambda: True
        )
        # 縮退は route + core の合わせ技なので、この経路だけ本物の run_search を通す。
        monkeypatch.setattr(env["routes"].pd_search, "run_search", _REAL_RUN_SEARCH)

        def _boom(*args, **kwargs):  # pragma: no cover — 呼ばれたら失敗させる
            raise AssertionError("arXiv must not be called while blocked")

        monkeypatch.setattr(env["routes"].arxiv_client, "search", _boom)
        monkeypatch.setattr(env["routes"].pd_store, "touch_last_checked", _boom)

    def _post(self, env, **extra):
        body = {"domain_key": "astrophysics", "categories": ["astro-ph.CO"]}
        body.update(extra)
        return env["client"].post(self.PATH, json=body, headers=_auth(env, "teacher"))

    def test_blocked_search_is_200_with_the_fact(self, env, monkeypatch):
        self._blocked(env, monkeypatch)
        res = self._post(env)
        assert res.status_code == 200
        body = res.json()
        assert body["arxiv_blocked"] is True
        assert body["candidates"] == []
        assert body["note"] == env["routes"].pd_radar.NOTE_ARXIV_BLOCKED
        # 閉世界の注記の前に「探していない」ことを置く（0 件の読み違いを防ぐ）。
        assert body["closed_world_note"].startswith(
            env["routes"].pd_radar.NOTE_ARXIV_BLOCKED
        )
        assert body["closed_world_note"].endswith(
            env["routes"].pd_search.CLOSED_WORLD_NOTE
        )

    def test_blocked_search_does_not_touch_last_checked(self, env, monkeypatch):
        """探していないのに「確かめた」と記録しない（地図の端の1ビットを汚さない）。"""
        self._blocked(env, monkeypatch)
        res = self._post(env)
        assert res.status_code == 200
        # ``touch_last_checked`` は呼ばれたら AssertionError（＝500）になる差し替え。
        assert env["audits"] == []

    def test_blocked_search_does_not_leak_numbers(self, env, monkeypatch):
        """PR2: 残り時間・回数を返さない（人に数値を見せない）。"""
        import json

        self._blocked(env, monkeypatch)
        body = self._post(env).json()
        for sentence in (body["note"], body["closed_world_note"]):
            assert not re.search(r"[0-9０-９]", sentence), sentence
        assert "cooldown" not in json.dumps(body, ensure_ascii=False)

    def test_relevance_order_does_not_embed_while_blocked(self, env, monkeypatch):
        """候補ゼロを並べ替えるために embedding を焚かない。"""
        self._blocked(env, monkeypatch)

        def _boom(*args, **kwargs):  # pragma: no cover
            raise AssertionError("ranking must not run while blocked")

        monkeypatch.setattr(env["routes"], "_apply_relevance_order", _boom)
        res = self._post(env, order="relevance")
        assert res.status_code == 200
        assert res.json()["arxiv_blocked"] is True

    def test_normal_search_states_that_it_is_not_blocked(self, env, monkeypatch):
        """制限されていないことも明示する（キーの不在で黙らせない）。"""
        monkeypatch.setattr(
            env["routes"].arxiv_client, "cooldown_active", lambda: False
        )
        monkeypatch.setattr(env["routes"].pd_search, "run_search", _REAL_RUN_SEARCH)
        monkeypatch.setattr(
            env["routes"].arxiv_client, "search", lambda query, **kwargs: (0, [])
        )
        res = self._post(env)
        assert res.status_code == 200
        body = res.json()
        assert body["arxiv_blocked"] is False
        assert "note" not in body, "止めていないのに事実文を出さない"
        assert body["closed_world_note"] == env["routes"].pd_search.CLOSED_WORLD_NOTE

    def test_the_route_passes_the_blocked_note_to_core(self, env, monkeypatch):
        """判定は route・縮退は core（文言の正本を1箇所にする）。"""
        monkeypatch.setattr(
            env["routes"].arxiv_client, "cooldown_active", lambda: False
        )
        self._post(env)
        assert env["search_calls"][0][1]["arxiv_blocked_note"] == ""
