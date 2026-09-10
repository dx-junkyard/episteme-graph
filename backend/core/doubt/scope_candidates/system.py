"""検証スコープ候補（D1-4）の WorkerSystem 宣言（設定キー・feature・上限既定の正本）。

骨格の組み方は core/llm_worker/system.py。ドメイン語彙はこちら側にだけ置く。
"""

from __future__ import annotations

from core.llm_worker.system import CostSpec, WorkerSystem

SYSTEM = WorkerSystem(
    name="doubt-scope-candidates",
    model_setting_key="doubt_scope_llm_model",
    feature="doubt:scope_candidates",
    log_label="scope candidate",
    cost=CostSpec(
        day_setting="doubt_scope_max_calls_per_day",
        day_default=10,
        # daily_key は日付のみ。過去日のカウンタは破棄する（常駐プロセスのメモリリーク防止）。
        prune_stale_daily=True,
    ),
)

__all__ = ["SYSTEM"]
