"""URL指定による教材取得 — ガードレール（構造的な不変条項）。

``tests/guardrail_helpers.py`` を使い、レビューでは守り切れない4点をソース走査で固定する:

  1. **core が FastAPI を import しない**（開発ルール2。取得ロジックを HTTP 層から
     切り離してテスト可能に保つ）
  2. **migration 070 にシード INSERT が無い**（毎起動・番号順に全ファイルが再実行される
     方式のため、初期ドメインを INSERT すると管理者が削除した行が再起動で復活する。
     初期状態は空 = 機能無効が仕様）
  3. **許可リスト照合なしに取得できる公開関数が無い**（``fetch_source_from_url`` の
     ``allowed_domains`` が必須引数であること = SSRF ガードの入口を1本に保つ）
  4. **リダイレクトを HTTP クライアントに自動追跡させない**（``allow_redirects=True``
     が無いこと。自動追跡させると「許可ドメイン → 内部アドレス」の 302 を検証できない）
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
for _path in (str(BACKEND), str(BACKEND / "api")):
    if _path not in sys.path:
        sys.path.insert(0, str(_path))

from core import url_fetch
from tests.guardrail_helpers import (
    assert_source_does_not_import,
    assert_source_forbids,
    extract_function_source,
    read_migration_sql,
)

_URL_FETCH_PY = BACKEND / "core" / "url_fetch.py"
_MIGRATION_NUMBER = 70


def _source() -> str:
    return _URL_FETCH_PY.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. core は FastAPI を import しない
# ---------------------------------------------------------------------------


class TestCoreIsFrameworkFree:
    def test_module_exists(self):
        assert _URL_FETCH_PY.is_file(), f"expected core module at {_URL_FETCH_PY}"

    def test_does_not_import_fastapi(self):
        assert_source_does_not_import(
            _source(), ["fastapi", "starlette"], context=str(_URL_FETCH_PY),
        )

    def test_does_not_raise_http_exception(self):
        """HTTP ステータスへの写像は API 層の責務（core は例外型で理由を表現する）。"""
        assert_source_forbids(
            _source(), ["HTTPException", "status_code="], context=str(_URL_FETCH_PY),
        )

    def test_does_not_read_environment_variables(self):
        """上限値はモジュール定数（リポジトリ規約: os.getenv 直書き禁止）。"""
        assert_source_forbids(
            _source(), ["os.getenv", "os.environ"], context=str(_URL_FETCH_PY),
        )


# ---------------------------------------------------------------------------
# 2. migration 070 にシード行を入れない
# ---------------------------------------------------------------------------


class TestMigrationHasNoSeed:
    def test_creates_table_idempotently(self):
        sql = read_migration_sql(BACKEND, _MIGRATION_NUMBER)
        assert "CREATE TABLE IF NOT EXISTS url_fetch_domains" in sql

    def test_has_no_insert(self):
        """シード行を入れると、管理者が削除したドメインが再起動のたびに復活する。"""
        sql = read_migration_sql(BACKEND, _MIGRATION_NUMBER)
        statements = " ".join(
            line for line in sql.splitlines() if not line.strip().startswith("--")
        ).upper()
        assert "INSERT" not in statements, (
            "migration 070 にシード INSERT を書かないこと（毎起動再実行のため、"
            "管理者が削除した許可ドメインが復活する）"
        )

    def test_has_no_destructive_ddl(self):
        sql = read_migration_sql(BACKEND, _MIGRATION_NUMBER).upper()
        for term in ("DROP TABLE", "TRUNCATE", "DELETE FROM"):
            assert term not in sql, f"migration 070 に {term} を書かないこと"


# ---------------------------------------------------------------------------
# 3. 許可リスト照合なしに取得できる公開関数が無い
# ---------------------------------------------------------------------------


class TestNoUnguardedFetch:
    def test_allowed_domains_is_required_argument(self):
        sig = inspect.signature(url_fetch.fetch_source_from_url)
        assert "allowed_domains" in sig.parameters, (
            "fetch_source_from_url は allowed_domains を受け取ること"
        )
        param = sig.parameters["allowed_domains"]
        assert param.default is inspect.Parameter.empty, (
            "allowed_domains に既定値を与えないこと（省略できると許可リストを"
            "迂回した取得が書けてしまう）"
        )
        assert param.kind in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        )

    def test_empty_allowlist_has_its_own_error(self):
        """空リストは「不許可」ではなく「機能未設定」として区別される（fail-closed）。"""
        assert issubclass(url_fetch.NoDomainsConfiguredError, url_fetch.UrlFetchError)

    def test_only_one_public_entry_point_performs_http(self):
        """HTTP を実行する公開関数は fetch_source_from_url ただ1つ。

        他の公開関数が ``session.get`` / ``requests.get`` を直接呼べると、許可リスト
        照合を通らない取得経路が増える。
        """
        source = _source()
        # モジュール直下の HTTP 呼び出しは、fetch_source_from_url の本体内だけに現れる。
        fn_src = extract_function_source(source, "fetch_source_from_url")
        assert "session.get(" in fn_src, "前提: 取得は fetch_source_from_url の中で行う"
        outside = source.replace(fn_src, "")
        assert_source_forbids(
            outside, ["requests.get(", "session.get(", "urlopen("],
            context=f"{_URL_FETCH_PY} (fetch_source_from_url の外)",
        )

    def test_public_names_are_the_expected_surface(self):
        public = {
            name for name in dir(url_fetch)
            if not name.startswith("_") and callable(getattr(url_fetch, name))
            and getattr(getattr(url_fetch, name), "__module__", "") == url_fetch.__name__
        }
        expected = {
            "normalize_domain",
            "domain_allowed",
            "list_url_fetch_domains",
            "add_url_fetch_domain",
            "remove_url_fetch_domain",
            "detect_source_kind",
            "derive_filename",
            "fetch_source_from_url",
            "FetchedSource",
            "UrlFetchError",
            "NoDomainsConfiguredError",
            "DomainNotAllowedError",
            "PrivateAddressError",
            "FetchFailedError",
            "UnsupportedContentError",
            "TooLargeError",
        }
        unexpected = public - expected
        assert unexpected == set(), (
            f"公開 API が増えている: {sorted(unexpected)}。取得経路を増やす場合は"
            "許可リスト照合を必須にしたうえでこの表を更新すること"
        )


# ---------------------------------------------------------------------------
# 4. リダイレクトを自動追跡させない
# ---------------------------------------------------------------------------


class TestRedirectsAreManual:
    def test_does_not_enable_automatic_redirects(self):
        assert_source_forbids(
            _source(), ["allow_redirects=True"], context=str(_URL_FETCH_PY),
        )

    def test_explicitly_disables_redirects(self):
        assert "allow_redirects=False" in _source(), (
            "requests に自動追跡させると、許可ドメインから内部アドレスへの 302 を"
            "検証できない（各ホップで再検証するため手動ループにすること）"
        )

    def test_has_a_redirect_hop_limit(self):
        assert isinstance(url_fetch.MAX_REDIRECTS, int)
        assert 0 < url_fetch.MAX_REDIRECTS <= 10

    def test_has_size_and_timeout_limits(self):
        assert url_fetch.MAX_FETCH_BYTES == 100 * 1024 * 1024
        assert url_fetch.FETCH_TIMEOUT_SECONDS == 60
