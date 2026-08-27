"""論文ディスカバリー Phase 3 — 関連度ランキング（``core/paper_discovery/ranking.py``
+ ``POST /api/admin/discovery/search`` の ``order``）。

正本: ``docs/features/paper_discovery_design.md`` §6（Phase 3）/ §2（PD4 数値スコアを
教員にも見せない・PD6 閉世界の正直さ）。

検証観点:

1. 重心（``field_centroid``）が決定論的（document ごとに先頭 N チャンクを平均 →
   document ベクトルを平均。対象ゼロ・embedding ゼロは ``None``）
2. 段階ラベルが ``core.label_vocab``（正本）経由で、cosine の生値が DTO に出ない（PD4）
3. fail-soft 3種（重心なし / embedding 例外 / 日次上限）— 検索は新着順で成立する
4. U層計測（``usage_context("discovery:ranking")`` 下で1バッチだけ呼ぶ）
5. API: ``order`` 不正値は 422 / 既定 ``date`` は Phase 1〜2 と完全に同一（``ranking``
   キーを付けない）/ ``relevance`` は並べ替え済み候補 + ``ranking`` を返す
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _path in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, str(_path))

from core.label_vocab import DISCOVERY_RELEVANCE_SCALE  # noqa: E402
from core.paper_discovery import ranking  # noqa: E402


# ---------------------------------------------------------------------------
# フェイクセッション（DB へ行かない）
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def fetchall(self):
        return list(self._rows)


class FakeSession:
    """``learning_courses`` / ``documents`` / ``chunks`` の最小フェイク。

    ``chunk_rows`` は ``(document_id, embedding)`` の列で、``chunk_index`` による
    絞り込みは ``chunk_limit`` で表現する（SQL の ``chunk_index < :per_document``）。
    """

    def __init__(self, *, courses=(), documents=(), chunks=()):
        self.courses = list(courses)
        self.documents = list(documents)
        self.chunks = list(chunks)
        self.calls: list[tuple[str, dict]] = []

    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        p = dict(params or {})
        self.calls.append((sql, p))
        if "FROM learning_courses" in sql:
            return _Result(
                [(c["data"],) for c in self.courses
                 if (c.get("data") or {}).get("cartridge_id") == p.get("domain_key")]
            )
        if "FROM documents" in sql:
            wanted = set(p.get("material_ids") or [])
            rows = [d for d in self.documents if d.get("source_path") in wanted]
            return _Result(
                [
                    (d["id"], d.get("title") or "", d.get("source_url") or "")
                    for d in rows
                ]
            )
        if "FROM chunks" in sql:
            wanted = set(p.get("document_ids") or [])
            limit = int(p.get("per_document") or 0)
            rows = [
                (c["document_id"], c["embedding"])
                for c in self.chunks
                if c["document_id"] in wanted and int(c.get("chunk_index", 0)) < limit
            ]
            return _Result(rows)
        return _Result()

    def commit(self):  # pragma: no cover — core は commit しない
        raise AssertionError("core must not commit")

    def close(self):
        pass

    @property
    def sql_log(self) -> str:
        return "\n".join(sql for sql, _ in self.calls)


def _session_with(chunks, *, source_url="") -> FakeSession:
    return FakeSession(
        courses=[{"data": {"cartridge_id": "astrophysics", "sources": [{"material_id": "m1"}]}}],
        documents=[{"id": "doc-1", "source_path": "m1", "title": "T", "source_url": source_url}],
        chunks=chunks,
    )


@pytest.fixture(autouse=True)
def _reset_counter():
    ranking.reset_daily_counter()
    yield
    ranking.reset_daily_counter()


# ---------------------------------------------------------------------------
# 1. 重心
# ---------------------------------------------------------------------------


class TestFieldCentroid:
    def test_averages_chunks_then_documents(self):
        session = FakeSession(
            courses=[
                {"data": {"cartridge_id": "astrophysics",
                          "sources": [{"material_id": "m1"}, {"material_id": "m2"}]}},
            ],
            documents=[
                {"id": "doc-1", "source_path": "m1"},
                {"id": "doc-2", "source_path": "m2"},
            ],
            chunks=[
                # doc-1 は2チャンク（平均 [1, 0]）、doc-2 は1チャンク（[0, 2]）。
                {"document_id": "doc-1", "chunk_index": 0, "embedding": [2.0, 0.0]},
                {"document_id": "doc-1", "chunk_index": 1, "embedding": [0.0, 0.0]},
                {"document_id": "doc-2", "chunk_index": 0, "embedding": [0.0, 2.0]},
            ],
        )
        centroid = ranking.field_centroid(session, "astrophysics")
        # チャンク数の多い doc-1 が重心を独占しない（document 単位で1票）。
        assert centroid == [0.5, 1.0]

    def test_is_deterministic(self):
        chunks = [
            {"document_id": "doc-1", "chunk_index": i, "embedding": [float(i), 1.0]}
            for i in range(5)
        ]
        first = ranking.field_centroid(_session_with(chunks), "astrophysics")
        second = ranking.field_centroid(_session_with(list(reversed(chunks))), "astrophysics")
        assert first == second

    def test_respects_the_per_document_chunk_limit(self):
        chunks = [
            {"document_id": "doc-1", "chunk_index": 0, "embedding": [1.0, 0.0]},
            {"document_id": "doc-1", "chunk_index": 9, "embedding": [99.0, 0.0]},
        ]
        centroid = ranking.field_centroid(_session_with(chunks), "astrophysics", chunks_per_document=1)
        assert centroid == [1.0, 0.0]

    def test_parses_pgvector_text_representation(self):
        chunks = [{"document_id": "doc-1", "chunk_index": 0, "embedding": "[1.0, 3.0]"}]
        assert ranking.field_centroid(_session_with(chunks), "astrophysics") == [1.0, 3.0]

    def test_no_documents_returns_none_without_touching_chunks(self):
        session = FakeSession()
        assert ranking.field_centroid(session, "astrophysics") is None
        assert "FROM chunks" not in session.sql_log

    def test_no_embeddings_returns_none(self):
        assert ranking.field_centroid(_session_with([]), "astrophysics") is None

    def test_unusable_vectors_are_dropped(self):
        chunks = [
            {"document_id": "doc-1", "chunk_index": 0, "embedding": "not-a-vector"},
            {"document_id": "doc-1", "chunk_index": 1, "embedding": [4.0, 0.0]},
        ]
        assert ranking.field_centroid(_session_with(chunks), "astrophysics") == [4.0, 0.0]


# ---------------------------------------------------------------------------
# 2. 並べ替えと段階ラベル（PD4）
# ---------------------------------------------------------------------------


def _stub_embeddings(monkeypatch, vectors, *, recorder=None):
    """``core.llm.generate_embeddings`` を差し替える（U層の feature も記録する）。"""
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


class TestRankCandidates:
    def _candidates(self):
        return [
            {"arxiv_id": "2608.00001", "title": "far", "summary": "unrelated"},
            {"arxiv_id": "2608.00002", "title": "near", "summary": "close"},
        ]

    def test_orders_by_similarity_and_labels_are_from_the_canon(self, monkeypatch):
        calls: list[dict] = []
        # 重心は [1, 0]。1件目は直交（cosine 0）、2件目はほぼ一致（cosine 1）。
        _stub_embeddings(monkeypatch, [[0.0, 1.0], [1.0, 0.0]], recorder=calls)
        session = _session_with(
            [{"document_id": "doc-1", "chunk_index": 0, "embedding": [1.0, 0.0]}]
        )

        result = ranking.rank_candidates(session, "astrophysics", self._candidates())

        assert result["available"] is True
        assert [c["arxiv_id"] for c in result["ordered"]] == ["2608.00002", "2608.00001"]
        assert result["ordered"][0]["relevance_label"] == DISCOVERY_RELEVANCE_SCALE.label_for(1.0)
        assert result["ordered"][1]["relevance_label"] == DISCOVERY_RELEVANCE_SCALE.label_for(0.0)
        assert result["ordered"][1]["relevance_label"] == DISCOVERY_RELEVANCE_SCALE.cautious_label
        # 1検索 = 1バッチコール
        assert len(calls) == 1
        assert calls[0]["texts"] == ["far\nunrelated", "near\nclose"]

    def test_usage_context_is_attributed_to_discovery_ranking(self, monkeypatch):
        calls: list[dict] = []
        _stub_embeddings(monkeypatch, [[1.0, 0.0]], recorder=calls)
        session = _session_with(
            [{"document_id": "doc-1", "chunk_index": 0, "embedding": [1.0, 0.0]}]
        )
        ranking.rank_candidates(session, "astrophysics", [{"title": "a", "summary": "b"}])
        assert calls[0]["feature"] == "discovery:ranking"

    def test_no_raw_similarity_in_the_dto(self, monkeypatch):
        _stub_embeddings(monkeypatch, [[1.0, 0.0], [0.0, 1.0]])
        session = _session_with(
            [{"document_id": "doc-1", "chunk_index": 0, "embedding": [1.0, 0.0]}]
        )
        result = ranking.rank_candidates(session, "astrophysics", self._candidates())
        for candidate in result["ordered"]:
            assert not any(
                key in candidate
                for key in ("score", "similarity", "relevance", "confidence", "rank")
            )
            assert not [v for v in candidate.values() if isinstance(v, float)]

    def test_input_candidates_are_not_mutated(self, monkeypatch):
        _stub_embeddings(monkeypatch, [[1.0, 0.0], [0.0, 1.0]])
        session = _session_with(
            [{"document_id": "doc-1", "chunk_index": 0, "embedding": [1.0, 0.0]}]
        )
        original = self._candidates()
        ranking.rank_candidates(session, "astrophysics", original)
        assert all("relevance_label" not in c for c in original)

    def test_ties_keep_the_incoming_order(self, monkeypatch):
        _stub_embeddings(monkeypatch, [[1.0, 0.0], [1.0, 0.0]])
        session = _session_with(
            [{"document_id": "doc-1", "chunk_index": 0, "embedding": [1.0, 0.0]}]
        )
        result = ranking.rank_candidates(session, "astrophysics", self._candidates())
        assert [c["arxiv_id"] for c in result["ordered"]] == ["2608.00001", "2608.00002"]

    def test_unmeasurable_candidates_go_last_with_the_cautious_label(self, monkeypatch):
        # 2件目のベクトルはゼロ（cosine 未測定）。
        _stub_embeddings(monkeypatch, [[0.0, 1.0], [0.0, 0.0]])
        session = _session_with(
            [{"document_id": "doc-1", "chunk_index": 0, "embedding": [1.0, 0.0]}]
        )
        result = ranking.rank_candidates(session, "astrophysics", self._candidates())
        assert result["ordered"][-1]["arxiv_id"] == "2608.00002"
        assert result["ordered"][-1]["relevance_label"] == DISCOVERY_RELEVANCE_SCALE.cautious_label


# ---------------------------------------------------------------------------
# 3. fail-soft（検索そのものは必ず成立させる）
# ---------------------------------------------------------------------------


class TestFailSoft:
    def test_no_corpus_keeps_the_incoming_order(self, monkeypatch):
        calls: list[dict] = []
        _stub_embeddings(monkeypatch, [[1.0]], recorder=calls)
        candidates = [{"arxiv_id": "a"}, {"arxiv_id": "b"}]
        result = ranking.rank_candidates(FakeSession(), "astrophysics", candidates)
        assert result["available"] is False
        assert result["note"] == ranking.NOTE_NO_CORPUS
        assert [c["arxiv_id"] for c in result["ordered"]] == ["a", "b"]
        assert all("relevance_label" not in c for c in result["ordered"])
        assert calls == [], "重心が無いときは embedding を呼ばない"

    def test_embedding_failure_degrades(self, monkeypatch):
        import core.llm as llm_module

        def _boom(texts, **kwargs):
            raise RuntimeError("provider down")

        monkeypatch.setattr(llm_module, "generate_embeddings", _boom)
        session = _session_with(
            [{"document_id": "doc-1", "chunk_index": 0, "embedding": [1.0, 0.0]}]
        )
        result = ranking.rank_candidates(session, "astrophysics", [{"title": "a"}])
        assert result["available"] is False
        assert result["note"] == ranking.NOTE_UNAVAILABLE
        assert result["ordered"] == [{"title": "a"}]

    def test_daily_limit_degrades_with_a_factual_note(self, monkeypatch):
        _stub_embeddings(monkeypatch, [[1.0, 0.0]])
        session = _session_with(
            [{"document_id": "doc-1", "chunk_index": 0, "embedding": [1.0, 0.0]}]
        )
        first = ranking.rank_candidates(
            session, "astrophysics", [{"title": "a"}], daily_limit=1
        )
        assert first["available"] is True
        second = ranking.rank_candidates(
            session, "astrophysics", [{"title": "a"}], daily_limit=1
        )
        assert second["available"] is False
        assert second["note"] == ranking.NOTE_LIMIT_REACHED
        # 事実文に数値（上限・使用回数）を出さない（PD4 / U5 の流儀）。
        assert not any(ch.isdigit() for ch in second["note"])

    def test_empty_candidates(self):
        result = ranking.rank_candidates(FakeSession(), "astrophysics", [])
        assert result == {
            "available": False,
            "note": ranking.NOTE_NO_CANDIDATES,
            "ordered": [],
        }

    def test_embedding_count_mismatch_degrades(self, monkeypatch):
        _stub_embeddings(monkeypatch, [[1.0, 0.0]])  # 候補2件に対して1件しか返らない
        session = _session_with(
            [{"document_id": "doc-1", "chunk_index": 0, "embedding": [1.0, 0.0]}]
        )
        result = ranking.rank_candidates(
            session, "astrophysics", [{"title": "a"}, {"title": "b"}]
        )
        assert result["available"] is False
        assert result["note"] == ranking.NOTE_UNAVAILABLE

    def test_centroid_error_degrades(self, monkeypatch):
        monkeypatch.setattr(
            ranking, "field_centroid",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")),
        )
        result = ranking.rank_candidates(FakeSession(), "astrophysics", [{"title": "a"}])
        assert result["available"] is False
        assert result["note"] == ranking.NOTE_UNAVAILABLE


# ---------------------------------------------------------------------------
# 4. API（``POST /search`` の ``order``）
# ---------------------------------------------------------------------------


try:
    import fastapi  # noqa: F401
    _HAS_FASTAPI = True
except ImportError:  # pragma: no cover
    _HAS_FASTAPI = False


@pytest.fixture
def api(monkeypatch):
    from fastapi.testclient import TestClient
    from api.main import app
    from dependencies import ROLE_TEACHER, _create_token
    import routes.paper_discovery as routes

    state: dict = {
        "search_result": {
            "domain_key": "astrophysics",
            "query": '(cat:astro-ph.CO)',
            "total": 2,
            "start": 0,
            "candidates": [
                {"arxiv_id": "2608.00001", "title": "a", "summary": "x"},
                {"arxiv_id": "2608.00002", "title": "b", "summary": "y"},
            ],
            "closed_world_note": routes.pd_search.CLOSED_WORLD_NOTE,
        },
        "rank_calls": [],
        "rank_result": None,
    }

    class _Session:
        def execute(self, *a, **k):
            return _Result()

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(routes, "_pg_session", lambda: _Session())
    monkeypatch.setattr(routes, "record_review_event", lambda *args: None)
    monkeypatch.setattr(
        routes.pd_search, "run_search",
        lambda session, domain_key, **kw: dict(state["search_result"]),
    )

    def _rank(session, domain_key, candidates, **kwargs):
        state["rank_calls"].append((domain_key, list(candidates)))
        if state["rank_result"] is not None:
            return state["rank_result"]
        ordered = [
            dict(c, relevance_label="関連: 高") for c in reversed(list(candidates))
        ]
        return {"available": True, "ordered": ordered}

    monkeypatch.setattr(routes.pd_ranking, "rank_candidates", _rank)

    state["client"] = TestClient(app)
    state["headers"] = {
        "Authorization": "Bearer "
        + _create_token("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "k", "k@x", ROLE_TEACHER)
    }
    return state


@pytest.mark.skipif(not _HAS_FASTAPI, reason="FastAPI not installed")
class TestSearchOrderApi:
    _PATH = "/api/admin/discovery/search"

    def test_default_is_unchanged(self, api):
        res = api["client"].post(
            self._PATH, json={"domain_key": "astrophysics"}, headers=api["headers"]
        )
        assert res.status_code == 200
        body = res.json()
        assert "ranking" not in body and "order" not in body
        assert body == api["search_result"]
        assert api["rank_calls"] == [], "既定では関連度計算をしない（embedding を使わない）"

    def test_explicit_date_is_unchanged(self, api):
        res = api["client"].post(
            self._PATH, json={"domain_key": "astrophysics", "order": "date"},
            headers=api["headers"],
        )
        assert res.status_code == 200
        assert "ranking" not in res.json()
        assert api["rank_calls"] == []

    def test_relevance_reorders_and_reports(self, api):
        res = api["client"].post(
            self._PATH, json={"domain_key": "astrophysics", "order": "relevance"},
            headers=api["headers"],
        )
        assert res.status_code == 200
        body = res.json()
        assert body["order"] == "relevance"
        assert body["ranking"] == {"available": True}
        assert [c["arxiv_id"] for c in body["candidates"]] == ["2608.00002", "2608.00001"]
        assert all(c["relevance_label"] == "関連: 高" for c in body["candidates"])
        # 閉世界の注記と検索条件は並べ替えても落とさない（PD6）。
        assert body["query"] == api["search_result"]["query"]
        assert body["closed_world_note"]

    def test_relevance_degrades_with_note_and_keeps_candidates(self, api):
        api["rank_result"] = {
            "available": False,
            "note": "この分野には、関連度の基準にできる取り込み済み論文がまだありません。新着順で表示します。",
            "ordered": api["search_result"]["candidates"],
        }
        res = api["client"].post(
            self._PATH, json={"domain_key": "astrophysics", "order": "relevance"},
            headers=api["headers"],
        )
        body = res.json()
        assert body["ranking"]["available"] is False
        assert body["ranking"]["note"]
        assert [c["arxiv_id"] for c in body["candidates"]] == ["2608.00001", "2608.00002"]
        assert all("relevance_label" not in c for c in body["candidates"])

    def test_ranking_exception_does_not_break_search(self, api, monkeypatch):
        import routes.paper_discovery as routes

        monkeypatch.setattr(
            routes.pd_ranking, "rank_candidates",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        res = api["client"].post(
            self._PATH, json={"domain_key": "astrophysics", "order": "relevance"},
            headers=api["headers"],
        )
        assert res.status_code == 200
        body = res.json()
        assert body["ranking"]["available"] is False
        assert len(body["candidates"]) == 2

    @pytest.mark.parametrize("order", ["score", "RELEVANCE", "newest", "0"])
    def test_invalid_order_is_422(self, api, order):
        res = api["client"].post(
            self._PATH, json={"domain_key": "astrophysics", "order": order},
            headers=api["headers"],
        )
        # 語彙外は fail-closed（黙って新着順にも関連度順にも倒さない）。
        assert res.status_code == 422
        assert api["rank_calls"] == []

    def test_empty_order_falls_back_to_the_default(self, api):
        """未指定と同義（フロントが空文字を送っても新着順で成立する）。"""
        res = api["client"].post(
            self._PATH, json={"domain_key": "astrophysics", "order": ""},
            headers=api["headers"],
        )
        assert res.status_code == 200
        assert "ranking" not in res.json()
