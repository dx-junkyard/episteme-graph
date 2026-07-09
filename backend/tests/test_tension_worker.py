"""tension worker（Stage 1 バッチ）の振る舞いテスト — 冪等性・トリガー・candidate 保存。

外部サービス依存なし: DB セッションはフェイク、Agent はモックに差し替える。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import core.tension.worker as worker
from core.tension.schema import TargetRefs, TensionCandidate, TensionMiningResult


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    """SQL の部分文字列で応答を切り替える簡易フェイク。実行文を記録する。"""

    def __init__(self, script: dict, log: list):
        self._script = script
        self.log = log

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self.log.append((sql, params))
        for key, rows in self._script.items():
            if key in sql:
                return _FakeResult(rows)
        return _FakeResult([])

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


@pytest.fixture(autouse=True)
def _reset_cost_counters():
    worker._session_call_counts.clear()
    worker._daily_call_counts.clear()
    yield


def _candidate() -> TensionCandidate:
    return TensionCandidate(
        tension_type="definition_unease",
        evidence_quote="なんとなく腑に落ちない",
        turn_ids=["msg_0000"],
        target_refs=TargetRefs(topic_id="t1"),
        paraphrase="引っかかりが残っているのかもしれません",
        confidence=0.8,
        reason="hedge + revisit",
    )


def _wire(monkeypatch, script: dict, result: TensionMiningResult):
    log: list = []
    monkeypatch.setattr(worker, "_pg_session", lambda: _FakeSession(script, log))
    agent = MagicMock()
    agent.run.return_value = result
    monkeypatch.setattr(worker, "TensionMiningAgent", lambda: agent)
    return log, agent


HISTORY = [
    {"role": "user", "content": "なんとなく腑に落ちない"},
    {"role": "assistant", "content": "説明します"},
]


class TestRunTensionMining:
    def test_saves_candidates_with_candidate_status(self, monkeypatch):
        script = {
            "payload->>'tension_hint' = 'true'": [("id-1", {"text": "なんとなく腑に落ちない", "tension_hint": True}, None)],
            "SET analyzed_at = now()": [("id-1",)],
            "FROM learning_chat_history": [(HISTORY,)],
            "FROM learning_courses": [({"topics": [], "chapters": []},)],
        }
        log, agent = _wire(monkeypatch, script, TensionMiningResult(candidates=[_candidate()]))
        saved = worker.run_tension_mining("u1", "c1", "t1")
        assert saved == 1
        assert agent.run.call_count == 1
        inserts = [(s, p) for s, p in log if "INSERT INTO interest_traces" in s]
        assert len(inserts) == 1
        sql, params = inserts[0]
        assert "'tension'" in sql and "'candidate'" in sql  # P1: 常に候補として保存
        assert "腑に落ちない" in params["payload"]

    def test_idempotent_when_no_pending_hints(self, monkeypatch):
        """analyzed_at 済み（未解析ヒントなし）の窓は再実行してもスキップされる。"""
        log, agent = _wire(monkeypatch, {}, TensionMiningResult(candidates=[_candidate()]))
        assert worker.run_tension_mining("u1", "c1", "t1") == 0
        assert agent.run.call_count == 0

    def test_skips_when_claim_lost_to_concurrent_run(self, monkeypatch):
        """並行スレッドが先に analyzed_at を立てた場合は LLM を呼ばず終了する。"""
        script = {
            "payload->>'tension_hint' = 'true'": [("id-1", {"text": "x"}, None)],
            "SET analyzed_at = now()": [],  # 先行スレッドが claim 済み
        }
        log, agent = _wire(monkeypatch, script, TensionMiningResult())
        assert worker.run_tension_mining("u1", "c1", "t1") == 0
        assert agent.run.call_count == 0

    def test_repair_failure_saves_one_unclassified_row(self, monkeypatch):
        script = {
            "payload->>'tension_hint' = 'true'": [("id-1", {"text": "なんとなく腑に落ちない"}, None)],
            "SET analyzed_at = now()": [("id-1",)],
            "FROM learning_chat_history": [(HISTORY,)],
            "FROM learning_courses": [({},)],
        }
        log, _ = _wire(monkeypatch, script,
                       TensionMiningResult(repair_failed=True, warnings=["repair_failed: bad json"]))
        saved = worker.run_tension_mining("u1", "c1", "t1")
        assert saved == 1  # P4: 破棄せず unclassified で1行残す
        sql, params = [(s, p) for s, p in log if "INSERT INTO interest_traces" in s][0]
        assert '"tension_type": "unclassified"' in params["payload"]
        assert '"repair_failed": true' in params["payload"]

    def test_daily_cost_cap_blocks_llm_call(self, monkeypatch):
        script = {
            "payload->>'tension_hint' = 'true'": [("id-1", {"text": "x"}, None)],
            "SET analyzed_at = now()": [("id-1",)],
            "FROM learning_chat_history": [(HISTORY,)],
            "FROM learning_courses": [({},)],
        }
        log, agent = _wire(monkeypatch, script, TensionMiningResult(candidates=[_candidate()]))
        worker._daily_call_counts[("u1", worker._today())] = 10_000
        assert worker.run_tension_mining("u1", "c1", "t1") == 0
        assert agent.run.call_count == 0


class TestMaybeSchedule:
    def _wire_threads(self, monkeypatch, pending_rows):
        monkeypatch.setattr(
            worker, "_pg_session",
            lambda: _FakeSession({"payload->>'tension_hint' = 'true'": pending_rows}, []),
        )
        started = []

        class _FakeThread:
            def __init__(self, target=None, args=(), daemon=None):
                started.append((target, args))

            def start(self):
                pass

        monkeypatch.setattr(worker.threading, "Thread", _FakeThread)
        return started

    def test_below_threshold_does_not_schedule(self, monkeypatch):
        rows = [("id-1", {}, None)] * (worker.HINT_BATCH_THRESHOLD - 1)
        started = self._wire_threads(monkeypatch, rows)
        assert worker.maybe_schedule_tension_mining("u1", "c1", "t1") is False
        assert started == []

    def test_threshold_reached_schedules_background_run(self, monkeypatch):
        rows = [("id-1", {}, None)] * worker.HINT_BATCH_THRESHOLD
        started = self._wire_threads(monkeypatch, rows)
        assert worker.maybe_schedule_tension_mining("u1", "c1", "t1") is True
        assert started and started[0][0] is worker.run_tension_mining

    def test_session_end_idle_schedules_even_below_threshold(self, monkeypatch):
        import datetime
        old = datetime.datetime.now() - datetime.timedelta(minutes=worker.SESSION_IDLE_MINUTES + 5)
        started = self._wire_threads(monkeypatch, [("id-1", {}, old)])
        assert worker.maybe_schedule_tension_mining("u1", "c1", "t1", session_end_check=True) is True
        assert len(started) == 1

    def test_recent_activity_defers_session_end_run(self, monkeypatch):
        import datetime
        recent = datetime.datetime.now() - datetime.timedelta(minutes=1)
        started = self._wire_threads(monkeypatch, [("id-1", {}, recent)])
        assert worker.maybe_schedule_tension_mining("u1", "c1", "t1", session_end_check=True) is False
        assert started == []

    def test_db_error_is_swallowed(self, monkeypatch):
        def _boom():
            raise RuntimeError("db down")
        monkeypatch.setattr(worker, "_pg_session", _boom)
        assert worker.maybe_schedule_tension_mining("u1", "c1", "t1") is False
