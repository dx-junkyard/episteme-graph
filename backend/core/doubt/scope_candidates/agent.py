"""ScopeCandidateAgent — 検証スコープ候補抽出の本体（D1-4）。

LLM 呼び出しは llm_client に分離。出力は validator でスキーマ検証し、
失敗時は repair（最大2回）を実行する。出典が無い対象は LLM を呼ばず空結果。
"""

from __future__ import annotations

import logging

from core.doubt.scope_candidates.llm_client import ScopeCandidateLLMClient
from core.doubt.scope_candidates.prompt import build_content
from core.doubt.scope_candidates.repair import run_with_repair
from core.doubt.scope_candidates.schema import ScopeCandidateResult, ScopeTargetContext

logger = logging.getLogger(__name__)


class ScopeCandidateAgent:
    def __init__(self, llm_client: ScopeCandidateLLMClient | None = None):
        self._llm_client = llm_client or ScopeCandidateLLMClient()

    def run(self, context: ScopeTargetContext) -> ScopeCandidateResult:
        if not context.has_sources():
            # 出典が無いなら候補も作らない（無から候補をひねり出させない）
            return ScopeCandidateResult(
                target_id=context.target_id,
                target_type=context.target_type,
                warnings=["no source texts; skipped"],
            )
        content = build_content(context)
        return run_with_repair(self._llm_client, content, context)
