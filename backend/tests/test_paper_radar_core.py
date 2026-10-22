"""論文レーダー — core（``core/paper_discovery/{radar,compare}.py`` + 既存モジュールへの
追加分）の単体テスト。

設計正本: ``docs/features/paper_radar_design.md``（不変条項 PR1〜PR8）。構造的な検査は
``test_paper_radar_guardrails.py``、API は ``test_paper_radar_api.py``。

検証観点:

1. 距離語彙（``schema.RADAR_DISTANCES``）
2. ``arxiv_client.fetch_by_ids``（``id_list`` パラメータ・スロットル経由・空は API を
   呼ばない・上限で切り詰め・版違いの正規化）
3. ``corpus.document_domain_keys``（コース sources からの逆引き・新しい順・
   material_id なしは空）
4. ``radar.resolve_seed``（カテゴリ供給順 arxiv → subscription → manual・arXiv 失敗の
   fail-soft・キーフレーズ供給）
5. ``radar.build_radar_query`` / ``run_radar_search``（seed 自身の除外・status 注釈・
   ``mid``/``far`` の新着順維持・``touch_last_checked`` を呼ばない）
5b. ``radar.overlap_component_labels`` / ``seed_placed_node_ids`` /
   ``_merge_distance_labels`` / ``run_radar_search`` の ``relation_context``
   （承認済み部品との語彙照合は決定論・配置ノードの読みは読み取り専用で fail-soft・
   ``mid``/``far`` でも地図の注釈を落とさない・アンカー文脈は注入 resolver 経由）
6. ``ranking.document_centroid`` / ``band_candidates``（未測定にラベルを付けない・
   要旨フォールバックが同一バッチ・fail-soft）
7. ``compare`` の validator（verbatim drop・aspect の fail-closed・素材ゼロで LLM を
   呼ばない）

ネットワークにも DB にも一切接続しない。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import pytest

BACKEND = Path(__file__).resolve().parents[1]
for _path in (str(BACKEND), str(BACKEND / "api")):
    if _path not in sys.path:
        sys.path.insert(0, str(_path))

from core.label_vocab import RADAR_DISTANCE_SCALE  # noqa: E402
from core.paper_discovery import (  # noqa: E402
    arxiv_client,
    compare as compare_mod,
    corpus,
    radar,
    ranking,
    schema,
)


ATOM_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom"
      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
  <opensearch:totalResults>2</opensearch:totalResults>
  <entry>
    <id>http://arxiv.org/abs/2608.20293v2</id>
    <title>Seed paper</title>
    <summary>We derive constraints on the dark energy equation of state.</summary>
    <arxiv:primary_category term="astro-ph.CO"/>
    <category term="astro-ph.CO"/>
    <category term="gr-qc"/>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2608.00002v1</id>
    <title>Another paper</title>
    <summary>A different approach to the same problem.</summary>
    <category term="astro-ph.CO"/>
  </entry>
</feed>
"""


# ---------------------------------------------------------------------------
# フェイクセッション
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, rows=(), rowcount=0):
        self._rows = list(rows)
        self.rowcount = rowcount

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeSession:
    """``documents`` / ``learning_courses`` / ``theory_components`` / ``chunks`` の最小フェイク。"""

    def __init__(self, *, documents=(), courses=(), components=(), chunks=(),
                 placements=(), arxiv_metadata=(), claims=()):
        self.documents = list(documents)
        # theory_claims_live（``{document_id, concepts}``）— キーフレーズ供給の段③。
        self.claims = list(claims)
        self.courses = list(courses)
        self.components = list(components)
        self.chunks = list(chunks)
        # landscape_placements（``{document_id, domain_key, node_id, status}``）
        self.placements = list(placements)
        # paper_discovery_arxiv_metadata_cache（migration 085 / 設計書 §14）。
        # 在る行はすべて「TTL 内」として返す（TTL の境界は store 側のテストの責務）。
        self.arxiv_metadata = {
            entry.arxiv_id: entry for entry in arxiv_metadata
        }
        self.metadata_writes: list[str] = []
        self.calls: list[tuple[str, dict]] = []

    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        p = dict(params or {})
        self.calls.append((sql, p))

        if "paper_discovery_arxiv_metadata_cache" in sql:
            if sql.startswith("INSERT"):
                self.metadata_writes.append(p.get("arxiv_id") or "")
                return _Result()
            wanted = list(p.get("arxiv_ids") or [])
            return _Result(
                [
                    (
                        e.arxiv_id, e.title, e.summary, list(e.categories),
                        e.primary_category or "", list(e.authors),
                        None, None, e.abs_url or "", e.pdf_url or "",
                    )
                    for key in wanted
                    for e in [self.arxiv_metadata.get(key)]
                    if e is not None
                ]
            )

        if sql.startswith("UPDATE documents"):
            # radar.register_arxiv_provenance（source_url が空のときだけ書き換える）
            changed = 0
            for doc in self.documents:
                if doc["id"] != p.get("document_id"):
                    continue
                if (doc.get("source_url") or "") != "":
                    continue
                doc["source_url"] = p.get("url")
                changed += 1
            return _Result(rowcount=changed)
        if "FROM documents" in sql and "ANY(:refs)" in sql:
            # core.document_refs.resolve_document_rows（両形の突き合わせの正本・IK-0550）
            refs = set(p.get("refs") or [])
            return _Result(
                [
                    {
                        "id": d["id"],
                        "source_path": d.get("source_path") or "",
                        "title": d.get("title"),
                        "filename": d.get("filename"),
                        "source_url": d.get("source_url"),
                    }
                    for d in self.documents
                    if d["id"] in refs or d.get("source_path") in refs
                ]
            )
        if "FROM documents" in sql and ":ref" in sql:
            ref = p.get("ref")
            rows = [
                d for d in self.documents
                if d["id"] == ref or d.get("source_path") == ref
            ][:1]
            if sql.startswith("SELECT COALESCE(source_path"):
                # corpus.document_domain_keys（source_path だけを引く）
                return _Result([(d.get("source_path") or "",) for d in rows])
            return _Result(
                [
                    (
                        d["id"],
                        d.get("source_path") or "",
                        d.get("title") or "",
                        d.get("source_url") or "",
                        d.get("filename") or "",
                    )
                    for d in rows
                ]
            )
        if "FROM documents" in sql and "ANY(:visible_ids)" in sql:
            # search.visible_ingested_documents（閲覧者に見える取り込み済みだけ — §19 C）
            visible = set(p.get("visible_ids") or [])
            return _Result(
                [
                    (
                        d["id"], d.get("source_path") or "", d.get("title") or "",
                        d.get("status") or "", d.get("source_url"),
                    )
                    for d in self.documents
                    if d.get("source_url") and d["id"] in visible
                ]
            )
        if "FROM documents" in sql and "source_url IS NOT NULL" in sql:
            return _Result([(d.get("source_url") or "",) for d in self.documents if d.get("source_url")])
        if "FROM learning_courses" in sql:
            return _Result([(c["data"].get("cartridge_id") or "", c["data"]) for c in self.courses])
        if "FROM theory_components" in sql:
            wanted = set(p.get("refs") or [])
            statuses = set(p.get("statuses") or [])
            names = sorted(
                {
                    c["name"]
                    for c in self.components
                    if c.get("document_id") in wanted and c.get("review_status") in statuses
                }
            )
            return _Result([(n,) for n in names[: int(p.get("limit") or 0) or None]])
        if "FROM landscape_placements" in sql and "status = 'confirmed'" in sql:
            # radar._confirmed_placement_phrases（確認済みの配置だけ・段②）
            rows = sorted(
                {
                    (row["domain_key"], row["node_id"], row.get("node_kind") or "concept")
                    for row in self.placements
                    if row.get("document_id") == p.get("document_id")
                    and row.get("status") == "confirmed"
                }
            )
            return _Result(rows)
        if "FROM theory_claims_live" in sql:
            wanted = set(p.get("refs") or [])
            return _Result(
                [(c.get("concepts"),) for c in self.claims if c.get("document_id") in wanted]
            )
        if "FROM landscape_placements" in sql:
            live = sorted(
                {
                    row["node_id"]
                    for row in self.placements
                    if row.get("document_id") == p.get("document_id")
                    and row.get("domain_key") == p.get("domain_key")
                    and row.get("status") not in ("superseded", "rejected")
                }
            )
            return _Result([(node_id,) for node_id in live])
        if "FROM chunks" in sql:
            wanted = set(p.get("document_ids") or [])
            limit = int(p.get("per_document") or 0)
            return _Result(
                [
                    (c["document_id"], c["embedding"])
                    for c in self.chunks
                    if c["document_id"] in wanted and int(c.get("chunk_index", 0)) < limit
                ]
            )
        return _Result()

    def commit(self):  # pragma: no cover — core は commit しない
        raise AssertionError("core must not commit")

    def close(self):
        pass

    @property
    def sql_log(self) -> str:
        return "\n".join(sql for sql, _ in self.calls)


def _seed_session(**kwargs) -> FakeSession:
    base = {
        "documents": [
            {
                "id": "doc-1",
                "source_path": "mat-1",
                "title": "起点論文",
                "source_url": "https://arxiv.org/pdf/2608.20293v2",
            }
        ],
        "courses": [
            {"data": {"cartridge_id": "astrophysics", "sources": [{"material_id": "mat-1"}]}}
        ],
        "components": [
            {"document_id": "doc-1", "name": "dark energy", "review_status": "teacher_approved"},
            {"document_id": "mat-1", "name": "w0waCDM", "review_status": "endorsed"},
            {"document_id": "doc-1", "name": "未承認の部品", "review_status": "ai_generated"},
        ],
        "chunks": [],
        "placements": [],
    }
    base.update(kwargs)
    return FakeSession(**base)


@pytest.fixture(autouse=True)
def _reset_counter():
    ranking.reset_daily_counter()
    yield
    ranking.reset_daily_counter()


@pytest.fixture
def no_arxiv(monkeypatch):
    """arXiv へ出ていく経路を封じる（明示的に許可したテストだけが差し替える）。"""

    def _boom(*args, **kwargs):  # pragma: no cover — 呼ばれたら失敗させる
        raise AssertionError("arXiv must not be called")

    monkeypatch.setattr(arxiv_client, "_http_get", _boom)


# ---------------------------------------------------------------------------
# 1. 距離語彙
# ---------------------------------------------------------------------------


class TestDistanceVocabulary:
    def test_vocabulary_is_the_canon(self):
        assert schema.RADAR_DISTANCES == ("near", "mid", "far")

    def test_labels_come_from_label_vocab(self):
        assert RADAR_DISTANCE_SCALE.labels == ("近い", "中間", "遠い")


# ---------------------------------------------------------------------------
# 2. fetch_by_ids（PR6 — 既存クライアント経由）
# ---------------------------------------------------------------------------


class TestFetchByIds:
    def test_builds_id_list_and_parses(self, monkeypatch):
        seen: dict[str, Any] = {}
        monkeypatch.setattr(
            arxiv_client,
            "_http_get",
            lambda params, timeout: seen.update(params) or ATOM_FIXTURE,
        )
        entries = arxiv_client.fetch_by_ids(["2608.20293v2", "https://arxiv.org/abs/2608.00002"])
        assert seen["id_list"] == "2608.20293,2608.00002"
        assert [e.arxiv_id for e in entries] == ["2608.20293", "2608.00002"]

    def test_empty_input_does_not_call_the_api(self, no_arxiv):
        assert arxiv_client.fetch_by_ids([]) == []
        assert arxiv_client.fetch_by_ids(["", "not-an-id"]) == []

    def test_deduplicates_versions_of_the_same_paper(self, monkeypatch):
        seen: dict[str, Any] = {}
        monkeypatch.setattr(
            arxiv_client,
            "_http_get",
            lambda params, timeout: seen.update(params) or ATOM_FIXTURE,
        )
        arxiv_client.fetch_by_ids(["2608.20293v1", "2608.20293v2", "2608.20293"])
        assert seen["id_list"] == "2608.20293"
        assert seen["max_results"] == 1

    def test_truncates_at_the_id_limit(self, monkeypatch):
        seen: dict[str, Any] = {}
        monkeypatch.setattr(
            arxiv_client,
            "_http_get",
            lambda params, timeout: seen.update(params) or ATOM_FIXTURE,
        )
        ids = [f"2608.{index:05d}" for index in range(arxiv_client.MAX_ID_LIST + 5)]
        arxiv_client.fetch_by_ids(ids)
        assert len(seen["id_list"].split(",")) == arxiv_client.MAX_ID_LIST

    def test_goes_through_the_shared_throttle(self, monkeypatch):
        """PR6: ID 指定取得も既存の3秒スロットル・定数ホストを通る。"""
        calls: list[str] = []
        monkeypatch.setattr(arxiv_client, "_throttle", lambda: calls.append("throttle"))

        class _Resp:
            status_code = 200
            text = ATOM_FIXTURE

        monkeypatch.setattr(
            arxiv_client.requests, "get", lambda url, params=None, timeout=None: _Resp()
        )
        arxiv_client.fetch_by_ids(["2608.20293"])
        assert calls == ["throttle"]

    def test_api_failure_propagates(self, monkeypatch):
        def _boom(params, timeout):
            raise arxiv_client.ArxivApiError("arXiv への接続に失敗しました")

        monkeypatch.setattr(arxiv_client, "_http_get", _boom)
        with pytest.raises(arxiv_client.ArxivApiError):
            arxiv_client.fetch_by_ids(["2608.20293"])


# ---------------------------------------------------------------------------
# 3. document → 分野の逆引き
# ---------------------------------------------------------------------------


class TestDocumentDomainKeys:
    def test_resolves_by_document_id_and_material_id(self):
        session = _seed_session()
        assert corpus.document_domain_keys(session, "doc-1") == ["astrophysics"]
        assert corpus.document_domain_keys(session, "mat-1") == ["astrophysics"]

    def test_keeps_the_newest_course_first_and_dedupes(self):
        session = _seed_session(
            courses=[
                {"data": {"cartridge_id": "modified_gravity", "sources": [{"material_id": "mat-1"}]}},
                {"data": {"cartridge_id": "astrophysics", "sources": [{"material_id": "mat-1"}]}},
                {"data": {"cartridge_id": "astrophysics", "sources": [{"material_id": "mat-1"}]}},
            ]
        )
        # SQL 側で created_at DESC 順に並ぶ前提（フェイクは宣言順をそのまま返す）。
        assert corpus.document_domain_keys(session, "doc-1") == [
            "modified_gravity",
            "astrophysics",
        ]

    def test_unrelated_courses_are_not_counted(self):
        session = _seed_session(
            courses=[
                {"data": {"cartridge_id": "particle_physics", "sources": [{"material_id": "other"}]}}
            ]
        )
        assert corpus.document_domain_keys(session, "doc-1") == []

    def test_document_without_material_id_is_empty(self):
        session = _seed_session(
            documents=[{"id": "doc-1", "source_path": "", "title": "t", "source_url": ""}]
        )
        assert corpus.document_domain_keys(session, "doc-1") == []
        assert "FROM learning_courses" not in session.sql_log

    def test_missing_document_is_empty(self):
        assert corpus.document_domain_keys(_seed_session(), "unknown") == []
        assert corpus.document_domain_keys(_seed_session(), "") == []


# ---------------------------------------------------------------------------
# 3b. arXiv 出所の推定（ファイル名・タイトル照合の純関数）
# ---------------------------------------------------------------------------


class TestArxivIdFromFilename:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("arXiv-2407.01221v2.tar.gz", "2407.01221"),
            ("2407.01221.pdf", "2407.01221"),
            ("arxiv_2407.1234.tar.gz", "2407.1234"),
            ("  arXiv-2407.01221v12.tar.gz  ", "2407.01221"),
            ("Foo 2407.01221 bar", "2407.01221"),
        ],
    )
    def test_extracts_the_new_style_id_without_the_version(self, value, expected):
        assert schema.arxiv_id_from_filename(value) == expected

    def test_same_paper_in_two_versions_is_one_id(self):
        assert schema.arxiv_id_from_filename("2407.01221v1_2407.01221v2.tar.gz") == (
            "2407.01221"
        )

    def test_two_distinct_ids_are_ambiguous_and_yield_nothing(self):
        """曖昧なら推定しない（どちらが論文本体かを当て推量しない）。"""
        assert schema.arxiv_id_from_filename("2407.01221_2408.00002.tar.gz") == ""

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "lecture-notes.pdf",
            "hep-ph/0101001.tar.gz",  # 旧形式は v1 非対応
            "12345.678901.pdf",  # 桁が合わない数値列
            "version 2.0.1 draft.pdf",
            None,
            12345,
        ],
    )
    def test_non_matching_input_is_empty(self, value):
        assert schema.arxiv_id_from_filename(value) == ""

    def test_is_deterministic(self):
        value = "arXiv-2407.01221v2.tar.gz"
        assert schema.arxiv_id_from_filename(value) == schema.arxiv_id_from_filename(value)


class TestTitleMatching:
    def test_normalization_folds_case_width_and_punctuation(self):
        assert schema.normalize_title_for_match(
            "  Dark  Energy: A Review!\n"
        ) == "darkenergyareview"
        assert schema.normalize_title_for_match("ＡＢＣ１２３") == "abc123"

    def test_non_string_is_empty(self):
        assert schema.normalize_title_for_match(None) == ""

    def test_matches_across_formatting_differences(self):
        assert schema.titles_match(
            "Dark Energy: A Review", "  dark   energy — a review\n"
        )

    def test_different_titles_do_not_match(self):
        assert not schema.titles_match("Dark Energy: A Review", "Dark Matter: A Review")

    def test_short_titles_never_match(self):
        """短すぎるタイトルの偶然一致で自動記帳させない。"""
        assert not schema.titles_match("Note", "note")
        assert schema.TITLE_MATCH_MIN_LENGTH == 10

    def test_empty_side_never_matches(self):
        assert not schema.titles_match("", "")
        assert not schema.titles_match("Dark Energy: A Review", "")


# ---------------------------------------------------------------------------
# 4. seed の解決
# ---------------------------------------------------------------------------


def _stub_fetch_by_ids(monkeypatch, entries, *, recorder=None):
    def _fake(ids, **kwargs):
        if recorder is not None:
            recorder.append(list(ids))
        if isinstance(entries, Exception):
            raise entries
        return list(entries)

    monkeypatch.setattr(radar.arxiv_client, "fetch_by_ids", _fake)


def _entry(arxiv_id, **kwargs):
    return schema.ArxivEntry(arxiv_id=arxiv_id, **kwargs)


class TestResolveSeed:
    def test_prefers_arxiv_metadata(self, monkeypatch):
        _stub_fetch_by_ids(
            monkeypatch,
            [
                _entry(
                    "2608.20293",
                    title="Seed",
                    summary="An abstract.",
                    categories=["astro-ph.CO"],
                    primary_category="astro-ph.CO",
                )
            ],
        )
        seed = radar.resolve_seed(_seed_session(), "doc-1")
        assert seed["arxiv_id"] == "2608.20293"
        assert seed["abs_url"].endswith("2608.20293")
        assert seed["categories"] == ["astro-ph.CO"]
        assert seed["categories_source"] == radar.CATEGORIES_SOURCE_ARXIV
        assert seed["summary"] == "An abstract."
        assert seed["domain_key"] == "astrophysics"

    def test_falls_back_to_the_subscription(self, monkeypatch):
        _stub_fetch_by_ids(monkeypatch, [])
        monkeypatch.setattr(
            radar.store,
            "get_subscription",
            lambda session, key: {"arxiv_categories": ["astro-ph.GA"]},
        )
        seed = radar.resolve_seed(_seed_session(), "doc-1")
        assert seed["categories"] == ["astro-ph.GA"]
        assert seed["categories_source"] == radar.CATEGORIES_SOURCE_SUBSCRIPTION

    def test_falls_back_to_manual_when_nothing_is_available(self, monkeypatch):
        _stub_fetch_by_ids(monkeypatch, [])
        monkeypatch.setattr(radar.store, "get_subscription", lambda session, key: None)
        seed = radar.resolve_seed(_seed_session(), "doc-1")
        assert seed["categories"] == []
        assert seed["categories_source"] == radar.CATEGORIES_SOURCE_MANUAL

    def test_arxiv_failure_degrades_with_a_factual_note(self, monkeypatch):
        """PR7: 引けなかったことを黙らせず、別の供給元へ縮退する。"""
        _stub_fetch_by_ids(monkeypatch, arxiv_client.ArxivApiError("接続に失敗しました"))
        monkeypatch.setattr(
            radar.store,
            "get_subscription",
            lambda session, key: {"arxiv_categories": ["astro-ph.GA"]},
        )
        seed = radar.resolve_seed(_seed_session(), "doc-1")
        assert seed["note"] == radar.NOTE_ARXIV_METADATA_UNAVAILABLE
        assert seed["categories_source"] == radar.CATEGORIES_SOURCE_SUBSCRIPTION

    def test_arxiv_rate_limit_has_its_own_note(self, monkeypatch):
        """PR7: 混雑（429）は「取得できなかった」と別の事実文で言う。

        教員の次の一手が違う（条件を手で入れるのか、待ってもう一度押すのか）。
        """
        _stub_fetch_by_ids(
            monkeypatch, arxiv_client.ArxivRateLimitedError("arXiv 側が混雑しています")
        )
        monkeypatch.setattr(radar.store, "get_subscription", lambda session, key: None)
        seed = radar.resolve_seed(_seed_session(), "doc-1")
        assert seed["note"] == radar.NOTE_ARXIV_RATE_LIMITED
        assert seed["note"] != radar.NOTE_ARXIV_METADATA_UNAVAILABLE
        assert seed["categories"] == []
        assert seed["categories_source"] == radar.CATEGORIES_SOURCE_MANUAL

    def test_empty_arxiv_response_is_stated_not_silent(self, monkeypatch):
        """200 だが該当なし（撤回・ID 誤り）を黙って空カテゴリにしない（PR7）。

        ここを黙ると、画面には「カテゴリが未指定です」だけが残り、条件ゼロで検索が
        成立しなかったことが「近い論文が無い」と読めてしまう。
        """
        _stub_fetch_by_ids(monkeypatch, [])
        monkeypatch.setattr(radar.store, "get_subscription", lambda session, key: None)
        seed = radar.resolve_seed(_seed_session(), "doc-1")
        assert seed["note"] == radar.NOTE_ARXIV_METADATA_NOT_FOUND
        assert seed["note"] != radar.NOTE_ARXIV_METADATA_UNAVAILABLE
        assert seed["categories_source"] == radar.CATEGORIES_SOURCE_MANUAL

    def test_successful_fetch_leaves_no_note(self, monkeypatch):
        """引けたときに注記を出さない（毎回出ると事実文が景色になる）。"""
        _stub_fetch_by_ids(
            monkeypatch,
            [_entry("2608.20293", categories=["astro-ph.CO"])],
        )
        seed = radar.resolve_seed(_seed_session(), "doc-1")
        assert "note" not in seed

    def test_non_arxiv_material_reports_unknown_id(self, no_arxiv):
        session = _seed_session(
            documents=[{"id": "doc-1", "source_path": "mat-1", "title": "手動", "source_url": ""}]
        )
        seed = radar.resolve_seed(session, "doc-1")
        assert seed["arxiv_id"] is None
        assert seed["abs_url"] is None
        assert seed["categories_source"] == radar.CATEGORIES_SOURCE_MANUAL

    def test_fetch_arxiv_false_skips_the_network(self, no_arxiv):
        seed = radar.resolve_seed(_seed_session(), "doc-1", fetch_arxiv=False)
        assert seed["arxiv_id"] == "2608.20293"
        assert seed["summary"] == ""

    def test_keyphrase_candidates_are_approved_components_only(self, no_arxiv):
        seed = radar.resolve_seed(_seed_session(), "doc-1", fetch_arxiv=False)
        texts = [phrase["text"] for phrase in seed["keyphrase_candidates"]]
        assert texts == ["dark energy", "w0waCDM"]
        assert all(phrase["source"] == "component" for phrase in seed["keyphrase_candidates"])
        assert "未承認の部品" not in texts

    def test_missing_document_raises(self, no_arxiv):
        with pytest.raises(LookupError):
            radar.resolve_seed(_seed_session(), "unknown")


