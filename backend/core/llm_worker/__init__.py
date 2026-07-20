"""LLM worker 共通基盤 — 非同期 LLM ワーカー系統の骨格の共通実装。

tension / structure_anchor / reconstruction / doubt.scope_candidates /
doubt.assumption_mining の5系統は、いずれも「LLM 1コール呼び出し →
validation失敗時の修復再試行（最大2回）→ in-memory コスト上限ゲート」という
同型の骨格を持つ（正本: docs/architecture/consolidation_survey_2026-07.md
Tier2 提案6）。このパッケージはその骨格だけを集約する。

ドメイン固有のロジック（会話窓構築・claim 選定・台帳ロック・冪等性フラグの
持ち場・DB書き込み・トリガー条件）は各モジュール（core/tension/ 等）に残る。
このパッケージは FastAPI は import しない。

- ``client.py``: ``BaseJSONLLMClient`` / ``resolve_model`` / ``parse_json_response``
- ``repair.py``: ``run_with_repair`` (validate/build_repair_prompt/on_repair_failed を注入)
- ``cost_gate.py``: ``CostGate`` (session+daily) / ``InMemoryCounterGate`` (単一カウンタ)
- ``history.py``: ``window_history`` (チャット型4者の会話履歴ウィンドウ化。
  正本: docs/features/assistant_common_infra_design.md §2)
"""

from __future__ import annotations

from core.llm_worker.client import BaseJSONLLMClient, parse_json_response, resolve_model
from core.llm_worker.cost_gate import CostGate, InMemoryCounterGate, today_str
from core.llm_worker.history import window_history
from core.llm_worker.repair import MAX_REPAIR_ATTEMPTS, run_with_repair

__all__ = [
    "BaseJSONLLMClient",
    "parse_json_response",
    "resolve_model",
    "CostGate",
    "InMemoryCounterGate",
    "today_str",
    "MAX_REPAIR_ATTEMPTS",
    "run_with_repair",
    "window_history",
]
