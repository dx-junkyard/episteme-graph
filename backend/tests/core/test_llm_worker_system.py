"""core/llm_worker/system.py（WorkerSystem / CostSpec）のユニットテスト。

7系統の worker が共通で使う「組み方」— クライアント生成・修復ループ委譲・
コスト上限ゲート・デーモンスレッド起動 — の振る舞いを、ドメイン非依存に検証する。
"""

from __future__ import annotations

from types import SimpleNamespace

from core.llm_worker.client import BaseJSONLLMClient
from core.llm_worker.cost_gate import CostGate, today_str
from core.llm_worker.system import CostSpec, WorkerSystem


def _system(**cost_kwargs) -> WorkerSystem:
    cost = CostSpec(day_setting="demo_max_calls_per_day", day_default=3, **cost_kwargs)
    return WorkerSystem(
        name="demo",
        model_setting_key="demo_llm_model",
        feature="demo:feature",
        cost=cost,
        log_label="demo worker",
    )


class TestCostSpecLimits:
    def test_reads_settings_fields(self):
        spec = CostSpec(
            day_setting="d", day_default=3, session_setting="s", session_default=1,
        )
        daily, session = spec.limits(SimpleNamespace(d=7, s=2))
        assert (daily, session) == (7, 2)

    def test_falls_back_to_defaults_when_setting_absent(self):
        spec = CostSpec(day_setting="d", day_default=3, session_setting="s", session_default=1)
        assert spec.limits(SimpleNamespace()) == (3, 1)

    def test_day_only_spec_returns_none_session_limit(self):
        assert CostSpec(day_setting="d", day_default=3).limits(SimpleNamespace()) == (3, None)


class TestClient:
    def test_client_is_base_client_with_setting_key(self):
        client = _system().client()
        assert isinstance(client, BaseJSONLLMClient)
        assert client._model_setting_key == "demo_llm_model"

    def test_explicit_model_overrides(self):
        assert _system().client(model="m-1")._model == "m-1"


class TestRunDelegatesToRepairLoop:
    def test_returns_validated_result_and_uses_log_label(self):
        system = _system()

        class _Client:
            def complete_json(self, content):
                return {"ok": True}

        result = system.run(
            _Client(),
            "base",
            validate=lambda data: ("done", [], []),
            build_repair_prompt=lambda raw, errors: "fix",
            on_repair_failed=lambda errors: "failed",
        )
        assert result == "done"

    def test_repair_failure_uses_domain_fallback(self):
        system = _system()
        calls = []

        class _Client:
            def complete_json(self, content):
                calls.append(content)
                return {"bad": True}

        result = system.run(
            _Client(),
            "base",
            validate=lambda data: (None, ["nope"], []),
            build_repair_prompt=lambda raw, errors: "fix",
            on_repair_failed=lambda errors: f"failed:{errors[0]}",
        )
        assert result == "failed:nope"
        assert len(calls) == 3  # 初回 + MAX_REPAIR_ATTEMPTS


