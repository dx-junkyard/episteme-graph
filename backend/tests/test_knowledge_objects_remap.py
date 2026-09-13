"""agent ID の付け替え記録と参照の再係留（knowledge_objects_design.md §5.5・KO8）。

検査する契約:
  ①``element_id_remap`` に (old → new, stable_key) を記録する
  ②W層 / D層の agent-ID 参照を **document_id で当該論文に閉じて**書き換える
  ③一意制約に当たる行は書き換えず ``reanchored.skipped`` に記録する（推測で結び直さない）
  ④agent ID を持つ参照表の無い種別（evidence / derivation_step / symbol）は記録だけ
DB も LLM も使わない。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.knowledge_objects.remap import record_and_reanchor, summarize  # noqa: E402
from tests.knowledge_object_fakes import FakeKnowledgeSession  # noqa: E402


def _run(session, kind="claim", remaps=(("claim_old", "claim_new", "k1:a"),)):
    return record_and_reanchor(
        session,
        document_id="doc-1",
        run_id="run-1",
        kind=kind,
        remaps=list(remaps),
    )


def _tables_touched(session, verb="UPDATE"):
    return [
        sql.split()[1]
        for sql in session.sql
        if sql.strip().upper().startswith(verb)
    ]


# ---------------------------------------------------------------------------
# ① 記録
# ---------------------------------------------------------------------------


def test_records_the_remap_row_with_kind_and_stable_key():
    session = FakeKnowledgeSession()
    summary = _run(session)
    row = session.inserted_into("element_id_remap")[0]
    assert row["document_id"] == "doc-1"
    assert row["object_kind"] == "claim"
    assert row["old_id"] == "claim_old"
    assert row["new_id"] == "claim_new"
    assert row["stable_key"] == "k1:a"
    assert summary["recorded"] == 1


def test_noop_pairs_are_ignored():
    session = FakeKnowledgeSession()
    summary = _run(session, remaps=[("same", "same", "k1:a"), ("", "x", "k1:b")])
    assert summary == {"recorded": 0, "reanchored": {}, "skipped": {}}
    assert session.sql == []


# ---------------------------------------------------------------------------
# ② 再係留（document_id で閉じる）
# ---------------------------------------------------------------------------


def test_claim_remap_rewrites_w_layer_and_d_layer_references():
    session = FakeKnowledgeSession()
    _run(session)
    updated = _tables_touched(session)
    for table in (
        "element_explanations", "element_annotations", "deliberation_sessions",
        "element_identity_links", "epistemic_ledger", "challenges",
    ):
        assert table in updated, table
    # すべて当該 document に閉じている（challenges は document_id 列を持たないため
    # 台帳経由で絞る）。
    for sql in session.sql:
        if sql.strip().upper().startswith("UPDATE") and "element_id_remap" not in sql:
            assert "document_id" in sql


def test_component_remap_uses_component_vocabulary():
    session = FakeKnowledgeSession()
    _run(session, kind="component", remaps=[("cmp_old", "cmp_new", "k1:c")])
    explanation_updates = [
        where for table, where, _v in session.updates if table == "element_explanations"
    ]
    assert explanation_updates[0]["element_type"] == "theory_component"
    # challenges.target_type の CHECK は ('assumption','claim') なので component では触らない。
    assert "challenges" not in _tables_touched(session)


def test_equation_remap_targets_equation_element_type():
    session = FakeKnowledgeSession()
    _run(session, kind="equation", remaps=[("eq_1", "eq_2", "k1:e")])
    explanation_updates = [
        where for table, where, _v in session.updates if table == "element_explanations"
    ]
    assert explanation_updates[0]["element_type"] == "equation"


# ---------------------------------------------------------------------------
# ③ 一意制約に当たる行はスキップして記録する
# ---------------------------------------------------------------------------


def test_conflicting_ledger_rows_are_recorded_as_skipped():
    session = FakeKnowledgeSession()
    # COUNT(*) の返り値: element_identity_links → epistemic_ledger の順。
    session.scalar_counts = [0, 2]
    summary = _run(session)
    assert summary["skipped"]["epistemic_ledger"] == 2
    ledger_sql = [sql for sql in session.sql if "UPDATE epistemic_ledger" in sql][0]
    # 衝突する行は UPDATE の対象から外す（教員の記帳を別の台帳行に合流させない）。
    assert "NOT EXISTS" in ledger_sql
    remap_update = [
        values for table, _w, values in session.updates if table == "element_id_remap"
    ][0]
    assert json.loads(remap_update["reanchored"])["skipped"]["epistemic_ledger"] == 2


# ---------------------------------------------------------------------------
# ④ 参照表を持たない種別
# ---------------------------------------------------------------------------


def test_kinds_without_agent_id_references_only_record():
    for kind in ("evidence", "derivation_step", "symbol"):
        session = FakeKnowledgeSession()
        summary = _run(session, kind=kind, remaps=[("a", "b", "k1:x")])
        assert summary["recorded"] == 1
        assert summary["reanchored"] == {}
        assert not any("element_explanations" in sql for sql in session.sql)


def test_summarize_counts_reanchored_rows():
    assert summarize({"reanchored": {"a": 2, "b": 3}}) == 5
    assert summarize(None) == 0
