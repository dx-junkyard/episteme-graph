"""標準化判定（Phase S）— LLM 事前知識（証拠①）の出力検証。

| 検査 | 失敗種別 |
|---|---|
| JSON オブジェクトであること | hard |
| recognized が bool | hard |
| reason が非空 | hard |
| breadth が語彙内（不正なら "unknown" へ既定・警告） | warning |
| confidence が数値（0..1 にクランプ） | warning |

grounded な逐語引用検証は行わない（この LLM は出典テキストの書き写しではなく、
一般知識に基づく事前知識の申告——ビジョン §3 修正③の「証拠①」——のため）。
"""

from __future__ import annotations

from core.deliberation.standardization.schema import BREADTH_UNKNOWN, BREADTHS, LLMPriorKnowledge


def validate_output(data: dict) -> tuple[LLMPriorKnowledge | None, list[str], list[str]]:
    """(result, errors, warnings) を返す。errors 非空なら result=None（repair 対象）。"""
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(data, dict):
        return None, ["output must be a JSON object"], warnings

    recognized = data.get("recognized")
    if not isinstance(recognized, bool):
        errors.append("'recognized' must be a boolean")
        recognized = False

    reason = str(data.get("reason") or "").strip()
    if not reason:
        errors.append("reason is required")

    breadth = str(data.get("breadth") or "").strip()
    if breadth not in BREADTHS:
        warnings.append(f"breadth was not one of {BREADTHS}; defaulted to {BREADTH_UNKNOWN!r}")
        breadth = BREADTH_UNKNOWN

    claimed_canonical_name = str(data.get("claimed_canonical_name") or "").strip()

    try:
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
        warnings.append("confidence was not numeric; set to 0.0")
    if confidence < 0.0 or confidence > 1.0:
        warnings.append("confidence clamped into [0,1]")
        confidence = min(1.0, max(0.0, confidence))

    if errors:
        return None, errors, warnings

    return (
        LLMPriorKnowledge(
            recognized=recognized,
            claimed_canonical_name=claimed_canonical_name,
            breadth=breadth,
            reason=reason,
            confidence=confidence,
            repair_failed=False,
        ),
        [],
        warnings,
    )
