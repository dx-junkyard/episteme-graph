"""IK-0790: scope_candidates worker の対象選択から式由来の合成主張を外す回帰テスト。

式から決定論で合成した定型主張（origin='equation_synthesis'）は論文の文を持たず、
LLM 候補は必ず空になる。対象に入ると日次上限だけを消費していた。
"""

from __future__ import annotations

from unittest.mock import patch

from core.doubt.scope_candidates import input_builder, worker
from core.schema import CLAIM_ORIGIN_EQUATION_SYNTHESIS


class _Result:
    def __init__(self, rows=None, one=None):
        self._rows = rows or []
        self._one = one

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._one


class _FakeSession:
    def __init__(self, *, claim_rows=None, skipped_exists=False):
        self.calls: list[tuple[str, dict]] = []
        self._claim_rows = claim_rows or []
        self._skipped = skipped_exists

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self.calls.append((sql, dict(params or {})))
        if "RETURNING target_type, target_id" in sql:
            return _Result(rows=self._claim_rows)
        if "SELECT EXISTS" in sql:
            return _Result(one=(self._skipped,))
        return _Result()

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def test_claim_sql_excludes_equation_synthesis_claims():
    session = _FakeSession()
    worker._claim_pending_targets(session, "doc-1", "")
    sql, params = session.calls[0]
    assert "theory_claims_live" in sql
    assert "NOT (" in sql
    assert "c.origin = ANY(:excluded_origins)" in sql
    assert params["excluded_origins"] == [CLAIM_ORIGIN_EQUATION_SYNTHESIS]


def test_worker_records_skip_reason_without_count_and_no_llm_call():
    session = _FakeSession(claim_rows=[], skipped_exists=True)
    with patch.object(worker, "get_session", return_value=session), \
         patch.object(worker, "ScopeCandidateAgent") as agent_cls, \
         patch.object(worker, "_check_and_count_llm_call") as gate:
        result = worker.run_scope_candidate_mining(document_id="doc-1")
    assert result["skipped_reasons"] == [worker.SKIP_REASON_SYNTHESIZED_CLAIMS]
    assert result["processed"] == 0
    gate.assert_not_called()
    agent_cls.return_value.run.assert_not_called()
    # 件数ではなく理由だけを返す
    assert all(isinstance(r, str) for r in result["skipped_reasons"])
    # 台帳行（scope_candidates）は書き換えない
    assert not any("SET scope_candidates =" in sql for sql, _ in session.calls)


def test_worker_has_no_skip_reason_when_nothing_excluded():
    session = _FakeSession(claim_rows=[], skipped_exists=False)
    with patch.object(worker, "get_session", return_value=session), \
         patch.object(worker, "ScopeCandidateAgent"):
        result = worker.run_scope_candidate_mining(document_id="doc-1")
    assert result["skipped_reasons"] == []


def test_input_builder_ignores_synthesized_claims_for_claim_and_equation():
    for target_type in ("claim", "equation"):
        session = _FakeSession()
        ctx = input_builder.build_target_context(session, target_type, "x")
        sql, params = session.calls[0]
        assert "origin IS DISTINCT FROM :synth" in sql
        assert params["synth"] == CLAIM_ORIGIN_EQUATION_SYNTHESIS
        assert not ctx.has_sources()
