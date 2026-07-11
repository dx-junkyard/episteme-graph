"""core/llm_usage/pricing.py — 価格表ロード + コスト換算のテスト。

観点: 最長前方一致 / テーブル未設定・パース不能時は None / cached_input 差引 /
tmp_path に書いた JSON からの load_price_table 検証。ソースコードに USD 単価は書かない
（テスト内で価格表を都度作る）。
"""

from __future__ import annotations

import json

import pytest

import core.llm_usage.pricing as pricing


class _FakeSettings:
    def __init__(self, llm_price_table_path=""):
        self.llm_price_table_path = llm_price_table_path


class TestLoadPriceTable:
    def test_empty_path_returns_none(self, monkeypatch):
        monkeypatch.setattr(pricing, "get_settings", lambda: _FakeSettings(llm_price_table_path=""))
        assert pricing.load_price_table() is None

    def test_missing_file_returns_none(self, monkeypatch, tmp_path):
        missing_path = tmp_path / "does_not_exist.json"
        monkeypatch.setattr(
            pricing, "get_settings", lambda: _FakeSettings(llm_price_table_path=str(missing_path))
        )
        assert pricing.load_price_table() is None

    def test_invalid_json_returns_none(self, monkeypatch, tmp_path):
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("{not valid json", encoding="utf-8")
        monkeypatch.setattr(
            pricing, "get_settings", lambda: _FakeSettings(llm_price_table_path=str(bad_file))
        )
        assert pricing.load_price_table() is None

    def test_non_dict_json_returns_none(self, monkeypatch, tmp_path):
        list_file = tmp_path / "list.json"
        list_file.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        monkeypatch.setattr(
            pricing, "get_settings", lambda: _FakeSettings(llm_price_table_path=str(list_file))
        )
        assert pricing.load_price_table() is None

    def test_empty_dict_json_returns_none(self, monkeypatch, tmp_path):
        empty_file = tmp_path / "empty.json"
        empty_file.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(
            pricing, "get_settings", lambda: _FakeSettings(llm_price_table_path=str(empty_file))
        )
        assert pricing.load_price_table() is None

    def test_valid_json_is_loaded(self, monkeypatch, tmp_path):
        table = {"some-model": {"input": 1.0, "output": 2.0}}
        price_file = tmp_path / "prices.json"
        price_file.write_text(json.dumps(table), encoding="utf-8")
        monkeypatch.setattr(
            pricing, "get_settings", lambda: _FakeSettings(llm_price_table_path=str(price_file))
        )
        loaded = pricing.load_price_table()
        assert loaded == table


class TestComputeCostUsd:
    def test_table_none_returns_none(self):
        assert pricing.compute_cost_usd("gpt-4o", 100, 0, 50, None) is None  # type: ignore[arg-type]

    def test_model_not_matched_returns_none(self):
        table = {"gpt-4o": {"input": 1.0, "output": 2.0}}
        assert pricing.compute_cost_usd("claude-x", 100, 0, 50, table) is None

    def test_longest_prefix_match_wins(self):
        table = {
            "gpt-5.4": {"input": 1.0, "output": 2.0},
            "gpt-5.4-nano": {"input": 0.05, "output": 0.4},
        }
        # "gpt-5.4-nano-2026-01-01" は両方に前方一致するが、より長いキーが勝つ
        cost = pricing.compute_cost_usd("gpt-5.4-nano-2026-01-01", 1_000_000, 0, 1_000_000, table)
        expected = (1_000_000 / 1_000_000.0) * 0.05 + (1_000_000 / 1_000_000.0) * 0.4
        assert cost == pytest.approx(expected)

        cost_base = pricing.compute_cost_usd("gpt-5.4-turbo", 1_000_000, 0, 1_000_000, table)
        expected_base = (1_000_000 / 1_000_000.0) * 1.0 + (1_000_000 / 1_000_000.0) * 2.0
        assert cost_base == pytest.approx(expected_base)

    def test_cached_input_rate_is_deducted_separately(self):
        table = {"gpt-4o": {"input": 2.0, "cached_input": 1.0, "output": 8.0}}
        cost = pricing.compute_cost_usd(
            "gpt-4o", prompt_tokens=1000, cached_tokens=400, completion_tokens=100, table=table
        )
        expected = (600 / 1_000_000.0) * 2.0 + (400 / 1_000_000.0) * 1.0 + (100 / 1_000_000.0) * 8.0
        assert cost == pytest.approx(expected)

    def test_cached_tokens_fall_back_to_input_rate_when_no_cached_rate(self):
        table = {"gpt-4o": {"input": 2.0, "output": 8.0}}
        cost = pricing.compute_cost_usd(
            "gpt-4o", prompt_tokens=1000, cached_tokens=400, completion_tokens=0, table=table
        )
        # cached_input が無いので全 prompt_tokens を input 単価で計算するのと同じになる
        expected = (1000 / 1_000_000.0) * 2.0
        assert cost == pytest.approx(expected)

    def test_missing_output_rate_returns_none_when_completion_tokens_present(self):
        table = {"gpt-4o": {"input": 2.0}}
        assert pricing.compute_cost_usd("gpt-4o", 1000, 0, 50, table) is None

    def test_zero_tokens_returns_zero_cost(self):
        table = {"gpt-4o": {"input": 2.0, "output": 8.0}}
        assert pricing.compute_cost_usd("gpt-4o", 0, 0, 0, table) == 0.0

    def test_cached_tokens_capped_at_prompt_tokens(self):
        table = {"gpt-4o": {"input": 2.0, "cached_input": 1.0, "output": 8.0}}
        # cached_tokens > prompt_tokens という異常値でもクラッシュしない（cap される）
        cost = pricing.compute_cost_usd(
            "gpt-4o", prompt_tokens=100, cached_tokens=500, completion_tokens=0, table=table
        )
        expected = (100 / 1_000_000.0) * 1.0  # 全量 cached 扱いになる
        assert cost == pytest.approx(expected)
