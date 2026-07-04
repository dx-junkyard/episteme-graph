"""構造帰属型の問い記録（Structure-Anchored Questions）— B層モジュール。

学習チャットの問いを「提示された情報構造のどこに（anchor）、どう引っかかったか
（doubt_type）」として interest_traces.payload.structure_anchor に記録する。
tension（backend/core/tension/）と同型の独立モジュール。

- 方法A（同期・非LLM）: 発話時の明示アンカー（テキスト選択・要素タップ）を
  attribution_source='learner_selected' で記録（routes/learning.py が schema の
  ヘルパーで構築）。
- 方法B（非同期LLM）: worker が未帰属の問いをバッチ帰属し 'llm_candidate' を書く。
  確定は本人の confirm のみ（P1）。
"""

from core.structure_anchor.agent import StructureAnchorAgent
from core.structure_anchor.schema import (
    ANCHOR_TYPE_LABELS,
    ANCHOR_TYPES,
    ATTRIBUTION_CONFIRMED,
    ATTRIBUTION_LEARNER_SELECTED,
    ATTRIBUTION_LLM_CANDIDATE,
    DETECTOR_VERSION,
    DOUBT_TYPE_LABELS,
    DOUBT_TYPES,
    AnchorCandidate,
    AnchorContext,
    AnchorMiningResult,
    QuestionRecord,
    anchor_type_for_element,
    build_anchor_payload,
)
from core.structure_anchor.worker import (
    check_and_count_confirm_prompt,
    maybe_schedule_anchor_mining,
    run_anchor_mining,
)

__all__ = [
    "ANCHOR_TYPE_LABELS",
    "ANCHOR_TYPES",
    "ATTRIBUTION_CONFIRMED",
    "ATTRIBUTION_LEARNER_SELECTED",
    "ATTRIBUTION_LLM_CANDIDATE",
    "DETECTOR_VERSION",
    "DOUBT_TYPE_LABELS",
    "DOUBT_TYPES",
    "AnchorCandidate",
    "AnchorContext",
    "AnchorMiningResult",
    "QuestionRecord",
    "StructureAnchorAgent",
    "anchor_type_for_element",
    "build_anchor_payload",
    "check_and_count_confirm_prompt",
    "maybe_schedule_anchor_mining",
    "run_anchor_mining",
]
