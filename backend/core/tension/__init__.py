"""TensionMiningAgent — 会話からの違和感（tension）候補検出（B層）。

学習者との対話ログから「未理解（gap）」ではなく「理解した上での引っかかり（tension）」の
候補を抽出し、学習者本人の確定を経て interest_traces に記録する。

設計原則（不変条項）:
  P1 違和感を生成するのは人間。AIは候補提示まで（LLM出力は常に status='candidate'）
  P2 断定しない（paraphrase は推量形を validator でハード強制）
  P3 監視にしない（候補・確定とも既定で本人のみ可視。教員へは匿名集約のみ）
  P4 情報を落とさない（dismiss は status='dismissed'、分類不能は 'unclassified' で保持）
  P5 evidence-based（全候補に逐語 evidence_quote・reason・confidence を必須付与）
  P6 チャット応答を遅延させない（同期パスは非LLMプレフィルタのみ。LLM分類は非同期バッチ）
  P7 演技化させない（件数のバッジ化・ランキング化をしない）
"""

from core.tension.agent import TensionMiningAgent
from core.tension.prefilter import judge_tension_hint
from core.tension.schema import (
    DETECTOR_VERSION,
    TENSION_TYPES,
    ConversationTurn,
    ConversationWindow,
    RejectedGap,
    TensionCandidate,
    TensionMiningResult,
)

__all__ = [
    "DETECTOR_VERSION",
    "TENSION_TYPES",
    "ConversationTurn",
    "ConversationWindow",
    "RejectedGap",
    "TensionCandidate",
    "TensionMiningResult",
    "TensionMiningAgent",
    "judge_tension_hint",
]
