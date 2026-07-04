"""Stage 2(B) — 出力検証。ハードエラーは repair へ回す。

| ルール | 種別 |
|---|---|
| JSONスキーマ適合（必須キー・型） | hard |
| trace_id が入力の問い集合に存在・重複しない | hard |
| anchors/unattributed が入力の全 trace_id を被覆する | hard |
| anchor_type ∈ ANCHOR_TYPES / doubt_type ∈ DOUBT_TYPES | hard |
| anchor_id が該当 anchor_type の候補ブロックに存在（捏造禁止） | hard |
| evidence_quote が該当質問文の部分文字列として逐語一致・非空 | hard |
| reason が空 | hard |
| confidence ∈ [0.0, 1.0] | hard |
| doubt_type=unclassified で confidence > 0.5 | warning（0.5 にクランプ） |
| chunk/segment 縮退アンカーで confidence > 0.7 | warning（0.7 にクランプ） |
| anchor_label 空 | warning（context blocks から補完） |
"""

from __future__ import annotations

from core.structure_anchor.schema import (
    ANCHOR_TYPES,
    DOUBT_TYPES,
    AnchorCandidate,
    AnchorContext,
    AnchorMiningResult,
    UnattributedQuestion,
)

# 縮退アンカー（粗い粒度）の confidence 上限（プロンプト規約 §6 と一致させる）
COARSE_ANCHOR_MAX_CONFIDENCE = 0.7
UNCLASSIFIED_MAX_CONFIDENCE = 0.5
_COARSE_TYPES = ("chunk", "segment")


def validate_output(
    data: dict,
    context: AnchorContext,
) -> tuple[AnchorMiningResult | None, list[str], list[str]]:
    """LLM 出力 dict を検証する。

    Returns
    -------
    (result, errors, warnings)
        errors が空なら result に正規化済み AnchorMiningResult（warning クランプ適用済み）。
        errors が非空なら result は None（repair 対象）。
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(data, dict):
        return None, ["output must be a JSON object"], warnings
    raw_anchors = data.get("anchors")
    raw_unattributed = data.get("unattributed", [])
    if not isinstance(raw_anchors, list):
        errors.append("'anchors' must be a JSON array")
        raw_anchors = []
    if not isinstance(raw_unattributed, list):
        errors.append("'unattributed' must be a JSON array")
        raw_unattributed = []

    question_ids = context.question_ids()
    seen_trace_ids: set[str] = set()

    candidates: list[AnchorCandidate] = []
    for idx, raw in enumerate(raw_anchors):
        if not isinstance(raw, dict):
            errors.append(f"anchors[{idx}] must be a JSON object")
            continue
        for key in ("trace_id", "anchor_type", "anchor_id", "doubt_type",
                    "evidence_quote", "confidence", "reason"):
            if key not in raw:
                errors.append(f"anchors[{idx}] is missing required key '{key}'")
        cand = AnchorCandidate.from_dict(raw)

        if cand.trace_id not in question_ids:
            errors.append(f"anchors[{idx}].trace_id '{cand.trace_id}' is not an input question")
        elif cand.trace_id in seen_trace_ids:
            errors.append(f"anchors[{idx}].trace_id '{cand.trace_id}' appears more than once")
        seen_trace_ids.add(cand.trace_id)

        if cand.anchor_type not in ANCHOR_TYPES:
            errors.append(f"anchors[{idx}].anchor_type '{cand.anchor_type}' is not in the vocabulary")
        else:
            allowed = context.allowed_ids(cand.anchor_type)
            if not cand.anchor_id:
                errors.append(f"anchors[{idx}].anchor_id is empty")
            elif cand.anchor_id not in allowed:
                errors.append(
                    f"anchors[{idx}].anchor_id '{cand.anchor_id}' is not in the "
                    f"candidate block for anchor_type '{cand.anchor_type}' (invented id)"
                )

        if cand.doubt_type not in DOUBT_TYPES:
            errors.append(f"anchors[{idx}].doubt_type '{cand.doubt_type}' is not in the taxonomy")

        question = context.question_by_id(cand.trace_id)
        quote = (cand.evidence_quote or "").strip()
        if not quote:
            errors.append(f"anchors[{idx}].evidence_quote is empty")
        elif question is not None and quote not in question.text:
            errors.append(
                f"anchors[{idx}].evidence_quote is not a verbatim substring of the question text"
            )
        cand.evidence_quote = quote

        if not (cand.reason or "").strip():
            errors.append(f"anchors[{idx}].reason is empty")

        if not (0.0 <= cand.confidence <= 1.0):
            errors.append(f"anchors[{idx}].confidence must be within [0.0, 1.0]")
        else:
            if cand.doubt_type == "unclassified" and cand.confidence > UNCLASSIFIED_MAX_CONFIDENCE:
                warnings.append(
                    f"anchors[{idx}]: confidence {cand.confidence} clamped to "
                    f"{UNCLASSIFIED_MAX_CONFIDENCE} (doubt_type is unclassified)"
                )
                cand.confidence = UNCLASSIFIED_MAX_CONFIDENCE
            elif cand.anchor_type in _COARSE_TYPES and cand.confidence > COARSE_ANCHOR_MAX_CONFIDENCE:
                warnings.append(
                    f"anchors[{idx}]: confidence {cand.confidence} clamped to "
                    f"{COARSE_ANCHOR_MAX_CONFIDENCE} (coarse anchor_type '{cand.anchor_type}')"
                )
                cand.confidence = COARSE_ANCHOR_MAX_CONFIDENCE

        if not (cand.anchor_label or "").strip():
            # warning: label は context blocks から補完できるためハードにしない
            cand.anchor_label = context.label_for(cand.anchor_type, cand.anchor_id)
            warnings.append(f"anchors[{idx}].anchor_label was empty; filled from candidate block")
        cand.anchor_label = cand.anchor_label[:80]

        candidates.append(cand)

    unattributed: list[UnattributedQuestion] = []
    for idx, raw in enumerate(raw_unattributed):
        if not isinstance(raw, dict):
            errors.append(f"unattributed[{idx}] must be a JSON object")
            continue
        tid = str(raw.get("trace_id") or "")
        if tid not in question_ids:
            errors.append(f"unattributed[{idx}].trace_id '{tid}' is not an input question")
        elif tid in seen_trace_ids:
            errors.append(f"unattributed[{idx}].trace_id '{tid}' also appears in anchors")
        seen_trace_ids.add(tid)
        unattributed.append(UnattributedQuestion(trace_id=tid, reason=str(raw.get("reason") or "")))

    missing = question_ids - seen_trace_ids
    if missing:
        errors.append(
            "every input question must appear in anchors or unattributed; "
            f"missing trace_ids: {sorted(missing)}"
        )

    if errors:
        return None, errors, warnings
    return AnchorMiningResult(candidates=candidates, unattributed=unattributed, warnings=warnings), [], warnings
