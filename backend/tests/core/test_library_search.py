"""core/library/search.py（L層 retrieval）のテスト。

N23残: 「最新凍結版の embedding が failed/欠落（NULL）なら、直近の有効な embedding を
持つ凍結版をそのエントリの代表として retrieval 対象に含める」fallback を構造的に固定する。

pgvector の `<=>` 演算子は実 DB でしか動かないため、SQL の構造検査（fallback の実体 =
row_number() の PARTITION を embedding IS NOT NULL の行だけで振ること）と、
セッションに渡る SQL/パラメータを記録するフェイクでの挙動検査を組み合わせる。
公開関数のシグネチャ（search_frozen_entries / find_similar_entries）は変更しない。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.library import search as library_search  # noqa: E402

_SEARCH_SRC = (Path(__file__).resolve().parents[2] / "core" / "library" / "search.py").read_text(
    encoding="utf-8"
)


def _embedding_search_block() -> str:
    match = re.search(r"def _embedding_search[\s\S]+?\n\ndef ", _SEARCH_SRC)
    assert match, "_embedding_search not found in search.py"
    return match.group(0)


class TestEmbeddingFallbackStructure:
    """SQL 構造で N23 fallback を固定する（ガードレール流儀の構造検査）。"""

    def test_embedding_filter_is_inside_latest_cte(self):
        """``embedding IS NOT NULL`` は CTE（row_number の母集団）側の WHERE に
        置かれる。これにより rn=1 は「embedding を持つ版のうち最新」を指し、
        最新版の embedding が NULL でも直近の有効版がエントリの代表になる。"""
        block = _embedding_search_block()
        # CTE 本体 = "WITH latest AS (" から外側の "SELECT l." の直前まで
        # （途中の row_number() OVER ( ... ) の閉じ括弧で誤って切らない）。
        cte_match = re.search(r"WITH latest AS \(([\s\S]+?)\)\s*SELECT l\.", block)
        assert cte_match, "WITH latest CTE not found in _embedding_search"
        cte_body = cte_match.group(1)
        assert "WHERE v.embedding IS NOT NULL" in cte_body, (
            "N23 fallback: embedding フィルタは row_number() の母集団（CTE 内 WHERE）に"
            "置くこと。外側の条件に置くと最新版が NULL のエントリが retrieval から"
            "静かに脱落する（フォールバックしない）"
        )

    def test_outer_conditions_do_not_refilter_latest_only(self):
        """外側の WHERE に ``l.embedding IS NOT NULL`` を再導入しない
        （再導入しても無害だが、fallback の実体が CTE 側であることを分かりにくくする。
        rn=1 フィルタと status/domain フィルタのみが外側の責務）。"""
        block = _embedding_search_block()
        conditions_match = re.search(r"conditions = \[([\s\S]+?)\]", block)
        assert conditions_match, "conditions list not found"
        assert "l.embedding IS NOT NULL" not in conditions_match.group(1)
        assert '"l.rn = 1"' in conditions_match.group(1)

    def test_partition_is_per_entry_latest_first(self):
        block = _embedding_search_block()
        assert "PARTITION BY v.entry_id ORDER BY v.version_no DESC" in block

    def test_returned_version_is_the_representative_version(self):
        """返す version_no / content は代表となった版のもの（fallback 時は旧版番号）。
        referenced_library_versions の記録が事実と一致するための前提。"""
        block = _embedding_search_block()
        assert "l.version_no" in block
        assert "l.content" in block


class TestSearchFrozenEntriesBehaviour:
    """セッション差し替えで SQL 発行までの挙動（縮退・パラメータ）を検証する。"""

    class _RecordingSession:
        def __init__(self, rows=None, fail=False):
            self.rows = rows or []
            self.fail = fail
            self.calls = []

        def execute(self, stmt, params=None):
            sql = " ".join(str(stmt).split())
            self.calls.append((sql, dict(params or {})))
            if self.fail:
                raise RuntimeError("boom")
            session = self

            class _Result:
                def fetchall(self_inner):
                    return list(session.rows)

            return _Result()

        def close(self):
            pass

    def _patch_session(self, monkeypatch, session):
        monkeypatch.setattr(library_search, "get_session", lambda: session)

    def test_returns_rows_from_frozen_content(self, monkeypatch):
        # 行形式: (entry_id, version_no, content, distance)
        rows = [("e1", 2, {"name": "ECDL", "aliases": ["laser"], "summary": "s", "body": {}}, 0.12)]
        session = self._RecordingSession(rows=rows)
        self._patch_session(monkeypatch, session)
        results = library_search.search_frozen_entries(
            domain_key="particle_physics",
            query_text="laser",
            embed_fn=lambda text: [0.1, 0.2],
        )
        assert results == [
            {
                "entry_id": "e1",
                "version_no": 2,
                "name": "ECDL",
                "aliases": ["laser"],
                "summary": "s",
                "body": {},
                "distance": 0.12,
            }
        ]
        sql = session.calls[0][0]
        assert "WHERE v.embedding IS NOT NULL" in sql  # N23 fallback が実 SQL にも出る

    def test_empty_domain_or_query_short_circuits(self, monkeypatch):
        session = self._RecordingSession()
        self._patch_session(monkeypatch, session)
        assert library_search.search_frozen_entries(
            domain_key="", query_text="x", embed_fn=lambda t: [0.1]
        ) == []
        assert library_search.search_frozen_entries(
            domain_key="d", query_text="", embed_fn=lambda t: [0.1]
        ) == []
        assert session.calls == []

    def test_query_failure_degrades_to_empty_list(self, monkeypatch):
        session = self._RecordingSession(fail=True)
        self._patch_session(monkeypatch, session)
        assert library_search.search_frozen_entries(
            domain_key="d", query_text="x", embed_fn=lambda t: [0.1]
        ) == []

    def test_embed_failure_degrades_to_empty_list(self, monkeypatch):
        session = self._RecordingSession()
        self._patch_session(monkeypatch, session)

        def _boom(text):
            raise RuntimeError("embedding down")

        assert library_search.search_frozen_entries(
            domain_key="d", query_text="x", embed_fn=_boom
        ) == []
        assert session.calls == []
