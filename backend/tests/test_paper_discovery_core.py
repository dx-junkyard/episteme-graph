"""論文ディスカバリー層 — core（``backend/core/paper_discovery/``）の単体テスト。

設計正本は ``docs/features/paper_discovery_design.md``（PD1〜PD8）、DDL は
``backend/db/071_paper_discovery.sql``。構造的な不変条項の検査は
``test_paper_discovery_guardrails.py`` 側。

検証観点:
  1. ``normalize_arxiv_id`` / ``split_arxiv_ref``（生 ID / version / abs / pdf /
     旧形式 / 不正値）— version 違いを同一論文へ畳むこと
  2. ``build_search_query`` の組み立て（カテゴリのみ / フレーズのみ / 両方 /
     enabled=False の除外 / 著者 / 条件ゼロ）
  3. ``arxiv_client`` の Atom パース（実 HTTP を呼ばず ``_http_get`` を差し替える）
     と 3 秒スロットル（``time`` を差し替えて sleep 量を検証）
  4. ``store`` の SQL 面（フェイクセッションで upsert / revoked 遷移 / 行削除しない）
  5. ``search.run_search`` の注釈（取り込み済み / 見送り済み / 一致フレーズ）
  6. ``vocab`` の fail-soft（1供給元が落ちても他の供給元の結果が返る）

ネットワークにも DB にも一切接続しない。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

BACKEND = Path(__file__).resolve().parents[1]
for _path in (str(BACKEND), str(BACKEND / "api")):
    if _path not in sys.path:
        sys.path.insert(0, str(_path))

from core.paper_discovery import arxiv_client, schema, search as search_mod, store, vocab  # noqa: E402


# ---------------------------------------------------------------------------
# 1. arXiv ID の正規化
# ---------------------------------------------------------------------------


class TestNormalizeArxivId:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            # 生 ID
            ("2608.20293", "2608.20293"),
            ("  2608.20293  ", "2608.20293"),
            ("2608.20293v2", "2608.20293"),
            ("arXiv:2608.20293v11", "2608.20293"),
            ("1711.03050", "1711.03050"),
            # abs / pdf URL
            ("https://arxiv.org/abs/2608.20293", "2608.20293"),
            ("https://arxiv.org/abs/2608.20293v1", "2608.20293"),
            ("http://arxiv.org/abs/2608.20293v3", "2608.20293"),
            ("https://arxiv.org/pdf/2608.20293", "2608.20293"),
            ("https://arxiv.org/pdf/2608.20293v2", "2608.20293"),
            ("https://arxiv.org/pdf/2608.20293v2.pdf", "2608.20293"),
            ("https://www.arxiv.org/abs/2608.20293", "2608.20293"),
            ("arxiv.org/abs/2608.20293", "2608.20293"),
            ("https://arxiv.org/src/1711.03050", "1711.03050"),
            ("https://arxiv.org/abs/2608.20293?context=astro-ph", "2608.20293"),
            # 旧形式
            ("hep-ph/9901234", "hep-ph/9901234"),
            ("hep-ph/9901234v1", "hep-ph/9901234"),
            ("HEP-PH/9901234", "hep-ph/9901234"),
            ("https://arxiv.org/abs/hep-ph/9901234v1", "hep-ph/9901234"),
            ("cond-mat.str-el/0512345", "cond-mat.str-el/0512345"),
            # 5桁連番（2015年以降）
            ("2608.202931", None),  # 6桁は arXiv の ID 形式ではない
        ],
    )
    def test_normalizes(self, raw, expected):
        assert schema.normalize_arxiv_id(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "   ",
            None,
            123,
            "https://example.com/paper.pdf",
            "not-an-id",
            "26.20293",
            "2608.202",
            "https://arxiv.org/",
            "https://arxiv.org/abs/",
        ],
    )
    def test_rejects(self, raw):
        assert schema.normalize_arxiv_id(raw) is None

    def test_version_is_split_off(self):
        assert schema.split_arxiv_ref("https://arxiv.org/abs/2608.20293v7") == (
            "2608.20293",
            7,
        )
        assert schema.split_arxiv_ref("2608.20293") == ("2608.20293", None)

    def test_versions_collapse_to_same_paper(self):
        """v1/v2 は同一論文（重複判定の土台 — 設計書 §4.1）。"""
        ids = {
            schema.normalize_arxiv_id(ref)
            for ref in (
                "2608.20293",
                "2608.20293v1",
                "https://arxiv.org/pdf/2608.20293v9.pdf",
                "arXiv:2608.20293v2",
            )
        }
        assert ids == {"2608.20293"}

    def test_url_builders_use_normalized_id(self):
        assert schema.pdf_url_for("2608.20293") == "https://arxiv.org/pdf/2608.20293"
        assert schema.abs_url_for("2608.20293") == "https://arxiv.org/abs/2608.20293"


# ---------------------------------------------------------------------------
# 2. クエリ組み立て
# ---------------------------------------------------------------------------


class TestBuildSearchQuery:
    def test_categories_only(self):
        q = search_mod.build_search_query(["astro-ph.CO", "astro-ph.GA"], [], [])
        assert q == "(cat:astro-ph.CO OR cat:astro-ph.GA)"

    def test_keyphrases_only(self):
        q = search_mod.build_search_query([], ["dark energy", "w0waCDM"], [])
        assert q == '(all:"dark energy" OR all:"w0waCDM")'

    def test_categories_and_keyphrases_are_and_joined(self):
        q = search_mod.build_search_query(["astro-ph.CO"], ["dark energy"], [])
        assert q == '(cat:astro-ph.CO) AND (all:"dark energy")'

    def test_accepts_object_keyphrases(self):
        """購読保存はオブジェクト配列、検索の条件上書きは文字列配列で来る。"""
        q = search_mod.build_search_query(
            [],
            [
                {"text": "dark energy", "source": "skeleton", "enabled": True},
                {"text": "baryon acoustic oscillations", "source": "cartridge"},
            ],
            [],
        )
        assert q == '(all:"dark energy" OR all:"baryon acoustic oscillations")'

    def test_disabled_keyphrases_are_excluded(self):
        q = search_mod.build_search_query(
            [],
            [
                {"text": "dark energy", "enabled": True},
                {"text": "neutrino mass", "enabled": False},
            ],
            [],
        )
        assert q == '(all:"dark energy")'
        assert "neutrino mass" not in q

    def test_authors(self):
        q = search_mod.build_search_query([], [], ["Doe, Jane"])
        assert q == '(au:"Doe, Jane")'

    def test_all_three_groups(self):
        q = search_mod.build_search_query(
            ["astro-ph.CO"], ["dark energy"], ["Doe, Jane"]
        )
        assert q == '(cat:astro-ph.CO) AND (all:"dark energy") AND (au:"Doe, Jane")'

    def test_empty_conditions_yield_empty_query(self):
        assert search_mod.build_search_query([], [], []) == ""
        assert search_mod.build_search_query(None, None, None) == ""
        assert search_mod.build_search_query([], [{"text": "x", "enabled": False}], []) == ""

    def test_quotes_are_stripped_from_phrases(self):
        q = search_mod.build_search_query([], ['dark "energy"'], [])
        assert q == '(all:"dark energy")'


# ---------------------------------------------------------------------------
# 3. arXiv クライアント（HTTP なし）
# ---------------------------------------------------------------------------


ATOM_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom"
      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
  <opensearch:totalResults>128</opensearch:totalResults>
  <opensearch:startIndex>0</opensearch:startIndex>
  <entry>
    <id>http://arxiv.org/abs/2608.20293v2</id>
    <updated>2026-08-20T10:00:00Z</updated>
    <published>2026-08-15T09:00:00Z</published>
    <title>Constraints on dark energy
      from late-time observations</title>
    <summary>  We derive constraints on the dark energy equation of state.
    </summary>
    <author><name>Jane Doe</name></author>
    <author><name>Rin Sato</name></author>
    <link href="http://arxiv.org/abs/2608.20293v2" rel="alternate" type="text/html"/>
    <link title="pdf" href="http://arxiv.org/pdf/2608.20293v2" rel="related"
          type="application/pdf"/>
    <arxiv:primary_category term="astro-ph.CO"/>
    <category term="astro-ph.CO"/>
    <category term="gr-qc"/>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/hep-ph/9901234v1</id>
    <title>An older paper</title>
    <summary>Old style identifier.</summary>
    <author><name>A. Author</name></author>
    <category term="hep-ph"/>
  </entry>
  <entry>
    <id>http://example.com/not-arxiv</id>
    <title>Unparsable</title>
  </entry>
</feed>
"""


