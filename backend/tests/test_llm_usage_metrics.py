"""core/llm_usage/metrics.py — collect_metrics() の集計テスト。

観点: reported/estimated の分離集計 / group_by ホワイトリスト（不正値は ValueError、
SQL に生の group_by 文字列が連結されないこと）/ cost_usd（価格表あり・なし）/
dropped_events の連携。

DB セッションは fake（固定行を返す）に差し替える。
"""

from __future__ import annotations

import pytest

import core.llm_usage.metrics as metrics


class _FakeQueryResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows
        self.executed: list[tuple[str, dict]] = []

    def execute(self, stmt, params=None):
        self.executed.append((str(stmt), params))
        return _FakeQueryResult(self._rows)


# raw row shape for group_by=["feature"]:
# (feature, model, usage_source, sum_prompt, sum_completion, sum_total, sum_cached, calls)
_ROWS_FOR_FEATURE_GROUPING = [
    ("feat-a", "gpt-4o", "reported", 1000, 200, 1200, 100, 5),
    ("feat-a", "gpt-4o", "estimated_heuristic", 50, 10, 60, 0, 1),
    ("feat-b", "gpt-4o-mini", "reported", 300, 50, 350, 0, 2),
]


@pytest.fixture(autouse=True)
def _no_price_table_by_default(monkeypatch):
    monkeypatch.setattr(metrics.pricing, "load_price_table", lambda: None)
    monkeypatch.setattr(metrics, "dropped_count", lambda: 0)
    yield


class TestReportedEstimatedSeparation:
    def test_reported_and_estimated_are_separated_per_group(self):
        session = _FakeSession(_ROWS_FOR_FEATURE_GROUPING)
        result = metrics.collect_metrics(
            session, date_from="2026-07-01", date_to="2026-07-12", group_by=["feature"]
        )
        rows_by_key = {tuple(r["key"].items()): r for r in result["rows"]}

        feat_a = rows_by_key[(("feature", "feat-a"),)]
        assert feat_a["reported"] == {
            "prompt_tokens": 1000,
            "completion_tokens": 200,
            "total_tokens": 1200,
            "calls": 5,
        }
        assert feat_a["estimated"] == {
            "prompt_tokens": 50,
            "completion_tokens": 10,
            "total_tokens": 60,
            "calls": 1,
        }

        feat_b = rows_by_key[(("feature", "feat-b"),)]
        assert feat_b["reported"]["calls"] == 2
        assert feat_b["estimated"] == {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "calls": 0,
        }

    def test_no_single_merged_field_exists(self):
        session = _FakeSession(_ROWS_FOR_FEATURE_GROUPING)
        result = metrics.collect_metrics(
            session, date_from="2026-07-01", date_to="2026-07-12", group_by=["feature"]
        )
        for row in result["rows"]:
            assert "prompt_tokens" not in row
            assert "total_tokens" not in row
            assert set(row.keys()) == {"key", "reported", "estimated", "cost_usd"}

    def test_dropped_events_and_price_table_flag(self, monkeypatch):
        monkeypatch.setattr(metrics, "dropped_count", lambda: 7)
        session = _FakeSession(_ROWS_FOR_FEATURE_GROUPING)
        result = metrics.collect_metrics(
            session, date_from="2026-07-01", date_to="2026-07-12", group_by=["feature"]
        )
        assert result["dropped_events"] == 7
        assert result["price_table_loaded"] is False
        assert all(r["cost_usd"] is None for r in result["rows"])


class TestCostComputation:
    def test_cost_usd_computed_when_price_table_loaded(self, monkeypatch):
        table = {
            "gpt-4o": {"input": 2.0, "output": 8.0},
            "gpt-4o-mini": {"input": 1.0, "output": 4.0},
        }
        monkeypatch.setattr(metrics.pricing, "load_price_table", lambda: table)

        session = _FakeSession(_ROWS_FOR_FEATURE_GROUPING)
        result = metrics.collect_metrics(
            session, date_from="2026-07-01", date_to="2026-07-12", group_by=["feature"]
        )
        rows_by_key = {tuple(r["key"].items()): r for r in result["rows"]}

        # feat-a: row1(reported) cost = (900/1e6*2.0)+(100/1e6*2.0)+(200/1e6*8.0) = 0.0036
        #         row2(estimated) cost = (50/1e6*2.0)+(10/1e6*8.0) = 0.00018
        assert rows_by_key[(("feature", "feat-a"),)]["cost_usd"] == pytest.approx(0.00378)

        # feat-b: (300/1e6*1.0)+(50/1e6*4.0) = 0.0005
        assert rows_by_key[(("feature", "feat-b"),)]["cost_usd"] == pytest.approx(0.0005)
        assert result["price_table_loaded"] is True

    def test_cost_usd_none_when_model_unmatched(self, monkeypatch):
        table = {"some-other-model": {"input": 1.0, "output": 1.0}}
        monkeypatch.setattr(metrics.pricing, "load_price_table", lambda: table)

        session = _FakeSession(_ROWS_FOR_FEATURE_GROUPING)
        result = metrics.collect_metrics(
            session, date_from="2026-07-01", date_to="2026-07-12", group_by=["feature"]
        )
        # 価格表はロードされているが、どのモデルもマッチしないので None のまま
        assert result["price_table_loaded"] is True
        assert all(r["cost_usd"] is None for r in result["rows"])


class TestGroupByWhitelist:
    def test_invalid_group_by_raises_value_error(self):
        session = _FakeSession([])
        with pytest.raises(ValueError):
            metrics.collect_metrics(
                session, date_from="2026-07-01", date_to="2026-07-12", group_by=["bogus_field"]
            )
        # 不正な group_by ではクエリを実行しない
        assert session.executed == []

    def test_empty_group_by_raises_value_error(self):
        session = _FakeSession([])
        with pytest.raises(ValueError):
            metrics.collect_metrics(
                session, date_from="2026-07-01", date_to="2026-07-12", group_by=[]
            )

    def test_sql_injection_attempt_is_rejected_not_concatenated(self):
        session = _FakeSession([])
        malicious = "feature; DROP TABLE llm_usage_events;--"
        with pytest.raises(ValueError):
            metrics.collect_metrics(
                session, date_from="2026-07-01", date_to="2026-07-12", group_by=[malicious]
            )
        assert session.executed == []

    def test_valid_multi_group_by_is_accepted(self):
        rows = [
            ("2026-07-01", "feat-a", "gpt-4o", "reported", 10, 5, 15, 0, 1),
        ]
        session = _FakeSession(rows)
        result = metrics.collect_metrics(
            session, date_from="2026-07-01", date_to="2026-07-12", group_by=["day", "feature"]
        )
        assert len(result["rows"]) == 1
        assert set(result["rows"][0]["key"].keys()) == {"day", "feature"}

    def test_group_by_values_only_come_from_whitelist_dict(self):
        # SQL に現れる GROUP BY 対象は _GROUP_BY_SQL にある固定文字列のみであることを確認する
        session = _FakeSession(_ROWS_FOR_FEATURE_GROUPING)
        metrics.collect_metrics(
            session, date_from="2026-07-01", date_to="2026-07-12", group_by=["feature", "model"]
        )
        sql_text = session.executed[0][0]
        assert "DROP" not in sql_text.upper()
        assert "feature" in sql_text and "model" in sql_text
