"""arXiv の取得形式の解決（TeX 既定・PDF への同期フォールバック・呼び出し予算 ≤ 2）。

正本: ``docs/features/paper_radar_design.md`` §15.9 / ``docs/features/url_material_upload_design.md``
の追補（arXiv URL の書き換えとフォールバック）。個人化記録 P-0007（外部 API は一連の操作で
1〜2 回）。

**外部に一切接続しない**: ``url_fetch.fetch_source_from_url`` を差し替えて呼び出し回数と URL を
記録する。TeX として読めるかの判定は ``tex_archive.load_tex_archive``（純関数）を本物のまま使う。
"""

from __future__ import annotations

import gzip
import io
import sys
import tarfile
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import url_fetch  # noqa: E402
import source_resolution as sr  # noqa: E402

PDF_BYTES = b"%PDF-1.7\nhello"
ALLOWED = ["arxiv.org"]


def _tex_tar_gz() -> bytes:
    body = b"\\documentclass{article}\n\\title{T}\n\\begin{document}\nHello\n\\end{document}\n"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo("main.tex")
        info.size = len(body)
        tar.addfile(info, io.BytesIO(body))
    return buf.getvalue()


def _non_tex_gzip() -> bytes:
    return gzip.compress(b"this is not a TeX source at all, just some bytes")


def _fetched(content: bytes, kind: str, name: str = "x") -> url_fetch.FetchedSource:
    return url_fetch.FetchedSource(content=content, source_kind=kind, filename=name)


@pytest.fixture
def fetcher(monkeypatch):
    """URL の部分文字列 → 返す FetchedSource / 送出する例外 の表で動く偽の取得。"""
    state = {"calls": [], "table": {}}

    def _fetch(url, allowed_domains):
        state["calls"].append(url)
        for token, outcome in state["table"].items():
            if token in url:
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome
        raise AssertionError(f"unexpected fetch: {url}")

    monkeypatch.setattr(url_fetch, "fetch_source_from_url", _fetch)
    return state


class TestFetchArxivSource:
    def test_readable_tex_is_one_call(self, fetcher):
        fetcher["table"] = {"/src/": _fetched(_tex_tar_gz(), "tex_archive", "a.tar.gz")}
        r = sr.fetch_arxiv_source("2608.20293", source_format="tex", allowed_domains=ALLOWED)
        assert fetcher["calls"] == ["https://arxiv.org/src/2608.20293"]
        assert r.effective_format == "tex"
        assert r.fell_back is False
        assert r.source_url == "https://arxiv.org/src/2608.20293"

    def test_pdf_only_submission_is_absorbed_with_one_call(self, fetcher):
        """PDF だけを投稿した論文は arXiv が /src/ で PDF を返す — 取り直さない。"""
        fetcher["table"] = {"/src/": _fetched(PDF_BYTES, "pdf")}
        r = sr.fetch_arxiv_source("2608.20293", source_format="tex", allowed_domains=ALLOWED)
        assert len(fetcher["calls"]) == 1
        assert r.effective_format == "pdf"
        assert r.fell_back is False
        assert r.source_url == "https://arxiv.org/src/2608.20293"

    def test_non_tex_gzip_falls_back_to_pdf(self, fetcher):
        fetcher["table"] = {
            "/src/": _fetched(_non_tex_gzip(), "tex_archive"),
            "/pdf/": _fetched(PDF_BYTES, "pdf"),
        }
        r = sr.fetch_arxiv_source("2608.20293", source_format="tex", allowed_domains=ALLOWED)
        assert fetcher["calls"] == [
            "https://arxiv.org/src/2608.20293", "https://arxiv.org/pdf/2608.20293",
        ]
        assert r.fell_back is True
        assert r.effective_format == "pdf"
        # 出所は実際にバイト列を返した URL。
        assert r.source_url == "https://arxiv.org/pdf/2608.20293"
        assert len(r.attempts) <= sr.MAX_FETCH_ATTEMPTS

    def test_404_on_src_falls_back_to_pdf(self, fetcher):
        fetcher["table"] = {
            "/src/": url_fetch.FetchFailedError("URLからの取得に失敗しました"),
            "/pdf/": _fetched(PDF_BYTES, "pdf"),
        }
        r = sr.fetch_arxiv_source("2608.20293", source_format="tex", allowed_domains=ALLOWED)
        assert len(fetcher["calls"]) == 2
        assert r.fell_back is True
        assert r.source_url.endswith("/pdf/2608.20293")

    def test_429_never_makes_the_fallback_call(self, fetcher):
        fetcher["table"] = {
            "/src/": url_fetch.RateLimitedError("x"),
            "/pdf/": _fetched(PDF_BYTES, "pdf"),
        }
        with pytest.raises(url_fetch.RateLimitedError) as info:
            sr.fetch_arxiv_source("2608.20293", source_format="tex", allowed_domains=ALLOWED)
        assert fetcher["calls"] == ["https://arxiv.org/src/2608.20293"]
        assert str(info.value) == sr.DETAIL_ARXIV_RATE_LIMITED

    def test_429_on_the_fallback_is_reported_as_rate_limited(self, fetcher):
        fetcher["table"] = {
            "/src/": _fetched(_non_tex_gzip(), "tex_archive"),
            "/pdf/": url_fetch.RateLimitedError("x"),
        }
        with pytest.raises(url_fetch.RateLimitedError) as info:
            sr.fetch_arxiv_source("2608.20293", source_format="tex", allowed_domains=ALLOWED)
        assert len(fetcher["calls"]) == 2
        assert str(info.value) == sr.DETAIL_ARXIV_RATE_LIMITED

    @pytest.mark.parametrize("exc_type", [
        url_fetch.NoDomainsConfiguredError,
        url_fetch.DomainNotAllowedError,
        url_fetch.PrivateAddressError,
    ])
    def test_allowlist_rejections_do_not_fall_back(self, fetcher, exc_type):
        fetcher["table"] = {"/src/": exc_type("no"), "/pdf/": _fetched(PDF_BYTES, "pdf")}
        with pytest.raises(exc_type):
            sr.fetch_arxiv_source("2608.20293", source_format="tex", allowed_domains=ALLOWED)
        assert len(fetcher["calls"]) == 1

    def test_explicit_pdf_is_one_call_to_pdf(self, fetcher):
        fetcher["table"] = {"/pdf/": _fetched(PDF_BYTES, "pdf")}
        r = sr.fetch_arxiv_source("2608.20293", source_format="pdf", allowed_domains=ALLOWED)
        assert fetcher["calls"] == ["https://arxiv.org/pdf/2608.20293"]
        assert r.fell_back is False

    def test_unspecified_format_defaults_to_tex(self, fetcher):
        fetcher["table"] = {"/src/": _fetched(PDF_BYTES, "pdf")}
        sr.fetch_arxiv_source("2608.20293", source_format=None, allowed_domains=ALLOWED)
        assert fetcher["calls"] == ["https://arxiv.org/src/2608.20293"]

    @pytest.mark.parametrize("src_outcome", [
        "non_tex", "404", "too_large", "unsupported",
    ])
    def test_call_count_never_exceeds_two(self, fetcher, src_outcome):
        src = {
            "non_tex": _fetched(_non_tex_gzip(), "tex_archive"),
            "404": url_fetch.FetchFailedError("f"),
            "too_large": url_fetch.TooLargeError("big"),
            "unsupported": url_fetch.UnsupportedContentError("u"),
        }[src_outcome]
        fetcher["table"] = {"/src/": src, "/pdf/": url_fetch.FetchFailedError("f")}
        with pytest.raises(url_fetch.UrlFetchError):
            sr.fetch_arxiv_source("2608.20293", source_format="tex", allowed_domains=ALLOWED)
        assert len(fetcher["calls"]) <= sr.MAX_FETCH_ATTEMPTS == 2


