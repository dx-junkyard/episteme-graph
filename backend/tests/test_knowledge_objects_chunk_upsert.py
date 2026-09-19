"""source chunk の upsert（migration 084 / KO3）。

再解析のたびに ``DELETE FROM chunks WHERE document_id = …`` → 全件 INSERT していた頃は、
``theory_claims.chunk_id`` の FK が ``ON DELETE CASCADE`` だったため **claim の live 行が
物理削除**されていた（2026-09-13 のレビュー P1-R1）。FK は 084 で ``SET NULL`` に
張り替えたが、そもそも chunk UUID が毎回変わること自体が痕跡（``interest_traces`` の
チャンクアンカー・音声キャッシュ）を切る。

固定する契約:
  ①同じ ``chunk_index`` の既存行は **同じ UUID のまま UPDATE**（INSERT しない）
  ②document 丸ごとの DELETE を発行しない（消すのは余剰行だけ・``id <> ALL(...)``）
  ③新しい chunk_index は INSERT する
  ④084 が FK を ``ON DELETE SET NULL`` に張り替えている（migration の内容検査）

DB も LLM も使わない。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.document_pipeline import persistence  # noqa: E402

DB_DIR = ROOT / "backend" / "db"


class _Chunk:
    def __init__(self, index: int, text: str = "body"):
        self.chunk_index = index
        self.text = text
        self.page_start = 1
        self.page_end = 1
        self.section_id = "sec1"
        self.block_ids = ["b1"]
        self.metadata = {}
        self.formulas = []


class _Result:
    def __init__(self, rows=None):
        self._rows = rows or []

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    """chunks の SELECT に既存行を返し、実行された SQL とパラメータを捕捉する。"""

    def __init__(self, existing: list[tuple[int, str]] | None = None):
        self.existing = existing or []
        self.sql: list[str] = []
        self.params: list[dict] = []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.sql.append(sql)
        self.params.append(dict(params or {}))
        if sql.strip().upper().startswith("SELECT"):
            return _Result([(index, row_id) for index, row_id in self.existing])
        return _Result()

    def commit(self):
        pass

    def rollback(self):  # pragma: no cover - 失敗経路は別テスト
        pass

    def close(self):
        pass


def _run(chunks, existing=None):
    session = _FakeSession(existing=existing)
    with patch.object(persistence, "_pg_session", return_value=session), patch.object(
        persistence, "generate_embeddings", lambda texts: [[0.0] for _ in texts]
    ):
        saved = persistence.persist_source_chunks(
            document_id="11111111-1111-1111-1111-111111111111",
            material_id="mat-1",
            chunks=list(chunks),
        )
    return saved, session


def _statements(session, verb: str) -> list[str]:
    return [sql for sql in session.sql if sql.strip().upper().startswith(verb)]


# ---------------------------------------------------------------------------
# ① 既存の chunk_index は同じ UUID のまま更新される
# ---------------------------------------------------------------------------


def test_existing_chunk_index_keeps_its_uuid():
    saved, session = _run([_Chunk(0), _Chunk(1)], existing=[(0, "uuid-a"), (1, "uuid-b")])

    assert [row["chunk_id"] for row in saved] == ["uuid-a", "uuid-b"]
    assert _statements(session, "INSERT") == []
    assert len(_statements(session, "UPDATE")) == 2


def test_new_chunk_index_is_inserted():
    saved, session = _run([_Chunk(0), _Chunk(1)], existing=[(0, "uuid-a")])

    assert saved[0]["chunk_id"] == "uuid-a"
    assert saved[1]["chunk_id"] != "uuid-a"
    assert len(_statements(session, "INSERT")) == 1
    assert len(_statements(session, "UPDATE")) == 1


def test_all_new_document_inserts_every_chunk():
    saved, session = _run([_Chunk(0), _Chunk(1)])

    assert len(_statements(session, "INSERT")) == 2
    assert len({row["chunk_id"] for row in saved}) == 2


# ---------------------------------------------------------------------------
# ② document 丸ごとの DELETE を発行しない（余剰行だけを消す）
# ---------------------------------------------------------------------------


def test_no_wholesale_delete_for_the_document():
    _saved, session = _run([_Chunk(0)], existing=[(0, "uuid-a"), (1, "uuid-b")])

    deletes = _statements(session, "DELETE")
    assert deletes, "余剰 chunk の掃除は行う"
    for sql in deletes:
        assert "id <> ALL" in sql, (
            "document 丸ごとの DELETE は claim / 痕跡を巻き添えにする（P1-R1）"
        )


def test_surplus_chunks_are_the_only_deletion_target():
    _saved, session = _run([_Chunk(0)], existing=[(0, "uuid-a"), (1, "uuid-b")])

    delete_params = [
        params for sql, params in zip(session.sql, session.params)
        if sql.strip().upper().startswith("DELETE")
    ]
    assert delete_params[0]["kept"] == ["uuid-a"]


def test_empty_chunk_list_touches_nothing():
    saved, session = _run([])
    assert saved == []
    assert session.sql == []


# ---------------------------------------------------------------------------
# ④ migration 084: theory_claims.chunk_id は ON DELETE SET NULL
# ---------------------------------------------------------------------------


def test_migration_084_switches_the_claim_chunk_fk_to_set_null():
    sql = (DB_DIR / "084_claim_chunk_fk_set_null.sql").read_text(encoding="utf-8")
    assert "FOREIGN KEY (chunk_id) REFERENCES chunks(id) ON DELETE SET NULL" in sql
    # 既に SET NULL のときは触らない（毎起動の DROP ↔ ADD を作らない）。
    assert "confdeltype" in sql
    statements = "\n".join(
        line for line in sql.splitlines() if not line.strip().startswith("--")
    )
    assert "DELETE FROM" not in statements, "本 migration に行削除は無い（KO3）"


def test_migration_084_guards_the_chunk_unique_index_against_duplicates():
    sql = (DB_DIR / "084_claim_chunk_fk_set_null.sql").read_text(encoding="utf-8")
    assert "uq_chunks_document_chunk_index" in sql
    assert "CREATE UNIQUE INDEX IF NOT EXISTS" in sql
    # 重複があるときは索引を作らず NOTICE で報告する（黙って行を消さない）。
    assert re.search(r"HAVING count\(\*\) > 1", sql)