class TestCheckAndCount:
    def test_daily_limit_blocks_after_cap(self):
        system = _system()
        settings = SimpleNamespace(demo_max_calls_per_day=2)
        assert system.check_and_count(settings=settings) is True
        assert system.check_and_count(settings=settings) is True
        assert system.check_and_count(settings=settings) is False

    def test_defaults_to_today_key(self):
        system = _system()
        system.check_and_count(settings=SimpleNamespace(demo_max_calls_per_day=5))
        assert system.gate.daily_counts[today_str()] == 1

    def test_session_limit_checked_when_session_key_given(self):
        system = _system(session_setting="demo_max_calls_per_session", session_default=1)
        settings = SimpleNamespace(demo_max_calls_per_day=99, demo_max_calls_per_session=1)
        assert system.check_and_count(daily_key=("u", "d"), session_key=("u", "t"), settings=settings) is True
        assert system.check_and_count(daily_key=("u", "d"), session_key=("u", "t"), settings=settings) is False
        # 別セッションキーはブロックされない（日次上限までは通る）
        assert system.check_and_count(daily_key=("u", "d"), session_key=("u", "t2"), settings=settings) is True

    def test_prune_stale_daily_drops_other_keys(self):
        system = _system(prune_stale_daily=True)
        system.gate.daily_counts["2020-01-01"] = 5
        system.check_and_count(daily_key="2020-01-02", settings=SimpleNamespace(demo_max_calls_per_day=9))
        assert "2020-01-01" not in system.gate.daily_counts

    def test_prune_disabled_by_default_keeps_other_keys(self):
        system = _system()
        system.gate.daily_counts[("other-user", "2020-01-01")] = 5
        system.check_and_count(
            daily_key=("me", "2020-01-01"), settings=SimpleNamespace(demo_max_calls_per_day=9),
        )
        assert system.gate.daily_counts[("other-user", "2020-01-01")] == 5

    def test_explicit_gate_argument_is_used(self):
        system = _system()
        gate = CostGate()
        system.check_and_count(gate=gate, settings=SimpleNamespace(demo_max_calls_per_day=9))
        assert gate.daily_counts and not system.gate.daily_counts


class TestSpawn:
    def test_passes_args_daemon_and_name(self):
        system = _system()
        seen = {}

        class _Thread:
            def __init__(self, target=None, args=(), kwargs=None, name=None, daemon=None):
                seen.update(target=target, args=args, kwargs=kwargs, name=name, daemon=daemon)

            def start(self):
                seen["started"] = True

        def _target():
            pass

        assert system.spawn(_target, thread_factory=_Thread, args=("a",)) is True
        assert seen["target"] is _target
        assert seen["args"] == ("a",)
        assert seen["daemon"] is True
        assert seen["name"] == "demo-worker"
        assert seen["started"] is True

    def test_keyword_arguments_are_forwarded_as_thread_kwargs(self):
        system = _system()
        seen = {}

        class _Thread:
            def __init__(self, target=None, kwargs=None, name=None, daemon=None):
                seen.update(kwargs=kwargs, name=name)

            def start(self):
                pass

        assert system.spawn(lambda: None, thread_factory=_Thread, thread_name="x", document_id="d") is True
        assert seen["kwargs"] == {"document_id": "d"}
        assert seen["name"] == "x"

    def test_name_is_omitted_when_factory_does_not_accept_it(self):
        """name はあくまで診断用の付加情報。受け取れない生成器でも起動を止めない。"""
        system = _system()
        started = []

        class _Thread:
            def __init__(self, target=None, args=(), daemon=None):
                started.append((target, args, daemon))

            def start(self):
                pass

        assert system.spawn(lambda: None, thread_factory=_Thread, args=()) is True
        assert started and started[0][2] is True

    def test_returns_false_when_thread_creation_raises(self):
        system = _system()

        def _boom(**kwargs):
            raise RuntimeError("no threads")

        assert system.spawn(lambda: None, thread_factory=_boom) is False

    def test_returns_false_when_start_raises(self):
        system = _system()

        class _Thread:
            def __init__(self, **kwargs):
                pass

            def start(self):
                raise RuntimeError("can't start new thread")

        assert system.spawn(lambda: None, thread_factory=_Thread) is False


class TestRunForwardsCallInjection:
    """共通ループ側の ``call`` 注入（complete_json を持たない呼び出し口）を素通しする。"""

    def test_call_is_used_instead_of_complete_json(self):
        system = _system()
        seen = []

        def _call(content):
            seen.append(content)
            return {"ok": True}

        result = system.run(
            None,
            "base",
            validate=lambda data: ("done", [], []),
            build_repair_prompt=lambda raw, errors: "fix",
            on_repair_failed=lambda errors: "failed",
            call=_call,
        )
        assert result == "done"
        assert seen == ["base"]