class TestArxivUrlRewriting:
    @pytest.mark.parametrize("url,expected", [
        ("https://arxiv.org/abs/2608.20293", ("2608.20293", None)),
        ("https://arxiv.org/abs/2608.20293v2", ("2608.20293v2", None)),
        ("https://arxiv.org/pdf/2608.20293v2", ("2608.20293v2", "pdf")),
        ("https://arxiv.org/pdf/2608.20293.pdf", ("2608.20293", "pdf")),
        ("https://arxiv.org/src/2608.20293", ("2608.20293", "tex")),
        ("https://www.arxiv.org/e-print/hep-ph/9901234", ("hep-ph/9901234", "tex")),
    ])
    def test_arxiv_urls_are_recognised(self, url, expected):
        assert sr.arxiv_ref_from_url(url) == expected

    @pytest.mark.parametrize("url", [
        "https://example.com/abs/2608.20293",
        "https://arxiv.org/list/astro-ph",
        "",
        "not a url",
    ])
    def test_non_arxiv_urls_are_left_alone(self, url):
        assert sr.arxiv_ref_from_url(url) is None

    def test_abs_url_without_format_goes_to_tex(self, fetcher):
        fetcher["table"] = {"/src/": _fetched(_tex_tar_gz(), "tex_archive")}
        r = sr.fetch_url_source(
            "https://arxiv.org/abs/2608.20293", source_format=None, allowed_domains=ALLOWED,
        )
        assert fetcher["calls"] == ["https://arxiv.org/src/2608.20293"]
        assert r.effective_format == "tex"

    def test_pdf_url_without_format_honours_the_path(self, fetcher):
        fetcher["table"] = {"/pdf/": _fetched(PDF_BYTES, "pdf")}
        sr.fetch_url_source(
            "https://arxiv.org/pdf/2608.20293", source_format=None, allowed_domains=ALLOWED,
        )
        assert fetcher["calls"] == ["https://arxiv.org/pdf/2608.20293"]

    def test_explicit_format_wins_over_the_path(self, fetcher):
        fetcher["table"] = {"/src/": _fetched(_tex_tar_gz(), "tex_archive")}
        sr.fetch_url_source(
            "https://arxiv.org/pdf/2608.20293", source_format="tex", allowed_domains=ALLOWED,
        )
        assert fetcher["calls"] == ["https://arxiv.org/src/2608.20293"]

    def test_non_arxiv_url_is_fetched_once_as_is(self, fetcher):
        fetcher["table"] = {"example.com": _fetched(PDF_BYTES, "pdf")}
        r = sr.fetch_url_source(
            "https://example.com/paper.pdf", source_format="tex", allowed_domains=["example.com"],
        )
        assert fetcher["calls"] == ["https://example.com/paper.pdf"]
        assert r.source_url == "https://example.com/paper.pdf"
        assert r.fell_back is False