# ---------------------------------------------------------------------------
# 3b. キーフレーズの供給段（設計書 §16 — 承認前の教材でも「カテゴリの新着」に縮退しない）
# ---------------------------------------------------------------------------


class _FakeConcept:
    def __init__(self, node_id, label):
        self.id = node_id
        self.label = label


class _FakeRegion:
    def __init__(self, node_id, label, concepts=()):
        self.id = node_id
        self.label = label
        self.concepts = tuple(concepts)


class _FakeSkeleton:
    def __init__(self, regions):
        self.regions = tuple(regions)


_KURTOSIS_TITLE = (
    "Kurtosis consistency relation in large-scale structure as a probe of gravity theories"
)


def _unapproved_session(**kwargs) -> FakeSession:
    """承認済み部品ゼロの seed（自律周回 1 周目の 2407.01221 と同じ状況）。"""
    base = {
        "documents": [
            {
                "id": "doc-1",
                "source_path": "mat-1",
                "title": _KURTOSIS_TITLE,
                "source_url": "https://arxiv.org/abs/2407.01221",
            }
        ],
        "components": [
            {"document_id": "doc-1", "name": "Kurtosis estimator", "review_status": "candidate"},
        ],
    }
    base.update(kwargs)
    return _seed_session(**base)


@pytest.fixture(autouse=True)
def _no_thesis_artifact(monkeypatch):
    """既定では中心命題の artifact は無い（読みたいテストだけが差し替える）。"""
    monkeypatch.setattr(radar, "_seed_artifacts", lambda session, document_id: {})


#: 実データ（2407.01221・scan-03）の形を写した主張の concepts。ほぼ全部が記号で、
#: 記号を除いた残りが文のような「概念」だった。
_REAL_SHAPE_CLAIMS = [
    {"document_id": "doc-1", "concepts": [
        {"name": "b_1", "concept_type": "symbol"},
        {"name": "\\beta_2", "concept_type": "symbol"},
        {"name": "\\kappa", "concept_type": "symbol"},
        {"name": "Conditional joint forecast: approximately 0.62 ≤ λ ≤ 1.41",
         "concept_type": "concept"},
    ]},
    {"document_id": "doc-1", "concepts": [
        {"name": "\\lambda", "concept_type": "symbol"},
        {"name": "Conditional joint forecast: approximately 0.92 ≤ κ ≤ 1.08",
         "concept_type": "concept"},
    ]},
]

#: 実データの thesis_reconstruction artifact の形（central_question は dict 形も来る）。
_REAL_THESIS = {
    "thesis_reconstruction": {
        "central_question": {"text": (
            "Can bias-independent consistency relations built from skewness and kurtosis "
            "of large-scale-structure density fluctuations be derived in DHOST or related "
            "modified-gravity settings, and can they test gravity?"
        )},
        "central_thesis": {"text": (
            "Two kurtosis consistency relations hold in DHOST theories and can be used "
            "to test modified gravity with large-scale structure."
        )},
    }
}


class TestSeedKeyphraseSupply:
    def _row(self, title=_KURTOSIS_TITLE):
        return {"document_id": "doc-1", "source_path": "mat-1", "title": title}

    def test_real_shape_symbols_and_sentences_are_not_supplied(self, monkeypatch):
        """実データ形: 記号だらけの concepts と文のような concept 名は供給されない。"""
        monkeypatch.setattr(radar, "_seed_artifacts", lambda session, document_id: _REAL_THESIS)
        session = _unapproved_session(claims=_REAL_SHAPE_CLAIMS)
        out = radar.seed_keyphrase_candidates(session, self._row())
        texts = [item["text"] for item in out]
        assert not any(item["source"] == "concept" for item in out)
        assert not any("forecast" in t or "≤" in t or "\\" in t or "_" in t for t in texts)
        lowered = {t.casefold() for t in texts}
        # 題名 ∩ 中心命題の句が先頭（出所は title）、中心命題の反復句（出所 thesis）。
        assert out[0]["source"] == "title"
        assert "large-scale structure" in lowered
        assert "modified gravity" in lowered
        assert any("consistency relation" in t for t in lowered)
        assert any("kurtosis" in t for t in lowered)
        sources = {item["text"].casefold(): item["source"] for item in out}
        assert sources["modified gravity"] == "thesis"
        assert {item["source"] for item in out} <= {"title", "thesis"}

    def test_concept_filter_rules(self):
        ok = radar.is_searchable_concept_name
        assert ok("modified gravity") and ok("kurtosis")
        assert not ok("Conditional joint forecast: approximately 0.62 ≤ λ ≤ 1.41")
        assert not ok("\\beta_2") and not ok("b_1") and not ok("重力")
        assert not ok("one two three four five six")          # 6 語
        assert not ok("x" * 49)                                # 49 字
        assert not ok("sigma 8 normalization")                 # 数字

    def test_supply_comes_from_claim_concepts_without_approved_components(self):
        session = _unapproved_session(
            claims=[
                {"document_id": "doc-1", "concepts": ["kurtosis", {"name": "modified gravity"}]},
                {"document_id": "doc-1", "concepts": [
                    "modified gravity", "\\sigma_8", "m_g",
                    {"name": "matter power", "concept_type": "symbol"},
                ]},
                {"document_id": "mat-1", "concepts": ["modified gravity", "重力"]},
            ]
        )
        out = radar.seed_keyphrase_candidates(session, self._row())
        concepts = [item["text"] for item in out if item["source"] == "concept"]
        # 出現回数順（modified gravity 3 回 > kurtosis 1 回）・記号様と非英字は除く。
        assert concepts == ["modified gravity", "kurtosis"]
        # 承認前の部品は使わない（PD3 / KR2）。
        assert "Kurtosis estimator" not in [item["text"] for item in out]
        # 段③の後に題名の段が続く（同じ語は先の段が勝つ）。
        assert [item["source"] for item in out[:2]] == ["concept", "concept"]
        assert all(item["source"] == "title" for item in out[2:])

    def test_confirmed_placements_come_before_concepts(self, monkeypatch):
        skeleton = _FakeSkeleton(
            [_FakeRegion("r1", "Large-scale structure", [_FakeConcept("c1", "Galaxy clustering")])]
        )
        monkeypatch.setattr(
            "core.atlas_store.load_learner_skeleton", lambda key, session: skeleton
        )
        session = _unapproved_session(
            placements=[
                {"document_id": "doc-1", "domain_key": "astrophysics", "node_id": "c1",
                 "node_kind": "concept", "status": "confirmed"},
                {"document_id": "doc-1", "domain_key": "astrophysics", "node_id": "r1",
                 "node_kind": "region", "status": "confirmed"},
                # AI 推定（inferred）は使わない。
                {"document_id": "doc-1", "domain_key": "astrophysics", "node_id": "c9",
                 "node_kind": "concept", "status": "inferred"},
            ],
            claims=[{"document_id": "doc-1", "concepts": ["galaxy clustering", "kurtosis"]}],
        )
        out = radar.seed_keyphrase_candidates(session, self._row())
        assert [(item["text"], item["source"]) for item in out[:3]] == [
            ("Galaxy clustering", "skeleton"),
            ("Large-scale structure", "skeleton"),
            # 同じ語は先の段が勝つ（出所を後の段で上書きしない）。
            ("kurtosis", "concept"),
        ]
        # 題名の「large-scale structure」は skeleton の同じ語に負ける。
        assert [item["text"].casefold() for item in out].count("large-scale structure") == 1

    def test_approved_components_stay_first(self):
        session = _seed_session(
            claims=[{"document_id": "doc-1", "concepts": ["dark energy", "growth rate"]}]
        )
        out = radar.seed_keyphrase_candidates(
            session, {"document_id": "doc-1", "source_path": "mat-1", "title": "起点論文"}
        )
        assert [(item["text"], item["source"]) for item in out] == [
            ("dark energy", "component"),
            ("w0waCDM", "component"),
            ("growth rate", "concept"),
        ]

    def test_title_alone_when_nothing_else(self):
        out = radar.seed_keyphrase_candidates(_unapproved_session(), self._row())
        assert [(item["text"], item["source"]) for item in out] == [
            ("Kurtosis consistency relation", "title"),
            ("large-scale structure", "title"),
            ("gravity theories", "title"),
        ]

    def test_supply_is_capped(self):
        concepts = [f"concept phrase {chr(97 + i)}" for i in range(20)]
        session = _unapproved_session(claims=[{"document_id": "doc-1", "concepts": concepts}])
        out = radar.seed_keyphrase_candidates(session, self._row())
        assert len(out) == radar.MAX_SEED_KEYPHRASES

    def test_failing_stage_does_not_break_the_others(self, monkeypatch):
        def _boom(*_a, **_k):
            raise RuntimeError("unavailable")

        monkeypatch.setattr("core.atlas_store.load_learner_skeleton", _boom)
        monkeypatch.setattr(radar, "_seed_artifacts", _boom)
        session = _unapproved_session(
            placements=[{"document_id": "doc-1", "domain_key": "astrophysics", "node_id": "c1",
                         "node_kind": "concept", "status": "confirmed"}],
            claims=[{"document_id": "doc-1", "concepts": ["kurtosis"]}],
        )
        out = radar.seed_keyphrase_candidates(session, self._row())
        assert out[0] == {"text": "kurtosis", "source": "concept", "enabled": True}
        # 中心命題が読めなくても題名の段は続く。
        assert any(item["source"] == "title" for item in out)

    def test_sources_are_in_the_catalog(self):
        for source in (
            radar.KEYPHRASE_SOURCE_COMPONENT, radar.KEYPHRASE_SOURCE_SKELETON,
            radar.KEYPHRASE_SOURCE_CONCEPT, radar.KEYPHRASE_SOURCE_TITLE,
            radar.KEYPHRASE_SOURCE_THESIS,
        ):
            assert source in schema.KEYPHRASE_SOURCES

    def test_title_keyphrases_rules(self):
        assert radar.title_keyphrases(_KURTOSIS_TITLE) == [
            "Kurtosis consistency relation", "large-scale structure", "gravity theories",
        ]
        # 英字の語が無い題名からは取らない（訳語を捏造しない）。
        assert radar.title_keyphrases("起点論文") == []
        # 5 語以上は 4 語ずつ・2 語以上の句が先・丸ごと 1 語の句は最後・上限あり。
        # 題名にしか出ない大文字 1 語（Alpha）は採らず、略号（ALPHA）は採る（§23）。
        assert radar.title_keyphrases("Alpha of Beta Gamma Delta Epsilon Zeta Eta") == [
            "Beta Gamma Delta Epsilon", "Zeta Eta",
        ]
        assert radar.title_keyphrases("ALPHA of Beta Gamma Delta Epsilon Zeta Eta") == [
            "Beta Gamma Delta Epsilon", "Zeta Eta", "ALPHA",
        ]
        many = " of ".join(f"w{i}a w{i}b" for i in range(8))
        assert len(radar.title_keyphrases(many)) == radar.MAX_TITLE_THESIS_KEYPHRASES

    def test_title_thesis_merge_order(self):
        thesis = radar._thesis_text_from_artifacts(_REAL_THESIS)
        out = radar.title_thesis_keyphrases(_KURTOSIS_TITLE, thesis)
        texts = [t.casefold() for t, _s in out]
        # 句の上限 + 固有名の別枠（§20 C）。
        assert len(out) <= radar.MAX_TITLE_THESIS_KEYPHRASES + radar.MAX_PROPER_NOUN_KEYPHRASES
        # 題名と中心命題の両方に現れる句が先頭。
        assert out[0][1] == "title"
        both_idx = max(texts.index("large-scale structure"), next(
            i for i, t in enumerate(texts) if "consistency relation" in t))
        assert texts.index("modified gravity") > both_idx
        # 句の切れ端（「Kurtosis consistency」）や断片の 1 語（「gravity」）を出さない。
        assert "kurtosis consistency" not in texts and "gravity" not in texts
        # 日本語の中心命題からは何も足さない（題名だけ）。
        ja = radar.title_thesis_keyphrases(_KURTOSIS_TITLE, "自由スカラー場を介した有効作用")
        assert ja == radar.title_thesis_keyphrases(_KURTOSIS_TITLE, "")

    def test_thesis_text_accepts_string_and_dict_forms(self):
        assert radar._thesis_text_from_artifacts({}) == ""
        assert radar._thesis_text_from_artifacts(
            {"thesis_reconstruction": {"central_question": "Why X?", "central_thesis": {"text": "Y."}}}
        ) == "Why X? . Y."

    def test_seed_reports_supply_and_note(self, no_arxiv):
        session = _unapproved_session(
            claims=[{"document_id": "doc-1", "concepts": ["kurtosis"]}]
        )
        seed = radar.resolve_seed(session, "doc-1", fetch_arxiv=False)
        assert seed["keyphrase_supply"] == {
            "sources": ["concept", "title"],
            "note": radar.NOTE_KEYPHRASE_SUPPLY_WITHOUT_APPROVED,
        }

    def test_seed_with_approved_components_has_no_note(self, no_arxiv):
        seed = radar.resolve_seed(_seed_session(), "doc-1", fetch_arxiv=False)
        assert seed["keyphrase_supply"] == {"sources": ["component"], "note": None}

    def test_seed_with_no_supply_says_so(self, no_arxiv):
        session = _unapproved_session(
            documents=[{"id": "doc-1", "source_path": "mat-1", "title": "起点論文",
                        "source_url": ""}]
        )
        seed = radar.resolve_seed(session, "doc-1", fetch_arxiv=False)
        assert seed["keyphrase_candidates"] == []
        assert seed["keyphrase_supply"] == {
            "sources": [], "note": radar.NOTE_KEYPHRASE_SUPPLY_EMPTY,
        }

    def test_notes_carry_no_numbers(self):
        for note in (
            radar.NOTE_KEYPHRASE_SUPPLY_EMPTY,
            radar.NOTE_KEYPHRASE_SUPPLY_WITHOUT_APPROVED,
            radar.NOTE_SEARCH_WITHOUT_KEYPHRASES,
        ):
            assert not re.search(r"\d", note)


class TestSearchWithoutKeyphrasesNote:
    def test_near_without_keyphrases_states_category_fallback(self, monkeypatch):
        _stub_fetch_by_ids(monkeypatch, [_entry("2407.01221", categories=["astro-ph.CO"])])
        _stub_search(monkeypatch, [_entry("2608.00002", title="A", summary="x")])
        _stub_band(monkeypatch)
        result = radar.run_radar_search(
            _seed_session(), "doc-1", distance="near", keyphrases=[]
        )
        assert result["keyphrase_note"] == radar.NOTE_SEARCH_WITHOUT_KEYPHRASES
        # 閉世界注記は従来どおり残す（置き換えない）。
        assert result["closed_world_note"]

    def test_disabled_keyphrases_count_as_none(self, monkeypatch):
        _stub_fetch_by_ids(monkeypatch, [_entry("2407.01221", categories=["astro-ph.CO"])])
        _stub_search(monkeypatch, [_entry("2608.00002", title="A", summary="x")])
        _stub_band(monkeypatch)
        result = radar.run_radar_search(
            _seed_session(), "doc-1", distance="near",
            keyphrases=[{"text": "dark energy", "source": "component", "enabled": False}],
        )
        assert result["keyphrase_note"] == radar.NOTE_SEARCH_WITHOUT_KEYPHRASES

    def test_near_with_keyphrases_has_no_note(self, monkeypatch):
        _stub_fetch_by_ids(monkeypatch, [_entry("2608.20293", categories=["astro-ph.CO"])])
        _stub_search(monkeypatch, [_entry("2608.00002", title="A", summary="dark energy")])
        _stub_band(monkeypatch)
        result = radar.run_radar_search(_seed_session(), "doc-1", distance="near")
        assert "keyphrase_note" not in result

    def test_mid_does_not_attach_the_note(self, monkeypatch):
        _stub_fetch_by_ids(monkeypatch, [_entry("2608.20293", categories=["astro-ph.CO"])])
        _stub_search(monkeypatch, [_entry("2608.00002", title="A", summary="x")])
        _stub_band(monkeypatch)
        result = radar.run_radar_search(
            _seed_session(), "doc-1", distance="mid", categories=["astro-ph.CO"]
        )
        # mid / far はもともとカテゴリだけの網（距離の選択肢がそう言っている）。
        assert "keyphrase_note" not in result


# ---------------------------------------------------------------------------
# 4a. arXiv メタデータの写し（migration 085 / 設計書 §14）
# ---------------------------------------------------------------------------


class TestSeedMetadataCache:
    """教員の一連の操作で arXiv を叩くのは最大2回（開く1回 + 検索1回）。

    写しは**外部事実**だけを持ち、候補・教員の判断は保存しない（PD5 / CC3 同型）。
    """

    def _cached_session(self, **kwargs):
        return _seed_session(
            arxiv_metadata=[
                _entry(
                    "2608.20293",
                    title="Seed",
                    summary="A cached abstract.",
                    categories=["astro-ph.CO"],
                    primary_category="astro-ph.CO",
                    abs_url="https://arxiv.org/abs/2608.20293",
                )
            ],
            **kwargs,
        )

    def test_fresh_cache_hit_does_not_call_arxiv(self, no_arxiv, monkeypatch):
        calls: list[list[str]] = []
        _stub_fetch_by_ids(monkeypatch, [], recorder=calls)
        seed = radar.resolve_seed(self._cached_session(), "doc-1")
        assert calls == [], "写しが新鮮なら arXiv を呼ばない（開き直しで再取得しない）"
        assert seed["metadata_source"] == radar.METADATA_SOURCE_CACHE
        assert seed["summary"] == "A cached abstract."
        assert seed["categories"] == ["astro-ph.CO"]
        assert seed["categories_source"] == radar.CATEGORIES_SOURCE_ARXIV
        assert "note" not in seed, "写しから読めたのは縮退ではない"

    def test_cache_miss_fetches_and_remembers(self, monkeypatch):
        calls: list[list[str]] = []
        _stub_fetch_by_ids(
            monkeypatch,
            [_entry("2608.20293", summary="A live abstract.", categories=["astro-ph.CO"])],
            recorder=calls,
        )
        session = _seed_session()
        seed = radar.resolve_seed(session, "doc-1")
        assert calls == [["2608.20293"]]
        assert seed["metadata_source"] == radar.METADATA_SOURCE_ARXIV
        assert session.metadata_writes == ["2608.20293"], "引けた事実だけを写しに残す"

    @pytest.mark.parametrize(
        "outcome",
        [
            arxiv_client.ArxivRateLimitedError("混雑"),
            arxiv_client.ArxivApiError("接続に失敗しました"),
            [],
        ],
    )
    def test_failures_are_not_cached(self, monkeypatch, outcome):
        _stub_fetch_by_ids(monkeypatch, outcome)
        session = _seed_session()
        seed = radar.resolve_seed(session, "doc-1")
        assert session.metadata_writes == [], "読めなかった記録を写しにしない"
        assert "metadata_source" not in seed, "引けていないのに出所を騙らない"
        assert seed["note"], "縮退の事実文は従来どおり立てる（PR7）"

    def test_inferred_seed_also_reads_the_cache(self, no_arxiv, monkeypatch):
        calls: list[list[str]] = []
        _stub_fetch_by_ids(monkeypatch, [], recorder=calls)
        session = _manual_session()
        session.arxiv_metadata = {
            "2407.01221": _entry(
                "2407.01221",
                title="Dark Energy: A Review",
                summary="A cached abstract.",
                categories=["astro-ph.CO"],
                abs_url="https://arxiv.org/abs/2407.01221",
            )
        }
        seed = radar.resolve_seed(session, "doc-1")
        assert calls == []
        assert seed["metadata_source"] == radar.METADATA_SOURCE_CACHE
        provenance = seed["provenance"]
        assert provenance["fetched"] is True
        assert provenance["title_match"] is True
        assert seed["categories_source"] == radar.CATEGORIES_SOURCE_ARXIV_INFERRED

    def test_fetch_arxiv_false_does_not_read_the_cache(self, no_arxiv):
        """条件が明示されている経路は写しも読まない（従来の挙動を変えない）。"""
        session = self._cached_session()
        seed = radar.resolve_seed(session, "doc-1", fetch_arxiv=False)
        assert seed["summary"] == ""
        assert "metadata_source" not in seed
        assert "paper_discovery_arxiv_metadata_cache" not in session.sql_log

    def test_search_results_are_remembered(self, monkeypatch):
        """検索で返った候補の要旨も写しに残す（比較分析が取り直さずに済む）。"""
        _stub_fetch_by_ids(monkeypatch, [_entry("2608.20293", categories=["astro-ph.CO"])])
        _stub_search(
            monkeypatch,
            [_entry("2608.00002", title="A", summary="x"),
             _entry("2608.00003", title="B", summary="y")],
        )
        _stub_band(monkeypatch)
        session = _seed_session()
        radar.run_radar_search(session, "doc-1", distance="near")
        assert session.metadata_writes == ["2608.20293", "2608.00002", "2608.00003"]

    def test_a_cache_write_failure_does_not_break_the_search(self, monkeypatch):
        """写しに書けないことは探索の失敗ではない（fail-soft）。"""
        _stub_fetch_by_ids(monkeypatch, [_entry("2608.20293", categories=["astro-ph.CO"])])
        monkeypatch.setattr(
            radar.metadata_cache,
            "upsert_entries",
            lambda session, entries: (_ for _ in ()).throw(RuntimeError("no table")),
        )
        session = _seed_session()
        session.rollback = lambda: None
        seed = radar.resolve_seed(session, "doc-1")
        assert seed["categories"] == ["astro-ph.CO"]
        assert seed["metadata_source"] == radar.METADATA_SOURCE_ARXIV

    def test_a_cache_read_failure_falls_back_to_arxiv(self, monkeypatch):
        calls: list[list[str]] = []
        _stub_fetch_by_ids(
            monkeypatch, [_entry("2608.20293", categories=["astro-ph.CO"])], recorder=calls
        )
        monkeypatch.setattr(
            radar.metadata_cache,
            "get_fresh",
            lambda session, ids, **kwargs: (_ for _ in ()).throw(RuntimeError("no table")),
        )
        seed = radar.resolve_seed(_seed_session(), "doc-1")
        assert calls == [["2608.20293"]]
        assert seed["metadata_source"] == radar.METADATA_SOURCE_ARXIV


