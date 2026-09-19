"""同期 API の日次コスト上限を消費する共通ヘルパー。

チャット型・単発 AI の各ルートに、同じ4行が個別に書かれていた
（設定から上限を読む → ``CostGate.check_and_count(daily_limit=..., daily_key=
(today_str(), user_id))`` → 超過なら 429 + 事実文）。本モジュールはその
**消費と 429 への写像**だけを集約する。

各ルートに残るもの:

- ``CostGate`` インスタンス（どこまでを1カウンタとして共有するかは層の設計。
  例: 原稿スタジオ rewrite は scripts/topics の2経路で1インスタンスを共有する）
- 上限値の設定キーと既定値
- 429 の事実文（層ごとに文言が違う。**数値は載せない**＝I2）
- 呼び出し位置（権限ゲートの後・LLM 呼び出しの前。拒否された要求を数えない）

正本: docs/features/assistant_common_infra_design.md §1。
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from core.llm_worker.cost_gate import today_str


def consume_daily_quota(
    gate: Any,
    *,
    user_id: str,
    limit: int,
    message: str,
    quota_state: dict | None = None,
) -> None:
    """日次上限を1消費する。超過時は ``HTTPException(429, message)``。

    Parameters
    ----------
    gate:
        層が保持する ``CostGate``（``check_and_count(daily_limit=, daily_key=)``）。
    quota_state:
        リクエストスコープの mutable dict。渡された場合、同一リクエスト内で
        複数回呼ばれても消費は**1回だけ**（学習チャットのように intent 分類〜本体
        まで含めて1と数える経路のための重複防止）。
    """
    if quota_state is not None:
        if quota_state.get("consumed"):
            return
        quota_state["consumed"] = True
    if gate.check_and_count(daily_limit=limit, daily_key=(today_str(), user_id)):
        return
    raise HTTPException(status_code=429, detail=message)


__all__ = ["consume_daily_quota"]
