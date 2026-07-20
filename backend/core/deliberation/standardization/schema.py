"""標準化判定 worker（Phase S）の語彙・dataclass の正本。

設計: `docs/features/knowledge_network_vision.md`（§3 修正③・§6・§7 Phase S）、
`docs/features/element_deliberation_workspace_design.md`（§5.5 標準化判定）。

判定語彙（``standardization_status`` の5値）の正本は `core.library.schema`
（``library_entries.standardization_status`` の CHECK 制約と対応する側が正本であるため）。
本モジュールはそれを読み取り専用で再エクスポートする（書き込み関数は import しない）。

本モジュールは FastAPI にも ``routes``/``services`` にも依存しない（core 共通ルール）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.library.schema import (
    STANDARDIZATION_STATUS_EMERGING_COMMON,
    STANDARDIZATION_STATUS_FIELD_STANDARD,
    STANDARDIZATION_STATUS_NOVEL,
    STANDARDIZATION_STATUS_STANDARD,
    STANDARDIZATION_STATUS_UNKNOWN,
    STANDARDIZATION_STATUSES,
)

__all__ = [
    "STANDARDIZATION_STATUS_STANDARD",
    "STANDARDIZATION_STATUS_FIELD_STANDARD",
    "STANDARDIZATION_STATUS_EMERGING_COMMON",
    "STANDARDIZATION_STATUS_NOVEL",
    "STANDARDIZATION_STATUS_UNKNOWN",
    "STANDARDIZATION_STATUSES",
    "BREADTH_TEXTBOOK",
    "BREADTH_FIELD",
    "BREADTH_UNKNOWN",
    "BREADTHS",
    "LLMPriorKnowledge",
    "LibrarySimilarityEvidence",
    "CorpusRepetitionEvidence",
    "StandardizationAssessment",
]

# ── LLM の breadth 語彙（証拠①「認知の広さ」）───────────────────────────────────
BREADTH_TEXTBOOK = "textbook"
BREADTH_FIELD = "field"
BREADTH_UNKNOWN = "unknown"
BREADTHS = (BREADTH_TEXTBOOK, BREADTH_FIELD, BREADTH_UNKNOWN)


@dataclass
class LLMPriorKnowledge:
    """三角測量・証拠①（LLM 事前知識）。structured output そのまま保持する。

    ``repair_failed=True`` は2回の修復再試行後も有効な JSON が得られなかった状態
    （P4: そのまま「認知していない」相当として続行し、他の2証拠で判定を続ける）。
    """

    recognized: bool = False
    claimed_canonical_name: str = ""
    breadth: str = BREADTH_UNKNOWN
    reason: str = ""
    confidence: float = 0.0
    repair_failed: bool = False


@dataclass
class LibrarySimilarityEvidence:
    """証拠②（L層凍結版類似）。同 domain の他エントリ（自分を除く）の retrieval ヒット。"""

    hit: bool = False
    matched_entry_ids: list[str] = field(default_factory=list)


@dataclass
class CorpusRepetitionEvidence:
    """証拠③（コーパス内反復）。confirmed identity link ∪ source_document_ids の
    distinct document 数（D層 assumption mining と同じ「2論文以上」の閾値思想）。
    """

    repeated: bool = False
    document_count: int = 0
    document_ids: list[str] = field(default_factory=list)


@dataclass
class StandardizationAssessment:
    """三角測量の合成結果（``aggregate.decide()`` の戻り値）。"""

    standardization_status: str
    claimed_canonical_name: str = ""
    reason: str = ""
    confidence: float | None = None
    evidence: list[str] = field(default_factory=list)
    evidence_summary: dict = field(default_factory=dict)
