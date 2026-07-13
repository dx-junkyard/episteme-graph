"""暗黙前提の正規化 — validation 失敗時の修復再試行（D2-2, tension と同方式）。

2回失敗しても検出クラスタ行は保持され、呼び出し側（worker）が
created_from.normalization_failed=true を付けて placeholder 文のまま残す（P4）。

ループの骨格は core/llm_worker/repair.py に共通化済み。ここでは assumption_mining 固有の
validate_output 呼び出しと、失敗時に None を返す（呼び出し側が P4 で保持する）挙動のみを持つ。
"""

from __future__ import annotations

from core.doubt.assumption_mining.prompt import build_repair_prompt
from core.doubt.assumption_mining.validator import validate_output
from core.llm_worker.repair import MAX_REPAIR_ATTEMPTS, run_with_repair as _run_with_repair

__all__ = ["MAX_REPAIR_ATTEMPTS", "run_with_repair"]


def run_with_repair(
    llm_client,
    base_content: str,
    source_texts: list[str],
) -> dict | None:
    """成功時は正規化 dict、全試行失敗時は None（呼び出し側が P4 で保持）。"""
    return _run_with_repair(
        llm_client,
        base_content,
        validate=lambda data: validate_output(data, source_texts),
        build_repair_prompt=build_repair_prompt,
        on_repair_failed=lambda errors: None,
        log_label="assumption normalization",
    )
