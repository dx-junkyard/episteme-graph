"""反証条件候補の出力検証（SL-1）。

| 検査 | 失敗種別 |
|---|---|
| JSON スキーマ（candidates 配列・必須キー） | hard |
| statement 非空 | hard |
| kind が observation_value / auxiliary_hypothesis のいずれか | hard |
| evidence_quote が出典テキストの逐語部分文字列 | hard |
| reason 非空 | hard |
| confidence が数値（0..1 にクランプ） | warning |
| 候補数 ≤ MAX_CANDIDATES_PER_TARGET | hard |
| kind == "not_formulable"（人間専用, SL2） | warning（その候補のみ drop） |
| reachability フィールドの混入（人間専用, SL3） | warning（フィールドのみ剥ぐ） |
"""

from __future__ import annotations

from core.doubt.schema import HUMAN_ONLY_FALSIFICATION_FIELDS, FalsificationCandidate
from core.doubt.falsification_conditions.schema import (
    MAX_CANDIDATES_PER_TARGET,
    FalsificationCandidateResult,
    FalsificationTargetContext,
)

# LLM 候補が出力してよい kind は2値のみ（not_formulable は人間専用, SL2）
_CANDIDATE_KINDS = ("observation_value", "auxiliary_hypothesis")


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
    context: FalsificationTargetContext,
) -> tuple[FalsificationCandidateResult | None, list[str], list[str]]:
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
    candidates: list[FalsificationCandidate] = []
    for idx, item in enumerate(raw_candidates[:MAX_CANDIDATES_PER_TARGET]):
        if not isinstance(item, dict):
            errors.append(f"candidates[{idx}] must be an object")
            continue

        # 人間専用フィールドの混入は剥いで warning（hard error にしない — SL3）。
        item = dict(item)
        for field_name in HUMAN_ONLY_FALSIFICATION_FIELDS:
            if field_name in item:
                warnings.append(f"candidates[{idx}]: {field_name} is human-only; stripped")
                item.pop(field_name, None)

        kind = str(item.get("kind") or "").strip()
        if kind == "not_formulable":
            # 人間専用の記帳語彙（SL2）が候補に混入した場合はこの候補だけを
            # drop する（hard error にはしない — 他の候補は活かす）。
            warnings.append(f"candidates[{idx}]: not_formulable is human-only; candidate skipped")
            continue

        statement = str(item.get("statement") or "").strip()
        if not statement:
            errors.append(f"candidates[{idx}]: statement is required")
        if kind not in _CANDIDATE_KINDS:
            errors.append(f"candidates[{idx}]: kind must be one of {_CANDIDATE_KINDS}")
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

        candidates.append(FalsificationCandidate(
            statement=statement,
            kind=kind,
            evidence_quote=quote,
            reason=reason,
            confidence=confidence,
            status="candidate",
        ))

    if errors:
        return None, errors, warnings

    candidates.sort(key=lambda c: c.confidence, reverse=True)
    return (
        FalsificationCandidateResult(
            target_id=context.target_id,
            target_type=context.target_type,
            candidates=candidates,
            warnings=warnings,
        ),
        [],
        warnings,
    )