# ---------------------------------------------------------------------------
# 4b. arXiv 出所の後付け（推定 seed + 記帳）
# ---------------------------------------------------------------------------


def _manual_session(**overrides) -> FakeSession:
    """手動アップロード相当（``source_url`` が空でファイル名に arXiv ID が残る教材）。"""
    document = {
        "id": "doc-1",
        "source_path": "mat-1",
        "title": "Dark Energy: A Review",
        "source_url": "",
        "filename": "arXiv-2407.01221v2.tar.gz",
    }
    document.update(overrides)
    return _seed_session(documents=[document], components=[])


class TestResolveSeedProvenance:
    def test_registered_material_reports_registered_provenance(self, monkeypatch):
        _stub_fetch_by_ids(monkeypatch, [_entry("2608.20293", categories=["astro-ph.CO"])])
        seed = radar.resolve_seed(_seed_session(), "doc-1")
        provenance = seed["provenance"]
        assert provenance["status"] == radar.PROVENANCE_STATUS_REGISTERED
        assert provenance["arxiv_id"] == "2608.20293"
        assert provenance["fetched"] is False
        assert provenance["title_match"] is False
        assert "can_register" not in provenance, "権限は core が知らない（route が注入する）"

    def test_nothing_to_infer_reports_none(self, no_arxiv):
        session = _manual_session(filename="lecture-notes.pdf", title="講義ノート")
        seed = radar.resolve_seed(session, "doc-1")
        provenance = seed["provenance"]
        assert provenance["status"] == radar.PROVENANCE_STATUS_NONE
        assert provenance["arxiv_id"] is None
        assert seed["categories_source"] == radar.CATEGORIES_SOURCE_MANUAL

    def test_infers_from_the_filename_and_prefills_the_conditions(self, monkeypatch):
        calls: list[list[str]] = []
        _stub_fetch_by_ids(
            monkeypatch,
            [
                _entry(
                    "2407.01221",
                    title="Dark Energy: A Review",
                    summary="An abstract.",
                    categories=["astro-ph.CO"],
                    primary_category="astro-ph.CO",
                    abs_url="https://arxiv.org/abs/2407.01221",
                )
            ],
            recorder=calls,
        )
        seed = radar.resolve_seed(_manual_session(), "doc-1")

        assert calls == [["2407.01221"]]
        assert seed["categories"] == ["astro-ph.CO"]
        assert seed["categories_source"] == radar.CATEGORIES_SOURCE_ARXIV_INFERRED
        assert seed["summary"] == "An abstract."
        # 推定は seed の登録済み ID に昇格しない（未記帳を偽装しない）。
        assert seed["arxiv_id"] is None
        assert seed["abs_url"] is None

        provenance = seed["provenance"]
        assert provenance["status"] == radar.PROVENANCE_STATUS_INFERRED
        assert provenance["arxiv_id"] == "2407.01221"
        assert provenance["fetched"] is True
        assert provenance["arxiv_title"] == "Dark Energy: A Review"
        assert provenance["arxiv_abs_url"] == "https://arxiv.org/abs/2407.01221"
        assert provenance["document_title"] == "Dark Energy: A Review"
        assert provenance["title_match"] is True

    def test_falls_back_to_the_title_when_the_filename_has_no_id(self, monkeypatch):
        _stub_fetch_by_ids(monkeypatch, [_entry("2407.01221", title="別の題名")])
        session = _manual_session(filename="paper.pdf", title="arXiv:2407.01221 の論文")
        provenance = radar.resolve_seed(session, "doc-1")["provenance"]
        assert provenance["status"] == radar.PROVENANCE_STATUS_INFERRED
        assert provenance["arxiv_id"] == "2407.01221"

    def test_title_mismatch_is_reported_without_blocking_the_prefill(self, monkeypatch):
        _stub_fetch_by_ids(
            monkeypatch,
            [_entry("2407.01221", title="A completely different paper",
                    categories=["astro-ph.CO"])],
        )
        seed = radar.resolve_seed(_manual_session(), "doc-1")
        assert seed["provenance"]["title_match"] is False
        assert seed["provenance"]["fetched"] is True
        assert seed["categories_source"] == radar.CATEGORIES_SOURCE_ARXIV_INFERRED

    def test_arxiv_failure_degrades_to_the_subscription_with_a_note(self, monkeypatch):
        """PR7: 推定 ID でも到達失敗は黙らせず、供給元を正直に切り替える。"""
        _stub_fetch_by_ids(monkeypatch, arxiv_client.ArxivApiError("接続に失敗しました"))
        monkeypatch.setattr(
            radar.store,
            "get_subscription",
            lambda session, key: {"arxiv_categories": ["astro-ph.GA"]},
        )
        seed = radar.resolve_seed(_manual_session(), "doc-1")
        assert seed["note"] == radar.NOTE_ARXIV_METADATA_UNAVAILABLE
        assert seed["categories_source"] == radar.CATEGORIES_SOURCE_SUBSCRIPTION
        assert seed["provenance"]["status"] == radar.PROVENANCE_STATUS_INFERRED
        assert seed["provenance"]["fetched"] is False

    def test_fetch_arxiv_false_keeps_the_inference_offline(self, no_arxiv):
        """compare.py の経路（``fetch_arxiv=False``）はネットワークに触れない。"""
        seed = radar.resolve_seed(_manual_session(), "doc-1", fetch_arxiv=False)
        assert seed["provenance"]["status"] == radar.PROVENANCE_STATUS_INFERRED
        assert seed["provenance"]["arxiv_id"] == "2407.01221"
        assert seed["provenance"]["fetched"] is False
        assert seed["categories_source"] == radar.CATEGORIES_SOURCE_MANUAL

    def test_resolve_seed_writes_only_the_external_fact_cache(self, monkeypatch):
        """PR1: 推定を seed 解決の副作用で記帳しない。

        2026-09-14 以降、探索経路が書くのは **arXiv が返したメタデータの写し**
        （migration 085 / 設計書 §14）だけ。``documents`` には触れない。
        """
        _stub_fetch_by_ids(monkeypatch, [_entry("2407.01221", title="Dark Energy: A Review")])
        session = _manual_session()
        radar.resolve_seed(session, "doc-1")
        assert "UPDATE documents" not in session.sql_log
        written = set(re.findall(r"INSERT INTO (\w+)", session.sql_log))
        assert written <= {"paper_discovery_arxiv_metadata_cache"}
        assert session.documents[0]["source_url"] == ""


class TestRegisterArxivProvenance:
    def test_writes_the_abs_url_without_the_version(self):
        session = _manual_session()
        url = radar.register_arxiv_provenance(session, "doc-1", "2407.01221v2")
        assert url == "https://arxiv.org/abs/2407.01221"
        assert session.documents[0]["source_url"] == url

    def test_accepts_url_forms(self):
        session = _manual_session()
        url = radar.register_arxiv_provenance(
            session, "doc-1", "https://arxiv.org/pdf/2407.01221v2"
        )
        assert url == "https://arxiv.org/abs/2407.01221"

    def test_only_updates_when_the_source_url_is_empty(self):
        session = _manual_session(source_url="https://arxiv.org/abs/2608.20293")
        with pytest.raises(ValueError):
            radar.register_arxiv_provenance(session, "doc-1", "2407.01221")
        assert session.documents[0]["source_url"] == "https://arxiv.org/abs/2608.20293"

    def test_unknown_document_raises(self):
        with pytest.raises(ValueError):
            radar.register_arxiv_provenance(_manual_session(), "doc-9", "2407.01221")

    def test_unparsable_id_raises_before_touching_the_db(self):
        session = _manual_session()
        with pytest.raises(ValueError):
            radar.register_arxiv_provenance(session, "doc-1", "not-an-id")
        assert session.calls == []

    def test_does_not_commit(self):
        """トランザクションを閉じるのは route 層（FakeSession.commit は失敗させる）。"""
        session = _manual_session()
        radar.register_arxiv_provenance(session, "doc-1", "2407.01221")

    def test_registered_material_then_resolves_as_registered(self, monkeypatch):
        _stub_fetch_by_ids(monkeypatch, [_entry("2407.01221", categories=["astro-ph.CO"])])
        session = _manual_session()
        radar.register_arxiv_provenance(session, "doc-1", "2407.01221")
        seed = radar.resolve_seed(session, "doc-1")
        assert seed["arxiv_id"] == "2407.01221"
        assert seed["provenance"]["status"] == radar.PROVENANCE_STATUS_REGISTERED
        assert seed["categories_source"] == radar.CATEGORIES_SOURCE_ARXIV


# ---------------------------------------------------------------------------
# 5. クエリ組み立てと検索
# ---------------------------------------------------------------------------


class TestBuildRadarQuery:
    _CATEGORIES = ["astro-ph.CO"]
    _PHRASES = [{"text": "dark energy", "source": "component", "enabled": True}]

    def test_near_uses_categories_and_keyphrases(self):
        query = radar.build_radar_query("near", self._CATEGORIES, self._PHRASES)
        assert query == '(cat:astro-ph.CO) AND (all:"dark energy")'

    @pytest.mark.parametrize("distance", ["mid", "far"])
    def test_mid_and_far_use_categories_only(self, distance):
        query = radar.build_radar_query(distance, self._CATEGORIES, self._PHRASES)
        assert query == "(cat:astro-ph.CO)"

    def test_no_categories_yields_an_empty_query(self):
        assert radar.build_radar_query("mid", [], self._PHRASES) == ""

    def test_unknown_distance_is_rejected(self):
        with pytest.raises(ValueError):
            radar.build_radar_query("nearby", self._CATEGORIES, self._PHRASES)


def _stub_search(monkeypatch, entries, *, total=None, recorder=None):
    def _fake(query, **kwargs):
        if recorder is not None:
            recorder.append((query, kwargs))
        return (total if total is not None else len(entries), list(entries))

    monkeypatch.setattr(radar.arxiv_client, "search", _fake)


def _stub_band(monkeypatch, *, available=True, note=None, recorder=None):
    def _fake(session, candidates, **kwargs):
        items = list(candidates)
        if recorder is not None:
            recorder.append({"candidates": items, "kwargs": dict(kwargs)})
        if not available:
            payload = {"available": False, "ordered": items}
            if note:
                payload["note"] = note
            return payload
        # 逆順に並べ替えてラベルを付ける（並び順の扱いの差を見えるようにする）。
        ordered = [dict(item, distance_label="近い") for item in reversed(items)]
        return {"available": True, "ordered": ordered}

    monkeypatch.setattr(radar.pd_ranking, "band_candidates", _fake)
    monkeypatch.setattr(radar.pd_ranking, "document_centroid", lambda session, doc: None)


class TestRunRadarSearch:
    def _entries(self):
        return [
            _entry("2608.20293", title="Seed", summary="s"),
            _entry("2608.00002", title="A", summary="dark energy study"),
            _entry("2608.00003", title="B", summary="other"),
        ]

    def test_excludes_the_seed_itself(self, monkeypatch):
        _stub_fetch_by_ids(monkeypatch, [_entry("2608.20293", categories=["astro-ph.CO"])])
        _stub_search(monkeypatch, self._entries())
        _stub_band(monkeypatch)
        result = radar.run_radar_search(_seed_session(), "doc-1", distance="near")
        assert "2608.20293" not in [c["arxiv_id"] for c in result["candidates"]]

    def test_annotates_ingested_status_without_dismissals(self, monkeypatch):
        _stub_fetch_by_ids(monkeypatch, [_entry("2608.20293", categories=["astro-ph.CO"])])
        _stub_search(monkeypatch, self._entries())
        _stub_band(monkeypatch)
        monkeypatch.setattr(
            radar.search,
            "visible_ingested_documents",
            lambda session, *, visible_document_ids: {
                "2608.00002": {
                    "document_id": "doc-2", "material_id": "mat-2",
                    "title": "A", "analysis_status": "completed",
                }
            },
        )
        result = radar.run_radar_search(_seed_session(), "doc-1", distance="near")
        statuses = {c["arxiv_id"]: c["status"] for c in result["candidates"]}
        assert statuses == {"2608.00002": "ingested", "2608.00003": "new"}
        assert "dismissed" not in set(statuses.values())


    def test_near_keeps_the_banded_order(self, monkeypatch):
        _stub_fetch_by_ids(monkeypatch, [_entry("2608.20293", categories=["astro-ph.CO"])])
        _stub_search(monkeypatch, self._entries())
        _stub_band(monkeypatch)
        result = radar.run_radar_search(_seed_session(), "doc-1", distance="near")
        # band のフェイクは逆順を返す（= 並べ替えが効いている）。
        assert [c["arxiv_id"] for c in result["candidates"]] == ["2608.00003", "2608.00002"]
        assert all(c["distance_label"] == "近い" for c in result["candidates"])

    @pytest.mark.parametrize("distance", ["mid", "far"])
    def test_mid_and_far_keep_the_submission_order(self, monkeypatch, distance):
        _stub_fetch_by_ids(monkeypatch, [_entry("2608.20293", categories=["astro-ph.CO"])])
        _stub_search(monkeypatch, self._entries())
        _stub_band(monkeypatch)
        result = radar.run_radar_search(
            _seed_session(), "doc-1", distance=distance, categories=["astro-ph.CO"]
        )
        # 遠い順に並べ替えない（疑似精度にしない）が、ラベルだけは受け取る。
        assert [c["arxiv_id"] for c in result["candidates"]] == ["2608.00002", "2608.00003"]
        assert all(c["distance_label"] == "近い" for c in result["candidates"])

    def test_matched_keyphrases_only_for_near(self, monkeypatch):
        _stub_fetch_by_ids(monkeypatch, [_entry("2608.20293", categories=["astro-ph.CO"])])
        _stub_search(monkeypatch, self._entries())
        _stub_band(monkeypatch)
        near = radar.run_radar_search(_seed_session(), "doc-1", distance="near")
        assert any(c["matched_keyphrases"] for c in near["candidates"])
        far = radar.run_radar_search(
            _seed_session(), "doc-1", distance="far", categories=["astro-ph.CO"]
        )
        assert all(c["matched_keyphrases"] == [] for c in far["candidates"])

    def test_empty_query_does_not_call_arxiv(self, monkeypatch):
        """PD6: 条件ゼロで arXiv を呼ばない（カテゴリも供給フレーズも無い教材）。"""
        _stub_fetch_by_ids(monkeypatch, [])
        monkeypatch.setattr(radar.store, "get_subscription", lambda session, key: None)
        monkeypatch.setattr(
            radar.arxiv_client,
            "search",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not search")),
        )
        result = radar.run_radar_search(
            _seed_session(components=[]), "doc-1", distance="near"
        )
        assert result["query"] == ""
        assert result["candidates"] == []
        assert result["banding"] == {"available": False}
        assert result["closed_world_note"]

    def test_does_not_touch_the_subscription_state(self, monkeypatch):
        """PR5 / 地図の端の集約ビットを汚さない。"""
        _stub_fetch_by_ids(monkeypatch, [_entry("2608.20293", categories=["astro-ph.CO"])])
        _stub_search(monkeypatch, self._entries())
        _stub_band(monkeypatch)
        monkeypatch.setattr(
            radar.store,
            "touch_last_checked",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not touch")),
        )
        radar.run_radar_search(_seed_session(), "doc-1", distance="near")

    def test_explicit_categories_skip_the_seed_metadata_fetch(self, monkeypatch, no_arxiv):
        _stub_search(monkeypatch, self._entries())
        _stub_band(monkeypatch)
        monkeypatch.setattr(
            radar.arxiv_client,
            "fetch_by_ids",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not fetch")),
        )
        result = radar.run_radar_search(
            _seed_session(), "doc-1", distance="near", categories=["astro-ph.CO"]
        )
        assert result["query"].startswith("(cat:astro-ph.CO)")

    def test_banding_degradation_is_reported(self, monkeypatch):
        _stub_fetch_by_ids(monkeypatch, [_entry("2608.20293", categories=["astro-ph.CO"])])
        _stub_search(monkeypatch, self._entries())
        _stub_band(monkeypatch, available=False, note=ranking.NOTE_NO_SEED)
        result = radar.run_radar_search(_seed_session(), "doc-1", distance="near")
        assert result["banding"] == {"available": False, "note": ranking.NOTE_NO_SEED}
        assert all("distance_label" not in c for c in result["candidates"])

    @pytest.mark.parametrize(
        ("distance", "expected_label"),
        [("near", "近い"), ("mid", "中間"), ("far", "遠い")],
    )
    def test_banding_declares_the_primary_label(self, monkeypatch, distance, expected_label):
        """選択距離の帯を UI が決定論的に展開できるよう ``primary_label`` を返す。

        ラベルの正本は ``core.label_vocab``（radar 側で文字列を再定義しない — PR2）。
        帯分け不能時は ``primary_label`` を付けない（測れていないのに帯を指さない）。
        """
        from core import label_vocab

        _stub_fetch_by_ids(monkeypatch, [_entry("2608.20293", categories=["astro-ph.CO"])])
        _stub_search(monkeypatch, self._entries())
        _stub_band(monkeypatch)
        result = radar.run_radar_search(
            _seed_session(), "doc-1", distance=distance, categories=["astro-ph.CO"]
        )
        assert result["banding"]["primary_label"] == expected_label
        assert result["banding"]["primary_label"] in label_vocab.RADAR_DISTANCE_SCALE.labels

    def test_unknown_distance_is_rejected(self, no_arxiv):
        with pytest.raises(ValueError):
            radar.run_radar_search(_seed_session(), "doc-1", distance="close")


# ---------------------------------------------------------------------------
# 5b. 「重なり」チップ（承認済み部品との語彙照合・非ベクトル）
# ---------------------------------------------------------------------------


class TestOverlapComponentLabels:
    def test_matches_title_and_summary_case_insensitively(self):
        candidate = {"title": "Dark Energy constraints", "summary": "using the CMB power"}
        got = radar.overlap_component_labels(
            candidate, ["dark energy", "cmb power", "supernovae"]
        )
        assert got == ["dark energy", "cmb power"]

    def test_keeps_the_input_order_and_is_deterministic(self):
        candidate = {"title": "beta alpha", "summary": ""}
        labels = ["alpha", "beta"]
        first = radar.overlap_component_labels(candidate, labels)
        assert first == ["alpha", "beta"]
        assert radar.overlap_component_labels(candidate, labels) == first

    def test_short_labels_are_skipped(self):
        """誤爆防止: 極端に短いラベルは部分一致に使わない。"""
        candidate = {"title": "A comparison of CP and other schemes", "summary": ""}
        assert radar.overlap_component_labels(candidate, ["CP"]) == []
        assert radar.MIN_OVERLAP_LABEL_CHARS == 3

    def test_respects_the_limit(self):
        candidate = {"title": "alpha beta gamma", "summary": ""}
        labels = ["alpha", "beta", "gamma"]
        assert radar.overlap_component_labels(candidate, labels) == labels
        assert radar.overlap_component_labels(candidate, labels, limit=2) == ["alpha", "beta"]
        assert radar.overlap_component_labels(candidate, labels, limit=0) == []

    def test_dedupes_labels_that_differ_only_in_case_or_spacing(self):
        candidate = {"title": "Dark Energy", "summary": "dark energy again"}
        got = radar.overlap_component_labels(
            candidate, ["dark energy", "Dark  Energy", "DARK ENERGY"]
        )
        assert got == ["dark energy"]

    def test_empty_inputs(self):
        assert radar.overlap_component_labels({}, ["dark energy"]) == []
        assert radar.overlap_component_labels({"title": "x"}, []) == []
        assert radar.overlap_component_labels({"title": "x"}, None) == []

    def test_no_counts_are_returned(self):
        """PR2 / LS5: 一致「数」やスコアを返さない（ラベル文字列だけ）。"""
        got = radar.overlap_component_labels(
            {"title": "dark energy", "summary": ""}, ["dark energy"]
        )
        assert got == ["dark energy"]
        assert all(isinstance(value, str) for value in got)


# ---------------------------------------------------------------------------
# 5c. 起点論文の配置ノード（「新しい面」の差集合の材料）
# ---------------------------------------------------------------------------


class TestSeedPlacedNodeIds:
    def _session(self, placements):
        return _seed_session(placements=placements)

    def test_collects_live_placements_only(self):
        session = self._session(
            [
                {"document_id": "doc-1", "domain_key": "astrophysics",
                 "node_id": "cmb", "status": "confirmed"},
                {"document_id": "doc-1", "domain_key": "astrophysics",
                 "node_id": "bao", "status": "inferred"},
                {"document_id": "doc-1", "domain_key": "astrophysics",
                 "node_id": "old", "status": "superseded"},
                {"document_id": "doc-1", "domain_key": "astrophysics",
                 "node_id": "no", "status": "rejected"},
            ]
        )
        assert radar.seed_placed_node_ids(session, "doc-1", "astrophysics") == {"cmb", "bao"}

    def test_scoped_to_the_document_and_the_domain(self):
        session = self._session(
            [
                {"document_id": "doc-2", "domain_key": "astrophysics",
                 "node_id": "other_doc", "status": "confirmed"},
                {"document_id": "doc-1", "domain_key": "particle_physics",
                 "node_id": "other_domain", "status": "confirmed"},
            ]
        )
        assert radar.seed_placed_node_ids(session, "doc-1", "astrophysics") == set()

    def test_empty_arguments_do_not_hit_the_database(self):
        session = self._session([])
        assert radar.seed_placed_node_ids(session, "", "astrophysics") == set()
        assert radar.seed_placed_node_ids(session, "doc-1", "") == set()
        assert "landscape_placements" not in session.sql_log

    def test_reads_only(self):
        session = self._session(
            [{"document_id": "doc-1", "domain_key": "astrophysics",
              "node_id": "cmb", "status": "confirmed"}]
        )
        radar.seed_placed_node_ids(session, "doc-1", "astrophysics")
        assert "landscape_placements" in session.sql_log
        upper = session.sql_log.upper()
        assert "INSERT" not in upper and "UPDATE" not in upper and "DELETE" not in upper

    def test_database_failure_degrades_to_an_empty_set(self):
        class _Boom:
            def execute(self, *args, **kwargs):
                raise RuntimeError("table missing")

        assert radar.seed_placed_node_ids(_Boom(), "doc-1", "astrophysics") == set()


# ---------------------------------------------------------------------------
# 5d. 地図との関係（着地予測・新しい面の配線 — VA層 §8）
# ---------------------------------------------------------------------------