class TestParseAtom:
    def test_total_and_entries(self):
        total, entries = arxiv_client.parse_atom(ATOM_FIXTURE)
        assert total == 128
        # ID を正規化できない3件目は落とす（重複判定も見送りもできないため）
        assert [e.arxiv_id for e in entries] == ["2608.20293", "hep-ph/9901234"]

    def test_entry_fields(self):
        _total, entries = arxiv_client.parse_atom(ATOM_FIXTURE)
        first = entries[0]
        assert first.version == 2
        assert first.title == "Constraints on dark energy from late-time observations"
        assert first.summary.startswith("We derive constraints")
        assert first.authors == ["Jane Doe", "Rin Sato"]
        assert first.categories == ["astro-ph.CO", "gr-qc"]
        assert first.primary_category == "astro-ph.CO"
        assert first.published == "2026-08-15T09:00:00Z"
        assert first.pdf_url == "http://arxiv.org/pdf/2608.20293v2"
        assert first.abs_url == "http://arxiv.org/abs/2608.20293v2"

    def test_missing_links_fall_back_to_derived_urls(self):
        _total, entries = arxiv_client.parse_atom(ATOM_FIXTURE)
        second = entries[1]
        assert second.pdf_url == "https://arxiv.org/pdf/hep-ph/9901234"
        assert second.abs_url == "https://arxiv.org/abs/hep-ph/9901234"

    def test_to_dict_is_json_serializable(self):
        import json

        _total, entries = arxiv_client.parse_atom(ATOM_FIXTURE)
        payload = entries[0].to_dict()
        assert json.loads(json.dumps(payload))["arxiv_id"] == "2608.20293"

    def test_broken_xml_raises(self):
        with pytest.raises(arxiv_client.ArxivApiError):
            arxiv_client.parse_atom("<feed><unclosed>")

    def test_missing_total_falls_back_to_entry_count(self):
        payload = ATOM_FIXTURE.replace(
            "<opensearch:totalResults>128</opensearch:totalResults>", ""
        )
        total, entries = arxiv_client.parse_atom(payload)
        assert total == len(entries) == 2


