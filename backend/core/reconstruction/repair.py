"""item オーサリング validation 失敗時の修復再試行（§4.2、A層と同方式）。

validation 失敗時、元出力 + エラーリストを添えて最大2回再試行する。
2回失敗したら item を生成しない（=配信しない）。呼び出し側（agent.py）は
repair_failed=True の結果を返し、worker はその claim をスキップする。

ループの骨格は core/llm_worker/repair.py に共通化済み。ここでは reconstruction 固有の
validate_output 呼び出しと repair_failed 時の ItemAuthoringResult 組み立てのみを持つ。
"""

from __future__ import annotations

from core.llm_worker.repair import MAX_REPAIR_ATTEMPTS, run_with_repair as _run_with_repair
from core.reconstruction.prompt import build_repair_prompt
from core.reconstruction.schema import ItemAuthoringResult
from core.reconstruction.validator import validate_output

__all__ = ["MAX_REPAIR_ATTEMPTS", "run_with_repair"]


def run_with_repair(llm_client, base_content: str, claim: dict) -> ItemAuthoringResult:
    """LLM 呼び出し → 検証 → 失敗なら修復再試行（最大 MAX_REPAIR_ATTEMPTS 回）。"""
    return _run_with_repair(
        llm_client,
        base_content,
        validate=lambda data: validate_output(data, claim),
        build_repair_prompt=build_repair_prompt,
        on_repair_failed=lambda errors: ItemAuthoringResult(
            repair_failed=True, warnings=[f"repair_failed: {e}" for e in errors],
        ),
        log_label="recon item authoring",
    )
