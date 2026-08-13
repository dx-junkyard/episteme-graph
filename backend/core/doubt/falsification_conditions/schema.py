"""反証条件候補抽出の入出力データモデル（SL-1, scope_candidates/schema.py の写し）。"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.doubt.schema import FalsificationCandidate

DETECTOR_VERSION = "doubt-falsification-conditions/v1"

# 1 対象あたりの候補上限（煽らない: 大量の候補で圧をかけない）
MAX_CANDIDATES_PER_TARGET = 3

# 下流到達集合の要約（非LLM）に含めるラベルの上限
MAX_DOWNSTREAM_LABELS = 5


@dataclass
class SourceBlock:
    """LLM に渡す出典テキスト 1 件。evidence_quote の逐語検証の母集団になる。"""

    block_id: str
    label: str
    text: str


@dataclass
class FalsificationTargetContext:
    """反証条件候補抽出の対象 1 件分の文脈。"""

    target_id: str
    target_type: str  # claim | equation | component | assumption
    target_label: str = ""
    source_blocks: list[SourceBlock] = field(default_factory=list)
    # この対象に依存する下流ノードのラベル要約（非LLM・最大 MAX_DOWNSTREAM_LABELS 件）。
    # 「覆えたときの帰結」を条件文の具体性に使わせるための文脈（判定には使わない）。
    downstream_labels: list[str] = field(default_factory=list)

    def all_texts(self) -> list[str]:
        return [b.text for b in self.source_blocks if (b.text or "").strip()]

    def has_sources(self) -> bool:
        return bool(self.all_texts())


@dataclass
class FalsificationCandidateResult:
    """1 対象分の抽出結果。repair 2 回失敗でも行は保持される（P4）。"""

    target_id: str = ""
    target_type: str = ""
    candidates: list[FalsificationCandidate] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    repair_failed: bool = False

    def to_payload_list(self) -> list[dict]:
        return [c.model_dump() for c in self.candidates]
