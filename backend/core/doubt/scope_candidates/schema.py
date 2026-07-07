"""検証スコープ候補抽出の入出力データモデル（D1-4）。"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.doubt.schema import ScopeCandidate

DETECTOR_VERSION = "doubt-scope-candidates/v1"

# 1 対象あたりの候補上限（煽らない: 大量の候補で圧をかけない）
MAX_CANDIDATES_PER_TARGET = 3


@dataclass
class SourceBlock:
    """LLM に渡す出典テキスト 1 件。evidence_quote の逐語検証の母集団になる。"""

    block_id: str
    label: str
    text: str


@dataclass
class ScopeTargetContext:
    """スコープ候補抽出の対象 1 件分の文脈。"""

    target_id: str
    target_type: str  # claim | equation | component | assumption
    target_label: str = ""
    source_blocks: list[SourceBlock] = field(default_factory=list)

    def all_texts(self) -> list[str]:
        return [b.text for b in self.source_blocks if (b.text or "").strip()]

    def has_sources(self) -> bool:
        return bool(self.all_texts())


@dataclass
class ScopeCandidateResult:
    """1 対象分の抽出結果。repair 2 回失敗でも行は保持される（P4）。"""

    target_id: str = ""
    target_type: str = ""
    candidates: list[ScopeCandidate] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    repair_failed: bool = False

    def to_payload_list(self) -> list[dict]:
        return [c.model_dump() for c in self.candidates]
