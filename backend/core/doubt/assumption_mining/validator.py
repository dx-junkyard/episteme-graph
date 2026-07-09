"""暗黙前提の正規化出力の検証（D2-2）。

| 検査 | 失敗種別 |
|---|---|
| JSON スキーマ（statement / evidence_quote / reason / confidence） | hard |
| statement ≤ MAX_STATEMENT_CHARS・1 文 | hard |
| statement 非空のとき evidence_quote が source_texts の逐語部分文字列 | hard |
| statement 非空のとき reason 非空 | hard |
| confidence 数値（0..1 クランプ） | warning |

statement 空文字列は「言語化できない」の正常出力（呼び出し側が placeholder を保持）。
"""

from __future__ import annotations

from core.doubt.assumption_mining.schema import MAX_STATEMENT_CHARS


def _normalize_ws(text: str) -> str:
    return " ".join((text or "").split())


def validate_output(
    data: dict,
    source_texts: list[str],
) -> tuple[dict | None, list[str], list[str]]:
    """(result, errors, warnings)。result は正規化済み dict（statement 空も成功扱い）。"""
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(data, dict):
        return None, ["output must be a JSON object"], warnings

    statement = str(data.get("statement") or "").strip()
    evidence_quote = str(data.get("evidence_quote") or "").strip()
    reason = str(data.get("reason") or "").strip()

    if statement:
        if len(statement) > MAX_STATEMENT_CHARS:
            errors.append(f"statement exceeds {MAX_STATEMENT_CHARS} chars")
        if not evidence_quote:
            errors.append("evidence_quote is required when statement is non-empty")
        else:
            needle = _normalize_ws(evidence_quote)
            if not any(needle in _normalize_ws(s) for s in source_texts):
                errors.append("evidence_quote must be a verbatim substring of the cluster texts")
        if not reason:
            errors.append("reason is required when statement is non-empty")

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
        {
            "statement": statement,
            "evidence_quote": evidence_quote,
            "reason": reason,
            "confidence": confidence,
        },
        [],
        warnings,
    )
