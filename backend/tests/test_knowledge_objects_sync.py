"""live 行の同期（knowledge_objects_design.md §5.2 / §5.3・KO3）。

検査する契約:
  ①一致（stable_key）は **同じ UUID のまま** 内容列だけ UPDATE する（DELETE しない）
  ②``preserved_columns``（人間の確定列）は一致時に触らない
  ③「人間が触った行」では ``protected_when_touched`` も触らない
  ④incoming に無い旧 live 行は ``superseded_at`` / ``superseded_by_run_id`` を刻む
  ⑤agent ID だけが変わった一致行は remap 候補になる
DB も LLM も使わない（fake session で SQL とパラメータを捕捉する）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.knowledge_objects.sync import sync_live_rows  # noqa: E402
from tests.knowledge_object_fakes import FakeKnowledgeSession  # noqa: E402

TABLE = "theory_claims"


def _item(agent_id, stable_key, **values):
    base = {"text": "body", "review_status": "teacher_review_required"}
    base.update(values)
    return {"agent_id": agent_id, "stable_key": stable_key, "values": base}


def _sync(session, incoming, **kwargs):
    options = {
        "table": TABLE,
        "document_id": "doc-1",
        "run_id": "run-1",
        "incoming": incoming,
        "content_columns": ("text", "source_scope"),
        "preserved_columns": ("review_status", "created_by"),
        "agent_id_column": "agent_claim_id",
    }
    options.update(kwargs)
    return sync_live_rows(session, **options)


def _live(row_id, stable_key, agent_id="old_agent", **extra):
    row = {
        "id": row_id,
        "stable_key": stable_key,
        "agent_id": agent_id,
        "review_status": "teacher_review_required",
        "created_by": None,
    }
    row.update(extra)
    return row


# ---------------------------------------------------------------------------
# ① 一致は同 UUID のまま更新し、DELETE しない
# ---------------------------------------------------------------------------


def test_matching_stable_key_updates_in_place_without_delete():
    session = FakeKnowledgeSession(live_rows=[_live("uuid-1", "k1:a")])
    result = _sync(session, [_item("new_agent", "k1:a", text="updated")])

    assert result.id_map == {"new_agent": "uuid-1"}
    assert result.stats == {"updated": 1, "inserted": 0, "superseded": 0}
    assert session.inserted_into(TABLE) == []
    assert not any("DELETE" in sql for sql in session.sql)
    values = session.updated_in(TABLE)[0]
    assert values["text"] == "updated"
    assert values["produced_by_run_id"] == "run-1"
    assert values["agent_claim_id"] == "new_agent"


def test_preserved_columns_are_not_written_on_match():
    session = FakeKnowledgeSession(live_rows=[
        _live("uuid-1", "k1:a", review_status="teacher_approved"),
    ])
    _sync(session, [_item("a", "k1:a", review_status="teacher_review_required")])
    values = session.updated_in(TABLE)[0]
    assert "review_status" not in values
    assert "created_by" not in values


def test_preserved_columns_are_written_on_insert():
    session = FakeKnowledgeSession()
    _sync(session, [_item("a", "k1:a", review_status="teacher_review_required")])
    row = session.inserted_into(TABLE)[0]
    assert row["review_status"] == "teacher_review_required"
    assert row["stable_key"] == "k1:a"
    assert row["document_id"] == "doc-1"
    assert row["produced_by_run_id"] == "run-1"


# ---------------------------------------------------------------------------
# ③ 人間が触った行の追加保護
# ---------------------------------------------------------------------------


def test_protected_columns_skipped_only_for_touched_rows():
    def touched(row):
        return bool(row.get("teacher_notes"))

    session = FakeKnowledgeSession(live_rows=[
        _live("uuid-touched", "k1:a", teacher_notes="教員のメモ"),
        _live("uuid-plain", "k1:b", teacher_notes=""),
    ])
    _sync(
        session,
        [_item("a", "k1:a", text="t1"), _item("b", "k1:b", text="t2")],
        content_columns=("text", "name"),
        human_touched=touched,
        protected_when_touched=("name",),
        touch_columns=("teacher_notes",),
    )
    touched_values, plain_values = session.updated_in(TABLE)
    assert "text" not in touched_values or touched_values.get("text") == "t1"
    assert "name" not in touched_values
    assert plain_values["text"] == "t2"


# ---------------------------------------------------------------------------
# ④ incoming に無い live 行は supersede（行は残る）
# ---------------------------------------------------------------------------


def test_missing_live_rows_are_superseded_not_deleted():
    session = FakeKnowledgeSession(live_rows=[
        _live("uuid-keep", "k1:a"), _live("uuid-drop", "k1:z"),
    ])
    result = _sync(session, [_item("a", "k1:a")])
    assert result.stats["superseded"] == 1
    assert session.superseded == ["uuid-drop"]
    supersede_sql = [sql for sql in session.sql if "superseded_at = now()" in sql]
    assert supersede_sql and "DELETE" not in supersede_sql[0]


def test_empty_incoming_still_supersedes_live_rows():
    """S-7 の早期 return 撤去: 素材ゼロを「行はそのまま」と解釈しない。"""
    session = FakeKnowledgeSession(live_rows=[_live("uuid-1", "k1:a")])
    result = _sync(session, [])
    assert result.stats == {"updated": 0, "inserted": 0, "superseded": 1}
    assert session.superseded == ["uuid-1"]


# ---------------------------------------------------------------------------
# ⑤ remap 候補
# ---------------------------------------------------------------------------


def test_agent_id_change_on_match_becomes_a_remap_candidate():
    session = FakeKnowledgeSession(live_rows=[_live("uuid-1", "k1:a", agent_id="claim_old")])
    result = _sync(session, [_item("claim_new", "k1:a")])
    assert result.remaps == [("claim_old", "claim_new", "k1:a")]


def test_same_agent_id_produces_no_remap():
    session = FakeKnowledgeSession(live_rows=[_live("uuid-1", "k1:a", agent_id="claim_1")])
    result = _sync(session, [_item("claim_1", "k1:a")])
    assert result.remaps == []


# ---------------------------------------------------------------------------
# 値のバインド（dict / list は jsonb、宣言された列は明示 CAST）
# ---------------------------------------------------------------------------


def test_dict_and_list_values_are_bound_as_jsonb():
    session = FakeKnowledgeSession()
    _sync(session, [_item("a", "k1:a", source_scope={"legacy_ids": ["x"]})])
    row = session.inserted_into(TABLE)[0]
    assert json.loads(row["source_scope"]) == {"legacy_ids": ["x"]}
    insert_sql = [sql for sql in session.sql if "INSERT INTO" in sql][0]
    assert "AS jsonb" in insert_sql


def test_column_casts_are_applied():
    session = FakeKnowledgeSession()
    _sync(
        session,
        [_item("a", "k1:a", chunk_id="00000000-0000-0000-0000-000000000001")],
        content_columns=("text", "chunk_id"),
        column_casts={"chunk_id": "uuid"},
    )
    insert_sql = [sql for sql in session.sql if "INSERT INTO" in sql][0]
    assert "AS uuid" in insert_sql


def test_nul_characters_are_stripped_before_binding():
    session = FakeKnowledgeSession()
    _sync(session, [_item("a", "k1:a", text="bad\x00text")])
    row = session.inserted_into(TABLE)[0]
    assert row["text"] == "badtext"


def test_run_id_none_does_not_write_produced_by_run_id():
    session = FakeKnowledgeSession()
    _sync(session, [_item("a", "k1:a")], run_id=None)
    row = session.inserted_into(TABLE)[0]
    assert "produced_by_run_id" not in row


def test_duplicate_stable_keys_in_one_run_do_not_reuse_the_same_row():
    """同じ live 行に2つの incoming をぶつけない（2本目は新規行になる）。"""
    session = FakeKnowledgeSession(live_rows=[_live("uuid-1", "k1:a")])
    result = _sync(session, [_item("a1", "k1:a"), _item("a2", "k1:a")])
    assert result.stats["updated"] == 1
    assert result.stats["inserted"] == 1
    assert result.id_map["a1"] == "uuid-1"
    assert result.id_map["a2"] != "uuid-1"
