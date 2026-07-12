"""検証スコープ候補 — validation 失敗時の修復再試行（D1-4, tension と同方式）。

2回失敗しても対象行は破棄せず、repair_failed=True の空結果を返す。
worker は scope_candidates_analyzed_at を打った上で候補 0 件のまま保持する（P4）。

ループの骨格は core/llm_worker/repair.py に共通化済み。ここでは scope_candidates 固有の
validate_output 呼び出しと repair_failed 時の ScopeCandidateResult 組み立てのみを持つ。
"""

from __future__ import annotations

from core.doubt.scope_candidates.prompt import build_repair_prompt
from core.doubt.scope_candidates.schema import ScopeCandidateResult, ScopeTargetContext
from core.doubt.scope_candidates.validator import validate_output
from core.llm_worker.repair import MAX_REPAIR_ATTEMPTS, run_with_repair as _run_with_repair

__all__ = ["MAX_REPAIR_ATTEMPTS", "run_with_repair"]


def run_with_repair(
    llm_client,
    base_content: str,
    context: ScopeTargetContext,
) -> ScopeCandidateResult:
    return _run_with_repair(
        llm_client,
        base_content,
        validate=lambda data: validate_output(data, context),
        build_repair_prompt=build_repair_prompt,
        on_repair_failed=lambda errors: ScopeCandidateResult(
            target_id=context.target_id,
            target_type=context.target_type,
            repair_failed=True,
            warnings=[f"repair_failed: {e}" for e in errors],
        ),
        log_label="scope candidate",
    )
