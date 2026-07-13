"""core/privacy.py のガードレール（調査レポート Tier2 提案8: k-匿名ゲートの共通化）。

- core/privacy.py は FastAPI / DB クライアントを import しない（project rule 2 と同型。
  純粋関数のみで完結する）
- K_ANONYMITY == 3（既存4箇所すべてが前提にしていた閾値と一致）
- レンジ境界ロジック（<=5 / <=10 / それ以外）が既存4箇所と同じ
- 4箇所（core/reconstruction/health.py, core/doubt/schema.py, api/services.py の
  tension/anchor 集計）がこの共通実装に委譲している（import 経路の確認）
- health.py の再エクスポート（K_ANON / count_range / gate_by_users / rate_level）が
  core/reconstruction/stumble.py の import を壊していない
"""

from __future__ import annotations

from pathlib import Path

from core.privacy import (
    K_ANONYMITY,
    bucket_count_range,
    count_range_gated,
    gate_label,
    meets_k_anonymity,
)
from tests.guardrail_helpers import assert_source_does_not_import

_BACKEND = Path(__file__).resolve().parents[1]
_PRIVACY_SRC = (_BACKEND / "core" / "privacy.py").read_text(encoding="utf-8")


class TestNoFastAPIOrDBDependency:
    def test_privacy_module_has_no_external_dependencies(self):
        assert_source_does_not_import(
            _PRIVACY_SRC, ["fastapi", "sqlalchemy", "core.postgres"], context="core/privacy.py",
        )

    def test_privacy_module_is_pure(self):
        """DB セッションを一切作らない（純関数のみ）。"""
        assert "get_session" not in _PRIVACY_SRC
        assert "def " in _PRIVACY_SRC


class TestThresholdAndRangeBoundaries:
    def test_threshold_is_three(self):
        assert K_ANONYMITY == 3

    def test_meets_k_anonymity(self):
        assert meets_k_anonymity(2) is False
        assert meets_k_anonymity(3) is True
        assert meets_k_anonymity(100) is True

    def test_bucket_boundaries_match_existing_four_implementations(self):
        # 境界: <=5 → "3-5", <=10 → "6-10", それ以外 → "11+"
        assert bucket_count_range(3) == "3-5"
        assert bucket_count_range(5) == "3-5"
        assert bucket_count_range(6) == "6-10"
        assert bucket_count_range(10) == "6-10"
        assert bucket_count_range(11) == "11+"
        assert bucket_count_range(1000) == "11+"

    def test_count_range_gated_below_threshold_uses_caller_label(self):
        assert count_range_gated(2, below_label="") == ""
        assert count_range_gated(2, below_label="まだデータなし") == "まだデータなし"
        assert count_range_gated(3, below_label="") == "3-5"

    def test_gate_label_below_threshold_uses_caller_label(self):
        assert gate_label("高", 2, below_label="まだデータなし") == "まだデータなし"
        assert gate_label("高", 3, below_label="まだデータなし") == "高"


class TestFourSitesDelegateToCommonImplementation:
    """提案8: 4箇所が core/privacy.py の共通ゲートに委譲している。

    各所固有のレスポンス形式（レンジ下限表記・非該当時の文言・キー名）は
    1つも変えていない — ここで検証するのは閾値とレンジ境界ロジックの委譲のみ。
    """

    def test_reconstruction_health_delegates(self):
        from core.reconstruction import health

        assert health.K_ANON == K_ANONYMITY
        # 既存の挙動（下回ったら「まだデータなし」）は不変
        assert health.count_range(2) == "まだデータなし"
        assert health.count_range(4) == "3-5"
        assert health.gate_by_users("高", 2) == "まだデータなし"
        assert health.gate_by_users("高", 5) == "高"

    def test_reconstruction_stumble_imports_still_work(self):
        """health.py からの再エクスポートで import 面を壊さない。"""
        from core.reconstruction.stumble import K_ANON, count_range, gate_by_users, rate_level

        assert K_ANON == K_ANONYMITY
        assert callable(count_range) and callable(gate_by_users) and callable(rate_level)

    def test_doubt_naive_signal_delegates(self):
        from core.doubt.schema import NAIVE_SIGNAL_K_ANONYMITY, count_range_label

        assert NAIVE_SIGNAL_K_ANONYMITY == K_ANONYMITY
        # 既存の挙動（下回ったら空文字）は不変
        assert count_range_label(2) == ""
        assert count_range_label(4) == "3-5"
        assert count_range_label(11) == "11+"

    def test_services_tension_anchor_heatmap_delegates(self):
        src = (_BACKEND / "api" / "services.py").read_text(encoding="utf-8")
        assert "from core.privacy import K_ANONYMITY" in src
        assert "_TENSION_K_ANONYMITY" not in src
        assert "_ANCHOR_K_ANONYMITY" not in src
        body = src.split("def aggregate_interest_dashboard")[1].split("\ndef ")[0]
        assert body.count("learners < K_ANONYMITY") == 2
