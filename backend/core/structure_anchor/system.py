"""structure_anchor（構造帰属型の問い記録）の WorkerSystem 宣言（設定キー・feature・上限既定の正本）。

骨格の組み方は core/llm_worker/system.py。ドメイン語彙はこちら側にだけ置く。
"""

from __future__ import annotations

from core.llm_worker.system import CostSpec, WorkerSystem

SYSTEM = WorkerSystem(
    name="structure-anchor",
    model_setting_key="anchor_llm_model",
    feature="learning:structure_anchor",
    log_label="anchor mining",
    cost=CostSpec(
        day_setting="anchor_max_calls_per_day",
        day_default=10,
        session_setting="anchor_max_calls_per_session",
        session_default=3,
        # daily_key に user_id を含むため他ユーザーのカウンタを消さない（prune しない）。
        prune_stale_daily=False,
    ),
)

__all__ = ["SYSTEM"]