class TestSearchParams:
    def test_builds_params_and_parses(self, monkeypatch):
        seen: dict[str, Any] = {}

        def fake_http_get(params, timeout):
            seen["params"] = params
            seen["timeout"] = timeout
            return ATOM_FIXTURE

        monkeypatch.setattr(arxiv_client, "_http_get", fake_http_get)
        total, entries = arxiv_client.search("(cat:astro-ph.CO)", max_results=10)

        assert total == 128
        assert len(entries) == 2
        assert seen["params"]["search_query"] == "(cat:astro-ph.CO)"
        assert seen["params"]["max_results"] == 10
        assert seen["params"]["sortBy"] == "submittedDate"
        assert seen["params"]["sortOrder"] == "descending"

    def test_clamps_max_results_and_start(self, monkeypatch):
        seen: dict[str, Any] = {}
        monkeypatch.setattr(
            arxiv_client,
            "_http_get",
            lambda params, timeout: seen.update(params) or ATOM_FIXTURE,
        )
        arxiv_client.search("(cat:x)", start=-5, max_results=99999)
        assert seen["start"] == 0
        assert seen["max_results"] == arxiv_client.MAX_RESULTS_LIMIT

    def test_unknown_sort_falls_back(self, monkeypatch):
        seen: dict[str, Any] = {}
        monkeypatch.setattr(
            arxiv_client,
            "_http_get",
            lambda params, timeout: seen.update(params) or ATOM_FIXTURE,
        )
        arxiv_client.search("(cat:x)", sort_by="popularity", sort_order="sideways")
        assert seen["sortBy"] == "submittedDate"
        assert seen["sortOrder"] == "descending"

    def test_empty_query_never_calls_the_api(self, monkeypatch):
        def boom(params, timeout):  # pragma: no cover — 呼ばれてはいけない
            raise AssertionError("arXiv API must not be called for an empty query")

        monkeypatch.setattr(arxiv_client, "_http_get", boom)
        with pytest.raises(arxiv_client.ArxivApiError):
            arxiv_client.search("   ")

    def test_api_endpoint_is_the_fixed_host(self):
        assert arxiv_client._api_url() == "https://export.arxiv.org/api/query"
        assert schema.ARXIV_API_HOST == "export.arxiv.org"