class TestMergeDistanceLabels:
    """``mid`` / ``far`` の並び替え禁止でも、帯分けが付けた注釈は落とさない。"""

    def test_moves_every_annotation_key(self):
        originals = [{"arxiv_id": "a"}, {"arxiv_id": "b"}]
        banded = [
            {"arxiv_id": "b", "distance_label": "近い",
             "landing": {"node_label": "CMB"}, "new_facets": ["BAO"]},
            {"arxiv_id": "a"},
        ]
        got = radar._merge_distance_labels(originals, banded)
        assert [item["arxiv_id"] for item in got] == ["a", "b"]
        assert got[0] == {"arxiv_id": "a"}
        assert got[1]["distance_label"] == "近い"
        assert got[1]["landing"] == {"node_label": "CMB"}
        assert got[1]["new_facets"] == ["BAO"]

    def test_does_not_mutate_the_originals(self):
        originals = [{"arxiv_id": "a"}]
        radar._merge_distance_labels(
            originals, [{"arxiv_id": "a", "new_facets": ["BAO"]}]
        )
        assert originals == [{"arxiv_id": "a"}]


class _FakeAnchor:
    """``core.atlas_vectors.store.AnchorVector`` の最小形（属性しか見られない）。"""

    def __init__(self, node_id, vector, *, label=""):
        self.node_id = node_id
        self.node_kind = "concept"
        self.label = label or node_id
        self.region_label = ""
        self.vector = list(vector)


class TestRadarRelationContext:
    def _entries(self):
        return [_entry("2608.00002", title="A", summary="dark energy study")]

    def _run(self, monkeypatch, resolver, *, session=None, recorder=None, **kwargs):
        _stub_fetch_by_ids(monkeypatch, [_entry("2608.20293", categories=["astro-ph.CO"])])
        _stub_search(monkeypatch, self._entries())
        _stub_band(monkeypatch, recorder=recorder)
        return radar.run_radar_search(
            session if session is not None else _seed_session(),
            "doc-1",
            distance="near",
            anchor_context_resolver=resolver,
            **kwargs,
        )

    def test_absent_resolver_reports_unavailable(self, monkeypatch):
        result = self._run(monkeypatch, None)
        assert result["relation_context"] == {"available": False}

    def test_resolved_anchors_report_the_skeleton_version(self, monkeypatch):
        calls: list[str] = []

        def _resolver(domain_key):
            calls.append(domain_key)
            return {"anchors": [_FakeAnchor("cmb", [1.0, 0.0])],
                    "skeleton_version": "2026.1"}

        result = self._run(monkeypatch, _resolver)
        assert calls == ["astrophysics"]
        assert result["relation_context"] == {"available": True, "skeleton_version": "2026.1"}

    def test_resolver_result_is_forwarded_with_the_seed_placements(self, monkeypatch):
        recorder: list[dict] = []
        session = _seed_session(
            placements=[
                {"document_id": "doc-1", "domain_key": "astrophysics",
                 "node_id": "cmb", "status": "confirmed"},
            ]
        )
        anchors = [_FakeAnchor("cmb", [1.0, 0.0])]
        self._run(
            monkeypatch,
            lambda domain_key: {"anchors": anchors, "skeleton_version": "2026.1"},
            session=session,
            recorder=recorder,
        )
        context = recorder[0]["kwargs"]["anchor_context"]
        assert context["anchors"] is anchors
        assert context["skeleton_version"] == "2026.1"
        assert context["exclude_node_ids"] == {"cmb"}

    def test_unplaced_seed_omits_the_exclude_set(self, monkeypatch):
        """起点論文がその分野に1件も配置されていなければ ``exclude_node_ids`` を足さない。

        「起点論文は未言及」という対比は起点側の配置が最低1件あって初めて成立する。
        配置ゼロの分野で全ての近傍アンカーが「新しい面」に化けるのを構造的に防ぐ
        （キー不在 → ranking 側は ``new_facets`` を導出しない — 慎重側）。
        着地予測（landing）はこの抑止と独立に出る（anchor_context 自体は渡る）。
        """
        recorder: list[dict] = []
        anchors = [_FakeAnchor("cmb", [1.0, 0.0])]
        result = self._run(
            monkeypatch,
            lambda domain_key: {"anchors": anchors, "skeleton_version": "2026.1"},
            session=_seed_session(placements=[]),
            recorder=recorder,
        )
        context = recorder[0]["kwargs"]["anchor_context"]
        assert context is not None
        assert "exclude_node_ids" not in context
        assert result["relation_context"] == {"available": True, "skeleton_version": "2026.1"}

    def test_resolver_failure_degrades_without_breaking_the_search(self, monkeypatch):
        recorder: list[dict] = []

        def _boom(_domain_key):
            raise RuntimeError("vector layer down")

        result = self._run(monkeypatch, _boom, recorder=recorder)
        assert result["relation_context"] == {"available": False}
        assert recorder[0]["kwargs"]["anchor_context"] is None
        assert result["candidates"]

    def test_empty_context_is_unavailable(self, monkeypatch):
        recorder: list[dict] = []
        result = self._run(monkeypatch, lambda domain_key: None, recorder=recorder)
        assert result["relation_context"] == {"available": False}
        assert recorder[0]["kwargs"]["anchor_context"] is None

    def test_empty_query_still_reports_the_relation_context(self, monkeypatch):
        """条件ゼロ（arXiv を呼ばない）でもキーは必ず付く（黙らせない — VA8）。"""
        _stub_fetch_by_ids(monkeypatch, [])
        monkeypatch.setattr(radar.store, "get_subscription", lambda session, key: None)
        result = radar.run_radar_search(
            _seed_session(components=[]),
            "doc-1",
            distance="near",
            anchor_context_resolver=lambda domain_key: None,
        )
        assert result["query"] == ""
        assert result["relation_context"] == {"available": False}

    def test_overlap_components_are_attached_before_banding(self, monkeypatch):
        recorder: list[dict] = []
        result = self._run(monkeypatch, None, recorder=recorder)
        # 帯分けへ渡る時点で付いている（= 並べ替え後の候補にも乗る）。
        assert recorder[0]["candidates"][0]["overlap_components"] == ["dark energy"]
        assert result["candidates"][0]["overlap_components"] == ["dark energy"]

    def test_no_overlap_leaves_the_key_absent(self, monkeypatch):
        _stub_fetch_by_ids(monkeypatch, [_entry("2608.20293", categories=["astro-ph.CO"])])
        _stub_search(monkeypatch, [_entry("2608.00002", title="A", summary="unrelated")])
        _stub_band(monkeypatch)
        result = radar.run_radar_search(_seed_session(), "doc-1", distance="near")
        assert all("overlap_components" not in c for c in result["candidates"])


# ---------------------------------------------------------------------------
# 6. 距離帯（ranking への追加分）
# ---------------------------------------------------------------------------


def _stub_embeddings(monkeypatch, vectors, *, recorder=None):
    import core.llm as llm_module
    from core.llm_usage import context as usage_ctx

    def _fake(texts, **kwargs):
        if recorder is not None:
            recorder.append(
                {"texts": list(texts), "feature": usage_ctx.current_usage_context().feature}
            )
        if callable(vectors):
            return vectors(texts)
        return list(vectors)

    monkeypatch.setattr(llm_module, "generate_embeddings", _fake)


class TestDocumentCentroid:
    def test_averages_the_leading_chunks(self):
        session = FakeSession(
            chunks=[
                {"document_id": "doc-1", "chunk_index": 0, "embedding": [2.0, 0.0]},
                {"document_id": "doc-1", "chunk_index": 1, "embedding": [0.0, 0.0]},
            ]
        )
        assert ranking.document_centroid(session, "doc-1") == [1.0, 0.0]

    def test_respects_the_chunk_limit(self):
        session = FakeSession(
            chunks=[
                {"document_id": "doc-1", "chunk_index": 0, "embedding": [1.0, 0.0]},
                {"document_id": "doc-1", "chunk_index": 9, "embedding": [99.0, 0.0]},
            ]
        )
        assert ranking.document_centroid(session, "doc-1", chunks_per_document=1) == [1.0, 0.0]

    def test_no_chunks_is_none(self):
        assert ranking.document_centroid(FakeSession(), "doc-1") is None
        assert ranking.document_centroid(FakeSession(), "") is None


class TestBandCandidates:
    def _candidates(self):
        return [
            {"arxiv_id": "2608.00001", "title": "far", "summary": "unrelated"},
            {"arxiv_id": "2608.00002", "title": "near", "summary": "close"},
        ]

    def test_labels_come_from_the_canon_and_sort_by_similarity(self, monkeypatch):
        calls: list[dict] = []
        _stub_embeddings(monkeypatch, [[0.0, 1.0], [1.0, 0.0]], recorder=calls)
        result = ranking.band_candidates(
            FakeSession(), self._candidates(), seed_vector=[1.0, 0.0]
        )
        assert result["available"] is True
        assert [c["arxiv_id"] for c in result["ordered"]] == ["2608.00002", "2608.00001"]
        assert result["ordered"][0]["distance_label"] == RADAR_DISTANCE_SCALE.label_for(1.0)
        assert result["ordered"][1]["distance_label"] == RADAR_DISTANCE_SCALE.label_for(0.0)
        assert len(calls) == 1
        assert calls[0]["feature"] == "discovery:ranking"

    def test_unmeasurable_candidates_get_no_label(self, monkeypatch):
        """PR2: 未測定を最遠帯に化けさせない（キー自体を付けない）。"""
        _stub_embeddings(monkeypatch, [[1.0, 0.0], [0.0, 0.0]])
        result = ranking.band_candidates(
            FakeSession(), self._candidates(), seed_vector=[1.0, 0.0]
        )
        last = result["ordered"][-1]
        assert last["arxiv_id"] == "2608.00002"
        assert "distance_label" not in last

    def test_seed_text_is_embedded_in_the_same_batch(self, monkeypatch):
        calls: list[dict] = []
        _stub_embeddings(monkeypatch, [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]], recorder=calls)
        result = ranking.band_candidates(
            FakeSession(), self._candidates(), seed_text="seed abstract"
        )
        assert result["available"] is True
        assert len(calls) == 1, "seed 用の追加コールを増やさない"
        assert calls[0]["texts"][0] == "seed abstract"
        assert len(calls[0]["texts"]) == 3

    def test_no_seed_material_degrades_without_embedding(self, monkeypatch):
        calls: list[dict] = []
        _stub_embeddings(monkeypatch, [[1.0]], recorder=calls)
        result = ranking.band_candidates(FakeSession(), self._candidates())
        assert result["available"] is False
        assert result["note"] == ranking.NOTE_NO_SEED
        assert calls == []
        assert all("distance_label" not in c for c in result["ordered"])

    def test_embedding_failure_degrades(self, monkeypatch):
        import core.llm as llm_module

        monkeypatch.setattr(
            llm_module,
            "generate_embeddings",
            lambda texts, **kw: (_ for _ in ()).throw(RuntimeError("provider down")),
        )
        result = ranking.band_candidates(
            FakeSession(), self._candidates(), seed_vector=[1.0, 0.0]
        )
        assert result["available"] is False
        assert result["note"] == ranking.NOTE_UNAVAILABLE

    def test_daily_limit_is_shared_with_ranking(self, monkeypatch):
        _stub_embeddings(monkeypatch, [[1.0, 0.0]])
        first = ranking.band_candidates(
            FakeSession(), [{"title": "a"}], seed_vector=[1.0, 0.0], daily_limit=1
        )
        assert first["available"] is True
        second = ranking.band_candidates(
            FakeSession(), [{"title": "a"}], seed_vector=[1.0, 0.0], daily_limit=1
        )
        assert second["available"] is False
        assert second["note"] == ranking.NOTE_LIMIT_REACHED
        assert not any(ch.isdigit() for ch in second["note"])

    def test_empty_candidates(self):
        result = ranking.band_candidates(FakeSession(), [], seed_vector=[1.0, 0.0])
        assert result == {
            "available": False,
            "note": ranking.NOTE_NO_CANDIDATES,
            "ordered": [],
        }

    def test_input_candidates_are_not_mutated(self, monkeypatch):
        _stub_embeddings(monkeypatch, [[1.0, 0.0], [0.0, 1.0]])
        original = self._candidates()
        ranking.band_candidates(FakeSession(), original, seed_vector=[1.0, 0.0])
        assert all("distance_label" not in c for c in original)

    def test_no_raw_similarity_in_the_dto(self, monkeypatch):
        _stub_embeddings(monkeypatch, [[1.0, 0.0], [0.0, 1.0]])
        result = ranking.band_candidates(
            FakeSession(), self._candidates(), seed_vector=[1.0, 0.0]
        )
        for candidate in result["ordered"]:
            assert not [v for v in candidate.values() if isinstance(v, float)]


# ---------------------------------------------------------------------------
# 7. 比較分析（validator と入口ゲート）
# ---------------------------------------------------------------------------


ABSTRACT = "We use a Bayesian approach to constrain the equation of state."


def _candidate_entry(arxiv_id="2608.00002", summary=ABSTRACT):
    return schema.ArxivEntry(arxiv_id=arxiv_id, title="A candidate", summary=summary)


def _parsed(items):
    return compare_mod._CompareOutput(**{"items": items})  # noqa: SLF001


class TestCompareValidator:
    def test_keeps_verbatim_quotes(self):
        items, notes = compare_mod.validate_items(
            _parsed(
                [
                    {
                        "arxiv_id": "2608.00002",
                        "common_ground": "どちらも状態方程式を扱っているようです。",
                        "differences": [
                            {
                                "aspect": "method",
                                "statement": "統計手法が異なるように見えます。",
                                "evidence_quote": "a Bayesian approach",
                            }
                        ],
                    }
                ]
            ),
            [_candidate_entry()],
        )
        assert notes == []
        assert items[0]["differences"][0]["aspect"] == "method"
        assert items[0]["caveat"] == compare_mod.CAVEAT

    def test_drops_only_the_fabricated_difference(self):
        items, notes = compare_mod.validate_items(
            _parsed(
                [
                    {
                        "arxiv_id": "2608.00002",
                        "common_ground": "共通点。",
                        "differences": [
                            {
                                "aspect": "method",
                                "statement": "本文に無い引用。",
                                "evidence_quote": "a frequentist approach",
                            },
                            {
                                "aspect": "scope",
                                "statement": "対象が違うようです。",
                                "evidence_quote": "the equation of state",
                            },
                        ],
                    }
                ]
            ),
            [_candidate_entry()],
        )
        assert [d["aspect"] for d in items[0]["differences"]] == ["scope"]
        # 具体的な語を含まない共通点は落として adjacent へ降格する（§18。item は保持）。
        assert items[0]["common_ground"] == ""
        assert items[0]["relation_kind"] == "adjacent"
        assert compare_mod.NOTE_QUOTE_NOT_VERBATIM in notes

    def test_unknown_aspect_falls_back_without_dropping(self):
        items, _notes = compare_mod.validate_items(
            _parsed(
                [
                    {
                        "arxiv_id": "2608.00002",
                        "common_ground": "共通点。",
                        "differences": [
                            {
                                "aspect": "novelty",
                                "statement": "違いがありそうです。",
                                "evidence_quote": "a Bayesian approach",
                            }
                        ],
                    }
                ]
            ),
            [_candidate_entry()],
        )
        assert items[0]["differences"][0]["aspect"] == compare_mod.ASPECT_UNKNOWN

    def test_order_follows_the_request_not_the_llm(self):
        candidates = [_candidate_entry("2608.00002"), _candidate_entry("2608.00003")]
        items, _notes = compare_mod.validate_items(
            _parsed(
                [
                    {"arxiv_id": "2608.00003", "common_ground": "b", "differences": []},
                    {"arxiv_id": "2608.00002", "common_ground": "a", "differences": []},
                ]
            ),
            candidates,
        )
        assert [item["arxiv_id"] for item in items] == ["2608.00002", "2608.00003"]

    def test_missing_item_is_reported(self):
        items, notes = compare_mod.validate_items(
            _parsed([{"arxiv_id": "2608.00002", "common_ground": "a", "differences": []}]),
            [_candidate_entry("2608.00002"), _candidate_entry("2608.00003")],
        )
        assert len(items) == 1
        assert compare_mod.NOTE_ITEM_MISSING in notes

    def test_unknown_arxiv_id_is_ignored(self):
        items, _notes = compare_mod.validate_items(
            _parsed([{"arxiv_id": "9999.99999", "common_ground": "x", "differences": []}]),
            [_candidate_entry()],
        )
        assert items == []

    def test_no_numeric_keys_in_the_dto(self):
        items, _notes = compare_mod.validate_items(
            _parsed(
                [
                    {
                        "arxiv_id": "2608.00002",
                        "common_ground": "a",
                        "differences": [
                            {
                                "aspect": "theme",
                                "statement": "s",
                                "evidence_quote": "a Bayesian approach",
                            }
                        ],
                    }
                ]
            ),
            [_candidate_entry()],
        )
        assert set(items[0]) == {
            "arxiv_id",
            "title",
            "relation_kind",
            "relation_label",
            "common_ground",
            "overlaps",
            "differences",
            "caveat",
        }
        assert set(items[0]["differences"][0]) == {"aspect", "statement", "evidence_quote"}


class TestCompareOverlaps:
    """重なり2区画（``overlaps``）— 逐語ガードと部品名の閉世界（P4 併記）。"""

    LABELS = ["軸性場–光子結合", "状態方程式"]

    def _parsed_overlaps(self, overlaps):
        return _parsed(
            [
                {
                    "arxiv_id": "2608.00002",
                    "common_ground": "共通点。",
                    "overlaps": overlaps,
                    "differences": [],
                }
            ]
        )

    def test_keeps_verbatim_overlap_with_a_known_label(self):
        items, notes = compare_mod.validate_items(
            self._parsed_overlaps(
                [
                    {
                        "component_label": "状態方程式",
                        "statement": "同じ量を別の文脈で扱っていそうです。",
                        "evidence_quote": "the equation of state",
                    }
                ]
            ),
            [_candidate_entry()],
            self.LABELS,
        )
        assert notes == []
        assert items[0]["overlaps"] == [
            {
                "component_label": "状態方程式",
                "statement": "同じ量を別の文脈で扱っていそうです。",
                "evidence_quote": "the equation of state",
            }
        ]

    def test_drops_only_the_fabricated_overlap(self):
        items, notes = compare_mod.validate_items(
            self._parsed_overlaps(
                [
                    {
                        "component_label": "状態方程式",
                        "statement": "本文に無い引用。",
                        "evidence_quote": "a frequentist approach",
                    },
                    {
                        "component_label": "状態方程式",
                        "statement": "重なっていそうです。",
                        "evidence_quote": "the equation of state",
                    },
                ]
            ),
            [_candidate_entry()],
            self.LABELS,
        )
        assert [o["statement"] for o in items[0]["overlaps"]] == ["重なっていそうです。"]
        assert compare_mod.NOTE_QUOTE_NOT_VERBATIM in notes

    def test_unknown_label_is_blanked_but_the_statement_survives(self):
        """リスト外の部品名は空へ（存在しない名前は出さない）。項目は捨てない（P4）。"""
        items, notes = compare_mod.validate_items(
            self._parsed_overlaps(
                [
                    {
                        "component_label": "架空の部品",
                        "statement": "重なっていそうです。",
                        "evidence_quote": "a Bayesian approach",
                    }
                ]
            ),
            [_candidate_entry()],
            self.LABELS,
        )
        assert items[0]["overlaps"] == [
            {
                "component_label": "",
                "statement": "重なっていそうです。",
                "evidence_quote": "a Bayesian approach",
            }
        ]
        assert notes == []

    def test_label_matching_ignores_case_and_returns_the_supplied_spelling(self):
        items, _notes = compare_mod.validate_items(
            self._parsed_overlaps(
                [
                    {
                        "component_label": "BAYESIAN Inference",
                        "statement": "重なっていそうです。",
                        "evidence_quote": "a Bayesian approach",
                    }
                ]
            ),
            [_candidate_entry()],
            ["Bayesian inference"],
        )
        assert items[0]["overlaps"][0]["component_label"] == "Bayesian inference"

    def test_empty_statement_is_dropped(self):
        items, notes = compare_mod.validate_items(
            self._parsed_overlaps(
                [
                    {
                        "component_label": "状態方程式",
                        "statement": "",
                        "evidence_quote": "the equation of state",
                    }
                ]
            ),
            [_candidate_entry()],
            self.LABELS,
        )
        assert items[0]["overlaps"] == []
        assert compare_mod.NOTE_QUOTE_NOT_VERBATIM in notes

    def test_common_ground_and_differences_are_untouched(self):
        """既存キーは非改変（フロントは overlaps 空 + common_ground で従来表示へ縮退）。"""
        items, notes = compare_mod.validate_items(
            _parsed(
                [
                    {
                        "arxiv_id": "2608.00002",
                        "common_ground": "どちらも状態方程式を扱っているようです。",
                        "overlaps": [],
                        "differences": [
                            {
                                "aspect": "method",
                                "statement": "統計手法が異なるように見えます。",
                                "evidence_quote": "a Bayesian approach",
                            }
                        ],
                    }
                ]
            ),
            [_candidate_entry()],
            self.LABELS,
        )
        assert notes == []
        assert items[0]["common_ground"] == "どちらも状態方程式を扱っているようです。"
        assert items[0]["differences"][0]["aspect"] == "method"
        assert items[0]["overlaps"] == []

    def test_works_without_any_component_labels(self):
        """部品リストが空でも比較は成立する（重なりに部品参照が付かないだけ）。"""
        items, _notes = compare_mod.validate_items(
            self._parsed_overlaps(
                [
                    {
                        "component_label": "状態方程式",
                        "statement": "重なっていそうです。",
                        "evidence_quote": "a Bayesian approach",
                    }
                ]
            ),
            [_candidate_entry()],
            [],
        )
        assert items[0]["overlaps"][0]["component_label"] == ""
        assert items[0]["overlaps"][0]["statement"] == "重なっていそうです。"

    def test_overlaps_default_to_empty_when_the_llm_omits_them(self):
        items, _notes = compare_mod.validate_items(
            _parsed([{"arxiv_id": "2608.00002", "common_ground": "a", "differences": []}]),
            [_candidate_entry()],
        )
        assert items[0]["overlaps"] == []

    def test_seed_component_labels_filters_the_source_vocabulary(self):
        labels = compare_mod.seed_component_labels(
            {
                "keyphrase_candidates": [
                    {"text": "状態方程式", "source": "component"},
                    {"text": "skeleton 由来", "source": "skeleton"},
                    {"text": "状態方程式", "source": "component"},
                    {"text": "", "source": "component"},
                ]
            }
        )
        assert labels == ["状態方程式"]

    def test_seed_component_labels_is_empty_without_candidates(self):
        assert compare_mod.seed_component_labels({}) == []

    def test_prompt_lists_the_component_parts(self):
        content = compare_mod.build_prompt(
            {"title": "起点", "summary": "要旨"}, [_candidate_entry()], self.LABELS
        )
        assert "【起点論文の部品リスト】" in content
        assert "- 状態方程式" in content
        assert "重なり（overlaps）は" in content

    def test_prompt_omits_the_part_list_when_empty(self):
        content = compare_mod.build_prompt(
            {"title": "起点", "summary": "要旨"}, [_candidate_entry()], []
        )
        assert "【起点論文の部品リスト】" not in content
        # 重なりの指示自体は残る（部品参照が付かないだけ）。
        assert "重なり（overlaps）は" in content


