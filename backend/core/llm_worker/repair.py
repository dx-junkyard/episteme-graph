"""LLM worker 系統共通の修復再試行ループ（validation 失敗→repair→再試行）。

tension / structure_anchor / reconstruction / doubt.scope_candidates /
doubt.assumption_mining の5系統で85-90%同一のまま存在していた
「元出力＋エラーリストを添えて最大2回再試行する」ループを集約する。

修復失敗時にどんな値を返すか（repair_failed=True の結果オブジェクト、None、等）は
系統ごとに異なるため、このモジュールでは決めない。呼び出し側が ``on_repair_failed``
でドメイン固有の失敗値を組み立てる。DBへの反映（1行保持する・item を生成しない等）も
すべて各モジュールの worker.py に残す。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

MAX_REPAIR_ATTEMPTS = 2

T = TypeVar("T")


def run_with_repair(
    llm_client: Any,
    base_content: str,
    *,
    validate: Callable[[dict], tuple[T | None, list[str], list[str]]],
    build_repair_prompt: Callable[[str, list[str]], str],
    on_repair_failed: Callable[[list[str]], T],
    max_attempts: int = MAX_REPAIR_ATTEMPTS,
    log_label: str = "llm worker",
) -> T:
    """LLM 呼び出し → 検証 → 失敗なら修復再試行（最大 max_attempts 回）。

    Parameters
    ----------
    validate:
        LLM が返した dict を受け取り ``(result, errors, warnings)`` を返す関数。
        ``result`` が None でなければ検証成功として即座に返す。
    build_repair_prompt:
        直前の生出力とエラーリストから repair 用の追加プロンプトを組み立てる関数。
    on_repair_failed:
        max_attempts 回すべて失敗したときに呼ばれ、戻り値がそのまま返される
        （系統ごとの repair_failed 表現はここで決める）。
    """
    previous_raw = ""
    errors: list[str] = []
    for attempt in range(1 + max_attempts):
        if attempt == 0:
            content = base_content
        else:
            content = base_content + "\n\n" + build_repair_prompt(previous_raw, errors)
        try:
            data = llm_client.complete_json(content)
        except Exception as exc:
            errors = [f"output was not valid JSON: {exc}"]
            previous_raw = ""
            logger.warning("%s LLM attempt %d failed to parse: %s", log_label, attempt + 1, exc)
            continue
        previous_raw = json.dumps(data, ensure_ascii=False)
        result, errors, _warnings = validate(data)
        if result is not None:
            return result
        logger.info("%s attempt %d failed validation: %s", log_label, attempt + 1, errors)

    logger.warning("%s repair failed after %d attempts: %s", log_label, 1 + max_attempts, errors)
    return on_repair_failed(errors)