class _FakeTime:
    """``time`` モジュールの差し替え（monotonic / sleep のみ）。"""

    def __init__(self):
        self.now = 1000.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class TestThrottle:
    @pytest.fixture(autouse=True)
    def _reset(self):
        arxiv_client.reset_throttle()
        yield
        arxiv_client.reset_throttle()

    def test_first_call_does_not_sleep(self, monkeypatch):
        fake = _FakeTime()
        monkeypatch.setattr(arxiv_client, "time", fake)
        arxiv_client._throttle()
        assert fake.slept == []

    def test_second_immediate_call_sleeps_the_remainder(self, monkeypatch):
        fake = _FakeTime()
        monkeypatch.setattr(arxiv_client, "time", fake)
        arxiv_client._throttle()
        arxiv_client._throttle()
        assert fake.slept == [pytest.approx(arxiv_client._MIN_INTERVAL_SECONDS)]
        assert arxiv_client._MIN_INTERVAL_SECONDS == 3.0

    def test_partial_wait(self, monkeypatch):
        fake = _FakeTime()
        monkeypatch.setattr(arxiv_client, "time", fake)
        arxiv_client._throttle()
        fake.now += 1.0
        arxiv_client._throttle()
        assert fake.slept == [pytest.approx(2.0)]

    def test_no_sleep_after_enough_time(self, monkeypatch):
        fake = _FakeTime()
        monkeypatch.setattr(arxiv_client, "time", fake)
        arxiv_client._throttle()
        fake.now += 10.0
        arxiv_client._throttle()
        assert fake.slept == []

    def test_http_get_goes_through_the_throttle(self, monkeypatch):
        calls: list[str] = []
        monkeypatch.setattr(arxiv_client, "_throttle", lambda: calls.append("throttle"))

        class _Resp:
            status_code = 200
            text = ATOM_FIXTURE

        monkeypatch.setattr(
            arxiv_client.requests, "get", lambda url, params=None, timeout=None: _Resp()
        )
        arxiv_client._http_get({"search_query": "x"}, 5.0)
        assert calls == ["throttle"]

    def test_non_200_raises_without_internal_detail(self, monkeypatch):
        monkeypatch.setattr(arxiv_client, "_throttle", lambda: None)

        class _Resp:
            status_code = 503
            text = "internal backend 10.0.0.5 exploded"

        monkeypatch.setattr(
            arxiv_client.requests, "get", lambda url, params=None, timeout=None: _Resp()
        )
        with pytest.raises(arxiv_client.ArxivApiError) as excinfo:
            arxiv_client._http_get({"search_query": "x"}, 5.0)
        assert "10.0.0.5" not in str(excinfo.value)


# ---------------------------------------------------------------------------
# 4. store（フェイクセッション）
# ---------------------------------------------------------------------------


