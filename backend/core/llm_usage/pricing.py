"""価格表ロード + コスト換算（設計書 §9, U7）。

U7: モデル単価は頻繁に変わるため、ソースコードに USD 単価をハードコードしない。
価格表は ``settings.llm_price_table_path`` の JSON から読む。未設定・不在・パース不能
の場合は None を返す（捏造しない。P4 と同型）。
"""

from __future__ import annotations

import json
import logging

from core.config import get_settings

logger = logging.getLogger(__name__)


def load_price_table() -> dict | None:
    """価格表 JSON をロードする。

    形式（1M トークンあたり USD。設計書 §9）::

        {"gpt-5.2": {"input": 1.25, "cached_input": 0.125, "output": 10.0}, ...}

    パス未設定・ファイル不在・JSON パース失敗はすべて None（例外を投げない）。
    """
    try:
        settings = get_settings()
        path = (settings.llm_price_table_path or "").strip()
        if not path:
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not data:
            return None
        return data
    except Exception:
        logger.debug("llm_usage.pricing: failed to load price table", exc_info=True)
        return None


def _resolve_model_prices(model: str, table: dict) -> dict | None:
    """モデル名を最長前方一致で価格表エントリに解決する。"""
    best_key_len = -1
    best_prices: dict | None = None
    for key, prices in table.items():
        if not isinstance(key, str):
            continue
        if model.startswith(key) and len(key) > best_key_len:
            best_key_len = len(key)
            best_prices = prices
    return best_prices


def compute_cost_usd(
    model: str,
    prompt_tokens: int,
    cached_tokens: int,
    completion_tokens: int,
    table: dict,
) -> float | None:
    """価格表からコスト（USD）を換算する。モデルがマッチしなければ None。

    ``cached_input`` 単価があれば、``cached_tokens`` 分を通常の input 単価から差し引き、
    別単価で計算する（同じ prompt_tokens に含まれる内訳のため二重計上しない）。
    """
    if not table or not model:
        return None

    prices = _resolve_model_prices(model, table)
    if not prices:
        return None

    prompt_tokens = max(0, int(prompt_tokens or 0))
    completion_tokens = max(0, int(completion_tokens or 0))
    cached_tokens = max(0, min(int(cached_tokens or 0), prompt_tokens))
    uncached_prompt_tokens = prompt_tokens - cached_tokens

    input_rate = prices.get("input")
    cached_rate = prices.get("cached_input")
    output_rate = prices.get("output")

    cost = 0.0
    rate_used = False

    if uncached_prompt_tokens:
        if input_rate is None:
            return None
        cost += (uncached_prompt_tokens / 1_000_000.0) * float(input_rate)
        rate_used = True

    if cached_tokens:
        if cached_rate is not None:
            cost += (cached_tokens / 1_000_000.0) * float(cached_rate)
            rate_used = True
        elif input_rate is not None:
            # cached 専用単価が無ければ通常単価で代用する（過大方向の安全側丸め。捏造ではない）
            cost += (cached_tokens / 1_000_000.0) * float(input_rate)
            rate_used = True
        else:
            return None

    if completion_tokens:
        if output_rate is None:
            return None
        cost += (completion_tokens / 1_000_000.0) * float(output_rate)
        rate_used = True

    if not rate_used:
        # トークンが全て 0 件など、換算対象が無い場合は 0.0 を返す（None は「不明」の意味に予約）
        return 0.0

    return cost
