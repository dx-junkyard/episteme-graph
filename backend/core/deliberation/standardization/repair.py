"""標準化判定（Phase S）— validation 失敗時の修復再試行（tension と同方式）。

2回失敗しても対象を破棄せず、証拠①（LLM 事前知識）を repair_failed=True の
「非認知」相当（recognized=False・breadth='unknown'）に落として続行する（P4）。
``aggregate.decide()`` はこの状態でも他の2証拠（L層凍結版類似・コーパス内反復）だけで
決定論的に判定を続けられる（LLM 呼び出しの失敗が worker 全体を止めない）。

ループの骨格は core/llm_worker/repair.py に共通化済み。ここでは standardization 固有の
validate_output 呼び出しと repair_failed 時の LLMPriorKnowledge 組み立てのみを持つ。
"""

from __future__ import annotations

from core.deliberation.standardization.prompt import build_repair_prompt
from core.deliberation.standardization.schema import BREADTH_UNKNOWN, LLMPriorKnowledge
from core.deliberation.standardization.validator import validate_output
from core.llm_worker.repair import MAX_REPAIR_ATTEMPTS, run_with_repair as _run_with_repair

__all__ = ["MAX_REPAIR_ATTEMPTS", "run_with_repair"]


def run_with_repair(llm_client, base_content: str) -> LLMPriorKnowledge:
    return _run_with_repair(
        llm_client,
        base_content,
        validate=lambda data: validate_output(data),
        build_repair_prompt=build_repair_prompt,
        on_repair_failed=lambda errors: LLMPriorKnowledge(
            recognized=False, breadth=BREADTH_UNKNOWN, reason="", confidence=0.0, repair_failed=True,
        ),
        log_label="standardization prior knowledge",
    )