class TestCompareMaterialGate:
    def test_seed_material_is_read_from_artifacts(self):
        material = compare_mod.build_seed_material(
            {"title": "起点", "summary": ""},
            artifacts={
                "paper_skeleton": {"paper_goal": {"text": "目的の文"}},
                "thesis_reconstruction": {
                    "central_thesis": {"text": "中心命題"},
                    "central_question": "中心的な問い",
                },
            },
        )
        assert material["paper_goal"] == "目的の文"
        assert material["central_thesis"] == "中心命題"
        assert material["central_question"] == "中心的な問い"
        assert compare_mod.has_seed_material(material) is True

    def test_title_alone_is_not_material(self):
        material = compare_mod.build_seed_material({"title": "起点"}, artifacts={})
        assert compare_mod.has_seed_material(material) is False

    def test_run_compare_without_material_does_not_call_the_llm(self, monkeypatch):
        called: list[str] = []
        monkeypatch.setattr(
            compare_mod, "_call_llm", lambda *a, **k: called.append("llm")
        )
        monkeypatch.setattr(
            compare_mod.arxiv_client,
            "fetch_by_ids",
            lambda ids, **kw: [_candidate_entry()],
        )
        monkeypatch.setattr(
            "core.deliberation.refs.document_run_artifacts", lambda document_id: {}
        )
        session = _seed_session(
            documents=[{"id": "doc-1", "source_path": "mat-1", "title": "t", "source_url": ""}]
        )
        with pytest.raises(compare_mod.NoSeedMaterialError):
            compare_mod.run_compare(session, "doc-1", ["2608.00002"])
        assert called == []

    def test_no_candidate_metadata_raises_unavailable(self, monkeypatch):
        monkeypatch.setattr(compare_mod.arxiv_client, "fetch_by_ids", lambda ids, **kw: [])
        with pytest.raises(compare_mod.CompareUnavailableError):
            compare_mod.run_compare(_seed_session(), "doc-1", ["2608.00002"])

    def test_seed_abstract_rides_the_same_fetch(self, monkeypatch):
        seen: list[list[str]] = []

        def _fetch(ids, **kwargs):
            seen.append(list(ids))
            return [
                schema.ArxivEntry(arxiv_id="2608.20293", summary="seed abstract"),
                _candidate_entry(),
            ]

        monkeypatch.setattr(compare_mod.arxiv_client, "fetch_by_ids", _fetch)
        monkeypatch.setattr(
            "core.deliberation.refs.document_run_artifacts", lambda document_id: {}
        )
        monkeypatch.setattr(
            compare_mod,
            "_call_llm",
            lambda content, model: _parsed(
                [{"arxiv_id": "2608.00002", "common_ground": "a", "differences": []}]
            ),
        )
        result = compare_mod.run_compare(_seed_session(), "doc-1", ["2608.00002"])
        assert seen == [["2608.20293", "2608.00002"]], "arXiv 呼び出しは1回に相乗りさせる"
        assert result["items"][0]["caveat"] == compare_mod.CAVEAT
        assert result["skipped"] == []

    def test_cached_abstracts_skip_the_arxiv_call(self, monkeypatch):
        """設計書 §14: 直前の検索で写しが在れば比較の arXiv 呼び出しは 0 回。"""
        seen: list[list[str]] = []

        def _fetch(ids, **kwargs):  # pragma: no cover — 呼ばれないことを検査する
            seen.append(list(ids))
            return []

        monkeypatch.setattr(compare_mod.arxiv_client, "fetch_by_ids", _fetch)
        monkeypatch.setattr(
            "core.deliberation.refs.document_run_artifacts", lambda document_id: {}
        )
        monkeypatch.setattr(
            compare_mod,
            "_call_llm",
            lambda content, model: _parsed(
                [{"arxiv_id": "2608.00002", "common_ground": "a", "differences": []}]
            ),
        )
        session = _seed_session(
            arxiv_metadata=[
                _entry("2608.20293", summary="seed abstract"),
                _entry("2608.00002", title="候補", summary="the equation of state"),
            ]
        )
        result = compare_mod.run_compare(session, "doc-1", ["2608.00002"])
        assert seen == [], "写しで足りるなら arXiv を呼ばない"
        assert session.metadata_writes == [], "読んだだけで書き戻さない"
        assert result["skipped"] == []
        assert result["items"][0]["arxiv_id"] == "2608.00002"

    def test_missing_ids_are_fetched_and_remembered(self, monkeypatch):
        """写しに無い分だけを取りに行き、引けた分を写しに残す（read-through）。"""
        seen: list[list[str]] = []

        def _fetch(ids, **kwargs):
            seen.append(list(ids))
            return [_candidate_entry()]

        monkeypatch.setattr(compare_mod.arxiv_client, "fetch_by_ids", _fetch)
        monkeypatch.setattr(
            "core.deliberation.refs.document_run_artifacts", lambda document_id: {}
        )
        monkeypatch.setattr(
            compare_mod,
            "_call_llm",
            lambda content, model: _parsed(
                [{"arxiv_id": "2608.00002", "common_ground": "a", "differences": []}]
            ),
        )
        session = _seed_session(
            arxiv_metadata=[_entry("2608.20293", summary="seed abstract")]
        )
        compare_mod.run_compare(session, "doc-1", ["2608.00002"])
        assert seen == [["2608.00002"]], "写しに在る seed は取り直さない"
        assert session.metadata_writes == ["2608.00002"]

    def test_unfetchable_candidates_are_reported_as_skipped(self, monkeypatch):
        monkeypatch.setattr(
            compare_mod.arxiv_client,
            "fetch_by_ids",
            lambda ids, **kw: [
                schema.ArxivEntry(arxiv_id="2608.20293", summary="seed abstract"),
                _candidate_entry(),
            ],
        )
        monkeypatch.setattr(
            "core.deliberation.refs.document_run_artifacts", lambda document_id: {}
        )
        monkeypatch.setattr(
            compare_mod, "_call_llm", lambda content, model: _parsed([])
        )
        result = compare_mod.run_compare(
            _seed_session(), "doc-1", ["2608.00002", "2608.09999"]
        )
        assert [row["arxiv_id"] for row in result["skipped"]] == ["2608.09999"]

    def test_llm_failure_raises_unavailable(self, monkeypatch):
        monkeypatch.setattr(
            compare_mod.arxiv_client,
            "fetch_by_ids",
            lambda ids, **kw: [
                schema.ArxivEntry(arxiv_id="2608.20293", summary="seed abstract"),
                _candidate_entry(),
            ],
        )
        monkeypatch.setattr(
            "core.deliberation.refs.document_run_artifacts", lambda document_id: {}
        )

        def _boom(content, model):
            raise RuntimeError("provider down")

        monkeypatch.setattr(compare_mod, "_call_llm", _boom)
        with pytest.raises(compare_mod.CompareUnavailableError):
            compare_mod.run_compare(_seed_session(), "doc-1", ["2608.00002"])

    def test_run_compare_supplies_the_seed_parts_and_returns_overlaps(self, monkeypatch):
        """seed 解決の部品ラベルがプロンプトへ渡り、overlaps が透過する（追加コールなし）。"""
        seen: dict[str, str] = {}

        monkeypatch.setattr(
            compare_mod.arxiv_client,
            "fetch_by_ids",
            lambda ids, **kw: [
                schema.ArxivEntry(arxiv_id="2608.20293", summary="seed abstract"),
                _candidate_entry(),
            ],
        )
        monkeypatch.setattr(
            "core.deliberation.refs.document_run_artifacts", lambda document_id: {}
        )

        def _call(content, model):
            seen["content"] = content
            return _parsed(
                [
                    {
                        "arxiv_id": "2608.00002",
                        "common_ground": "共通点。",
                        "overlaps": [
                            {
                                "component_label": "dark energy",
                                "statement": "同じ対象を扱っていそうです。",
                                "evidence_quote": "the equation of state",
                            }
                        ],
                        "differences": [],
                    }
                ]
            )

        monkeypatch.setattr(compare_mod, "_call_llm", _call)
        result = compare_mod.run_compare(_seed_session(), "doc-1", ["2608.00002"])

        assert "【起点論文の部品リスト】" in seen["content"]
        assert "- dark energy" in seen["content"]
        # 未承認の部品はキーフレーズ供給元に載らない（閉世界に混ぜない）。
        assert "未承認の部品" not in seen["content"]
        assert result["items"][0]["overlaps"][0]["component_label"] == "dark energy"

    def test_prompt_carries_the_required_constraints(self):
        content = compare_mod.build_prompt(
            {"title": "起点", "summary": "要旨"}, [_candidate_entry()]
        )
        assert "アブストラクトに書かれていることだけを比較する" in content
        assert "断定せず推量形で書く" in content
        assert "数値スコア・優劣の評価を書かない" in content
        assert "2608.00002" in content


class TestCompareIngestedCandidateAnalysis:
    """IK-0818: 取り込み済み候補の解析結果（部品・中心命題）も比較の材料にする（§22.5）。"""

    THESIS = "The VDG damping function models the small-scale Fingers-of-God effect."

    def _analysis(self, approved=True):
        return compare_mod.candidate_analysis_from(
            "doc-cand",
            [("VDG damping", "teacher_approved" if approved else "candidate"),
             ("却下済み", "rejected")],
            {"thesis_reconstruction": {"central_thesis": {"text": self.THESIS}}},
        )

    def test_analysis_prefers_approved_and_labels_unapproved(self):
        approved = self._analysis(approved=True)
        assert approved["labels"] == ["VDG damping"]
        assert approved["labels_approved"] is True
        assert approved["central_thesis"] == self.THESIS
        unapproved = self._analysis(approved=False)
        assert unapproved["labels"] == ["VDG damping"]
        assert unapproved["labels_approved"] is False
        # 承認済みがあれば未承認は混ぜない。
        mixed = compare_mod.candidate_analysis_from(
            "d", [("A", "teacher_approved"), ("B", "candidate")], {}
        )
        assert mixed["labels"] == ["A"]

    def test_prompt_without_analyses_is_unchanged(self):
        base = compare_mod.build_prompt(
            {"title": "起点", "summary": "要旨"}, [_candidate_entry()], ["FoG"]
        )
        same = compare_mod.build_prompt(
            {"title": "起点", "summary": "要旨"}, [_candidate_entry()], ["FoG"], {}
        )
        assert base == same
        assert "取り込み済み" not in base

    def test_prompt_carries_the_candidate_analysis(self):
        content = compare_mod.build_prompt(
            {"title": "起点", "summary": "要旨"},
            [_candidate_entry()],
            ["FoG"],
            {"2608.00002": self._analysis(approved=False)},
        )
        assert "（取り込み済み — こちらの解析結果）" in content
        assert f"中心命題: {self.THESIS}" in content
        assert "部品リスト（承認前）:" in content
        assert "  - VDG damping" in content
        assert "起点論文の部品リストか、その候補の部品リスト" in content
        assert "却下済み" not in content
        # 既存の制約は残る（PR4）。
        assert "断定せず推量形で書く" in content

    def test_thesis_quote_and_candidate_side_are_accepted(self):
        items, notes = compare_mod.validate_items(
            _parsed(
                [
                    {
                        "arxiv_id": "2608.00002",
                        "relation_kind": "shared_subject",
                        "common_ground": "VDG damping と FoG を扱うようです。",
                        "overlaps": [
                            {
                                "component_label": "vdg damping",
                                "statement": "候補の減衰関数は起点の FoG の模型にあたるようです。",
                                "evidence_quote": "models the small-scale Fingers-of-God effect",
                            },
                            {
                                "component_label": "FoG",
                                "statement": "要旨にも出ているようです。",
                                "evidence_quote": "the equation of state",
                            },
                        ],
                        "differences": [
                            {
                                "aspect": "theme",
                                "statement": "主題が違うようです。",
                                "evidence_quote": "The VDG damping function",
                            }
                        ],
                    }
                ]
            ),
            [_candidate_entry()],
            ["FoG"],
            candidate_analyses={"2608.00002": self._analysis(approved=False)},
        )
        assert notes == []
        item = items[0]
        assert item["candidate_analysis_used"] is True
        assert item["analysis_note"] == compare_mod.NOTE_CANDIDATE_ANALYSIS_USED
        assert item["caveat"] == compare_mod.CAVEAT
        first, second = item["overlaps"]
        assert first["component_label"] == "VDG damping"
        assert first["side"] == "candidate"
        assert first["evidence_source"] == "thesis"
        assert compare_mod.NOTE_SOURCE_CANDIDATE_PART in first["source_note"]
        assert compare_mod.NOTE_SOURCE_UNAPPROVED_PART in first["source_note"]
        assert compare_mod.NOTE_SOURCE_THESIS_QUOTE in first["source_note"]
        assert second["side"] == "seed"
        assert second["evidence_source"] == "abstract"
        assert "source_note" not in second
        assert item["differences"][0]["evidence_source"] == "thesis"
        assert not [v for v in first.values() if isinstance(v, float)]

    def test_both_side_and_unknown_label(self):
        items, _ = compare_mod.validate_items(
            _parsed(
                [
                    {
                        "arxiv_id": "2608.00002",
                        "overlaps": [
                            {"component_label": "VDG damping", "statement": "両方。",
                             "evidence_quote": "the equation of state"},
                            {"component_label": "無い部品", "statement": "どれでもない。",
                             "evidence_quote": "the equation of state"},
                        ],
                    }
                ]
            ),
            [_candidate_entry()],
            ["VDG damping"],
            candidate_analyses={"2608.00002": self._analysis(approved=True)},
        )
        both, unknown = items[0]["overlaps"]
        assert both["side"] == "both"
        assert both["source_note"] == compare_mod.NOTE_SOURCE_BOTH_PARTS
        assert unknown["component_label"] == ""
        assert "side" not in unknown

    def test_thesis_quote_is_rejected_without_analysis(self):
        """未取り込み候補は従来どおり要旨の逐語だけ（中心命題の引用は drop）。"""
        items, notes = compare_mod.validate_items(
            _parsed(
                [
                    {
                        "arxiv_id": "2608.00002",
                        "overlaps": [
                            {"component_label": "", "statement": "x",
                             "evidence_quote": "models the small-scale Fingers-of-God effect"}
                        ],
                    }
                ]
            ),
            [_candidate_entry()],
            [],
        )
        assert items[0]["overlaps"] == []
        assert "candidate_analysis_used" not in items[0]
        assert compare_mod.NOTE_QUOTE_NOT_VERBATIM in notes

    def test_load_is_fail_closed_without_visibility(self):
        session = _seed_session()
        assert compare_mod.load_candidate_analyses(
            session, [_candidate_entry()], visible_document_ids=None
        ) == {}
        assert compare_mod.load_candidate_analyses(
            session, [_candidate_entry()], visible_document_ids=set()
        ) == {}

    def test_load_reads_visible_ingested_candidates_only(self, monkeypatch):
        from core.paper_discovery import search as search_mod

        seen: dict = {}

        def _ingested(session, *, visible_document_ids):
            seen["visible"] = set(visible_document_ids)
            return {
                "2608.00002": {"document_id": "doc-cand"},
                "2608.00003": {"document_id": "doc-1"},  # 起点と同じ document は対象外
            }

        monkeypatch.setattr(search_mod, "visible_ingested_documents", _ingested)
        monkeypatch.setattr(
            "core.deliberation.refs.document_run_artifacts",
            lambda document_id: {
                "thesis_reconstruction": {"central_thesis": {"text": self.THESIS}}
            },
        )

        class _Rows:
            def __init__(self, rows):
                self._rows = rows

            def fetchall(self):
                return self._rows

        class _Session:
            def __init__(self):
                self.params = []

            def execute(self, stmt, params=None):
                self.params.append(params)
                return _Rows([("doc-cand", "VDG damping", "teacher_approved")])

        session = _Session()
        out = compare_mod.load_candidate_analyses(
            session,
            [_candidate_entry(), _candidate_entry("2608.00003"), _candidate_entry("2608.00004")],
            visible_document_ids={"doc-cand", "doc-1"},
            seed_document_id="doc-1",
        )
        assert list(out) == ["2608.00002"]
        assert out["2608.00002"]["labels"] == ["VDG damping"]
        assert out["2608.00002"]["central_thesis"] == self.THESIS
        assert session.params == [{"refs": ["doc-cand"]}]

    def test_run_compare_uses_the_analysis_with_one_llm_call(self, monkeypatch):
        calls: list[str] = []
        monkeypatch.setattr(
            compare_mod.arxiv_client,
            "fetch_by_ids",
            lambda ids, **kw: [
                schema.ArxivEntry(arxiv_id="2608.20293", summary="seed abstract"),
                _candidate_entry(),
            ],
        )
        monkeypatch.setattr(
            "core.deliberation.refs.document_run_artifacts", lambda document_id: {}
        )
        captured: dict = {}

        def _load(session, candidates, *, visible_document_ids, seed_document_id=""):
            captured["visible"] = visible_document_ids
            captured["seed"] = seed_document_id
            return {"2608.00002": self._analysis(approved=True)}

        monkeypatch.setattr(compare_mod, "load_candidate_analyses", _load)

        def _call(content, model):
            calls.append(content)
            return _parsed(
                [
                    {
                        "arxiv_id": "2608.00002",
                        "overlaps": [
                            {"component_label": "VDG damping", "statement": "重なりそうです。",
                             "evidence_quote": "small-scale Fingers-of-God effect"}
                        ],
                    }
                ]
            )

        monkeypatch.setattr(compare_mod, "_call_llm", _call)
        result = compare_mod.run_compare(
            _seed_session(), "doc-1", ["2608.00002"], visible_document_ids={"doc-cand"}
        )
        assert len(calls) == 1, "LLM は 1 回のまま"
        assert "中心命題: " + self.THESIS in calls[0]
        assert captured == {"visible": {"doc-cand"}, "seed": "doc-1"}
        overlap = result["items"][0]["overlaps"][0]
        assert overlap["side"] == "candidate"
        assert overlap["evidence_source"] == "thesis"


# ---------------------------------------------------------------------------
# 8. ブロックの目印（2026-09-15 / 設計書 §14.7）
# ---------------------------------------------------------------------------


def _stub_cooldown(monkeypatch, active: bool) -> None:
    """``arxiv_client.cooldown_active`` だけを差し替える（窓の実装には触らない）。"""
    monkeypatch.setattr(arxiv_client, "cooldown_active", lambda: active)


class TestArxivBlockedMarker:
    """429 の事実を文章にだけ残さず、機械可読の目印にする（オーナー指示 2026-09-15）。

    「制限されている」は seed DTO の ``arxiv_blocked`` / ``metadata_failure`` で読める。
    フロントが事実文の部分一致で理由を当てにいかなくて済むようにするのが主眼。
    """

    def test_constants_carry_no_numbers(self):
        """PR2: 残り時間・回数を文面に埋め込まない（数値を人に見せない）。"""
        for text in (
            radar.NOTE_ARXIV_BLOCKED,
            radar.NOTE_ARXIV_RATE_LIMITED,
            radar.NOTE_ARXIV_METADATA_UNAVAILABLE,
            radar.NOTE_ARXIV_METADATA_NOT_FOUND,
        ):
            assert not re.search(r"[0-9０-９]", text), f"数値を含む事実文: {text}"

    def test_blocked_wording_states_the_restriction(self):
        """「混雑」ではなく「制限されている」と、観測できた事実で書く（§14.7）。"""
        assert "arXiv からアクセスを制限されています" in radar.NOTE_ARXIV_BLOCKED
        assert "条件を変えても検索・比較はできません" in radar.NOTE_ARXIV_BLOCKED
        assert "混雑" not in radar.NOTE_ARXIV_BLOCKED
        assert "混雑" not in radar.NOTE_ARXIV_RATE_LIMITED

    def test_rate_limited_fetch_marks_the_seed_blocked(self, monkeypatch):
        """この操作で 429 を受けたら、その応答から目印が立つ（窓の有無より先に）。"""
        _stub_cooldown(monkeypatch, False)
        _stub_fetch_by_ids(
            monkeypatch, arxiv_client.ArxivRateLimitedError("refused")
        )
        monkeypatch.setattr(radar.store, "get_subscription", lambda session, key: None)
        seed = radar.resolve_seed(_seed_session(), "doc-1")
        assert seed["arxiv_blocked"] is True
        assert seed["arxiv_blocked_note"] == radar.NOTE_ARXIV_BLOCKED
        assert seed["metadata_failure"] == radar.METADATA_FAILURE_RATE_LIMITED
        assert seed["note"] == radar.NOTE_ARXIV_RATE_LIMITED

    def test_cooldown_marks_the_seed_blocked_even_on_a_cache_hit(
        self, no_arxiv, monkeypatch
    ):
        """写しから読めた回でも「いまは問い合わせを止めている」は立つ。"""
        _stub_cooldown(monkeypatch, True)
        calls: list[list[str]] = []
        _stub_fetch_by_ids(monkeypatch, [], recorder=calls)
        session = _seed_session(
            arxiv_metadata=[
                _entry("2608.20293", summary="A cached abstract.", categories=["astro-ph.CO"])
            ]
        )
        seed = radar.resolve_seed(session, "doc-1")
        assert calls == [], "写しがあるなら制限の有無に関わらず arXiv を呼ばない"
        assert seed["metadata_source"] == radar.METADATA_SOURCE_CACHE
        assert seed["arxiv_blocked"] is True
        assert seed["arxiv_blocked_note"] == radar.NOTE_ARXIV_BLOCKED
        assert "metadata_failure" not in seed, "読めた回を失敗として記録しない"

    def test_normal_fetch_is_not_marked_blocked(self, monkeypatch):
        _stub_cooldown(monkeypatch, False)
        _stub_fetch_by_ids(
            monkeypatch, [_entry("2608.20293", summary="x", categories=["astro-ph.CO"])]
        )
        seed = radar.resolve_seed(_seed_session(), "doc-1")
        assert seed["arxiv_blocked"] is False
        assert "arxiv_blocked_note" not in seed, "止めていないのに事実文を出さない"
        assert "metadata_failure" not in seed

    @pytest.mark.parametrize(
        "outcome,expected",
        [
            (
                arxiv_client.ArxivRateLimitedError("refused"),
                radar.METADATA_FAILURE_RATE_LIMITED,
            ),
            (arxiv_client.ArxivApiError("接続に失敗しました"), radar.METADATA_FAILURE_UNAVAILABLE),
            ([], radar.METADATA_FAILURE_NOT_FOUND),
        ],
    )
    def test_metadata_failure_names_the_reason(self, monkeypatch, outcome, expected):
        """3つの理由が ``note`` と1対1で機械可読になる（文面を当てにいかせない）。"""
        _stub_cooldown(monkeypatch, False)
        _stub_fetch_by_ids(monkeypatch, outcome)
        monkeypatch.setattr(radar.store, "get_subscription", lambda session, key: None)
        seed = radar.resolve_seed(_seed_session(), "doc-1")
        assert seed["metadata_failure"] == expected
        assert seed["note"], "機械可読の目印は事実文の置き換えではない（両方出す）"

    def test_run_radar_search_states_that_it_is_not_blocked(self, monkeypatch):
        _stub_cooldown(monkeypatch, False)
        _stub_fetch_by_ids(monkeypatch, [_entry("2608.20293", categories=["astro-ph.CO"])])
        _stub_search(monkeypatch, [_entry("2608.00002", title="A", summary="x")])
        _stub_band(monkeypatch)
        result = radar.run_radar_search(_seed_session(), "doc-1", distance="near")
        assert result["arxiv_blocked"] is False