class FakeResult:
    def __init__(self, rows=None):
        self._rows = list(rows or [])

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class DiscoveryFake:
    """``paper_discovery_*`` テーブルのインメモリ実装（セッション兼テーブル）。"""

    def __init__(self):
        self.subscriptions: dict[str, dict] = {}
        self.dismissals: dict[tuple[str, str], dict] = {}
        self.documents: list[dict] = []
        self.courses: list[dict] = []
        self.components: list[dict] = []
        self.calls: list[tuple[str, dict]] = []

    # -- session protocol --
    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        p = dict(params or {})
        self.calls.append((sql, p))
        return self._dispatch(sql, p)

    def commit(self):  # pragma: no cover — 呼ばれない想定
        raise AssertionError("core must not commit; that is the API layer's job")

    def close(self):
        pass

    @property
    def sql_log(self) -> str:
        return "\n".join(sql for sql, _ in self.calls)

    # -- dispatch --
    def _dispatch(self, sql: str, p: dict) -> FakeResult:
        if "paper_discovery_subscriptions" in sql:
            return self._subscriptions(sql, p)
        if "paper_discovery_dismissals" in sql:
            return self._dismissals(sql, p)
        if "FROM documents" in sql and "source_url" in sql:
            return FakeResult([(d.get("source_url"),) for d in self.documents])
        if "FROM documents" in sql:
            wanted = set(p.get("material_ids") or [])
            return FakeResult(
                [
                    (d["id"], d.get("source_path") or "")
                    for d in self.documents
                    if d.get("source_path") in wanted
                ]
            )
        if "FROM learning_courses" in sql:
            return FakeResult(
                [
                    (c["data"],)
                    for c in self.courses
                    if (c.get("data") or {}).get("cartridge_id") == p.get("domain_key")
                ]
            )
        if "FROM theory_components" in sql:
            refs = set(p.get("refs") or [])
            statuses = set(p.get("statuses") or [])
            names = sorted(
                {
                    c["name"]
                    for c in self.components
                    if c.get("document_id") in refs
                    and c.get("review_status") in statuses
                    and c.get("name")
                }
            )
            return FakeResult([(n,) for n in names])
        raise AssertionError(f"unhandled SQL: {sql}")

    def _subscriptions(self, sql: str, p: dict) -> FakeResult:
        import json as _json

        if sql.startswith("INSERT INTO paper_discovery_subscriptions"):
            row = self.subscriptions.setdefault(p["domain_key"], {})
            row.update(
                {
                    "arxiv_categories": list(p["arxiv_categories"]),
                    "keyphrases": _json.loads(p["keyphrases"]),
                    "followed_authors": _json.loads(p["followed_authors"]),
                    "updated_by": p.get("updated_by"),
                    "updated_at": "now",
                }
            )
            row.setdefault("last_checked_at", None)
            return FakeResult()
        if sql.startswith("UPDATE paper_discovery_subscriptions"):
            row = self.subscriptions.get(p["domain_key"])
            if row is not None:
                row["last_checked_at"] = "checked"
            return FakeResult()
        if sql.startswith("SELECT"):
            keys = (
                [p["domain_key"]]
                if "domain_key = :domain_key" in sql
                else sorted(self.subscriptions)
            )
            rows = []
            for key in keys:
                row = self.subscriptions.get(key)
                if row is None:
                    continue
                rows.append(
                    (
                        key,
                        row["arxiv_categories"],
                        row["keyphrases"],
                        row["followed_authors"],
                        row.get("updated_by"),
                        row.get("updated_at"),
                        row.get("last_checked_at"),
                    )
                )
            return FakeResult(rows)
        raise AssertionError(f"unhandled subscription SQL: {sql}")

    def _dismissals(self, sql: str, p: dict) -> FakeResult:
        key = (p.get("domain_key"), p.get("arxiv_id"))
        if sql.startswith("INSERT INTO paper_discovery_dismissals"):
            self.dismissals[key] = {
                "dismissed_by": p.get("user_id"),
                "dismissed_at": "now",
                "revoked": False,
            }
            return FakeResult()
        if sql.startswith("UPDATE paper_discovery_dismissals"):
            row = self.dismissals.get(key)
            if row is None:
                return FakeResult()
            row["revoked"] = True
            return FakeResult([(key[0], key[1])])
        if "SELECT arxiv_id FROM paper_discovery_dismissals" in sql:
            return FakeResult(
                [
                    (aid,)
                    for (dom, aid), row in sorted(self.dismissals.items())
                    if dom == p.get("domain_key") and not row["revoked"]
                ]
            )
        if sql.startswith("SELECT arxiv_id, dismissed_by"):
            return FakeResult(
                [
                    (aid, row["dismissed_by"], row["dismissed_at"], row["revoked"])
                    for (dom, aid), row in sorted(self.dismissals.items())
                    if dom == p.get("domain_key")
                ]
            )
        raise AssertionError(f"unhandled dismissal SQL: {sql}")


class TestStoreSubscriptions:
    def test_upsert_then_read_back(self):
        session = DiscoveryFake()
        saved = store.upsert_subscription(
            session,
            "astrophysics",
            arxiv_categories=["astro-ph.CO", " astro-ph.GA "],
            keyphrases=[
                {"text": "dark energy", "source": "skeleton"},
                "manual phrase",
            ],
            followed_authors=["Doe, Jane"],
            updated_by="11111111-1111-1111-1111-111111111111",
        )
        assert saved["arxiv_categories"] == ["astro-ph.CO", "astro-ph.GA"]
        assert saved["keyphrases"] == [
            {"text": "dark energy", "source": "skeleton", "enabled": True},
            {"text": "manual phrase", "source": "manual", "enabled": True},
        ]
        assert saved["followed_authors"] == ["Doe, Jane"]

        read = store.get_subscription(session, "astrophysics")
        assert read["keyphrases"] == saved["keyphrases"]
        assert store.list_subscriptions(session)[0]["domain_key"] == "astrophysics"

    def test_unknown_source_falls_back_to_manual(self):
        session = DiscoveryFake()
        saved = store.upsert_subscription(
            session,
            "d",
            keyphrases=[{"text": "x", "source": "llm_generated"}],
        )
        assert saved["keyphrases"][0]["source"] == "manual"
        assert saved["keyphrases"][0]["source"] in schema.KEYPHRASE_SOURCES

    def test_disabled_phrases_are_persisted(self):
        """外した状態も保持する（P4）。"""
        session = DiscoveryFake()
        saved = store.upsert_subscription(
            session, "d", keyphrases=[{"text": "x", "enabled": False}]
        )
        assert saved["keyphrases"] == [
            {"text": "x", "source": "manual", "enabled": False}
        ]

    def test_empty_domain_key_rejected(self):
        with pytest.raises(ValueError):
            store.upsert_subscription(DiscoveryFake(), "  ")

    def test_get_subscription_missing_returns_none(self):
        assert store.get_subscription(DiscoveryFake(), "nope") is None
        assert store.get_subscription(DiscoveryFake(), "") is None

    def test_touch_last_checked_does_not_create_a_row(self):
        session = DiscoveryFake()
        store.touch_last_checked(session, "astrophysics")
        assert store.get_subscription(session, "astrophysics") is None


