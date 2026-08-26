"""URL指定による教材取得 — 管理 API（``api/routes/admin.py``）。

対象エンドポイント:
  - ``GET    /api/admin/url-fetch-domains``      （TEACHER 以上）
  - ``POST   /api/admin/url-fetch-domains``      （SYSTEM_ADMIN のみ）
  - ``DELETE /api/admin/url-fetch-domains/{domain}``（SYSTEM_ADMIN のみ）
  - ``POST   /api/admin/materials/upload-from-url``（TEACHER 以上）

``tests/test_account_lifecycle_api.py`` の流儀（実 app + ``TestClient`` +
``_pg_session`` のフェイク差し替え）を踏襲する。DB・MinIO・ネットワークには接続しない。

検証観点:
  1. 権限の fail-closed（STUDENT は全て 403 / TEACHER は変更操作 403）
  2. 許可リストの正規化・冪等・404・監査記帳
  3. 許可リスト空 → 422（事実文）・不許可ドメイン → 422
  4. 正常系が**既存アップロードフローに合流**する（同じ helper・同じレスポンス形）
  5. 例外型 → HTTP ステータスの写像（502 / 413 / 422）と内部情報の非漏洩
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
        return "2026-08-25T00:00:00+00:00"


class FakeSession:
    def __init__(self, domains=(), delete_hits=True):
        self.domains = list(domains)
        self.delete_hits = delete_hits
        self.calls: list[tuple[str, dict]] = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = 0

    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        params = dict(params or {})
        self.calls.append((sql, params))
        if sql.startswith("SELECT domain"):
            return _Result([(d, _Stamp()) for d in sorted(self.domains)])
        if sql.startswith("DELETE FROM url_fetch_domains"):
            return _Result([(params.get("domain"),)] if self.delete_hits else [])
        return _Result()

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed += 1

    def sqls(self):
        return [sql for sql, _p in self.calls]

    def params_for(self, prefix):
        for sql, params in self.calls:
            if sql.startswith(prefix):
                return params
        return None


@pytest.fixture
def env(monkeypatch):
    """TestClient + フェイクセッション + 監査 / 取得 / パイプライン起動の記録。"""
    from fastapi.testclient import TestClient
    from api.main import app
    from dependencies import ROLE_STUDENT, ROLE_SYSTEM_ADMIN, ROLE_TEACHER, _create_token
    import routes.admin as routes

    state: dict = {
        "session": FakeSession(),
        "audits": [],
        "accepted": [],
        "fetches": [],
        "fetch_result": None,
        "fetch_error": None,
    }

    monkeypatch.setattr(routes, "_pg_session", lambda: state["session"])
    monkeypatch.setattr(
        routes, "record_review_event",
        lambda *args: state["audits"].append(args),
    )

    def _fetch(url, allowed_domains):
        state["fetches"].append((url, list(allowed_domains)))
        if state["fetch_error"] is not None:
            raise state["fetch_error"]
        return state["fetch_result"] or routes.url_fetch.FetchedSource(
            content=PDF_BYTES, source_kind="pdf", filename="1711.03050.pdf",
        )

    monkeypatch.setattr(routes.url_fetch, "fetch_source_from_url", _fetch)

    def _accept(**kwargs):
        state["accepted"].append(kwargs)
        return {
            "task_id": "task-1",
            "material_id": "mat-1",
            "filename": kwargs["filename"],
            "title": "1711.03050",
            "source_kind": kwargs["source_kind"],
            "status": "pending",
            "uploaded_at": "2026-08-25T00:00:00",
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


# ---------------------------------------------------------------------------
# 1. GET /url-fetch-domains
# ---------------------------------------------------------------------------


class TestListDomains:
    def test_student_is_forbidden(self, env):
        res = env["client"].get("/api/admin/url-fetch-domains", headers=_auth(env, "student"))
        assert res.status_code == 403

    def test_anonymous_is_rejected(self, env):
        res = env["client"].get("/api/admin/url-fetch-domains")
        assert res.status_code in (401, 403)

    def test_teacher_can_read(self, env):
        env["session"].domains = ["example.com", "arxiv.org"]
        res = env["client"].get("/api/admin/url-fetch-domains", headers=_auth(env, "teacher"))
        assert res.status_code == 200
        body = res.json()
        assert [d["domain"] for d in body["domains"]] == ["arxiv.org", "example.com"]
        assert body["domains"][0]["created_at"] == "2026-08-25T00:00:00+00:00"

    def test_empty_by_default(self, env):
        """初期状態は空 = URL 取得は無効（シード行を入れない仕様）。"""
        res = env["client"].get("/api/admin/url-fetch-domains", headers=_auth(env, "admin"))
        assert res.status_code == 200
        assert res.json() == {"domains": []}

    def test_session_is_closed(self, env):
        env["client"].get("/api/admin/url-fetch-domains", headers=_auth(env, "teacher"))
        assert env["session"].closed == 1


# ---------------------------------------------------------------------------
# 2. POST /url-fetch-domains
# ---------------------------------------------------------------------------


class TestAddDomain:
    def test_student_is_forbidden(self, env):
        res = env["client"].post(
            "/api/admin/url-fetch-domains", json={"domain": "arxiv.org"},
            headers=_auth(env, "student"),
        )
        assert res.status_code == 403

    def test_teacher_is_forbidden(self, env):
        """変更は SYSTEM_ADMIN のみ（許可リストは SSRF ガードの要）。"""
        res = env["client"].post(
            "/api/admin/url-fetch-domains", json={"domain": "arxiv.org"},
            headers=_auth(env, "teacher"),
        )
        assert res.status_code == 403
        assert env["session"].calls == []

    def test_admin_creates_and_normalizes(self, env):
        res = env["client"].post(
            "/api/admin/url-fetch-domains", json={"domain": "HTTPS://ArXiv.org/pdf/"},
            headers=_auth(env, "admin"),
        )
        assert res.status_code == 201
        assert res.json() == {"domain": "arxiv.org"}
        params = env["session"].params_for("INSERT INTO url_fetch_domains")
        assert params["domain"] == "arxiv.org"
        assert params["added_by"] == _ADMIN
        assert env["session"].commits == 1

    def test_is_idempotent(self, env):
        res = env["client"].post(
            "/api/admin/url-fetch-domains", json={"domain": "arxiv.org"},
            headers=_auth(env, "admin"),
        )
        assert res.status_code == 201
        assert "ON CONFLICT (domain) DO NOTHING" in env["session"].sqls()[0]

    @pytest.mark.parametrize("domain", ["localhost", "127.0.0.1", "", "  ", "not a domain"])
    def test_invalid_domain_is_422(self, env, domain):
        res = env["client"].post(
            "/api/admin/url-fetch-domains", json={"domain": domain},
            headers=_auth(env, "admin"),
        )
        assert res.status_code == 422
        assert env["session"].commits == 0

    def test_missing_body_field_is_422(self, env):
        res = env["client"].post(
            "/api/admin/url-fetch-domains", json={}, headers=_auth(env, "admin"),
        )
        assert res.status_code == 422

    def test_audit_is_recorded(self, env):
        from core.schema import AUDIT_ENTITY_URL_FETCH_DOMAIN

        env["client"].post(
            "/api/admin/url-fetch-domains", json={"domain": "ARXIV.org"},
            headers=_auth(env, "admin"),
        )
        assert len(env["audits"]) == 1
        entity_type, entity_id, _old, _new, actor, metadata = env["audits"][0]
        assert entity_type == AUDIT_ENTITY_URL_FETCH_DOMAIN
        assert entity_id == "arxiv.org"
        assert actor == _ADMIN
        assert metadata["action"] == "create"

    def test_no_audit_when_rejected(self, env):
        env["client"].post(
            "/api/admin/url-fetch-domains", json={"domain": "localhost"},
            headers=_auth(env, "admin"),
        )
        assert env["audits"] == []


# ---------------------------------------------------------------------------
# 3. DELETE /url-fetch-domains/{domain}
# ---------------------------------------------------------------------------


class TestDeleteDomain:
    def test_teacher_is_forbidden(self, env):
        res = env["client"].delete(
            "/api/admin/url-fetch-domains/arxiv.org", headers=_auth(env, "teacher"),
        )
        assert res.status_code == 403
        assert env["session"].calls == []

    def test_student_is_forbidden(self, env):
        res = env["client"].delete(
            "/api/admin/url-fetch-domains/arxiv.org", headers=_auth(env, "student"),
        )
        assert res.status_code == 403

    def test_admin_deletes(self, env):
        res = env["client"].delete(
            "/api/admin/url-fetch-domains/arxiv.org", headers=_auth(env, "admin"),
        )
        assert res.status_code == 200
        assert res.json() == {"domain": "arxiv.org", "deleted": True}
        assert env["session"].commits == 1

    def test_missing_domain_is_404(self, env):
        env["session"].delete_hits = False
        res = env["client"].delete(
            "/api/admin/url-fetch-domains/absent.org", headers=_auth(env, "admin"),
        )
        assert res.status_code == 404
        assert env["audits"] == []

    def test_audit_is_recorded(self, env):
        from core.schema import AUDIT_ENTITY_URL_FETCH_DOMAIN

        env["client"].delete(
            "/api/admin/url-fetch-domains/arxiv.org", headers=_auth(env, "admin"),
        )
        entity_type, entity_id, _old, _new, actor, metadata = env["audits"][0]
        assert entity_type == AUDIT_ENTITY_URL_FETCH_DOMAIN
        assert entity_id == "arxiv.org"
        assert actor == _ADMIN
        assert metadata["action"] == "delete"


# ---------------------------------------------------------------------------
# 4. POST /materials/upload-from-url
# ---------------------------------------------------------------------------


class TestUploadFromUrl:
    URL = "https://arxiv.org/pdf/1711.03050"

    def test_student_is_forbidden(self, env):
        res = env["client"].post(
            "/api/admin/materials/upload-from-url", json={"url": self.URL},
            headers=_auth(env, "student"),
        )
        assert res.status_code == 403
        assert env["fetches"] == []

    def test_empty_allowlist_is_422_with_setup_guidance(self, env):
        """許可リストが空（=機能未設定）は、不許可ドメインとは別の事実文で返す。"""
        env["session"].domains = []
        # core が空リストを NoDomainsConfiguredError として拒否する経路を通す
        env["fetch_error"] = env["routes"].url_fetch.NoDomainsConfiguredError(
            "URLからの取得は、管理者が取得先ドメインを許可リストに登録すると利用できます"
        )
        res = env["client"].post(
            "/api/admin/materials/upload-from-url", json={"url": self.URL},
            headers=_auth(env, "teacher"),
        )
        assert res.status_code == 422
        assert "許可リストに登録" in res.json()["detail"]
        assert env["accepted"] == []
        # ルートは空リストを自前で判定せず、core へそのまま渡す（判定の正本は1箇所）
        assert env["fetches"] == [(self.URL, [])]

    def test_disallowed_domain_is_422(self, env):
        env["fetch_error"] = env["routes"].url_fetch.DomainNotAllowedError(
            "このURLのドメインは許可されていません"
        )
        res = env["client"].post(
            "/api/admin/materials/upload-from-url", json={"url": "https://evil.com/x.pdf"},
            headers=_auth(env, "teacher"),
        )
        assert res.status_code == 422
        assert res.json()["detail"] == "このURLのドメインは許可されていません"

    def test_private_address_is_422_without_leaking_internals(self, env):
        env["fetch_error"] = env["routes"].url_fetch.PrivateAddressError(
            "URL の接続先が内部アドレスのため取得できません"
        )
        res = env["client"].post(
            "/api/admin/materials/upload-from-url", json={"url": self.URL},
            headers=_auth(env, "teacher"),
        )
        assert res.status_code == 422
        detail = res.json()["detail"]
        assert "169.254" not in detail and "127.0.0.1" not in detail

    def test_fetch_failure_is_502(self, env):
        env["fetch_error"] = env["routes"].url_fetch.FetchFailedError("URLからの取得に失敗しました")
        res = env["client"].post(
            "/api/admin/materials/upload-from-url", json={"url": self.URL},
            headers=_auth(env, "teacher"),
        )
        assert res.status_code == 502

    def test_unsupported_content_is_422(self, env):
        env["fetch_error"] = env["routes"].url_fetch.UnsupportedContentError("nope")
        res = env["client"].post(
            "/api/admin/materials/upload-from-url", json={"url": self.URL},
            headers=_auth(env, "teacher"),
        )
        assert res.status_code == 422

    def test_too_large_is_413(self, env):
        env["fetch_error"] = env["routes"].url_fetch.TooLargeError("too big")
        res = env["client"].post(
            "/api/admin/materials/upload-from-url", json={"url": self.URL},
            headers=_auth(env, "teacher"),
        )
        assert res.status_code == 413

    def test_missing_url_is_422(self, env):
        res = env["client"].post(
            "/api/admin/materials/upload-from-url", json={}, headers=_auth(env, "teacher"),
        )
        assert res.status_code == 422

    def test_allowlist_is_passed_to_core(self, env):
        env["session"].domains = ["arxiv.org", "example.com"]
        env["client"].post(
            "/api/admin/materials/upload-from-url", json={"url": self.URL},
            headers=_auth(env, "teacher"),
        )
        assert env["fetches"] == [(self.URL, ["arxiv.org", "example.com"])]

    def test_success_joins_existing_upload_flow(self, env):
        env["session"].domains = ["arxiv.org"]
        res = env["client"].post(
            "/api/admin/materials/upload-from-url", json={"url": self.URL},
            headers=_auth(env, "teacher"),
        )
        assert res.status_code == 202
        body = res.json()
        assert set(body) == {
            "task_id", "material_id", "filename", "title",
            "source_kind", "status", "uploaded_at", "analyze_images",
        }
        assert body["source_kind"] == "pdf"
        assert body["filename"] == "1711.03050.pdf"

        assert len(env["accepted"]) == 1
        accepted = env["accepted"][0]
        assert accepted["source_bytes"] == PDF_BYTES
        assert accepted["source_kind"] == "pdf"
        assert accepted["filename"] == "1711.03050.pdf"
        assert accepted["analyze_images"] is False
        assert accepted["models_option"] is None
        assert accepted["current_user"]["id"] == _TEACHER

    def test_tex_archive_flows_through(self, env):
        env["session"].domains = ["arxiv.org"]
        env["fetch_result"] = env["routes"].url_fetch.FetchedSource(
            content=b"\x1f\x8b\x08\x00x", source_kind="tex_archive",
            filename="1711.03050.tar.gz",
        )
        res = env["client"].post(
            "/api/admin/materials/upload-from-url",
            json={"url": "https://arxiv.org/src/1711.03050"},
            headers=_auth(env, "teacher"),
        )
        assert res.status_code == 202
        assert res.json()["source_kind"] == "tex_archive"
        assert env["accepted"][0]["filename"] == "1711.03050.tar.gz"

    def test_analyze_images_is_forwarded(self, env):
        env["session"].domains = ["arxiv.org"]
        res = env["client"].post(
            "/api/admin/materials/upload-from-url",
            json={"url": self.URL, "analyze_images": True},
            headers=_auth(env, "teacher"),
        )
        assert res.status_code == 202
        assert env["accepted"][0]["analyze_images"] is True

    def test_models_are_validated_by_existing_helper(self, env):
        """`models` は既存アップロードと同じ fail-closed 検証を通す。"""
        env["session"].domains = ["arxiv.org"]
        res = env["client"].post(
            "/api/admin/materials/upload-from-url",
            json={"url": self.URL, "models": {"pipeline": "not-a-real-model"}},
            headers=_auth(env, "teacher"),
        )
        assert res.status_code == 422
        assert env["fetches"] == [], "検証前に取得してはならない"

    def test_invalid_models_key_is_422(self, env):
        env["session"].domains = ["arxiv.org"]
        res = env["client"].post(
            "/api/admin/materials/upload-from-url",
            json={"url": self.URL, "models": {"bogus_key": "gpt-4o"}},
            headers=_auth(env, "teacher"),
        )
        assert res.status_code == 422

    def test_valid_models_are_forwarded(self, env, monkeypatch):
        env["session"].domains = ["arxiv.org"]
        monkeypatch.setattr(
            env["routes"], "_validate_models_option", lambda m: {"pipeline": "gpt-4o"},
        )
        res = env["client"].post(
            "/api/admin/materials/upload-from-url",
            json={"url": self.URL, "models": {"pipeline": "gpt-4o"}},
            headers=_auth(env, "teacher"),
        )
        assert res.status_code == 202
        assert env["accepted"][0]["models_option"] == {"pipeline": "gpt-4o"}

    def test_no_audit_event_for_upload(self, env):
        """教材アップロードは既存経路と同様に監査 entity を増やさない。"""
        env["session"].domains = ["arxiv.org"]
        env["client"].post(
            "/api/admin/materials/upload-from-url", json={"url": self.URL},
            headers=_auth(env, "teacher"),
        )
        assert env["audits"] == []
