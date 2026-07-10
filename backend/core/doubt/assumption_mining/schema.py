"""暗黙前提マイニングのデータモデル（D2-2 / D3-5）。"""

from __future__ import annotations

from dataclasses import dataclass, field

DETECTOR_VERSION = "assumption-mining/v1"

# D層側の review 理由語彙（A層 validator には追加しない）
REVIEW_REASON_IMPLICIT_ASSUMPTION = "implicit_assumption_candidate"

# 経路A: 候補化に必要な反復の下限（単一導出のみの補完は候補化しない）
MIN_DOCUMENTS_FOR_CANDIDATE = 2
MIN_DERIVATIONS_FOR_CANDIDATE = 2

# 経路B: 実行に必要なコーパス規模の下限（偽陽性防止）
MIN_CORPUS_DOCUMENTS = 3

# LLM 正規化後の前提文の最大長（原子化: 1 claim = 1 minimal proposition と同じ型）
MAX_STATEMENT_CHARS = 200


@dataclass
class GapCluster:
    """経路A の検出クラスタ（同一補完の反復）。"""

    cluster_key: str
    operation: str = ""
    review_reasons: list[str] = field(default_factory=list)
    node_ids: list[str] = field(default_factory=list)
    document_ids: list[str] = field(default_factory=list)
    derivation_ids: list[str] = field(default_factory=list)
    claim_ids: list[str] = field(default_factory=list)
    equation_ids: list[str] = field(default_factory=list)
    # LLM 正規化の evidence_quote 母集団（ノード description / review_reason 由来）
    source_texts: list[str] = field(default_factory=list)

    def is_candidate(self) -> bool:
        """2 論文以上 or 2 導出以上で反復する同一補完のみ候補化。"""
        return (
            len(set(self.document_ids)) >= MIN_DOCUMENTS_FOR_CANDIDATE
            or len(set(self.derivation_ids)) >= MIN_DERIVATIONS_FOR_CANDIDATE
        )

    def placeholder_statement(self) -> str:
        """LLM 正規化前の決定論的な仮の前提文（正規化失敗時もこの文で保持, P4）。"""
        label = self.operation or "unknown_operation"
        return (
            f"補完ステップ『{label}』が "
            f"{len(set(self.document_ids))} 論文・{len(set(self.derivation_ids))} 導出で"
            "反復しています（前提文の正規化待ち）"
        )

    def created_from(self) -> dict:
        return {
            "detector_version": DETECTOR_VERSION,
            "operation": self.operation,
            "review_reasons": sorted(set(self.review_reasons)),
            "node_ids": sorted(set(self.node_ids)),
            "document_ids": sorted(set(self.document_ids)),
            "derivation_ids": sorted(set(self.derivation_ids)),
            "claim_ids": sorted(set(self.claim_ids)),
            "equation_ids": sorted(set(self.equation_ids)),
            "d_layer_review_reasons": [REVIEW_REASON_IMPLICIT_ASSUMPTION],
        }
