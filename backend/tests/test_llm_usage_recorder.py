"""core/llm_usage/recorder.py — record()/flush_now()/reset_for_tests() のテスト。

観点: record() が例外を漏らさない / バッファ上限と dropped_count / flush_now が
fake session に正しい行数を書く / tracking_enabled=False で no-op / reset_for_tests。

DB セッションは常にフェイクに差し替える（``recorder._session_factory`` を monkeypatch）。
バックグラウンド flusher スレッドは reset_for_tests() で必ず止める。
"""

from __future__ import annotations

import pytest

import core.llm_usage.recorder as recorder
from core.llm_usage.schema import UsageEvent


def _event(**overrides) -> UsageEvent:
    base = dict(
        provider="openai",
        model="gpt-4o",
        operation="chat",
        feature="unattributed",
        usage_source="reported",
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
    )
    base.update(overrides)
    return UsageEvent(**base)


class _FakeResult:
    def __init__(self, rowcount=0):
        self.rowcount = rowcount


class _FakeSession:
    def __init__(self, fail_on_execute=False, fail_on_commit=False):
        self.executed_params: list[dict] = []
        self.committed = False
        self.rolled_back = False
        self.closed = False
        self.fail_on_execute = fail_on_execute
        self.fail_on_commit = fail_on_commit

    def execute(self, stmt, params=None):
        if self.fail_on_execute:
            raise RuntimeError("execute boom")
        if isinstance(params, list):
            self.executed_params.extend(params)
        elif params is not None:
            self.executed_params.append(params)
        return _FakeResult(rowcount=len(params) if isinstance(params, list) else 1)

    def commit(self):
        if self.fail_on_commit:
            raise RuntimeError("commit boom")
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class _RaisingFactory:
    """呼ばれるたびに例外を投げるフェイクの session factory。"""

    def __call__(self):
        raise RuntimeError("cannot connect to db")


@pytest.fixture(autouse=True)
def _clean_recorder_state(monkeypatch):
    recorder.reset_for_tests()
    monkeypatch.setattr(recorder, "_session_factory", None)
    yield
    recorder.reset_for_tests()


class TestRecordNeverRaises:
    def test_record_survives_raising_session_factory(self, monkeypatch):
        monkeypatch.setattr(recorder, "_session_factory", _RaisingFactory())
        # record() 自体は buffer に積むだけなので、session factory の異常には触れない。
        recorder.record(_event())
        assert recorder.dropped_count() == 0

    def test_record_survives_broken_event(self, monkeypatch):
        # metadata に JSON 化できないオブジェクトを混ぜても record() 自体は例外を出さない
        # （実際の失敗は flush_now 側で吸収される）。
        class _Unserializable:
            pass

        monkeypatch.setattr(recorder, "_session_factory", lambda: _FakeSession())
        recorder.record(_event(metadata={"bad": _Unserializable()}))
        # flush 時も json.dumps(default=str) でフォールバックするため例外を出さない
        written = recorder.flush_now()
        assert written == 1

    def test_flush_now_survives_execute_failure(self, monkeypatch):
        session = _FakeSession(fail_on_execute=True)
        monkeypatch.setattr(recorder, "_session_factory", lambda: session)
        recorder.record(_event())
        written = recorder.flush_now()
        assert written == 0
        assert session.closed is True

    def test_flush_now_survives_missing_session(self, monkeypatch):
        monkeypatch.setattr(recorder, "_session_factory", _RaisingFactory())
        recorder.record(_event())
        written = recorder.flush_now()
        assert written == 0


class TestTrackingEnabledToggle:
    def test_disabled_tracking_is_noop(self, monkeypatch):
        fake_settings = _FakeSettings(llm_usage_tracking_enabled=False)
        monkeypatch.setattr(recorder, "get_settings", lambda: fake_settings)
        session = _FakeSession()
        monkeypatch.setattr(recorder, "_session_factory", lambda: session)

        recorder.record(_event())

        written = recorder.flush_now()
        assert written == 0
        assert session.executed_params == []


class TestBufferLimitAndDropped:
    def test_buffer_over_limit_drops_oldest_and_counts(self, monkeypatch):
        fake_settings = _FakeSettings(llm_usage_buffer_max=3, llm_usage_flush_batch=1000)
        monkeypatch.setattr(recorder, "get_settings", lambda: fake_settings)
        session = _FakeSession()
        monkeypatch.setattr(recorder, "_session_factory", lambda: session)

        for i in range(5):
            recorder.record(_event(model=f"model-{i}"))

        assert recorder.dropped_count() == 2

        written = recorder.flush_now()
        assert written == 3
        # 最新の3件（model-2, model-3, model-4）だけが残っているはず
        kept_models = [p["model"] for p in session.executed_params]
        assert kept_models == ["model-2", "model-3", "model-4"]


class TestFlushNowWritesCorrectRows:
    def test_flush_now_writes_all_buffered_rows(self, monkeypatch):
        fake_settings = _FakeSettings(llm_usage_buffer_max=100, llm_usage_flush_batch=1000)
        monkeypatch.setattr(recorder, "get_settings", lambda: fake_settings)
        session = _FakeSession()
        monkeypatch.setattr(recorder, "_session_factory", lambda: session)

        for i in range(4):
            recorder.record(_event(model=f"model-{i}"))

        written = recorder.flush_now()
        assert written == 4
        assert session.committed is True
        assert session.closed is True
        assert len(session.executed_params) == 4
        assert [p["model"] for p in session.executed_params] == [f"model-{i}" for i in range(4)]

    def test_flush_now_empty_buffer_returns_zero(self, monkeypatch):
        session = _FakeSession()
        monkeypatch.setattr(recorder, "_session_factory", lambda: session)
        assert recorder.flush_now() == 0
        assert session.executed_params == []

    def test_flush_now_keeps_buffer_on_commit_failure(self, monkeypatch):
        session = _FakeSession(fail_on_commit=True)
        monkeypatch.setattr(recorder, "_session_factory", lambda: session)
        recorder.record(_event())

        written = recorder.flush_now()
        assert written == 0
        assert session.rolled_back is True

        # バッファに残っているはずなので、成功する session に差し替えれば書き込める
        session2 = _FakeSession()
        monkeypatch.setattr(recorder, "_session_factory", lambda: session2)
        written2 = recorder.flush_now()
        assert written2 == 1


class TestResetForTests:
    def test_reset_clears_buffer_and_dropped(self, monkeypatch):
        fake_settings = _FakeSettings(llm_usage_buffer_max=1)
        monkeypatch.setattr(recorder, "get_settings", lambda: fake_settings)
        monkeypatch.setattr(recorder, "_session_factory", lambda: _FakeSession())

        recorder.record(_event())
        recorder.record(_event())  # 1件 drop される
        assert recorder.dropped_count() == 1

        recorder.reset_for_tests()

        assert recorder.dropped_count() == 0
        assert recorder.flush_now() == 0  # バッファも空になっている


class _FakeSettings:
    """core.config.Settings の代わりに使う最小限のフェイク。"""

    def __init__(
        self,
        llm_usage_tracking_enabled=True,
        llm_usage_buffer_max=1000,
        llm_usage_flush_interval_seconds=10.0,
        llm_usage_flush_batch=100,
    ):
        self.llm_usage_tracking_enabled = llm_usage_tracking_enabled
        self.llm_usage_buffer_max = llm_usage_buffer_max
        self.llm_usage_flush_interval_seconds = llm_usage_flush_interval_seconds
        self.llm_usage_flush_batch = llm_usage_flush_batch
