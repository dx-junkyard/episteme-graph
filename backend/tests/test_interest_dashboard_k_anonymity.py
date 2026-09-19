"""関心集約ダッシュボード（`services.aggregate_interest_dashboard`）の k-匿名ゲート。

正本: vision §6 原則5（教員へ渡るのは k-匿名集約のみ）/ core/privacy.py の
`K_ANONYMITY` / 制度指標カタログ `interest-dashboard` の `k_anonymity=True` 宣言。

2026-09-05 のビジョン監査（C3）で、ヒートマップ2表には k ゲートがある一方、
`hotspots`（トピック × 件数）・`cohort_size`・`unfinished_summary` が無ゲートの
生整数で返っていた（受講者1名のコースでもトピック名と件数が出る）ことが見つかった。
本テストはフェイク session で SQL 結果を与え、ゲートの存在を固定する。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

import services  # noqa: E402
from core.privacy import K_ANONYMITY  # noqa: E402


class _Result:
    def __init__(self, rows=None, scalar=None):
        self._rows = list(rows or [])
        self._scalar = scalar

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        return self._scalar


class _SequenceSession:
    """execute 呼び出し順に結果を返すフェイク（cohort → rows → summary → tension → anchor）。"""

    def __init__(self, results):
        self._results = list(results)
        self.calls = 0

    def execute(self, _stmt, _params=None):
        self.calls += 1
        return self._results.pop(0)

    def close(self):
        pass


def _run(monkeypatch, *, cohort, rows, summary=(4, 1, 2)):
    session = _SequenceSession([
        _Result(scalar=cohort),
        _Result(rows=rows),
        _Result(rows=[summary]),
        _Result(rows=[]),   # tension heatmap
        _Result(rows=[]),   # anchor heatmap
    ])
    monkeypatch.setattr(services, "_pg_session", lambda: session)
    return services.aggregate_interest_dashboard("course-1", {"t1": "トピック1", "t2": "トピック2"})


def test_small_cohort_suppresses_the_whole_dashboard(monkeypatch):
    result = _run(
        monkeypatch,
        cohort=K_ANONYMITY - 1,
        rows=[("t1", 5, 2, K_ANONYMITY - 1)],
    )
    assert result["k_anonymity_suppressed"] is True
    assert result["hotspots"] == []
    assert result["cohort_size"] == 0
    assert result["unfinished_summary"] == {
        "open_questions": 0, "repeated_detours": 0, "recurring_misconceptions": 0,
    }


def test_hotspot_rows_below_k_are_dropped_even_when_cohort_is_large(monkeypatch):
    result = _run(
        monkeypatch,
        cohort=K_ANONYMITY + 5,
        rows=[("t1", 9, 3, K_ANONYMITY), ("t2", 4, 1, K_ANONYMITY - 1)],
    )
    assert "k_anonymity_suppressed" not in result
    titles = [h["topic_title"] for h in result["hotspots"]]
    assert titles == ["トピック1"], "関与人数 n<k のトピック行が hotspots に残っている"
    assert result["cohort_size"] == K_ANONYMITY + 5


def test_gate_threshold_is_the_privacy_module_constant():
    """k=3 をリテラルで再定義しない（core/privacy.py が正本）。"""
    from tests.guardrail_helpers import extract_function_source
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "api" / "services.py").read_text(encoding="utf-8")
    body = extract_function_source(src, "aggregate_interest_dashboard")
    assert body.count("< K_ANONYMITY") >= 4, (
        "aggregate_interest_dashboard の k ゲート（cohort / hotspots / tension / anchor）"
        "が K_ANONYMITY 比較として4箇所以上現れない"
    )
    # k のリテラル再定義（`learners < 3` 等）が無いこと。コメント中の「n<3」は対象外。
    import re
    assert not re.search(r"(learners|cohort)\)?\s*<\s*3\b", body), "k=3 をリテラルで比較している"
