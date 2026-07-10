"""検証スコープ候補の出力検証（D1-4）。

| 検査 | 失敗種別 |
|---|---|
| JSON スキーマ（candidates 配列・必須キー） | hard |
| 4 軸のうち 1 つ以上が非空 | hard |
| evidence_quote が出典テキストの逐語部分文字列 | hard |
| reason 非空 | hard |
| confidence が数値（0..1 にクランプ） | warning |
| 候補数 ≤ MAX_CANDIDATES_PER_TARGET | hard |
"""

from __future__ import annotations

from core.doubt.schema import ScopeCandidate
from core.doubt.scope_candidates.schema import (
    MAX_CANDIDATES_PER_TARGET,
    ScopeCandidateResult,
    ScopeTargetContext,
)

_AXIS_KEYS = ("condition", "domain", "precision", "system")


def _normalize_ws(text: str) -> str:
    return " ".join((text or "").split())


def _quote_in_sources(quote: str, sources: list[str]) -> bool:
    """空白ゆらぎだけを許容した逐語一致。"""
    needle = _normalize_ws(quote)
    if not needle:
        return False
    return any(needle in _normalize_ws(source) for source in sources)


def validate_output(
    data: dict,
    context: ScopeTargetContext,
) -> tuple[ScopeCandidateResult | None, list[str], list[str]]:
    """(result, errors, warnings) を返す。errors 非空なら result=None（repair 対象）。"""
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(data, dict):
        return None, ["output must be a JSON object"], warnings
    raw_candidates = data.get("candidates")
    if not isinstance(raw_candidates, list):
        return None, ["'candidates' must be an array"], warnings
    if len(raw_candidates) > MAX_CANDIDATES_PER_TARGET:
        errors.append(
            f"too many candidates: {len(raw_candidates)} > {MAX_CANDIDATES_PER_TARGET}"
        )

    sources = context.all_texts()
    candidates: list[ScopeCandidate] = []
    for idx, item in enumerate(raw_candidates[:MAX_CANDIDATES_PER_TARGET]):
        if not isinstance(item, dict):
            errors.append(f"candidates[{idx}] must be an object")
            continue
        axes = {key: str(item.get(key) or "").strip() for key in _AXIS_KEYS}
        if not any(axes.values()):
            errors.append(
                f"candidates[{idx}]: at least one of condition/domain/precision/system is required"
            )
        quote = str(item.get("evidence_quote") or "").strip()
        if not quote:
            errors.append(f"candidates[{idx}]: evidence_quote is required")
        elif not _quote_in_sources(quote, sources):
            errors.append(
                f"candidates[{idx}]: evidence_quote must be a verbatim substring of the source texts"
            )
        reason = str(item.get("reason") or "").strip()
        if not reason:
            errors.append(f"candidates[{idx}]: reason is required")
        try:
            confidence = float(item.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
            warnings.append(f"candidates[{idx}]: confidence was not numeric; set to 0.0")
        if confidence < 0.0 or confidence > 1.0:
            warnings.append(f"candidates[{idx}]: confidence clamped into [0,1]")
            confidence = min(1.0, max(0.0, confidence))

        candidates.append(ScopeCandidate(
            condition=axes["condition"],
            domain=axes["domain"],
            precision=axes["precision"],
            system=axes["system"],
            evidence_quote=quote,
            reason=reason,
            confidence=confidence,
            status="candidate",
        ))

    if errors:
        return None, errors, warnings

    candidates.sort(key=lambda c: c.confidence, reverse=True)
    return (
        ScopeCandidateResult(
            target_id=context.target_id,
            target_type=context.target_type,
            candidates=candidates,
            warnings=warnings,
        ),
        [],
        warnings,
    )
