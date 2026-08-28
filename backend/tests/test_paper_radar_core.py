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
6. ``ranking.document_centroid`` / ``band_candidates``（未測定にラベルを付けない・
   要旨フォールバックが同一バッチ・fail-soft）
7. ``compare`` の validator（verbatim drop・aspect の fail-closed・素材ゼロで LLM を
   呼ばない）

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

    def __init__(self, *, documents=(), courses=(), components=(), chunks=()):
        self.documents = list(documents)
        self.courses = list(courses)
        self.components = list(components)
        self.chunks = list(chunks)
        self.calls: list[tuple[str, dict]] = []

    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        p = dict(params or {})
        self.calls.append((sql, p))

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

    def test_resolve_seed_never_writes(self, monkeypatch):
        """PR1: 推定を seed 解決の副作用で記帳しない。"""
        _stub_fetch_by_ids(monkeypatch, [_entry("2407.01221", title="Dark Energy: A Review")])
        session = _manual_session()
        radar.resolve_seed(session, "doc-1")
        assert "UPDATE" not in session.sql_log
        assert "INSERT" not in session.sql_log
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
            radar.search, "ingested_arxiv_ids", lambda session: {"2608.00002"}
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
        assert items[0]["common_ground"] == "共通点。"
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
            "common_ground",
            "differences",
            "caveat",
        }
        assert set(items[0]["differences"][0]) == {"aspect", "statement", "evidence_quote"}


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

    def test_prompt_carries_the_required_constraints(self):
        content = compare_mod.build_prompt(
            {"title": "起点", "summary": "要旨"}, [_candidate_entry()]
        )
        assert "アブストラクトに書かれていることだけを比較する" in content
        assert "断定せず推量形で書く" in content
        assert "数値スコア・優劣の評価を書かない" in content
        assert "2608.00002" in content
