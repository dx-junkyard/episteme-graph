"""Stage 2(B) — validation 失敗時の修復再試行（tension / A層と同方式）。

validation 失敗時、元出力＋エラーリストを添えて最大2回再試行する。
2回失敗したらバッチは破棄せず、呼び出し側（agent.py）が repair_failed=True の
結果を返し、worker が対象痕跡に anchor_repair_failed の印を残す（P4: 情報を落とさない）。

ループの骨格は core/llm_worker/repair.py に共通化済み。ここでは anchor 固有の
validate_output 呼び出しと repair_failed 時の AnchorMiningResult 組み立てのみを持つ。
"""

from __future__ import annotations

from core.llm_worker.repair import MAX_REPAIR_ATTEMPTS, run_with_repair as _run_with_repair
from core.structure_anchor.prompt import build_repair_prompt
from core.structure_anchor.schema import AnchorContext, AnchorMiningResult
from core.structure_anchor.validator import validate_output

__all__ = ["MAX_REPAIR_ATTEMPTS", "run_with_repair"]


def run_with_repair(
    llm_client,
    base_content: str,
    context: AnchorContext,
) -> AnchorMiningResult:
    """LLM 呼び出し → 検証 → 失敗なら修復再試行（最大 MAX_REPAIR_ATTEMPTS 回）。

    Returns
    -------
    AnchorMiningResult
        成功時は検証済み結果。全試行失敗時は repair_failed=True の空結果。
    """
    return _run_with_repair(
        llm_client,
        base_content,
        validate=lambda data: validate_output(data, context),
        build_repair_prompt=build_repair_prompt,
        on_repair_failed=lambda errors: AnchorMiningResult(
            repair_failed=True, warnings=[f"repair_failed: {e}" for e in errors],
        ),
        log_label="anchor mining",
    )