class TestBlockedRadarResult:
    """制限中の「探していない」結果が、通常経路と**同じ形**で返ること（§14.7）。"""

    def test_mirrors_the_normal_shape_without_calling_arxiv(self, no_arxiv, monkeypatch):
        _stub_cooldown(monkeypatch, True)

        def _boom(*args, **kwargs):  # pragma: no cover — 呼ばれたら失敗させる
            raise AssertionError("arXiv must not be called while blocked")

        monkeypatch.setattr(radar.arxiv_client, "search", _boom)
        monkeypatch.setattr(radar.arxiv_client, "fetch_by_ids", _boom)

        result = radar.blocked_radar_result(
            _seed_session(), "doc-1", distance="near", categories=["astro-ph.CO"]
        )
        assert result["arxiv_blocked"] is True
        assert result["note"] == radar.NOTE_ARXIV_BLOCKED
        assert result["candidates"] == []
        assert result["total"] == 0
        assert result["banding"] == {"available": False, "note": radar.NOTE_ARXIV_BLOCKED}
        assert result["relation_context"] == {"available": False}
        assert result["closed_world_note"] == radar.search.CLOSED_WORLD_NOTE
        assert result["distance"] == "near"
        assert result["seed"]["arxiv_blocked"] is True
        # 何を探そうとしていたかは、探せなかったときこそ画面に要る（PR7）。
        assert "cat:astro-ph.CO" in result["query"]

    def test_rejects_an_unknown_distance(self, no_arxiv):
        with pytest.raises(ValueError):
            radar.blocked_radar_result(_seed_session(), "doc-1", distance="everywhere")

    def test_missing_document_raises_lookup_error(self, no_arxiv):
        with pytest.raises(LookupError):
            radar.blocked_radar_result(FakeSession(), "doc-missing")


class TestCompareRequiresArxiv:
    """写しだけで比較できるなら、制限中でも比較は成立させる（§14.3 の予算 0 コール）。"""

    def test_true_when_an_abstract_is_missing(self, no_arxiv):
        session = _seed_session()
        assert radar.compare_requires_arxiv(session, "doc-1", ["2608.00002"]) is True

    def test_false_when_every_abstract_is_cached(self, no_arxiv):
        session = _seed_session(
            arxiv_metadata=[
                _entry("2608.20293", summary="seed"),
                _entry("2608.00002", summary="candidate"),
            ]
        )
        assert radar.compare_requires_arxiv(session, "doc-1", ["2608.00002"]) is False

    def test_unparsable_ids_do_not_require_a_call(self, no_arxiv):
        session = _seed_session(arxiv_metadata=[_entry("2608.20293", summary="seed")])
        assert radar.compare_requires_arxiv(session, "doc-1", ["  ", "not-an-id"]) is False


class TestVisibleIngested:
    """「取り込み済み」は閲覧者に見える教材だけで判定する（§19 C・IK-0701）。"""

    def _entries(self):
        return [
            _entry("2608.20293", title="Seed", summary="s"),
            _entry("2608.00002", title="A", summary="dark energy study"),
            _entry("2608.00003", title="B", summary="other"),
        ]

    def _session(self):
        return _seed_session(
            documents=[
                {"id": "doc-1", "source_path": "mat-1", "title": "起点論文",
                 "source_url": "https://arxiv.org/pdf/2608.20293v2"},
                {"id": "doc-2", "source_path": "mat-2", "title": "A",
                 "source_url": "https://arxiv.org/abs/2608.00002", "status": "completed"},
                # 他の教員が非公開で取り込んだ（閲覧者には見えない）
                {"id": "doc-3", "source_path": "mat-3", "title": "B",
                 "source_url": "https://arxiv.org/abs/2608.00003v1", "status": "processing"},
            ]
        )

    def _run(self, monkeypatch, visible):
        _stub_fetch_by_ids(monkeypatch, [_entry("2608.20293", categories=["astro-ph.CO"])])
        _stub_search(monkeypatch, self._entries())
        _stub_band(monkeypatch)
        session = self._session()
        result = radar.run_radar_search(
            session, "doc-1", distance="near", visible_document_ids=visible
        )
        return session, {c["arxiv_id"]: c for c in result["candidates"]}

    def test_visible_ingested_carries_the_material_handles(self, monkeypatch):
        _, by_id = self._run(monkeypatch, {"doc-1", "doc-2"})
        a = by_id["2608.00002"]
        assert a["status"] == "ingested"
        assert a["document_id"] == "doc-2"
        assert a["material_id"] == "mat-2"
        assert a["analysis_status"] == "completed"

    def test_invisible_ingested_stays_new_and_leaks_nothing(self, monkeypatch):
        _, by_id = self._run(monkeypatch, {"doc-1", "doc-2"})
        b = by_id["2608.00003"]
        assert b["status"] == "new", "見えない教材を理由に取り込みを塞がない"
        for key in ("document_id", "material_id", "analysis_status"):
            assert key not in b

    def test_missing_visibility_is_fail_closed_without_sql(self, monkeypatch):
        session, by_id = self._run(monkeypatch, None)
        assert {c["status"] for c in by_id.values()} == {"new"}
        assert "ANY(:visible_ids)" not in session.sql_log


class TestCategoryDefaults:
    """主カテゴリだけ既定で有効・副カテゴリは enabled=False（§19 B）。"""

    def _stub(self, monkeypatch):
        _stub_fetch_by_ids(
            monkeypatch,
            [
                _entry(
                    "2608.20293",
                    categories=["hep-ph", "astro-ph.CO", "gr-qc"],
                    primary_category="astro-ph.CO",
                )
            ],
        )

    def test_seed_enables_only_the_primary_category(self, monkeypatch):
        self._stub(monkeypatch)
        seed = radar.resolve_seed(_seed_session(), "doc-1")
        assert seed["categories"] == ["astro-ph.CO"]
        assert seed["primary_category"] == "astro-ph.CO"
        chips = seed["category_candidates"]
        assert [c["text"] for c in chips] == ["astro-ph.CO", "hep-ph", "gr-qc"]
        assert [c["enabled"] for c in chips] == [True, False, False]

    def test_default_query_uses_the_primary_category_only(self, monkeypatch):
        self._stub(monkeypatch)
        calls: list = []
        _stub_search(monkeypatch, [], recorder=calls)
        _stub_band(monkeypatch)
        result = radar.run_radar_search(_seed_session(), "doc-1", distance="mid")
        assert result["query"] == "(cat:astro-ph.CO)"

    def test_without_primary_the_first_category_is_the_default(self):
        enabled, chips = radar.category_candidates(["hep-th", "gr-qc"], None)
        assert enabled == ["hep-th"]
        assert [c["enabled"] for c in chips] == [True, False]

    def test_subscription_categories_stay_all_enabled(self, monkeypatch):
        monkeypatch.setattr(
            radar.store, "get_subscription",
            lambda session, key: {"arxiv_categories": ["astro-ph.CO", "gr-qc"]},
        )
        session = _seed_session(
            documents=[{"id": "doc-1", "source_path": "mat-1", "title": "講義ノート",
                        "source_url": "", "filename": "notes.pdf"}]
        )
        seed = radar.resolve_seed(session, "doc-1")
        assert seed["categories_source"] == radar.CATEGORIES_SOURCE_SUBSCRIPTION
        assert seed["categories"] == ["astro-ph.CO", "gr-qc"]
        assert all(c["enabled"] for c in seed["category_candidates"])


class TestRelationFacts:
    """近さの根拠が出ない理由を事実文で返す（§19 A・PR7）。"""

    def _run(self, monkeypatch, *, session=None, resolver=None):
        _stub_fetch_by_ids(monkeypatch, [_entry("2608.20293", categories=["astro-ph.CO"])])
        _stub_search(monkeypatch, [_entry("2608.00002", title="A", summary="x")])
        _stub_band(monkeypatch)
        return radar.run_radar_search(
            session or _seed_session(), "doc-1", distance="mid",
            anchor_context_resolver=resolver,
        )

    def test_no_domain_says_so(self, monkeypatch):
        result = self._run(
            monkeypatch, session=_seed_session(courses=[]), resolver=lambda key: None
        )
        assert radar.FACT_RELATION_NO_DOMAIN in result["facts"]

    def test_domain_without_anchors_says_so(self, monkeypatch):
        result = self._run(monkeypatch, resolver=lambda key: None)
        assert radar.FACT_RELATION_NO_ANCHORS in result["facts"]
        assert radar.FACT_RELATION_NO_DOMAIN not in result["facts"]

    def test_no_approved_components_says_so(self, monkeypatch):
        context = {"anchors": [], "skeleton_version": "v1"}
        result = self._run(
            monkeypatch, session=_seed_session(components=[]), resolver=lambda key: context
        )
        assert result["facts"] == [radar.FACT_NO_APPROVED_COMPONENTS]

    def test_nothing_missing_adds_no_key(self, monkeypatch):
        context = {"anchors": [], "skeleton_version": "v1"}
        result = self._run(monkeypatch, resolver=lambda key: context)
        assert "facts" not in result

    def test_facts_carry_no_numbers(self):
        import re as _re
        for text in (
            radar.FACT_RELATION_NO_DOMAIN,
            radar.FACT_RELATION_NO_ANCHORS,
            radar.FACT_NO_APPROVED_COMPONENTS,
            radar.NOTE_SEED_TITLE_IS_FILENAME,
        ):
            assert not _re.search(r"[0-9０-９]", text)


class TestSeedTitleIsFilename:
    """起点の題名がファイル名のとき（§19 E・same-name-different-referents）。"""

    _ARXIV_TITLE = "A b to c semileptonic sum rule beyond the heavy quark limit"

    def _stub(self, monkeypatch, paper_title):
        _stub_fetch_by_ids(
            monkeypatch,
            [_entry("2508.06322", title=self._ARXIV_TITLE, categories=["hep-ph"])],
        )
        monkeypatch.setattr(
            radar, "_seed_artifacts",
            lambda session, document_id: {
                "document_structure": {"metadata": {"title": paper_title}}
            },
        )
        return _manual_session(
            title="arXiv-2508.06322v2", filename="arXiv-2508.06322v2.tar.gz"
        )

    def test_paper_title_from_the_structure_matches(self, monkeypatch):
        session = self._stub(monkeypatch, self._ARXIV_TITLE)
        seed = radar.resolve_seed(session, "doc-1")
        provenance = seed["provenance"]
        assert provenance["title_match"] is True
        assert provenance["title_match_via"] == "paper_title"
        assert provenance["paper_title"] == self._ARXIV_TITLE

    def test_unmatched_paper_title_stays_unmatched(self, monkeypatch):
        session = self._stub(monkeypatch, "Something else entirely different")
        seed = radar.resolve_seed(session, "doc-1")
        assert seed["provenance"]["title_match"] is False
        assert "title_match_via" not in seed["provenance"]

    def test_filename_title_uses_the_arxiv_title_as_heading(self, monkeypatch):
        session = self._stub(monkeypatch, "")
        seed = radar.resolve_seed(session, "doc-1")
        assert seed["title"] == "arXiv-2508.06322v2", "教材名は書き換えない（PR1）"
        assert seed["display_title"] == self._ARXIV_TITLE
        assert seed["title_note"] == radar.NOTE_SEED_TITLE_IS_FILENAME

    @pytest.mark.parametrize(
        "title, expected",
        [
            ("arXiv-2508.06322v2", True),
            ("arXiv-2508.06322v2.tar.gz", True),
            ("2407.01221.pdf", True),
            ("Kurtosis consistency relation", False),
            ("", False),
        ],
    )
    def test_filename_like_titles(self, title, expected):
        assert radar.title_looks_like_filename(title) is expected


# ---------------------------------------------------------------------------
# 第 3 波（設計書 §20）: 当たり外れの事実文・要旨の併合・固有名・帯の読み方
# ---------------------------------------------------------------------------


_SCAN05_SUMMARY = (
    "Various gravity theories beyond general relativity have been rigorously investigated in "
    "the literature such as Horndeski and degenerate higher-order scalar-tensor (DHOST) "
    "theories. In general, numerous model parameters are involved in such theories, which "
    "should be constrained to test the theories with experiments and observations. We "
    "construct the kurtosis consistency relations, calculated based on matter density "
    "fluctuations, in which the information of gravity theories is encoded. We derive two "
    "independent consistency relations that should hold in the framework of the DHOST "
    "theories and argue that such consistency relations would be useful for testing gravity "
    "theories."
)


class TestProperNounKeyphrases:
    def test_acronym_and_mid_sentence_capital_are_supplied(self):
        out = radar.proper_noun_keyphrases([_SCAN05_SUMMARY])
        assert out == ["DHOST", "Horndeski"]

    def test_sentence_initial_capital_is_not_a_proper_noun(self):
        assert radar.proper_noun_keyphrases(["Various theories. Gravity tests."]) == []

    def test_word_that_also_appears_lowercase_is_not_a_proper_noun(self):
        assert radar.proper_noun_keyphrases(["We test Gravity and gravity."]) == []

    def test_short_acronyms_digits_and_hyphens_are_skipped(self):
        assert radar.proper_noun_keyphrases(["in GR and LCDM2 and Einstein-Hilbert terms"]) == []

    def test_cap_and_exclusion(self):
        text = "in Alpha and Beta and Gamma and Delta"
        assert radar.proper_noun_keyphrases([text]) == ["Alpha", "Beta"]
        assert radar.proper_noun_keyphrases([text], exclude_norms=["alpha model"]) == [
            "Beta", "Gamma",
        ]

    def test_japanese_text_gives_nothing(self):
        assert radar.proper_noun_keyphrases(["自由スカラー場を介した有効作用"]) == []


class TestSummaryMergeOrder:
    def test_summary_promotes_phrases_it_shares_with_title_or_thesis(self):
        thesis = radar._thesis_text_from_artifacts(_REAL_THESIS)
        without = radar.title_thesis_keyphrases(_KURTOSIS_TITLE, thesis)
        withsum = radar.title_thesis_keyphrases(_KURTOSIS_TITLE, thesis, summary=_SCAN05_SUMMARY)
        texts = [t for t, _s in withsum]
        # 題名と中心命題の両方に出る句が先頭のまま。
        assert withsum[0] == without[0]
        # 要旨にも出る題名の句（gravity theories）は、要旨に出ない中心命題の反復句より前。
        assert texts.index("gravity theories") < texts.index("modified gravity")
        # 固有名の 1 語（要旨にしか無い Horndeski）が別枠で通る（出所は thesis）。
        assert ("Horndeski", "thesis") in withsum
        assert len(withsum) <= radar.MAX_TITLE_THESIS_KEYPHRASES + radar.MAX_PROPER_NOUN_KEYPHRASES

    def test_summary_only_phrases_are_not_supplied(self):
        """要旨にしか出ない句（固有名と略語のフル表記以外 — §23）は足さない（新しい主題を足す材料ではない）。"""
        out = radar.title_thesis_keyphrases(_KURTOSIS_TITLE, "", summary=_SCAN05_SUMMARY)
        lowered = {t.casefold() for t, _s in out}
        assert "model parameters" not in lowered
        assert "general relativity" not in lowered
        assert {"dhost", "horndeski"} <= lowered

    def test_no_summary_keeps_previous_behavior(self):
        assert radar.title_thesis_keyphrases(_KURTOSIS_TITLE, "", summary="") == (
            radar.title_thesis_keyphrases(_KURTOSIS_TITLE, "")
        )

    def test_seed_reads_summary_from_the_cache_only(self, monkeypatch):
        """直接呼び出し（summary 未指定）は写しだけを読む。arXiv は呼ばない。"""
        def _no_fetch(*_a, **_k):
            raise AssertionError("arXiv must not be called")

        monkeypatch.setattr(radar.arxiv_client, "fetch_by_ids", _no_fetch)
        session = _unapproved_session(
            arxiv_metadata=[_entry("2407.01221", title=_KURTOSIS_TITLE, summary=_SCAN05_SUMMARY)]
        )
        row = {"document_id": "doc-1", "source_path": "mat-1", "title": _KURTOSIS_TITLE,
               "source_url": "https://arxiv.org/abs/2407.01221"}
        out = radar.seed_keyphrase_candidates(session, row)
        assert ("Horndeski", "thesis") in [(i["text"], i["source"]) for i in out]

    def test_no_cached_summary_means_nothing_extra(self, monkeypatch):
        """写しが無ければ何もしない（本文チャンクで代替しない）。"""
        session = _unapproved_session()
        row = {"document_id": "doc-1", "source_path": "mat-1", "title": _KURTOSIS_TITLE,
               "source_url": "https://arxiv.org/abs/2407.01221"}
        out = radar.seed_keyphrase_candidates(session, row)
        assert [i["text"] for i in out] == [
            "Kurtosis consistency relation", "large-scale structure", "gravity theories",
        ]
        assert not any("chunks" in sql for sql, _p in session.calls)

    def test_resolve_seed_uses_the_fetched_summary(self, monkeypatch):
        _stub_fetch_by_ids(
            monkeypatch,
            [_entry("2407.01221", title=_KURTOSIS_TITLE, summary=_SCAN05_SUMMARY,
                    categories=["astro-ph.CO"], primary_category="astro-ph.CO")],
        )
        seed = radar.resolve_seed(_unapproved_session(), "doc-1")
        texts = [item["text"] for item in seed["keyphrase_candidates"]]
        assert "Horndeski" in texts and "DHOST" in texts
        assert seed["keyphrase_supply"]["sources"] == ["title", "thesis"]


class TestKeyphraseHits:
    def _phrases(self, *texts, disabled=()):
        return [{"text": t, "source": "title", "enabled": t not in disabled} for t in texts]

    def test_hit_is_boolean_per_enabled_phrase(self):
        candidates = [
            {"title": "Large Scale Structure trispectrum", "summary": "galaxy bias"},
            {"title": "Inflation", "summary": "large-scale structure of the universe"},
        ]
        hits = radar.keyphrase_hits(
            candidates,
            self._phrases("large-scale structure", "kurtosis", "galaxy bias", "dropped",
                          disabled=("dropped",)),
        )
        assert hits == [
            {"text": "large-scale structure", "hit": True, "widens": False},
            {"text": "kurtosis", "hit": False, "widens": False},
            {"text": "galaxy bias", "hit": True, "widens": False},
        ]
        # 真偽だけ（件数を返さない — PD4）。
        assert all(set(h) == {"text", "hit", "widens"} for h in hits)
        assert all(isinstance(h["hit"], bool) for h in hits)

    def test_hyphen_and_space_match_the_same(self):
        """``large-scale structure`` は ``Large Scale Structure`` にも当たる（一致チップと同じ照合）。"""
        payload = {"title": "Large Scale Structure", "summary": ""}
        assert radar.search.matched_keyphrases(payload, [{"text": "large-scale structure"}]) == [
            "large-scale structure"
        ]

    def test_widens_only_with_enough_candidates_and_phrases(self):
        three = [{"title": f"large-scale structure {i}", "summary": ""} for i in range(3)]
        hits = radar.keyphrase_hits(three, self._phrases("large-scale structure", "kurtosis"))
        assert hits[0]["widens"] is True and hits[1]["widens"] is False
        # 候補 2 件では「すべてに当たった」を言わない。
        assert not radar.keyphrase_hits(
            three[:2], self._phrases("large-scale structure", "kurtosis")
        )[0]["widens"]
        # 有効な語が 1 語では自明なので言わない。
        assert not radar.keyphrase_hits(three, self._phrases("large-scale structure"))[0]["widens"]

    def test_no_candidates_gives_no_hits(self):
        assert radar.keyphrase_hits([], self._phrases("kurtosis")) == []

    def test_notes_list_words_without_numbers(self):
        notes = radar.keyphrase_hit_notes([
            {"text": "kurtosis", "hit": False, "widens": False},
            {"text": "gravity theories", "hit": False, "widens": False},
            {"text": "large-scale structure", "hit": True, "widens": True},
        ])
        assert notes["keyphrase_hits_note"] == (
            radar.NOTE_KEYPHRASES_NOT_HIT_HEAD + "kurtosis, gravity theories"
        )
        assert notes["keyphrase_widening_note"].endswith("large-scale structure")
        for head in (radar.NOTE_KEYPHRASES_NOT_HIT_HEAD, radar.NOTE_KEYPHRASES_WIDENING_HEAD):
            assert not re.search(r"\d", head)
        assert radar.keyphrase_hit_notes([{"text": "x", "hit": True, "widens": False}]) == {}

    def test_search_result_carries_hits_and_notes(self, monkeypatch):
        _stub_fetch_by_ids(monkeypatch, [_entry("2608.20293", categories=["astro-ph.CO"])])
        _stub_search(monkeypatch, [
            _entry("2608.00002", title="A", summary="dark energy survey"),
            _entry("2608.00003", title="B", summary="dark energy model"),
            _entry("2608.00004", title="C", summary="dark energy again"),
        ])
        _stub_band(monkeypatch)
        result = radar.run_radar_search(_seed_session(), "doc-1", distance="near")
        hits = {h["text"]: h for h in result["keyphrase_hits"]}
        assert hits["dark energy"] == {"text": "dark energy", "hit": True, "widens": True}
        assert hits["w0waCDM"]["hit"] is False
        assert result["keyphrase_hits_note"].endswith("w0waCDM")
        assert result["keyphrase_widening_note"].endswith("dark energy")
        # 網を広げている語を自動で外さない（PD3）— 条件はそのまま。
        assert "dark energy" in result["query"]

    def test_mid_has_no_hits(self, monkeypatch):
        _stub_fetch_by_ids(monkeypatch, [_entry("2608.20293", categories=["astro-ph.CO"])])
        _stub_search(monkeypatch, [_entry("2608.00002", title="A", summary="dark energy")])
        _stub_band(monkeypatch)
        result = radar.run_radar_search(
            _seed_session(), "doc-1", distance="mid", categories=["astro-ph.CO"]
        )
        assert "keyphrase_hits" not in result
        assert "keyphrase_hits_note" not in result


