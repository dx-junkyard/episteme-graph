"""コーパスを補う論文 — レンズC「基盤論文」の core
（``core/paper_discovery/citation_client.references_for_arxiv`` /
``reference_cache.py`` / ``foundation.py`` / migration 077）。

正本: ``docs/features/corpus_complement_design.md`` §5.3 / §5.4 / §7（core 項目）/ §2
（CC1 補完の根拠はコーパス構造のみ・CC2 決定論非LLM・CC3 保存は外部事実のキャッシュ
だけ・CC4 数値非表示・CC7 取り込みは既存の弁のみ・CC8 fail-soft）。
親: ``docs/features/paper_discovery_design.md``（PD2 arXiv ID を持つ論文のみ・
PD4 数値スコアなし・PD6 閉世界の正直さ・PD7 外部 API の行儀）。

検証観点:

1. client: 固定ホスト・スロットル・タイムアウト経由 / arXiv ID の無い参照を落とす /
   形違いは空リストへ縮退 / limit の丸め
2. キャッシュ: read-through（新鮮な行は API を呼ばない）/ 失敗の記録 / 行削除なし
3. 集約: ``min_citing_seeds`` の境界・シード自身の除外・status 注釈・決定論の並び順
4. fail-soft: ``fetch_per_call`` の上限と ``pending_seeds`` / 部分失敗 / 全失敗 raise /
   オプトイン off は外部 API を呼ばない
5. DTO に数値キーが無い（CC4 / PD4）
6. migration 077 が冪等・INSERT なし・FK なし・DELETE なし
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _path in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, str(_path))

from core.paper_discovery import (  # noqa: E402
    citation_client,
    citation_search,
    foundation,
    reference_cache,
)
from core.paper_discovery.schema import CitationEntry  # noqa: E402
from tests.guardrail_helpers import read_migration_sql  # noqa: E402

CORE_DIR = BACKEND / "core" / "paper_discovery"
MIGRATION_NUMBER = 77

_SQL_LINE_COMMENT_RE = re.compile(r"--[^\n]*")


def _migration_statements() -> str:
    """migration 077 の SQL 文だけ（解説コメントを落とす）。"""
    return _SQL_LINE_COMMENT_RE.sub("", read_migration_sql(BACKEND, MIGRATION_NUMBER))


# ---------------------------------------------------------------------------
# フェイク
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def fetchall(self):
        return list(self._rows)


class FakeSession:
    """``learning_courses`` / ``documents`` / 見送り / 参照キャッシュの最小実装。

    参照キャッシュは ``{arxiv_id: (entries, status, fresh)}`` の辞書で持ち、TTL の
    経過は ``fresh=False`` で表現する（時計を回さずに read-through を検査する）。
    """

    def __init__(self, *, documents=(), dismissed=(), cache=None, other_ingested=()):
        self.documents = list(documents)
        self.other_ingested = list(other_ingested)
        self.dismissed = set(dismissed)
        self.cache = dict(cache or {})
        self.writes: list[tuple[str, str, int]] = []
        self.calls: list[str] = []

    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        p = dict(params or {})
        self.calls.append(sql)
        if "INSERT INTO paper_discovery_reference_cache" in sql:
            import json as _json

            entries = _json.loads(p["reference_entries"])
            self.cache[p["arxiv_id"]] = (entries, p["fetch_status"], True)
            self.writes.append((p["arxiv_id"], p["fetch_status"], len(entries)))
            return _Result()
        if "FROM paper_discovery_reference_cache" in sql:
            wanted = set(p.get("arxiv_ids") or ())
            want_failed = "fetch_status = 'failed'" in sql
            rows = []
            for arxiv_id, (entries, status, fresh) in sorted(self.cache.items()):
                if arxiv_id not in wanted or not fresh:
                    continue
                if want_failed and status == "failed":
                    rows.append((arxiv_id,))
                elif not want_failed and status == "ok":
                    rows.append((arxiv_id, entries))
            return _Result(rows)
        if "FROM learning_courses" in sql:
            return _Result(
                [({"cartridge_id": p.get("domain_key"), "sources": [{"material_id": "m1"}]},)]
            )
        if "paper_discovery_dismissals" in sql:
            return _Result([(a,) for a in sorted(self.dismissed)])
        if "FROM documents" in sql and "COALESCE(title" in sql:
            return _Result(
                [
                    (d["id"], d.get("title") or "", d.get("source_url") or "")
                    for d in self.documents
                ]
            )
        if "FROM documents" in sql:
            # search.ingested_arxiv_ids: 分野外の document も「取り込み済み」に数える。
            return _Result(
                [(d.get("source_url") or "",) for d in self.documents]
                + [(f"https://arxiv.org/pdf/{a}",) for a in self.other_ingested]
            )
        return _Result()

    def commit(self):  # pragma: no cover - core は commit しない
        raise AssertionError("core must not commit")

    def close(self):
        pass


def _session(*, papers=(), dismissed=(), cache=None, other_ingested=()):
    documents = [
        {
            "id": f"doc-{index}",
            "title": title,
            "source_url": f"https://arxiv.org/pdf/{arxiv_id}",
        }
        for index, (arxiv_id, title) in enumerate(papers)
    ]
    return FakeSession(
        documents=documents,
        dismissed=dismissed,
        cache=cache,
        other_ingested=other_ingested,
    )


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    """既定 off なので、明示的に有効化する（off 経路は専用テストで確認する）。"""
    monkeypatch.setattr(citation_search, "citation_source_enabled", lambda: True)
    citation_client.reset_throttle()
    yield
    citation_client.reset_throttle()


def _stub_references(monkeypatch, mapping):
    """``references_for_arxiv`` を差し替える（値 or 例外）。"""
    calls: list[str] = []

    def _fake(arxiv_id, *, limit=100, timeout=30.0):
        calls.append(arxiv_id)
        value = mapping.get(arxiv_id, [])
        if isinstance(value, Exception):
            raise value
        return list(value)

    monkeypatch.setattr(citation_client, "references_for_arxiv", _fake)
    return calls


def _entry(arxiv_id, seed, title="ref", year=None):
    return CitationEntry(
        arxiv_id=arxiv_id, title=title, summary="s", year=year, seed_arxiv_id=seed
    )


def _run(session, **kwargs):
    params = {"min_citing_seeds": 2, "fetch_per_call": 5, "ttl_days": 30}
    params.update(kwargs)
    return foundation.run_foundation_search(session, "astrophysics", **params)


# ---------------------------------------------------------------------------
# 1. client（PD7 の行儀 / PD2）
# ---------------------------------------------------------------------------


class TestReferencesClient:
    def test_endpoint_is_the_graph_references_api_on_the_fixed_host(self):
        url = citation_client._api_url("2608.20293", citation_client._PATH_REFERENCES)
        assert url == (
            "https://api.semanticscholar.org/graph/v1/paper/arXiv:2608.20293/references"
        )
        # 推薦 API の既定は変わっていない（既存呼び出しの後方互換）。
        assert citation_client._api_url("2608.20293").endswith(
            "/recommendations/v1/papers/forpaper/arXiv:2608.20293"
        )

    def test_http_call_throttles_and_passes_a_timeout(self, monkeypatch):
        seen: dict = {}
        throttled: list[int] = []

        class _Response:
            status_code = 200

            @staticmethod
            def json():
                return {"data": []}

        def _get(url, params=None, timeout=None):
            seen["url"] = url
            seen["params"] = dict(params or {})
            seen["timeout"] = timeout
            return _Response()

        monkeypatch.setattr(citation_client.requests, "get", _get)
        monkeypatch.setattr(citation_client, "_throttle", lambda: throttled.append(1))

        citation_client.references_for_arxiv("arXiv:2608.20293v2", limit=7)
        assert throttled == [1], "すべての HTTP 呼び出しがスロットルを通る"
        assert seen["url"] == (
            "https://api.semanticscholar.org/graph/v1/paper/arXiv:2608.20293/references"
        )
        assert seen["params"] == {"fields": citation_client.REFERENCE_FIELDS, "limit": 7}
        assert seen["timeout"] == citation_client.DEFAULT_TIMEOUT_SECONDS

    def test_limit_is_clamped(self, monkeypatch):
        seen: dict = {}

        class _Response:
            status_code = 200

            @staticmethod
            def json():
                return {"data": []}

        monkeypatch.setattr(citation_client, "_throttle", lambda: None)
        monkeypatch.setattr(
            citation_client.requests,
            "get",
            lambda url, params=None, timeout=None: (seen.update(params or {}), _Response())[1],
        )
        citation_client.references_for_arxiv("2608.20293", limit=10_000)
        assert seen["limit"] == citation_client.MAX_REFERENCE_LIMIT
        citation_client.references_for_arxiv("2608.20293", limit=0)
        assert seen["limit"] == 1

    def test_only_arxiv_backed_references_survive(self):
        payload = {
            "offset": 0,
            "data": [
                {
                    "citedPaper": {
                        "title": "  A   Foundation ",
                        "abstract": "abs",
                        "year": "1998",
                        "authors": [{"name": "Doe, J"}, {"name": ""}],
                        "externalIds": {"ArXiv": "https://arxiv.org/abs/9801.00001v3"},
                    }
                },
                {"citedPaper": {"title": "doi only", "externalIds": {"DOI": "10.1/x"}}},
                {"citedPaper": {"title": "no external ids"}},
                {"citedPaper": None},
                "garbage",
            ],
        }
        entries = citation_client.parse_references(payload, "2601.00001")
        assert len(entries) == 1
        entry = entries[0]
        assert entry.arxiv_id == "9801.00001"
        assert entry.title == "A Foundation"
        assert entry.year == 1998
        # seed は「引用している側」（取り込み済みシード）を指す。
        assert entry.seed_arxiv_id == "2601.00001"
        payload_dict = entry.to_dict()
        assert not any(
            key in payload_dict for key in ("score", "similarity", "relevance", "rank")
        )

    def test_malformed_payload_degrades_to_empty(self):
        assert citation_client.parse_references({}, "s") == []
        assert citation_client.parse_references({"data": None}, "s") == []
        assert citation_client.parse_references({"recommendedPapers": [{}]}, "s") == []

    def test_transport_failures_raise_the_layer_error(self, monkeypatch):
        monkeypatch.setattr(citation_client, "_throttle", lambda: None)

        class _Boom:
            status_code = 500

            @staticmethod
            def json():
                return {}

        monkeypatch.setattr(citation_client.requests, "get", lambda *a, **k: _Boom())
        with pytest.raises(citation_client.CitationApiError):
            citation_client.references_for_arxiv("2608.20293")

    def test_unparsable_id_raises_before_any_request(self, monkeypatch):
        called: list[int] = []
        monkeypatch.setattr(
            citation_client.requests, "get", lambda *a, **k: called.append(1)
        )
        with pytest.raises(citation_client.CitationApiError):
            citation_client.references_for_arxiv("not-an-id")
        assert called == []

    def test_signature_has_no_destination_argument(self):
        import inspect

        params = set(inspect.signature(citation_client.references_for_arxiv).parameters)
        assert not (params & {"url", "host", "endpoint", "base_url", "path"}), sorted(params)


# ---------------------------------------------------------------------------
# 2. キャッシュ（CC3 の例外 — 外部事実の写しだけを保存する）
# ---------------------------------------------------------------------------


class TestReferenceCache:
    def test_get_fresh_returns_only_ok_rows(self):
        seeds = ["2601.00001", "2601.00002", "2601.00003"]
        session = FakeSession(
            cache={
                seeds[0]: ([{"arxiv_id": "9801.00001"}], "ok", True),
                seeds[1]: ([], "failed", True),
                seeds[2]: ([{"arxiv_id": "9801.00002"}], "ok", False),
            }
        )
        fresh = reference_cache.get_fresh(session, seeds, ttl_days=30)
        assert set(fresh) == {seeds[0]}
        assert fresh[seeds[0]] == [{"arxiv_id": "9801.00001"}]
        # 失敗は「読めた」ではないが「TTL 内に試した」ではある。
        assert reference_cache.fresh_failed_ids(session, seeds, ttl_days=30) == {seeds[1]}
        # 正規化前の表記（version 付き・URL）でも同じ行に当たる。
        assert set(reference_cache.get_fresh(session, ["arXiv:2601.00001v2"], ttl_days=30)) == {
            seeds[0]
        }

    def test_empty_input_does_not_touch_the_database(self):
        session = FakeSession()
        assert reference_cache.get_fresh(session, [], ttl_days=30) == {}
        assert reference_cache.fresh_failed_ids(session, [], ttl_days=30) == set()
        assert session.calls == []

    def test_upsert_records_failures_with_an_empty_list(self):
        session = FakeSession()
        reference_cache.upsert(
            session, "2601.00001", [{"arxiv_id": "x"}], fetch_status="failed"
        )
        assert session.writes == [("2601.00001", "failed", 0)]

    def test_upsert_falls_back_to_failed_for_unknown_status(self):
        session = FakeSession()
        reference_cache.upsert(session, "2601.00001", [], fetch_status="bogus")
        assert session.writes == [("2601.00001", "failed", 0)]

    def test_upsert_rejects_an_unparsable_id(self):
        with pytest.raises(ValueError):
            reference_cache.upsert(FakeSession(), "not-an-id", [])

    def test_module_has_no_delete_statement(self):
        src = (CORE_DIR / "reference_cache.py").read_text(encoding="utf-8")
        assert "DELETE FROM" not in src.upper()
        assert "ON CONFLICT (arxiv_id) DO UPDATE" in src


# ---------------------------------------------------------------------------
# 3. 集約（レンズC の本体）
# ---------------------------------------------------------------------------


class TestFoundationAggregation:
    def test_min_citing_seeds_boundary(self, monkeypatch):
        session = _session(papers=[("2601.00001", "seed A"), ("2601.00002", "seed B")])
        _stub_references(
            monkeypatch,
            {
                "2601.00001": [_entry("9801.00001", "2601.00001"), _entry("9801.00009", "2601.00001")],
                "2601.00002": [_entry("9801.00001", "2601.00002")],
            },
        )
        result = _run(session)
        assert [c["arxiv_id"] for c in result["candidates"]] == ["9801.00001"]
        assert result["available"] is True

        # 閾値 1 なら1本だけが引用している論文も浮上する（境界は「以上」）。
        session = _session(papers=[("2601.00001", "seed A"), ("2601.00002", "seed B")])
        _stub_references(
            monkeypatch,
            {
                "2601.00001": [_entry("9801.00001", "2601.00001"), _entry("9801.00009", "2601.00001")],
                "2601.00002": [_entry("9801.00001", "2601.00002")],
            },
        )
        result = _run(session, min_citing_seeds=1)
        assert set(c["arxiv_id"] for c in result["candidates"]) == {
            "9801.00001",
            "9801.00009",
        }

    def test_cited_by_lists_the_citing_seed_titles(self, monkeypatch):
        session = _session(papers=[("2601.00001", "seed A"), ("2601.00002", "seed B")])
        _stub_references(
            monkeypatch,
            {
                "2601.00001": [_entry("9801.00001", "2601.00001")],
                "2601.00002": [_entry("9801.00001", "2601.00002")],
            },
        )
        result = _run(session)
        cited_by = result["candidates"][0]["cited_by"]
        assert cited_by == [
            {"arxiv_id": "2601.00001", "title": "seed A"},
            {"arxiv_id": "2601.00002", "title": "seed B"},
        ]
        assert result["seeds_read"] == [
            {"arxiv_id": "2601.00001", "title": "seed A"},
            {"arxiv_id": "2601.00002", "title": "seed B"},
        ]

    def test_seeds_themselves_are_not_candidates(self, monkeypatch):
        """コーパス内の相互引用は「まだ無い論文」ではない。"""
        session = _session(papers=[("2601.00001", "seed A"), ("2601.00002", "seed B")])
        _stub_references(
            monkeypatch,
            {
                "2601.00001": [_entry("2601.00002", "2601.00001")],
                "2601.00002": [_entry("2601.00001", "2601.00002")],
            },
        )
        result = _run(session, min_citing_seeds=1)
        assert result["candidates"] == []
        assert result["note"] == foundation.NOTE_NO_CANDIDATES

    def test_status_annotation_keeps_ingested_candidates_in_the_list(self, monkeypatch):
        """取り込み済み・見送り済みも一覧から外さず status で示す（PD6）。"""
        session = _session(
            papers=[("2601.00001", "seed A"), ("2601.00002", "seed B")],
            dismissed=["9801.00002"],
            other_ingested=["9801.00001"],
        )
        _stub_references(
            monkeypatch,
            {
                "2601.00001": [
                    _entry("9801.00001", "2601.00001"),
                    _entry("9801.00002", "2601.00001"),
                    _entry("9801.00003", "2601.00001"),
                ],
                "2601.00002": [
                    _entry("9801.00001", "2601.00002"),
                    _entry("9801.00002", "2601.00002"),
                    _entry("9801.00003", "2601.00002"),
                ],
            },
        )
        result = _run(session)
        statuses = {c["arxiv_id"]: c["status"] for c in result["candidates"]}
        assert statuses == {
            "9801.00001": "ingested",
            "9801.00002": "dismissed",
            "9801.00003": "new",
        }

    def test_order_is_deterministic_and_carries_no_numbers(self, monkeypatch):
        """並び順は引用元数の降順 → 年の降順 → ID。件数は DTO に出さない（CC4）。"""
        session = _session(
            papers=[
                ("2601.00001", "seed A"),
                ("2601.00002", "seed B"),
                ("2601.00003", "seed C"),
            ]
        )
        _stub_references(
            monkeypatch,
            {
                "2601.00001": [
                    _entry("9801.00003", "2601.00001", year=2001),
                    _entry("9801.00001", "2601.00001", year=1999),
                    _entry("9801.00002", "2601.00001", year=2005),
                    _entry("9801.00004", "2601.00001", year=None),
                ],
                "2601.00002": [
                    _entry("9801.00003", "2601.00002", year=2001),
                    _entry("9801.00001", "2601.00002", year=1999),
                    _entry("9801.00002", "2601.00002", year=2005),
                    _entry("9801.00004", "2601.00002", year=None),
                ],
                "2601.00003": [_entry("9801.00003", "2601.00003", year=2001)],
            },
        )
        result = _run(session)
        assert [c["arxiv_id"] for c in result["candidates"]] == [
            "9801.00003",  # 3本が引用（最上位）
            "9801.00002",  # 2本 / 2005
            "9801.00001",  # 2本 / 1999
            "9801.00004",  # 2本 / 年不明は末尾
        ]
        for candidate in result["candidates"]:
            assert not any(
                key in candidate
                for key in (
                    "score",
                    "similarity",
                    "confidence",
                    "relevance",
                    "rank",
                    "match_score",
                    "citation_count",
                    "cited_by_count",
                )
            )
            assert all(not isinstance(v, float) for v in candidate.values())


# ---------------------------------------------------------------------------
# 4. read-through・上限・fail-soft
# ---------------------------------------------------------------------------


class TestFoundationFetching:
    def test_fresh_cache_is_not_refetched(self, monkeypatch):
        cached = _entry("9801.00001", "2601.00001").to_dict()
        session = _session(
            papers=[("2601.00001", "seed A"), ("2601.00002", "seed B")],
            cache={
                "2601.00001": ([cached], "ok", True),
                "2601.00002": ([cached], "ok", True),
            },
        )
        calls = _stub_references(monkeypatch, {})
        result = _run(session)
        assert calls == [], "新鮮なキャッシュがあるシードは外部 API を呼ばない（PD7）"
        assert session.writes == [], "読むだけのときは書かない"
        assert [c["arxiv_id"] for c in result["candidates"]] == ["9801.00001"]
        assert result["pending_seeds"] is False

    def test_stale_cache_is_refetched_and_written_back(self, monkeypatch):
        stale = _entry("9801.00009", "2601.00001").to_dict()
        session = _session(
            papers=[("2601.00001", "seed A")],
            cache={"2601.00001": ([stale], "ok", False)},
        )
        calls = _stub_references(
            monkeypatch, {"2601.00001": [_entry("9801.00001", "2601.00001")]}
        )
        result = _run(session, min_citing_seeds=1)
        assert calls == ["2601.00001"]
        assert session.writes == [("2601.00001", "ok", 1)]
        assert [c["arxiv_id"] for c in result["candidates"]] == ["9801.00001"]

    def test_fetch_per_call_limits_new_requests_and_reports_pending(self, monkeypatch):
        session = _session(
            papers=[
                ("2601.00001", "seed A"),
                ("2601.00002", "seed B"),
                ("2601.00003", "seed C"),
            ]
        )
        calls = _stub_references(
            monkeypatch,
            {
                "2601.00001": [_entry("9801.00001", "2601.00001")],
                "2601.00002": [_entry("9801.00001", "2601.00002")],
                "2601.00003": [_entry("9801.00001", "2601.00003")],
            },
        )
        result = _run(session, fetch_per_call=2)
        assert calls == ["2601.00001", "2601.00002"], "新しい順に上限まで"
        assert result["pending_seeds"] is True
        assert result["note"] == foundation.NOTE_PENDING_SEEDS
        assert [s["arxiv_id"] for s in result["seeds_read"]] == [
            "2601.00001",
            "2601.00002",
        ]
        assert [c["arxiv_id"] for c in result["candidates"]] == ["9801.00001"]

    def test_recently_failed_seeds_are_not_refetched_nor_pending(self, monkeypatch):
        ok = _entry("9801.00001", "2601.00001").to_dict()
        session = _session(
            papers=[("2601.00001", "seed A"), ("2601.00002", "seed B")],
            cache={
                "2601.00001": ([ok], "ok", True),
                "2601.00002": ([], "failed", True),
            },
        )
        calls = _stub_references(monkeypatch, {})
        result = _run(session, min_citing_seeds=1)
        assert calls == [], "TTL 内に失敗を記録したシードは取りに行かない（PD7）"
        assert result["pending_seeds"] is False
        assert [s["arxiv_id"] for s in result["seeds_read"]] == ["2601.00001"]

    def test_partial_failure_keeps_the_rest_and_records_it(self, monkeypatch):
        session = _session(papers=[("2601.00001", "seed A"), ("2601.00002", "seed B")])
        calls = _stub_references(
            monkeypatch,
            {
                "2601.00001": [_entry("9801.00001", "2601.00001")],
                "2601.00002": citation_client.CitationApiError("boom"),
            },
        )
        result = _run(session, min_citing_seeds=1)
        assert calls == ["2601.00001", "2601.00002"]
        assert result["available"] is True
        assert result["partial"] is True
        assert [c["arxiv_id"] for c in result["candidates"]] == ["9801.00001"]
        # 失敗は 'failed' で記録し、TTL 内の再取得を抑える。
        assert ("2601.00002", "failed", 0) in session.writes

    def test_total_failure_raises_instead_of_pretending_no_candidates(self, monkeypatch):
        session = _session(papers=[("2601.00001", "seed A"), ("2601.00002", "seed B")])
        _stub_references(
            monkeypatch,
            {
                "2601.00001": citation_client.CitationApiError("boom"),
                "2601.00002": citation_client.CitationApiError("boom"),
            },
        )
        with pytest.raises(citation_client.CitationApiError):
            _run(session, min_citing_seeds=1)

    def test_readable_but_empty_is_not_an_error(self, monkeypatch):
        """「読めたが参照が空」と「読めなかった」を混同しない。"""
        session = _session(papers=[("2601.00001", "seed A")])
        _stub_references(monkeypatch, {"2601.00001": []})
        result = _run(session, min_citing_seeds=1)
        assert result["available"] is True
        assert result["candidates"] == []
        assert result["note"] == foundation.NOTE_NO_CANDIDATES

    def test_no_seeds_is_available_false_with_a_factual_note(self, monkeypatch):
        calls = _stub_references(monkeypatch, {})
        result = _run(FakeSession())
        assert result["enabled"] is True
        assert result["available"] is False
        assert result["note"] == foundation.NOTE_NO_SEEDS
        assert result["candidates"] == [] and result["seeds_read"] == []
        assert calls == []

    def test_disabled_returns_a_factual_note_without_calling_the_api(self, monkeypatch):
        monkeypatch.setattr(citation_search, "citation_source_enabled", lambda: False)
        calls = _stub_references(monkeypatch, {})
        session = _session(papers=[("2601.00001", "seed A")])
        result = _run(session)
        assert result["enabled"] is False
        assert result["available"] is False
        assert result["note"] == citation_search.NOTE_DISABLED
        assert result["candidates"] == [] and result["seeds_read"] == []
        assert result["pending_seeds"] is False
        assert calls == [] and session.calls == []

    def test_closed_world_note_is_always_present(self, monkeypatch):
        _stub_references(monkeypatch, {})
        for session in (FakeSession(), _session(papers=[("2601.00001", "seed A")])):
            _stub_references(monkeypatch, {"2601.00001": []})
            result = _run(session)
            assert result["closed_world_note"] == foundation.CLOSED_WORLD_NOTE


# ---------------------------------------------------------------------------
# 5. 構造（CC1 / CC2 / CC7）
# ---------------------------------------------------------------------------


class TestFoundationStructure:
    @staticmethod
    def _code_only(name: str) -> str:
        """docstring（不変条項の記述に語が出る）を除いたコード部分。"""
        src = (CORE_DIR / name).read_text(encoding="utf-8")
        return re.sub(r'"""[\s\S]*?"""', "", src)

    def test_core_modules_stay_pure(self):
        """CC1（学習者信号を混ぜない）/ CC2 / CC7 を import の不在として固定する。"""
        for name in ("foundation.py", "reference_cache.py"):
            code = self._code_only(name)
            for forbidden in (
                "fastapi",
                "core.llm",
                "url_fetch",
                "_accept_material_source",
                "interest_traces",
                "frontier_interest",
                "stumble",
            ):
                assert forbidden not in code, f"{name} must not reference {forbidden}"

    def test_foundation_does_not_call_http_itself(self):
        code = self._code_only("foundation.py")
        assert "requests." not in code, "外部 API の入口は citation_client だけ（PD7）"



# ---------------------------------------------------------------------------
# 6. migration 077
# ---------------------------------------------------------------------------


class TestMigration:
    def test_creates_the_cache_table_idempotently(self):
        sql = _migration_statements()
        assert "CREATE TABLE IF NOT EXISTS paper_discovery_reference_cache" in sql
        assert "CREATE TABLE " in sql
        for statement in re.findall(r"CREATE (TABLE|INDEX)[^;]*;", sql):
            assert statement  # 形の確認は下の個別アサーションで行う
        assert sql.count("CREATE TABLE") == sql.count("CREATE TABLE IF NOT EXISTS")
        assert sql.count("CREATE INDEX") == sql.count("CREATE INDEX IF NOT EXISTS")

    def test_does_not_seed_rows(self):
        assert "INSERT" not in _migration_statements().upper()

    def test_has_no_delete_and_no_user_foreign_key(self):
        sql = _migration_statements().upper()
        assert "DELETE" not in sql
        assert "REFERENCES USERS" not in sql
        assert "REFERENCES DOCUMENTS" not in sql

    def test_status_vocabulary_matches_the_core_constants(self):
        sql = _migration_statements()
        assert "CHECK (fetch_status IN ('ok', 'failed'))" in sql
        assert set(reference_cache.FETCH_STATUSES) == {"ok", "failed"}

    def test_column_name_avoids_the_reserved_word(self):
        """``references`` は PostgreSQL の予約語なので列名にしない（設計書 §5.4 の逸脱点）。"""
        sql = _migration_statements()
        assert "reference_entries JSONB" in sql
        assert not re.search(r"^\s*references\s+JSONB", sql, re.MULTILINE | re.IGNORECASE)
