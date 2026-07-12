"""Stage 1 — validation 失敗時の修復再試行（設計書 §7、A層と同方式）。

validation 失敗時、元出力＋エラーリストを添えて最大2回再試行する。
2回失敗したらバッチは破棄せず、呼び出し側（agent.py）が repair_failed=True の
結果を返し、worker が unclassified の1行として保存する（P4: 情報を落とさない）。

ループの骨格は core/llm_worker/repair.py に共通化済み。ここでは tension 固有の
validate_output 呼び出しと repair_failed 時の TensionMiningResult 組み立てのみを持つ。
"""

from __future__ import annotations

from core.llm_worker.repair import MAX_REPAIR_ATTEMPTS, run_with_repair as _run_with_repair
from core.tension.prompt import build_repair_prompt
from core.tension.schema import ConversationWindow, TensionMiningResult
from core.tension.validator import validate_output

__all__ = ["MAX_REPAIR_ATTEMPTS", "run_with_repair"]


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
    return _run_with_repair(
        llm_client,
        base_content,
        validate=lambda data: validate_output(data, window, max_candidates),
        build_repair_prompt=build_repair_prompt,
        on_repair_failed=lambda errors: TensionMiningResult(
            repair_failed=True, warnings=[f"repair_failed: {e}" for e in errors],
        ),
        log_label="tension mining",
    )