class TestReadingNote:
    def test_note_only_when_bands_are_present(self):
        assert radar.reading_note([{"arxiv_id": "a"}], "near") == ""
        near = radar.reading_note([{"distance_label": "近い"}], "near")
        assert near.startswith(radar.READING_NOTE_BANDS_AND_RELATIONS)
        assert near.endswith(radar.READING_NOTE_NEAR_ORDER)
        assert radar.reading_note([{"distance_label": "遠い"}], "far").endswith(
            radar.READING_NOTE_NEWEST_ORDER
        )
        for text in (radar.READING_NOTE_BANDS_AND_RELATIONS, radar.READING_NOTE_NEAR_ORDER,
                     radar.READING_NOTE_NEWEST_ORDER):
            assert not re.search(r"\d", text)

    def test_search_attaches_note_when_banded(self, monkeypatch):
        _stub_fetch_by_ids(monkeypatch, [_entry("2608.20293", categories=["astro-ph.CO"])])
        _stub_search(monkeypatch, [_entry("2608.00002", title="A", summary="dark energy")])
        _stub_band(monkeypatch)
        result = radar.run_radar_search(_seed_session(), "doc-1", distance="near")
        assert result["reading_note"].endswith(radar.READING_NOTE_NEAR_ORDER)

    def test_no_note_when_banding_unavailable(self, monkeypatch):
        _stub_fetch_by_ids(monkeypatch, [_entry("2608.20293", categories=["astro-ph.CO"])])
        _stub_search(monkeypatch, [_entry("2608.00002", title="A", summary="dark energy")])
        _stub_band(monkeypatch, available=False)
        result = radar.run_radar_search(_seed_session(), "doc-1", distance="near")
        assert "reading_note" not in result


# ---------------------------------------------------------------------------
# 第 4 波（設計書 §21）— 供給の品質・照合の語境界・配置の対比・構造照合の成立条件
# ---------------------------------------------------------------------------

from core.paper_discovery import search as search_mod  # noqa: E402


class TestWave4SupplyQuality:
    """実例 ``uxsim/runs/radar_loop/scan-06.json``（2610.08908 を起点にした連鎖）の供給語。"""

    def test_scan06_bad_words_are_rejected(self):
        ok = radar.is_quality_keyphrase
        for bad in ("P", "B", "Gpc", "averaging", "binning", "modeling", "3Gpc", "10 keV"):
            assert not ok(bad), bad
        for good in ("FoG", "AbacusSummit", "EFTofLSS", "Full-Shape", "Galaxy Trispectrum",
                     "kurtosis"):
            assert ok(good), good

    def test_thesis_single_words_need_an_uppercase_letter(self):
        assert radar.is_quality_keyphrase("FoG", require_uppercase_single=True)
        assert not radar.is_quality_keyphrase("kurtosis", require_uppercase_single=True)
        # 2 語以上の句には大文字を要求しない。
        assert radar.is_quality_keyphrase("galaxy bias", require_uppercase_single=True)

    def test_concept_names_are_capped_at_four_words_forty_chars(self):
        ok = radar.is_searchable_concept_name
        assert not ok("Fisher forecast of covariance-modeling impact")   # 5 語・45 字
        assert not ok("x" * 41)
        assert ok("redshift-space galaxy trispectrum")

    def test_thesis_does_not_supply_symbols_or_units(self):
        thesis = (
            "We fit the EFTofLSS model to P and B and T in a 8 Gpc volume. "
            "The FoG damping and the AbacusSummit mocks. P and B again, Gpc again."
        )
        out = [p for p, _src in radar.title_thesis_keyphrases(
            "Full-Shape Analysis of the Redshift-Space Galaxy Trispectrum in the EFTofLSS",
            thesis,
        )]
        for bad in ("P", "B", "T", "Gpc"):
            assert bad not in out
        assert "EFTofLSS" in out or any("EFTofLSS" in p for p in out)


class TestWave4MatchedKeyphrasesWordBoundary:
    """一致チップ・``keyphrase_hits`` の照合は語境界付きの正本（``alias_matching``）。"""

    def _payload(self):
        return {"title": "Power spectrum and Bispectrum of galaxies",
                "summary": "We measure the P_0 multipole and B in Mpc boxes. Large Scale Structure."}

    def test_one_letter_word_does_not_hit_inside_other_words(self):
        hits = search_mod.matched_keyphrases(
            {"title": "Power spectrum of biased tracers", "summary": "probes"},
            [{"text": "P"}, {"text": "B"}],
        )
        assert hits == []

    def test_short_word_is_case_sensitive_exact(self):
        hits = search_mod.matched_keyphrases(self._payload(), [{"text": "B"}, {"text": "b"}])
        assert hits == ["B"]

    def test_hyphen_and_space_are_the_same_separator(self):
        hits = search_mod.matched_keyphrases(self._payload(), [{"text": "large-scale structure"}])
        assert hits == ["large-scale structure"]


class TestWave4PlacementContrast:
    LABELS = {
        ("cosmo", "lss_formation"): "大規模構造形成",
        ("cosmo", "gravity"): "基礎物理・重力",
        ("cosmo", "inflation"): "初期宇宙・インフレーション",
        ("cosmo", "stats"): "統計推論",
    }

    @staticmethod
    def _row(node, status="inferred", weight=0.9):
        return {"domain_key": "cosmo", "node_id": node, "status": status, "weight": weight,
                "confidence": 0.8}

    def _walk(self, value):
        if isinstance(value, dict):
            for k, v in value.items():
                yield k, v
                yield from self._walk(v)
        elif isinstance(value, list):
            for v in value:
                yield from self._walk(v)

    def test_shape_and_split(self):
        seed = [self._row("lss_formation", "confirmed"), self._row("gravity")]
        cand = [self._row("lss_formation"), self._row("inflation"), self._row("stats", "confirmed")]
        out = radar.placement_contrast(seed, cand, labels=self.LABELS,
                                       skeleton_versions={"cosmo": "3"})
        assert set(out) == {"shared", "seed_only", "candidate_only", "skeleton_version", "perspective_facts"}
        assert [i["label"] for i in out["shared"]] == ["大規模構造形成"]
        # 重なりは両側とも確認済みのときだけ「教員確認済み」（ここは候補側が推定）。
        assert out["shared"][0]["provenance_label"] == "AIによる推定（未確認）"
        assert [i["label"] for i in out["seed_only"]] == ["基礎物理・重力"]
        assert {i["label"] for i in out["candidate_only"]} == {"初期宇宙・インフレーション", "統計推論"}
        stats = next(i for i in out["candidate_only"] if i["label"] == "統計推論")
        assert stats["provenance_label"] == "教員確認済み"
        assert out["skeleton_version"] == "3"

    def test_no_numbers_leak(self):
        out = radar.placement_contrast(
            [self._row("gravity")], [self._row("stats")],
            labels=self.LABELS, skeleton_versions={"cosmo": "3"},
        )
        for key, value in self._walk(out):
            assert key not in ("weight", "confidence", "score", "count")
            assert not isinstance(value, (int, float)) or isinstance(value, bool)

    def test_unplaced_side_returns_none(self):
        assert radar.placement_contrast([], [self._row("stats")], labels=self.LABELS,
                                        skeleton_versions={}) is None
        # 現行骨格に無いノードはラベルを捏造しない（対比に使わない）。
        assert radar.placement_contrast(
            [{"domain_key": "cosmo", "node_id": "gone", "status": "confirmed"}],
            [self._row("stats")], labels=self.LABELS, skeleton_versions={},
        ) is None

    def test_attach_writes_contrast_or_fact(self, monkeypatch):
        from core.landscape import store as landscape_store

        rows = [
            {"document_id": "seed", **self._row("gravity")},
            {"document_id": "c1", **self._row("stats")},
        ]
        monkeypatch.setattr(landscape_store, "list_for_documents",
                            lambda session, ids, statuses: rows)
        monkeypatch.setattr(radar, "_skeleton_labels",
                            lambda session, domains: (self.LABELS, {"cosmo": "3"}))
        cands = [{"document_id": "c1"}, {"document_id": "c2"}, {"arxiv_id": "new"}]
        radar.attach_placement_contrasts(object(), "seed", cands)
        assert "placement_contrast" in cands[0]
        assert cands[1]["placement_contrast_note"] == radar.FACT_CONTRAST_CANDIDATE_UNPLACED
        assert "placement_contrast" not in cands[2] and "placement_contrast_note" not in cands[2]

    def test_attach_names_the_unplaced_seed(self, monkeypatch):
        from core.landscape import store as landscape_store

        monkeypatch.setattr(landscape_store, "list_for_documents",
                            lambda session, ids, statuses: [
                                {"document_id": "c1", **self._row("stats")}])
        monkeypatch.setattr(radar, "_skeleton_labels",
                            lambda session, domains: (self.LABELS, {"cosmo": "3"}))
        cands = [{"document_id": "c1"}]
        radar.attach_placement_contrasts(object(), "seed", cands)
        assert cands[0]["placement_contrast_note"] == radar.FACT_CONTRAST_SEED_UNPLACED

    def test_attach_is_fail_soft(self, monkeypatch):
        from core.landscape import store as landscape_store

        def boom(*a, **k):
            raise RuntimeError("db down")

        monkeypatch.setattr(landscape_store, "list_for_documents", boom)
        cands = [{"document_id": "c1"}]
        radar.attach_placement_contrasts(object(), "seed", cands)
        assert cands == [{"document_id": "c1"}]


class TestWave4ModuleMatchNote:
    def test_names_the_side_that_could_not_be_matched(self):
        assert radar.module_match_note(False, True) == radar.FACT_MODULES_SEED_UNSAVED
        assert radar.module_match_note(False, False) == radar.FACT_MODULES_SEED_UNSAVED
        assert radar.module_match_note(True, False) == radar.FACT_MODULES_CANDIDATE_UNSAVED
        assert radar.module_match_note(True, True) is None
        assert "起点の論文" in radar.FACT_MODULES_SEED_UNSAVED

    def test_attach_uses_saved_set(self, monkeypatch):
        from core.library import structural_matches

        monkeypatch.setattr(structural_matches, "module_match_states",
                            lambda session, ids, rule_version: {"c1": {"stale": False, "fingerprints": set()}})
        cands = [{"document_id": "c1"}, {"arxiv_id": "x"}]
        radar.attach_module_match_notes(object(), "seed", cands)
        assert cands[0]["module_match_note"] == radar.FACT_MODULES_SEED_UNSAVED
        assert "module_match_note" not in cands[1]


class TestWave5ModuleMatchNote:
    """§22 A — 両側保存のとき「対象外」と「照合したが一致なし」を畳まない。"""

    def test_both_saved_distinguishes_ineligible_from_no_match(self):
        note = radar.module_match_note
        assert note(True, True, seed_fingerprints=set(), candidate_fingerprints=set()) \
            == radar.FACT_MODULES_NO_ELIGIBLE_BOTH
        assert note(True, True, seed_fingerprints=set(), candidate_fingerprints={"a"}) \
            == radar.FACT_MODULES_NO_ELIGIBLE_SEED
        assert note(True, True, seed_fingerprints={"a"}, candidate_fingerprints=set()) \
            == radar.FACT_MODULES_NO_ELIGIBLE_CANDIDATE
        assert note(True, True, seed_fingerprints={"a"}, candidate_fingerprints={"b"}) \
            == radar.FACT_MODULES_NO_MATCH
        assert note(True, True, seed_fingerprints={"a"}, candidate_fingerprints={"a", "b"}) \
            == radar.FACT_MODULES_MATCHED
        assert note(True, True, seed_fingerprints={"a"}, candidate_fingerprints={"a"}, stale=True) \
            == radar.FACT_MODULES_STALE

    def test_texts_carry_no_fingerprint_or_numbers(self):
        import re

        for text in (radar.FACT_MODULES_NO_ELIGIBLE_BOTH, radar.FACT_MODULES_NO_ELIGIBLE_SEED,
                     radar.FACT_MODULES_NO_ELIGIBLE_CANDIDATE, radar.FACT_MODULES_NO_MATCH,
                     radar.FACT_MODULES_MATCHED, radar.FACT_MODULES_STALE):
            assert "ops=" not in text and not re.search(r"[0-9０-９]", text)
        # 一致・不一致の文は照合の範囲（記号・概念は見ていない）を言う。
        assert radar.FACT_MODULES_SCOPE in radar.FACT_MODULES_NO_MATCH
        assert radar.FACT_MODULES_SCOPE in radar.FACT_MODULES_MATCHED

    def test_attach_reads_states_and_names_ineligible_side(self, monkeypatch):
        from core.library import structural_matches

        states = {
            "seed": {"stale": False, "fingerprints": {"fp"}},
            "c1": {"stale": False, "fingerprints": set()},
            "c2": {"stale": False, "fingerprints": {"other"}},
        }
        monkeypatch.setattr(structural_matches, "module_match_states",
                            lambda session, ids, rule_version: states)
        cands = [{"document_id": "c1"}, {"document_id": "c2"}]
        radar.attach_module_match_notes(object(), "seed", cands)
        assert cands[0]["module_match_note"] == radar.FACT_MODULES_NO_ELIGIBLE_CANDIDATE
        assert cands[1]["module_match_note"] == radar.FACT_MODULES_NO_MATCH
        assert "fingerprints" not in str(cands)


class TestWave5ContrastPerspective:
    """§22 C — 対比の各項目に観点（正本の日本語）と粒度（領域 / 概念）。"""

    LABELS = {("cosmo", "lss"): "宇宙論・大規模構造", ("cosmo", "stats"): "統計推論",
              ("cosmo", "sim"): "シミュレーション"}
    LAYERS = {("cosmo", "lss"): "region", ("cosmo", "stats"): "concept",
              ("cosmo", "sim"): "concept"}

    @staticmethod
    def _row(node, perspective, status="inferred"):
        return {"domain_key": "cosmo", "node_id": node, "perspective": perspective, "status": status}

    def test_items_carry_perspective_and_layer(self):
        from core.landscape.schema import perspective_label

        out = radar.placement_contrast(
            [self._row("lss", "question"), self._row("stats", "method")],
            [self._row("lss", "question"), self._row("lss", "theory"), self._row("sim", "method")],
            labels=self.LABELS, skeleton_versions={"cosmo": "3"}, layers=self.LAYERS,
        )
        shared = out["shared"][0]
        assert shared["layer"] == "region"
        # 両側に共通の観点を主に採る（候補側だけの「理論から」は一覧に残す）。
        assert shared["perspective_label"] == perspective_label("question")
        assert shared["perspective_labels"] == [perspective_label("question"), perspective_label("theory")]
        assert out["seed_only"][0]["perspective_label"] == perspective_label("method")
        assert out["seed_only"][0]["layer"] == "concept"
        assert out["candidate_only"][0]["perspective_label"] == perspective_label("method")

    def test_unknown_perspective_is_not_shown_raw(self):
        out = radar.placement_contrast(
            [self._row("stats", "nonsense")], [self._row("sim", "method")],
            labels=self.LABELS, skeleton_versions={}, layers=self.LAYERS,
        )
        assert out["seed_only"][0]["perspective_label"] == ""
        assert "nonsense" not in str(out)

    def test_perspective_label_comes_from_the_landscape_canon(self):
        import inspect

        src = inspect.getsource(radar.placement_contrast)
        assert "from core.landscape.schema import" in src and "perspective_label" in src
        assert "方法から" not in inspect.getsource(radar)

    def test_without_layers_no_layer_key(self):
        out = radar.placement_contrast(
            [self._row("stats", "method")], [self._row("sim", "method")],
            labels=self.LABELS, skeleton_versions={},
        )
        assert "layer" not in out["seed_only"][0]


class TestWave6PerspectiveAbsence:
    """§22.4 — 片側にだけ配置のある観点を「相手側には配置がない」と言う（違いに見せない）。"""

    LABELS = {("cosmo", "lss"): "宇宙論・大規模構造", ("cosmo", "stats"): "統計推論",
              ("cosmo", "halo"): "銀河・銀河団"}

    @staticmethod
    def _row(node, perspective, status="inferred"):
        return {"domain_key": "cosmo", "node_id": node, "perspective": perspective, "status": status}

    def test_candidate_without_method_is_stated_as_absence(self):
        out = radar.placement_contrast(
            [self._row("lss", "question"), self._row("stats", "method")],
            [self._row("lss", "question"), self._row("halo", "subject")],
            labels=self.LABELS, skeleton_versions={"cosmo": "3"},
        )
        facts = out["perspective_facts"]
        assert radar.FACT_CONTRAST_PERSPECTIVE_ABSENT_CANDIDATE.format(perspective="方法") in facts
        # 逆向き（起点に対象の観点の配置が無い）も言い分ける。
        assert radar.FACT_CONTRAST_PERSPECTIVE_ABSENT_SEED.format(perspective="対象") in facts
        # 両側にある観点（問い）は言わない。
        assert not any("問いの観点" in f for f in facts)
        assert all("違う" not in f.split("（")[0] for f in facts)

    def test_no_asymmetry_means_no_facts_and_no_numbers(self):
        out = radar.placement_contrast(
            [self._row("lss", "question")], [self._row("halo", "question")],
            labels=self.LABELS, skeleton_versions={"cosmo": "3"},
        )
        assert out["perspective_facts"] == []
        for fact in radar.FACT_CONTRAST_PERSPECTIVE_ABSENT_CANDIDATE, radar.FACT_CONTRAST_PERSPECTIVE_ABSENT_SEED:
            assert not any(ch.isdigit() for ch in fact)


# ---------------------------------------------------------------------------
# §23: 供給は名詞句単位（複合語を割らない・略語はフル表記を先に）
#       実データ uxsim/runs/radar_loop/scan-08.json（2609.35757「Can Fingers of God be Resummed?」）
# ---------------------------------------------------------------------------


_SCAN08_TITLE = "Can Fingers of God be Resummed?"
_SCAN08_SUMMARY = (
    "Fingers of God (FoGs), Doppler shifts due to the small-scale virial motions of galaxies, "
    "are one of the most significant challenges for analytic models of galaxy clustering in "
    "redshift surveys. In the effective field theory approach, the characteristic scale of "
    "these shifts introduces a new, large length scale, limiting the reach of the theory. "
    "We show that common damping models can be understood as the mean-field limit of the "
    "single-particle characteristic function (SPCF) of FoG velocities, but that the "
    "environmental dependence of the SPCF generates equally large corrections that break the "
    "simple damping form."
)
#: 実データの形: 生成言語が日本語で、英語は用語の定義（全角括弧）にだけ残る。
_SCAN08_ARTIFACTS = {
    "thesis_reconstruction": {
        "central_question": "FoG の効果を、速度の特性関数（単一粒子特性関数）を通じて再和できるか。",
        "central_thesis": {"text": (
            "FoG の影響は、速度の単一粒子特性関数（SPCF）$W(\\lambda|\\textbf{x})$ を通じて"
            "再和できうる。"
        )},
    },
    "paper_skeleton": {"paper_goal": {"text": (
        "銀河の小スケールのビリアル運動による Fingers of God（FoG）が有効場理論（EFT）の"
        "予言の適用範囲を狭めている問題に対し、FoG の効果を再和できるかを調べる。"
    )}},
}


class TestPhraseUnitSupply:
    def _out(self, summary=_SCAN08_SUMMARY):
        thesis = radar._thesis_text_from_artifacts(_SCAN08_ARTIFACTS)
        return radar.title_thesis_keyphrases(_SCAN08_TITLE, thesis, summary=summary)

    def test_scan08_compound_is_one_phrase_and_fragments_are_not_supplied(self):
        texts = [t for t, _s in self._out()]
        assert texts[0] == "Fingers of God"
        for bad in ("Fingers", "God", "Resummed", "Doppler"):
            assert bad not in texts

    def test_scan08_abbreviation_full_form_precedes_the_abbreviation(self):
        out = self._out()
        texts = [t for t, _s in out]
        full = texts.index("single-particle characteristic function")
        assert texts.index("SPCF") > full
        assert "FoG" in texts and texts.index("FoG") > texts.index("Fingers of God")
        # 複数形（FoGs）を別の語として出さない。
        assert "FoGs" not in texts
        # 出所は既存の語彙の中（題名にある句は title・要旨の定義は thesis）。
        assert dict(out)["Fingers of God"] == "title"
        assert dict(out)["single-particle characteristic function"] == "thesis"
        assert {s for _t, s in out} <= set(schema.KEYPHRASE_SOURCES)

    def test_paper_goal_is_read_with_the_thesis(self):
        text = radar._thesis_text_from_artifacts(_SCAN08_ARTIFACTS)
        assert "Fingers of God（FoG）" in text
        # thesis_reconstruction が無くても目的の文は読む。
        only_goal = {"paper_skeleton": _SCAN08_ARTIFACTS["paper_skeleton"]}
        assert "Fingers of God" in radar._thesis_text_from_artifacts(only_goal)

    def test_full_width_parentheses_define_an_abbreviation(self):
        out = radar.title_thesis_keyphrases(
            "Something else", "銀河の Fingers of God（FoG）が問題になる。",
        )
        texts = [t for t, _s in out]
        assert "Fingers of God" in texts and "FoG" in texts
        assert texts.index("Fingers of God") < texts.index("FoG")

    def test_of_compound_is_kept_only_when_fragments_or_attested(self):
        # 両側 1 語 → 1 句。
        assert radar._of_compounds("Can Fingers of God be Resummed?") == [
            ("Fingers of God", True),
        ]
        # 冠詞を挟む of the はつながない。
        assert radar._of_compounds("Kinetic Theory of the EFTofLSS") == []
        # 片側が長い複合語は、他の材料に同じ表記が無ければ題名の句に足さない。
        texts = radar.title_keyphrases("Analytic models of galaxy clustering statistics")
        assert not any(" of " in t for t in texts)

    def test_title_case_full_form_words_are_not_proper_nouns(self):
        summary = (
            "The Effective Field Theory of Large Scale Structure (EFTofLSS) provides a "
            "coarse-grained description of clustering."
        )
        out = radar.title_thesis_keyphrases(
            "Kinetic Theory of the EFTofLSS: A Halo-based Approach", "", summary=summary,
        )
        texts = [t for t, _s in out]
        assert "Effective Field Theory of Large Scale Structure" in texts
        for bad in ("Field", "Large", "Theory", "Structure"):
            assert bad not in texts

    def test_participle_and_modifier_proper_nouns_are_dropped(self):
        assert radar._looks_like_participle("Resummed")
        assert not radar._looks_like_participle("Halo-based")
        assert not radar._looks_like_participle("speed")
        # 小文字の名詞を修飾する固有名（Doppler shifts）は 1 語では採らない。
        assert radar.proper_noun_keyphrases(["due to Doppler shifts of galaxies"]) == []
        # 並列の固有名（Horndeski and …）は従来どおり採る。
        assert radar.proper_noun_keyphrases(["such as Horndeski and other theories"]) == [
            "Horndeski",
        ]

    def test_seed_supply_for_scan08_shape(self, monkeypatch):
        monkeypatch.setattr(
            radar, "_seed_artifacts", lambda session, document_id: _SCAN08_ARTIFACTS,
        )
        row = {"document_id": "doc-1", "source_path": "mat-1", "title": _SCAN08_TITLE}
        out = radar.seed_keyphrase_candidates(
            _unapproved_session(), row, summary=_SCAN08_SUMMARY,
        )
        texts = [item["text"] for item in out]
        assert texts[:2] == ["Fingers of God", "single-particle characteristic function"]
        assert "Doppler" not in texts and "God" not in texts and "Resummed" not in texts


