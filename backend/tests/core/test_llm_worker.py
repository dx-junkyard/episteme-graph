"""core/llm_worker/ の共通実装（BaseJSONLLMClient / run_with_repair / CostGate）のユニットテスト。

tension / structure_anchor / reconstruction / doubt.scope_candidates /
doubt.assumption_mining の5系統に個別実装されていた骨格を集約した実装そのものを
検証する（各系統側の repair.py / worker.py は薄いラッパになったため、5系統の
既存テスト — test_tension_worker.py / test_doubt_scope_candidates.py 等 — は
そのまま「委譲先が正しく動くこと」の回帰テストとして機能する）。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from core.llm_worker.client import BaseJSONLLMClient, parse_json_response, resolve_model
from core.llm_worker.cost_gate import CostGate, InMemoryCounterGate, today_str
from core.llm_worker.repair import MAX_REPAIR_ATTEMPTS, run_with_repair


class TestParseJsonResponse:
    def test_plain_json(self):
        assert parse_json_response('{"a": 1}') == {"a": 1}

    def test_strips_markdown_fence(self):
        assert parse_json_response('```json\n{"a": 1}\n```') == {"a": 1}

    def test_strips_bare_fence(self):
        assert parse_json_response('```\n{"a": 1}\n```') == {"a": 1}

    def test_extracts_outer_braces_with_surrounding_prose(self):
        assert parse_json_response('Sure, here it is: {"a": 1} — hope that helps') == {"a": 1}

    def test_raises_value_error_when_not_json(self):
        with pytest.raises(ValueError):
            parse_json_response("not json at all")


class TestResolveModel:
    def test_uses_override_when_set(self):
        settings = type("S", (), {"llm_fast_model": "fast-x", "my_model_key": "override-model"})()
        with patch("core.llm_policy.get_settings", return_value=settings):
            assert resolve_model("my_model_key") == "override-model"

    def test_falls_back_to_fast_tier_when_empty(self):
        settings = type("S", (), {"llm_fast_model": "fast-x", "my_model_key": ""})()
        with patch("core.llm_policy.get_settings", return_value=settings):
            assert resolve_model("my_model_key") == "fast-x"

    def test_falls_back_to_fast_tier_when_attribute_missing(self):
        settings = type("S", (), {"llm_fast_model": "fast-x"})()
        with patch("core.llm_policy.get_settings", return_value=settings):
            assert resolve_model("nonexistent_key") == "fast-x"

    def test_fallback_fast_is_the_default(self):
        settings = type("S", (), {"llm_fast_model": "fast-x", "llm_analysis_model": "analysis-x"})()
        with patch("core.llm_policy.get_settings", return_value=settings):
            assert resolve_model("missing_key") == resolve_model("missing_key", fallback="fast")

    def test_fallback_analysis_falls_back_to_analysis_tier_when_empty(self):
        settings = type(
            "S", (), {"llm_fast_model": "fast-x", "llm_analysis_model": "analysis-x", "my_model_key": ""}
        )()
        with patch("core.llm_policy.get_settings", return_value=settings):
            assert resolve_model("my_model_key", fallback="analysis") == "analysis-x"

    def test_fallback_analysis_still_prefers_explicit_override(self):
        settings = type(
            "S",
            (),
            {"llm_fast_model": "fast-x", "llm_analysis_model": "analysis-x", "my_model_key": "override-model"},
        )()
        with patch("core.llm_policy.get_settings", return_value=settings):
            assert resolve_model("my_model_key", fallback="analysis") == "override-model"

    def test_unknown_fallback_tier_raises_value_error(self):
        settings = type("S", (), {"llm_fast_model": "fast-x"})()
        with patch("core.llm_policy.get_settings", return_value=settings):
            with pytest.raises(ValueError):
                resolve_model("my_model_key", fallback="deep")


class TestBaseJSONLLMClient:
    def test_complete_json_uses_explicit_model_and_returns_parsed_dict(self):
        client = BaseJSONLLMClient(model_setting_key="my_model_key", model="explicit-model")
        with patch("core.llm_worker.client.generate_text", return_value='{"ok": true}') as mocked:
            result = client.complete_json("hello")
        assert result == {"ok": True}
        assert mocked.call_args.kwargs["model"] == "explicit-model"
        assert mocked.call_args.kwargs["messages"] == [{"role": "user", "content": "hello"}]

    def test_complete_json_resolves_model_when_not_explicit(self):
        client = BaseJSONLLMClient(model_setting_key="my_model_key")
        settings = type("S", (), {"llm_fast_model": "fast-x", "my_model_key": ""})()
        with patch("core.llm_policy.get_settings", return_value=settings), \
             patch("core.llm_worker.client.generate_text", return_value="{}") as mocked:
            client.complete_json("hello")
        assert mocked.call_args.kwargs["model"] == "fast-x"


class TestRunWithRepair:
    """5系統で85-90%同一だった修復ループの骨格そのものを検証する。"""

    def test_succeeds_on_first_attempt(self):
        client = _StubClient([{"value": "ok"}])
        result = run_with_repair(
            client, "base",
            validate=lambda data: (data, [], []),
            build_repair_prompt=lambda prev, errs: "repair",
            on_repair_failed=lambda errs: {"failed": True, "errors": errs},
        )
        assert result == {"value": "ok"}
        assert client.calls == ["base"]

    def test_succeeds_after_one_repair(self):
        client = _StubClient([{"bad": True}, {"value": "ok"}])

        def validate(data):
            if data.get("bad"):
                return None, ["missing value"], []
            return data, [], []

        result = run_with_repair(
            client, "base",
            validate=validate,
            build_repair_prompt=lambda prev, errs: f"repair: {errs}",
            on_repair_failed=lambda errs: {"failed": True},
        )
        assert result == {"value": "ok"}
        assert len(client.calls) == 2
        assert "repair" in client.calls[1]

    def test_calls_on_repair_failed_after_max_attempts(self):
        client = _StubClient([{"bad": True}] * 10)
        result = run_with_repair(
            client, "base",
            validate=lambda data: (None, ["still bad"], []),
            build_repair_prompt=lambda prev, errs: "repair",
            on_repair_failed=lambda errs: {"failed": True, "errors": list(errs)},
        )
        assert result == {"failed": True, "errors": ["still bad"]}
        # 初回 + MAX_REPAIR_ATTEMPTS 回の再試行
        assert len(client.calls) == 1 + MAX_REPAIR_ATTEMPTS

    def test_json_parse_exception_triggers_repair_not_crash(self):
        class _RaisingThenOkClient:
            def __init__(self):
                self.calls = 0

            def complete_json(self, content):
                self.calls += 1
                if self.calls == 1:
                    raise ValueError("bad json")
                return {"value": "ok"}

        client = _RaisingThenOkClient()
        result = run_with_repair(
            client, "base",
            validate=lambda data: (data, [], []),
            build_repair_prompt=lambda prev, errs: "repair",
            on_repair_failed=lambda errs: None,
        )
        assert result == {"value": "ok"}
        assert client.calls == 2

    def test_on_repair_failed_can_return_none(self):
        """assumption_mining のように失敗時に None を返す契約もそのまま透過する。"""
        client = _StubClient([{"bad": True}] * 10)
        result = run_with_repair(
            client, "base",
            validate=lambda data: (None, ["bad"], []),
            build_repair_prompt=lambda prev, errs: "repair",
            on_repair_failed=lambda errs: None,
        )
        assert result is None


class _StubClient:
    def __init__(self, responses: list[dict]):
        self._responses = list(responses)
        self.calls: list[str] = []

    def complete_json(self, content: str) -> dict:
        self.calls.append(content)
        return self._responses[len(self.calls) - 1]


class TestInMemoryCounterGate:
    def test_allows_up_to_limit_then_blocks(self):
        gate = InMemoryCounterGate()
        assert gate.check_and_count("k", 2) is True
        assert gate.check_and_count("k", 2) is True
        assert gate.check_and_count("k", 2) is False

    def test_keys_are_independent(self):
        gate = InMemoryCounterGate()
        assert gate.check_and_count("a", 1) is True
        assert gate.check_and_count("b", 1) is True
        assert gate.check_and_count("a", 1) is False

    def test_prune_others_drops_stale_keys(self):
        gate = InMemoryCounterGate()
        gate.check_and_count("yesterday", 10)
        assert gate.check_and_count("today", 10, prune_others=True) is True
        assert "yesterday" not in gate.counts

    def test_counts_dict_is_directly_mutable_for_tests(self):
        """既存テスト（例: test_tension_worker.py）が worker._daily_call_counts を
        直接 clear()/代入する互換性を担保する: .counts はコピーではなく参照。"""
        gate = InMemoryCounterGate()
        alias = gate.counts
        alias["k"] = 999
        assert gate.counts["k"] == 999
        alias.clear()
        assert gate.counts == {}


class TestCostGate:
    def test_daily_only_mode(self):
        gate = CostGate()
        assert gate.check_and_count(daily_limit=2, daily_key="d") is True
        assert gate.check_and_count(daily_limit=2, daily_key="d") is True
        assert gate.check_and_count(daily_limit=2, daily_key="d") is False

    def test_session_and_daily_both_enforced(self):
        gate = CostGate()
        # セッション上限1・日次上限10 → 2回目はセッション側で拒否される
        assert gate.check_and_count(
            session_limit=1, session_key="s", daily_limit=10, daily_key="d",
        ) is True
        assert gate.check_and_count(
            session_limit=1, session_key="s", daily_limit=10, daily_key="d",
        ) is False

    def test_daily_limit_blocks_even_under_session_limit(self):
        gate = CostGate()
        assert gate.check_and_count(
            session_limit=10, session_key="s1", daily_limit=1, daily_key="d",
        ) is True
        assert gate.check_and_count(
            session_limit=10, session_key="s2", daily_limit=1, daily_key="d",
        ) is False

    def test_prune_stale_daily_removes_other_day_keys(self):
        gate = CostGate()
        gate.check_and_count(daily_limit=100, daily_key="2026-01-01")
        assert gate.check_and_count(daily_limit=100, daily_key="2026-01-02", prune_stale_daily=True) is True
        assert "2026-01-01" not in gate.daily_counts

    def test_session_counts_and_daily_counts_are_mutable_aliases(self):
        """既存 worker.py の _session_call_counts / _daily_call_counts の直接操作
        （clear() や辞書代入によるテスト向けリセット）と互換であることを担保する。"""
        gate = CostGate()
        session_alias = gate.session_counts
        daily_alias = gate.daily_counts
        gate.check_and_count(session_limit=5, session_key="s", daily_limit=5, daily_key="d")
        assert session_alias == {"s": 1}
        assert daily_alias == {"d": 1}
        session_alias.clear()
        daily_alias.clear()
        assert gate.check_and_count(session_limit=5, session_key="s", daily_limit=5, daily_key="d") is True

    # -----------------------------------------------------------------
    # daily_remaining / count_extra_daily (#499 review P1: figure_reanalysis
    # needs to read the live remaining allowance and post-hoc reconcile
    # actual vision spend without going through check_and_count's gating).
    # -----------------------------------------------------------------

    def test_daily_remaining_reflects_prior_consumption(self):
        gate = CostGate()
        gate.check_and_count(daily_limit=5, daily_key="d")
        gate.check_and_count(daily_limit=5, daily_key="d")
        assert gate.daily_remaining(daily_limit=5, daily_key="d") == 3

    def test_daily_remaining_is_zero_for_unknown_key(self):
        gate = CostGate()
        assert gate.daily_remaining(daily_limit=5, daily_key="never-touched") == 5

    def test_daily_remaining_never_goes_negative(self):
        gate = CostGate()
        gate.daily_counts["d"] = 999
        assert gate.daily_remaining(daily_limit=5, daily_key="d") == 0

    def test_daily_remaining_does_not_advance_the_counter(self):
        gate = CostGate()
        gate.daily_remaining(daily_limit=5, daily_key="d")
        gate.daily_remaining(daily_limit=5, daily_key="d")
        assert gate.daily_counts.get("d", 0) == 0

    def test_count_extra_daily_adds_to_existing_count(self):
        gate = CostGate()
        gate.check_and_count(daily_limit=100, daily_key="d")
        gate.count_extra_daily(daily_key="d", amount=3)
        assert gate.daily_counts["d"] == 4

    def test_count_extra_daily_creates_key_when_absent(self):
        gate = CostGate()
        gate.count_extra_daily(daily_key="new", amount=2)
        assert gate.daily_counts["new"] == 2

    def test_count_extra_daily_zero_or_negative_is_a_no_op(self):
        gate = CostGate()
        gate.count_extra_daily(daily_key="d", amount=0)
        assert "d" not in gate.daily_counts
        gate.count_extra_daily(daily_key="d", amount=-5)
        assert "d" not in gate.daily_counts

    def test_count_extra_daily_can_push_past_the_limit_honestly(self):
        """This is an accounting call, not a gate — it must not clamp to the
        limit (overshoot needs to be visible for the next check_and_count to
        correctly refuse further calls)."""
        gate = CostGate()
        gate.check_and_count(daily_limit=2, daily_key="d")
        gate.count_extra_daily(daily_key="d", amount=10)
        assert gate.daily_counts["d"] == 11
        assert gate.check_and_count(daily_limit=2, daily_key="d") is False


def test_today_str_is_iso_date():
    value = today_str()
    assert len(value) == 10
    assert value[4] == "-" and value[7] == "-"
