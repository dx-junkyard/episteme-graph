"""item オーサリング出力の検証（§4.2）。ハードエラーは repair へ回す。

| ルール | 種別 |
|---|---|
| JSON スキーマ適合（必須キー・型） | hard |
| elicit_mode ∈ {predict, restate} | hard |
| prompt が非空 | hard |
| predict: response_space が 2 個以上 / expected.option_id が response_space に含まれる | hard |
| restate: response_space は空 / expected は空 | hard |
| prompt に答え（claim 本文・normalized_text）を丸写ししていない | hard |
| author_confidence ∈ [0.0, 1.0] | warning（クランプ） |
"""

from __future__ import annotations

from typing import Any

from core.reconstruction.schema import ItemAuthoringResult, ResponseOption


def validate_output(
    data: dict[str, Any],
    claim: dict[str, Any],
) -> tuple[ItemAuthoringResult | None, list[str], list[str]]:
    """LLM 出力 dict を検証する。

    Returns
    -------
    (result, errors, warnings)
        errors が空なら result に正規化済み ItemAuthoringResult。
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(data, dict):
        return None, ["output must be a JSON object"], warnings

    mode = str(data.get("elicit_mode") or "").strip()
    if mode not in ("predict", "restate"):
        errors.append(f"elicit_mode '{mode}' must be 'predict' or 'restate'")

    prompt = str(data.get("prompt") or "").strip()
    if not prompt:
        errors.append("prompt is empty")

    raw_space = data.get("response_space")
    if raw_space is None:
        raw_space = []
    if not isinstance(raw_space, list):
        errors.append("response_space must be a JSON array")
        raw_space = []

    options: list[ResponseOption] = []
    option_ids: list[str] = []
    for idx, opt in enumerate(raw_space):
        if not isinstance(opt, dict):
            errors.append(f"response_space[{idx}] must be a JSON object")
            continue
        oid = str(opt.get("id") or "").strip()
        label = str(opt.get("label") or "").strip()
        if not oid:
            errors.append(f"response_space[{idx}].id is empty")
            continue
        if oid in option_ids:
            errors.append(f"response_space[{idx}].id '{oid}' is duplicated")
            continue
        if not label:
            errors.append(f"response_space[{idx}].label is empty")
        options.append(ResponseOption(id=oid, label=label))
        option_ids.append(oid)

    raw_expected = data.get("expected")
    expected: dict = raw_expected if isinstance(raw_expected, dict) else {}

    if mode == "predict":
        if len(options) < 2:
            errors.append("predict mode requires at least 2 response_space options")
        gold = str(expected.get("option_id") or "").strip()
        if not gold:
            errors.append("predict mode requires expected.option_id")
        elif gold not in option_ids:
            errors.append(f"expected.option_id '{gold}' is not present in response_space")
    elif mode == "restate":
        if options:
            errors.append("restate mode must not define response_space options")
            options = []
        if expected:
            warnings.append("restate mode expected is ignored")
            expected = {}

    # 答えの丸写し検出（prompt に claim 本文・normalized_text が実質そのまま入っていないか）
    if prompt:
        for key in ("text", "normalized_text"):
            gt = str(claim.get(key) or "").strip()
            if gt and len(gt) >= 12 and gt in prompt:
                errors.append("prompt must not contain the claim answer verbatim")
                break

    confidence = data.get("author_confidence")
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.0
    if not (0.0 <= confidence <= 1.0):
        warnings.append(f"author_confidence {confidence} clamped to [0,1]")
        confidence = max(0.0, min(1.0, confidence))

    fields_used = data.get("claim_fields_used")
    if not isinstance(fields_used, list):
        fields_used = []
    fields_used = [str(f) for f in fields_used if str(f).strip()]

    if errors:
        return None, errors, warnings

    result = ItemAuthoringResult(
        elicit_mode=mode,
        prompt=prompt,
        response_space=options,
        expected=expected,
        claim_fields_used=fields_used,
        author_confidence=confidence,
        reason=str(data.get("reason") or "").strip(),
        warnings=warnings,
    )
    return result, [], warnings
