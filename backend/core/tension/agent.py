"""TensionMiningAgent 本体（Stage 1、run() を公開インターフェースとする）。

会話窓 + アンカー + tier を入力に、TensionCandidate[] を JSON（validator/repair 付き）で返す。
LLM 出力は常に候補（status='candidate'）であり、確定は学習者本人の操作のみ（P1）。
永続化は行わない（worker.py の責務）。
"""

from __future__ import annotations

import logging

from core.tension.input_builder import build_user_content
from core.tension.llm_client import TensionLLMClient
from core.tension.prompt import build_instruction
from core.tension.repair import run_with_repair
from core.tension.schema import ConversationWindow, TensionMiningResult

logger = logging.getLogger(__name__)

DEFAULT_MAX_CANDIDATES = 3


class TensionMiningAgent:
    """1会話窓 = LLM 1コール（+ 修復再試行）で tension 候補を検出する。"""

    def __init__(self, llm_client=None):
        self._llm_client = llm_client

    def run(
        self,
        window: ConversationWindow,
        max_candidates: int = DEFAULT_MAX_CANDIDATES,
        model: str | None = None,
    ) -> TensionMiningResult:
        """会話窓から tension 候補を検出する。

        学習者発話が1つも無い窓は LLM を呼ばず空結果を返す（コスト・過検出の両面で安全側）。
        """
        if not window.learner_texts():
            return TensionMiningResult()
        client = self._llm_client or TensionLLMClient(model=model)
        content = build_instruction(max_candidates) + "\n\n" + build_user_content(window, max_candidates)
        result = run_with_repair(client, content, window, max_candidates)
        logger.info(
            "tension mining: course=%s topic=%s candidates=%d rejected=%d repair_failed=%s",
            window.course_id, window.topic_id,
            len(result.candidates), len(result.rejected_as_gap), result.repair_failed,
        )
        return result