# ---------------------------------------------------------------------------
# §23.5 短い略語はフル表記の句を先に・この語だけに一致した候補が多い語（judge-scan-09）
# ---------------------------------------------------------------------------

SCAN09_TITLE = "Can Fingers of God be Resummed?"
SCAN09_THESIS = (
    "FoG の効果を、速度の特性関数（単一粒子特性関数）を通じて再和できるか。 . "
    "FoG の応答は速度の単一粒子特性関数（SPCF）を通じて再和できうる。 . "
    "銀河の小スケールのビリアル運動による Fingers of God（FoG）が有効場理論（EFT）の予言の"
    "適用範囲を狭めている問題に対し、EFT の到達範囲を広げる道を探る。"
)
SCAN09_SUMMARY = (
    "Fingers of God (FoGs), Doppler shifts due to the small-scale virial motions of galaxies, "
    "are one of the most significant challenges. In the effective field theory approach, the "
    "characteristic scale of these shifts introduces a new length scale. We show that common "
    "damping models can be understood as the mean-field limit of the single-particle "
    "characteristic function (SPCF) of FoG velocities."
)
SCAN09_BODY = (
    "is one of the main obstacles to extending the perturbative reach of effective field "
    "theory (EFT) predictions of galaxy clustering in spectroscopic surveys."
)


def _texts(pairs):
    return [p for p, _src in pairs]


class TestShortAbbreviationFullForm:
    def test_is_short_abbreviation(self):
        assert radar.is_short_abbreviation("EFT")
        assert radar.is_short_abbreviation("LSS")
        for word in ("FoG", "SPCF", "EFTofLSS", "E", "eft", "Fingers"):
            assert not radar.is_short_abbreviation(word)

    def test_body_full_form_replaces_short_abbreviation(self):
        """scan-09: ``EFT`` は本文の「effective field theory (EFT)」のフル表記に置き換わる。"""
        from core.abbreviations import extract_abbreviations

        def body(abbr):
            return extract_abbreviations([SCAN09_BODY], shortest=True).get(abbr, "")

        out = radar.title_thesis_keyphrases(
            SCAN09_TITLE, SCAN09_THESIS, summary=SCAN09_SUMMARY, body_full_form=body,
        )
        texts = _texts(out)
        assert "EFT" not in texts
        assert "effective field theory" in texts
        # 論文固有の略語（短い略語の形に当たらない）は残す。
        assert "FoG" in texts and "SPCF" in texts
        assert texts[0] == "Fingers of God"

    def test_without_full_form_short_abbreviation_is_kept(self):
        """フル表記が見つからないときは略語を残す（情報を落とさない）。"""
        texts = _texts(radar.title_thesis_keyphrases(
            SCAN09_TITLE, SCAN09_THESIS, summary=SCAN09_SUMMARY,
            body_full_form=lambda abbr: "",
        ))
        assert "EFT" in texts
        assert "effective field theory" not in texts
        # 注入関数が無い（純関数の既定）でも同じ。
        assert "EFT" in _texts(radar.title_thesis_keyphrases(
            SCAN09_TITLE, SCAN09_THESIS, summary=SCAN09_SUMMARY,
        ))

    def test_failing_body_lookup_keeps_abbreviation(self):
        def boom(abbr):
            raise RuntimeError("db down")

        texts = _texts(radar.title_thesis_keyphrases(
            SCAN09_TITLE, SCAN09_THESIS, summary=SCAN09_SUMMARY, body_full_form=boom,
        ))
        assert "EFT" in texts

    def test_abbreviation_dropped_when_containing_phrase_supplied(self):
        """842bd9cb の形: フル表記を含む句が既に供給されていれば短い略語は落とす。"""
        title = "Kinetic Theory of the EFTofLSS: A Halo-based Approach"
        summary = (
            "The Effective Field Theory of Large Scale Structure (EFTofLSS) provides a "
            "description. The Effective Field Theory (EFT) of Large Scale Structure is used. "
            "The EFT counterterms are computed with the EFT."
        )
        calls = []
        texts = _texts(radar.title_thesis_keyphrases(
            title, "", summary=summary, body_full_form=lambda a: calls.append(a) or "",
        ))
        assert "Effective Field Theory of Large Scale Structure" in texts
        assert "EFTofLSS" in texts
        assert "EFT" not in texts
        # 材料にフル表記があれば本文を引かない。
        assert calls == []

    def test_body_full_form_queries_chunks_only_for_short_abbreviations(self):
        class _Rows:
            def __init__(self, rows):
                self._rows = rows

            def fetchall(self):
                return self._rows

        class _Session:
            def __init__(self):
                self.calls = []

            def execute(self, stmt, params):
                self.calls.append((str(stmt), params))
                return _Rows([(SCAN09_BODY,)])

        session = _Session()
        assert radar.body_full_form(session, "doc-1", "EFT") == "effective field theory"
        sql, params = session.calls[0]
        assert "FROM chunks" in sql and "CAST(:document_id AS uuid)" in sql
        assert params["half"] == "(EFT" and params["limit"] == radar.BODY_ABBREVIATION_MAX_CHUNKS
        # 短い略語でなければ DB を読まない。
        assert radar.body_full_form(session, "doc-1", "SPCF") == ""
        assert radar.body_full_form(session, "", "EFT") == ""
        assert len(session.calls) == 1


class TestSoleWidening:
    def _phrases(self, *texts):
        return [{"text": t, "source": "thesis", "enabled": True} for t in texts]

    def _scan09_like(self):
        # 9 件中 8 件が EFT だけで一致（scan-09 の形）。
        cands = [{"title": "Full-shape trispectrum", "summary": "Fingers of God and FoG"}]
        cands += [
            {"title": f"Axion paper {i}", "summary": "an EFT of something else"} for i in range(8)
        ]
        return cands

    def test_sole_majority_word_widens_with_note(self):
        hits = radar.keyphrase_hits(
            self._scan09_like(),
            self._phrases("Fingers of God", "single-particle characteristic function", "FoG", "EFT"),
        )
        by = {h["text"]: h for h in hits}
        assert by["EFT"]["widens"] is True
        assert by["EFT"]["widens_note"] == radar.NOTE_KEYPHRASE_SOLE_WIDENING
        assert by["FoG"]["widens"] is False and "widens_note" not in by["FoG"]
        notes = radar.keyphrase_hit_notes(hits)
        assert notes["keyphrase_widening_note"].startswith(radar.NOTE_KEYPHRASE_SOLE_WIDENING)
        assert notes["keyphrase_widening_note"].endswith(": EFT")
        # 件数・割合を書かない（PD4）。
        assert not any(ch.isdigit() for ch in notes["keyphrase_widening_note"])

    def test_sole_not_majority_does_not_widen(self):
        cands = [
            {"title": "a", "summary": "EFT"},
            {"title": "b", "summary": "FoG and EFT"},
            {"title": "c", "summary": "FoG and EFT"},
            {"title": "d", "summary": "FoG"},
        ]
        by = {h["text"]: h for h in radar.keyphrase_hits(cands, self._phrases("FoG", "EFT"))}
        assert by["EFT"]["widens"] is False and "widens_note" not in by["EFT"]

    def test_sole_widening_needs_enough_candidates_and_phrases(self):
        two = [{"title": "x", "summary": "EFT"}, {"title": "y", "summary": "EFT"}]
        assert not radar.keyphrase_hits(two, self._phrases("FoG", "EFT"))[1]["widens"]
        three = two + [{"title": "z", "summary": "EFT"}]
        # 有効な語が 1 語では自明なので言わない。
        assert not radar.keyphrase_hits(three, self._phrases("EFT"))[0]["widens"]

    def test_all_hit_word_keeps_existing_note(self):
        three = [{"title": f"dark energy {i}", "summary": ""} for i in range(3)]
        hits = radar.keyphrase_hits(three, self._phrases("dark energy", "kurtosis"))
        assert hits[0] == {"text": "dark energy", "hit": True, "widens": True}
        assert radar.keyphrase_hit_notes(hits)["keyphrase_widening_note"].startswith(
            radar.NOTE_KEYPHRASES_WIDENING_HEAD
        )


# ---------------------------------------------------------------------------
# §24 A（IK-0760）— 1 つの語が取得した候補の多くに当たる
# ---------------------------------------------------------------------------


class TestDominantKeyphrase:
    def _phrases(self, *texts):
        return [{"text": t, "source": "thesis", "enabled": True} for t in texts]

    def _scan07_like(self):
        # 9 件中 6 件に Full-Shape（主題語 Galaxy Trispectrum と併記の論文もある = 単独一致ではない）。
        cands = [
            {"title": f"DESI Full-Shape analysis {i}", "summary": "EFTofLSS full-shape fits"}
            for i in range(4)
        ]
        cands += [
            {"title": "Full-Shape and Galaxy Trispectrum", "summary": "EFTofLSS"},
            {"title": "Full-Shape bispectrum", "summary": "FoG"},
        ]
        cands += [
            {"title": f"Galaxy Trispectrum paper {i}", "summary": "FoG and EFTofLSS"} for i in range(3)
        ]
        return cands

    def test_majority_hit_word_gets_a_count_free_fact(self):
        hits = radar.keyphrase_hits(
            self._scan07_like(), self._phrases("Full-Shape", "Galaxy Trispectrum", "EFTofLSS", "FoG"),
        )
        by = {h["text"]: h for h in hits}
        assert by["Full-Shape"]["dominant"] is True
        assert by["Full-Shape"]["dominant_note"] == radar.NOTE_KEYPHRASE_DOMINANT_TEMPLATE.format(
            text="Full-Shape"
        )
        # 既存の印とは重ねない（すべてに当たらず・単独一致の過半でもない）。
        assert by["Full-Shape"]["widens"] is False
        assert "dominant" not in by["Galaxy Trispectrum"]
        notes = radar.keyphrase_hit_notes(hits)
        assert notes["keyphrase_dominant_note"].startswith("「Full-Shape」")
        assert "網が一般語に寄っている可能性があります" in notes["keyphrase_dominant_note"]
        # 件数・割合を書かない（PD4）。
        assert not any(ch.isdigit() for ch in notes["keyphrase_dominant_note"])

    def test_all_hit_and_sole_words_are_not_doubled(self):
        three = [{"title": f"dark energy {i}", "summary": ""} for i in range(3)]
        hits = radar.keyphrase_hits(three, self._phrases("dark energy", "kurtosis"))
        assert "dominant" not in hits[0] and hits[0]["widens"] is True
        assert "keyphrase_dominant_note" not in radar.keyphrase_hit_notes(hits)

    def test_below_the_share_is_silent(self):
        cands = [
            {"title": "a", "summary": "FoG"},
            {"title": "b", "summary": "FoG EFT"},
            {"title": "c", "summary": "EFT SPCF"},
            {"title": "d", "summary": "SPCF"},
        ]
        hits = radar.keyphrase_hits(cands, self._phrases("FoG", "EFT", "SPCF"))
        assert not any(h.get("dominant") for h in hits)

    def test_needs_enough_candidates_and_phrases(self):
        two = [{"title": "x", "summary": "Full-Shape FoG"}, {"title": "y", "summary": "Full-Shape"}]
        assert not any(h.get("dominant") for h in radar.keyphrase_hits(two, self._phrases("Full-Shape", "FoG")))

    def test_threshold_is_a_code_constant_not_env(self):
        src = (BACKEND / "core" / "paper_discovery" / "radar.py").read_text(encoding="utf-8")
        assert "DOMINANT_HIT_SHARE_NUMERATOR = 2" in src
        assert "DOMINANT_HIT_SHARE_DENOMINATOR = 3" in src
        assert "DOMINANT" not in (BACKEND / "core" / "config.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# §24 B（IK-0706）— 帯の母集団の事実文
# ---------------------------------------------------------------------------


class TestBandPopulationNote:
    def test_present_whenever_bands_are_shown(self):
        assert radar.band_population_note([{"distance_label": "x"}]) == radar.NOTE_BAND_POPULATION
        assert radar.band_population_note([{"arxiv_id": "a"}]) == ""
        assert radar.band_population_note([]) == ""

    def test_note_has_no_numbers(self):
        assert not any(ch.isdigit() for ch in radar.NOTE_BAND_POPULATION)
        assert "取得していない論文" in radar.NOTE_BAND_POPULATION

    def test_run_radar_search_attaches_it_without_relation_labels(self, no_arxiv, monkeypatch):
        """§20 D の reading_note と違い、関係の種類が無くても帯が出れば付く。"""
        src = (BACKEND / "core" / "paper_discovery" / "radar.py").read_text(encoding="utf-8")
        body = src[src.index("def run_radar_search"):src.index("def keyphrase_hits")]
        assert 'result["band_population_note"] = population' in body
        assert "band_population_note(result[\"candidates\"])" in body


# ---------------------------------------------------------------------------
# §24 C（IK-0759）— 取り込み済み候補の比較は写しを読む（arXiv を増やさない）
# ---------------------------------------------------------------------------


class TestCompareIngestedUsesCache:
    def test_cached_ingested_candidate_needs_no_arxiv_call(self, no_arxiv, monkeypatch):
        fetch_calls: list = []
        monkeypatch.setattr(
            compare_mod.arxiv_client, "fetch_by_ids",
            lambda ids, **kw: fetch_calls.append(list(ids)) or [],
        )
        monkeypatch.setattr("core.deliberation.refs.document_run_artifacts", lambda document_id: {})
        monkeypatch.setattr(
            compare_mod, "load_candidate_analyses", lambda *a, **k: {}
        )
        llm_calls: list = []
        monkeypatch.setattr(
            compare_mod, "_call_llm",
            lambda content, model: llm_calls.append(content) or _parsed([]),
        )
        session = _seed_session(
            documents=[
                {"id": "doc-1", "source_path": "mat-1", "title": "起点論文",
                 "source_url": "https://arxiv.org/pdf/2608.20293v2"},
                # 取り込み済みの候補（教材として在る）。
                {"id": "doc-cand", "source_path": "mat-cand", "title": "A candidate",
                 "source_url": "https://arxiv.org/src/2608.00002"},
            ],
            arxiv_metadata=[
                schema.ArxivEntry(arxiv_id="2608.20293", summary="seed abstract"),
                _candidate_entry(),
            ],
        )
        compare_mod.run_compare(session, "doc-1", ["2608.00002"], visible_document_ids={"doc-cand"})
        assert fetch_calls == [], "写しに在れば取り込み済み候補でも arXiv を呼ばない（§14）"
        assert len(llm_calls) == 1, "LLM は 1 回のまま"


# ---------------------------------------------------------------------------
# §24 D（IK-0761）— 起点の系譜（取り込みの監査から読み時導出）
# ---------------------------------------------------------------------------


class _LineageSession(FakeSession):
    """取り込みの監査（theory_review_events）を持つフェイク。"""

    def __init__(self, *, events=(), **kwargs):
        super().__init__(**kwargs)
        # {"action", "arxiv_ids", "seed_document_id"}（新しい順に並べておく）
        self.events = list(events)

    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        if "FROM theory_review_events" in sql:
            import json as _json

            p = dict(params or {})
            self.calls.append((sql, p))
            wanted = _json.loads(p["arxiv_json"])[0]
            rows = [
                (e["seed_document_id"],)
                for e in self.events
                if e["action"] in p["actions"]
                and wanted in e["arxiv_ids"]
                and e.get("seed_document_id")
            ]
            return _Result(rows[: int(p["limit"])])
        return super().execute(stmt, params)


def _lineage_docs():
    return [
        {"id": "doc-a", "source_path": "mat-a", "title": "元の起点", "source_url": "https://arxiv.org/abs/2407.01221"},
        {"id": "doc-b", "source_path": "mat-b", "title": "トライスペクトル", "source_url": "https://arxiv.org/src/2610.08908"},
        {"id": "doc-c", "source_path": "mat-c", "title": "FoG の再和", "source_url": "https://arxiv.org/src/2609.35757"},
    ]


def _lineage_events():
    return [
        {"action": "ingest", "arxiv_ids": ["2609.35757"], "seed_document_id": "doc-b"},
        {"action": "ingest_batch", "arxiv_ids": ["2610.08908", "2610.00001"], "seed_document_id": "doc-a"},
    ]


class TestSeedLineage:
    def test_walks_back_oldest_to_newest(self):
        session = _LineageSession(documents=_lineage_docs(), events=_lineage_events())
        out = radar.seed_lineage(session, "doc-c", visible_document_ids={"doc-a", "doc-b", "doc-c"})
        assert [x["document_id"] for x in out["lineage"]] == ["doc-a", "doc-b", "doc-c"]
        assert out["lineage_line"] == radar.LINEAGE_HEAD + "元の起点 → トライスペクトル → FoG の再和"
        assert out["lineage_note"] == radar.NOTE_LINEAGE_SCOPE
        assert not any(ch.isdigit() for ch in out["lineage_line"] + out["lineage_note"])

    def test_material_id_ref_is_resolved(self):
        session = _LineageSession(documents=_lineage_docs(), events=_lineage_events())
        out = radar.seed_lineage(session, "mat-c", visible_document_ids={"doc-a", "doc-b", "doc-c"})
        assert out["lineage"][-1]["document_id"] == "doc-c"

    def test_no_record_means_no_lineage(self):
        session = _LineageSession(documents=_lineage_docs(), events=[])
        assert radar.seed_lineage(session, "doc-c", visible_document_ids={"doc-c"}) == {}

    def test_empty_visibility_issues_no_sql(self):
        session = _LineageSession(documents=_lineage_docs(), events=_lineage_events())
        assert radar.seed_lineage(session, "doc-c", visible_document_ids=set()) == {}
        assert session.calls == []

    def test_visibility_is_a_required_keyword(self):
        session = _LineageSession(documents=_lineage_docs(), events=_lineage_events())
        with pytest.raises(TypeError):
            radar.seed_lineage(session, "doc-c")  # type: ignore[call-arg]

    def test_hidden_document_is_not_named_and_stops_the_walk(self):
        session = _LineageSession(documents=_lineage_docs(), events=_lineage_events())
        out = radar.seed_lineage(session, "doc-c", visible_document_ids={"doc-a", "doc-c"})
        assert out["lineage"][0] == {"document_id": "", "title": radar.LINEAGE_HIDDEN_TITLE}
        assert "トライスペクトル" not in out["lineage_line"]
        assert "元の起点" not in out["lineage_line"], "見えない論文より前は辿らない"
        assert out["lineage_note"].endswith(radar.NOTE_LINEAGE_HIDDEN)

    def test_depth_is_capped_with_a_fact(self):
        n = radar.LINEAGE_MAX_DEPTH + 3
        docs = [
            {"id": f"d{i}", "source_path": f"m{i}", "title": f"論文{chr(0x3042 + i)}",
             "source_url": f"https://arxiv.org/abs/2601.{i:05d}"}
            for i in range(n)
        ]
        events = [
            {"action": "ingest", "arxiv_ids": [f"2601.{i:05d}"], "seed_document_id": f"d{i - 1}"}
            for i in range(1, n)
        ]
        session = _LineageSession(documents=docs, events=events)
        out = radar.seed_lineage(session, f"d{n - 1}", visible_document_ids={d["id"] for d in docs})
        assert len(out["lineage"]) == radar.LINEAGE_MAX_DEPTH + 1
        assert out["lineage_note"].endswith(radar.NOTE_LINEAGE_TRUNCATED)

    def test_cycle_does_not_loop(self):
        docs = _lineage_docs()
        events = _lineage_events() + [
            {"action": "ingest", "arxiv_ids": ["2407.01221"], "seed_document_id": "doc-c"},
        ]
        session = _LineageSession(documents=docs, events=events)
        out = radar.seed_lineage(session, "doc-c", visible_document_ids={"doc-a", "doc-b", "doc-c"})
        assert [x["document_id"] for x in out["lineage"]] == ["doc-a", "doc-b", "doc-c"]

    def test_failure_is_fail_soft(self):
        class _Broken(_LineageSession):
            def execute(self, stmt, params=None):
                if "theory_review_events" in str(stmt):
                    raise RuntimeError("db down")
                return super().execute(stmt, params)

        session = _Broken(documents=_lineage_docs(), events=_lineage_events())
        assert radar.seed_lineage(session, "doc-c", visible_document_ids={"doc-c"}) == {}

    def test_lineage_reads_only_audit_and_does_not_write(self):
        src = (BACKEND / "core" / "paper_discovery" / "radar.py").read_text(encoding="utf-8")
        body = src[src.index("def _lineage_parent_ids"):src.index("__all__ = [")]
        for word in ("INSERT", "UPDATE", "DELETE", "commit("):
            assert word not in body


class TestKeyphraseSupplyTexAndVerbs:
    """§25（2026-10-22・実測 2610.08751）: 題名・中心命題の TeX 残骸と動詞の断片を供給に出さない。"""

    _TITLE = "Mitigating prior-volume effects in galaxy clustering by adopting Mpc over Mpc/h units"
    _THESIS = (
        "フルシェイプ銀河クラスタリング解析の事前体積効果（PVE）は、迷惑パラメータの事前分布を "
        "${\\rm Mpc}/h$ 単位で課すことで $h$ との見かけの相関が持ち込まれることが主な原因であり、"
        "事前分布を ${\\rm Mpc}$ 単位で課すだけで $\\Lambda$CDM の事後分布のずれを取り除ける。"
    )

    def test_strip_tex_drops_math_and_glued_fragments(self):
        out = radar.strip_tex_for_phrases("priors in ${\\rm Mpc}/h$ units shift $\\Lambda$CDM posteriors")
        assert "rm" not in out.split()
        assert "CDM" not in out
        assert "Mpc" not in out  # 数式の中身は記号であって句ではない
        assert "priors in units shift posteriors" == out

    def test_tex_math_fragments_do_not_become_keyphrases(self):
        phrases = [p for p, _ in radar.title_thesis_keyphrases(self._TITLE, self._THESIS)]
        assert "rm Mpc" not in phrases
        assert "CDM" not in phrases

    def test_slash_token_is_one_word(self):
        runs = radar._phrase_runs("adopting Mpc over Mpc/h units")
        assert ["Mpc/h", "units"] in runs
        assert ["h", "units"] not in runs
        phrases = [p for p, _ in radar.title_thesis_keyphrases(self._TITLE, self._THESIS)]
        assert "Mpc/h units" in phrases
        assert "h units" not in phrases

    def test_leading_generic_gerund_is_not_part_of_the_phrase(self):
        runs = radar._phrase_runs("by adopting Mpc over Mpc/h units")
        assert ["adopting", "Mpc"] not in runs
        phrases = [p for p, _ in radar.title_thesis_keyphrases(self._TITLE, self._THESIS)]
        assert "adopting Mpc" not in phrases
        # 分野の名詞の -ing（clustering）は落とさない
        assert "galaxy clustering" in phrases

    def test_display_text_is_not_changed_by_phrase_stripping(self):
        # 落とすのは検索語の材料だけ — 表示の本文は呼び出し側が持つ
        assert radar.strip_tex_for_phrases("plain title") == "plain title"