class TestStoreDismissals:
    def test_dismiss_normalizes_the_id(self):
        session = DiscoveryFake()
        row = store.dismiss(session, "d", "https://arxiv.org/abs/2608.20293v2", "u")
        assert row == {"domain_key": "d", "arxiv_id": "2608.20293", "revoked": False}
        assert store.dismissed_ids(session, "d") == {"2608.20293"}

    def test_restore_is_a_revoked_transition_not_a_delete(self):
        session = DiscoveryFake()
        store.dismiss(session, "d", "2608.20293", "u")
        restored = store.restore(session, "d", "2608.20293v3", "u")

        assert restored == {"domain_key": "d", "arxiv_id": "2608.20293", "revoked": True}
        assert store.dismissed_ids(session, "d") == set()
        # 行は残る（履歴を落とさない — P4）
        assert store.list_dismissals(session, "d") == [
            {
                "domain_key": "d",
                "arxiv_id": "2608.20293",
                "dismissed_by": "u",
                "dismissed_at": "now",
                "revoked": True,
            }
        ]
        assert "DELETE" not in session.sql_log

    def test_restore_missing_row_returns_none(self):
        assert store.restore(DiscoveryFake(), "d", "2608.20293") is None

    def test_dismiss_again_after_restore(self):
        session = DiscoveryFake()
        store.dismiss(session, "d", "2608.20293")
        store.restore(session, "d", "2608.20293")
        store.dismiss(session, "d", "2608.20293")
        assert store.dismissed_ids(session, "d") == {"2608.20293"}

    @pytest.mark.parametrize("bad", ["", "   ", "not-an-id", None])
    def test_invalid_arxiv_id_rejected(self, bad):
        with pytest.raises(ValueError):
            store.dismiss(DiscoveryFake(), "d", bad)


# ---------------------------------------------------------------------------
# 5. run_search の注釈
# ---------------------------------------------------------------------------


