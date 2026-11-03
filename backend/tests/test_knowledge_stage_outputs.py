"""知識オブジェクト保存の結果を run の ``stage_outputs`` にも残す（2026-09-19）。

これまで保存件数は ``persist_claims_components_graph`` の artifact にしか無く、
``stage_outputs.knowledge_objects`` はどの run にも存在しなかった。派生表の保存が
失敗しても run は ``completed`` のままなので、run 行だけを見ている読み手には
「入っている」ように見えていた（learning_units が UniqueViolation で 78 件まるごと
ロールバックしても run は completed）。

固定する契約:

①claims / components / 知識オブジェクト / learning_units の各保存が、成功時に
  ``document_analysis_runs.stage_outputs.knowledge_objects`` へ要約を**合流**させる
  （既存キーを消さない浅いマージ）
②``run_id`` が無い呼び出しでは run 行に触らない
③失敗したときは、その事実（``failed`` / ``error``）が別セッションで記録される
④要約の記録は呼び出し元トランザクションに同乗する（保存が巻き戻れば要約も巻き戻る）
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.document_pipeline import persistence  # noqa: E402
from tests.knowledge_object_fakes import FakeKnowledgeSession  # noqa: E402

DOC = "33333333-3333-3333-3333-333333333333"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _run_updates(session) -> list[dict]:
    return [
        params
        for table, params, _values in session.updates
        if table == "document_analysis_runs"
    ]


def _stage_output_payloads(session) -> list[dict]:
    return [json.loads(params["payload"]) for params in _run_updates(session)]


def _evidence_registry():
    return types.SimpleNamespace(records=[
        types.SimpleNamespace(
            evidence_id="ev_1", document_id=DOC,
            source=types.SimpleNamespace(
                block_id="b1", section_id="sec1", page=1, span_start=0, span_end=5,
            ),
            evidence_text="quote", evidence_role="source_quote",
            public_export_policy="location_only", parent_evidence_id=None,
        ),
    ])


def _persist_knowledge(run_id="run-1", session=None):
    session = session or FakeKnowledgeSession(id_prefix="ko")
    with patch.object(persistence, "_pg_session", return_value=session):
        summary = persistence.persist_knowledge_objects(
            document_id=DOC, run_id=run_id, evidence_registry=_evidence_registry(),
        )
    return summary, session


# ---------------------------------------------------------------------------
# ① 成功時の記録
# ---------------------------------------------------------------------------


def test_knowledge_objects_summary_lands_in_stage_outputs():
    summary, session = _persist_knowledge()
    payloads = _stage_output_payloads(session)
    assert payloads, "stage_outputs へ要約が書かれていない"
    assert payloads[0]["evidence"]["inserted"] == 1
    assert payloads[0]["skipped"] == summary["skipped"]


def test_stage_output_merge_keeps_existing_keys():
    _summary, session = _persist_knowledge()
    sql = [s for s in session.sql if "document_analysis_runs" in s][0]
    # 既存の stage_outputs と knowledge_objects の中身の両方を `||` で合流させる。
    assert "COALESCE(stage_outputs, '{}'::jsonb)" in sql
    assert "COALESCE(stage_outputs -> :key, '{}'::jsonb)" in sql
    assert "DELETE" not in sql.upper()


def test_stage_output_key_is_knowledge_objects():
    _summary, session = _persist_knowledge()
    assert _run_updates(session)[0]["key"] == "knowledge_objects"
    assert persistence.KNOWLEDGE_OBJECTS_STAGE_OUTPUT_KEY == "knowledge_objects"


def test_learning_units_summary_lands_in_stage_outputs():
    skeleton = types.SimpleNamespace(logical_blocks=[
        types.SimpleNamespace(
            block_id="lb_1", block_type="assumptions", label="Slow roll",
            section_ids=["sec2"], evidence_block_ids=["b1"],
            summary="", reason="", confidence=0.9,
        ),
    ])
    session = FakeKnowledgeSession(id_prefix="unit")
    with patch.object(persistence, "_pg_session", return_value=session):
        persistence.persist_learning_units(document_id=DOC, run_id="run-1", skeleton=skeleton)
    payloads = _stage_output_payloads(session)
    assert payloads[0]["learning_units"]["inserted"] == 1


# ---------------------------------------------------------------------------
# ② run_id が無ければ run 行に触らない
# ---------------------------------------------------------------------------


def test_without_a_run_id_the_run_row_is_untouched():
    _summary, session = _persist_knowledge(run_id=None)
    assert _run_updates(session) == []


# ---------------------------------------------------------------------------
# ③ 失敗の事実も残す
# ---------------------------------------------------------------------------


def test_learning_units_failure_is_recorded_on_the_run():
    class _Boom(FakeKnowledgeSession):
        def execute(self, statement, params=None):
            if "learning_units" in str(statement):
                raise RuntimeError("duplicate key value violates unique constraint")
            return super().execute(statement, params)

    failing = _Boom(id_prefix="unit")
    recorder = FakeKnowledgeSession(id_prefix="rec")
    sessions = [failing, recorder]
    skeleton = types.SimpleNamespace(logical_blocks=[
        types.SimpleNamespace(
            block_id="lb_1", block_type="assumptions", label="Slow roll",
            section_ids=["sec2"], evidence_block_ids=["b1"],
            summary="", reason="", confidence=0.9,
        ),
    ])
    with patch.object(persistence, "_pg_session", side_effect=lambda: sessions.pop(0)):
        try:
            persistence.persist_learning_units(
                document_id=DOC, run_id="run-1", skeleton=skeleton
            )
        except RuntimeError:
            pass
    payloads = _stage_output_payloads(recorder)
    assert payloads[0]["learning_units"]["failed"] is True
    assert "duplicate key" in payloads[0]["learning_units"]["error"]
    assert failing.rolled_back is True


def test_failure_recording_never_raises():
    class _AlwaysBroken(FakeKnowledgeSession):
        def execute(self, statement, params=None):
            raise RuntimeError("db is gone")

    with patch.object(persistence, "_pg_session", return_value=_AlwaysBroken()):
        # 記録の失敗で解析を落とさない（fail-soft）。
        persistence.record_knowledge_stage_output_failure(
            run_id="run-1", kind="learning_units", error="boom"
        )


# ---------------------------------------------------------------------------
# ④ 同一トランザクション
# ---------------------------------------------------------------------------


def test_summary_is_written_before_commit_in_the_same_session():
    _summary, session = _persist_knowledge()
    # commit 済み = 保存と同じトランザクションで確定している。
    assert session.committed is True
    assert _run_updates(session), "別セッションではなく同じセッションで書く"


def test_every_knowledge_persist_function_reports_its_summary():
    """4 つの保存経路すべてが要約を記録する（片方だけ落ちない構造にする）。"""
    import ast

    source = (ROOT / "backend" / "core" / "document_pipeline" / "persistence.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    reporting: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        body = ast.get_source_segment(source, node) or ""
        if "_record_knowledge_stage_output(" in body:
            reporting.add(node.name)
    assert {
        "persist_qualified_claims",
        "persist_components",
        "persist_knowledge_objects",
        "persist_learning_units",
    } <= reporting
