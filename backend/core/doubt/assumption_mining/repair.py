"""暗黙前提の正規化 — validation 失敗時の修復再試行（D2-2, tension と同方式）。

2回失敗しても検出クラスタ行は保持され、呼び出し側（worker）が
created_from.normalization_failed=true を付けて placeholder 文のまま残す（P4）。
"""

from __future__ import annotations

import json
import logging

from core.doubt.assumption_mining.prompt import build_repair_prompt
from core.doubt.assumption_mining.validator import validate_output

logger = logging.getLogger(__name__)

MAX_REPAIR_ATTEMPTS = 2


def run_with_repair(
    llm_client,
    base_content: str,
    source_texts: list[str],
) -> dict | None:
    """成功時は正規化 dict、全試行失敗時は None（呼び出し側が P4 で保持）。"""
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
            logger.warning("assumption normalization attempt %d parse failed: %s", attempt + 1, exc)
            continue
        previous_raw = json.dumps(data, ensure_ascii=False)
        result, errors, _warnings = validate_output(data, source_texts)
        if result is not None:
            return result
        logger.info("assumption normalization attempt %d failed validation: %s", attempt + 1, errors)

    logger.warning(
        "assumption normalization repair failed after %d attempts: %s",
        1 + MAX_REPAIR_ATTEMPTS, errors,
    )
    return None
