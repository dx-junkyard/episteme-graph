"""item オーサリング validation 失敗時の修復再試行（§4.2、A層と同方式）。

validation 失敗時、元出力 + エラーリストを添えて最大2回再試行する。
2回失敗したら item を生成しない（=配信しない）。呼び出し側（agent.py）は
repair_failed=True の結果を返し、worker はその claim をスキップする。
"""

from __future__ import annotations

import json
import logging

from core.reconstruction.prompt import build_repair_prompt
from core.reconstruction.schema import ItemAuthoringResult
from core.reconstruction.validator import validate_output

logger = logging.getLogger(__name__)

MAX_REPAIR_ATTEMPTS = 2


def run_with_repair(llm_client, base_content: str, claim: dict) -> ItemAuthoringResult:
    """LLM 呼び出し → 検証 → 失敗なら修復再試行（最大 MAX_REPAIR_ATTEMPTS 回）。"""
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
            logger.warning("recon item authoring attempt %d failed to parse: %s", attempt + 1, exc)
            continue
        previous_raw = json.dumps(data, ensure_ascii=False)
        result, errors, _warnings = validate_output(data, claim)
        if result is not None:
            return result
        logger.info("recon item authoring attempt %d failed validation: %s", attempt + 1, errors)

    logger.warning("recon item authoring repair failed after %d attempts: %s", 1 + MAX_REPAIR_ATTEMPTS, errors)
    return ItemAuthoringResult(repair_failed=True, warnings=[f"repair_failed: {e}" for e in errors])
