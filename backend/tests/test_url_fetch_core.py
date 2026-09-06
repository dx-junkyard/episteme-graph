"""URL指定による教材取得 — core（``backend/core/url_fetch.py``）の単体テスト。

DDL は ``backend/db/070_url_fetch_domains.sql``、API 層は
``backend/api/routes/admin.py``（``tests/test_url_fetch_api.py``）。

検証観点:
  1. ``normalize_domain`` の正規化と拒否（scheme / port / path / 大文字 / IP / 単一ラベル）
  2. ``domain_allowed`` の境界（完全一致・ドット境界のサブドメイン・偽サフィックス）
  3. 形式判定が**実バイトのマジックのみ**に依存すること（Content-Type / 拡張子は無視）
  4. ファイル名導出（Content-Disposition → URL パス末尾 → "download"、拡張子の保証）
  5. SSRF ガード: private / loopback / link-local を解決するホストの拒否
  6. リダイレクトを**各ホップで再検証**すること（許可ドメイン → 内部アドレスの 302 を塞ぐ）
  7. サイズ上限・許可リスト空・非対応形式の例外型

ネットワークには一切接続しない（``requests.Session`` と ``socket.getaddrinfo`` を
モックする）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
for _path in (str(BACKEND), str(BACKEND / "api")):
    if _path not in sys.path:
        sys.path.insert(0, str(_path))

from core import url_fetch


# ---------------------------------------------------------------------------
# 1. normalize_domain
# ---------------------------------------------------------------------------


class TestNormalizeDomain:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("arxiv.org", "arxiv.org"),
            ("  arxiv.org  ", "arxiv.org"),
            ("ARXIV.ORG", "arxiv.org"),
            ("ArXiv.Org.", "arxiv.org"),
            ("https://arxiv.org", "arxiv.org"),
            ("http://arxiv.org/pdf/1711.03050", "arxiv.org"),
            ("https://ARXIV.org:443/", "arxiv.org"),
            ("arxiv.org:8080", "arxiv.org"),
            ("arxiv.org/pdf", "arxiv.org"),
            ("export.arxiv.org", "export.arxiv.org"),
            ("https://user:pw@arxiv.org/x", "arxiv.org"),
            # パス成分はホスト抽出の時点で落ちる（"/../" があっても host は arxiv.org）
            ("arxiv.org/../etc", "arxiv.org"),
        ],
    )
    def test_normalizes(self, raw, expected):
        assert url_fetch.normalize_domain(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "   ",
            "localhost",           # 単一ラベル
            "127.0.0.1",           # IP は許可リストの単位にしない
            "::1",
            "192.168.0.1",
            "ftp://arxiv.org",     # 非 http/https scheme
            "arxiv .org",          # ラベルに空白
            "-arxiv.org",          # ハイフンで始まるラベル
            "arxiv-.org",          # ハイフンで終わるラベル
            "arxiv..org",          # 空ラベル
        ],
    )
    def test_rejects(self, raw):
        with pytest.raises(ValueError):
            url_fetch.normalize_domain(raw)

    def test_rejects_non_string(self):
        with pytest.raises(ValueError):
            url_fetch.normalize_domain(None)  # type: ignore[arg-type]

    def test_rejects_overlong(self):
        with pytest.raises(ValueError):
            url_fetch.normalize_domain(".".join(["abcdefgh"] * 40) + ".org")


# ---------------------------------------------------------------------------
# 2. domain_allowed
# ---------------------------------------------------------------------------


class TestDomainAllowed:
    ALLOWED = ["arxiv.org", "example.com"]

    @pytest.mark.parametrize(
        "host",
        [
            "arxiv.org",
            "ARXIV.ORG",
            "arxiv.org.",
            "export.arxiv.org",
            "a.b.arxiv.org",
            "example.com",
        ],
    )
    def test_allows(self, host):
        assert url_fetch.domain_allowed(host, self.ALLOWED) is True

    @pytest.mark.parametrize(
        "host",
        [
            "evilarxiv.org",          # 偽サフィックス（ドット境界でない）
            "arxiv.org.evil.com",     # 別ドメインの配下
            "notarxiv.org",
            "org",
            "arxiv.com",
            "",
            "   ",
        ],
    )
    def test_rejects(self, host):
        assert url_fetch.domain_allowed(host, self.ALLOWED) is False

    def test_empty_allowlist_rejects_everything(self):
        assert url_fetch.domain_allowed("arxiv.org", []) is False

    def test_ignores_blank_entries(self):
        assert url_fetch.domain_allowed("arxiv.org", ["", None, "arxiv.org"]) is True


# ---------------------------------------------------------------------------
# 3. 形式判定（マジックバイトのみ）
# ---------------------------------------------------------------------------


class TestDetectSourceKind:
    def test_pdf_magic(self):
        assert url_fetch.detect_source_kind(b"%PDF-1.7\n...") == "pdf"

    def test_gzip_magic(self):
        assert url_fetch.detect_source_kind(b"\x1f\x8b\x08\x00rest") == "tex_archive"

    @pytest.mark.parametrize(
        "content",
        [
            b"<html><body>404</body></html>",
            b"PK\x03\x04",           # zip は非対応
            b"",
            b"not a pdf %PDF",       # マジックは先頭でなければならない
        ],
    )
    def test_rejects_other_bytes(self, content):
        with pytest.raises(url_fetch.UnsupportedContentError):
            url_fetch.detect_source_kind(content)


# ---------------------------------------------------------------------------
# 4. ファイル名導出
# ---------------------------------------------------------------------------


class TestDeriveFilename:
    def test_prefers_content_disposition(self):
        name = url_fetch.derive_filename(
            "https://arxiv.org/pdf/1711.03050", "pdf",
            'attachment; filename="paper.pdf"',
        )
        assert name == "paper.pdf"

    def test_content_disposition_rfc5987(self):
        name = url_fetch.derive_filename(
            "https://arxiv.org/pdf/1711.03050", "pdf",
            "attachment; filename*=UTF-8''my%20paper.pdf",
        )
        assert name == "my paper.pdf"

    def test_content_disposition_path_is_stripped(self):
        name = url_fetch.derive_filename(
            "https://arxiv.org/pdf/x", "pdf",
            'attachment; filename="../../etc/passwd"',
        )
        assert "/" not in name and ".." not in name.split(".pdf")[0]
        assert name.endswith(".pdf")

    def test_falls_back_to_url_path(self):
        assert url_fetch.derive_filename(
            "https://arxiv.org/pdf/1711.03050", "pdf", None,
        ) == "1711.03050.pdf"

    def test_falls_back_to_download(self):
        assert url_fetch.derive_filename("https://arxiv.org/", "pdf", None) == "download.pdf"

    def test_tex_archive_suffix_is_enforced(self):
        assert url_fetch.derive_filename(
            "https://arxiv.org/src/1711.03050", "tex_archive", None,
        ) == "1711.03050.tar.gz"

    def test_existing_suffix_is_kept(self):
        assert url_fetch.derive_filename(
            "https://arxiv.org/x/paper.tgz", "tex_archive", None,
        ) == "paper.tgz"
        assert url_fetch.derive_filename(
            "https://arxiv.org/x/paper.PDF", "pdf", None,
        ) == "paper.PDF"


# ---------------------------------------------------------------------------
# fetch_source_from_url — フェイク requests / getaddrinfo
# ---------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status_code=200, headers=None, body=b"", chunks=None):
        self.status_code = status_code
        self.headers = dict(headers or {})
        self._chunks = chunks if chunks is not None else [body]
        self.closed = False

    @property
    def is_redirect(self):
        return self.status_code in (301, 302, 303, 307, 308)

    def iter_content(self, chunk_size=None):
        for chunk in self._chunks:
            yield chunk

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, responses):
        # responses: url -> FakeResponse（または単一 FakeResponse）
        self.responses = responses
        self.requested: list[str] = []
        self.kwargs: list[dict] = []
        self.closed = False

    def get(self, url, **kwargs):
        self.requested.append(url)
        self.kwargs.append(kwargs)
        if isinstance(self.responses, dict):
            if url not in self.responses:
                raise AssertionError(f"unexpected URL requested: {url}")
            return self.responses[url]
        return self.responses

    def close(self):
        self.closed = True


@pytest.fixture
def net(monkeypatch):
    """``requests.Session`` と ``socket.getaddrinfo`` を差し替える。"""
    state: dict = {"session": None, "resolve": {}, "default_ip": "93.184.216.34"}

    def _install(responses):
        session = FakeSession(responses)
        state["session"] = session
        monkeypatch.setattr(url_fetch.requests, "Session", lambda: session)
        return session

    def _getaddrinfo(host, port, *args, **kwargs):
        ips = state["resolve"].get(host, [state["default_ip"]])
        if ips == "fail":
            raise url_fetch.socket.gaierror("no such host")
        return [(2, 1, 6, "", (ip, 0)) for ip in ips]

    monkeypatch.setattr(url_fetch.socket, "getaddrinfo", _getaddrinfo)
    state["install"] = _install
    return state


ALLOWED = ["arxiv.org"]
PDF_BYTES = b"%PDF-1.7\nhello"
GZ_BYTES = b"\x1f\x8b\x08\x00payload"


# ---------------------------------------------------------------------------
# 5. 許可リスト・scheme
# ---------------------------------------------------------------------------


class TestAllowlistGate:
    def test_empty_allowlist_is_its_own_error(self, net):
        net["install"](FakeResponse(body=PDF_BYTES))
        with pytest.raises(url_fetch.NoDomainsConfiguredError):
            url_fetch.fetch_source_from_url("https://arxiv.org/pdf/x", [])

    def test_disallowed_domain(self, net):
        net["install"](FakeResponse(body=PDF_BYTES))
        with pytest.raises(url_fetch.DomainNotAllowedError):
            url_fetch.fetch_source_from_url("https://evilarxiv.org/pdf/x", ALLOWED)

    def test_disallowed_domain_is_not_requested(self, net):
        session = net["install"]({})
        with pytest.raises(url_fetch.DomainNotAllowedError):
            url_fetch.fetch_source_from_url("https://evil.com/pdf/x", ALLOWED)
        assert session.requested == []

    @pytest.mark.parametrize(
        "url",
        ["file:///etc/passwd", "ftp://arxiv.org/x", "gopher://arxiv.org", "   "],
    )
    def test_non_http_schemes(self, net, url):
        net["install"]({})
        with pytest.raises(url_fetch.DomainNotAllowedError):
            url_fetch.fetch_source_from_url(url, ALLOWED)

    def test_subdomain_of_allowed_domain_is_fetched(self, net):
        url = "https://export.arxiv.org/pdf/x"
        net["install"]({url: FakeResponse(body=PDF_BYTES)})
        result = url_fetch.fetch_source_from_url(url, ALLOWED)
        assert result.source_kind == "pdf"


# ---------------------------------------------------------------------------
# 6. SSRF ガード
# ---------------------------------------------------------------------------


class TestSsrfGuard:
    @pytest.mark.parametrize(
        "ip",
        [
            "127.0.0.1",        # loopback
            "10.0.0.5",         # private
            "192.168.1.10",     # private
            "172.16.0.1",       # private
            "169.254.169.254",  # link-local (cloud metadata)
            "0.0.0.0",          # unspecified
            "::1",              # IPv6 loopback
            "fd00::1",          # IPv6 unique-local
            "fe80::1",          # IPv6 link-local
            "::ffff:127.0.0.1", # IPv4-mapped loopback
        ],
    )
    def test_private_addresses_are_rejected(self, net, ip):
        session = net["install"]({})
        net["resolve"]["arxiv.org"] = [ip]
        with pytest.raises(url_fetch.PrivateAddressError):
            url_fetch.fetch_source_from_url("https://arxiv.org/pdf/x", ALLOWED)
        assert session.requested == [], "内部アドレスへは1度も接続してはならない"

    def test_any_private_address_in_round_robin_is_rejected(self, net):
        """DNS が公開 IP と内部 IP を混ぜて返しても拒否する。"""
        net["install"]({})
        net["resolve"]["arxiv.org"] = ["93.184.216.34", "127.0.0.1"]
        with pytest.raises(url_fetch.PrivateAddressError):
            url_fetch.fetch_source_from_url("https://arxiv.org/pdf/x", ALLOWED)

    def test_unresolvable_host_is_fetch_failure(self, net):
        net["install"]({})
        net["resolve"]["arxiv.org"] = "fail"
        with pytest.raises(url_fetch.FetchFailedError):
            url_fetch.fetch_source_from_url("https://arxiv.org/pdf/x", ALLOWED)

    def test_error_message_does_not_leak_ip(self, net):
        net["install"]({})
        net["resolve"]["arxiv.org"] = ["169.254.169.254"]
        with pytest.raises(url_fetch.PrivateAddressError) as excinfo:
            url_fetch.fetch_source_from_url("https://arxiv.org/pdf/x", ALLOWED)
        assert "169.254" not in str(excinfo.value)


# ---------------------------------------------------------------------------
# 7. リダイレクト（各ホップで再検証）
# ---------------------------------------------------------------------------


class TestRedirects:
    def test_does_not_delegate_redirects_to_requests(self, net):
        url = "https://arxiv.org/pdf/x"
        session = net["install"]({url: FakeResponse(body=PDF_BYTES)})
        url_fetch.fetch_source_from_url(url, ALLOWED)
        assert session.kwargs[0]["allow_redirects"] is False

    def test_follows_allowed_redirect(self, net):
        first = "https://arxiv.org/pdf/x"
        second = "https://export.arxiv.org/pdf/x.pdf"
        net["install"]({
            first: FakeResponse(302, headers={"Location": second}),
            second: FakeResponse(body=PDF_BYTES),
        })
        result = url_fetch.fetch_source_from_url(first, ALLOWED)
        assert result.source_kind == "pdf"
        assert result.filename == "x.pdf"

    def test_relative_redirect_is_resolved(self, net):
        first = "https://arxiv.org/pdf/x"
        second = "https://arxiv.org/final.pdf"
        net["install"]({
            first: FakeResponse(302, headers={"Location": "/final.pdf"}),
            second: FakeResponse(body=PDF_BYTES),
        })
        assert url_fetch.fetch_source_from_url(first, ALLOWED).filename == "final.pdf"

    def test_redirect_to_disallowed_domain_is_rejected(self, net):
        first = "https://arxiv.org/pdf/x"
        session = net["install"]({
            first: FakeResponse(302, headers={"Location": "https://evil.com/payload"}),
        })
        with pytest.raises(url_fetch.DomainNotAllowedError):
            url_fetch.fetch_source_from_url(first, ALLOWED)
        assert session.requested == [first], "不許可ドメインへは接続してはならない"

    def test_redirect_to_private_address_is_rejected(self, net):
        """許可ドメイン → 内部アドレス（メタデータサーバ）への 302 を塞ぐ。"""
        first = "https://arxiv.org/pdf/x"
        second = "https://internal.arxiv.org/latest/meta-data/"
        session = net["install"]({
            first: FakeResponse(302, headers={"Location": second}),
        })
        net["resolve"]["internal.arxiv.org"] = ["169.254.169.254"]
        with pytest.raises(url_fetch.PrivateAddressError):
            url_fetch.fetch_source_from_url(first, ALLOWED)
        assert session.requested == [first]

    def test_too_many_redirects(self, net):
        url = "https://arxiv.org/loop"
        net["install"]({url: FakeResponse(302, headers={"Location": url})})
        with pytest.raises(url_fetch.FetchFailedError):
            url_fetch.fetch_source_from_url(url, ALLOWED)

    def test_redirect_without_location(self, net):
        url = "https://arxiv.org/pdf/x"
        net["install"]({url: FakeResponse(302)})
        with pytest.raises(url_fetch.FetchFailedError):
            url_fetch.fetch_source_from_url(url, ALLOWED)


# ---------------------------------------------------------------------------
# 8. 本文の取得（サイズ上限 / 形式 / ステータス / 通信例外）
# ---------------------------------------------------------------------------


class TestFetchBody:
    def test_pdf(self, net):
        url = "https://arxiv.org/pdf/1711.03050"
        net["install"]({url: FakeResponse(body=PDF_BYTES)})
        result = url_fetch.fetch_source_from_url(url, ALLOWED)
        assert result.content == PDF_BYTES
        assert result.source_kind == "pdf"
        assert result.filename == "1711.03050.pdf"

    def test_tex_archive(self, net):
        url = "https://arxiv.org/src/1711.03050"
        net["install"]({url: FakeResponse(body=GZ_BYTES)})
        result = url_fetch.fetch_source_from_url(url, ALLOWED)
        assert result.content == GZ_BYTES
        assert result.source_kind == "tex_archive"
        assert result.filename == "1711.03050.tar.gz"

    def test_content_type_header_is_ignored(self, net):
        """``Content-Type: application/pdf`` を名乗る HTML は拒否する。"""
        url = "https://arxiv.org/pdf/x"
        net["install"]({url: FakeResponse(
            headers={"Content-Type": "application/pdf"}, body=b"<html>nope</html>",
        )})
        with pytest.raises(url_fetch.UnsupportedContentError):
            url_fetch.fetch_source_from_url(url, ALLOWED)

    def test_gzip_served_as_pdf_url_is_detected_by_magic(self, net):
        url = "https://arxiv.org/pdf/x.pdf"
        net["install"]({url: FakeResponse(body=GZ_BYTES)})
        assert url_fetch.fetch_source_from_url(url, ALLOWED).source_kind == "tex_archive"

    def test_empty_body(self, net):
        url = "https://arxiv.org/pdf/x"
        net["install"]({url: FakeResponse(body=b"")})
        with pytest.raises(url_fetch.UnsupportedContentError):
            url_fetch.fetch_source_from_url(url, ALLOWED)

    def test_http_error_status(self, net):
        url = "https://arxiv.org/pdf/x"
        net["install"]({url: FakeResponse(404, body=b"nope")})
        with pytest.raises(url_fetch.FetchFailedError):
            url_fetch.fetch_source_from_url(url, ALLOWED)

    def test_network_exception(self, net, monkeypatch):
        url = "https://arxiv.org/pdf/x"
        session = net["install"]({})

        def _raise(u, **kwargs):
            raise url_fetch.requests.RequestException("boom")

        monkeypatch.setattr(session, "get", _raise)
        with pytest.raises(url_fetch.FetchFailedError):
            url_fetch.fetch_source_from_url(url, ALLOWED)

    def test_declared_content_length_over_limit(self, net):
        url = "https://arxiv.org/pdf/x"
        session = net["install"]({url: FakeResponse(
            headers={"Content-Length": str(url_fetch.MAX_FETCH_BYTES + 1)},
            body=PDF_BYTES,
        )})
        with pytest.raises(url_fetch.TooLargeError):
            url_fetch.fetch_source_from_url(url, ALLOWED)

    def test_streamed_body_over_limit(self, net, monkeypatch):
        """Content-Length を偽っても、ストリーム読み出し中に上限を強制する。"""
        monkeypatch.setattr(url_fetch, "MAX_FETCH_BYTES", 16)
        url = "https://arxiv.org/pdf/x"
        net["install"]({url: FakeResponse(
            headers={"Content-Length": "4"}, chunks=[b"%PDF" + b"a" * 8, b"b" * 20],
        )})
        with pytest.raises(url_fetch.TooLargeError):
            url_fetch.fetch_source_from_url(url, ALLOWED)

    def test_broken_content_length_is_ignored(self, net):
        url = "https://arxiv.org/pdf/x"
        net["install"]({url: FakeResponse(
            headers={"Content-Length": "not-a-number"}, body=PDF_BYTES,
        )})
        assert url_fetch.fetch_source_from_url(url, ALLOWED).content == PDF_BYTES

    def test_timeout_is_passed(self, net):
        url = "https://arxiv.org/pdf/x"
        session = net["install"]({url: FakeResponse(body=PDF_BYTES)})
        url_fetch.fetch_source_from_url(url, ALLOWED)
        assert session.kwargs[0]["timeout"] == url_fetch.FETCH_TIMEOUT_SECONDS
        assert session.kwargs[0]["stream"] is True

    def test_session_is_closed(self, net):
        url = "https://arxiv.org/pdf/x"
        session = net["install"]({url: FakeResponse(body=PDF_BYTES)})
        url_fetch.fetch_source_from_url(url, ALLOWED)
        assert session.closed is True

    def test_all_errors_share_a_base_class(self):
        for exc in (
            url_fetch.NoDomainsConfiguredError,
            url_fetch.DomainNotAllowedError,
            url_fetch.PrivateAddressError,
            url_fetch.FetchFailedError,
            url_fetch.UnsupportedContentError,
            url_fetch.TooLargeError,
        ):
            assert issubclass(exc, url_fetch.UrlFetchError)


# ---------------------------------------------------------------------------
# 9. 許可リスト CRUD（フェイクセッション）
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class CrudSession:
    def __init__(self, rows=(), delete_hits=True):
        self.rows = list(rows)
        self.delete_hits = delete_hits
        self.calls: list[tuple[str, dict]] = []

    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        self.calls.append((sql, dict(params or {})))
        if sql.startswith("SELECT domain"):
            return _Result(self.rows)
        if sql.startswith("DELETE FROM url_fetch_domains"):
            return _Result([("arxiv.org",)] if self.delete_hits else [])
        return _Result()


class _Stamp:
    def isoformat(self):
        return "2026-08-25T00:00:00+00:00"


class TestAllowlistCrud:
    def test_list_returns_domain_and_created_at(self):
        session = CrudSession(rows=[("arxiv.org", _Stamp()), ("example.com", None)])
        assert url_fetch.list_url_fetch_domains(session) == [
            {"domain": "arxiv.org", "created_at": "2026-08-25T00:00:00+00:00"},
            {"domain": "example.com", "created_at": ""},
        ]

    def test_list_orders_by_domain(self):
        session = CrudSession()
        url_fetch.list_url_fetch_domains(session)
        assert "ORDER BY domain ASC" in session.calls[0][0]

    def test_add_normalizes_and_is_idempotent(self):
        session = CrudSession()
        assert url_fetch.add_url_fetch_domain(session, "HTTPS://ArXiv.org/pdf/", "u-1") == "arxiv.org"
        sql, params = session.calls[0]
        assert "ON CONFLICT (domain) DO NOTHING" in sql
        assert params["domain"] == "arxiv.org"
        assert params["added_by"] == "u-1"

    def test_add_rejects_invalid_domain_before_sql(self):
        session = CrudSession()
        with pytest.raises(ValueError):
            url_fetch.add_url_fetch_domain(session, "localhost", "u-1")
        assert session.calls == []

    def test_remove_returns_true_when_deleted(self):
        session = CrudSession(delete_hits=True)
        assert url_fetch.remove_url_fetch_domain(session, "ARXIV.org") is True
        assert session.calls[0][1]["domain"] == "arxiv.org"

    def test_remove_returns_false_when_absent(self):
        session = CrudSession(delete_hits=False)
        assert url_fetch.remove_url_fetch_domain(session, "arxiv.org") is False

    def test_remove_invalid_domain_is_false_without_sql(self):
        session = CrudSession()
        assert url_fetch.remove_url_fetch_domain(session, "localhost") is False
        assert session.calls == []


# ---------------------------------------------------------------------------
# パーサ差分による allowlist / SSRF バイパス（回帰）
# ---------------------------------------------------------------------------


class TestAuthorityParserDivergence:
    """``urlparse`` と、requests/urllib3 が送信時に使うパーサでホストが食い違う URL。

    回帰: ``https://169.254.169.254\\@arxiv.org/x`` は ``urlparse`` では
    ``arxiv.org``（userinfo は最後の ``@`` で分割）、urllib3 では
    ``169.254.169.254``（``\\`` が authority の終端）になる。ガードは前者を検証し、
    ソケットは後者へ繋ぐため、許可リストと内部アドレス検査の両方がすり抜けていた。
    """

    BYPASS_URL = "https://169.254.169.254\\@arxiv.org/x"

    def test_backslash_authority_is_rejected(self, net):
        session = net["install"](FakeResponse(body=PDF_BYTES))
        net["resolve"]["arxiv.org"] = ["93.184.216.34"]
        with pytest.raises(url_fetch.DomainNotAllowedError):
            url_fetch.fetch_source_from_url(self.BYPASS_URL, ALLOWED)
        # 1リクエストも飛ばさずに拒否する（検証前に接続しない）。
        assert session.requested == []

    def test_backslash_authority_in_redirect_is_rejected(self, net):
        """悪意ある教員が不要なもう一方の経路 — 許可オリジンからの 302。"""
        session = net["install"]({
            "https://arxiv.org/start": FakeResponse(
                status_code=302, headers={"Location": "https://127.0.0.1\\@arxiv.org/admin"}
            ),
        })
        net["resolve"]["arxiv.org"] = ["93.184.216.34"]
        with pytest.raises(url_fetch.DomainNotAllowedError):
            url_fetch.fetch_source_from_url("https://arxiv.org/start", ALLOWED)
        assert session.requested == ["https://arxiv.org/start"]

    def test_plain_userinfo_still_resolves_to_the_real_host(self, net):
        """``@`` だけの URL は従来どおり右側のホストとして扱い、拒否する。"""
        net["install"](FakeResponse(body=PDF_BYTES))
        with pytest.raises(url_fetch.DomainNotAllowedError):
            url_fetch.fetch_source_from_url("https://arxiv.org@evil.com/x", ALLOWED)

    def test_unbalanced_brackets_do_not_escape_as_valueerror(self, net):
        """``parsed.hostname`` の ``ValueError`` を UrlFetchError へ畳む（500 回避）。"""
        net["install"](FakeResponse(body=PDF_BYTES))
        for url in ("https://arxiv.org]/x", "https://a[b].arxiv.org/x", "http://[::1/x"):
            with pytest.raises(url_fetch.UrlFetchError):
                url_fetch.fetch_source_from_url(url, ALLOWED)

    def test_ordinary_url_is_unaffected(self, net):
        """一致するかぎり従来どおり取得できる（過剰拒否していない）。"""
        net["install"](FakeResponse(body=PDF_BYTES))
        result = url_fetch.fetch_source_from_url("https://arxiv.org/pdf/1234.5678", ALLOWED)
        assert result.source_kind == "pdf"


class TestSharedAddressSpaceIsNotPublic:
    """100.64.0.0/10（CGNAT / 共有アドレス空間）は外部扱いしない。

    回帰: Python 3.13 で当該レンジが ``is_private`` から外れ、
    ``is_loopback / is_link_local / is_reserved / is_multicast / is_unspecified``
    のいずれにも該当しなくなったため、個別列挙だけの判定をすり抜けていた。
    """

    def test_cgnat_address_is_rejected(self, net):
        session = net["install"](FakeResponse(body=PDF_BYTES))
        net["resolve"]["arxiv.org"] = ["100.64.1.1"]
        with pytest.raises(url_fetch.PrivateAddressError):
            url_fetch.fetch_source_from_url("https://arxiv.org/x", ALLOWED)
        assert session.requested == []

    def test_public_addresses_are_still_allowed(self, net):
        for ip in ("93.184.216.34", "8.8.8.8", "2606:4700:4700::1111"):
            net["install"](FakeResponse(body=PDF_BYTES))
            net["resolve"]["arxiv.org"] = [ip]
            assert url_fetch.fetch_source_from_url("https://arxiv.org/x", ALLOWED).source_kind == "pdf"


class TestBodyReadFailuresAreFolded:
    def test_stream_failure_becomes_fetch_failed(self, net):
        """本文読み出し中の requests 例外も UrlFetchError に畳む（500 回避）。"""

        class _BrokenBody(FakeResponse):
            def iter_content(self, chunk_size=None):
                yield b"%PDF-1.7"
                raise url_fetch.requests.exceptions.ChunkedEncodingError("boom")

        net["install"](_BrokenBody(body=b""))
        with pytest.raises(url_fetch.FetchFailedError):
            url_fetch.fetch_source_from_url("https://arxiv.org/x", ALLOWED)