class TestRunSearch:
    def _session_with_subscription(self) -> DiscoveryFake:
        session = DiscoveryFake()
        store.upsert_subscription(
            session,
            "astrophysics",
            arxiv_categories=["astro-ph.CO"],
            keyphrases=[{"text": "dark energy", "source": "skeleton"}],
        )
        return session

    def test_annotates_status_and_matches(self, monkeypatch):
        session = self._session_with_subscription()
        session.documents = [
            {"id": "doc-1", "source_url": "https://arxiv.org/abs/2608.20293v1"}
        ]
        store.dismiss(session, "astrophysics", "hep-ph/9901234")
        monkeypatch.setattr(
            search_mod.arxiv_client, "search", lambda *a, **k: arxiv_client.parse_atom(ATOM_FIXTURE)
        )

        result = search_mod.run_search(session, "astrophysics")

        assert result["query"] == '(cat:astro-ph.CO) AND (all:"dark energy")'
        assert result["total"] == 128
        assert result["closed_world_note"] == search_mod.CLOSED_WORLD_NOTE
        statuses = {c["arxiv_id"]: c["status"] for c in result["candidates"]}
        assert statuses == {"2608.20293": "ingested", "hep-ph/9901234": "dismissed"}
        assert set(statuses.values()) <= set(schema.CANDIDATE_STATUSES)

        first = result["candidates"][0]
        assert first["matched_keyphrases"] == ["dark energy"]
        assert result["candidates"][1]["matched_keyphrases"] == []

    def test_new_status_when_neither_ingested_nor_dismissed(self, monkeypatch):
        session = self._session_with_subscription()
        monkeypatch.setattr(
            search_mod.arxiv_client, "search", lambda *a, **k: arxiv_client.parse_atom(ATOM_FIXTURE)
        )
        result = search_mod.run_search(session, "astrophysics")
        assert {c["status"] for c in result["candidates"]} == {"new"}

    def test_overrides_take_precedence_without_saving(self, monkeypatch):
        session = self._session_with_subscription()
        monkeypatch.setattr(
            search_mod.arxiv_client, "search", lambda *a, **k: (0, [])
        )
        result = search_mod.run_search(
            session,
            "astrophysics",
            categories=["hep-ph"],
            keyphrases=["form factor"],
        )
        assert result["query"] == '(cat:hep-ph) AND (all:"form factor")'
        # 購読条件は書き換わらない（PD3 — 保存は教員の明示操作のみ）
        assert store.get_subscription(session, "astrophysics")["arxiv_categories"] == [
            "astro-ph.CO"
        ]

    def test_empty_conditions_do_not_call_the_api(self, monkeypatch):
        session = DiscoveryFake()

        def boom(*a, **k):  # pragma: no cover
            raise AssertionError("arXiv API must not be called without conditions")

        monkeypatch.setattr(search_mod.arxiv_client, "search", boom)
        result = search_mod.run_search(session, "unknown-domain")
        assert result["query"] == ""
        assert result["candidates"] == []
        assert result["total"] == 0
        assert result["closed_world_note"] == search_mod.CLOSED_WORLD_NOTE

    def test_last_checked_is_touched(self, monkeypatch):
        session = self._session_with_subscription()
        monkeypatch.setattr(search_mod.arxiv_client, "search", lambda *a, **k: (0, []))
        search_mod.run_search(session, "astrophysics")
        assert store.get_subscription(session, "astrophysics")["last_checked_at"] == "checked"

    def test_no_numeric_scores_in_the_dto(self, monkeypatch):
        session = self._session_with_subscription()
        monkeypatch.setattr(
            search_mod.arxiv_client, "search", lambda *a, **k: arxiv_client.parse_atom(ATOM_FIXTURE)
        )
        result = search_mod.run_search(session, "astrophysics")
        forbidden = ("score", "similarity", "confidence", "relevance", "rank")
        for candidate in result["candidates"]:
            assert not [k for k in candidate if any(f in k.lower() for f in forbidden)]

    def test_ingested_ids_ignore_non_arxiv_urls(self):
        session = DiscoveryFake()
        session.documents = [
            {"id": "d1", "source_url": "https://example.com/paper.pdf"},
            {"id": "d2", "source_url": "https://arxiv.org/pdf/1711.03050v1.pdf"},
        ]
        assert search_mod.ingested_arxiv_ids(session) == {"1711.03050"}


# ---------------------------------------------------------------------------
# 6. vocab（fail-soft）
# ---------------------------------------------------------------------------


