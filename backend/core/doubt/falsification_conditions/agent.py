"""FalsificationConditionAgent — 反証条件候補抽出の本体（SL-1）。

LLM 呼び出しは llm_client に分離。出力は validator でスキーマ検証し、
失敗時は repair（最大2回）を実行する。出典が無い対象は LLM を呼ばず空結果
（無から候補をひねり出させない, scope_candidates と同じ規約）。
"""

from __future__ import annotations

import logging

from core.doubt.falsification_conditions.llm_client import FalsificationConditionLLMClient
from core.doubt.falsification_conditions.prompt import build_content
from core.doubt.falsification_conditions.repair import run_with_repair
from core.doubt.falsification_conditions.schema import (
    FalsificationCandidateResult,
    FalsificationTargetContext,
)

logger = logging.getLogger(__name__)


class FalsificationConditionAgent:
    def __init__(self, llm_client: FalsificationConditionLLMClient | None = None):
        self._llm_client = llm_client or FalsificationConditionLLMClient()

    def run(self, context: FalsificationTargetContext) -> FalsificationCandidateResult:
        if not context.has_sources():
            return FalsificationCandidateResult(
                target_id=context.target_id,
                target_type=context.target_type,
                warnings=["no source texts; skipped"],
            )
        content = build_content(context)
        return run_with_repair(self._llm_client, content, context)
