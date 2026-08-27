"""コーパス回遊層 — core（``core/corpus_view.py``）の読み時導出テスト。

正本: docs/features/corpus_roaming_design.md §4（Phase A コーパス地図）/
§6（Phase C 地図の端）。DB・FastAPI・ネットワークには接続しない
（フェイクセッション + フェイク骨格）。

検証観点:
  1. 可視性が SQL の ``= ANY(:doc_ids)`` で強制され、空集合では SQL を発行しない（CR1）
  2. 骨格に無いノード / 領域の行を落とす fail-closed（LS6 の同族）
  3. 出所ラベルが必ず付き、weight / confidence / 件数が DTO に出ない（CR3）
  4. 縁が ``atlas_gap_signals`` の active のみを読み、教員の判断を読まない（§6.1）
  5. 外の輪が ``last_search_found_new`` TRUE + 時点ありのときだけ出る（§6.2）
  6. 論文リストが「配置 ∪ 置けなかった信号」で、``placed`` がその区別になる
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import corpus_view  # noqa: E402


# ---------------------------------------------------------------------------
# フェイク骨格 / フェイクセッション
# ---------------------------------------------------------------------------


class _Concept:
    def __init__(self, cid, label):
        self.id = cid
        self.label = label


class _Region:
    def __init__(self, rid, label, concepts=()):
        self.id = rid
        self.label = label
        self.concepts = tuple(concepts)


class _Skeleton:
    version = "v3"

    def __init__(self, regions=()):
        self.regions = tuple(regions)


def _skeleton():
    return _Skeleton(
        [
            _Region("r_dark", "ダークエネルギー", [_Concept("c_w0wa", "w0waCDM")]),
            _Region("r_lss", "大規模構造", []),
        ]
    )


class _Result:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeSession:
    """SQL の断片で分岐して固定行を返す最小セッション。"""

    def __init__(self, *, placements=(), signals=(), subscription=None, documents=(),
                 domains_with_placements=()):
        self.placements = list(placements)
        self.signals = list(signals)
        self.subscription = subscription
        self.documents = list(documents)
        self.domains_with_placements = list(domains_with_placements)
        self.calls: list[tuple[str, dict]] = []

    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        self.calls.append((sql, dict(params or {})))
        if "SELECT DISTINCT domain_key FROM landscape_placements" in sql:
            return _Result([(d,) for d in self.domains_with_placements])
        if "FROM landscape_placements p JOIN documents d" in sql:
            return _Result(self.placements)
        if "FROM landscape_gap_signals s JOIN documents d" in sql:
            return _Result(self.signals)
        if "FROM paper_discovery_subscriptions" in sql:
            return _Result([self.subscription] if self.subscription else [])
        if "FROM documents d WHERE d.id::text = ANY(:doc_ids)" in sql:
            return _Result(self.documents)
        return _Result([])

    def close(self):
        pass

    def sqls(self):
        return [sql for sql, _ in self.calls]


def _numeric_keys(node, forbidden=("weight", "confidence", "score", "count", "similarity")):
    """DTO を再帰走査して禁止キーを集める（CR3）。"""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            lowered = str(key).lower()
            if any(term in lowered for term in forbidden):
                found.append(str(key))
            found.extend(_numeric_keys(value, forbidden))
    elif isinstance(node, (list, tuple)):
        for item in node:
            found.extend(_numeric_keys(item, forbidden))
    return found


# ---------------------------------------------------------------------------
# ドメイン一覧
# ---------------------------------------------------------------------------


class TestListCorpusDomains:
    def _domains(self):
        return [
            {"domain_key": "astrophysics", "domain_name": "宇宙物理",
             "frozen_version": "v3", "lifecycle": "active"},
            {"domain_key": "draft_only", "domain_name": "", "frozen_version": "",
             "lifecycle": "active"},
            {"domain_key": "retired_domain", "domain_name": "旧分野",
             "frozen_version": "v1", "lifecycle": "retired"},
        ]

    def test_only_active_frozen_domains(self, monkeypatch):
        monkeypatch.setattr(corpus_view.atlas_store, "list_domains", lambda s: self._domains())
        session = FakeSession(domains_with_placements=["astrophysics"])

        out = corpus_view.list_corpus_domains(session, {"d1"})

        assert [d["domain_key"] for d in out] == ["astrophysics"]
        assert out[0]["has_visible_papers"] is True
        assert out[0]["domain_name"] == "宇宙物理"

    def test_has_visible_papers_is_a_bool_not_a_count(self, monkeypatch):
        monkeypatch.setattr(corpus_view.atlas_store, "list_domains", lambda s: self._domains())
        session = FakeSession(domains_with_placements=[])

        out = corpus_view.list_corpus_domains(session, {"d1"})

        assert out[0]["has_visible_papers"] is False
        assert _numeric_keys(out) == []

    def test_no_sql_when_visible_set_is_empty(self, monkeypatch):
        monkeypatch.setattr(corpus_view.atlas_store, "list_domains", lambda s: self._domains())
        session = FakeSession()

        out = corpus_view.list_corpus_domains(session, set())

        assert [d["has_visible_papers"] for d in out] == [False]
        assert session.sqls() == []

    def test_domain_listing_failure_degrades_to_empty(self, monkeypatch):
        def _boom(_s):
            raise RuntimeError("boom")

        monkeypatch.setattr(corpus_view.atlas_store, "list_domains", _boom)
        assert corpus_view.list_corpus_domains(FakeSession(), {"d1"}) == []


# ---------------------------------------------------------------------------
# 地図（配置 + 縁 + 外）
# ---------------------------------------------------------------------------


class TestBuildCorpusLandscape:
    @pytest.fixture(autouse=True)
    def _skeleton_patch(self, monkeypatch):
        monkeypatch.setattr(
            corpus_view.atlas_store, "load_learner_skeleton",
            lambda key, session=None: _skeleton() if key == "astrophysics" else None,
        )

    def test_missing_skeleton_returns_none(self):
        assert corpus_view.build_corpus_landscape(FakeSession(), "unknown", {"d1"}) is None
        assert corpus_view.build_corpus_landscape(FakeSession(), "", {"d1"}) is None

    def test_placements_carry_source_label_and_no_numbers(self):
        session = FakeSession(
            placements=[
                ("d1", "Dark energy survey", "c_w0wa", "theory", "inferred"),
                ("d2", "Structure growth", "r_dark", "observation", "confirmed"),
                # 骨格に無いノード → 落とす（LS6 の同族の fail-closed）
                ("d3", "Ghost", "c_removed", "theory", "inferred"),
            ],
        )

        result = corpus_view.build_corpus_landscape(session, "astrophysics", {"d1", "d2", "d3"})

        assert [p["document_id"] for p in result["placements"]] == ["d1", "d2"]
        assert result["placements"][0]["source_label"] == "AIによる推定（未確認）"
        assert result["placements"][1]["source_label"] == "教員確認済み"
        assert result["placements"][0]["anchor_node_id"] == "c_w0wa"
        assert result["placements"][0]["region_id"] == "r_dark"
        assert result["skeleton_version"] == "v3"
        assert _numeric_keys(result) == []

    def test_visibility_is_enforced_inside_sql(self):
        session = FakeSession(placements=[])
        corpus_view.build_corpus_landscape(session, "astrophysics", {"d1", "d2"})

        placement_calls = [
            (sql, params) for sql, params in session.calls
            if "FROM landscape_placements p JOIN documents d" in sql
        ]
        assert placement_calls, "配置の SQL が発行されていない"
        sql, params = placement_calls[0]
        assert "p.document_id::text = ANY(:doc_ids)" in sql
        assert params["doc_ids"] == ["d1", "d2"]

    def test_empty_visible_set_issues_no_placement_or_signal_sql(self):
        session = FakeSession()
        result = corpus_view.build_corpus_landscape(session, "astrophysics", set())

        assert result["placements"] == []
        assert result["fringe"] == []
        assert not [s for s in session.sqls() if "landscape_placements" in s]
        assert not [s for s in session.sqls() if "landscape_gap_signals" in s]

    def test_fringe_groups_by_region_with_fixed_fact_line(self):
        session = FakeSession(
            signals=[
                ("r_dark", "Paper B"),
                ("r_dark", "Paper A"),
                ("r_dark", "Paper A"),      # 重複タイトルは1回
                ("r_missing", "Orphan"),    # 骨格に無い領域 → 落とす
            ],
        )

        result = corpus_view.build_corpus_landscape(session, "astrophysics", {"d1"})

        assert len(result["fringe"]) == 1
        entry = result["fringe"][0]
        assert entry["region_id"] == "r_dark"
        assert entry["region_label"] == "ダークエネルギー"
        assert entry["fact_line"] == corpus_view.FACT_FRINGE
        assert entry["paper_titles"] == ["Paper A", "Paper B"]
        assert "paper_count" not in entry

    def test_fringe_reads_active_signals_only_and_not_teacher_decisions(self):
        session = FakeSession(signals=[])
        corpus_view.build_corpus_landscape(session, "astrophysics", {"d1"})

        signal_sql = [s for s in session.sqls() if "landscape_gap_signals" in s]
        assert signal_sql
        assert "s.status = :active" in signal_sql[0]
        assert not [s for s in session.sqls() if "atlas_gap_decisions" in s]

    def test_outer_ring_present_only_when_bit_and_timestamp_exist(self):
        session = FakeSession(subscription=(True, "2026-08-27T10:00:00+00:00"))
        result = corpus_view.build_corpus_landscape(session, "astrophysics", {"d1"})

        assert result["outer"] == {
            "fact_line": corpus_view.outer_fact_line("2026-08-27"),
        }
        assert "2026-08-27" in result["outer"]["fact_line"]
        assert "検索条件" in result["outer"]["fact_line"]

    @pytest.mark.parametrize(
        "subscription",
        [None, (False, "2026-08-27T10:00:00+00:00"), (None, "2026-08-27T00:00:00Z"), (True, None)],
    )
    def test_outer_ring_absent_cases(self, subscription):
        session = FakeSession(subscription=subscription)
        result = corpus_view.build_corpus_landscape(session, "astrophysics", {"d1"})
        assert result["outer"] is None


class TestOuterFactLine:
    def test_states_the_search_condition_limit_and_date(self):
        line = corpus_view.outer_fact_line("2026-08-27")
        assert line.startswith("教員の検索条件では")
        assert "arXiv" in line
        assert "2026-08-27" in line

    def test_closed_world_denylist(self):
        for line in (corpus_view.FACT_FRINGE, corpus_view.outer_fact_line("2026-08-27")):
            for banned in ("世界初", "誰も", "この分野には論文がない", "未踏"):
                assert banned not in line


# ---------------------------------------------------------------------------
# 論文リスト
# ---------------------------------------------------------------------------


class TestListCorpusDocuments:
    def test_placed_flag_and_new_first_order(self):
        session = FakeSession(
            documents=[
                ("d1", "Newest", ["Doe, J."], 2026, True),
                ("d2", "Older", [], None, False),
            ],
        )

        out = corpus_view.list_corpus_documents(session, "astrophysics", {"d1", "d2"})

        assert [d["document_id"] for d in out] == ["d1", "d2"]
        assert out[0] == {
            "document_id": "d1",
            "title": "Newest",
            "authors": ["Doe, J."],
            "year": 2026,
            "placed": True,
            "can_discuss": True,
        }
        assert out[1]["placed"] is False
        assert out[1]["year"] is None
        sql = [s for s in session.sqls() if "FROM documents d" in s][0]
        assert "ORDER BY d.created_at DESC" in sql
        assert "d.id::text = ANY(:doc_ids)" in sql

    def test_empty_visible_set_returns_empty_without_sql(self):
        session = FakeSession()
        assert corpus_view.list_corpus_documents(session, "astrophysics", set()) == []
        assert session.sqls() == []

    def test_empty_domain_returns_empty_without_sql(self):
        session = FakeSession()
        assert corpus_view.list_corpus_documents(session, "", {"d1"}) == []
        assert session.sqls() == []


# ---------------------------------------------------------------------------
# services 側の3ヘルパー（Phase D。記録 / 取り消し / k-匿名集約）
# ---------------------------------------------------------------------------


class _TraceSession:
    def __init__(self, rows=(), row=None):
        self.rows = list(rows)
        self.row = row
        self.calls: list[tuple[str, dict]] = []
        self.committed = False

    def execute(self, stmt, params=None):
        self.calls.append((" ".join(str(stmt).split()), dict(params or {})))
        return _Result(self.rows if self.rows else ([self.row] if self.row else []))

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def close(self):
        pass


class TestFrontierInterestServices:
    def test_record_goes_through_the_single_trace_entry_point(self, monkeypatch):
        from api import services

        captured: dict = {}

        def _fake(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return "trace-9"

        monkeypatch.setattr(services, "record_interest_trace", _fake)

        assert services.record_frontier_interest("u1", "astrophysics", "fringe", "r_dark") == "trace-9"
        assert captured["args"][0] == "u1"
        assert captured["args"][1] == services.CORPUS_TRACE_COURSE_ID
        assert captured["args"][2] is None
        assert captured["kwargs"]["kind"] == "frontier_interest"
        assert captured["kwargs"]["text"] == ""
        assert captured["kwargs"]["extra_payload"] == {
            "domain_key": "astrophysics", "region_id": "r_dark", "ring": "fringe",
        }
        assert captured["kwargs"]["status"] == "open"

    def test_record_requires_a_domain(self, monkeypatch):
        from api import services

        monkeypatch.setattr(services, "record_interest_trace", lambda *a, **k: "x")
        assert services.record_frontier_interest("u1", "  ", "fringe") is None

    def test_withdraw_updates_status_for_the_owner_only(self, monkeypatch):
        from api import services

        session = _TraceSession(row=("trace-1",))
        monkeypatch.setattr(services, "_pg_session", lambda: session)

        assert services.withdraw_frontier_interest("u1", "trace-1") is True
        sql, params = session.calls[0]
        assert "SET status = 'dismissed'" in sql
        assert "user_id = CAST(:uid AS uuid)" in sql
        assert "kind = 'frontier_interest'" in sql
        assert "DELETE" not in sql
        assert params == {"tid": "trace-1", "uid": "u1"}
        assert session.committed is True

    def test_withdraw_of_a_foreign_row_returns_false(self, monkeypatch):
        from api import services

        monkeypatch.setattr(services, "_pg_session", lambda: _TraceSession())
        assert services.withdraw_frontier_interest("u1", "trace-1") is False
        assert services.withdraw_frontier_interest("u1", "") is False

    def test_aggregate_applies_k_anonymity_and_ranges(self, monkeypatch):
        from api import services

        session = _TraceSession(rows=[
            ("astrophysics", "r_dark", "fringe", 2),    # n < 3 → 出さない
            ("astrophysics", "r_lss", "fringe", 4),
            ("astrophysics", "", "outer", 12),
        ])
        monkeypatch.setattr(services, "_pg_session", lambda: session)

        rows = services.aggregate_frontier_interest("astrophysics")

        assert rows == [
            {"domain_key": "astrophysics", "region_id": "r_lss",
             "ring": "fringe", "range_label": "3-5"},
            {"domain_key": "astrophysics", "region_id": "",
             "ring": "outer", "range_label": "11+"},
        ]
        sql, params = session.calls[0]
        assert "COUNT(DISTINCT user_id)" in sql
        assert "status = 'open'" in sql          # 取り消し済みは数えない
        assert params == {"domain_key": "astrophysics"}

    def test_aggregate_without_domain_filter(self, monkeypatch):
        from api import services

        session = _TraceSession(rows=[])
        monkeypatch.setattr(services, "_pg_session", lambda: session)

        assert services.aggregate_frontier_interest() == []
        _sql, params = session.calls[0]
        assert params == {}

    def test_aggregate_degrades_to_empty_on_db_failure(self, monkeypatch):
        from api import services

        class _Boom(_TraceSession):
            def execute(self, *_a, **_kw):
                raise RuntimeError("boom")

        monkeypatch.setattr(services, "_pg_session", lambda: _Boom())
        assert services.aggregate_frontier_interest("astrophysics") == []