class TestKeyphraseCandidates:
    def test_collects_from_all_suppliers_with_source(self, monkeypatch):
        session = DiscoveryFake()
        monkeypatch.setattr(
            vocab, "_skeleton_phrases", lambda s, d: ["dark energy", "structure growth"]
        )
        monkeypatch.setattr(vocab, "_cartridge_phrases", lambda d: ["HQET"])
        monkeypatch.setattr(vocab, "_alias_phrases", lambda s, d: ["CMB lensing"])
        monkeypatch.setattr(vocab, "_component_phrases", lambda s, d: ["form factor"])

        out = vocab.keyphrase_candidates(session, "astrophysics")
        # 供給順は 骨格 → カートリッジ → 別名（教員の確定語彙）→ 部品（VA層 §7 の還流2）。
        assert out == [
            {"text": "dark energy", "source": "skeleton"},
            {"text": "structure growth", "source": "skeleton"},
            {"text": "HQET", "source": "cartridge"},
            {"text": "CMB lensing", "source": "alias"},
            {"text": "form factor", "source": "component"},
        ]
        assert {c["source"] for c in out} <= set(schema.KEYPHRASE_SOURCES)

    def test_first_supplier_wins_on_duplicates(self, monkeypatch):
        monkeypatch.setattr(vocab, "_skeleton_phrases", lambda s, d: ["Dark Energy"])
        monkeypatch.setattr(vocab, "_cartridge_phrases", lambda d: ["dark energy"])
        monkeypatch.setattr(vocab, "_component_phrases", lambda s, d: [])
        out = vocab.keyphrase_candidates(DiscoveryFake(), "d")
        assert out == [{"text": "Dark Energy", "source": "skeleton"}]

    def test_failing_supplier_does_not_break_the_others(self, monkeypatch):
        def boom(*args):
            raise RuntimeError("skeleton store unavailable")

        monkeypatch.setattr(vocab, "_skeleton_phrases", boom)
        monkeypatch.setattr(vocab, "_cartridge_phrases", boom)
        monkeypatch.setattr(vocab, "_component_phrases", lambda s, d: ["form factor"])

        out = vocab.keyphrase_candidates(DiscoveryFake(), "d")
        assert out == [{"text": "form factor", "source": "component"}]

    def test_all_suppliers_failing_yields_empty(self, monkeypatch):
        def boom(*args):
            raise RuntimeError("nope")

        for name in ("_skeleton_phrases", "_cartridge_phrases", "_component_phrases"):
            monkeypatch.setattr(vocab, name, boom)
        assert vocab.keyphrase_candidates(DiscoveryFake(), "d") == []

    def test_limit_and_empty_domain(self, monkeypatch):
        monkeypatch.setattr(vocab, "_skeleton_phrases", lambda s, d: ["a a", "b b", "c c"])
        monkeypatch.setattr(vocab, "_cartridge_phrases", lambda d: ["d d"])
        monkeypatch.setattr(vocab, "_component_phrases", lambda s, d: [])
        assert len(vocab.keyphrase_candidates(DiscoveryFake(), "d", limit=2)) == 2
        assert vocab.keyphrase_candidates(DiscoveryFake(), "") == []

    def test_component_supplier_reads_approved_components_only(self):
        session = DiscoveryFake()
        session.courses = [
            {"data": {"cartridge_id": "astrophysics", "sources": [{"material_id": "m1"}]}},
            {"data": {"cartridge_id": "other", "sources": [{"material_id": "m2"}]}},
        ]
        session.documents = [
            {"id": "doc-1", "source_path": "m1"},
            {"id": "doc-2", "source_path": "m2"},
        ]
        session.components = [
            {"document_id": "doc-1", "name": "growth factor", "review_status": "teacher_approved"},
            {"document_id": "doc-1", "name": "unreviewed thing", "review_status": "teacher_review_required"},
            {"document_id": "doc-2", "name": "other domain", "review_status": "teacher_approved"},
        ]
        assert vocab._component_phrases(session, "astrophysics") == ["growth factor"]

    def test_component_supplier_without_courses_issues_no_component_sql(self):
        session = DiscoveryFake()
        assert vocab._component_phrases(session, "astrophysics") == []
        assert "theory_components" not in session.sql_log


class TestAliasSupplier:
    """VA層 §7 の還流2 — 教員が確定した別名がキーフレーズ供給に入る。"""

    def test_flattens_confirmed_aliases_in_a_deterministic_order(self, monkeypatch):
        monkeypatch.setattr(
            "core.atlas_vectors.store.confirmed_aliases_by_node",
            lambda _session, _domain: {
                "cmb": ["CMB", "宇宙マイクロ波背景"],
                "bao": ["BAO"],
            },
        )
        # node_id 昇順 → その中は store が返す順（normalized 昇順）。
        assert vocab._alias_phrases(DiscoveryFake(), "astrophysics") == [
            "BAO",
            "CMB",
            "宇宙マイクロ波背景",
        ]

    def test_no_aliases_is_a_normal_state(self, monkeypatch):
        monkeypatch.setattr(
            "core.atlas_vectors.store.confirmed_aliases_by_node",
            lambda _session, _domain: {},
        )
        assert vocab._alias_phrases(DiscoveryFake(), "astrophysics") == []

    def test_failure_does_not_break_the_other_suppliers(self, monkeypatch):
        def _boom(*_args, **_kwargs):
            raise RuntimeError("alias table unavailable")

        monkeypatch.setattr(
            "core.atlas_vectors.store.confirmed_aliases_by_node", _boom
        )
        monkeypatch.setattr(vocab, "_skeleton_phrases", lambda s, d: ["dark energy"])
        monkeypatch.setattr(vocab, "_cartridge_phrases", lambda d: [])
        monkeypatch.setattr(vocab, "_component_phrases", lambda s, d: ["form factor"])

        out = vocab.keyphrase_candidates(DiscoveryFake(), "astrophysics")
        assert out == [
            {"text": "dark energy", "source": "skeleton"},
            {"text": "form factor", "source": "component"},
        ]

    def test_supplier_is_registered_between_cartridge_and_component(self):
        order = [name for name, _fn in vocab._SUPPLIERS]
        assert order == ["skeleton", "cartridge", "alias", "component"]
        assert set(order) <= set(schema.KEYPHRASE_SOURCES)
        # "manual"（教員が自分で足したもの）は供給元ではなく、語彙の末尾に残る。
        assert schema.KEYPHRASE_SOURCES[-1] == "manual"
        assert "manual" not in order