class TestRateLimitWordingAndLayering:
    def test_first_sentence_matches_the_radar_block_note(self):
        from core.paper_discovery import radar

        first = radar.NOTE_ARXIV_BLOCKED.split("。")[0]
        assert sr.DETAIL_ARXIV_RATE_LIMITED.startswith(first + "。")

    def test_no_numbers_in_the_rate_limit_fact(self):
        import re

        assert re.search(r"\d", sr.DETAIL_ARXIV_RATE_LIMITED) is None

    def test_rate_limited_error_is_a_fetch_failure(self):
        assert issubclass(url_fetch.RateLimitedError, url_fetch.FetchFailedError)

    def test_resolver_does_not_import_the_search_client_or_llm(self):
        from tests.guardrail_helpers import assert_source_does_not_import

        src = (BACKEND / "api" / "source_resolution.py").read_text(encoding="utf-8")
        assert_source_does_not_import(
            src,
            ["core.paper_discovery.arxiv_client", "arxiv_client", "core.llm", "requests"],
            context="api/source_resolution.py",
        )

    def test_url_fetch_maps_429_to_rate_limited(self, monkeypatch):
        """url_fetch は 429 を RateLimitedError で表す（接続はしない — session を差し替え）。"""

        class _Resp:
            status_code = 429
            is_redirect = False
            headers: dict = {}

            def close(self):
                pass

        class _Session:
            def get(self, *a, **k):
                return _Resp()

            def close(self):
                pass

        monkeypatch.setattr(url_fetch.requests, "Session", _Session)
        monkeypatch.setattr(url_fetch, "_validated_target", lambda url, domains: url)
        with pytest.raises(url_fetch.RateLimitedError):
            url_fetch.fetch_source_from_url("https://arxiv.org/src/2608.20293", ["arxiv.org"])


class TestHostPreservingRewrite:
    """書き換えるのはパスだけ — 貼られたホストを保つ（2026-10-01 レビュー m10）。"""

    def test_export_host_is_kept_for_src(self, fetcher):
        fetcher["table"] = {"/src/": _fetched(_tex_tar_gz(), "tex_archive")}
        r = sr.fetch_url_source(
            "https://export.arxiv.org/abs/2608.20293", source_format=None,
            allowed_domains=["export.arxiv.org"],
        )
        assert fetcher["calls"] == ["https://export.arxiv.org/src/2608.20293"]
        assert r.source_url == "https://export.arxiv.org/src/2608.20293"

    def test_export_host_is_kept_for_the_pdf_fallback(self, fetcher):
        fetcher["table"] = {
            "/src/": url_fetch.FetchFailedError("404"),
            "/pdf/": _fetched(PDF_BYTES, "pdf"),
        }
        r = sr.fetch_url_source(
            "https://export.arxiv.org/abs/2608.20293", source_format=None,
            allowed_domains=["export.arxiv.org"],
        )
        assert fetcher["calls"] == [
            "https://export.arxiv.org/src/2608.20293",
            "https://export.arxiv.org/pdf/2608.20293",
        ]
        assert r.fell_back is True

    def test_real_allowlist_check_passes_for_export_only(self, monkeypatch):
        """許可リストが export.arxiv.org だけでも、書き換え後の URL が照合を通る。"""
        seen = []

        def _fetch(url, allowed_domains):
            host = url.split("/")[2]
            assert url_fetch.domain_allowed(host, allowed_domains)
            seen.append(url)
            return _fetched(PDF_BYTES, "pdf")

        monkeypatch.setattr(url_fetch, "fetch_source_from_url", _fetch)
        sr.fetch_url_source(
            "https://export.arxiv.org/pdf/2608.20293", source_format=None,
            allowed_domains=["export.arxiv.org"],
        )
        assert seen == ["https://export.arxiv.org/pdf/2608.20293"]

    def test_ingest_path_keeps_the_default_host(self, fetcher):
        fetcher["table"] = {"/src/": _fetched(_tex_tar_gz(), "tex_archive")}
        sr.fetch_arxiv_source("2608.20293", source_format="tex", allowed_domains=ALLOWED)
        assert fetcher["calls"] == ["https://arxiv.org/src/2608.20293"]
