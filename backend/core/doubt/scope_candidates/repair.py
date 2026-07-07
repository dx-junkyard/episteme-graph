"""検証スコープ候補 — validation 失敗時の修復再試行（D1-4, tension と同方式）。

2回失敗しても対象行は破棄せず、repair_failed=True の空結果を返す。
worker は scope_candidates_analyzed_at を打った上で候補 0 件のまま保持する（P4）。
"""

from __future__ import annotations

import json
import logging

from core.doubt.scope_candidates.prompt import build_repair_prompt
from core.doubt.scope_candidates.schema import ScopeCandidateResult, ScopeTargetContext
from core.doubt.scope_candidates.validator import validate_output

logger = logging.getLogger(__name__)

MAX_REPAIR_ATTEMPTS = 2


def run_with_repair(
    llm_client,
    base_content: str,
    context: ScopeTargetContext,
) -> ScopeCandidateResult:
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
            logger.warning("scope candidate LLM attempt %d failed to parse: %s", attempt + 1, exc)
            continue
        previous_raw = json.dumps(data, ensure_ascii=False)
        result, errors, _warnings = validate_output(data, context)
        if result is not None:
            return result
        logger.info("scope candidate attempt %d failed validation: %s", attempt + 1, errors)

    logger.warning(
        "scope candidate repair failed after %d attempts: %s", 1 + MAX_REPAIR_ATTEMPTS, errors
    )
    return ScopeCandidateResult(
        target_id=context.target_id,
        target_type=context.target_type,
        repair_failed=True,
        warnings=[f"repair_failed: {e}" for e in errors],
    )
