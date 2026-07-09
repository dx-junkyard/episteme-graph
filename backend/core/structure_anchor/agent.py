"""StructureAnchorAgent 本体（Stage 2(B)、run() を公開インターフェースとする）。

未帰属の問いバッチ + アンカー候補ブロックを入力に、AnchorCandidate[] を
JSON（validator/repair 付き）で返す。LLM 出力は常に候補
（attribution_source='llm_candidate'）であり、確定は学習者本人の操作のみ（P1）。
永続化は行わない（worker.py の責務）。
"""

from __future__ import annotations

import logging

from core.structure_anchor.input_builder import build_user_content
from core.structure_anchor.llm_client import AnchorLLMClient
from core.structure_anchor.prompt import build_instruction
from core.structure_anchor.repair import run_with_repair
from core.structure_anchor.schema import AnchorContext, AnchorMiningResult

logger = logging.getLogger(__name__)


class StructureAnchorAgent:
    """1問いバッチ = LLM 1コール（+ 修復再試行）で帰属候補を生成する。"""

    def __init__(self, llm_client=None):
        self._llm_client = llm_client

    def run(
        self,
        context: AnchorContext,
        model: str | None = None,
    ) -> AnchorMiningResult:
        """問いバッチから帰属候補を生成する。

        問いが1つも無いバッチは LLM を呼ばず空結果を返す（コスト面で安全側）。
        """
        if not context.questions:
            return AnchorMiningResult()
        client = self._llm_client or AnchorLLMClient(model=model)
        content = build_instruction() + "\n\n" + build_user_content(context)
        result = run_with_repair(client, content, context)
        logger.info(
            "anchor mining: course=%s topic=%s anchors=%d unattributed=%d repair_failed=%s",
            context.course_id, context.topic_id,
            len(result.candidates), len(result.unattributed), result.repair_failed,
        )
        return result
