"""Stage 1 — validation 失敗時の修復再試行（設計書 §7、A層と同方式）。

validation 失敗時、元出力＋エラーリストを添えて最大2回再試行する。
2回失敗したらバッチは破棄せず、呼び出し側（agent.py）が repair_failed=True の
結果を返し、worker が unclassified の1行として保存する（P4: 情報を落とさない）。
"""

from __future__ import annotations

import json
import logging

from core.tension.prompt import build_repair_prompt
from core.tension.schema import ConversationWindow, TensionMiningResult
from core.tension.validator import validate_output

logger = logging.getLogger(__name__)

MAX_REPAIR_ATTEMPTS = 2


def run_with_repair(
    llm_client,
    base_content: str,
    window: ConversationWindow,
    max_candidates: int,
) -> TensionMiningResult:
    """LLM 呼び出し → 検証 → 失敗なら修復再試行（最大 MAX_REPAIR_ATTEMPTS 回）。

    Returns
    -------
    TensionMiningResult
        成功時は検証済み結果。全試行失敗時は repair_failed=True の空結果。
    """
    previous_raw = ""
    errors: list[str] = []
    for attempt in range(1 + MAX_REPAIR_ATTEMPTS):
        if attempt == 0:
            content = base_content
        else:
            content = base_content + "\n\n" + build_repair_prompt(previous_raw, errors)
        try:
            data = llm_client.complete_json(content)
        except Exception as exc:
            errors = [f"output was not valid JSON: {exc}"]
            previous_raw = ""
            logger.warning("tension mining LLM attempt %d failed to parse: %s", attempt + 1, exc)
            continue
        previous_raw = json.dumps(data, ensure_ascii=False)
        result, errors, _warnings = validate_output(data, window, max_candidates)
        if result is not None:
            return result
        logger.info("tension mining attempt %d failed validation: %s", attempt + 1, errors)

    logger.warning("tension mining repair failed after %d attempts: %s", 1 + MAX_REPAIR_ATTEMPTS, errors)
    return TensionMiningResult(repair_failed=True, warnings=[f"repair_failed: {e}" for e in errors])
